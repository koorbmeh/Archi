"""
Archi Service - Main service wrapper.

Runs Archi as a persistent background service with
health monitoring, dream cycles, and graceful shutdown.
Gate F Phase 1: Production service wrapper.
"""

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

# Ensure project root on path
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

import src.core.cuda_bootstrap  # noqa: F401 - CUDA path

from src.core.agent_loop import run_agent_loop
from src.core.dream_cycle import DreamCycle
from src.core.heartbeat import AdaptiveHeartbeat
from src.core.goal_manager import GoalManager as CoreGoalManager
from src.monitoring.health_check import health_check
from src.monitoring.cost_tracker import get_cost_tracker

logger = logging.getLogger(__name__)


class ArchiService:
    """
    Main Archi service wrapper.

    Manages the agent loop, dream cycles, and graceful shutdown.
    """

    def __init__(self) -> None:
        self.running = False
        self.dream_cycle: Optional[DreamCycle] = None
        self.core_goal_manager: Optional[CoreGoalManager] = None
        self.heartbeat: Optional[AdaptiveHeartbeat] = None
        self.dashboard_thread: Optional[threading.Thread] = None
        self.web_chat_thread: Optional[threading.Thread] = None
        self.discord_bot_thread: Optional[threading.Thread] = None
        logger.info("Archi service initialized")

    def start(self) -> None:
        """Start the service."""
        logger.info("=" * 60)
        logger.info("Starting Archi Service")
        logger.info("=" * 60)

        self.running = True

        try:
            # Patch click.echo to avoid OSError (Windows error 6) when Flask runs in background threads
            self._patch_click_for_windows_threads()

            # Load .env
            self._load_env()

            # Shared heartbeat for agent loop + web chat (web chat records user interaction)
            self.heartbeat = AdaptiveHeartbeat()

            # Initialize dream cycle components (optional - may fail if no local model)
            self._initialize_dream_cycle()

            # Run health check
            logger.info("Running initial health check...")
            health = health_check.check_all()
            logger.info("System status: %s", health["overall_status"])
            logger.info("Summary: %s", health["summary"])

            # Start dream cycle monitoring (runs in background thread)
            if self.dream_cycle:
                logger.info("Starting dream cycle monitoring...")
                self.dream_cycle.start_monitoring()

            # Start web dashboard in background thread
            try:
                from src.web.dashboard import init_dashboard, run_dashboard

                init_dashboard(self.core_goal_manager, self.dream_cycle)
                self.dashboard_thread = threading.Thread(
                    target=run_dashboard,
                    kwargs={"host": "127.0.0.1", "port": 5000},
                    daemon=True,
                )
                self.dashboard_thread.start()
                logger.info("Web dashboard started at http://127.0.0.1:5000")
            except Exception as e:
                logger.warning("Dashboard not started: %s", e)

            try:
                from src.interfaces.web_chat import init_web_chat, run_web_chat

                init_web_chat(
                    self.core_goal_manager,
                    heartbeat=self.heartbeat,
                    dream_cycle=self.dream_cycle,
                )
                self.web_chat_thread = threading.Thread(
                    target=run_web_chat,
                    kwargs={"host": "127.0.0.1", "port": 5001},
                    daemon=True,
                )
                self.web_chat_thread.start()
                logger.info("Web chat started at http://127.0.0.1:5001/chat")
            except Exception as e:
                logger.warning("Web chat not started: %s", e)

            # Start Discord bot if token is set and discord.py is installed
            discord_token = os.environ.get("DISCORD_BOT_TOKEN")
            if discord_token:
                try:
                    import discord  # noqa: F401
                    from src.interfaces.discord_bot import init_discord_bot, run_bot

                    init_discord_bot(self.core_goal_manager)
                    self.discord_bot_thread = threading.Thread(
                        target=run_bot,
                        kwargs={"token": discord_token},
                        daemon=True,
                    )
                    self.discord_bot_thread.start()
                    logger.info("Discord bot started")
                except ImportError:
                    logger.warning(
                        "Discord bot not started: discord.py not installed. "
                        "Run: pip install discord.py"
                    )
                except Exception as e:
                    logger.warning("Discord bot not started: %s", e)

            # Main agent loop (blocks until shutdown)
            logger.info("Archi service started successfully")
            logger.info("Press Ctrl+C to stop")
            logger.info("=" * 60)

            run_agent_loop(heartbeat=self.heartbeat)

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        except Exception as e:
            logger.error("Service error: %s", e, exc_info=True)
        finally:
            self.stop()

    def _patch_click_for_windows_threads(self) -> None:
        """Patch click.echo to avoid OSError when writing to console from background threads."""
        try:
            import click

            _original_echo = click.echo

            def _safe_echo(*args: object, **kwargs: object) -> None:
                try:
                    _original_echo(*args, **kwargs)
                except OSError:
                    pass  # Windows error 6 when console unavailable in thread

            click.echo = _safe_echo
        except ImportError:
            pass

    def _load_env(self) -> None:
        """Load .env from project root."""
        try:
            from dotenv import load_dotenv
            env_path = _root / ".env"
            if env_path.exists():
                load_dotenv(env_path)
                logger.info("Loaded .env")
        except ImportError:
            pass

    def _initialize_dream_cycle(self) -> None:
        """Initialize dream cycle with core goal manager and optional local model."""
        self.core_goal_manager = CoreGoalManager()

        local_model = None
        try:
            from src.models.local_model import LocalModel
            logger.info("Loading local model for dream cycle...")
            local_model = LocalModel()
            logger.info("Local model loaded")
        except Exception as e:
            logger.warning(
                "Local model not available (dream cycle will run without autonomous tasks): %s",
                e,
            )

        self.dream_cycle = DreamCycle(idle_threshold_seconds=300, check_interval_seconds=30)

        if local_model:
            self.dream_cycle.enable_autonomous_mode(self.core_goal_manager, local_model)
            logger.info("Dream cycle: autonomous mode enabled")
        else:
            logger.info("Dream cycle: background processing only (no AI tasks)")

    def stop(self) -> None:
        """Stop the service gracefully."""
        if not self.running:
            return

        logger.info("=" * 60)
        logger.info("Stopping Archi Service")
        logger.info("=" * 60)

        self.running = False

        # Stop dream cycle
        if self.dream_cycle:
            logger.info("Stopping dream cycle...")
            self.dream_cycle.stop_monitoring()

        # Save core goal manager state
        if self.core_goal_manager:
            try:
                logger.info("Saving goal state...")
                self.core_goal_manager.save_state()
            except Exception as e:
                logger.warning("Failed to save goal state: %s", e)

        # Final health check
        try:
            logger.info("Running final health check...")
            health = health_check.check_all()
            logger.info("Final status: %s", health["overall_status"])
        except Exception as e:
            logger.warning("Final health check failed: %s", e)

        # Cost summary
        try:
            tracker = get_cost_tracker()
            summary = tracker.get_summary("today")
            logger.info("Today's cost: $%.4f", summary.get("total_cost", 0))
        except Exception as e:
            logger.warning("Cost summary failed: %s", e)

        logger.info("Archi service stopped")
        logger.info("=" * 60)


def main() -> None:
    """Main entry point."""
    # Create logs directory
    log_dir = _root / "logs"
    log_dir.mkdir(exist_ok=True)
    (log_dir / "system").mkdir(exist_ok=True)

    log_file = log_dir / "archi_service.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )

    # Reduce noise from third-party libs
    for name in ("urllib3", "httpcore", "httpx", "sentence_transformers", "huggingface_hub"):
        logging.getLogger(name).setLevel(logging.WARNING)

    service = ArchiService()
    service.start()


if __name__ == "__main__":
    main()
