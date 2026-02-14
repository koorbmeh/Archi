"""
Discord Bot Interface - Chat with Archi from Discord.

Listens to DMs and @mentions, sends messages to Archi via action_executor.
Supports text messages and image attachments (analyzed via vision model).

Outbound messaging: other components (dream cycle, agent loop) can call
send_notification(text) to proactively message the owner via DM.
"""

import asyncio
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, List, Optional, Tuple

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
            import src.core.cuda_bootstrap  # noqa: F401
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

def send_notification(text: str) -> bool:
    """
    Send a proactive message to the owner via Discord DM.

    Can be called from any thread (dream cycle, agent loop, etc.).
    Returns True if the message was queued successfully.

    Usage:
        from src.interfaces.discord_bot import send_notification
        send_notification("I finished working on your Health Optimization goal.")
    """
    global _bot_client, _bot_loop, _owner_dm_channel

    if not _bot_client or not _bot_loop or not _owner_dm_channel:
        logger.debug("Discord outbound not ready (bot=%s, loop=%s, dm=%s)",
                      _bot_client is not None, _bot_loop is not None,
                      _owner_dm_channel is not None)
        return False

    truncated = _truncate(text)

    try:
        future = asyncio.run_coroutine_threadsafe(
            _owner_dm_channel.send(truncated),
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
        f"\U0001f512 **Source modification approval needed**\n"
        f"**Action:** `{action}`\n"
        f"**File:** `{path}`\n"
        f"**Task:** {task_description[:200]}\n\n"
        f"Reply **yes** to approve or **no** to deny. "
        f"(Auto-denies in {int(timeout)}s)"
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
        send_notification(f"\u23f0 Approval timed out for `{path}`. Modification skipped.")
        return False

    logger.info("Source approval for %s: %s", path, "APPROVED" if result else "DENIED")
    return result


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

    from src.interfaces.action_executor import process_message

    response_text, actions_taken, cost = process_message(
        message, router, history=history, source="discord",
        goal_manager=_goal_manager, progress_callback=progress_callback,
    )

    out = response_text
    if actions_taken:
        action_lines = "\n".join(f"\u2022 {a.get('description', 'Done')}" for a in actions_taken)
        out = f"{out}\n\n{action_lines}"
    if cost > 0:
        out = f"{out}\n\n(Cost: ${cost:.4f})"

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

    result = router.chat_with_image(text_prompt, image_path)
    cost = result.get("cost_usd", 0)
    text = result.get("text", "").strip()
    if not text:
        text = f"I couldn't analyze the image: {result.get('error', 'unknown error')}"

    out = text
    if cost > 0:
        out = f"{out}\n\n(Cost: ${cost:.4f})"

    return out, _truncate(out)


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
            print(f"Archi Discord bot ready: {self.user}")

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

                            progress_line = f"\u23f3 Step {step_num}/{max_steps}: {status_text}"

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

                    # Check if actions include a generated image → send as attachment
                    media_sent = False
                    for act in actions_taken:
                        desc = act.get("description", "")
                        if desc.startswith("Generated image:"):
                            gen_path = act.get("result", {}).get("image_path", "")
                            if gen_path and os.path.isfile(gen_path):
                                try:
                                    img_file = discord.File(
                                        gen_path, filename=os.path.basename(gen_path),
                                    )
                                    await message.reply(response, file=img_file)
                                    media_sent = True
                                    logger.info("Sent generated image to Discord: %s", gen_path)
                                except Exception as e:
                                    logger.warning("Failed to attach image: %s", e)
                            break

                    if not media_sent:
                        await message.reply(response)

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
