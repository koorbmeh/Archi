"""
Discord Bot Interface - Chat with Archi from Discord.

Listens to DMs and @mentions, sends messages to Archi via message_handler.
Supports text messages and image attachments (analyzed via vision model).

Outbound messaging: other components (dream cycle, agent loop) can call
send_notification(text) to proactively message the owner via DM.
"""

import asyncio
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_router: Optional[Any] = None
_goal_manager: Optional[Any] = None
_dream_cycle: Optional[Any] = None
_upload_dir: Optional[Path] = None

# Outbound messaging state (set when bot connects)
_bot_client: Optional[Any] = None
_bot_loop: Optional[asyncio.AbstractEventLoop] = None
_owner_dm_channel: Optional[Any] = None
_owner_id: Optional[int] = None  # Discord user ID of the owner

# Source modification approval state (protected by _approval_lock)
_approval_lock = threading.Lock()
_pending_approval: Optional[threading.Event] = None
_approval_result: bool = False

# Free-form question state (protected by _question_lock)
# Used by ask_user() to block a worker thread until the user replies.
_question_lock = threading.Lock()
_pending_question: Optional[threading.Event] = None
_question_response: Optional[str] = None

# Recent question history for cross-goal dedup.  Prevents re-asking
# a question that was already sent (even by a different goal) within
# the cooldown window.  List of (timestamp, question_text, got_answer).
_recent_questions: list[tuple[float, str, bool]] = []
_QUESTION_DEDUP_COOLDOWN = 600  # 10 min — don't re-ask similar questions
_QUESTION_SIMILARITY_THRESHOLD = 0.5  # Jaccard word overlap

# Deferred approval tracking: stores paths that timed out so the user
# can retroactively approve them (e.g. "approve src/tools/foo.py").
# When a user later approves a timed-out path, the approval is logged
# so the dream cycle can retry the task in a future cycle.
_deferred_approvals: Dict[str, Dict[str, Any]] = {}  # path -> {"action", "task", "ts"}

# Last message per user — enables "try again" / "retry" to re-process
# the previous message with a different model.
_last_user_message: Dict[int, str] = {}  # user_id -> last message text

# ── Feedback tracking (Phase 3) ──────────────────────────────────
# Maps Discord message IDs → structured context for reaction-based feedback.
# When Jesse reacts 👍/👎, we look up the context here and record it.
_tracked_messages: Dict[int, Dict[str, Any]] = {}  # msg_id -> {goal, task, ...}
_MAX_TRACKED = 100  # Prune oldest entries beyond this


def track_notification_message(
    message_id: int,
    context: Dict[str, Any],
) -> None:
    """Register a sent notification for reaction-based feedback tracking.

    Args:
        message_id: The Discord message ID of the sent notification.
        context: Metadata about what the notification was about, e.g.
            {"goal": "...", "event": "goal_completion", ...}
    """
    _tracked_messages[message_id] = context
    # Prune oldest if we exceed the cap
    if len(_tracked_messages) > _MAX_TRACKED:
        oldest_keys = sorted(_tracked_messages.keys())[:len(_tracked_messages) - _MAX_TRACKED]
        for k in oldest_keys:
            _tracked_messages.pop(k, None)


def _record_reaction_feedback(message_id: int, emoji: str) -> None:
    """Record a reaction on a tracked notification as learning feedback.

    Called from on_raw_reaction_add when Jesse reacts to a tracked message.
    Uses the dream cycle's shared LearningSystem instance if available,
    otherwise creates a standalone one (which shares the same data file).
    """
    context = _tracked_messages.get(message_id)
    if not context:
        return

    sentiment = "positive" if emoji in ("👍", "❤️", "🎉", "🔥") else "negative"
    goal_desc = context.get("goal", "unknown goal")
    event = context.get("event", "notification")

    try:
        # Prefer the dream cycle's shared instance (avoids stale data)
        ls = None
        if _dream_cycle is not None and hasattr(_dream_cycle, "learning_system"):
            ls = _dream_cycle.learning_system
        if ls is None:
            from src.core.learning_system import LearningSystem
            ls = LearningSystem()

        ls.record_feedback(
            context=f"{event}: {goal_desc[:150]}",
            action=context.get("summary", event),
            feedback=f"User reacted {emoji} ({sentiment})",
        )
        logger.info("Recorded %s feedback on message %d: %s", sentiment, message_id, emoji)
    except Exception as e:
        logger.debug("Could not record reaction feedback: %s", e)


def _get_upload_dir() -> Path:
    """Return (and create) the upload directory for Discord images."""
    global _upload_dir
    if _upload_dir is None:
        _upload_dir = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
        _upload_dir.mkdir(parents=True, exist_ok=True)
    return _upload_dir


def init_discord_bot(
    goal_manager: Optional[Any] = None,
    router: Optional[Any] = None,
    dream_cycle: Optional[Any] = None,
) -> None:
    """Set goal manager, optional shared router, and dream cycle for Discord."""
    global _goal_manager, _router, _dream_cycle
    _goal_manager = goal_manager
    if router is not None:
        _router = router
    if dream_cycle is not None:
        _dream_cycle = dream_cycle


def _get_router():
    """Return shared ModelRouter (set via init_discord_bot) or lazy-load on first use."""
    global _router
    if _router is None:
        try:
            from src.models.router import ModelRouter
            _router = ModelRouter()
            logger.info("Model router initialized for Discord bot (lazy)")
        except Exception as e:
            logger.warning("Model router not available: %s", e)
    return _router


def _persist_owner_id(owner_id: int) -> None:
    """Write DISCORD_OWNER_ID to .env so it survives restarts."""
    try:
        env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        if not env_file.exists():
            logger.debug("No .env file found; skipping owner ID persistence")
            return

        lines = env_file.read_text(encoding="utf-8").splitlines(keepends=True)
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith("DISCORD_OWNER_ID="):
                lines[i] = f"DISCORD_OWNER_ID={owner_id}\n"
                found = True
                break

        if not found:
            # Append after DISCORD_BOT_TOKEN if present, else at end
            insert_idx = len(lines)
            for i, line in enumerate(lines):
                if line.strip().startswith("DISCORD_BOT_TOKEN="):
                    insert_idx = i + 1
                    break
            lines.insert(insert_idx, f"DISCORD_OWNER_ID={owner_id}\n")

        env_file.write_text("".join(lines), encoding="utf-8")
        # Also set in current process so it's available immediately
        os.environ["DISCORD_OWNER_ID"] = str(owner_id)
        logger.info("Persisted DISCORD_OWNER_ID=%d to .env", owner_id)
    except Exception as e:
        logger.warning("Could not persist owner ID to .env: %s", e)


def _log_outbound(text: str) -> None:
    """Log an outbound notification to conversations.jsonl (same format as inbound)."""
    try:
        import json
        from datetime import datetime
        log_file = Path(__file__).resolve().parent.parent.parent / "logs" / "conversations.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(),
            "source": "dream_cycle_outbound",
            "user": "",
            "response": (text or "")[:500],
            "action": "notification",
            "cost_usd": 0,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _truncate(text: str, max_len: int = 1900) -> str:
    """Truncate text for Discord (max 2000 chars per message)."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


# ──────────────────────────────────────────────────────────────────────
#  Outbound messaging — callable from ANY thread
# ──────────────────────────────────────────────────────────────────────

def send_notification(
    text: str,
    file_path: Optional[str] = None,
    track_context: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Send a proactive message to the owner via Discord DM.

    Can be called from any thread (dream cycle, agent loop, etc.).
    Returns True if the message was queued successfully.

    Args:
        text: Message text (truncated to ~1900 chars for Discord).
        file_path: Optional path to a file to attach to the message.
        track_context: Optional metadata dict to track this message for
            reaction-based feedback (Phase 3). If provided, the sent
            message's ID is registered so 👍/👎 reactions are recorded.

    Usage:
        from src.interfaces.discord_bot import send_notification
        send_notification("I finished working on your Health Optimization goal.")
        send_notification("Here's the report:", file_path="workspace/reports/roadmap.md")
        send_notification("Done with X.", track_context={"goal": "X", "event": "goal_completion"})
    """
    global _bot_client, _bot_loop, _owner_dm_channel

    if not _bot_client or not _bot_loop or not _owner_dm_channel:
        logger.debug("Discord outbound not ready (bot=%s, loop=%s, dm=%s)",
                      _bot_client is not None, _bot_loop is not None,
                      _owner_dm_channel is not None)
        return False

    truncated = _truncate(text) if text else ""

    # Build kwargs for channel.send()
    send_kwargs: Dict[str, Any] = {}
    if truncated:
        send_kwargs["content"] = truncated
    if file_path:
        try:
            import discord
            from src.core.plan_executor import _resolve_project_path
            resolved = _resolve_project_path(file_path)
            if os.path.isfile(resolved):
                send_kwargs["file"] = discord.File(resolved)
                logger.info("Attaching file: %s", resolved)
            else:
                logger.warning("File not found for attachment: %s", file_path)
        except Exception as e:
            logger.warning("Could not attach file %s: %s", file_path, e)

    if not send_kwargs:
        return False

    try:
        future = asyncio.run_coroutine_threadsafe(
            _owner_dm_channel.send(**send_kwargs),
            _bot_loop,
        )
        # Don't block indefinitely — 10 second timeout
        sent_msg = future.result(timeout=10)
        logger.info("Discord notification sent: %s", truncated[:80])

        # Track for reaction-based feedback if context provided
        if track_context and sent_msg:
            track_notification_message(sent_msg.id, track_context)

        # Log to conversations.jsonl so we have a debug trail
        _log_outbound(truncated)

        # Persist to chat history so Archi remembers what he told the owner
        try:
            from src.interfaces.chat_history import append
            append("assistant", truncated)
        except Exception:
            pass

        return True
    except Exception as e:
        logger.warning("Failed to send Discord notification: %s", e)
        return False


def is_outbound_ready() -> bool:
    """True if the bot is connected and has a DM channel for outbound messages."""
    return _bot_client is not None and _owner_dm_channel is not None


def kick_dream_cycle(goal_id: str, reactive: bool = True) -> None:
    """Submit a goal to the dream cycle's worker pool for immediate execution.

    Public API for other modules that need to kick off background work
    without importing _dream_cycle directly.
    """
    if _dream_cycle is not None:
        _dream_cycle.kick(goal_id=goal_id, reactive=reactive)


def close_bot() -> None:
    """Request graceful Discord bot shutdown.  Safe to call from any thread."""
    if _bot_client is not None and not _bot_client.is_closed():
        if _bot_loop and _bot_loop.is_running():
            import asyncio
            asyncio.run_coroutine_threadsafe(_bot_client.close(), _bot_loop)
        logger.info("Discord bot close requested")


# ──────────────────────────────────────────────────────────────────────
#  Source modification approval — callable from ANY thread
# ──────────────────────────────────────────────────────────────────────

def request_source_approval(
    action: str,
    path: str,
    task_description: str,
    timeout: float = 300,
) -> bool:
    """Request user approval for a source code modification via Discord DM.

    Blocks the calling thread until the user replies yes/no or the timeout
    expires.  Returns True only on explicit approval.

    This is the enforcement mechanism for approval_required_paths in rules.yaml.
    Unlike prompt injection (which asks the *model* to behave), this function
    blocks code execution at the Python level — the modification physically
    cannot proceed without a True return.

    Args:
        action: The action type ("write_source" or "edit_file").
        path: The file path being modified (e.g. "src/tools/foo.py").
        task_description: What the task is trying to accomplish.
        timeout: Seconds to wait for a response (default 5 min).

    Returns:
        True if the user approved, False otherwise (denied, timeout, or error).
    """
    global _pending_approval, _approval_result

    if not is_outbound_ready():
        logger.warning("Discord not ready — denying source modification: %s", path)
        return False

    # Set up the approval gate (threading.Event blocks until set)
    with _approval_lock:
        _pending_approval = threading.Event()
        _approval_result = False

    msg = (
        f"Can I {action} `{path}`? "
        f"({task_description[:120]})\n"
        f"Yes or no — auto-denies in {int(timeout)}s."
    )

    if not send_notification(msg):
        logger.warning("Failed to send approval request — denying: %s", path)
        with _approval_lock:
            _pending_approval = None
        return False

    # Block until user responds or timeout (Event.wait is thread-safe)
    responded = _pending_approval.wait(timeout=timeout)

    with _approval_lock:
        if not responded:
            logger.info("Approval timed out after %ds — denying: %s", int(timeout), path)
            _pending_approval = None
        result = _approval_result
        _pending_approval = None

    if not responded:
        # Record for deferred approval: the user might come back later and
        # say "approve src/tools/foo.py" — we'll log it so the dream cycle
        # knows the path is now pre-approved for the next attempt.
        import time as _time
        _deferred_approvals[path] = {
            "action": action,
            "task": task_description[:200],
            "ts": _time.time(),
        }
        # Fire-and-forget: don't block the calling thread waiting for Discord to
        # confirm delivery of this courtesy notification.  If Discord is disconnected
        # or the system just woke from sleep, send_notification could hang.
        # The approval decision (deny) is already made; this is just informational.
        import threading as _th
        _th.Thread(
            target=send_notification,
            args=(
                f"\u23f0 Approval timed out for `{path}`. Modification skipped.\n"
                f"_If you want to approve this later, reply:_ `approve {path}`",
            ),
            daemon=True,
            name="approval-timeout-notify",
        ).start()
        return False

    logger.info("Source approval for %s: %s", path, "APPROVED" if result else "DENIED")
    return result


# ── Ask User (free-form question) ────────────────────────────────

def _question_similarity(a: str, b: str) -> float:
    """Jaccard word overlap between two questions (0.0–1.0)."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _was_recently_asked(question: str) -> bool:
    """Check if a similar question was already sent within the cooldown window.

    Returns True if we should skip (question already asked recently).
    Also prunes expired entries.
    """
    now = time.time()
    # Prune old entries (outside lock — _recent_questions is only
    # mutated under _question_lock by ask_user, which we're inside)
    cutoff = now - _QUESTION_DEDUP_COOLDOWN
    _recent_questions[:] = [(t, q, a) for t, q, a in _recent_questions if t > cutoff]

    for _ts, prev_q, got_answer in _recent_questions:
        if _question_similarity(question, prev_q) >= _QUESTION_SIMILARITY_THRESHOLD:
            return True
    return False


def ask_user(
    question: str,
    timeout: float = 300,
) -> Optional[str]:
    """Ask Jesse a free-form question via Discord DM and wait for his reply.

    Time-aware: returns None immediately if it's quiet hours (outside
    working hours).  The caller should fall back to a sensible default.

    Cross-goal dedup: if a similar question was already asked within the
    last 10 minutes (by any goal), returns None instead of re-asking.

    Blocks the calling thread until the user replies or the timeout
    expires.  Safe to call from any worker thread.

    Args:
        question: The question text.
        timeout: Seconds to wait for a response (default 5 min).

    Returns:
        The user's text reply (stripped), or None if quiet hours / timeout / error.
    """
    global _pending_question, _question_response

    # Respect quiet hours — don't bother Jesse when he's sleeping
    try:
        from src.utils.time_awareness import is_quiet_hours
        if is_quiet_hours():
            logger.info("ask_user: skipping (quiet hours) — %s", question[:80])
            return None
    except Exception:
        pass  # If time_awareness fails, proceed anyway

    if not is_outbound_ready():
        logger.warning("ask_user: Discord not ready")
        return None

    # Cross-goal dedup: skip if a similar question was already asked recently
    with _question_lock:
        if _was_recently_asked(question):
            logger.info("ask_user: skipping (similar question asked within %ds) — %s",
                        _QUESTION_DEDUP_COOLDOWN, question[:60])
            return None

    # Dedup: if another task already has a question pending, don't spam
    # Jesse with a second one.  Piggyback on the existing question and
    # wait for that answer instead.
    with _question_lock:
        if _pending_question is not None and not _pending_question.is_set():
            existing_event = _pending_question
            logger.info("ask_user: another question already pending — piggybacking instead of spamming: %s", question[:60])
            # Release lock, then wait for the existing question's answer
        else:
            existing_event = None

    if existing_event is not None:
        responded = existing_event.wait(timeout=timeout)
        with _question_lock:
            return _question_response if responded else None

    # Set up the question gate (we're the first to ask)
    with _question_lock:
        # Double-check: another thread may have just set one between our
        # check above and acquiring the lock here
        if _pending_question is not None and not _pending_question.is_set():
            logger.info("ask_user: race — piggybacking on question that just appeared: %s", question[:60])
            evt = _pending_question
            responded = evt.wait(timeout=timeout)
            return _question_response if responded else None
        _pending_question = threading.Event()
        _question_response = None

    timeout_min = max(1, int(timeout // 60))
    msg = f"{question} (I'll use my best guess if I don't hear back in ~{timeout_min} min)"

    if not send_notification(msg):
        logger.warning("ask_user: failed to send question")
        with _question_lock:
            _pending_question = None
        return None

    # Record that we asked this question (for cross-goal dedup)
    with _question_lock:
        _recent_questions.append((time.time(), question, False))

    # Block until user responds or timeout
    responded = _pending_question.wait(timeout=timeout)

    with _question_lock:
        if not responded:
            logger.info("ask_user: timed out after %ds — %s", int(timeout), question[:60])
            _pending_question = None
            return None
        result = _question_response
        _pending_question = None
        # Update the record: we got an answer
        for i in range(len(_recent_questions) - 1, -1, -1):
            if _recent_questions[i][1] == question:
                _recent_questions[i] = (_recent_questions[i][0], question, True)
                break

    logger.info("ask_user: got reply — %s", (result or "")[:80])
    return result


def _has_pending_question() -> bool:
    """Check if there's a pending ask_user question (thread-safe)."""
    with _question_lock:
        return _pending_question is not None and not _pending_question.is_set()


def _has_pending_approval() -> bool:
    """Check if there's a pending source approval request (thread-safe)."""
    with _approval_lock:
        return _pending_approval is not None and not _pending_approval.is_set()


def _resolve_question_reply(content: str) -> None:
    """Deliver a user reply to the pending ask_user question.

    Called when the Router classifies a message as question_reply.
    """
    global _question_response
    with _question_lock:
        if _pending_question is None or _pending_question.is_set():
            return
        _question_response = content.strip()
        _pending_question.set()


def _resolve_approval(approved: bool, content: str = "") -> None:
    """Deliver a user approval/denial to the pending source approval.

    Called when the Router classifies a message as approval.
    Also handles "never <path>" cleanup-specific responses.
    """
    global _approval_result
    # Check for "never <path>" response (cleanup-specific)
    never_path = _check_cleanup_never(content)
    if never_path:
        with _approval_lock:
            _cleanup_never_paths.append(never_path)
        approved = False

    with _approval_lock:
        if _pending_approval is None or _pending_approval.is_set():
            return
        _approval_result = approved
        _pending_approval.set()


# ──────────────────────────────────────────────────────────────────────
#  File cleanup approval
# ──────────────────────────────────────────────────────────────────────

# Module-level state for cleanup approval (reuses the same approval gate)
_cleanup_never_paths: List[str] = []  # paths from "never <path>" responses


def request_cleanup_approval(
    stale_files: List[str],
    timeout: float = 120,
) -> str:
    """Request user approval to delete stale files via Discord DM.

    Blocks the calling thread until the user replies or timeout.
    Short timeout (2 min) to avoid stalling the dream cycle — if Jesse
    is busy, we just skip and ask again next cleanup cycle.

    Args:
        stale_files: List of workspace-relative file paths to propose for deletion.
        timeout: Seconds to wait for a response (default 2 min).

    Returns:
        One of:
        - "yes"  — user approved deletion of all listed files
        - "no"   — user denied (skip cleanup this time)
        - "never:<path>" — user wants a specific file marked as persistent
        - "timeout" — no response within timeout (safe default: don't delete)
    """
    global _pending_approval, _approval_result, _cleanup_never_paths

    if not stale_files:
        return "no"

    if not is_outbound_ready():
        logger.warning("Discord not ready — skipping cleanup proposal")
        return "timeout"

    # Set up the approval gate
    with _approval_lock:
        _pending_approval = threading.Event()
        _approval_result = False
        _cleanup_never_paths = []

    file_list = "\n".join(f"  • `{f}`" for f in stale_files[:15])
    if len(stale_files) > 15:
        file_list += f"\n  + {len(stale_files) - 15} more"

    msg = (
        f"🗑️ **Stale file cleanup proposal**\n"
        f"Found {len(stale_files)} files older than 14 days with no recent use:\n"
        f"{file_list}\n\n"
        f"Reply:\n"
        f"• **yes** — delete all listed files\n"
        f"• **no** — skip for now\n"
        f"• **never `<filename>`** — keep a specific file forever\n"
        f"No rush — if you're busy I'll skip this and ask again next time."
    )

    if not send_notification(msg):
        with _approval_lock:
            _pending_approval = None
        return "timeout"

    # Block until response or timeout
    responded = _pending_approval.wait(timeout=timeout)

    with _approval_lock:
        if not responded:
            _pending_approval = None
            return "timeout"
        result = _approval_result
        never_paths = list(_cleanup_never_paths)
        _pending_approval = None
        _cleanup_never_paths = []

    if never_paths:
        return f"never:{never_paths[0]}"

    return "yes" if result else "no"


def _check_cleanup_never(content: str) -> Optional[str]:
    """Check if a message is a 'never <path>' response to cleanup.

    Returns the path if matched, None otherwise.
    """
    lower = content.lower().strip()
    if lower.startswith("never "):
        path = content.strip()[6:].strip().strip("`'\"")
        if path:
            return path
    return None


# ──────────────────────────────────────────────────────────────────────
#  Message processing (existing)
# ──────────────────────────────────────────────────────────────────────

def process_with_archi(
    message: str,
    history: Optional[list] = None,
    progress_callback: Optional[Any] = None,
    router_result: Optional[Any] = None,
) -> Tuple[str, str, List]:
    """
    Process text message through Archi's action executor (blocking).

    Args:
        message: User's message text.
        history: Recent chat history for context.
        progress_callback: Optional callback for live progress updates during
            multi-step tasks. Called as progress_callback(step, max_steps, msg).
        router_result: Optional RouterResult from the Conversational Router (Phase 4).
            When provided, message_handler skips its own intent classification.

    Returns:
        (full_response_for_history, truncated_for_discord, actions_taken)
    """
    router = _get_router()
    if not router:
        msg = "Archi is not available. Check that the local model or OpenRouter API is configured."
        return msg, _truncate(msg), []

    from src.interfaces.message_handler import process_message

    response_text, actions_taken, cost = process_message(
        message, router, history=history, source="discord",
        goal_manager=_goal_manager, progress_callback=progress_callback,
        router_result=router_result,
    )

    out = response_text
    if actions_taken:
        action_lines = "\n".join(f"\u2022 {a.get('description', 'Done')}" for a in actions_taken)
        out = f"{out}\n\n{action_lines}"
    return out, _truncate(out), actions_taken


def process_image_with_archi(
    text_prompt: str,
    image_path: str,
) -> Tuple[str, str]:
    """
    Process an image through Archi's vision model (blocking).

    Returns:
        (full_response, truncated_for_discord)
    """
    router = _get_router()
    if not router:
        msg = "Archi vision is not available."
        return msg, _truncate(msg)

    # Auto-escalate to Claude Haiku for vision tasks (Grok has no vision)
    _auto_escalated = False
    try:
        _model_info = router.get_active_model_info()
        _current = (_model_info.get("model") or "").lower()
        if "claude" not in _current:
            router.switch_model_temp("claude-haiku", count=1)
            _auto_escalated = True
            logger.info("Auto-escalated to Claude Haiku for image analysis")
    except Exception:
        pass

    result = router.chat_with_image(text_prompt, image_path)
    cost = result.get("cost_usd", 0)
    text = result.get("text", "").strip()
    if not text:
        text = f"I couldn't analyze the image: {result.get('error', 'unknown error')}"

    # Revert auto-escalation
    if _auto_escalated:
        try:
            router.complete_temp_task()
        except Exception:
            pass

    out = text
    return out, _truncate(out)


# Cancel keywords that stop a running multi-step task
_CANCEL_EXACT = {"stop", "cancel", "nevermind", "never mind", "abort", "quit", "halt"}
_CANCEL_PHRASES = ("stop that", "cancel that", "stop working", "cancel task",
                   "never mind", "nevermind", "forget it", "forget that",
                   "stop the task", "cancel the task", "abort task")



def _get_goal_manager():
    """Return the goal_manager from the dream cycle instance, if available."""
    if _dream_cycle is not None:
        return _dream_cycle.goal_manager
    return None



def _parse_image_model_switch(content: str) -> Optional[str]:
    """Parse an image model switch command.

    Recognizes patterns like:
        "use illustrious for images"
        "switch image model to uber"
        "set image model to intorealism"
        "use uber for image generation"

    Returns the model alias string, or None if not an image model switch.
    """
    import re
    lower = content.lower().strip()

    # Pattern: "use X for images/image generation/pictures"
    m = re.match(
        r"(?:use|switch\s+to|set)\s+([a-z0-9_]+)\s+(?:for\s+)?(?:images?|image\s+(?:gen|generation|model)|pictures?)",
        lower,
    )
    if m:
        return m.group(1)

    # Pattern: "switch/set image model to X"
    m = re.match(
        r"(?:switch|set|change)\s+(?:the\s+)?image\s+model\s+to\s+([a-z0-9_]+)",
        lower,
    )
    if m:
        return m.group(1)

    return None


def _parse_model_switch(content: str) -> Optional[Tuple[str, bool, int]]:
    """Parse a model switch command from a message.

    Recognizes patterns like:
        "switch to grok"                         -> permanent switch (OpenRouter)
        "switch to grok direct"                  -> permanent switch (xAI direct)
        "use deepseek direct"                    -> permanent switch (DeepSeek direct)
        "switch to claude and try again"         -> permanent + retry
        "use claude direct for this task"        -> temp (1 message, Anthropic direct)
        "use claude for the next task"           -> temp (1 message)
        "switch to grok for 5 messages"          -> temp (5 messages)
        "use claude for this task and try again" -> temp + retry
        "switch to xai/grok-2"                   -> provider/model path

    Returns (model_name, should_retry, temp_count) or None if not a switch command.
    temp_count=0 means permanent, >0 means temporary for N messages.
    Adding "direct" after the model name appends "-direct" to the alias,
    which routes to the provider's own API instead of OpenRouter.
    """
    import re
    lower = content.lower().strip()

    # Pattern: "switch to <model>" with optional "direct", duration, and retry
    match = re.match(
        r"(?:switch\s+to|use|change\s+to|swap\s+to|set\s+model\s+to?)\s+"
        r"([a-z0-9_./-]+)"
        r"(\s+direct)?"
        r"(?:\s+for\s+(?:(?:this|the\s+next)\s+(?:task|message)|(\d+)\s+(?:messages?|tasks?|calls?)))?"
        r"(?:\s+and\s+(?:try\s+again|retry|redo))?",
        lower,
    )
    if match:
        model_name = match.group(1)
        is_direct = bool(match.group(2))
        retry = bool(re.search(r"\band\s+(?:try\s+again|retry|redo)\b", lower))

        # Append "-direct" suffix for direct provider routing
        if is_direct and "/" not in model_name:
            model_name = f"{model_name}-direct"

        # Determine temp count
        temp_count = 0
        if re.search(r"\bfor\s+(?:this|the\s+next)\s+(?:task|message)\b", lower):
            temp_count = 1
        elif match.group(3):
            temp_count = int(match.group(3))

        return (model_name, retry, temp_count)

    return None


def _parse_dream_cycle_interval(content: str) -> Optional[int]:
    """Parse a dream cycle interval command. Returns seconds or None.

    Recognizes patterns like:
        "set dream cycle to 15 minutes"
        "dream cycle 15 minutes"
        "switch dream cycles to 30 minutes"
        "set dream interval to 900 seconds"
        "15 minute dream cycles"
        "can you change the dream cycle delay to 2 minutes?"
        "please adjust the dream cycle to 10 minutes"
    """
    import re
    lower = content.lower().strip().rstrip("?!.")

    # Quick check: must mention "dream" to avoid false positives
    if "dream" not in lower:
        return None

    # Strip polite prefixes: "can you", "could you", "please", "would you", etc.
    lower = re.sub(
        r"^(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?",
        "", lower,
    ).strip()

    # _DC_WORDS matches "cycle", "interval", "delay", "timeout", "frequency",
    # and compound forms like "cycle delay", "cycle interval", "cycle timeout"
    _DC_WORDS = r"(?:cycle\s+)?(?:cycle|interval|delay|timeout|frequency)s?"

    # Pattern 1: "(set|switch|change|adjust|make|update) (the)? dream <dc_words> (to/at)? N unit"
    match = re.search(
        r"(?:set|switch|change|adjust|make|update)\s+(?:the\s+)?dream\s+"
        + _DC_WORDS + r"\s+"
        r"(?:to\s+|at\s+)?(\d+)\s*(minutes?|mins?|seconds?|secs?|s|m|hours?|hrs?|h)",
        lower,
    )
    if not match:
        # Pattern 2: "dream <dc_words> (to)? N unit"
        match = re.search(
            r"dream\s+" + _DC_WORDS + r"\s+"
            r"(?:to\s+)?(\d+)\s*(minutes?|mins?|seconds?|secs?|s|m|hours?|hrs?|h)",
            lower,
        )
    if not match:
        # Pattern 3: "N unit dream <dc_words>"
        match = re.search(
            r"(\d+)\s*(minutes?|mins?|seconds?|secs?|s|m|hours?|hrs?|h)\s+dream\s+"
            + _DC_WORDS,
            lower,
        )
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("h"):
        return value * 3600
    elif unit.startswith("m"):
        return value * 60
    else:  # seconds
        return value


def _should_respond(message, bot_user_id: int) -> bool:
    """True if we should respond to this message."""
    if message.author.bot:
        return False
    # DMs: always respond
    if message.guild is None:
        return True
    # In channels: only when bot is mentioned
    if message.mentions:
        return any(m.id == bot_user_id for m in message.mentions)
    return False


def _get_content(message, bot_user_id: int) -> str:
    """Extract the message content, stripping bot mention if present."""
    content = (message.content or "").strip()
    if message.mentions:
        for mention in message.mentions:
            if mention.id == bot_user_id:
                content = content.replace(f"<@{mention.id}>", "").strip()
                break
    return content


async def _extract_reply_context(message) -> Optional[str]:
    """If the user replied to a specific Discord message, fetch its content.

    This prevents context confusion when multiple notifications are sent
    in quick succession — the model can see exactly which message the
    user is responding to.

    Returns the referenced message's text (truncated to 300 chars), or None.
    """
    try:
        ref = getattr(message, "reference", None)
        if ref is None or ref.message_id is None:
            return None
        # Try cached version first, then fetch from API
        resolved = ref.resolved
        if resolved is None:
            resolved = await message.channel.fetch_message(ref.message_id)
        if resolved and resolved.content:
            text = resolved.content.strip()
            if len(text) > 300:
                text = text[:297] + "…"
            return text
    except Exception as e:
        logger.debug("Could not extract reply context: %s", e)
    return None



async def _download_attachment(attachment) -> Optional[str]:
    """Download a Discord attachment to local disk. Returns file path or None."""
    try:
        # Only handle images
        if not attachment.content_type or not attachment.content_type.startswith("image/"):
            return None
        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
        }
        ext = ext_map.get(attachment.content_type, ".png")
        upload_dir = _get_upload_dir()
        fname = f"discord_{uuid.uuid4().hex}{ext}"
        dest = upload_dir / fname
        await attachment.save(dest)
        logger.info("Downloaded Discord image: %s (%d bytes)", fname, attachment.size)
        return str(dest)
    except Exception as e:
        logger.error("Failed to download Discord attachment: %s", e)
        return None


async def _notify_interrupted_tasks() -> None:
    """Check for crash-recovered tasks and notify the user via Discord DM.

    Called once from on_ready after the DM channel is established.
    Uses the Notification Formatter (Phase 3) for natural messages.
    """
    if not _owner_dm_channel:
        return
    try:
        from src.core.plan_executor import PlanExecutor
        interrupted = PlanExecutor.get_interrupted_tasks()
        if not interrupted:
            return

        from src.core.notification_formatter import format_interrupted_tasks
        router = _get_router()
        fmt = format_interrupted_tasks(interrupted, router)

        await _owner_dm_channel.send(_truncate(fmt["message"]))
        logger.info("Notified user about %d interrupted task(s)", len(interrupted))
    except ImportError:
        logger.debug("PlanExecutor not available — skipping interrupted task check")
    except Exception as e:
        logger.warning("Failed to check/notify interrupted tasks: %s", e)


def create_bot() -> Any:
    """Create and return a configured Discord bot client."""
    try:
        import discord
    except ImportError:
        raise ImportError("discord.py is required. Run: pip install discord.py")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.dm_messages = True
    intents.dm_reactions = True
    intents.reactions = True

    class ArchiBot(discord.Client):
        async def on_ready(self):
            global _bot_client, _bot_loop, _owner_id
            _bot_client = self
            _bot_loop = asyncio.get_running_loop()

            # Resolve owner ID from env var (DISCORD_OWNER_ID)
            owner_id_str = os.environ.get("DISCORD_OWNER_ID", "").strip()
            if owner_id_str.isdigit():
                _owner_id = int(owner_id_str)
                await _ensure_owner_dm()
                logger.info("Discord bot ready: %s (owner ID %d from .env)",
                            self.user, _owner_id)
            else:
                logger.info("Discord bot ready: %s (no owner ID — will discover from first DM)",
                            self.user)
            logger.info("Archi Discord bot ready: %s", self.user)

            # Notify user about any interrupted tasks recovered from crash
            await _notify_interrupted_tasks()

        async def on_raw_reaction_add(self, payload):
            """Handle reactions on tracked notification messages.

            When Jesse reacts with 👍/👎 (or similar) on a completion message,
            record the feedback via the learning system.
            """
            # Ignore bot's own reactions
            if payload.user_id == self.user.id:
                return
            # Only process reactions from the owner
            if _owner_id is not None and payload.user_id != _owner_id:
                return
            # Check if this message is tracked for feedback
            emoji_str = str(payload.emoji)
            _FEEDBACK_EMOJIS = {"👍", "👎", "❤️", "🎉", "🔥", "😕", "😞"}
            if emoji_str in _FEEDBACK_EMOJIS and payload.message_id in _tracked_messages:
                _record_reaction_feedback(payload.message_id, emoji_str)

        async def on_message(self, message):
            if not _should_respond(message, self.user.id):
                return

            # Auto-discover owner from first DM (if not set via env var)
            global _owner_id, _owner_dm_channel
            if _owner_id is None and message.guild is None:
                _owner_id = message.author.id
                _owner_dm_channel = message.channel
                logger.info("Discord owner auto-discovered: %s (ID: %d)",
                            message.author.name, _owner_id)
                _persist_owner_id(_owner_id)

            # Reset dream cycle idle timer so dreams don't run mid-conversation
            if _dream_cycle is not None:
                _dream_cycle.mark_activity()
                _dream_cycle.reset_suggest_cooldown()

            content = _get_content(message, self.user.id)

            # ── Reply context: if the user replied to a specific message,
            # extract that message's content so the model knows what topic
            # the user is responding to (prevents context confusion when
            # multiple notifications are sent in quick succession).
            _reply_context = await _extract_reply_context(message)

            # ── Discord-level fast-paths (no model call, no Router) ───
            # These stay in discord_bot.py because they're Discord-specific
            # commands that don't need classification.

            # Check for deferred approval: "approve src/tools/foo.py"
            if content.lower().startswith("approve "):
                _deferred_path = content[8:].strip().strip("`")
                if _deferred_path in _deferred_approvals:
                    _info = _deferred_approvals.pop(_deferred_path)
                    logger.info(
                        "Deferred approval GRANTED for %s (originally timed out, "
                        "task: %s)", _deferred_path, _info.get("task", "?")[:80],
                    )
                    try:
                        from src.utils.paths import base_path
                        _pa_dir = os.path.join(base_path(), "data", "pre_approved")
                        os.makedirs(_pa_dir, exist_ok=True)
                        _pa_file = os.path.join(
                            _pa_dir,
                            _deferred_path.replace("/", "_").replace("\\", "_") + ".txt",
                        )
                        import time as _time
                        with open(_pa_file, "w") as _f:
                            _f.write(
                                f"path: {_deferred_path}\n"
                                f"action: {_info.get('action', '?')}\n"
                                f"task: {_info.get('task', '?')}\n"
                                f"approved_at: {_time.time()}\n"
                            )
                    except Exception as _e:
                        logger.warning("Failed to write pre-approval file: %s", _e)
                    await message.reply(
                        f"\u2705 Got it — `{_deferred_path}` is now pre-approved. "
                        f"Archi will use this approval next time it needs to modify that file."
                    )
                    return
                else:
                    await message.reply(
                        f"No pending approval found for `{_deferred_path}`. "
                        f"Currently waiting: {list(_deferred_approvals.keys()) or 'none'}"
                    )
                    return

            # ── Model switching: "switch to X" / "use X" ──────────────
            _switch_match = _parse_model_switch(content)
            if _switch_match is not None:
                _model_name, _retry, _temp_count = _switch_match
                router = _get_router()
                if router:
                    if _temp_count > 0:
                        result = router.switch_model_temp(_model_name, count=_temp_count)
                    else:
                        result = router.switch_model(_model_name)
                    reply_text = result["message"]
                    await message.reply(reply_text)

                    if _retry and message.author.id in _last_user_message:
                        _retry_content = _last_user_message[message.author.id]
                        await message.channel.send(
                            f"\U0001f504 Retrying your last message with **{result.get('display', _model_name)}**..."
                        )
                        content = _retry_content
                    else:
                        if _retry:
                            await message.channel.send("No previous message to retry.")
                        return
                else:
                    await message.reply("Model router not available.")
                    return

            # ── "try again" / "retry" without model switch ────────────
            if content.lower().strip() in ("try again", "retry", "redo", "redo that"):
                if message.author.id in _last_user_message:
                    content = _last_user_message[message.author.id]
                    await message.channel.send("\U0001f504 Retrying your last message...")
                else:
                    await message.reply("No previous message to retry.")
                    return

            # ── "what model" / "current model" / "status" check ────────
            _status_queries = (
                "what model", "current model", "which model", "model?",
                "status", "provider status", "api status",
            )
            if content.lower().strip() in _status_queries:
                router = _get_router()
                if router:
                    info = router.get_active_model_info()
                    _prov = info.get("provider", "openrouter")
                    _prov_label = f", provider: {_prov}" if _prov != "openrouter" else ""
                    from src.tools.image_gen import get_default_image_model_name, get_image_model_aliases
                    _img_default = get_default_image_model_name() or "auto"
                    _img_models = sorted(set(
                        k for k in get_image_model_aliases() if len(k) <= 20
                    ))
                    _img_info = f"\nImage model: **{_img_default}** (available: {', '.join(_img_models)})" if _img_models else ""

                    # Phase 8: Provider health status
                    _health_info = ""
                    try:
                        health = router.get_provider_health()
                        if health:
                            _state_icons = {"closed": "🟢", "open": "🔴", "half_open": "🟡"}
                            _lines = []
                            for p, h in health.items():
                                icon = _state_icons.get(h["state"], "⚪")
                                primary = " (primary)" if h.get("is_primary") else ""
                                _lines.append(f"{icon} {p}{primary}")
                            _health_info = "\n**Providers:** " + " | ".join(_lines)
                            if router.is_degraded():
                                _health_info += "\n⚠️ Running in **degraded mode**"
                    except Exception:
                        pass

                    await message.reply(
                        f"Currently using: **{info['display']}** (mode: {info['mode']}{_prov_label})"
                        f"{_img_info}{_health_info}"
                    )
                else:
                    await message.reply("Model router not available.")
                return

            # ── Image model switching: "use X for images" ─────────────
            _img_switch = _parse_image_model_switch(content)
            if _img_switch is not None:
                from src.tools.image_gen import set_default_image_model, get_image_model_aliases
                path = set_default_image_model(_img_switch)
                if path:
                    from pathlib import Path as _P
                    await message.reply(f"Image model set to **{_P(path).stem}**")
                else:
                    aliases = sorted(set(
                        k for k in get_image_model_aliases() if len(k) <= 20
                    ))
                    await message.reply(
                        f"Unknown image model '{_img_switch}'. "
                        f"Available: {', '.join(aliases) if aliases else 'none found'}"
                    )
                return

            # ── Dream cycle interval: "set dream cycle to 15 minutes" ─
            _dc_seconds = _parse_dream_cycle_interval(content)
            if _dc_seconds is not None:
                if _dream_cycle is not None:
                    msg = _dream_cycle.set_idle_threshold(_dc_seconds)
                    await message.reply(msg)
                else:
                    await message.reply("Dream cycle not available.")
                return

            # ── Dream cycle status: "dream cycle?" / "dream status" ───
            _dc_lower = content.lower().strip().rstrip("?!.")
            if _dc_lower in (
                "dream cycle", "dream status", "dream cycle status",
                "dream interval", "what dream cycle", "dream cycle delay",
                "dream delay", "dream timeout", "dream frequency",
                "what is the dream cycle", "what's the dream cycle",
                "what is the dream cycle delay", "what's the dream cycle delay",
            ):
                if _dream_cycle is not None:
                    secs = _dream_cycle.get_idle_threshold()
                    mins = secs / 60
                    if mins == int(mins):
                        await message.reply(
                            f"Dream cycle idle threshold: **{int(mins)} minute{'s' if mins != 1 else ''}** ({secs}s)"
                        )
                    else:
                        await message.reply(
                            f"Dream cycle idle threshold: **{mins:.1f} minutes** ({secs}s)"
                        )
                else:
                    await message.reply("Dream cycle not available.")
                return

            # Check for image attachments
            image_path = None
            if message.attachments:
                for att in message.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        image_path = await _download_attachment(att)
                        if image_path:
                            break

            # Need either text or image
            if not content and not image_path:
                return

            # Track last message for "try again" support
            _last_user_message[message.author.id] = content

            # ── Conversational Router (Phase 4) ───────────────────────
            # Single model call that classifies intent AND generates
            # easy-tier answers in one shot. Replaces all heuristic
            # routing (suggestion picks, approval parsing, question
            # reply detection, cancel detection, reply topic inference).
            async with message.channel.typing():
                try:
                    from src.interfaces.chat_history import get_recent, append

                    # Vision path bypasses Router (goes straight to vision model)
                    if image_path:
                        text_prompt = content or "Describe what you see in this image."
                        logger.info("Discord: vision analysis for %s", image_path)
                        full_response, response = await asyncio.to_thread(
                            process_image_with_archi, text_prompt, image_path
                        )
                        await message.reply(response)
                        try:
                            append("user", f"[Image attached] {content}")
                            append("assistant", full_response)
                        except Exception:
                            pass
                        return

                    # ── Build Router context ──────────────────────────
                    history = get_recent()

                    # Prepend reply context if user replied to a specific message
                    if _reply_context:
                        content = (
                            f"[Replying to Archi's message: \"{_reply_context}\"]\n\n"
                            f"{content}"
                        )

                    from src.core.conversational_router import (
                        route as router_route, ContextState,
                    )

                    # Gather context state for the Router
                    _pending_suggs = []
                    _recent_suggs = []
                    if _dream_cycle is not None:
                        if hasattr(_dream_cycle, '_pending_suggestions'):
                            _pending_suggs = [
                                s.get("description", "") for s in (_dream_cycle._pending_suggestions or [])
                            ]
                        if hasattr(_dream_cycle, '_recent_suggestions') and not _pending_suggs:
                            _recent_suggs = [
                                s.get("description", "") for s in (_dream_cycle._recent_suggestions or [])
                            ]

                    ctx = ContextState(
                        pending_suggestions=_pending_suggs,
                        recent_suggestions=_recent_suggs,
                        pending_approval=_has_pending_approval(),
                        pending_question=_has_pending_question(),
                    )

                    router = _get_router()
                    if not router:
                        await message.reply("Archi is not available. Check model configuration.")
                        return

                    # Build history messages for Router
                    from src.interfaces.message_handler import _build_history_messages
                    history_messages = _build_history_messages(history)

                    # ── Single Router call ────────────────────────────
                    rr = await asyncio.to_thread(
                        router_route, content, router, ctx, history_messages, _goal_manager,
                    )
                    logger.info(
                        "Router: intent=%s tier=%s fast_path=%s cost=$%.4f",
                        rr.intent, rr.tier, rr.fast_path, rr.cost,
                    )

                    # ── Dispatch based on Router result ───────────────

                    # Cancel
                    if rr.intent == "cancel":
                        try:
                            from src.core.plan_executor import signal_task_cancellation
                            signal_task_cancellation(content)
                            await message.reply(
                                "Got it — cancelling the current task. "
                                "I'll wrap up after the current step finishes."
                            )
                        except ImportError:
                            await message.reply("Cancellation not available.")
                        return

                    # Suggestion pick (single or multi)
                    # Fall back to recent_suggestions if pending is empty
                    if rr.intent == "suggestion_pick" and (rr.pick_number > 0 or rr.pick_numbers):
                        _sugg_source = None
                        if _dream_cycle is not None:
                            if _dream_cycle._pending_suggestions:
                                _sugg_source = _dream_cycle._pending_suggestions
                            elif getattr(_dream_cycle, '_recent_suggestions', None):
                                # User is referencing an old suggestion — the router
                                # saw it in recent_suggestions context. Use those.
                                _sugg_source = _dream_cycle._recent_suggestions[-5:]
                                logger.info(
                                    "Suggestion pick using recent suggestions "
                                    "(pending was empty, %d recent available)",
                                    len(_sugg_source),
                                )
                        if _sugg_source:
                            suggestions = _sugg_source
                            # Determine which indices to pick
                            picks = rr.pick_numbers if rr.pick_numbers else (
                                [rr.pick_number] if rr.pick_number > 0 else []
                            )
                            valid_picks = [p for p in picks if 1 <= p <= len(suggestions)]

                            if valid_picks:
                                _dream_cycle._pending_suggestions = []

                                # Record in idea history
                                try:
                                    from src.core.idea_history import get_idea_history
                                    hist = get_idea_history()
                                    for p in valid_picks:
                                        hist.record_accepted(
                                            suggestions[p - 1].get("description", "")
                                        )
                                    batch_id = getattr(_dream_cycle, '_pending_batch_id', None)
                                    if batch_id:
                                        hist.mark_batch_ignored(batch_id)
                                        _dream_cycle._pending_batch_id = None
                                except Exception:
                                    pass

                                # Create goals for each pick
                                try:
                                    gm = _get_goal_manager()
                                    if gm:
                                        created = []
                                        for p in valid_picks:
                                            chosen = suggestions[p - 1]
                                            desc = chosen.get("description", "")
                                            if not desc:
                                                continue
                                            category = chosen.get("category", "General")
                                            goal = gm.create_goal(
                                                description=desc,
                                                user_intent=f"User picked suggestion #{p} ({category})",
                                                priority=5,
                                            )
                                            _dream_cycle.kick(goal_id=goal.goal_id, reactive=True)
                                            created.append((p, desc[:60], goal.goal_id))
                                        if len(created) == 1:
                                            await message.reply(
                                                "On it — planning the approach now. "
                                                "I'll message you when it's done."
                                            )
                                        elif created:
                                            await message.reply(
                                                f"On it — working on {len(created)} tasks. "
                                                "I'll message you when they're done."
                                            )
                                        for p, desc_short, gid in created:
                                            logger.info(
                                                "User picked suggestion #%d: %s -> %s",
                                                p, desc_short, gid,
                                            )
                                    else:
                                        await message.reply("Goal manager not available.")
                                except Exception as _e:
                                    logger.error("Failed to create goal from suggestion: %s", _e)
                                    await message.reply(f"Error creating goal: {_e}")
                                return
                            else:
                                await message.reply(
                                    f"Please pick a number between 1 and {len(suggestions)}."
                                )
                                return

                    # If suggestions were pending but user didn't pick any,
                    # record them as ignored so future brainstorms avoid them.
                    if (rr.intent != "suggestion_pick"
                            and _dream_cycle is not None
                            and getattr(_dream_cycle, '_pending_suggestions', None)):
                        try:
                            from src.core.idea_history import get_idea_history
                            batch_id = getattr(_dream_cycle, '_pending_batch_id', None)
                            if batch_id:
                                get_idea_history().mark_batch_ignored(batch_id)
                                _dream_cycle._pending_batch_id = None
                            _dream_cycle._pending_suggestions = []
                            logger.info("Pending suggestions dismissed (user moved on)")
                        except Exception:
                            pass

                    # Approval response
                    if rr.intent == "approval" and rr.approval is not None:
                        _resolve_approval(rr.approval, content)
                        if rr.approval:
                            await message.reply("\u2705 Approved. Proceeding with modification.")
                        else:
                            await message.reply("\u274c Denied. Modification skipped.")
                        return

                    # Question reply
                    if rr.intent == "question_reply":
                        _resolve_question_reply(content)
                        await message.reply("\U0001f44d Got it, thanks!")
                        return

                    # Easy tier: Router already generated the answer
                    if rr.tier == "easy" and rr.answer and not rr.action:
                        response = _truncate(rr.answer)
                        await message.reply(response)
                        try:
                            append("user", content)
                            append("assistant", rr.answer)
                        except Exception:
                            pass
                        return

                    # Easy tier with action: pass to message_handler for action dispatch
                    # Complex tier: pass to message_handler for full processing
                    # Both paths go through process_with_archi which calls message_handler

                    loop = asyncio.get_running_loop()
                    _status_ref = [None]
                    _last_update = [0.0]

                    def _progress_callback(step_num, max_steps, status_text):
                        """Send/edit a progress message from the worker thread."""
                        import time as _time
                        now = _time.monotonic()
                        if _status_ref[0] is not None and (now - _last_update[0]) < 3.0:
                            return
                        _last_update[0] = now
                        est_prefix = "~" if step_num >= 3 else ""
                        progress_line = f"\u23f3 Step {step_num}/{est_prefix}{max_steps}: {status_text}"

                        async def _send_or_edit():
                            try:
                                if _status_ref[0] is None:
                                    _status_ref[0] = await message.channel.send(progress_line)
                                else:
                                    await _status_ref[0].edit(content=progress_line)
                            except Exception as e:
                                logger.debug("Progress update failed: %s", e)

                        future = asyncio.run_coroutine_threadsafe(_send_or_edit(), loop)
                        try:
                            future.result(timeout=5)
                        except Exception:
                            pass

                    full_response, response, actions_taken = await asyncio.to_thread(
                        process_with_archi, content, history, _progress_callback, rr
                    )

                    # Clean up the progress message
                    if _status_ref[0] is not None:
                        try:
                            await _status_ref[0].delete()
                        except Exception:
                            pass

                    # Check if actions include generated images or screenshot → send as attachment(s)
                    media_files = []
                    for act in (actions_taken or []):
                        desc = act.get("description", "")
                        if desc.startswith("Generated image:") or desc == "Screenshot taken":
                            img_path = act.get("result", {}).get("image_path", "")
                            if img_path and os.path.isfile(img_path):
                                try:
                                    media_files.append(
                                        discord.File(img_path, filename=os.path.basename(img_path))
                                    )
                                except Exception as e:
                                    logger.warning("Failed to open image file %s: %s", img_path, e)

                    if media_files:
                        try:
                            await message.reply(response, files=media_files[:10])
                            for f in media_files:
                                logger.info("Sent image to Discord: %s", f.filename)
                        except Exception as e:
                            logger.warning("Failed to send images: %s", e)
                            await message.reply(response)
                    else:
                        await message.reply(response)

                    # Check if a temporary model switch just expired
                    if router:
                        _revert_msg = router.complete_temp_task()
                        if _revert_msg:
                            await message.channel.send(_revert_msg)

                    # Persist to chat history
                    try:
                        append("user", content)
                        append("assistant", full_response)
                    except Exception as e:
                        logger.debug("Could not save chat history: %s", e)
                except Exception as e:
                    logger.error("Discord bot error: %s", e, exc_info=True)
                    await message.reply(f"Sorry, I encountered an error: {str(e)}")

    return ArchiBot(intents=intents)


async def _ensure_owner_dm() -> None:
    """Open a DM channel with the owner (if owner ID is known)."""
    global _bot_client, _owner_dm_channel, _owner_id
    if not _bot_client or not _owner_id:
        return
    try:
        user = await _bot_client.fetch_user(_owner_id)
        _owner_dm_channel = await user.create_dm()
        logger.info("Discord DM channel ready for owner: %s (ID: %d)", user.name, _owner_id)
    except Exception as e:
        logger.warning("Could not open DM with owner (ID: %d): %s", _owner_id, e)


def run_bot(token: Optional[str] = None) -> None:
    """Run the Discord bot (blocking)."""
    token = token or os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError(
            "DISCORD_BOT_TOKEN not set. Create a bot at https://discord.com/developers/applications "
            "and add the token to .env"
        )

    bot = create_bot()
    bot.run(token)
