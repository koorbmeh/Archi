"""Shared text cleaning utilities.

Consolidates strip_thinking, sanitize_identity, and extract_json which were
previously duplicated across multiple modules.
"""

import re
import logging

logger = logging.getLogger(__name__)

# ---- Thinking block removal ----

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think>.*", re.DOTALL)
_THINK_CLOSE_RE = re.compile(r"^.*?</think>\s*", re.DOTALL)


def strip_thinking(text: str) -> str:
    """Remove <think>...</think> reasoning blocks from model output.

    Handles complete blocks, unclosed tags, and </think> remnants.
    If the entire response was thinking (model ran out of tokens before
    producing an answer), extracts the last line from inside the block
    as a fallback — some models put the final answer there.
    Returns cleaned text, or empty string if only thinking content.
    """
    if not text or "<think>" not in text:
        return text or ""
    # Complete <think>...</think> blocks
    cleaned = _THINK_BLOCK_RE.sub("", text)
    # Unclosed <think> at end (model stopped mid-reasoning)
    cleaned = _THINK_OPEN_RE.sub("", cleaned)
    # Orphan </think> at start
    cleaned = _THINK_CLOSE_RE.sub("", cleaned)
    cleaned = cleaned.strip()
    if cleaned:
        return cleaned
    # Entire response was thinking; try last line of block as fallback
    match = re.search(r"<think>(.*?)(?:</think>|$)", text, flags=re.DOTALL)
    if match:
        inner = match.group(1).strip()
        if inner:
            last_line = inner.split("\n")[-1].strip()
            if last_line and len(last_line) < 200:
                return last_line
    return ""


# ---- Identity sanitization ----

# Patterns that should NOT be replaced (API-as-tool context)
_GROK_PRESERVE = ("use grok", "grok api", "from grok", "via grok", "switch to grok",
                   "ask grok", "try grok", "grok model", "grok 4", "grok-4", "grok for")

_IDENTITY_REPLACEMENTS = [
    (re.compile(r"\bi'm grok\b", re.IGNORECASE), "I'm Archi, an autonomous AI agent"),
    (re.compile(r"\bi am grok\b", re.IGNORECASE), "I am Archi"),
    (re.compile(r"\bvia the xai api\b", re.IGNORECASE), "via API"),
    (re.compile(r"\bbuilt by xai\b", re.IGNORECASE), "built for this project"),
    (re.compile(r"\bxai api\b", re.IGNORECASE), "API"),
    (re.compile(r"\bxai\b", re.IGNORECASE), "this project"),
]


def sanitize_identity(text: str) -> str:
    """Replace model self-identity (Grok, XAI) with Archi.

    Preserves API-as-tool references like 'use grok', 'grok api'.
    """
    if not text:
        return ""
    lower = text.lower()
    # Only do replacements if the raw text contains grok/xai
    if "grok" not in lower and "xai" not in lower:
        return text
    # Check if it's an API-context reference we should preserve
    for preserve in _GROK_PRESERVE:
        if preserve in lower:
            # Only replace the "I'm grok" self-identity, leave tool references
            text = re.sub(r"\bi'm grok\b", "I'm Archi", text, flags=re.IGNORECASE)
            text = re.sub(r"\bi am grok\b", "I am Archi", text, flags=re.IGNORECASE)
            return text
    # Full replacement: no preserve context
    for pattern, replacement in _IDENTITY_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    # Generic grok → Archi (after specific patterns)
    text = re.sub(r"\bgrok\b", "Archi", text, flags=re.IGNORECASE)
    return text

