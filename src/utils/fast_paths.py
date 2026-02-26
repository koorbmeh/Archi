"""Shared fast-path patterns for intent classification and conversational routing.

Zero-cost pattern matching for datetime, screenshot, image generation, and
cost/meta-questions. Used by both intent_classifier.py and
conversational_router.py to avoid duplicating pattern definitions.
"""

import re
from typing import Optional, Tuple


# ── Datetime ─────────────────────────────────────────────────────────

DATETIME_PATTERNS = (
    "what day", "today's date", "current date", "what's the date",
    "what is the date", "what time", "current time", "day of the week",
    "what date", "what is today",
)


def is_datetime_question(msg_lower: str) -> bool:
    """Detect requests for current date/time."""
    return any(p in msg_lower for p in DATETIME_PATTERNS)


# ── Screenshot ───────────────────────────────────────────────────────

SCREENSHOT_PATTERNS = (
    "take a screenshot", "take screenshot",
    "capture the screen", "capture screen", "screen capture",
    "take a picture of the screen", "take a picture of my screen",
    "what's on screen", "what's on my screen",
    "what is on screen", "what is on my screen",
    "show me the screen", "show me my screen",
    "grab the screen", "screen grab", "screengrab",
    "print screen", "printscreen",
)

# Phrases that indicate the user is asking ABOUT screenshots, not requesting one.
_SCREENSHOT_EXCLUSIONS = (
    "about screenshot", "about screenshots",
    "how do screenshot", "how do screenshots",
    "what is a screenshot", "what are screenshot",
    "what is screenshot", "what's a screenshot",
)


def is_screenshot_request(msg_lower: str) -> bool:
    """Detect requests for a screenshot. Zero-cost fast-path.

    Multi-word patterns match via substring. Bare 'screenshot' only matches
    when the message is a short imperative (not a question about screenshots).
    """
    if any(p in msg_lower for p in SCREENSHOT_PATTERNS):
        return True
    # Bare "screenshot" — match only if the whole message is essentially just
    # "screenshot" (possibly with punctuation) and not a question about the concept
    if "screenshot" in msg_lower:
        if any(ex in msg_lower for ex in _SCREENSHOT_EXCLUSIONS):
            return False
        stripped = msg_lower.strip("!?., ")
        # Exact "screenshot" or very short imperative like "screenshot please"
        if stripped == "screenshot" or stripped in ("screenshot please", "screenshot now"):
            return True
    return False


# ── Image generation ─────────────────────────────────────────────────

IMAGE_GEN_STARTERS = (
    "generate an image of ", "generate image of ", "generate a picture of ",
    "generate me an image of ", "generate me a picture of ",
    "generate a picture for me of ", "generate an image for me of ",
    "create an image of ", "create a picture of ",
    "create a picture for me of ", "create an image for me of ",
    "draw ", "draw me ", "paint ", "paint me ",
    "make an image of ", "make a picture of ", "make me an image of ",
    "make me a picture of ",
    "generate an image: ", "generate image: ",
    "send me a picture of ", "send me an image of ",
    "send me a photo of ", "send a picture of ",
)

IMAGE_GEN_COUNT_RE = re.compile(
    r"^(?:generate|create|draw|paint|make|send)\s+(?:me\s+)?(\d+)\s+"
    r"(?:images?|pictures?|drawings?|paintings?|photos?)\s+(?:of\s+)?(.+)",
    re.IGNORECASE,
)

IMAGE_MODEL_SUFFIX_RE = re.compile(
    r"^(.+?)\s+(?:with|using|in)\s+([a-z0-9_]+)\s*$",
    re.IGNORECASE,
)

IMAGE_MODEL_PREFIX_RE = re.compile(
    r"^(?:use|using|with)\s+([a-z0-9_]+)\s*[,:]?\s*(?:to\s+)?(.+)",
    re.IGNORECASE,
)


def extract_image_prompt(
    msg_lower: str, original: str,
) -> Optional[Tuple[str, int, Optional[str]]]:
    """Extract image prompt if clearly an image generation request.

    Returns (prompt, count, model_alias_or_None) or None.
    Zero-cost fast-path — no model call.

    Handles model specification as prefix or suffix:
      - "using illustrious, send me 3 pictures of ..." (prefix)
      - "generate an image of a cat with illustrious"  (suffix)
    """
    prompt = None
    count = 1
    model = None

    working_msg = original.strip()
    working_lower = msg_lower.strip()

    # Strip leading "using <model>," prefix
    m_prefix = IMAGE_MODEL_PREFIX_RE.match(working_msg)
    if m_prefix:
        model = m_prefix.group(1).lower()
        working_msg = m_prefix.group(2).strip()
        working_lower = working_msg.lower()

    # Try count pattern: "generate 3 images of X"
    m = IMAGE_GEN_COUNT_RE.match(working_msg)
    if m:
        count = min(int(m.group(1)), 10)
        prompt = m.group(2).strip().rstrip("?!.")
        if not prompt or len(prompt) < 3 or count < 1:
            return None
    else:
        for starter in IMAGE_GEN_STARTERS:
            if working_lower.startswith(starter):
                prompt = working_msg[len(starter):].strip().rstrip("?!.")
                # Strip leading "me " left by starters like "draw me ", "paint me "
                if prompt.lower().startswith("me "):
                    prompt = prompt[3:].lstrip()
                if not prompt or len(prompt) < 3:
                    return None
                break

    if prompt is None:
        return None

    # Check for trailing "with <model>" suffix
    if model is None:
        m2 = IMAGE_MODEL_SUFFIX_RE.match(prompt)
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


# ── Cost / spending meta-questions ──────────────────────────────────

# Keyword combos that indicate a cost/spending inquiry about Archi itself.
# These are introspective meta-questions, not tasks — should be fast-pathed
# to avoid unnecessary model calls (and avoid misrouting when on a non-default model).

# Phrases that strongly indicate introspective cost queries (about Archi, not external)
_COST_INTROSPECTIVE = (
    "how much have you spent", "how much did you spend", "how much you spent",
    "what have you spent", "what did you spend",
    "how much has it cost", "how much did it cost",
    "show me the cost", "show me the spending", "show me spending",
    "tell me the cost", "tell me the spending",
    "what's the cost so far", "what is the cost so far",
    "what's the budget", "what is the budget",
    "check spending", "check budget", "check cost",
    "cost report", "spending report", "budget report",
    "budget usage", "cost usage",
    "your spending", "your cost", "your budget",
    "today's cost", "today's spending",
    "how expensive was that", "how expensive were those",
)

# Very short standalone keywords that are clearly about Archi's cost
_COST_SHORT = ("spending", "cost", "budget")


def is_cost_query(msg_lower: str) -> bool:
    """Detect natural-language questions about Archi's cost/spending.

    Uses phrase matching to distinguish introspective queries ("how much
    have you spent?") from external ones ("how much does a flight cost?").

    Matches things like:
      "how much have you spent today?"
      "what's the budget usage?"
      "show me the cost report"
      "how expensive was that?"
      "check spending"
    """
    # Match full introspective phrases
    if any(p in msg_lower for p in _COST_INTROSPECTIVE):
        return True
    # Very short messages that are just the keyword (1-2 words)
    stripped = msg_lower.strip("?!. ")
    if stripped in _COST_SHORT:
        return True
    return False
