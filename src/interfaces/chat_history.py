"""
Persistent chat history for web chat. Survives restarts.
Automatically strips <think> blocks from stored responses.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_MAX_MESSAGES = 20  # Last 10 exchanges
_HISTORY_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "web_chat_history.json"


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> reasoning blocks from stored text.

    Prevents reasoning model internals from polluting chat history context.
    """
    if not text or "<think>" not in text:
        return text or ""
    # Remove complete <think>...</think> blocks
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Handle unclosed <think> tag
    if "<think>" in cleaned:
        cleaned = cleaned.split("<think>")[0].strip()
    cleaned = cleaned.replace("</think>", "").strip()
    return cleaned


def _ensure_file() -> Path:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _HISTORY_FILE.exists():
        _HISTORY_FILE.write_text("[]", encoding="utf-8")
    return _HISTORY_FILE


def load() -> List[dict]:
    """Load chat history from disk."""
    try:
        raw = _ensure_file().read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("Could not load chat history: %s", e)
        return []


def save(messages: List[dict]) -> None:
    """Save chat history to disk."""
    try:
        _ensure_file().write_text(
            json.dumps(messages[-_MAX_MESSAGES:], ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Could not save chat history: %s", e)


def append(role: str, content: str) -> None:
    """Append a message and save.  Strips <think> tags from assistant messages."""
    # Strip thinking blocks BEFORE storage — prevents history poisoning
    if role == "assistant":
        content = _strip_thinking(content)
    # Don't store empty assistant messages (model spent all tokens thinking)
    if role == "assistant" and not content.strip():
        logger.debug("Skipping empty assistant message (was all <think> content)")
        return
    messages = load()
    messages.append({"role": role, "content": content, "ts": time.time()})
    save(messages)


def seconds_since_last_message() -> Optional[float]:
    """Return seconds since the most recent stored message, or None if no history."""
    messages = load()
    if not messages:
        return None
    # Walk backwards to find a message with a timestamp
    for m in reversed(messages):
        ts = m.get("ts")
        if ts is not None:
            return time.time() - ts
    return None


def format_for_prompt(messages: List[dict], max_exchanges: int = 5) -> str:
    """Format history for inclusion in a prompt.

    Strips any residual <think> blocks from assistant messages (belt-and-suspenders
    in case old data was stored before the append() fix).
    """
    if not messages:
        return ""
    recent = messages[-(max_exchanges * 2) :]
    lines = []
    for m in recent:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        # Strip <think> blocks from assistant messages when reading
        if role == "assistant":
            content = _strip_thinking(content)
        if not content:
            continue
        prefix = "User:" if role == "user" else "Archi:"
        lines.append(f"{prefix} {content}")
    if not lines:
        return ""
    return "Previous conversation:\n" + "\n".join(lines) + "\n\n"


def get_recent() -> List[dict]:
    """Get recent messages for context.  Strips <think> from assistant content."""
    messages = load()
    cleaned = []
    for m in messages:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if role == "assistant":
            content = _strip_thinking(content)
        if not content:
            continue
        cleaned.append({"role": role, "content": content})
    return cleaned
