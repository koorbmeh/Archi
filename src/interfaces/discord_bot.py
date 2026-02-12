"""
Discord Bot Interface - Chat with Archi from Discord.

Listens to DMs and @mentions, sends messages to Archi via action_executor.
Gate G Phase 2: Discord integration.
"""

import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_router: Optional[Any] = None
_goal_manager: Optional[Any] = None


def init_discord_bot(goal_manager: Optional[Any] = None) -> None:
    """Set goal manager for Discord (used when processing messages)."""
    global _goal_manager
    _goal_manager = goal_manager


def _get_router():
    """Lazy-load ModelRouter on first use."""
    global _router
    if _router is None:
        try:
            import src.core.cuda_bootstrap  # noqa: F401
            from src.models.router import ModelRouter
            _router = ModelRouter()
            logger.info("Model router initialized for Discord bot")
        except Exception as e:
            logger.warning("Model router not available: %s", e)
    return _router


def _truncate(text: str, max_len: int = 1900) -> str:
    """Truncate text for Discord (max 2000 chars per message)."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def process_with_archi(message: str, history: Optional[list] = None) -> tuple[str, str, list]:
    """
    Process message through Archi's action executor (blocking - run off event loop).

    Returns:
        (full_response_for_history, truncated_for_discord, actions_taken)
    """
    router = _get_router()
    if not router:
        msg = "Archi is not available. Check that the local model or Grok API is configured."
        return msg, _truncate(msg), []

    from src.interfaces.action_executor import process_message

    response_text, actions_taken, cost = process_message(
        message, router, history=history, source="discord", goal_manager=_goal_manager
    )

    out = response_text
    if actions_taken:
        action_lines = "\n".join(f"• {a.get('description', 'Done')}" for a in actions_taken)
        out = f"{out}\n\n{action_lines}"
    if cost > 0:
        out = f"{out}\n\n(Cost: ${cost:.4f})"

    return out, _truncate(out), actions_taken


def _should_respond(message, bot_user_id: int) -> bool:
    """True if we should respond to this message."""
    # Ignore own messages
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
            logger.info("Discord bot ready: %s", self.user)
            print(f"Archi Discord bot ready: {self.user}")

        async def on_message(self, message):
            if not _should_respond(message, self.user.id):
                return

            content = _get_content(message, self.user.id)
            if not content:
                return

            # Typing indicator
            async with message.channel.typing():
                try:
                    from src.interfaces.chat_history import get_recent, append

                    history = get_recent()
                    # Run blocking process_message off the event loop to avoid heartbeat blocking
                    full_response, response, actions_taken = await asyncio.to_thread(
                        process_with_archi, content, history
                    )
                    await message.reply(response)
                    # Persist to chat history (survives restart)
                    try:
                        append("user", content)
                        append("assistant", full_response)
                    except Exception as e:
                        logger.debug("Could not save chat history: %s", e)
                except Exception as e:
                    logger.error("Discord bot error: %s", e, exc_info=True)
                    await message.reply(f"Sorry, I encountered an error: {str(e)}")

    return ArchiBot(intents=intents)


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
