"""
CLI Chat Interface

Interactive terminal-based chat with Archi.
Supports conversation, commands, and goal management.
Gate G Phase 1: CLI chat interface.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Ensure project root on path
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

import src.core.cuda_bootstrap  # noqa: F401 - CUDA path

from src.core.goal_manager import GoalManager
from src.interfaces.action_executor import process_message as execute_action
from src.interfaces.chat_history import append, get_recent
from src.monitoring.cost_tracker import get_cost_tracker

logger = logging.getLogger(__name__)


def _get_prompt_fn():
    """Use prompt_toolkit if available and TTY, else fall back to input()."""
    use_simple = not sys.stdin.isatty()

    if not use_simple:
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory

            history_file = _root / "data" / "chat_history.txt"
            history_file.parent.mkdir(exist_ok=True)
            session = PromptSession(history=FileHistory(str(history_file)))

            def prompt(prefix: str = "You: ") -> str:
                return session.prompt(prefix).strip()

            return prompt
        except (ImportError, OSError):
            pass

    def prompt(prefix: str = "You: ") -> str:
        try:
            return input(prefix).strip()
        except EOFError:
            raise
    return prompt


class CLIChat:
    """
    CLI Chat Interface for Archi.

    Interactive terminal chat with:
    - Natural conversation
    - Commands (/goal, /status, /help)
    - Goal management
    - Cost tracking
    """

    def __init__(self) -> None:
        self.router: Optional[object] = None
        self.goal_manager = GoalManager()
        self._prompt_fn = _get_prompt_fn()

        try:
            from src.models.router import ModelRouter

            self.router = ModelRouter()
            logger.info("Model router initialized for chat")
        except Exception as e:
            logger.warning("Model router not available: %s", e)

        logger.info("CLI Chat initialized")

    def start(self) -> None:
        """Start the chat interface."""
        self._print_welcome()

        while True:
            try:
                user_input = self._prompt_fn("You: ").strip()

                if not user_input:
                    continue

                # Exit and clear are UI-only, handled locally
                if user_input.lower() in ("/exit", "/quit"):
                    break
                if user_input.lower() == "/clear":
                    print("\033[2J\033[H", end="")
                    continue

                # Everything else (including /goal, /goals, /status, /cost, /help) goes through action_executor
                if not self.router:
                    print("\n\033[91mError:\033[0m No AI model available. Configure Grok API key.\n")
                    continue

                response = self._process_message(user_input)
                print(f"\n\033[94mArchi:\033[0m {response}\n")

            except KeyboardInterrupt:
                print("\n\nUse /exit to quit")
                continue
            except EOFError:
                break
            except Exception as e:
                print(f"\n\033[91mError:\033[0m {e}\n")
                logger.error("Chat error: %s", e, exc_info=True)

        self._print_goodbye()

    def _print_welcome(self) -> None:
        """Print welcome message."""
        print("\n" + "=" * 60)
        print("Archi - Autonomous AI Agent")
        print("=" * 60)
        print("\nWelcome! I'm Archi, your AI assistant.")
        print("\nI can answer questions and execute actions (e.g. create files in workspace).")
        print("Type /help for commands or just chat naturally.")
        print("Type /exit to quit.\n")

    def _print_goodbye(self) -> None:
        """Print goodbye message."""
        try:
            tracker = get_cost_tracker()
            summary = tracker.get_summary("today")
            cost = summary.get("total_cost", 0)
        except Exception:
            cost = 0

        print("\n" + "=" * 60)
        print("Goodbye!")
        print("=" * 60)
        print(f"\nSession cost: ${cost:.4f}")
        print("Thanks for chatting!\n")

    def _process_message(self, message: str) -> str:
        """
        Process user message with Archi.

        Uses action executor to parse intent and execute actions (file create, etc)
        when the user requests them. Falls back to conversational response otherwise.

        Args:
            message: User's message

        Returns:
            Archi's response
        """
        try:
            history = get_recent()
            response_text, actions_taken, _cost = execute_action(
                message,
                self.router,
                history=history,
                source="cli",
                goal_manager=self.goal_manager,
            )

            # Append action results if any
            if actions_taken:
                action_lines = "\n".join(
                    f"  [OK] {a['description']}" for a in actions_taken
                )
                response_text += f"\n\n{action_lines}"

            # Persist to shared chat history (same as web/discord)
            try:
                append("user", message)
                append("assistant", response_text)
            except Exception as e:
                logger.debug("Could not save chat history: %s", e)

            return response_text

        except Exception as e:
            logger.error("Error processing message: %s", e, exc_info=True)
            return f"Sorry, I encountered an error: {str(e)}"


def main() -> None:
    """Main entry point."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("src").setLevel(logging.INFO)

    try:
        from dotenv import load_dotenv

        load_dotenv(_root / ".env")
    except ImportError:
        pass

    chat = CLIChat()
    chat.start()


if __name__ == "__main__":
    main()
