"""
Persistent chat history. Survives restarts.
Automatically strips <think> blocks from stored responses.
Thread-safe via module-level lock; atomic writes via temp-then-rename.
"""

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional

from src.utils.text_cleaning import strip_thinking

logger = logging.getLogger(__name__)

_MAX_MESSAGES = 20  # 10 user + 10 assistant
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_HISTORY_FILE = _DATA_DIR / "chat_history.json"
_OLD_HISTORY_FILE = _DATA_DIR / "web_chat_history.json"
_lock = threading.Lock()


def _ensure_file() -> Path:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Migrate from old name (web_chat_history.json → chat_history.json)
    if not _HISTORY_FILE.exists() and _OLD_HISTORY_FILE.exists():
        try:
            _OLD_HISTORY_FILE.rename(_HISTORY_FILE)
            logger.info("Migrated %s → %s", _OLD_HISTORY_FILE.name, _HISTORY_FILE.name)
        except OSError as e:
            logger.warning("Could not migrate chat history file: %s", e)
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
    """Save chat history to disk atomically (write temp → rename).

    Must be called with _lock held when used from append/pop_archivable.
    """
    try:
        target = _ensure_file()
        data = json.dumps(messages[-_MAX_MESSAGES:], ensure_ascii=False, indent=0)
        # Atomic write: temp file in same directory, then rename (same-filesystem)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent), suffix=".tmp", prefix="chat_hist_",
        )
        try:
            os.write(fd, data.encode("utf-8"))
            os.close(fd)
            fd = -1  # mark closed
            os.replace(tmp_path, str(target))
        except BaseException:
            if fd >= 0:
                os.close(fd)
            # Clean up temp file on error
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.warning("Could not save chat history: %s", e)


def append(role: str, content: str) -> None:
    """Append a message and save.  Strips <think> tags from assistant messages."""
    # Strip thinking blocks BEFORE storage — prevents history poisoning
    if role == "assistant":
        content = strip_thinking(content)
    # Don't store empty assistant messages (model spent all tokens thinking)
    if role == "assistant" and not content.strip():
        logger.debug("Skipping empty assistant message (was all <think> content)")
        return
    with _lock:
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
            content = strip_thinking(content)
        if not content:
            continue
        prefix = "User:" if role == "user" else "Archi:"
        lines.append(f"{prefix} {content}")
    if not lines:
        return ""
    return "Previous conversation:\n" + "\n".join(lines) + "\n\n"


def pop_archivable(keep: int = 8) -> List[dict]:
    """Remove and return oldest messages beyond `keep`, for archival to long-term memory.

    Returns the removed messages (oldest first), or empty list if nothing to archive.
    Saves the trimmed history back to disk.
    """
    with _lock:
        messages = load()
        if len(messages) <= keep:
            return []
        archivable = messages[:-keep]
        remaining = messages[-keep:]
        save(remaining)
        logger.info("Archived %d chat messages (kept %d)", len(archivable), len(remaining))
        return archivable


def get_recent() -> List[dict]:
    """Get recent messages for context.  Strips <think> from assistant content."""
    messages = load()
    cleaned = []
    for m in messages:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if role == "assistant":
            content = strip_thinking(content)
        if not content:
            continue
        cleaned.append({"role": role, "content": content})
    return cleaned
