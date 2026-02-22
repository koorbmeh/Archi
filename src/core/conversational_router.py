"""
Conversational Router — Single model call per inbound message.

Replaces all heuristic routing scattered across discord_bot.py and
intent_classifier.py with one model call that returns:
  - intent: what the message means (new_request, affirmation, clarification,
            suggestion_pick, approval, cancel, greeting, question_reply, etc.)
  - tier: easy (answer included) or complex (needs goal/PlanExecutor)
  - answer: for easy tier, the response text (one call total)
  - complexity: for complex tier, routing hint (goal, multi_step, coding)

Also extracts user_signals as a side effect for the UserModel.

Input accumulation: for multi-message answers (user lists items one at a
time), keeps the question open and collects items.

Local fast-paths (no API call): slash commands, image gen, model selection,
datetime, screenshot, cancel/stop — run BEFORE the Router call.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.utils.parsing import extract_json
from src.utils.text_cleaning import strip_thinking, sanitize_identity

logger = logging.getLogger(__name__)


# ── Router result ────────────────────────────────────────────────────

@dataclass
class RouterResult:
    """Result from the Conversational Router."""
    intent: str                          # new_request, affirmation, clarification,
                                         # suggestion_pick, approval, cancel, greeting,
                                         # question_reply, accumulation, easy_answer
    tier: str = "complex"                # "easy" or "complex"
    answer: str = ""                     # For easy tier: the response text
    complexity: str = ""                 # For complex: "goal", "multi_step", "coding"
    pick_number: int = 0                 # For suggestion_pick: 1-based index (first/only pick)
    pick_numbers: List[int] = field(default_factory=list)  # For multi-pick: all 1-based indices
    approval: Optional[bool] = None      # For approval: True/False
    accumulated_items: List[str] = field(default_factory=list)  # For accumulation
    accumulation_done: bool = False      # True when user signals done collecting
    action: str = ""                     # Specific action if detected (create_goal, etc.)
    action_params: Dict[str, Any] = field(default_factory=dict)
    cost: float = 0.0
    fast_path: bool = False              # True if resolved without model call
    user_signals: List[Dict[str, str]] = field(default_factory=list)


# ── Input accumulation state ─────────────────────────────────────────

class _AccumulationState:
    """Tracks multi-message input collection for a pending question."""
    def __init__(self, task_id: str, prompt: str):
        self.task_id = task_id
        self.prompt = prompt
        self.items: List[str] = []
        self.started_at = time.time()
        self.last_item_at = time.time()

    SILENCE_TIMEOUT = 120  # 2 min of silence → auto-finalize

    def is_timed_out(self) -> bool:
        return time.time() - self.last_item_at > self.SILENCE_TIMEOUT


_accumulation: Optional[_AccumulationState] = None


def start_accumulation(task_id: str, prompt: str) -> None:
    """Begin collecting multi-message items for a task."""
    global _accumulation
    _accumulation = _AccumulationState(task_id, prompt)
    logger.info("Input accumulation started for task %s: %s", task_id, prompt[:60])


def get_accumulation_state() -> Optional[_AccumulationState]:
    """Return current accumulation state, or None."""
    return _accumulation


def clear_accumulation() -> None:
    """Clear the accumulation state."""
    global _accumulation
    _accumulation = None


# ── Context state for the Router ─────────────────────────────────────

@dataclass
class ContextState:
    """Current conversation state passed to the Router for context."""
    pending_suggestions: List[str] = field(default_factory=list)
    recent_suggestions: List[str] = field(default_factory=list)  # Recently dismissed but recoverable
    pending_approval: bool = False
    pending_question: bool = False
    active_goals: List[str] = field(default_factory=list)
    accumulating: bool = False
    accumulation_prompt: str = ""
    accumulated_items: List[str] = field(default_factory=list)


# ── Local fast-paths (no API call) ───────────────────────────────────

def _check_local_fast_paths(
    message: str, msg_lower: str, goal_manager: Any = None,
) -> Optional[RouterResult]:
    """Check for patterns that can be resolved without a model call.

    Returns a RouterResult if matched, None otherwise.
    These run BEFORE the Router model call.
    """
    # Slash commands
    if msg_lower.startswith("/"):
        return _handle_slash_command(msg_lower, message, goal_manager)

    # Datetime questions
    if _is_datetime_question(msg_lower):
        return RouterResult(
            intent="easy_answer",
            tier="easy",
            answer=datetime.now().strftime("It's %A, %B %d, %Y at %I:%M %p."),
            fast_path=True,
        )

    # Screenshot requests
    if _is_screenshot_request(msg_lower):
        return RouterResult(
            intent="easy_answer",
            tier="easy",
            action="screenshot",
            fast_path=True,
        )

    # Image generation (privacy: NSFW prompts stay local)
    img_result = _extract_image_prompt(msg_lower, message)
    if img_result:
        prompt, count, model = img_result
        params = {"prompt": prompt, "count": count}
        if model:
            params["model"] = model
        return RouterResult(
            intent="easy_answer",
            tier="easy",
            action="generate_image",
            action_params=params,
            fast_path=True,
        )

    return None


# ── Fast-path helpers (kept from intent_classifier.py) ───────────────

_DATETIME_PATTERNS = (
    "what day", "today's date", "current date", "what's the date",
    "what is the date", "what time", "current time", "day of the week",
    "what date", "what is today",
)


def _is_datetime_question(msg_lower: str) -> bool:
    return any(p in msg_lower for p in _DATETIME_PATTERNS)


_SCREENSHOT_PATTERNS = (
    "take a screenshot", "take screenshot", "screenshot",
    "capture the screen", "capture screen", "screen capture",
    "take a picture of the screen", "take a picture of my screen",
    "what's on screen", "what's on my screen",
    "what is on screen", "what is on my screen",
    "show me the screen", "show me my screen",
    "grab the screen", "screen grab", "screengrab",
    "print screen", "printscreen",
)


def _is_screenshot_request(msg_lower: str) -> bool:
    return any(p in msg_lower for p in _SCREENSHOT_PATTERNS)


# Image generation fast-path (moved from intent_classifier.py)
_IMAGE_GEN_STARTERS = (
    "generate an image of ", "generate image of ", "generate a picture of ",
    "generate me an image of ", "generate me a picture of ",
    "create an image of ", "create a picture of ",
    "draw ", "draw me ", "paint ", "paint me ",
    "make an image of ", "make a picture of ", "make me an image of ",
    "make me a picture of ",
    "generate an image: ", "generate image: ",
    "send me a picture of ", "send me an image of ",
    "send me a photo of ", "send a picture of ",
)

_IMAGE_GEN_COUNT_RE = re.compile(
    r"^(?:generate|create|draw|paint|make|send)\s+(?:me\s+)?(\d+)\s+"
    r"(?:images?|pictures?|drawings?|paintings?|photos?)\s+(?:of\s+)?(.+)",
    re.IGNORECASE,
)

_IMAGE_MODEL_SUFFIX_RE = re.compile(
    r"^(.+?)\s+(?:with|using|in)\s+([a-z0-9_]+)\s*$",
    re.IGNORECASE,
)

_IMAGE_MODEL_PREFIX_RE = re.compile(
    r"^(?:use|using|with)\s+([a-z0-9_]+)\s*[,:]?\s*(?:to\s+)?(.+)",
    re.IGNORECASE,
)


def _extract_image_prompt(
    msg_lower: str, original: str,
) -> Optional[Tuple[str, int, Optional[str]]]:
    """Extract image prompt if clearly an image generation request.

    Returns (prompt, count, model_alias_or_None) or None.
    """
    prompt = None
    count = 1
    model = None

    working_msg = original.strip()
    working_lower = msg_lower.strip()

    # Strip leading "using <model>," prefix
    m_prefix = _IMAGE_MODEL_PREFIX_RE.match(working_msg)
    if m_prefix:
        model = m_prefix.group(1).lower()
        working_msg = m_prefix.group(2).strip()
        working_lower = working_msg.lower()

    # Try count pattern: "generate 3 images of X"
    m = _IMAGE_GEN_COUNT_RE.match(working_msg)
    if m:
        count = min(int(m.group(1)), 10)
        prompt = m.group(2).strip().rstrip("?!.")
        if not prompt or len(prompt) < 3 or count < 1:
            return None
    else:
        for starter in _IMAGE_GEN_STARTERS:
            if working_lower.startswith(starter):
                prompt = working_msg[len(starter):].strip().rstrip("?!.")
                if not prompt or len(prompt) < 3:
                    return None
                break

    if prompt is None:
        return None

    # Check for trailing "with <model>" suffix
    if model is None:
        m2 = _IMAGE_MODEL_SUFFIX_RE.match(prompt)
        if m2:
            candidate_model = m2.group(2).lower()
            try:
                from src.tools.image_gen import resolve_image_model
                if resolve_image_model(candidate_model):
                    prompt = m2.group(1).strip()
                    model = candidate_model
            except ImportError:
                pass

    return (prompt, count, model)


def _handle_slash_command(
    msg_lower: str, message: str, goal_manager: Any,
) -> Optional[RouterResult]:
    """Handle /commands as fast-path results."""
    if msg_lower.startswith("/goal ") and goal_manager:
        desc = message[6:].strip()
        return RouterResult(
            intent="easy_answer", tier="easy",
            action="create_goal", action_params={"description": desc},
            fast_path=True,
        )
    if msg_lower == "/goals":
        return RouterResult(
            intent="easy_answer", tier="easy",
            action="goals_status", fast_path=True,
        )
    if msg_lower == "/status":
        return RouterResult(
            intent="easy_answer", tier="easy",
            action="system_status", fast_path=True,
        )
    if msg_lower == "/cost":
        return RouterResult(
            intent="easy_answer", tier="easy",
            action="cost_report", fast_path=True,
        )
    if msg_lower in ("/help", "/h"):
        return RouterResult(
            intent="easy_answer", tier="easy",
            action="help", fast_path=True,
        )
    if msg_lower.startswith("/test"):
        mode = "full" if "full" in msg_lower else "quick"
        return RouterResult(
            intent="easy_answer", tier="easy",
            action="run_tests", action_params={"mode": mode},
            fast_path=True,
        )
    if msg_lower.startswith("/"):
        return RouterResult(
            intent="easy_answer", tier="easy",
            action="unknown_command",
            answer=f"Unknown command: `{message.split()[0]}`. Type /help for available commands.",
            fast_path=True,
        )
    return None


# ── Deferred request detection (kept local, no model call) ───────────

_DEFERRED_SIGNALS = (
    "when you have time", "when you get a chance", "when you're free",
    "when you are free", "when you can", "at some point",
    "whenever you can", "no rush", "no hurry", "not urgent",
    "low priority", "if you get a chance", "if you get the chance",
)
_DEFERRED_VERBS = (
    "look into", "research", "check on", "check out",
    "find out about", "find out", "investigate", "dig into",
    "explore", "read up on", "study", "review",
)
_REMINDER_STARTS = (
    "remind me to", "don't forget to", "make a note to",
    "remember to", "can you remember to",
)
_LATER_SIGNALS = (
    "later", "eventually", "in the future",
    "for next time", "for later", "down the road",
)


def _is_deferred_request(message: str) -> Optional[str]:
    """Detect deferred/reminder-style requests. Returns task description or None."""
    if not message or len(message) < 15 or len(message) > 500:
        return None
    msg = message.strip()
    msg_lower = msg.lower()

    for starter in _REMINDER_STARTS:
        if starter in msg_lower:
            idx = msg_lower.index(starter) + len(starter)
            desc = msg[idx:].strip().rstrip("?!.")
            return desc if len(desc) >= 8 else None

    has_deferred = any(s in msg_lower for s in _DEFERRED_SIGNALS)
    has_later = any(s in msg_lower for s in _LATER_SIGNALS)
    if has_deferred or has_later:
        best_verb = None
        best_idx = len(msg_lower)
        for verb in _DEFERRED_VERBS:
            pos = 0
            while pos < len(msg_lower):
                idx = msg_lower.find(verb, pos)
                if idx == -1:
                    break
                if idx == 0 or not msg_lower[idx - 1].isalpha():
                    if idx < best_idx:
                        best_idx = idx
                        best_verb = verb
                    break
                pos = idx + 1
        if best_verb is not None:
            desc = msg[best_idx:].strip()
            for signal in _DEFERRED_SIGNALS + _LATER_SIGNALS:
                if desc.lower().endswith(signal):
                    desc = desc[:-len(signal)].strip()
            desc = desc.rstrip("?!.,")
            return desc if len(desc) >= 10 else None
        for signal in _DEFERRED_SIGNALS:
            if signal in msg_lower:
                idx = msg_lower.index(signal) + len(signal)
                desc = msg[idx:].strip().lstrip(",").strip()
                if len(desc) >= 10:
                    return desc
    return None


# ── Router prompt ────────────────────────────────────────────────────

_ROUTER_SYSTEM = """You are the Conversational Router for Archi, an AI agent for Jesse.
Your job: classify each inbound message and decide how to handle it.

Return ONLY a JSON object with these fields:
{
  "intent": "<one of the intents below>",
  "tier": "easy" or "complex",
  "answer": "for easy tier: your response text. omit for complex.",
  "complexity": "for complex: goal, multi_step, or coding. omit for easy.",
  "pick_number": <for suggestion_pick: the 1-based number (first or only pick)>,
  "pick_numbers": [<for multi-pick: list of all 1-based numbers, e.g. [1, 3]>],
  "approval": <for approval: true or false>,
  "accumulation_item": "for accumulation: the item to add, or null if done signal",
  "accumulation_done": <true if user signals they're done listing items>,
  "action": "optional: create_goal, deferred_request, send_file, etc.",
  "action_params": {},
  "user_signals": [{"type": "preference|correction|pattern|style", "text": "what you observed"}]
}

INTENTS:
- "new_request" — a new task, question, or command. Assess tier:
    easy = can answer in this same call (simple facts, greetings, social chat)
    complex = needs multi-step work, file operations, research, goal creation
- "affirmation" — agreeing with something ("sure", "go ahead", "yes please", "sounds good")
    If suggestions are pending → treat as suggestion_pick #1
    If approval is pending → treat as approval: true
    If question is pending → treat as question_reply
- "suggestion_pick" — ONLY when the user clearly ACCEPTS/APPROVES a suggestion ("1", "do 2",
    "option 3", "#2", "do 1 and 3", "all of them", "the first two", "go ahead with 2")
    Set pick_number to the first/only pick. For multiple picks, also set pick_numbers to the full list.
    "all of them" / "all" / "do everything" → pick_numbers = [1, 2, 3, ...] (all pending suggestions)
    IMPORTANT: Asking about a suggestion is NOT a pick. "tell me more about 2", "what does #3 mean",
    "can you explain option 1", "what would that involve" → these are questions, NOT acceptance.
    Classify these as "new_request" with tier "easy" and answer the question about the suggestion.
- "approval" — responding to an approval request. Set approval: true or false
    "yes"/"sure"/"go ahead" → true. "no"/"nah"/"don't" → false.
    Handles natural language: "No, I don't think you need to do that" → false
    "I have no idea what any of that is, but go ahead I guess" → true (affirmative despite confusion)
- "question_reply" — answering a pending question from Archi
    The answer IS the message content (pass through as-is)
- "clarification" — clarifying a previous message ("I meant the other one", "no, the blue one")
- "cancel" — stop/cancel/abort the current task
- "greeting" — pure social interaction with no substantive request
    tier: easy, include a contextual greeting as answer
- "accumulation" — adding an item to a multi-message list
    Set accumulation_item to the item text
    Set accumulation_done: true if user signals done ("that's all", "done", "go ahead")

IMPORTANT — USER STATEMENTS vs. REQUESTS:
When Jesse says "I'll…", "I'm going to…", "let me…" followed by a verb, he is usually
describing what HE plans to do — NOT asking Archi to do it.  Treat these as easy-tier
affirmations or acknowledgements unless the message CLEARLY asks Archi to act.
Examples that are NOT requests to Archi:
- "I'll look into that" → affirmation / acknowledgement
- "I'll see if I can figure out why it failed" → acknowledgement, easy tier
- "Let me check on that" → acknowledgement, easy tier
- "I'm going to try restarting it" → informational, easy tier
Contrast with actual requests:
- "Look into why it failed" → complex / multi_step (imperative directed at Archi)
- "Can you figure out why it failed?" → complex / multi_step (question directed at Archi)
- "See if you can figure out why it failed" → complex / multi_step (explicit "you")

COMPLEXITY ROUTING (for complex tier):
- "goal" — anything that involves building, creating, advancing a project, multi-file work,
  or tasks that will take more than a quick answer. Prefer this for non-trivial work.
- "multi_step" — quick tasks the user is waiting for: a few web searches, brief analysis,
  single file edit. NOT for projects or system building.
- "coding" — explicit code modification requests (add function, fix bug, edit file, refactor).

USER SIGNALS — As a side effect, extract any preference/correction signals:
- preference: "I prefer tabs over spaces" → {"type": "preference", "text": "Prefers tabs over spaces"}
- correction: "don't use bullet points" → {"type": "correction", "text": "Don't use bullet points"}
- pattern: if you notice a decision pattern → {"type": "pattern", "text": "..."}
- style: communication style notes → {"type": "style", "text": "..."}
Only include genuine signals. Most messages won't have any — return empty array.

COMMUNICATION STYLE for easy-tier answers:
- Talk like a person, not a bot. Be direct, skip filler.
- Match Jesse's energy. Short message → short reply.
- For greetings: include time-appropriate greeting + brief status if you know it.

JSON only. No markdown, no explanation outside the JSON."""


def _build_router_prompt(
    message: str,
    context: ContextState,
    user_model_context: str = "",
    history_snippet: str = "",
) -> str:
    """Build the user-turn prompt for the Router model call."""
    parts = [f'Message: "{message}"']

    # Context state
    state_parts = []
    if context.pending_suggestions:
        suggestions_text = "\n".join(
            f"  {i+1}. {s}" for i, s in enumerate(context.pending_suggestions)
        )
        state_parts.append(f"Pending suggestions:\n{suggestions_text}")
    elif context.recent_suggestions:
        # No active pending suggestions, but show recently dismissed ones
        # in case user wants to circle back to an old idea
        recent_text = "\n".join(
            f"  - {s}" for s in context.recent_suggestions[-5:]
        )
        state_parts.append(
            f"Recently suggested (no longer pending, but user may reference):\n{recent_text}"
        )
    if context.pending_approval:
        state_parts.append("Pending approval: YES (waiting for yes/no on a source modification)")
    if context.pending_question:
        state_parts.append("Pending question: YES (Archi asked Jesse a question, waiting for reply)")
    if context.active_goals:
        state_parts.append(f"Active goals: {len(context.active_goals)}")
    if context.accumulating:
        state_parts.append(
            f"Accumulating items for: {context.accumulation_prompt}\n"
            f"Items so far: {context.accumulated_items}"
        )

    if state_parts:
        parts.append("Context state:\n" + "\n".join(state_parts))

    if user_model_context:
        parts.append(user_model_context)

    if history_snippet:
        parts.append(f"Recent conversation:\n{history_snippet}")

    parts.append("Classify this message. JSON only:")
    return "\n\n".join(parts)


# ── Main Router entry point ──────────────────────────────────────────

def route(
    message: str,
    router: Any,
    context: ContextState,
    history_messages: Optional[list] = None,
    goal_manager: Any = None,
) -> RouterResult:
    """Route an inbound message. Single model call.

    Args:
        message: Raw user message text.
        router: ModelRouter instance for API calls.
        context: Current conversation context state.
        history_messages: Recent chat history for the model.
        goal_manager: GoalManager for slash commands.

    Returns:
        RouterResult with intent, tier, and (for easy) the answer.
    """
    msg_lower = (message or "").strip().lower()

    # ── 1. Local fast-paths (no API call) ────────────────────────
    fast = _check_local_fast_paths(message, msg_lower, goal_manager)
    if fast is not None:
        return fast

    # ── 1b. Deferred request detection (no API call) ─────────────
    deferred_desc = _is_deferred_request(message)
    if deferred_desc:
        return RouterResult(
            intent="new_request",
            tier="easy",
            action="deferred_request",
            action_params={"description": deferred_desc},
            fast_path=True,
        )

    # ── 2. Check accumulation timeout ────────────────────────────
    global _accumulation
    if _accumulation and _accumulation.is_timed_out():
        # Auto-finalize: return collected items
        items = list(_accumulation.items)
        clear_accumulation()
        return RouterResult(
            intent="accumulation",
            accumulated_items=items,
            accumulation_done=True,
            fast_path=True,
        )

    # ── 3. Build context for Router ──────────────────────────────
    user_model_ctx = ""
    try:
        from src.core.user_model import get_user_model
        user_model_ctx = get_user_model().get_context_for_router()
    except Exception:
        pass

    # Build history snippet (last few exchanges, compact)
    history_snippet = ""
    if history_messages:
        lines = []
        for m in history_messages[-8:]:
            role = m.get("role", "user")
            content = (m.get("content") or "")[:200]
            if content:
                prefix = "Jesse:" if role == "user" else "Archi:"
                lines.append(f"{prefix} {content}")
        if lines:
            history_snippet = "\n".join(lines)

    user_prompt = _build_router_prompt(
        message, context, user_model_ctx, history_snippet,
    )

    # ── 4. Single model call ─────────────────────────────────────
    messages = [
        {"role": "system", "content": _ROUTER_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    resp = router.generate(max_tokens=500, temperature=0.2, messages=messages)
    cost = resp.get("cost_usd", 0)
    text = resp.get("text", "")

    if not resp.get("success", True):
        # API failure — return a safe fallback
        return RouterResult(
            intent="new_request",
            tier="complex",
            complexity="multi_step",
            cost=cost,
        )

    parsed = extract_json(text)
    if not parsed:
        # JSON parse failed — retry with simplified prompt
        logger.info("Router JSON parse failed, retrying with simplified prompt")
        retry_resp = router.generate(
            prompt=(
                f'User said: "{message}"\n\n'
                f"Classify as JSON: "
                f'{{"intent":"new_request","tier":"easy","answer":"your reply"}} '
                f"or "
                f'{{"intent":"new_request","tier":"complex","complexity":"goal"}}. '
                f"JSON only:"
            ),
            max_tokens=300, temperature=0.1,
        )
        cost += retry_resp.get("cost_usd", 0)
        parsed = extract_json(retry_resp.get("text", ""))

    if not parsed:
        # Total failure — fall through as complex request
        return RouterResult(
            intent="new_request",
            tier="complex",
            complexity="goal",
            cost=cost,
        )

    # ── 5. Parse Router response ─────────────────────────────────
    result = _parse_router_response(parsed, context)
    result.cost = cost

    # ── 6. Extract user signals (side effect) ────────────────────
    try:
        from src.core.user_model import extract_user_signals
        extract_user_signals(message, parsed)
    except Exception:
        pass
    try:
        from src.utils.project_sync import sync_signals_to_project_context
        sync_signals_to_project_context(parsed.get("user_signals", []))
    except Exception:
        pass

    # ── 7. Handle accumulation ───────────────────────────────────
    if result.intent == "accumulation" and _accumulation:
        item = (parsed.get("accumulation_item") or "").strip()
        if item:
            _accumulation.items.append(item)
            _accumulation.last_item_at = time.time()
            result.accumulated_items = list(_accumulation.items)
        if result.accumulation_done or parsed.get("accumulation_done"):
            result.accumulated_items = list(_accumulation.items)
            result.accumulation_done = True
            clear_accumulation()

    # Sanitize easy-tier answer
    if result.tier == "easy" and result.answer:
        result.answer = sanitize_identity(strip_thinking(result.answer))

    return result


def _parse_router_response(
    parsed: Dict[str, Any], context: ContextState,
) -> RouterResult:
    """Convert parsed JSON from the Router into a RouterResult."""
    intent = (parsed.get("intent") or "new_request").lower()
    tier = (parsed.get("tier") or "complex").lower()
    answer = (parsed.get("answer") or "").strip()
    complexity = (parsed.get("complexity") or "").lower()
    action = (parsed.get("action") or "").strip()
    action_params = parsed.get("action_params") or {}

    result = RouterResult(
        intent=intent,
        tier=tier,
        answer=answer,
        complexity=complexity,
        action=action,
        action_params=action_params,
        user_signals=parsed.get("user_signals") or [],
    )

    # ── Intent-specific parsing ──────────────────────────────────

    if intent == "suggestion_pick":
        result.pick_number = int(parsed.get("pick_number") or 0)
        # Parse multi-pick list
        raw_picks = parsed.get("pick_numbers") or []
        if isinstance(raw_picks, list):
            result.pick_numbers = [int(p) for p in raw_picks if isinstance(p, (int, float)) and p > 0]
        # If pick_numbers provided but pick_number wasn't, use first from list
        if not result.pick_number and result.pick_numbers:
            result.pick_number = result.pick_numbers[0]
        # If only pick_number provided, populate pick_numbers from it
        if result.pick_number and not result.pick_numbers:
            result.pick_numbers = [result.pick_number]
        # Validate all picks against pending suggestions
        if context.pending_suggestions:
            max_idx = len(context.pending_suggestions)
            result.pick_numbers = [p for p in result.pick_numbers if 1 <= p <= max_idx]
            if result.pick_number < 1 or result.pick_number > max_idx:
                result.pick_number = result.pick_numbers[0] if result.pick_numbers else 0

    elif intent == "affirmation":
        # Resolve affirmation based on context
        if context.pending_suggestions:
            result.intent = "suggestion_pick"
            result.pick_number = 1  # Default to first suggestion
            result.pick_numbers = [1]
        elif context.pending_approval:
            result.intent = "approval"
            result.approval = True
        elif context.pending_question:
            result.intent = "question_reply"

    elif intent == "approval":
        result.approval = parsed.get("approval")
        if result.approval is None:
            # Try to infer from answer text
            lower = answer.lower()
            result.approval = not any(
                w in lower for w in ("no", "deny", "denied", "don't", "nah", "nope")
            )

    elif intent == "accumulation":
        item = (parsed.get("accumulation_item") or "").strip()
        if item:
            result.accumulated_items = [item]
        result.accumulation_done = bool(parsed.get("accumulation_done"))

    elif intent == "cancel":
        result.tier = "easy"  # Cancel is always handled directly

    elif intent == "greeting":
        result.tier = "easy"

    # ── Tier validation ──────────────────────────────────────────

    if tier == "easy" and not answer and intent not in (
        "suggestion_pick", "approval", "question_reply",
        "cancel", "accumulation",
    ):
        # Easy tier must have an answer (or be a special intent)
        result.tier = "complex"

    if tier == "complex" and not complexity:
        result.complexity = "goal"  # Default complex routing

    return result
