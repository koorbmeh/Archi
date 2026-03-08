"""Telegram bot interface for Archi — second communication channel.

Session 246: Mirrors Discord's message processing via Telegram.
Reuses conversational_router + action_dispatcher for all logic.

Env vars:
    TELEGRAM_BOT_TOKEN  — from @BotFather
    TELEGRAM_OWNER_ID   — numeric user ID (auto-discovered on first DM)

Usage:
    from src.interfaces.telegram_bot import start_telegram_bot, send_telegram_notification
    start_telegram_bot()  # non-blocking, runs in background thread
    send_telegram_notification("Hello from Archi!")
"""

import asyncio
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Module state ─────────────────────────────────────────────────────

_bot_app = None          # telegram.ext.Application
_bot_instance = None     # telegram.Bot
_bot_loop = None         # asyncio event loop running the bot
_owner_id: Optional[int] = None
_ready = False
_chat_history: list = []  # simple in-memory history for router context
_MAX_HISTORY = 20


def is_configured() -> bool:
    """Check if Telegram bot token is set."""
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())


def is_ready() -> bool:
    """Check if the Telegram bot is running and ready."""
    return _ready and _bot_instance is not None


# ── Message processing ───────────────────────────────────────────────

def _process_message(text: str) -> str:
    """Route a message through Archi's conversational router + dispatcher.

    This mirrors the Discord bot's message processing flow but without
    Discord-specific features (reactions, approvals, suggestion picks).
    """
    try:
        from src.core.conversational_router import (
            route as router_route,
            ContextState,
        )
        from src.interfaces.action_dispatcher import dispatch
        from src.interfaces.message_handler import _build_history_messages
        from src.interfaces.discord_bot import process_with_archi

        # Build router context
        history_messages = _build_history_messages(_chat_history[-_MAX_HISTORY:])
        ctx = ContextState(
            pending_suggestions=[],
            recent_suggestions=[],
            pending_approval=False,
            pending_question=False,
        )

        # Get router instance
        from src.interfaces.discord_bot import _get_router
        router = _get_router()
        if not router:
            return "Archi is not available. Check model configuration."

        # Get optional dependencies
        _heartbeat = None
        _goal_manager = None
        _memory = None
        try:
            from src.interfaces.discord_bot import _heartbeat as hb, _goal_manager as gm
            _heartbeat = hb
            _goal_manager = gm
            if _heartbeat:
                _memory = getattr(_heartbeat, 'memory', None)
        except (ImportError, AttributeError):
            pass

        # Route the message
        rr = router_route(text, router, ctx, history_messages, _goal_manager, _memory)
        logger.info(
            "Telegram Router: intent=%s tier=%s action=%s cost=$%.4f",
            rr.intent, rr.tier, rr.action, rr.cost,
        )

        # Easy tier with answer and no action → return directly
        if rr.tier == "easy" and rr.answer and not rr.action:
            _append_history("user", text)
            _append_history("assistant", rr.answer)
            return rr.answer

        # Easy tier with action → dispatch
        if rr.action:
            action_context = {
                "router": router,
                "system_prompt": "",
                "effective_message": text,
                "history_messages": history_messages,
                "source": "telegram",
            }
            response, actions, cost = dispatch(rr.action, rr.action_params or {}, action_context)
            _append_history("user", text)
            _append_history("assistant", response)
            return response

        # Complex tier → full processing
        full_response, response, actions = process_with_archi(
            text, _chat_history[-_MAX_HISTORY:], None, rr,
        )
        _append_history("user", text)
        _append_history("assistant", response or full_response)
        return response or full_response or "I processed that but have nothing to report."

    except Exception as e:
        logger.error("Telegram message processing failed: %s", e)
        return f"Sorry, something went wrong: {e}"


def _append_history(role: str, content: str) -> None:
    """Append to in-memory chat history."""
    _chat_history.append({"role": role, "content": content})
    while len(_chat_history) > _MAX_HISTORY * 2:
        _chat_history.pop(0)


# ── Telegram handlers ────────────────────────────────────────────────

async def _handle_start(update, context) -> None:
    """Handle /start command."""
    global _owner_id
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Auto-discover owner on first interaction
    if _owner_id is None:
        _owner_id = user.id
        os.environ["TELEGRAM_OWNER_ID"] = str(_owner_id)
        logger.info("Telegram owner auto-discovered: %s (ID: %d)", user.first_name, _owner_id)

    await update.message.reply_text(
        f"Hey {user.first_name}! I'm Archi, your autonomous AI agent. "
        f"Send me a message and I'll help you out.\n\n"
        f"I can do everything I do on Discord — search, create content, "
        f"track supplements, manage finances, and more."
    )


async def _handle_help(update, context) -> None:
    """Handle /help command."""
    if not _is_owner(update):
        return

    await update.message.reply_text(
        "**What I can do:**\n"
        "• Search the web and research topics\n"
        "• Create and publish content (blog, social media)\n"
        "• Track supplements and finances\n"
        "• Check your calendar and email\n"
        "• Generate images (when SDXL is available)\n"
        "• Answer questions and have conversations\n\n"
        "Just message me naturally — no special commands needed.\n\n"
        "/status — Check my current status\n"
        "/help — This message",
        parse_mode="Markdown",
    )


async def _handle_status(update, context) -> None:
    """Handle /status command."""
    if not _is_owner(update):
        return

    lines = ["**Archi Status:**"]
    try:
        from src.interfaces.discord_bot import _heartbeat
        if _heartbeat:
            cycle_count = len(getattr(_heartbeat, 'cycle_history', []))
            lines.append(f"• Dream cycles completed: {cycle_count}")
            is_dreaming = getattr(_heartbeat, 'is_dreaming', False)
            lines.append(f"• Mode: {'dreaming' if is_dreaming else 'awake'}")
    except (ImportError, AttributeError):
        lines.append("• Heartbeat: not available")

    lines.append(f"• Telegram: connected")
    lines.append(f"• Owner ID: {_owner_id}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _handle_message(update, context) -> None:
    """Handle regular text messages."""
    if not _is_owner(update):
        await update.message.reply_text("Sorry, I only respond to my owner.")
        return

    text = update.message.text
    if not text:
        return

    # Mark activity on heartbeat
    try:
        from src.interfaces.discord_bot import _heartbeat
        if _heartbeat:
            _heartbeat.mark_activity()
    except (ImportError, AttributeError):
        pass

    # Send typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Process in thread pool to avoid blocking the event loop
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, _process_message, text)

    # Telegram has a 4096 char limit per message
    if len(response) > 4000:
        # Split into chunks
        for i in range(0, len(response), 4000):
            chunk = response[i:i + 4000]
            await update.message.reply_text(chunk)
    else:
        await update.message.reply_text(response)


def _is_owner(update) -> bool:
    """Check if the message is from the configured owner."""
    global _owner_id
    if _owner_id is None:
        # Auto-discover on first message
        _owner_id = update.effective_user.id
        os.environ["TELEGRAM_OWNER_ID"] = str(_owner_id)
        logger.info("Telegram owner auto-discovered: %s (ID: %d)",
                     update.effective_user.first_name, _owner_id)
        return True

    return update.effective_user.id == _owner_id


# ── Proactive notifications ──────────────────────────────────────────

def send_telegram_notification(text: str) -> bool:
    """Send a proactive message to the owner via Telegram.

    Can be called from any thread (heartbeat, agent loop, etc.).
    Returns True if the message was sent.
    """
    global _bot_instance, _bot_loop, _owner_id

    if not _bot_instance or not _bot_loop or not _owner_id:
        logger.debug("Telegram outbound not ready")
        return False

    if not text or not text.strip():
        return False

    # Truncate very long messages
    if len(text) > 4000:
        text = text[:3990] + "\n...(truncated)"

    try:
        future = asyncio.run_coroutine_threadsafe(
            _bot_instance.send_message(chat_id=_owner_id, text=text),
            _bot_loop,
        )
        future.result(timeout=10)
        logger.debug("Telegram notification sent: %s", text[:60])
        return True
    except Exception as e:
        logger.warning("Failed to send Telegram notification: %s", e)
        return False


# ── Bot lifecycle ────────────────────────────────────────────────────

def _run_bot(token: str) -> None:
    """Run the Telegram bot in its own event loop (background thread)."""
    global _bot_app, _bot_instance, _bot_loop, _ready

    try:
        from telegram import Update
        from telegram.ext import (
            ApplicationBuilder,
            CommandHandler,
            MessageHandler,
            filters,
        )
    except ImportError:
        logger.warning(
            "python-telegram-bot not installed. "
            "Install with: pip install python-telegram-bot"
        )
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _bot_loop = loop

    try:
        app = ApplicationBuilder().token(token).build()
        _bot_app = app
        _bot_instance = app.bot

        # Register handlers
        app.add_handler(CommandHandler("start", _handle_start))
        app.add_handler(CommandHandler("help", _handle_help))
        app.add_handler(CommandHandler("status", _handle_status))
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            _handle_message,
        ))

        _ready = True
        logger.info("Telegram bot started and ready for messages")

        # This blocks until the bot is stopped
        app.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error("Telegram bot crashed: %s", e)
    finally:
        _ready = False
        _bot_loop = None


def start_telegram_bot() -> Optional[threading.Thread]:
    """Start the Telegram bot in a background thread.

    Returns the thread if started, None if not configured or already running.
    Non-blocking — the bot runs in its own thread with its own event loop.
    """
    global _owner_id, _ready

    if _ready:
        logger.debug("Telegram bot already running")
        return None

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.debug("TELEGRAM_BOT_TOKEN not set, skipping Telegram bot")
        return None

    # Load owner ID from env if available
    owner_str = os.environ.get("TELEGRAM_OWNER_ID", "").strip()
    if owner_str:
        try:
            _owner_id = int(owner_str)
        except ValueError:
            pass

    thread = threading.Thread(target=_run_bot, args=(token,), daemon=True, name="telegram-bot")
    thread.start()
    logger.info("Telegram bot thread started")
    return thread


def stop_telegram_bot() -> None:
    """Stop the Telegram bot gracefully."""
    global _bot_app, _ready
    _ready = False
    if _bot_app and _bot_loop:
        try:
            asyncio.run_coroutine_threadsafe(_bot_app.stop(), _bot_loop)
        except Exception as e:
            logger.debug("Telegram bot stop failed: %s", e)
    _bot_app = None
