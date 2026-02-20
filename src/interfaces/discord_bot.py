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

def send_notification(text: str, file_path: Optional[str] = None) -> bool:
    """
    Send a proactive message to the owner via Discord DM.

    Can be called from any thread (dream cycle, agent loop, etc.).
    Returns True if the message was queued successfully.

    Args:
        text: Message text (truncated to ~1900 chars for Discord).
        file_path: Optional path to a file to attach to the message.

    Usage:
        from src.interfaces.discord_bot import send_notification
        send_notification("I finished working on your Health Optimization goal.")
        send_notification("Here's the report:", file_path="workspace/reports/roadmap.md")
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
        future.result(timeout=10)
        logger.info("Discord notification sent: %s", truncated[:80])

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
        f"🔒 I want to modify a source file — need your OK.\n"
        f"**File:** `{path}` ({action})\n"
        f"**Why:** {task_description[:200]}\n\n"
        f"Reply **yes** or **no**. (Auto-denies in {int(timeout)}s)"
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
    msg = (
        f"Quick question — {question}\n\n"
        f"_(No rush — if you don't reply in ~{timeout_min} min I'll just use my best judgment.)_"
    )

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


def _is_likely_new_command(content: str) -> bool:
    """Heuristic: does this message look like a new command/question rather
    than a reply to a pending ask_user question?

    We check against fast-path patterns (datetime, slash commands, greetings,
    image generation, etc.) so they don't get swallowed by ask_user.
    """
    msg = content.strip()
    msg_lower = msg.lower()

    # Slash commands are always new commands
    if msg_lower.startswith("/"):
        return True

    # Datetime questions
    _DATETIME_PATTERNS = (
        "what day", "today's date", "current date", "what's the date",
        "what is the date", "what time", "current time", "day of the week",
        "what date", "what is today",
    )
    if any(p in msg_lower for p in _DATETIME_PATTERNS):
        return True

    # Screenshot requests
    _SCREENSHOT_PATTERNS = (
        "take a screenshot", "take screenshot", "screenshot",
        "capture the screen", "capture screen",
    )
    if any(p in msg_lower for p in _SCREENSHOT_PATTERNS):
        return True

    # Image generation (common starters)
    _IMG_STARTERS = (
        "generate an image", "generate image", "generate a picture",
        "create an image", "create image", "draw me", "draw a",
        "make an image", "make a picture", "send me a picture",
    )
    if any(msg_lower.startswith(p) for p in _IMG_STARTERS):
        return True

    # Goal-creation commands
    _GOAL_PATTERNS = (
        "make a goal", "create a goal", "new goal",
        "help me build", "help me make", "help me create",
    )
    if any(p in msg_lower for p in _GOAL_PATTERNS):
        return True

    # Stop/cancel commands
    if msg_lower in ("stop", "cancel", "nevermind", "never mind", "abort"):
        return True

    return False


def _check_pending_question(content: str) -> Optional[str]:
    """Check if a message is a reply to a pending ask_user question.

    Returns the user's text if a question is pending, None otherwise.
    Unlike approval (yes/no), any non-empty text is a valid answer.

    IMPORTANT: Messages that look like new commands (datetime questions,
    slash commands, goal creation, etc.) are NOT consumed as replies —
    they fall through to normal message processing.
    """
    with _question_lock:
        if _pending_question is None or _pending_question.is_set():
            return None

    stripped = content.strip()
    if not stripped:
        return None

    # Don't eat messages that look like new commands/questions
    if _is_likely_new_command(stripped):
        logger.info("ask_user: letting message through (looks like a new command): %s", stripped[:60])
        return None

    return stripped


def _check_pending_approval(content: str) -> Optional[bool]:
    """Check if a message is a response to a pending source approval.

    Returns True (approved), False (denied), or None (not an approval response).
    Thread-safe: reads _pending_approval under lock.

    Handles both exact responses ("yes", "no") and natural language that
    starts with or contains a clear approval/denial signal, e.g.
    "No, I don't think you need to do that" or "yeah go ahead with that".
    """
    with _approval_lock:
        if _pending_approval is None or _pending_approval.is_set():
            return None
    lower = content.lower().strip()

    # Check for "never <path>" response (cleanup-specific)
    never_path = _check_cleanup_never(content)
    if never_path:
        with _approval_lock:
            _cleanup_never_paths.append(never_path)
        return False  # "never" counts as deny (don't delete), but stores the path

    # Exact matches (fastest path)
    _APPROVE_EXACT = {"yes", "y", "approve", "approved", "ok", "go ahead", "go",
                      "yeah", "yep", "sure", "do it", "go for it"}
    _DENY_EXACT = {"no", "n", "deny", "denied", "stop", "cancel", "nope",
                   "nah", "don't", "dont"}
    if lower in _APPROVE_EXACT:
        return True
    if lower in _DENY_EXACT:
        return False

    # First-word check: natural language starting with yes/no signal.
    # Handles "No, I don't think you need to do that" and "Yes, go ahead".
    # Strip leading punctuation/whitespace after first word.
    first_word = lower.split(",")[0].split(" ")[0].rstrip(".,!?;:")
    if first_word in ("no", "nah", "nope", "don't", "dont", "deny", "stop", "cancel"):
        return False
    if first_word in ("yes", "yeah", "yep", "sure", "ok", "approve", "go"):
        return True

    # Phrase check: contains a clear signal anywhere in a short message.
    # Only for short messages (<80 chars) to avoid false positives in
    # normal conversation that happens to contain "no".
    if len(lower) < 80:
        _DENY_PHRASES = ("don't do that", "dont do that", "skip it",
                         "not approved", "do not", "don't need",
                         "dont need", "no thanks", "no need")
        _APPROVE_PHRASES = ("go ahead", "go for it", "sounds good",
                            "that's fine", "thats fine", "do it",
                            "approved")
        for phrase in _DENY_PHRASES:
            if phrase in lower:
                return False
        for phrase in _APPROVE_PHRASES:
            if phrase in lower:
                return True

    return None


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
) -> Tuple[str, str, List]:
    """
    Process text message through Archi's action executor (blocking).

    Args:
        message: User's message text.
        history: Recent chat history for context.
        progress_callback: Optional callback for live progress updates during
            multi-step tasks. Called as progress_callback(step, max_steps, msg).

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


def _parse_suggestion_pick(content: str) -> Optional[int]:
    """Parse a numbered suggestion pick from a message.

    Recognizes: "1", "2", "#1", "#2", "do 1", "do #2", "pick 3", "option 1"
    Returns the 1-based index, or None if not a pick.
    """
    import re
    lower = content.strip().lower()
    # Only match short messages to avoid false positives
    if len(lower) > 20:
        return None
    match = re.match(r"^(?:do\s+|pick\s+|option\s+|start\s+|#)?(\d)$", lower)
    if match:
        return int(match.group(1))
    return None


def _get_goal_manager():
    """Return the goal_manager from the dream cycle instance, if available."""
    if _dream_cycle is not None:
        return _dream_cycle.goal_manager
    return None


def _is_cancel_request(content: str) -> bool:
    """Detect if a message is a request to cancel the running task.

    Only matches short, unambiguous cancel signals to avoid false positives
    on normal conversation that happens to contain "stop".
    """
    lower = content.lower().strip()
    if lower in _CANCEL_EXACT:
        return True
    # Only check phrases in short messages to avoid false positives
    if len(lower) < 40:
        return any(phrase in lower for phrase in _CANCEL_PHRASES)
    return False


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


def _infer_reply_topic(
    user_msg: str, history: List[dict], min_overlap: int = 2,
) -> Optional[str]:
    """Infer which recent Archi message the user is responding to.

    Uses keyword overlap between the user's message and recent assistant
    messages.  Only triggers when:
      - There are 2+ recent assistant messages without a user message
        between them (i.e. back-to-back notifications).
      - One notification clearly matches the user's message better than
        the others (at least ``min_overlap`` shared keywords, and the
        best match has ≥2 more shared keywords than the runner-up).

    Returns the best-matching notification text (truncated to 300 chars),
    or None if the match is ambiguous or there's nothing to disambiguate.
    """
    import re as _re

    if not user_msg or not history:
        return None

    # Collect trailing assistant messages (back-to-back notifications)
    recent_assistant: List[str] = []
    for m in reversed(history):
        role = m.get("role", "user")
        if role == "assistant":
            text = (m.get("content") or "").strip()
            if text:
                recent_assistant.append(text)
        else:
            break  # stop at the first user message

    # Only disambiguate if there are 2+ back-to-back notifications
    if len(recent_assistant) < 2:
        return None

    # Build keyword set from user message (lowercase, 3+ chars, no stopwords)
    _STOPWORDS = {
        "the", "and", "but", "for", "not", "that", "this", "with", "are",
        "was", "were", "been", "have", "has", "had", "its", "from", "they",
        "them", "than", "into", "also", "just", "any", "some", "yet", "about",
        "know", "good", "thanks", "thank", "okay", "yeah", "yes", "sure",
        "think", "really", "would", "could", "should", "don't", "didn't",
        "isn't", "won't", "can't", "i'm", "it's", "you", "your", "those",
        "other", "taking", "like",
    }
    user_words = set(
        w for w in _re.findall(r"[a-z']+", user_msg.lower())
        if len(w) >= 3 and w not in _STOPWORDS
    )
    if not user_words:
        return None

    # Score each notification by keyword overlap
    scores = []
    for text in recent_assistant:
        notif_words = set(
            w for w in _re.findall(r"[a-z']+", text.lower())
            if len(w) >= 3 and w not in _STOPWORDS
        )
        overlap = len(user_words & notif_words)
        scores.append((overlap, text))

    scores.sort(key=lambda x: x[0], reverse=True)
    best_score, best_text = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0

    # Only tag if the best match is clearly better
    if best_score >= min_overlap and (best_score - runner_up) >= 2:
        if len(best_text) > 300:
            best_text = best_text[:297] + "…"
        logger.debug(
            "Inferred reply topic (score=%d vs %d): %s",
            best_score, runner_up, best_text[:80],
        )
        return best_text

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
    If PlanExecutor has interrupted task state from a previous crash,
    sends a notification so the user knows work will resume.
    """
    if not _owner_dm_channel:
        return
    try:
        from src.core.plan_executor import PlanExecutor
        interrupted = PlanExecutor.get_interrupted_tasks()
        if not interrupted:
            return

        if len(interrupted) == 1:
            task = interrupted[0]
            desc = task.get("description", "unknown task")[:150]
            msg = (
                f"🔄 Looks like I was in the middle of something before the restart — "
                f"*{desc}*. I'll pick it back up shortly."
            )
        else:
            lines = []
            for task in interrupted[:5]:
                desc = task.get("description", "unknown")[:100]
                lines.append(f"  • *{desc}*")
            msg = (
                f"🔄 Found {len(interrupted)} things I was working on before the restart:\n"
                + "\n".join(lines)
                + "\nI'll pick these back up shortly."
            )

        await _owner_dm_channel.send(_truncate(msg))
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

            content = _get_content(message, self.user.id)

            # ── Reply context: if the user replied to a specific message,
            # extract that message's content so the model knows what topic
            # the user is responding to (prevents context confusion when
            # multiple notifications are sent in quick succession).
            _reply_context = await _extract_reply_context(message)

            # Check if this message is a reply to a pending ask_user question
            question_reply = _check_pending_question(content)
            if question_reply is not None:
                with _question_lock:
                    _question_response = question_reply
                    _pending_question.set()
                await message.reply("\U0001f44d Got it, thanks!")
                return

            # Check if this message is a response to a pending source approval
            approval_response = _check_pending_approval(content)
            if approval_response is not None:
                with _approval_lock:
                    _approval_result = approval_response
                    _pending_approval.set()
                if approval_response:
                    await message.reply("\u2705 Approved. Proceeding with modification.")
                else:
                    await message.reply("\u274c Denied. Modification skipped.")
                return  # Don't process as a normal message

            # Check for suggestion pick: user replies "1", "2", "#3", etc.
            # to select from brainstormed work suggestions
            if _dream_cycle is not None and _dream_cycle._pending_suggestions:
                _pick = _parse_suggestion_pick(content)
                if _pick is not None:
                    suggestions = _dream_cycle._pending_suggestions
                    if 1 <= _pick <= len(suggestions):
                        chosen = suggestions[_pick - 1]
                        desc = chosen.get("description", "")
                        _dream_cycle._pending_suggestions = []  # Clear suggestions
                        # Create a goal from the chosen suggestion
                        try:
                            gm = _get_goal_manager()
                            if gm and desc:
                                category = chosen.get("category", "General")
                                goal = gm.create_goal(
                                    description=desc,
                                    user_intent=f"User picked suggestion #{_pick} ({category})",
                                    priority=5,
                                )
                                _dream_cycle.kick(goal_id=goal.goal_id)  # Start working immediately
                                await message.reply(
                                    f"\u2705 Got it — starting on that now! (Goal: {desc[:150]})"
                                )
                                logger.info(
                                    "User picked suggestion #%d: %s -> %s",
                                    _pick, desc[:60], goal.goal_id,
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

            # Check for deferred approval: "approve src/tools/foo.py"
            if content.lower().startswith("approve "):
                _deferred_path = content[8:].strip().strip("`")
                if _deferred_path in _deferred_approvals:
                    _info = _deferred_approvals.pop(_deferred_path)
                    logger.info(
                        "Deferred approval GRANTED for %s (originally timed out, "
                        "task: %s)", _deferred_path, _info.get("task", "?")[:80],
                    )
                    # Write a pre-approval file that the dream cycle can check
                    # before requesting approval again.
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
                    # No matching deferred approval
                    await message.reply(
                        f"No pending approval found for `{_deferred_path}`. "
                        f"Currently waiting: {list(_deferred_approvals.keys()) or 'none'}"
                    )
                    return

            # ── Task cancellation: "stop", "cancel", "nevermind" ─────
            if _is_cancel_request(content):
                try:
                    from src.core.plan_executor import signal_task_cancellation
                    signal_task_cancellation(content)
                    await message.reply(
                        "⏹️ Got it — cancelling the current task. "
                        "I'll wrap up after the current step finishes."
                    )
                except ImportError:
                    await message.reply("Cancellation not available.")
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

                    # If the user said "switch to X and try again" (or similar),
                    # re-process the last message with the new model.
                    if _retry and message.author.id in _last_user_message:
                        _retry_content = _last_user_message[message.author.id]
                        await message.channel.send(
                            f"\U0001f504 Retrying your last message with **{result.get('display', _model_name)}**..."
                        )
                        # Fall through to normal processing with the retry content
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

            # ── "what model" / "current model" status check ───────────
            if content.lower().strip() in ("what model", "current model", "which model", "model?"):
                router = _get_router()
                if router:
                    info = router.get_active_model_info()
                    _prov = info.get("provider", "openrouter")
                    _prov_label = f", provider: {_prov}" if _prov != "openrouter" else ""
                    # Include image model info
                    from src.tools.image_gen import get_default_image_model_name, get_image_model_aliases
                    _img_default = get_default_image_model_name() or "auto"
                    _img_models = sorted(set(
                        k for k in get_image_model_aliases() if len(k) <= 20
                    ))
                    _img_info = f"\nImage model: **{_img_default}** (available: {', '.join(_img_models)})" if _img_models else ""
                    await message.reply(
                        f"Currently using: **{info['display']}** (mode: {info['mode']}{_prov_label}){_img_info}"
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

            async with message.channel.typing():
                try:
                    from src.interfaces.chat_history import get_recent, append

                    actions_taken = []
                    if image_path:
                        # Vision path: analyze the image
                        text_prompt = content or "Describe what you see in this image."
                        logger.info("Discord: vision analysis for %s", image_path)
                        full_response, response = await asyncio.to_thread(
                            process_image_with_archi, text_prompt, image_path
                        )
                    else:
                        # Text-only path — with live progress updates
                        history = get_recent()

                        # If the user replied to a specific message, prepend
                        # that context so the model knows what they're responding to.
                        # If they didn't use reply, try to infer which recent
                        # notification they're responding to via keyword overlap.
                        if _reply_context:
                            content = (
                                f"[Replying to Archi's message: \"{_reply_context}\"]\n\n"
                                f"{content}"
                            )
                        else:
                            _inferred = _infer_reply_topic(content, history)
                            if _inferred:
                                content = (
                                    f"[Likely responding to Archi's message: \"{_inferred}\"]\n\n"
                                    f"{content}"
                                )

                        loop = asyncio.get_running_loop()
                        _status_msg = None  # mutable container for the status message
                        _status_ref = [None]  # list so closure can mutate it
                        _last_update = [0.0]  # throttle edits to avoid rate limits

                        def _progress_callback(step_num, max_steps, status_text):
                            """Send/edit a progress message from the worker thread."""
                            import time as _time
                            now = _time.monotonic()
                            # Throttle: don't edit more than once every 3 seconds
                            if _status_ref[0] is not None and (now - _last_update[0]) < 3.0:
                                return
                            _last_update[0] = now

                            # Show "~" prefix on estimate once we have enough data
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
                                future.result(timeout=5)  # wait briefly so edits are ordered
                            except Exception:
                                pass

                        full_response, response, actions_taken = await asyncio.to_thread(
                            process_with_archi, content, history, _progress_callback
                        )

                        # Clean up the progress message now that we have the real response
                        if _status_ref[0] is not None:
                            try:
                                await _status_ref[0].delete()
                            except Exception:
                                pass  # message may already be gone

                    # Check if actions include generated images or screenshot → send as attachment(s)
                    media_files = []
                    for act in actions_taken:
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
                            # Discord supports up to 10 files per message
                            await message.reply(response, files=media_files[:10])
                            for f in media_files:
                                logger.info("Sent image to Discord: %s", f.filename)
                        except Exception as e:
                            logger.warning("Failed to send images: %s", e)
                            # Fall back to text-only if attachment fails
                            await message.reply(response)
                    else:
                        await message.reply(response)

                    # Check if a temporary model switch just expired
                    # (the router ticks down in generate() and tags the response)
                    router = _get_router()
                    if router:
                        _revert_msg = router.complete_temp_task()
                        if _revert_msg:
                            await message.channel.send(_revert_msg)

                    # Persist to chat history
                    try:
                        user_msg = f"[Image attached] {content}" if image_path else content
                        append("user", user_msg)
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
