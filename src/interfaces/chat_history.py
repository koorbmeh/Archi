"""
Persistent chat history for web chat. Survives restarts.
"""

import json
import logging
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)

_MAX_MESSAGES = 20  # Last 10 exchanges
_HISTORY_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "web_chat_history.json"


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
    """Append a message and save."""
    messages = load()
    messages.append({"role": role, "content": content})
    save(messages)


def format_for_prompt(messages: List[dict], max_exchanges: int = 5) -> str:
    """Format history for inclusion in a prompt."""
    if not messages:
        return ""
    recent = messages[-(max_exchanges * 2) :]
    lines = []
    for m in recent:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        prefix = "User:" if role == "user" else "Archi:"
        lines.append(f"{prefix} {content}")
    if not lines:
        return ""
    return "Previous conversation:\n" + "\n".join(lines) + "\n\n"


def get_recent() -> List[dict]:
    """Get recent messages for context."""
    return load()
