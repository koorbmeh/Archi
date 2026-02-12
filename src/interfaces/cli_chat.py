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

    COMMANDS = {
        "/help": "Show available commands",
        "/goal": "Create a new goal (/goal <description>)",
        "/goals": "List all goals",
        "/status": "Show system status",
        "/cost": "Show cost summary",
        "/clear": "Clear screen",
        "/exit": "Exit chat",
        "/quit": "Exit chat",
    }

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

                if user_input.startswith("/"):
                    if not self._handle_command(user_input):
                        break
                    continue

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

    def _handle_command(self, command: str) -> bool:
        """
        Handle slash commands.

        Returns:
            False if should exit, True otherwise
        """
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ["/exit", "/quit"]:
            return False

        if cmd == "/help":
            self._show_help()
        elif cmd == "/goal":
            self._create_goal(args)
        elif cmd == "/goals":
            self._list_goals()
        elif cmd == "/status":
            self._show_status()
        elif cmd == "/cost":
            self._show_costs()
        elif cmd == "/clear":
            print("\033[2J\033[H", end="")
        else:
            print(f"\n\033[91mUnknown command:\033[0m {cmd}")
            print("Type /help for available commands\n")

        return True

    def _show_help(self) -> None:
        """Show available commands."""
        print("\n\033[1mAvailable Commands:\033[0m")
        print("-" * 60)
        for cmd, desc in self.COMMANDS.items():
            print(f"  {cmd:<15} {desc}")
        print()

    def _create_goal(self, description: str) -> None:
        """Create a new goal."""
        if not description:
            print("\n\033[91mError:\033[0m Goal description required")
            print("Usage: /goal <description>\n")
            return

        try:
            goal = self.goal_manager.create_goal(
                description=description,
                user_intent="User request via chat",
                priority=5,
            )

            print(f"\n\033[92m[OK]\033[0m Goal created: {goal.goal_id}")
            print(f"  Description: {description}")
            print("  Archi will work on this during dream cycles.\n")

        except Exception as e:
            print(f"\n\033[91mError:\033[0m Failed to create goal: {e}\n")

    def _list_goals(self) -> None:
        """List all goals."""
        status = self.goal_manager.get_status()

        print("\n\033[1mGoals:\033[0m")
        print("-" * 60)

        if status.get("total_goals", 0) == 0:
            print("  No goals yet. Create one with /goal <description>\n")
            return

        for goal_data in status.get("goals", []):
            goal_id = goal_data.get("goal_id", "?")
            desc = goal_data.get("description", "")[:60]
            progress = goal_data.get("completion_percentage", 0)
            tasks = goal_data.get("tasks", [])

            status_icon = "[OK]" if progress == 100 else "[...]"
            print(f"\n  {status_icon} {goal_id}: {desc}")
            print(f"     Progress: {progress:.0f}% ({len(tasks)} tasks)")

        print()

    def _show_status(self) -> None:
        """Show system status."""
        from src.monitoring.health_check import health_check

        health = health_check.check_all()

        print("\n\033[1mSystem Status:\033[0m")
        print("-" * 60)
        print(f"  Overall: {health.get('overall_status', 'unknown').upper()}")
        print(f"  Summary: {health.get('summary', 'Unknown')}")
        print("\n  Components:")

        for component, check in health.get("checks", {}).items():
            status = check.get("status", "unknown")
            icon = "[OK]" if status == "healthy" else "[!]" if status == "degraded" else "[X]"
            print(f"    {icon} {component}: {status}")

        print()

    def _show_costs(self) -> None:
        """Show cost summary."""
        tracker = get_cost_tracker()
        summary = tracker.get_summary("all")

        today = summary.get("today", {})
        month = summary.get("month", {})

        print("\n\033[1mCost Summary:\033[0m")
        print("-" * 60)
        print(f"  Today:    ${today.get('total_cost', 0):.4f} / ${today.get('budget', 0):.2f}")
        print(f"  Month:    ${month.get('total_cost', 0):.4f} / ${month.get('budget', 0):.2f}")
        print(f"  All-time: ${summary.get('total_cost', 0):.4f}")
        print(f"\n  Total calls: {summary.get('total_calls', 0)}")
        print()

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
            response_text, actions_taken, _cost = execute_action(message, self.router)

            # Append action results if any
            if actions_taken:
                action_lines = "\n".join(
                    f"  [OK] {a['description']}" for a in actions_taken
                )
                response_text += f"\n\n{action_lines}"

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
