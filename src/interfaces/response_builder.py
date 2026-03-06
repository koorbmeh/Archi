"""Response building, sanitization, and conversation logging.

Takes raw action results and wraps them with conversational context,
identity sanitization, thinking block stripping, and logging.

Phase 4 note: The Router generates complete answers for easy-tier messages,
so action_prefix is unused on that path. The prefix logic is retained for
the complex-tier dispatch paths in message_handler.py: multi_step (line 256),
coding (line 274), and non-chat actions (line 329). The autonomous_executor
also calls process_message, flowing through these same paths. Verified
session 58 — no callers outside message_handler.py use build_response().
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from src.utils.text_cleaning import strip_thinking, sanitize_identity

logger = logging.getLogger(__name__)

# ---- Trace / conversation logging ----

_root = Path(__file__).resolve().parent.parent.parent
_trace_file = _root / "logs" / "chat_trace.log"
_convo_file = _root / "logs" / "conversations.jsonl"


def trace(msg: str) -> None:
    """Append a debug line to logs/chat_trace.log."""
    try:
        ts = datetime.now().isoformat()
        with open(_trace_file, "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception as e:
        logger.debug("Trace write failed: %s", e)


_TEST_SOURCES = frozenset(("test", "test_runner", "test_harness"))


def log_conversation(source: str, user_msg: str, response: str,
                     action: str, cost: float) -> None:
    """Append a user↔Archi exchange to logs/conversations.jsonl.

    Skips logging for test sources to avoid filling the log with
    unit-test / smoke-test noise.
    """
    if source in _TEST_SOURCES:
        return
    try:
        clean_resp = strip_thinking(response) if response else ""
        record = {
            "ts": datetime.now().isoformat(),
            "source": source,
            "user": (user_msg or "")[:2000],
            "response": (clean_resp or "")[:2000],
            "action": action,
            "cost_usd": cost,
            "pid": os.getpid(),
        }
        with open(_convo_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Conversation logging failed: %s", e)


# ---- Response assembly ----

def build_response(raw_text: str, action_prefix: str = "",
                   pending_finding: dict | None = None) -> str:
    """Clean and assemble a final response for the user.

    1. Strip thinking blocks
    2. Sanitize identity (Grok → Archi)
    3. Prepend model's conversational prefix (if action had one)
    4. Append interesting finding (if queued)
    """
    cleaned = strip_thinking(raw_text or "")
    cleaned = sanitize_identity(cleaned)

    # Prepend conversational prefix from intent model
    if action_prefix and cleaned:
        cleaned = f"{sanitize_identity(action_prefix)}\n\n{cleaned}"
    elif action_prefix:
        cleaned = sanitize_identity(action_prefix)

    # Append interesting finding
    if pending_finding and cleaned and len(cleaned) < 1500:
        cleaned += f"\n\nAlso — {pending_finding['summary']}"

    return cleaned or "I'm not sure how to respond."


# ---- Interesting findings helpers ----

def get_pending_finding() -> dict | None:
    """Get a queued finding ready for delivery (respects cooldown)."""
    try:
        from src.core.interesting_findings import get_findings_queue
        fq = get_findings_queue()
        ready = fq.get_ready_for_delivery()
        if ready:
            return ready[0]
    except Exception as e:
        logger.debug("get_pending_finding failed: %s", e)
    return None


def mark_finding_delivered(finding_id: str) -> None:
    """Mark a finding as delivered."""
    try:
        from src.core.interesting_findings import get_findings_queue
        get_findings_queue().mark_delivered(finding_id)
    except Exception as e:
        logger.debug("mark_finding_delivered failed: %s", e)


# ---- Preference extraction ----

def extract_preferences(message: str, source: str, router) -> None:
    """Non-blocking preference extraction from user messages."""
    try:
        from src.core.user_preferences import extract_and_record
        extract_and_record(message, source, router)
    except Exception as e:
        logger.debug("extract_preferences failed: %s", e)
