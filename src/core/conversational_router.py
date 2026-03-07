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
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.utils.config import (
    get_persona_prompt_cached,
    get_relevant_quote,
    get_user_name,
    on_reload as _on_config_reload,
)
from src.utils.fast_paths import (
    is_datetime_question as _is_datetime_question,
    is_screenshot_request as _is_screenshot_request,
    extract_image_prompt as _extract_image_prompt,
    is_cost_query as _is_cost_query,
)
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
    config_requests: List[str] = field(default_factory=list)  # Detected config change requests
    mood_signal: str = ""                # Detected user mood (session 201)


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
_accumulation_lock = threading.Lock()


def start_accumulation(task_id: str, prompt: str) -> None:
    """Begin collecting multi-message items for a task."""
    global _accumulation
    with _accumulation_lock:
        _accumulation = _AccumulationState(task_id, prompt)
    logger.info("Input accumulation started for task %s: %s", task_id, prompt[:60])


def get_accumulation_state() -> Optional[_AccumulationState]:
    """Return current accumulation state, or None."""
    return _accumulation


def clear_accumulation() -> None:
    """Clear the accumulation state."""
    global _accumulation
    with _accumulation_lock:
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

    # Cost / spending meta-questions (introspective, no model call needed)
    if _is_cost_query(msg_lower):
        return RouterResult(
            intent="easy_answer",
            tier="easy",
            action="cost_report",
            fast_path=True,
        )

    return None



# Fast-path helpers imported from src.utils.fast_paths:
# _is_datetime_question, _is_screenshot_request, _extract_image_prompt


def _handle_slash_command(
    msg_lower: str, message: str, goal_manager: Any,
) -> Optional[RouterResult]:
    """Handle /commands as fast-path results."""
    if msg_lower.startswith("/goal "):
        if not goal_manager:
            return RouterResult(
                intent="easy_answer", tier="easy",
                action="error",
                answer="Goal system is still starting up. Try again in a few seconds.",
                fast_path=True,
            )
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
    if msg_lower.startswith("/skill"):
        return _handle_skill_command(msg_lower, message)
    if msg_lower.startswith("/schedule") or msg_lower.startswith("/reminders"):
        return _handle_schedule_command(msg_lower, message)
    if msg_lower.startswith("/"):
        return RouterResult(
            intent="easy_answer", tier="easy",
            action="unknown_command",
            answer=f"Unknown command: `{message.split()[0]}`. Type /help for available commands.",
            fast_path=True,
        )
    return None


def _handle_skill_command(
    msg_lower: str, message: str,
) -> RouterResult:
    """Handle /skill commands for the self-extending skill system."""
    parts = message.split(maxsplit=2)
    if len(parts) < 2:
        return RouterResult(
            intent="easy_answer", tier="easy",
            answer=(
                "**Skill commands:**\n"
                "  `/skill list` — Show available skills\n"
                "  `/skill create <description>` — Create a new skill\n"
                "  `/skill info <name>` — Show skill details"
            ),
            fast_path=True,
        )

    subcommand = parts[1].lower() if len(parts) > 1 else ""

    if subcommand == "list":
        try:
            from src.core.skill_system import get_shared_skill_registry
            registry = get_shared_skill_registry()
            skills = registry.get_available_skills()
            if not skills:
                text = "No skills created yet. Use `/skill create <description>` to make one!"
            else:
                lines = [f"**Available skills ({len(skills)}):**"]
                for name in skills:
                    info = registry.get_skill_info(name)
                    desc = info.get("description", "")[:60] if info else ""
                    rate = info.get("success_rate", "") if info else ""
                    rate_str = f" ({rate})" if rate and rate != "0%" else ""
                    lines.append(f"  - `skill_{name}`{rate_str}: {desc}")
                text = "\n".join(lines)
            return RouterResult(
                intent="easy_answer", tier="easy",
                answer=text, fast_path=True,
            )
        except Exception as e:
            return RouterResult(
                intent="easy_answer", tier="easy",
                answer=f"Error loading skills: {e}",
                fast_path=True,
            )

    if subcommand == "info" and len(parts) > 2:
        skill_name = parts[2].strip().replace("skill_", "")
        try:
            from src.core.skill_system import get_shared_skill_registry
            info = get_shared_skill_registry().get_skill_info(skill_name)
            if info:
                text = (
                    f"**{info['name']}** (v{info['version']})\n"
                    f"{info['description']}\n"
                    f"Risk: {info['risk_level']} | "
                    f"Invocations: {info['invocations']} | "
                    f"Success rate: {info['success_rate']}"
                )
            else:
                text = f"Skill '{skill_name}' not found. Try `/skill list`."
            return RouterResult(
                intent="easy_answer", tier="easy",
                answer=text, fast_path=True,
            )
        except Exception as e:
            return RouterResult(
                intent="easy_answer", tier="easy",
                answer=f"Error: {e}", fast_path=True,
            )

    if subcommand == "create":
        desc = parts[2].strip() if len(parts) > 2 else ""
        if not desc:
            return RouterResult(
                intent="easy_answer", tier="easy",
                answer="Usage: `/skill create <description of what the skill should do>`",
                fast_path=True,
            )
        # Route to skill creator (not goal creation)
        return RouterResult(
            intent="easy_answer", tier="easy",
            action="create_skill",
            action_params={"description": desc},
            fast_path=True,
        )

    return RouterResult(
        intent="easy_answer", tier="easy",
        answer=(
            f"Unknown skill subcommand: `{subcommand}`. "
            "Try `/skill list`, `/skill create`, or `/skill info`."
        ),
        fast_path=True,
    )


def _handle_schedule_command(
    msg_lower: str, message: str,
) -> RouterResult:
    """Handle /schedule and /reminders commands (session 196)."""
    parts = message.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    if not arg or arg.lower() in ("list", "ls"):
        return RouterResult(
            intent="easy_answer", tier="easy",
            action="list_schedule", fast_path=True,
        )

    return RouterResult(
        intent="easy_answer", tier="easy",
        answer=(
            "**Schedule commands:**\n"
            "  `/schedule` or `/reminders` — List all scheduled tasks\n\n"
            "To create, modify, or remove schedules, just ask naturally:\n"
            '  "Remind me to stretch every day at 4:15"\n'
            '  "Stop the stretch reminder"\n'
            '  "Change my morning reminder to 8:30"'
        ),
        fast_path=True,
    )


# ── Router prompt ────────────────────────────────────────────────────

_router_system_cache: Optional[str] = None


def _router_system() -> str:
    """Return the Router system prompt, cached across calls.

    The prompt depends on get_user_name() and get_persona_prompt_cached(),
    both of which are stable until config.reload() is called. Caching
    avoids rebuilding the ~3KB prompt on every message.
    """
    global _router_system_cache
    if _router_system_cache is not None:
        return _router_system_cache
    _router_system_cache = _build_router_system()
    return _router_system_cache


def invalidate_router_cache() -> None:
    """Clear the cached system prompt (call after config.reload())."""
    global _router_system_cache
    _router_system_cache = None


# Auto-invalidate when config is reloaded
_on_config_reload(invalidate_router_cache)


def _build_router_system() -> str:
    """Build the Router system prompt with config-driven user name."""
    user_name = get_user_name()
    return f"""You are the Conversational Router for Archi, an AI agent for {user_name}.
Your job: classify each inbound message and decide how to handle it.

Return ONLY a JSON object with these fields:
{{
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
  "action_params": {{}},
  "user_signals": [{{"type": "preference|correction|pattern|style", "text": "what you observed"}}],
  "mood_signal": "optional: busy, frustrated, excited, engaged, neutral, tired, playful — read the room"
}}

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
- "clarification" — correcting or clarifying a previous message ("I meant the other one",
    "no, the blue one", "that's wrong, do X instead", "no I meant X").
    These are CONVERSATIONAL CORRECTIONS — NOT new goals. Always tier "easy".
    If the correction includes a new directive ("do X instead"), set the answer to acknowledge
    the correction and address the revised request.
- "cancel" — stop/cancel/abort the current task
- "greeting" — pure social interaction with no substantive request
    tier: easy, include a contextual greeting as answer
- "accumulation" — adding an item to a multi-message list
    Set accumulation_item to the item text
    Set accumulation_done: true if user signals done ("that's all", "done", "go ahead")
- "schedule" — creating, modifying, removing, or listing scheduled tasks/reminders
    tier: easy. Set action to one of: create_schedule, modify_schedule, remove_schedule, list_schedule
    For create_schedule, set action_params with: description, cron (5-field cron expr), action ("notify"
    or "create_goal"), payload (message text or goal description).
    For modify_schedule, set action_params with: task_id, plus any fields to change (cron, description,
    payload, enabled).
    For remove_schedule, set action_params with: task_id.
    For list_schedule, no params needed.
    Examples:
      "Remind me to stretch every day at 4:15" → create_schedule, cron "15 16 * * *"
      "Every Monday morning, summarize the week" → create_schedule, cron "0 9 * * 1"
      "Stop the stretch reminder" → remove_schedule, task_id "stretch" (best guess)
      "Change my morning reminder to 8:30" → modify_schedule, task_id + cron
      "What reminders do I have?" → list_schedule
      "Don't remind me about that anymore" → remove_schedule
- "email" — sending, checking, or searching Archi's email inbox
    tier: easy. Set action to one of: send_email, check_email, search_email
    For send_email, set action_params with: to (email address), subject, body.
    For check_email, set action_params with: max_count (optional, default 5), unread_only (optional, default true).
    For search_email, set action_params with: query (IMAP search string, e.g., 'FROM user@example.com').
    Examples:
      "Check my email" → check_email
      "Any new emails?" → check_email, unread_only true
      "Send an email to bob@example.com about the project" → send_email, to, subject, body
      "Search emails from Amazon" → search_email, query 'FROM Amazon'
- "digest" — on-demand morning briefing (weather + calendar + inbox + news)
    tier: easy. action: morning_digest. No params needed.
    Examples:
      "Give me a digest" → morning_digest
      "Morning briefing" → morning_digest
      "What's the weather and news?" → morning_digest
      "Briefing" → morning_digest
- "calendar" — check upcoming calendar events
    tier: easy. action: check_calendar. No params needed.
    Examples:
      "What's on my calendar?" → check_calendar
      "Any meetings today?" → check_calendar
      "What do I have coming up?" → check_calendar
      "Check my calendar" → check_calendar
      "Calendar" → check_calendar
- "content" — creating, publishing, or managing content (blog posts, tweets, reddit posts)
    tier: easy. Set action to one of: create_content, publish_content, list_content
    For create_content, set action_params with: topic (what to write about), format (one of: blog, tweet, tweet_thread, reddit), extra_context (optional audience/tone notes).
    For publish_content, set action_params with: platform (github_blog, twitter, reddit), title (optional), subreddit (for reddit only).
    For list_content, no params needed.
    Examples:
      "Write a blog post about AI trends" → create_content, format "blog", topic "AI trends"
      "Create a tweet about the latest tech news" → create_content, format "tweet", topic "latest tech news"
      "Publish that to the blog" → publish_content, platform "github_blog"
      "Post that on Reddit in r/artificial" → publish_content, platform "reddit", subreddit "artificial"
      "What have I published?" → list_content
      "Show my content log" → list_content

IMPORTANT — USER STATEMENTS vs. REQUESTS:
When the user says "I'll…", "I'm going to…", "let me…" followed by a verb, they are usually
describing what THEY plan to do — NOT asking Archi to do it.  Treat these as easy-tier
affirmations or acknowledgements unless the message CLEARLY asks Archi to act.

THINKING OUT LOUD — NOT ACTIONABLE:
Casual remarks, musings, hedging, and notes-to-self are NOT requests.  These should be
tier "easy" with a brief conversational reply (acknowledgement, agreement, or light comment).
Do NOT create goals or trigger complex work for these.  Key signals:
- Conditional/uncertain language: "I think…", "maybe…", "I wonder if…", "we might…",
  "probably should…", "could be worth…", "might need to…"
- Vague timeframes with no directive: "we'll have to…", "at some point we should…",
  "eventually…", "one of these days…"
- Observations: "that's interesting", "hmm", "huh", "good to know", "noted"
- Notes to self: "note to self…", "something to keep in mind…", "I should remember…"

Examples that are NOT requests to Archi:
- "I'll look into that" → acknowledgement, easy tier
- "I'll see if I can figure out why it failed" → acknowledgement, easy tier
- "Let me check on that" → acknowledgement, easy tier
- "I'm going to try restarting it" → informational, easy tier
- "I think we'll have to check on that" → musing, easy tier
- "hmm that's interesting" → observation, easy tier
- "maybe later" → deferral, easy tier
- "I wonder if that's related to the other issue" → thinking aloud, easy tier
- "note to self: look into X" → note to self, easy tier (NOT a goal)
- "we might need to revisit that" → musing, easy tier
- "probably should clean that up at some point" → vague musing, easy tier
- "could be worth looking into" → idle thought, easy tier

Contrast with actual requests:
- "Look into why it failed" → complex / multi_step (imperative directed at Archi)
- "Can you figure out why it failed?" → complex / multi_step (question directed at Archi)
- "See if you can figure out why it failed" → complex / multi_step (explicit "you")
- "Check on that for me" → complex (explicit delegation to Archi)
- "When you have time, look into X" → deferred request (has clear action verb + delegation)

DEFERRED REQUESTS — action: "deferred_request":
A deferred request is when the user EXPLICITLY delegates a task to Archi with a low-urgency
signal. Use action "deferred_request" with action_params {{"description": "<task>"}} ONLY when
ALL of these are true:
  1. There is a CLEAR action verb directed at Archi (look into, research, check, find, etc.)
  2. The user is telling Archi to do it (not themselves) — uses "you" or imperative form
  3. There is a deferral signal (when you have time, later, no rush, etc.)
Examples that ARE deferred requests:
- "When you have time, research nootropics for focus" → deferred_request
- "Can you look into sleep trackers later?" → deferred_request
- "Remind me to check the server logs" → deferred_request
Examples that are NOT deferred requests:
- "I'll review them again later" → the user talking about themselves, easy tier
- "yeah just thinking out loud so it's in the logs when I review them later" → musing, easy tier
- "we should probably look into that eventually" → vague musing, easy tier
- "that might be worth looking into later" → idle thought, easy tier
The word "later" alone does NOT make something a deferred request. The user must be clearly
asking Archi to do something.

RULE OF THUMB: If the message lacks a clear imperative verb directed at Archi (no "you",
no command form, no "can you", no "please"), default to easy tier.  When in doubt, treat
it as conversational — it's better to under-classify than to spawn unwanted goals.

COMPLEXITY ROUTING (for complex tier):
- "goal" — anything that involves building, creating, advancing a project, multi-file work,
  or tasks that will take more than a quick answer. Prefer this for non-trivial work.
- "multi_step" — quick tasks the user is waiting for: a few web searches, brief analysis,
  single file edit. NOT for projects or system building.
- "coding" — explicit code modification requests (add function, fix bug, edit file, refactor).

USER SIGNALS — As a side effect, extract any personal facts, preferences, or corrections:
- fact: personal/biographical info the user shares about themselves
- preference: "I prefer tabs over spaces" → {{"type": "preference", "text": "Prefers tabs over spaces"}}
- correction: "don't use bullet points" → {{"type": "correction", "text": "Don't use bullet points"}}
- pattern: if you notice a decision pattern → {{"type": "pattern", "text": "..."}}
- style: communication style notes → {{"type": "style", "text": "..."}}
- config_request: The user is asking Archi to change its own configuration, rules, identity, or behavior files.
  Examples: "add X to your prime directive", "change your rules to allow Y", "update your identity to Z",
  "make yourself more casual", "stop doing X" (when it implies a rules/config change).
  The text should describe what change was requested.
  IMPORTANT: Always capture these — they indicate the user wants a config change that Archi can't
  autonomously apply (protected files). The preference/correction is ALSO stored, but this flag
  ensures the user is notified that the actual file wasn't modified.

FACTS — BE AGGRESSIVE. Extract ANY personal info the user shares:
  "I'm 32" → {{"type": "fact", "text": "32 years old"}}
  "I weigh 175 lbs" → {{"type": "fact", "text": "Weighs 175 lbs"}}
  "I'm about 5'10" → {{"type": "fact", "text": "5'10\" tall"}}
  "I'm half Filipino" → {{"type": "fact", "text": "Half Filipino"}}
  "I have a Rat Terrier" → {{"type": "fact", "text": "Has a Rat Terrier"}}
  "I work in finance" → {{"type": "fact", "text": "Works in finance"}}
  "I have three kids" → {{"type": "fact", "text": "Has three children"}}
  "I work nights" → {{"type": "fact", "text": "Works night shifts"}}
  "I play guitar" → {{"type": "fact", "text": "Plays guitar"}}
Capture: age, height, weight, ethnicity, health, skills, hobbies, job, family, pets,
location, schedule, anything about the user as a person. This info is VALUABLE — never skip it.
Only include genuine signals. Most messages won't have any — return empty array.

MOOD SIGNAL — Read the room. Set mood_signal based on the user's tone:
- "busy" — short/terse messages, minimal words, clearly multitasking
- "frustrated" — complaints, things not working, exasperation
- "excited" — enthusiasm, exclamation marks, eager language
- "engaged" — detailed messages, follow-up questions, deep in a topic
- "tired" — low energy, short responses, time of day cues
- "playful" — jokes, banter, casual tone
- "neutral" — normal, no strong signal
Read tone from message length, punctuation, word choice, and conversation flow.
When the user seems busy or frustrated, keep responses SHORT and direct.
When engaged or excited, be more conversational and share more.

COMMUNICATION STYLE for easy-tier answers:
{get_persona_prompt_cached()}

You remember things the user has told you and reference them naturally — "Since you work nights,
that timing makes sense" not "According to my records, you work night shifts."

Core rules:
- Match the user's energy. Short message → short reply. Banter → banter back.
- Lead with substance. Skip filler openings and don't restate what the user just said.
- Vary your openings and phrasing. Never start every reply the same way.
- When the user shares something personal, engage genuinely — react, follow up, connect it
  to something you know about them. Don't just acknowledge and pivot to tasks.
- Have opinions when asked. Commit to a position with reasoning. Don't hedge into mush.
- Use conversation history — reference what was just discussed, callback to earlier points.
- Humor is observational and situational, not constant. The joke is noticing the obvious
  thing nobody said yet. If nothing's funny, just be competent and warm.
- When things go wrong, get quieter and more precise — not louder and more apologetic.

GOOD examples:
  User: "I'm thinking about picking up woodworking"
  → "Oh nice — that's a solid hobby. You've got the patience for it too. Any particular projects in mind, or just exploring?"

  User: "good morning"
  → "Morning! Been pretty quiet overnight — no fires. How'd you sleep?"

  User: "what do you think about rust vs go?"
  → "For what you're doing? Go, probably. Faster to get productive, and the concurrency model is cleaner for the kind of services you'd build. Rust is cool but the learning curve is steep if you just want to ship."

  User: "this API keeps timing out"
  → "Third time this hour. At some point it stops being a timeout and starts being a policy. Want me to look at retry logic or try a different endpoint?"

BAD examples (don't do this):
  User: "I'm thinking about picking up woodworking"
  → "That sounds like a great idea! Woodworking can be a rewarding hobby. Would you like me to research woodworking resources for you?"

  User: "good morning"
  → "Good morning! I'm here and ready. How can I help you today?"

  User: "what do you think about rust vs go?"
  → "Both Rust and Go are excellent languages with their own strengths. Rust offers memory safety while Go provides simplicity. The best choice depends on your specific use case."

JSON only. No markdown, no explanation outside the JSON."""


def _build_router_prompt(
    message: str,
    context: ContextState,
    user_model_context: str = "",
    history_snippet: str = "",
    conversation_memories: Optional[List[str]] = None,
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
        state_parts.append("Pending question: YES (Archi asked the user a question, waiting for reply)")
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

    # Inject worldview context for personality-consistent responses (session 199)
    try:
        from src.core.worldview import get_worldview_context
        wv_ctx = get_worldview_context(max_chars=400)
        if wv_ctx:
            parts.append(f"Your evolving worldview (from experience):\n{wv_ctx}")
    except Exception:
        pass  # worldview unavailable — non-critical

    # Inject meta-cognition + project context (session 203)
    try:
        from src.core.worldview import get_meta_context, get_project_context
        meta_ctx = get_meta_context(max_chars=200)
        if meta_ctx:
            parts.append(meta_ctx)
        proj_ctx = get_project_context(max_chars=200)
        if proj_ctx:
            parts.append(proj_ctx)
    except Exception:
        pass

    # Inject mood context for behavioral adjustment (session 201)
    try:
        from src.core.user_model import get_user_model
        mood_ctx = get_user_model().get_mood_context()
        if mood_ctx:
            parts.append(mood_ctx)
    except Exception:
        pass

    if conversation_memories:
        mem_lines = "\n".join(f"- {m}" for m in conversation_memories[:3])
        parts.append(f"Relevant past conversations:\n{mem_lines}")

    if history_snippet:
        parts.append(f"Recent conversation:\n{history_snippet}")

    # Occasionally inject a relevant guiding quote for personality color
    quote = get_relevant_quote(message)
    if quote:
        parts.append(
            f'If it fits naturally in your easy-tier answer, you may weave in '
            f'this thought: "{quote["text"]}" — {quote["source"]}. '
            f'Don\'t force it — skip if it doesn\'t fit.'
        )

    parts.append("Classify this message. JSON only:")
    return "\n\n".join(parts)


# ── Main Router entry point ──────────────────────────────────────────

def route(
    message: str,
    router: Any,
    context: ContextState,
    history_messages: Optional[list] = None,
    goal_manager: Any = None,
    memory: Any = None,
) -> RouterResult:
    """Route an inbound message. Single model call.

    Args:
        message: Raw user message text.
        router: ModelRouter instance for API calls.
        context: Current conversation context state.
        history_messages: Recent chat history for the model.
        goal_manager: GoalManager for slash commands.
        memory: Optional MemoryManager for conversation memory retrieval.

    Returns:
        RouterResult with intent, tier, and (for easy) the answer.
    """
    msg_lower = (message or "").strip().lower()

    # ── 1. Local fast-paths (no API call) ────────────────────────
    fast = _check_local_fast_paths(message, msg_lower, goal_manager)
    if fast is not None:
        return fast

    # ── 2. Check accumulation timeout ────────────────────────────
    global _accumulation
    with _accumulation_lock:
        if _accumulation and _accumulation.is_timed_out():
            # Auto-finalize: return collected items
            items = list(_accumulation.items)
            _accumulation = None
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
        user_model_ctx = get_user_model().get_context_for_chat()
    except Exception:
        pass

    # Build history snippet (last few exchanges, compact)
    history_snippet = ""
    if history_messages:
        lines = []
        for m in history_messages[-8:]:
            role = m.get("role", "user")
            content = (m.get("content") or "")[:400]
            if content:
                user_name = get_user_name()
                prefix = f"{user_name}:" if role == "user" else "Archi:"
                lines.append(f"{prefix} {content}")
        if lines:
            history_snippet = "\n".join(lines)

    # Retrieve relevant conversation memories from long-term storage
    conversation_memories: Optional[list] = None
    if memory:
        try:
            conversation_memories = memory.get_conversation_context(message, n_results=3)
        except Exception:
            pass

    user_prompt = _build_router_prompt(
        message, context, user_model_ctx, history_snippet,
        conversation_memories=conversation_memories,
    )

    # ── 4. Single model call ─────────────────────────────────────
    messages = [
        {"role": "system", "content": _router_system()},
        {"role": "user", "content": user_prompt},
    ]

    resp = router.generate(max_tokens=650, temperature=0.35, messages=messages)
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
        logger.warning("Router: JSON parsing failed after retry, falling back to complex/goal for: %s", message[:120])
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
        config_reqs = extract_user_signals(message, parsed)
        if config_reqs:
            result.config_requests = config_reqs
    except Exception:
        pass
    try:
        from src.utils.project_sync import sync_signals_to_project_context
        sync_signals_to_project_context(parsed.get("user_signals", []))
    except Exception:
        pass

    # ── 6b. Store mood signal (session 201) ──────────────────────
    if result.mood_signal:
        try:
            from src.core.user_model import get_user_model
            get_user_model().record_mood(result.mood_signal)
        except Exception:
            pass
        try:
            from src.core.journal import add_entry
            add_entry("mood_signal", result.mood_signal, {"source": "router"})
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


def _parse_suggestion_pick(
    parsed: Dict[str, Any], result: RouterResult, context: ContextState,
) -> None:
    """Parse suggestion_pick fields: pick_number, pick_numbers, validation."""
    result.pick_number = int(parsed.get("pick_number") or 0)
    raw_picks = parsed.get("pick_numbers") or []
    if isinstance(raw_picks, list):
        result.pick_numbers = [int(p) for p in raw_picks if isinstance(p, (int, float)) and p > 0]
    # Reconcile: if only one side was provided, derive the other
    if not result.pick_number and result.pick_numbers:
        result.pick_number = result.pick_numbers[0]
    if result.pick_number and not result.pick_numbers:
        result.pick_numbers = [result.pick_number]
    # Validate against pending suggestions
    if context.pending_suggestions:
        max_idx = len(context.pending_suggestions)
        result.pick_numbers = [p for p in result.pick_numbers if 1 <= p <= max_idx]
        if result.pick_number < 1 or result.pick_number > max_idx:
            result.pick_number = result.pick_numbers[0] if result.pick_numbers else 0


def _resolve_affirmation(result: RouterResult, context: ContextState) -> None:
    """Resolve an affirmation intent based on what's currently pending."""
    if context.pending_suggestions:
        result.intent = "suggestion_pick"
        result.pick_number = 1
        result.pick_numbers = [1]
    elif context.pending_approval:
        result.intent = "approval"
        result.approval = True
    elif context.pending_question:
        result.intent = "question_reply"


def _parse_router_response(
    parsed: Dict[str, Any], context: ContextState,
) -> RouterResult:
    """Convert parsed JSON from the Router into a RouterResult."""
    intent = (parsed.get("intent") or "new_request").lower()
    tier = (parsed.get("tier") or "complex").lower()
    answer = (parsed.get("answer") or "").strip()
    complexity = (parsed.get("complexity") or "").lower()

    result = RouterResult(
        intent=intent, tier=tier, answer=answer, complexity=complexity,
        action=(parsed.get("action") or "").strip(),
        action_params=parsed.get("action_params") or {},
        user_signals=parsed.get("user_signals") or [],
        mood_signal=(parsed.get("mood_signal") or "").strip().lower(),
    )

    # ── Intent-specific parsing ──────────────────────────────────

    if intent == "suggestion_pick":
        _parse_suggestion_pick(parsed, result, context)
    elif intent == "affirmation":
        _resolve_affirmation(result, context)
    elif intent == "approval":
        result.approval = parsed.get("approval")
        if result.approval is None:
            lower = answer.lower()
            result.approval = not any(
                w in lower for w in ("no", "deny", "denied", "don't", "nah", "nope")
            )
    elif intent == "accumulation":
        item = (parsed.get("accumulation_item") or "").strip()
        if item:
            result.accumulated_items = [item]
        result.accumulation_done = bool(parsed.get("accumulation_done"))
    elif intent == "schedule":
        result.tier = "easy"
        # action and action_params already extracted from parsed JSON
    elif intent == "email":
        result.tier = "easy"
        # action and action_params already extracted from parsed JSON
    elif intent == "digest":
        result.tier = "easy"
        result.action = "morning_digest"
    elif intent == "calendar":
        result.tier = "easy"
        result.action = "check_calendar"
    elif intent == "content":
        result.tier = "easy"
        # action and action_params already extracted from parsed JSON
    elif intent in ("cancel", "greeting", "clarification"):
        result.tier = "easy"

    # ── Tier validation ──────────────────────────────────────────

    if tier == "easy" and not answer and intent not in (
        "suggestion_pick", "approval", "question_reply",
        "cancel", "accumulation", "clarification",
        "schedule", "email", "digest", "calendar", "content",
    ):
        result.tier = "complex"

    if tier == "complex" and not complexity:
        result.complexity = "goal"

    return result
