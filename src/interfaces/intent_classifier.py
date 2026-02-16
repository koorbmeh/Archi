"""Intent classification for user messages.

v2 architecture: Let the model decide first. Only 3 zero-cost fast-paths
remain (datetime, slash commands, greeting). Everything else goes to the
model with proper multi-turn context.
"""

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.utils.text_cleaning import extract_json, sanitize_identity, strip_thinking
from src.interfaces.response_builder import trace

logger = logging.getLogger(__name__)

# ---- Result type ----

class IntentResult:
    """Result of intent classification."""
    __slots__ = ("action", "params", "prefix", "cost", "fast_path")

    def __init__(self, action: str, params: dict, prefix: str = "",
                 cost: float = 0.0, fast_path: bool = False):
        self.action = action
        self.params = params
        self.prefix = prefix       # Conversational prefix from model
        self.cost = cost
        self.fast_path = fast_path  # True if no model call was needed


# ---- Public API ----

def classify(message: str, effective_message: str, router, history_messages: list,
             system_prompt: str, goal_manager=None) -> IntentResult:
    """Classify user intent and return the action to take.

    Checks zero-cost fast-paths first, then asks the model.

    Args:
        message: Raw user message
        effective_message: Resolved message (may differ if follow-up correction)
        router: ModelRouter instance
        history_messages: Proper multi-turn messages list
        system_prompt: System prompt for model context
        goal_manager: Optional GoalManager for slash commands
    """

    msg_lower = (message or "").strip().lower()

    # ---- Zero-cost fast-paths ($0.00, no model call) ----

    # 1. Datetime questions
    if _is_datetime_question(msg_lower):
        trace("fast-path: datetime")
        return IntentResult(
            action="datetime",
            params={"response": _get_datetime_response()},
            fast_path=True,
        )

    # 2. Slash commands
    if msg_lower.startswith("/"):
        result = _handle_slash_command(msg_lower, message, goal_manager)
        if result:
            return result

    # 3. Simple greetings (no substantive content)
    if _is_greeting_or_social(message):
        trace(f"fast-path: greeting")
        return IntentResult(
            action="greeting",
            params={},
            fast_path=True,
        )

    # 4. Screenshot requests (no model call needed — just take the screenshot)
    if _is_screenshot_request(msg_lower):
        trace("fast-path: screenshot")
        return IntentResult(
            action="screenshot",
            params={},
            fast_path=True,
        )

    # 5. Deferred requests ("when you have time, look into X")
    deferred_desc = _is_deferred_request(message)
    if deferred_desc:
        trace(f"fast-path: deferred request → goal: {deferred_desc[:60]}")
        return IntentResult(
            action="deferred_request",
            params={"description": deferred_desc},
            fast_path=True,
        )

    # ---- Model intent classification ----
    # Everything else: ask the model with full multi-turn context

    return _model_classify(effective_message, router, history_messages, system_prompt)


# ---- Fast-path helpers ----

def _is_datetime_question(msg_lower: str) -> bool:
    """Detect requests for current date/time."""
    _DATETIME_PATTERNS = (
        "what day", "today's date", "current date", "what's the date",
        "what is the date", "what time", "current time", "day of the week",
        "what date", "what is today",
    )
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
    """Detect requests for a screenshot. Zero-cost fast-path — no model call."""
    return any(p in msg_lower for p in _SCREENSHOT_PATTERNS)


# ---- Deferred request detection ----

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
    """Detect deferred/reminder-style requests and extract the task description.

    Matches patterns like:
    - "When you have time, look into protein powder brands"
    - "Remind me to check the server logs"
    - "Research lithium orotate dosing when you get a chance"
    - "Can you look into X later?"

    Returns the extracted task description, or None if not a deferred request.
    Zero-cost fast-path — no model call.
    """
    if not message or len(message) < 15 or len(message) > 500:
        return None

    msg = message.strip()
    msg_lower = msg.lower()

    # Pattern 1: Reminder starters ("remind me to X", "don't forget to X")
    for starter in _REMINDER_STARTS:
        if starter in msg_lower:
            idx = msg_lower.index(starter) + len(starter)
            desc = msg[idx:].strip().rstrip("?!.")
            if len(desc) >= 8:
                return desc
            return None

    # Pattern 2: Deferred signal + action verb
    # e.g. "when you have time, look into X" or "look into X when you get a chance"
    has_deferred = any(s in msg_lower for s in _DEFERRED_SIGNALS)
    has_later = any(s in msg_lower for s in _LATER_SIGNALS)

    if has_deferred or has_later:
        # Find the EARLIEST action verb in the message (avoid matching nouns)
        best_verb = None
        best_idx = len(msg_lower)
        for verb in _DEFERRED_VERBS:
            # Match verb at word boundary (start of word)
            pos = 0
            while pos < len(msg_lower):
                idx = msg_lower.find(verb, pos)
                if idx == -1:
                    break
                # Ensure it's at a word boundary (start of string or preceded by space/punctuation)
                if idx == 0 or not msg_lower[idx - 1].isalpha():
                    if idx < best_idx:
                        best_idx = idx
                        best_verb = verb
                    break
                pos = idx + 1

        if best_verb is not None:
            desc = msg[best_idx:].strip()
            # Strip trailing deferred signals and punctuation
            for signal in _DEFERRED_SIGNALS + _LATER_SIGNALS:
                desc_lower = desc.lower()
                if desc_lower.endswith(signal):
                    desc = desc[: -len(signal)].strip()
            desc = desc.rstrip("?!.,")
            if len(desc) >= 10:
                return desc
            return None

        # No specific verb but has deferred signal — extract content after signal
        for signal in _DEFERRED_SIGNALS:
            if signal in msg_lower:
                idx = msg_lower.index(signal) + len(signal)
                desc = msg[idx:].strip().lstrip(",").strip()
                if len(desc) >= 10:
                    return desc

    return None


def _get_datetime_response() -> str:
    """Return formatted current date/time."""
    return datetime.now().strftime("It's %A, %B %d, %Y at %I:%M %p.")


def _handle_slash_command(msg_lower: str, message: str,
                          goal_manager) -> Optional[IntentResult]:
    """Handle /commands. Returns IntentResult or None if unrecognized."""

    if msg_lower.startswith("/goal ") and goal_manager:
        desc = message[6:].strip()
        return IntentResult(
            action="create_goal", params={"description": desc}, fast_path=True)

    if msg_lower == "/goals":
        return IntentResult(action="goals_status", params={}, fast_path=True)

    if msg_lower == "/status":
        return IntentResult(action="system_status", params={}, fast_path=True)

    if msg_lower == "/cost":
        return IntentResult(action="cost_report", params={}, fast_path=True)

    if msg_lower in ("/help", "/h"):
        return IntentResult(action="help", params={}, fast_path=True)

    if msg_lower.startswith("/test"):
        mode = "full" if "full" in msg_lower else "quick"
        return IntentResult(action="run_tests", params={"mode": mode}, fast_path=True)

    if msg_lower.startswith("/"):
        return IntentResult(
            action="unknown_command",
            params={"response": f"Unknown command: `{message.split()[0]}`. Type /help for available commands."},
            fast_path=True)

    return None


# ---- Greeting detection ----

_ACTION_KEYWORDS = (
    "goal", "make it", "can you", "could you", "would you", "please ",
    "do a ", "do the ", "run ", "search ", "find ", "look ",
    "read ", "check ", "tell me", "show me", "give me", "send me",
    "work on", "start ", "stop ", "fix ", "update ", "change ",
    "add ", "remove ", "delete ", "modify ", "edit ", "open ",
    "research ", "analyze ", "compare ", "list ", "organize ",
    "remind", "remember", "schedule", "what is", "what's", "what are",
    "how do", "how to", "why ", "when ", "where ",
    "make", "write", "create", "close", "set",
)

_SOCIAL_EXCEPTIONS = ("what's up", "what's new", "what's good", "what's happening",
                       "what's going on", "what are you up to",
                       "how are you", "how's it going", "how you doing")

_SOCIAL_STARTS = ("hello", "hi ", "hi,", "hey ", "hey,", "good morning", "good afternoon",
                   "good evening", "howdy", "greetings", "hiya", "yo ", "yo,", "sup")

_SOCIAL_PHRASES = ("what's up", "how are you", "how are things", "how's it going",
                    "checking to make sure", "checking on you", "still functioning",
                    "still working", "are you there", "you there", "still there",
                    "health check", "do a health check", "check on yourself",
                    "surprise me",
                    "i'm coming from", "just got back", "arrived", "heading to",
                    "on my way", "coming from", "visiting", "back from",
                    "thanks for", "thank you for", "that's all", "nothing else",
                    "never mind", "nvm")

_FAREWELL_PHRASES = ("going to sleep", "going to bed", "good night", "goodnight",
                     "gotta go", "gotta run", "heading out", "logging off",
                     "signing off", "see you", "catch you", "talk later",
                     "bye", "goodbye", "take care", "i'm out",
                     "i'm done for", "calling it a night", "calling it a day",
                     "talk to you later", "ttyl", "peace out", "later",
                     "off to bed", "hitting the hay", "turning in")

_PRAISE = ("good job", "nice work", "nice job", "thanks", "thank you", "correct",
            "perfect", "excellent", "great work", "great job", "well done",
            "awesome", "amazing", "brilliant", "fantastic", "nailed it", "spot on",
            "nice one", "good stuff", "right on", "good work",
            "that's right", "thats right", "exactly",
            "you're right", "you are right")


def _is_greeting_or_social(message: str) -> bool:
    """Detect messages that are ONLY social/greeting (no substantive request).

    Returns False if message has action keywords, file intent, or is >200 chars.
    """
    if not message or len(message) > 200:
        return False

    msg = message.strip()
    msg_lower = msg.lower()

    # Exclusions: slash commands, file intent, or action verbs
    if msg_lower.startswith("/"):
        return False
    _FILE_INTENTS = ("create ", "write ", "make a file", "create file", ".txt", ".py", ".md", ".json")
    if any(fi in msg_lower for fi in _FILE_INTENTS):
        return False

    # Social exceptions fire before action keyword check
    if any(se in msg_lower for se in _SOCIAL_EXCEPTIONS):
        return True

    # Has action keywords → not just social
    if any(kw in msg_lower for kw in _ACTION_KEYWORDS):
        return False

    # Starts with greeting prefix (or exact match after stripping trailing space)
    for start in _SOCIAL_STARTS:
        if msg_lower.startswith(start) or msg_lower == start.rstrip():
            remainder = msg_lower[len(start):].lstrip(" ,.:!-").strip()
            # Strip name references ("archi", "buddy", etc.)
            for name in ("archi", "buddy", "friend", "mate", "pal", "dude", "bro"):
                if remainder.startswith(name):
                    remainder = remainder[len(name):].lstrip(" ,.:!-").strip()
            # If there's still substantial content left, this is NOT just a greeting
            if len(remainder) < 16:
                return True

    # Farewell phrases (subset of social — handled separately in response builder)
    if any(fp in msg_lower for fp in _FAREWELL_PHRASES):
        return True

    # Social phrases anywhere
    if any(sp in msg_lower for sp in _SOCIAL_PHRASES):
        return True

    # Exact match praise
    if msg_lower.strip("!., ") in _PRAISE:
        return True

    return False


def _is_farewell(message: str) -> bool:
    """Check if a message is a farewell/departure (subset of social).

    Used by the response builder to pick an appropriate goodbye vs greeting.
    """
    if not message:
        return False
    msg_lower = message.strip().lower()
    return any(fp in msg_lower for fp in _FAREWELL_PHRASES)


# ---- Model intent classification ----

_INTENT_INSTRUCTION = """Respond with ONLY a JSON object. Pick the ONE best action:
- {"action":"chat","response":"your reply"} — for questions, greetings, conversation
- {"action":"create_file","path":"workspace/file.txt","content":"text"} — ONLY when user explicitly says "create/write a file"
- {"action":"search","query":"search terms"} — for live data (prices, weather, news)
- {"action":"screenshot"} — to take a screenshot of the current screen and send it
- {"action":"click","target":"what to click"} — to click UI elements
- {"action":"browser_navigate","url":"https://..."} — to open a URL
- {"action":"generate_image","prompt":"description"} — to generate/draw an image
- {"action":"create_goal","description":"what to do"} — ONLY when user says "create a goal" or "/goal"
- {"action":"fetch_webpage","url":"https://..."} — to fetch/read a webpage's content
- {"action":"list_files","path":"src/"} — to list files/folders in a directory
- {"action":"read_file","path":"src/main.py"} — to read a file's contents
- {"action":"multi_step","description":"what to research/build"} — for tasks needing research, analysis, or multi-file work

For any non-chat action, you may include a "response" field with a short conversational message to show alongside the result.

RULES: Use conversation history for context but respond to the user's latest message. Never claim you did something without executing it. Greetings = chat. JSON only."""


def _model_classify(effective_message: str, router, history_messages: list,
                    system_prompt: str) -> IntentResult:
    """Ask the model to classify intent with full multi-turn context."""

    # Build proper multi-turn messages
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history_messages)
    messages.append({"role": "user", "content": f"{effective_message}\n\n{_INTENT_INSTRUCTION}"})

    trace("intent_classifier: model classify")
    trace(f"intent classification for: {(effective_message or '')[:200]}")

    resp = router.generate(max_tokens=400, temperature=0.2, messages=messages)
    cost = resp.get("cost_usd", 0)
    text = resp.get("text", "")

    trace(f"intent model={resp.get('model')} text_len={len(text)}")

    if not resp.get("success", True):
        return IntentResult(
            action="chat",
            params={"response": f"Sorry, I couldn't process that: {resp.get('error', 'Unknown error')}"},
            cost=cost,
        )

    parsed = extract_json(text)
    if not parsed:
        # Single retry with simplified prompt
        logger.info("Intent parse failed, retrying with simplified prompt")
        retry_resp = router.generate(
            prompt=f"User said: {effective_message}\n\nRespond with ONLY valid JSON. "
                   f"Pick ONE:\n- {{\"action\":\"chat\",\"response\":\"your reply\"}}\n"
                   f"- {{\"action\":\"list_files\",\"path\":\"directory/\"}}\n"
                   f"- {{\"action\":\"search\",\"query\":\"search terms\"}}\n"
                   f"JSON only:",
            max_tokens=200, temperature=0.1,
        )
        cost += retry_resp.get("cost_usd", 0)
        parsed = extract_json(retry_resp.get("text", ""))

    if not parsed:
        # Last resort: let model respond conversationally
        return IntentResult(action="chat_fallback", params={}, cost=cost)

    action = parsed.get("action", "chat")
    prefix = ""
    if action != "chat":
        prefix = sanitize_identity((parsed.get("response") or "").strip())

    return IntentResult(action=action, params=parsed, prefix=prefix, cost=cost)


# ---- Multi-step detection ----

def needs_multi_step(msg: str) -> bool:
    """True if a user message likely requires multiple actions to fulfill properly.

    These requests should be routed to PlanExecutor instead of the single-shot
    intent model, so Archi can chain research → file creation → verification.
    """
    if not msg:
        return False
    m = msg.strip().lower()
    if len(m) < 15:
        return False

    _RESEARCH_PATTERNS = (
        "research ", "investigate ", "look into ", "find out about ",
        "find out what ", "find out how ", "find out why ",
        "dig into ", "deep dive ", "explore ",
        "study ", "analyze ", "analyse ",
        "compare ", "evaluate ", "review ",
        "write a report", "write a summary", "write me a report",
        "write a document", "write up ", "write an analysis",
        "put together a report", "put together a summary",
        "compile ", "gather information",
    )
    _WORKSPACE_PATTERNS = (
        "create files", "create the files", "make files",
        "create 2 ", "create 3 ", "create 4 ", "create 5 ",
        "create two ", "create three ", "create four ", "create five ",
        "organize ", "reorganize ", "clean up ",
        "set up ", "build a ", "build the ",
        "summarize the files", "summarize my files",
        "summarize the reports", "read all ",
        "go through ", "process all ", "process the ",
    )
    _MULTI_TASK_SIGNALS = (
        " and then ", " then ", " after that ",
        " and also ", " and create ", " and write ",
        " and save ", " and send ", " and summarize ",
    )
    _WORK_VERBS = (
        "figure out ", "work on ", "handle ",
        "take care of ", "get me ", "fetch ",
        "download ", "scrape ", "crawl ",
        "check on ", "monitor ", "track ",
    )

    if any(p in m for p in _RESEARCH_PATTERNS):
        return True
    if any(p in m for p in _WORKSPACE_PATTERNS):
        return True
    if any(p in m for p in _MULTI_TASK_SIGNALS):
        return True
    if any(p in m for p in _WORK_VERBS):
        return True
    if m.startswith("search for ") and (" and " in m or " then " in m):
        return True
    return False


def is_coding_request(msg: str) -> bool:
    """True if message is a coding / file modification request.

    Detects explicit code patterns (add a function, fix the bug, run tests, etc.)
    and file-extension + action-verb combos (e.g. "update router.py").
    """
    if not msg:
        return False
    _code_lower = msg.strip().lower()

    _CODE_PATTERNS = (
        "add a function", "add a method", "add a class",
        "add function", "add method", "add class",
        "modify ", "change the code", "update the code",
        "fix the code", "fix the bug", "fix this bug",
        "edit the file", "edit this file", "edit file",
        "refactor ", "rewrite ",
        "create a script", "write a script", "create a module",
        "write a function", "write a class", "write code",
        "implement ", "add a feature",
        "run the tests", "run tests", "run pytest",
        "run the command", "run command",
        "pip install", "npm install", "install the package",
        "install the module", "install the library", "install the dependency",
        "add to src/", "modify src/", "update src/",
        "change src/", "fix src/", "edit src/",
        "add to config/", "modify config/",
    )
    _CODE_VERBS = ("add", "modify", "change", "update", "fix", "edit",
                    "create", "write", "implement", "refactor", "remove",
                    "delete", "rename", "install")
    _CODE_EXTENSIONS = (".py", ".js", ".ts", ".yaml", ".yml", ".json",
                        ".toml", ".cfg", ".ini", ".html", ".css")

    _has_code_pattern = any(p in _code_lower for p in _CODE_PATTERNS)
    _has_file_ext = any(ext in _code_lower for ext in _CODE_EXTENSIONS)
    _has_code_verb = any(v in _code_lower.split() for v in _CODE_VERBS)

    return _has_code_pattern or (_has_file_ext and _has_code_verb)

