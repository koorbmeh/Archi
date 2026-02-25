"""Intent classification for user messages.

Zero-cost fast-paths for common patterns (datetime, slash commands, greetings,
screenshots, image generation, deferred requests). Everything else falls
through to chat_fallback for the message handler to resolve.

The Conversational Router (src/core/conversational_router.py) handles the
primary Discord path with a single model call. This module is used by
internal callers (test runner, message_handler legacy path).
"""

import logging
from datetime import datetime
from typing import Optional

from src.interfaces.response_builder import trace
from src.utils.fast_paths import (
    is_datetime_question as _is_datetime_question,
    is_screenshot_request as _is_screenshot_request,
    extract_image_prompt as _extract_image_prompt,
)

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

    # 4b. Image generation requests (skip LLM when intent is obvious)
    img_result = _extract_image_prompt(msg_lower, message)
    if img_result:
        img_prompt, img_count, img_model = img_result
        _model_label = f" [{img_model}]" if img_model else ""
        trace(f"fast-path: generate_image x{img_count}{_model_label} → {img_prompt[:60]}")
        params = {"prompt": img_prompt, "count": img_count}
        if img_model:
            params["model"] = img_model
        return IntentResult(
            action="generate_image",
            params=params,
            fast_path=True,
        )

    # Everything else: let the message handler resolve via chat fallback
    return IntentResult(action="chat_fallback", params={}, cost=0)



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

_FILE_INTENTS = ("create ", "write ", "make a file", "create file", ".txt", ".py", ".md", ".json")

_FAREWELL_PHRASES = ("going to sleep", "going to bed", "good night", "goodnight",
                     "gotta go", "gotta run", "heading out", "logging off",
                     "signing off", "see you", "catch you", "talk later",
                     "goodbye", "take care", "i'm out",
                     "i'm done for", "calling it a night", "calling it a day",
                     "talk to you later", "ttyl", "peace out",
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
    if _is_farewell_phrase(msg_lower):
        return True

    # Social phrases anywhere
    if any(sp in msg_lower for sp in _SOCIAL_PHRASES):
        return True

    # Exact match praise
    if msg_lower.strip("!., ") in _PRAISE:
        return True

    return False


def _is_farewell_phrase(msg_lower: str) -> bool:
    """Check if lowered message contains a farewell phrase.

    Uses substring matching for multi-word phrases, plus a word-boundary
    check for bare 'bye' (avoids false positives on 'bypass', 'bystander', etc.).
    """
    if any(fp in msg_lower for fp in _FAREWELL_PHRASES):
        return True
    # Word-boundary check for bare "bye" — not in _FAREWELL_PHRASES to avoid
    # substring matches on "bypass", "bystander", etc.
    stripped = msg_lower.strip("!., ")
    if stripped == "bye":
        return True
    # " bye" at word boundary (e.g. "ok bye", "alright bye!")
    if " bye" in msg_lower:
        idx = msg_lower.index(" bye") + 4
        if idx >= len(msg_lower) or not msg_lower[idx].isalpha():
            return True
    return False


def _is_farewell(message: str) -> bool:
    """Check if a message is a farewell/departure (subset of social).

    Used by the response builder to pick an appropriate goodbye vs greeting.
    """
    if not message:
        return False
    return _is_farewell_phrase(message.strip().lower())


# ---- Multi-step detection ----

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


def is_coding_request(msg: str) -> bool:
    """True if message is a coding / file modification request.

    Detects explicit code patterns (add a function, fix the bug, run tests, etc.)
    and file-extension + action-verb combos (e.g. "update router.py").
    """
    if not msg:
        return False
    _code_lower = msg.strip().lower()

    _has_code_pattern = any(p in _code_lower for p in _CODE_PATTERNS)
    _has_file_ext = any(ext in _code_lower for ext in _CODE_EXTENSIONS)
    _has_code_verb = any(v in _code_lower.split() for v in _CODE_VERBS)

    return _has_code_pattern or (_has_file_ext and _has_code_verb)

