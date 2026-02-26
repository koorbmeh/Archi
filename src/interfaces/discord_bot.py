"""
Discord Bot Interface - Chat with Archi from Discord.

Listens to DMs and @mentions, sends messages to Archi via message_handler.
Supports text messages and image attachments (analyzed via vision model).

Outbound messaging: other components (heartbeat, agent loop) can call
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

from src.utils.config import get_user_name

logger = logging.getLogger(__name__)

_router: Optional[Any] = None
_goal_manager: Optional[Any] = None
_heartbeat: Optional[Any] = None
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
_approval_message_id: Optional[int] = None  # Discord msg ID for reaction-based approval

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
# so the heartbeat can retry the task in a future cycle.
_deferred_approvals: Dict[str, Dict[str, Any]] = {}  # path -> {"action", "task", "ts"}

# ── Quiet-hours notification accumulator (session 101) ────────────
# Messages suppressed during quiet hours are queued here and drained
# in a single digest when the user next sends a message.
_suppressed_queue: list[str] = []
_suppressed_lock = threading.Lock()
_MAX_SUPPRESSED = 50  # cap to avoid unbounded growth overnight

# Last message per user — enables "try again" / "retry" to re-process
# the previous message with a different model.
_last_user_message: Dict[int, str] = {}  # user_id -> last message text

# ── Feedback tracking (Phase 3) ──────────────────────────────────
# Maps Discord message IDs → structured context for reaction-based feedback.
# When the user reacts 👍/👎, we look up the context here and record it.
_tracked_messages: Dict[int, Dict[str, Any]] = {}  # msg_id -> {goal, task, ...}
_MAX_TRACKED = 100  # Prune oldest entries beyond this

# ── Tone feedback tracking (session 98) ─────────────────────────
# Maps Discord message IDs → response text snippet for easy-tier chat
# responses. When the user reacts, we record tone preference in UserModel.
_chat_response_messages: Dict[int, str] = {}  # msg_id -> response snippet
_MAX_CHAT_TRACKED = 50

# ── Startup timestamp guard ───────────────────────────────────────
# When the bot connects, Discord's gateway may replay recent DM messages
# which fire on_message. We record the time the bot became ready and
# skip any message created before that moment. Simple, no race condition.
_ready_at: Optional[float] = None  # time.time() when on_ready fires
_STALE_THRESHOLD_SECONDS: float = 30.0  # messages older than this are stale


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


def _track_chat_response(message_id: int, response_text: str) -> None:
    """Register an easy-tier chat response for tone feedback tracking.

    When the user reacts with 👍/👎, we record the tone preference.
    """
    _chat_response_messages[message_id] = (response_text or "")[:100]
    if len(_chat_response_messages) > _MAX_CHAT_TRACKED:
        oldest_keys = sorted(_chat_response_messages.keys())[:len(_chat_response_messages) - _MAX_CHAT_TRACKED]
        for k in oldest_keys:
            _chat_response_messages.pop(k, None)


def _record_tone_feedback(message_id: int, emoji: str) -> None:
    """Record tone feedback from a reaction on an easy-tier chat response.

    Stores the sentiment + message snippet in UserModel.tone_feedback.
    """
    snippet = _chat_response_messages.get(message_id)
    if not snippet:
        return
    sentiment = "positive" if emoji in ("👍", "❤️", "🎉", "🔥") else "negative"
    try:
        from src.core.user_model import get_user_model
        get_user_model().add_tone_feedback(sentiment, snippet)
        logger.info("Recorded tone %s on chat response: %s", sentiment, snippet[:50])
    except Exception as e:
        logger.debug("Could not record tone feedback: %s", e)


def _record_reaction_feedback(message_id: int, emoji: str) -> None:
    """Record a reaction on a tracked notification as learning feedback.

    Called from on_raw_reaction_add when the user reacts to a tracked message.
    Uses the heartbeat's shared LearningSystem instance if available,
    otherwise creates a standalone one (which shares the same data file).
    """
    context = _tracked_messages.get(message_id)
    if not context:
        return

    sentiment = "positive" if emoji in ("👍", "❤️", "🎉", "🔥") else "negative"
    goal_desc = context.get("goal", "unknown goal")
    event = context.get("event", "notification")

    try:
        # Prefer the heartbeat's shared instance (avoids stale data)
        ls = None
        if _heartbeat is not None and hasattr(_heartbeat, "learning_system"):
            ls = _heartbeat.learning_system
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
    heartbeat: Optional[Any] = None,
) -> None:
    """Set goal manager, optional shared router, and heartbeat for Discord."""
    global _goal_manager, _router, _heartbeat
    _goal_manager = goal_manager
    if router is not None:
        _router = router
    if heartbeat is not None:
        _heartbeat = heartbeat


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
            "response": (text or "")[:2000],
            "action": "notification",
            "cost_usd": 0,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Failed to log notification: %s", e)


def _truncate(text: str, max_len: int = 1900) -> str:
    """Truncate text for Discord (max 2000 chars per message)."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _build_config_request_note(config_requests: list) -> str:
    """Build a note explaining that a config change request was captured but not applied.

    Appended to the response when the Router detects that the user asked Archi
    to change a protected config/rules/identity file.  Includes the request
    descriptions so the user sees exactly what was captured.
    """
    if len(config_requests) == 1:
        return (
            f"**Heads up:** I've noted your request (\"{config_requests[0]}\"), "
            f"but I can't modify my config files directly (they're protected). "
            f"If you want this change applied, you'll need to edit the file manually."
        )
    items = "; ".join(f"\"{r}\"" for r in config_requests)
    return (
        f"**Heads up:** I've noted your requests ({items}), "
        f"but I can't modify my config files directly (they're protected). "
        f"If you want these changes applied, you'll need to edit the files manually."
    )


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

    Can be called from any thread (heartbeat, agent loop, etc.).
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

    # Suppress notifications during quiet hours (session 88, accumulator session 101).
    # Messages are queued and delivered as a digest when the user next messages.
    if _check_quiet_hours():
        logger.info("Notification queued (quiet hours): %s", (text or "")[:80])
        with _suppressed_lock:
            if len(_suppressed_queue) < _MAX_SUPPRESSED:
                _suppressed_queue.append(text or "")
            elif len(_suppressed_queue) == _MAX_SUPPRESSED:
                _suppressed_queue.append("(...and more — queue full)")
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
        except Exception as e:
            logger.debug("Failed to append notification to chat history: %s", e)

        return True
    except Exception as e:
        logger.warning("Failed to send Discord notification: %s", e)
        return False


def drain_suppressed_notifications() -> int:
    """Send all messages that were queued during quiet hours as a digest.

    Called from on_message when the user sends a message (activity
    override means quiet hours are now lifted).  Batches messages
    into ≤2000-char Discord messages.

    Returns the number of queued messages that were delivered.
    """
    with _suppressed_lock:
        if not _suppressed_queue:
            return 0
        pending = list(_suppressed_queue)
        _suppressed_queue.clear()

    if not _bot_client or not _bot_loop or not _owner_dm_channel:
        return 0

    # Build digest chunks that fit within Discord's 2000-char limit.
    header = f"**While you were away ({len(pending)} queued):**\n"
    chunks: list[str] = []
    current = header

    for i, msg in enumerate(pending, 1):
        # Trim each message to a reasonable summary line
        line = msg.strip().replace("\n", " ")
        if len(line) > 200:
            line = line[:197] + "..."
        entry = f"{i}. {line}\n"

        if len(current) + len(entry) > 1900:
            chunks.append(current)
            current = f"**(continued)**\n{entry}"
        else:
            current += entry

    if current.strip():
        chunks.append(current)

    sent = 0
    for chunk in chunks:
        try:
            import asyncio
            future = asyncio.run_coroutine_threadsafe(
                _owner_dm_channel.send(chunk),
                _bot_loop,
            )
            future.result(timeout=10)
            sent += 1
        except Exception as e:
            logger.warning("Failed to send suppressed digest chunk: %s", e)
            break

    if sent:
        logger.info("Drained %d queued notifications (%d chunks)", len(pending), sent)
        _log_outbound(f"[Digest] Delivered {len(pending)} queued notifications")
    return len(pending)


def is_outbound_ready() -> bool:
    """True if the bot is connected and has a DM channel for outbound messages."""
    return _bot_client is not None and _owner_dm_channel is not None


def kick_heartbeat(goal_id: str, reactive: bool = True) -> None:
    """Submit a goal to the heartbeat's worker pool for immediate execution.

    Public API for other modules that need to kick off background work
    without importing _heartbeat directly.
    """
    if _heartbeat is not None:
        _heartbeat.kick(goal_id=goal_id, reactive=reactive)


# Back-compat alias for legacy code
kick_dream_cycle = kick_heartbeat


def close_bot() -> None:
    """Request graceful Discord bot shutdown.  Safe to call from any thread.

    Signals the bot's event loop via ``request_bot_stop`` (preferred) and
    falls back to scheduling ``bot.close()`` directly.
    """
    request_bot_stop()
    if _bot_client is not None and not _bot_client.is_closed():
        if _bot_loop and _bot_loop.is_running():
            asyncio.run_coroutine_threadsafe(_bot_client.close(), _bot_loop)
        logger.info("Discord bot close requested")


# ──────────────────────────────────────────────────────────────────────
#  Approval flow — shared helpers + source/cleanup variants
# ──────────────────────────────────────────────────────────────────────

def _setup_approval_gate(check_pending: bool = True) -> bool:
    """Initialize the approval gate. Returns False if another approval is pending.

    Sets _pending_approval to a fresh Event, resets _approval_result and
    _approval_message_id. Thread-safe (acquires _approval_lock).
    """
    global _pending_approval, _approval_result, _approval_message_id
    with _approval_lock:
        if check_pending and _pending_approval is not None and not _pending_approval.is_set():
            return False
        _pending_approval = threading.Event()
        _approval_result = False
        _approval_message_id = None
    return True


def _send_embed_or_fallback(msg_id: Optional[int], fallback_msg: str) -> bool:
    """Store embed message ID or send fallback text.

    Returns False only if both embed and fallback fail (caller should abort).
    """
    global _pending_approval, _approval_message_id
    if msg_id:
        with _approval_lock:
            _approval_message_id = msg_id
        return True
    if not send_notification(fallback_msg):
        with _approval_lock:
            _pending_approval = None
            _approval_message_id = None
        return False
    return True


def _collect_approval_result(timeout: float):
    """Wait for approval response and clear gate state.

    Returns (responded: bool, approved: bool).
    """
    global _pending_approval, _approval_message_id
    responded = _pending_approval.wait(timeout=timeout)
    with _approval_lock:
        approved = _approval_result
        _pending_approval = None
        _approval_message_id = None
    return responded, approved


def _send_approval_embed(
    action: str,
    path: str,
    task_description: str,
    timeout: int,
) -> Optional[int]:
    """Send a rich embed for source approval, add ✅/❌ reactions.

    Returns the Discord message ID on success, None on failure.
    """
    import discord

    embed = discord.Embed(
        title=f"Source modification: `{action}`",
        description=task_description[:200],
        color=0xF59E0B,  # amber
    )
    embed.add_field(name="File", value=f"`{path}`", inline=False)
    embed.set_footer(text=f"React ✅ to approve or ❌ to deny (auto-denies in {timeout}s)")

    try:
        async def _send_and_react():
            msg = await _owner_dm_channel.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            return msg.id

        future = asyncio.run_coroutine_threadsafe(_send_and_react(), _bot_loop)
        return future.result(timeout=10)
    except Exception as e:
        logger.warning("Failed to send approval embed: %s", e)
        return None


def _send_cleanup_embed(stale_files: list, file_list: str) -> Optional[int]:
    """Send a rich embed for cleanup approval. Returns message ID or None."""
    try:
        import discord
        embed = discord.Embed(
            title="\U0001f5d1\ufe0f Stale file cleanup proposal",
            description=(
                f"Found {len(stale_files)} files older than 14 days "
                f"with no recent use:"
            ),
            color=0xEF4444,  # red
        )
        embed.add_field(name="Files", value=file_list[:1024], inline=False)
        embed.set_footer(
            text=(
                "React \u2705 to delete all, \u274c to skip. "
                "Or reply \"never <filename>\" to keep one forever."
            )
        )

        async def _send_and_react():
            m = await _owner_dm_channel.send(embed=embed)
            await m.add_reaction("\u2705")
            await m.add_reaction("\u274c")
            return m.id

        future = asyncio.run_coroutine_threadsafe(_send_and_react(), _bot_loop)
        return future.result(timeout=10)
    except Exception as e:
        logger.warning("Failed to send cleanup embed, falling back to text: %s", e)
        return None


def _handle_source_timeout(action: str, path: str, task_description: str) -> None:
    """Record deferred approval and notify user of timeout."""
    import time as _time
    _deferred_approvals[path] = {
        "action": action,
        "task": task_description[:200],
        "ts": _time.time(),
    }
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


def request_source_approval(
    action: str,
    path: str,
    task_description: str,
    timeout: float = 300,
) -> bool:
    """Request user approval for a source code modification via Discord DM.

    Sends a rich embed with ✅/❌ reactions.  The user can either click a
    reaction or type yes/no — both resolve the approval gate.

    Blocks the calling thread until the user responds or the timeout expires.
    Returns True only on explicit approval.

    This is the enforcement mechanism for approval_required_paths in rules.yaml.
    Unlike prompt injection (which asks the *model* to behave), this function
    blocks code execution at the Python level — the modification physically
    cannot proceed without a True return.
    """
    if not is_outbound_ready():
        logger.warning("Discord not ready — denying source modification: %s", path)
        return False

    if not _setup_approval_gate(check_pending=True):
        logger.warning("Another approval already pending — denying: %s", path)
        return False

    msg_id = _send_approval_embed(action, path, task_description, int(timeout))
    fallback = (
        f"Can I {action} `{path}`? "
        f"({task_description[:120]})\n"
        f"Yes or no — auto-denies in {int(timeout)}s."
    )
    if not _send_embed_or_fallback(msg_id, fallback):
        logger.warning("Failed to send approval request — denying: %s", path)
        return False

    responded, result = _collect_approval_result(timeout)
    if not responded:
        logger.info("Approval timed out after %ds — denying: %s", int(timeout), path)
        _handle_source_timeout(action, path, task_description)
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


def _check_quiet_hours() -> bool:
    """Return True if it's quiet hours (caller should skip user interaction)."""
    try:
        from src.utils.time_awareness import is_quiet_hours
        return is_quiet_hours()
    except Exception:
        logger.debug("_check_quiet_hours: time_awareness unavailable")
        return False


def _try_piggyback_question(timeout: float) -> tuple[bool, Optional[str]]:
    """If another task already has a pending question, wait for its answer.

    Returns (handled, result).  If handled is True, the caller should return
    result directly.  If False, the caller should proceed to ask a new question.
    """
    global _pending_question, _question_response

    with _question_lock:
        if _pending_question is not None and not _pending_question.is_set():
            existing_event = _pending_question
        else:
            return False, None

    logger.info("ask_user: piggybacking on existing pending question")
    responded = existing_event.wait(timeout=timeout)
    with _question_lock:
        return True, (_question_response if responded else None)


def _mark_question_answered(question: str) -> None:
    """Update the recent-questions list to record that we got an answer.

    Must be called under _question_lock.
    """
    for i in range(len(_recent_questions) - 1, -1, -1):
        if _recent_questions[i][1] == question:
            _recent_questions[i] = (_recent_questions[i][0], question, True)
            break


def ask_user(
    question: str,
    timeout: float = 300,
) -> Optional[str]:
    """Ask the user a free-form question via Discord DM and wait for their reply.

    Time-aware: returns None immediately if it's quiet hours.
    Cross-goal dedup: skips if a similar question was asked within 10 min.
    Blocks the calling thread until the user replies or timeout expires.
    Safe to call from any worker thread.
    """
    global _pending_question, _question_response

    if _check_quiet_hours():
        logger.info("ask_user: skipping (quiet hours) — %s", question[:80])
        return None

    if not is_outbound_ready():
        logger.warning("ask_user: Discord not ready")
        return None

    with _question_lock:
        if _was_recently_asked(question):
            logger.info("ask_user: skipping (recently asked) — %s", question[:60])
            return None

    # Piggyback on an existing pending question instead of spamming
    handled, result = _try_piggyback_question(timeout)
    if handled:
        return result

    # Set up the question gate (we're the first to ask)
    with _question_lock:
        # Double-check: another thread may have raced us
        if _pending_question is not None and not _pending_question.is_set():
            logger.info("ask_user: race — piggybacking: %s", question[:60])
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

    with _question_lock:
        _recent_questions.append((time.time(), question, False))

    responded = _pending_question.wait(timeout=timeout)

    with _question_lock:
        if not responded:
            logger.info("ask_user: timed out after %ds — %s", int(timeout), question[:60])
            _pending_question = None
            return None
        result = _question_response
        _pending_question = None
        _mark_question_answered(question)

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

    Sends a rich embed with ✅/❌ reactions.  User can also type yes/no
    or "never <filename>".  Blocks until response or timeout.

    Returns "yes", "no", "never:<path>", or "timeout".
    """
    global _cleanup_never_paths

    if not stale_files:
        return "no"
    if not is_outbound_ready():
        logger.warning("Discord not ready — skipping cleanup proposal")
        return "timeout"

    _cleanup_never_paths = []
    _setup_approval_gate(check_pending=False)

    file_list = "\n".join(f"\u2022 `{f}`" for f in stale_files[:15])
    if len(stale_files) > 15:
        file_list += f"\n+ {len(stale_files) - 15} more"

    msg_id = _send_cleanup_embed(stale_files, file_list)
    fallback = (
        f"\U0001f5d1\ufe0f **Stale file cleanup proposal**\n"
        f"Found {len(stale_files)} files older than 14 days with no recent use:\n"
        f"{file_list}\n\n"
        f"Reply:\n"
        f"\u2022 **yes** \u2014 delete all listed files\n"
        f"\u2022 **no** \u2014 skip for now\n"
        f"\u2022 **never `<filename>`** \u2014 keep a specific file forever\n"
        f"No rush \u2014 if you're busy I'll skip this and ask again next time."
    )
    if not _send_embed_or_fallback(msg_id, fallback):
        return "timeout"

    responded, result = _collect_approval_result(timeout)
    if not responded:
        return "timeout"

    with _approval_lock:
        never_paths = list(_cleanup_never_paths)
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
#  Conversation logging helper
# ──────────────────────────────────────────────────────────────────────


def _log_convo(user_msg: str, response: str, action: str, cost: float = 0.0) -> None:
    """Log a user↔Archi exchange to conversations.jsonl."""
    try:
        from src.interfaces.response_builder import log_conversation
        log_conversation("discord", user_msg, response, action, cost)
    except Exception as e:
        logger.warning("Failed to log conversation: %s", e)


async def _handle_config_commands(
    message, content: str,
) -> Tuple[bool, Optional[str]]:
    """Handle Discord-level config commands (model switch, retry, status, etc.).

    Returns (handled, new_content):
        (True, None)         — command handled, caller should return
        (False, None)        — not a config command, continue with original content
        (False, new_content) — retry/switch modified content, continue processing
    """
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
                return (False, _retry_content)
            else:
                if _retry:
                    await message.channel.send("No previous message to retry.")
                _log_convo(content, reply_text, "model_switch")
                return (True, None)
        else:
            await message.reply("Model router not available.")
            _log_convo(content, "Model router not available.", "model_switch")
            return (True, None)

    # ── "try again" / "retry" without model switch ────────────
    if content.lower().strip() in ("try again", "retry", "redo", "redo that"):
        if message.author.id in _last_user_message:
            await message.channel.send("\U0001f504 Retrying your last message...")
            return (False, _last_user_message[message.author.id])
        else:
            await message.reply("No previous message to retry.")
            _log_convo(content, "No previous message to retry.", "retry")
            return (True, None)

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

            # Provider health status
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
            except Exception as e:
                logger.debug("Could not fetch provider health: %s", e)

            _status_reply = (
                f"Currently using: **{info['display']}** (mode: {info['mode']}{_prov_label})"
                f"{_img_info}{_health_info}"
            )
            await message.reply(_status_reply)
            _log_convo(content, _status_reply, "status_query")
        else:
            await message.reply("Model router not available.")
            _log_convo(content, "Model router not available.", "status_query")
        return (True, None)

    # ── Image model switching: "use X for images" ─────────────
    _img_switch = _parse_image_model_switch(content)
    if _img_switch is not None:
        from src.tools.image_gen import set_default_image_model, get_image_model_aliases
        path = set_default_image_model(_img_switch)
        if path:
            from pathlib import Path as _P
            _img_reply = f"Image model set to **{_P(path).stem}**"
            await message.reply(_img_reply)
        else:
            aliases = sorted(set(
                k for k in get_image_model_aliases() if len(k) <= 20
            ))
            _img_reply = (
                f"Unknown image model '{_img_switch}'. "
                f"Available: {', '.join(aliases) if aliases else 'none found'}"
            )
            await message.reply(_img_reply)
        _log_convo(content, _img_reply, "image_model_switch")
        return (True, None)

    # ── Dream cycle interval: "set dream cycle to 15 minutes" ─
    _dc_seconds = _parse_dream_cycle_interval(content)
    if _dc_seconds is not None:
        if _heartbeat is not None:
            _dc_reply = _heartbeat.set_idle_threshold(_dc_seconds)
            await message.reply(_dc_reply)
        else:
            _dc_reply = "Dream cycle not available."
            await message.reply(_dc_reply)
        _log_convo(content, _dc_reply, "dream_cycle_set")
        return (True, None)

    # ── Dream cycle status: "dream cycle?" / "dream status" ───
    _dc_lower = content.lower().strip().rstrip("?!.")
    if _dc_lower in (
        "dream cycle", "dream status", "dream cycle status",
        "dream interval", "what dream cycle", "dream cycle delay",
        "dream delay", "dream timeout", "dream frequency",
        "what is the dream cycle", "what's the dream cycle",
        "what is the dream cycle delay", "what's the dream cycle delay",
    ):
        if _heartbeat is not None:
            secs = _heartbeat.get_idle_threshold()
            mins = secs / 60
            if mins == int(mins):
                _dc_status = f"Dream cycle idle threshold: **{int(mins)} minute{'s' if mins != 1 else ''}** ({secs}s)"
            else:
                _dc_status = f"Dream cycle idle threshold: **{mins:.1f} minutes** ({secs}s)"
            await message.reply(_dc_status)
        else:
            _dc_status = "Dream cycle not available."
            await message.reply(_dc_status)
        _log_convo(content, _dc_status, "dream_cycle_query")
        return (True, None)

    # ── Project management: "add/remove/list projects" ──
    _proj_cmd = _parse_project_command(content)
    if _proj_cmd is not None:
        _proj_action, _proj_name = _proj_cmd
        _proj_reply = _handle_project_command(_proj_action, _proj_name)
        await message.reply(_proj_reply)
        _log_convo(content, _proj_reply, "project_command")
        return (True, None)

    return (False, None)


async def _handle_suggestion_pick(message, content: str, rr) -> bool:
    """Handle suggestion pick intent. Returns True if handled (caller should return)."""
    picks = rr.pick_numbers if rr.pick_numbers else (
        [rr.pick_number] if rr.pick_number > 0 else []
    )
    if not picks:
        return False

    # Find suggestion source (pending first, then recent)
    _sugg_source = None
    if _heartbeat is not None:
        if _heartbeat._pending_suggestions:
            _sugg_source = _heartbeat._pending_suggestions
        elif getattr(_heartbeat, '_recent_suggestions', None):
            _sugg_source = _heartbeat._recent_suggestions[-5:]
            logger.info(
                "Suggestion pick using recent suggestions "
                "(pending was empty, %d recent available)",
                len(_sugg_source),
            )

    if not _sugg_source:
        # Router misclassified an affirmation as suggestion_pick
        logger.info("suggestion_pick with no suggestions available — treating as acknowledgment")
        await message.reply("Got it!")
        _log_convo(content, "Got it!", "suggestion_ack", rr.cost)
        return True

    suggestions = _sugg_source
    valid_picks = [p for p in picks if 1 <= p <= len(suggestions)]

    if not valid_picks:
        _reply = f"Please pick a number between 1 and {len(suggestions)}."
        await message.reply(_reply)
        _log_convo(content, _reply, "suggestion_pick", rr.cost)
        return True

    # Clear pending suggestions
    _heartbeat._pending_suggestions = []

    # Record in idea history
    try:
        from src.core.idea_history import get_idea_history
        hist = get_idea_history()
        for p in valid_picks:
            hist.record_accepted(suggestions[p - 1].get("description", ""))
        batch_id = getattr(_heartbeat, '_pending_batch_id', None)
        if batch_id:
            hist.mark_batch_ignored(batch_id)
            _heartbeat._pending_batch_id = None
    except Exception as e:
        logger.warning("Failed to record idea history: %s", e)

    # Create goals for each pick
    _reply = "Got it!"
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
                if goal is None:
                    continue  # duplicate of existing goal
                _heartbeat.kick(goal_id=goal.goal_id, reactive=True)
                created.append((p, desc[:60], goal.goal_id))
            if len(created) == 1:
                _reply = "On it — planning the approach now. I'll message you when it's done."
            elif created:
                _reply = f"On it — working on {len(created)} tasks. I'll message you when they're done."
            await message.reply(_reply)
            for p, desc_short, gid in created:
                logger.info("User picked suggestion #%d: %s -> %s", p, desc_short, gid)
        else:
            _reply = "Goal manager not available."
            await message.reply(_reply)
    except Exception as e:
        logger.error("Failed to create goal from suggestion: %s", e)
        _reply = f"Error creating goal: {e}"
        await message.reply(_reply)
    _log_convo(content, _reply, "suggestion_pick", rr.cost)
    return True


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
) -> Tuple[str, str, float]:
    """
    Process an image through Archi's vision model (blocking).

    Returns:
        (full_response, truncated_for_discord, cost_usd)
    """
    router = _get_router()
    if not router:
        msg = "Archi vision is not available."
        return msg, _truncate(msg), 0.0

    # Auto-escalate to Claude Haiku for vision tasks (Grok has no vision)
    _auto_escalated = False
    try:
        _model_info = router.get_active_model_info()
        _current = (_model_info.get("model") or "").lower()
        if "claude" not in _current:
            router.switch_model_temp("claude-haiku", count=1)
            _auto_escalated = True
            logger.info("Auto-escalated to Claude Haiku for image analysis")
    except Exception as e:
        logger.debug("Could not auto-escalate for image analysis: %s", e)

    result = router.chat_with_image(text_prompt, image_path)
    cost = result.get("cost_usd", 0)
    text = result.get("text", "").strip()
    if not text:
        text = f"I couldn't analyze the image: {result.get('error', 'unknown error')}"

    # Revert auto-escalation
    if _auto_escalated:
        try:
            router.complete_temp_task()
        except Exception as e:
            logger.debug("Could not revert auto-escalation: %s", e)

    out = text
    return out, _truncate(out), cost


# Cancel keywords that stop a running multi-step task
_CANCEL_EXACT = {"stop", "cancel", "nevermind", "never mind", "abort", "quit", "halt"}
_CANCEL_PHRASES = ("stop that", "cancel that", "stop working", "cancel task",
                   "never mind", "nevermind", "forget it", "forget that",
                   "stop the task", "cancel the task", "abort task")



def _get_goal_manager():
    """Return the goal_manager from the heartbeat instance, if available."""
    if _heartbeat is not None:
        return _heartbeat.goal_manager
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
        "switch back to auto"                    -> reset to auto (word "back" allowed)
        "go back to auto"                        -> reset to auto
        "reset model"                            -> reset to auto

    Returns (model_name, should_retry, temp_count) or None if not a switch command.
    temp_count=0 means permanent, >0 means temporary for N messages.
    Adding "direct" after the model name appends "-direct" to the alias,
    which routes to the provider's own API instead of OpenRouter.
    """
    import re
    lower = content.lower().strip()

    # Quick check: "go back to auto", "reset model", "default model" → auto
    if re.match(r"(?:go\s+back\s+to\s+(?:auto|default|normal)|reset\s+(?:the\s+)?model|default\s+model)", lower):
        return ("auto", False, 0)

    # Pattern: "switch to <model>" with optional "back", "direct", duration, and retry
    match = re.match(
        r"(?:switch\s+(?:back\s+)?to|use|change\s+(?:back\s+)?to|swap\s+(?:back\s+)?to|set\s+model\s+to?)\s+"
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


def _parse_project_command(content: str) -> Optional[tuple]:
    """Parse project management commands. Returns (action, name) or None.

    Recognizes patterns like:
        "add project health tracker"
        "remove project health_tracker"
        "list projects" / "show projects" / "what projects"
        "can you add a project called meal planner?"
        "drop the health tracker project"
    """
    import re
    lower = content.lower().strip().rstrip("?!.")

    # Quick check: must mention "project" to avoid false positives
    if "project" not in lower:
        return None

    # Strip polite prefixes
    lower = re.sub(
        r"^(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?",
        "", lower,
    ).strip()

    # List/show patterns
    if re.match(r"(?:list|show|what(?:'s|\s+are)?)\s+(?:my\s+|the\s+|active\s+)?projects?$", lower):
        return ("list", None)

    # Add patterns: "add (a) project (called/named)? <name>"
    match = re.search(
        r"(?:add|create|new|start)\s+(?:a\s+)?project\s+(?:called\s+|named\s+)?(.+)",
        lower,
    )
    if match:
        name = match.group(1).strip().strip('"\'')
        if name:
            return ("add", name)

    # Remove patterns: "remove/delete/drop (the)? project <name>"
    match = re.search(
        r"(?:remove|delete|drop|deactivate)\s+(?:the\s+)?project\s+(.+)",
        lower,
    )
    if match:
        name = match.group(1).strip().strip('"\'')
        if name:
            return ("remove", name)

    # Reverse remove: "remove/delete/drop (the)? <name> project"
    match = re.search(
        r"(?:remove|delete|drop|deactivate)\s+(?:the\s+)?(.+?)\s+project",
        lower,
    )
    if match:
        name = match.group(1).strip().strip('"\'')
        if name:
            return ("remove", name)

    return None


def _handle_project_command(action: str, name: Optional[str]) -> str:
    """Execute a project management command. Returns response message."""
    from src.utils import project_context

    ctx = project_context.load()
    projects = ctx.get("active_projects", {})

    if action == "list":
        if not projects:
            return "No active projects. You can add one with \"add project <name>\"."
        lines = []
        for key, info in sorted(projects.items()):
            desc = info.get("description", key)
            prio = info.get("priority", "medium")
            lines.append(f"• **{key}** ({prio}) — {desc}")
        return "Active projects:\n" + "\n".join(lines)

    if action == "add":
        key = name.lower().replace(" ", "_").replace("-", "_")
        if key in projects:
            return f"Project **{key}** already exists."
        projects[key] = {
            "path": f"workspace/projects/{name.replace(' ', '_')}",
            "description": name,
            "priority": "medium",
            "focus_areas": [],
            "autonomous_tasks": [
                f"Read existing files in workspace/projects/{name.replace(' ', '_')} and identify what to build next",
            ],
        }
        ctx["active_projects"] = projects
        if project_context.save(ctx):
            return f"Added project **{key}**. I'll start looking for work on it in the next heartbeat."
        return f"Failed to save project **{key}** — check the logs."

    if action == "remove":
        key = name.lower().replace(" ", "_").replace("-", "_")
        # Try exact match first, then fuzzy
        if key not in projects:
            # Try substring match
            matches = [k for k in projects if key in k or k in key]
            if len(matches) == 1:
                key = matches[0]
            elif len(matches) > 1:
                return f"Multiple matches: {', '.join(matches)}. Be more specific."
            else:
                return f"No project matching **{key}**. Current projects: {', '.join(sorted(projects)) or '(none)'}."
        del projects[key]
        ctx["active_projects"] = projects
        if project_context.save(ctx):
            return f"Removed project **{key}**."
        return f"Failed to remove project **{key}** — check the logs."

    return "Unknown project command."


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
            global _bot_client, _bot_loop, _owner_id, _ready_at
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

            # Load recent DM history as conversational context for Archi.
            # This gives the model awareness of what was discussed before
            # the restart, without re-processing any of those messages.
            await self._load_startup_context()

            # Mark the bot as ready — on_message uses this timestamp to
            # skip any stale gateway messages replayed during connect.
            _ready_at = time.time()
            logger.info("Now accepting messages (ready_at=%.1f)", _ready_at)

            # Notify user about any interrupted tasks recovered from crash
            await _notify_interrupted_tasks()

        async def _load_startup_context(self):
            """Load recent DM history into chat_history for conversational context.

            On startup, reads the last few messages from the DM channel
            so Archi knows what was being discussed before the restart.
            Only loads messages not already present in chat_history.
            """
            if _owner_dm_channel is None:
                return
            try:
                from src.interfaces.chat_history import load, save

                existing = load()
                # If we already have recent context, skip the fetch
                if existing:
                    logger.info(
                        "Chat history already has %d messages, skipping Discord backfill",
                        len(existing),
                    )
                    return

                # Fetch last 10 messages (oldest first) for context
                messages = []
                async for msg in _owner_dm_channel.history(limit=10, oldest_first=False):
                    role = "assistant" if msg.author.id == self.user.id else "user"
                    content = (msg.content or "").strip()
                    if content:
                        messages.append({
                            "role": role,
                            "content": content,
                            "ts": msg.created_at.timestamp(),
                        })

                if messages:
                    # history() returns newest-first, reverse for chronological order
                    messages.reverse()
                    save(messages)
                    logger.info(
                        "Loaded %d messages from DM history as startup context",
                        len(messages),
                    )
            except Exception as e:
                logger.warning("Failed to load startup context from DM history: %s", e)

        async def _handle_purge(self, message):
            """Delete ALL old messages from this DM channel.

            Bots can only delete their OWN messages in DMs (Discord API
            limitation). User messages are listed so the owner can delete
            them manually.

            Loops through the ENTIRE history (no limit) to ensure old
            test messages buried deep in the DM are cleaned up.
            """
            import discord as _disc

            channel = message.channel
            # Remember IDs of messages we just sent so we don't delete them
            purge_cmd_id = message.id
            status_msg = await message.reply(
                "Purging all bot messages from DM history... this may take a moment."
            )
            status_id = status_msg.id if status_msg else None

            bot_deleted = 0
            user_msg_count = 0
            errors = 0
            batch = 0

            try:
                # No limit — walk the entire DM history
                async for old_msg in channel.history(limit=None, oldest_first=True):
                    # Don't delete the /purge command or our status message
                    if old_msg.id in (purge_cmd_id, status_id):
                        continue
                    if old_msg.author.id == self.user.id:
                        # Bot's own message — delete it
                        try:
                            await old_msg.delete()
                            bot_deleted += 1
                            batch += 1
                            # Respect Discord rate limits: brief pause every
                            # message, with a longer breather every 25 deletes
                            if batch >= 25:
                                await asyncio.sleep(2)
                                batch = 0
                            else:
                                await asyncio.sleep(0.3)
                        except _disc.Forbidden:
                            errors += 1
                        except _disc.NotFound:
                            pass  # already gone
                        except _disc.HTTPException as e:
                            if e.status == 429:  # rate limited
                                retry_after = getattr(e, 'retry_after', 5)
                                await asyncio.sleep(retry_after)
                                try:
                                    await old_msg.delete()
                                    bot_deleted += 1
                                except Exception as e:
                                    logger.debug("Purge retry delete failed: %s", e)
                                    errors += 1
                            else:
                                errors += 1
                    else:
                        user_msg_count += 1

                # Summary
                parts = [f"Done! Deleted **{bot_deleted}** bot messages."]
                if errors:
                    parts.append(f"({errors} failed)")
                if user_msg_count > 0:
                    parts.append(
                        f"\n**{user_msg_count} of your messages** remain "
                        f"(Discord doesn't let bots delete other users' DMs). "
                        f"You can select and delete them manually if needed."
                    )
                else:
                    parts.append("DM channel is clean!")

                await channel.send("\n".join(parts))
                logger.info(
                    "/purge complete: deleted %d bot messages, %d user messages remain, %d errors",
                    bot_deleted, user_msg_count, errors,
                )
            except Exception as e:
                logger.error("/purge failed: %s", e)
                await channel.send(f"Purge failed after deleting {bot_deleted} messages: {e}")

        async def on_raw_reaction_add(self, payload):
            """Handle reactions on tracked notification messages.

            When the user reacts with 👍/👎 (or similar) on a completion message,
            record the feedback via the learning system.  Also handles ✅/❌
            reactions on approval embeds.
            """
            # Ignore bot's own reactions
            if payload.user_id == self.user.id:
                return
            # Only process reactions from the owner
            if _owner_id is not None and payload.user_id != _owner_id:
                return

            emoji_str = str(payload.emoji)

            # ── Approval reaction (✅/❌ on an approval embed) ──────────
            if emoji_str in ("✅", "❌"):
                is_approval_msg = False
                with _approval_lock:
                    is_approval_msg = (
                        _approval_message_id is not None
                        and payload.message_id == _approval_message_id
                    )
                if is_approval_msg:
                    _resolve_approval(emoji_str == "✅")
                    return

            # Check if this message is tracked for feedback
            _FEEDBACK_EMOJIS = {"👍", "👎", "❤️", "🎉", "🔥", "😕", "😞"}
            if emoji_str in _FEEDBACK_EMOJIS:
                if payload.message_id in _tracked_messages:
                    _record_reaction_feedback(payload.message_id, emoji_str)
                if payload.message_id in _chat_response_messages:
                    _record_tone_feedback(payload.message_id, emoji_str)

        async def on_message(self, message):
            if not _should_respond(message, self.user.id):
                return

            # ── Startup guard: skip stale messages ─────────────────
            # Discord's gateway may replay recent DM messages on connect.
            # Skip anything created before the bot was ready, or if the
            # bot hasn't finished starting up yet.
            if _ready_at is None:
                logger.debug(
                    "Ignoring message before bot ready: %s",
                    (message.content or "")[:60],
                )
                return
            message_age = time.time() - message.created_at.timestamp()
            if message_age > _STALE_THRESHOLD_SECONDS:
                logger.debug(
                    "Skipping stale message (%.0fs old): %s",
                    message_age, (message.content or "")[:60],
                )
                return

            # Auto-discover owner from first DM (if not set via env var)
            global _owner_id, _owner_dm_channel
            if _owner_id is None and message.guild is None:
                _owner_id = message.author.id
                _owner_dm_channel = message.channel
                logger.info("Discord owner auto-discovered: %s (ID: %d)",
                            message.author.name, _owner_id)
                _persist_owner_id(_owner_id)

            # Reset heartbeat idle timer so heartbeats don't run mid-conversation
            if _heartbeat is not None:
                _heartbeat.mark_activity()
                _heartbeat.reset_suggest_cooldown()

            # Record activity so quiet hours are suppressed while chatting,
            # then drain any notifications that queued up during quiet hours.
            try:
                from src.utils.time_awareness import record_user_activity
                record_user_activity()
            except Exception as e:
                logger.debug("Could not record user activity: %s", e)
            try:
                drain_suppressed_notifications()
            except Exception as e:
                logger.debug("Could not drain suppressed notifications: %s", e)

            content = _get_content(message, self.user.id)

            # ── Reply context: if the user replied to a specific message,
            # extract that message's content so the model knows what topic
            # the user is responding to (prevents context confusion when
            # multiple notifications are sent in quick succession).
            _reply_context = await _extract_reply_context(message)

            # ── Discord-level fast-paths (no model call, no Router) ───
            # These stay in discord_bot.py because they're Discord-specific
            # commands that don't need classification.

            # ── /purge: delete old messages from this DM channel ───
            if content.lower().strip() in ("/purge", "/clear", "/cleanup"):
                await self._handle_purge(message)
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
                    _reply = (
                        f"\u2705 Got it — `{_deferred_path}` is now pre-approved. "
                        f"Archi will use this approval next time it needs to modify that file."
                    )
                    await message.reply(_reply)
                    _log_convo(content, _reply, "deferred_approval")
                    return
                else:
                    _reply = (
                        f"No pending approval found for `{_deferred_path}`. "
                        f"Currently waiting: {list(_deferred_approvals.keys()) or 'none'}"
                    )
                    await message.reply(_reply)
                    _log_convo(content, _reply, "deferred_approval")
                    return

            # ── Config commands (model switch, retry, status, etc.) ──
            _cfg_handled, _cfg_content = await _handle_config_commands(message, content)
            if _cfg_handled:
                return
            if _cfg_content is not None:
                content = _cfg_content  # retry/switch modified content

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
                        full_response, response, vision_cost = await asyncio.to_thread(
                            process_image_with_archi, text_prompt, image_path
                        )
                        await message.reply(response)
                        try:
                            append("user", f"[Image attached] {content}")
                            append("assistant", full_response)
                        except Exception as e:
                            logger.debug("Failed to save vision chat history: %s", e)
                        _log_convo(f"[Image attached] {content}", full_response, "vision", vision_cost)
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
                    if _heartbeat is not None:
                        if hasattr(_heartbeat, '_pending_suggestions'):
                            _pending_suggs = [
                                s.get("description", "") for s in (_heartbeat._pending_suggestions or [])
                            ]
                        if hasattr(_heartbeat, '_recent_suggestions') and not _pending_suggs:
                            _recent_suggs = [
                                s.get("description", "") for s in (_heartbeat._recent_suggestions or [])
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
                    _memory = _heartbeat.memory if _heartbeat is not None else None
                    rr = await asyncio.to_thread(
                        router_route, content, router, ctx, history_messages,
                        _goal_manager, _memory,
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
                            _reply = (
                                "Got it — cancelling the current task. "
                                "I'll wrap up after the current step finishes."
                            )
                        except ImportError:
                            _reply = "Cancellation not available."
                        await message.reply(_reply)
                        _log_convo(content, _reply, "cancel", rr.cost)
                        return

                    # Suggestion pick (single or multi)
                    if rr.intent == "suggestion_pick":
                        if await _handle_suggestion_pick(message, content, rr):
                            return

                    # If suggestions were pending but user didn't pick any,
                    # record them as ignored so future brainstorms avoid them.
                    if (rr.intent != "suggestion_pick"
                            and _heartbeat is not None
                            and getattr(_heartbeat, '_pending_suggestions', None)):
                        try:
                            from src.core.idea_history import get_idea_history
                            batch_id = getattr(_heartbeat, '_pending_batch_id', None)
                            if batch_id:
                                get_idea_history().mark_batch_ignored(batch_id)
                                _heartbeat._pending_batch_id = None
                            _heartbeat._pending_suggestions = []
                            logger.info("Pending suggestions dismissed (user moved on)")
                        except Exception as e:
                            logger.debug("Failed to dismiss pending suggestions: %s", e)

                    # Approval response
                    if rr.intent == "approval" and rr.approval is not None:
                        _resolve_approval(rr.approval, content)
                        _reply = (
                            "\u2705 Approved. Proceeding with modification." if rr.approval
                            else "\u274c Denied. Modification skipped."
                        )
                        await message.reply(_reply)
                        _log_convo(content, _reply, "approval", rr.cost)
                        return

                    # Question reply
                    if rr.intent == "question_reply":
                        _resolve_question_reply(content)
                        await message.reply("\U0001f44d Got it, thanks!")
                        _log_convo(content, "Got it, thanks!", "question_reply", rr.cost)
                        return

                    # Easy tier: Router already generated the answer
                    if rr.tier == "easy" and rr.answer and not rr.action:
                        response = rr.answer
                        # If config change requests were detected, append
                        # a note so the user knows the file wasn't modified
                        if rr.config_requests:
                            note = _build_config_request_note(rr.config_requests)
                            response = f"{response}\n\n{note}"
                        response = _truncate(response)
                        sent_msg = await message.reply(response)
                        # Track for tone feedback via reactions
                        if sent_msg:
                            _track_chat_response(sent_msg.id, rr.answer)
                        try:
                            append("user", content)
                            append("assistant", response)
                        except Exception as e:
                            logger.debug("Failed to save easy-tier chat history: %s", e)
                        _log_convo(content, response, rr.intent, rr.cost)
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
                        except Exception as e:
                            logger.debug("Progress callback send failed: %s", e)

                    full_response, response, actions_taken = await asyncio.to_thread(
                        process_with_archi, content, history, _progress_callback, rr
                    )

                    # Clean up the progress message
                    if _status_ref[0] is not None:
                        try:
                            await _status_ref[0].delete()
                        except Exception as e:
                            logger.debug("Failed to delete progress message: %s", e)

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

                    # Append config request note for complex-tier too
                    if rr.config_requests:
                        note = _build_config_request_note(rr.config_requests)
                        response = f"{response}\n\n{note}"
                        response = _truncate(response)

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


_bot_stop_event: Optional[asyncio.Event] = None


def request_bot_stop() -> None:
    """Thread-safe request to stop the Discord bot's event loop.

    Sets the asyncio Event that ``run_bot`` monitors, causing it to
    initiate a clean ``bot.close()`` and return.  Safe to call from any
    thread (uses ``call_soon_threadsafe``).
    """
    if _bot_stop_event and _bot_loop and _bot_loop.is_running():
        _bot_loop.call_soon_threadsafe(_bot_stop_event.set)


def run_bot(token: Optional[str] = None) -> None:
    """Run the Discord bot (blocking).

    Uses ``bot.start()`` inside an explicit asyncio loop instead of
    ``bot.run()`` so that the main thread's signal handlers remain in
    control.  The loop watches ``_bot_stop_event`` and initiates a
    graceful ``bot.close()`` when set.
    """
    global _bot_stop_event

    token = token or os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError(
            "DISCORD_BOT_TOKEN not set. Create a bot at https://discord.com/developers/applications "
            "and add the token to .env"
        )

    bot = create_bot()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _bot_stop_event = asyncio.Event()

    async def _run() -> None:
        stop_task = asyncio.create_task(_bot_stop_event.wait())
        bot_task = asyncio.create_task(bot.start(token))
        # Wait for either the bot to exit on its own or a stop request.
        done, pending = await asyncio.wait(
            {stop_task, bot_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if not bot.is_closed():
            try:
                await asyncio.wait_for(bot.close(), timeout=5)
            except Exception as e:
                logger.debug("Bot close during shutdown: %s", e)

    try:
        loop.run_until_complete(_run())
    except Exception as e:
        logger.warning("Discord bot loop error: %s", e)
    finally:
        # Cancel all remaining tasks (aiohttp connector close, etc.)
        # to prevent "Task was destroyed but it is pending!" spam
        # during interpreter shutdown.
        try:
            pending = asyncio.all_tasks(loop)
            if pending:
                for t in pending:
                    t.cancel()
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except Exception:
            pass
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
