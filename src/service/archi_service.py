"""
Archi Service - Main service wrapper.

Runs Archi as a persistent background service with
health monitoring, dream cycles, and graceful shutdown.
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

from src.core.agent_loop import run_agent_loop
from src.core.dream_cycle import DreamCycle
from src.core.heartbeat import AdaptiveHeartbeat
from src.core.goal_manager import GoalManager as CoreGoalManager
from src.monitoring.health_check import health_check
from src.monitoring.cost_tracker import get_cost_tracker
from src.utils.paths import base_path

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
        self.discord_bot_thread: Optional[threading.Thread] = None
        self.voice_interface = None
        self._shared_router = None
        logger.info("Archi service initialized")

    def start(self) -> None:
        """Start the service."""
        logger.info("=" * 60)
        logger.info("Starting Archi Service")
        logger.info("=" * 60)

        self.running = True

        # Clear any sticky shutdown flag from a previous run
        try:
            from src.core.plan_executor import clear_shutdown_flag
            clear_shutdown_flag()
        except ImportError:
            pass

        try:
            # Load .env
            self._load_env()

            # Ensure workspace directories exist
            _base = base_path()
            for subdir in ("workspace", "workspace/reports", "workspace/images", "workspace/projects"):
                os.makedirs(os.path.join(_base, subdir), exist_ok=True)

            # Shared heartbeat for agent loop + Discord chat
            self.heartbeat = AdaptiveHeartbeat()

            # Initialize dream cycle components
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

            # Start Discord bot if token is set and discord.py is installed
            discord_token = os.environ.get("DISCORD_BOT_TOKEN")
            if discord_token:
                try:
                    import discord  # noqa: F401
                    from src.interfaces.discord_bot import init_discord_bot, run_bot

                    init_discord_bot(
                        self.core_goal_manager,
                        router=getattr(self, "_shared_router", None),
                        dream_cycle=self.dream_cycle,
                    )
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

            # Start voice interface if enabled
            if os.environ.get("ARCHI_VOICE_ENABLED", "").lower() in ("true", "1", "yes"):
                try:
                    from src.interfaces.voice_interface import VoiceInterface

                    def _voice_callback(text: str) -> None:
                        """Route voice transcription through the same pipeline as chat."""
                        logger.info("Voice input: %s", text[:80])
                        if self._shared_router:
                            from src.interfaces.message_handler import process_message
                            response_text, _, _ = process_message(
                                text, self._shared_router,
                                source="voice", goal_manager=self.core_goal_manager,
                            )
                            if self.voice_interface:
                                self.voice_interface.speak(response_text)

                    self.voice_interface = VoiceInterface(on_transcription=_voice_callback)
                    status = self.voice_interface.initialize()
                    # Voice is initialized but NOT auto-listening.
                    # TTS is ready for spoken output; STT microphone stays off
                    # until the user explicitly activates it (push-to-talk).
                    # Set ARCHI_VOICE_AUTO_LISTEN=true to enable always-on listening.
                    auto_listen = os.environ.get("ARCHI_VOICE_AUTO_LISTEN", "").lower() in ("true", "1", "yes")
                    if status["stt"] and auto_listen:
                        self.voice_interface.start_listening()
                        logger.info("Voice interface started — always-listening mode (STT=%s, TTS=%s)", status["stt"], status["tts"])
                    elif status["stt"]:
                        logger.info("Voice interface ready — push-to-talk mode (STT=%s, TTS=%s). Use /voice to activate mic.", status["stt"], status["tts"])
                    else:
                        logger.warning("Voice STT not available, voice interface disabled")
                except Exception as e:
                    logger.warning("Voice interface not started: %s", e)

            # Main agent loop (blocks until shutdown)
            logger.info("Archi service started successfully")
            logger.info("Press Ctrl+C to stop")
            logger.info("=" * 60)

            # Share dream cycle's MemoryManager with the agent loop and
            # message handler (avoids loading sentence-transformers twice).
            shared_memory = None
            if self.dream_cycle and self.dream_cycle._memory_init_thread:
                self.dream_cycle._memory_init_thread.join(timeout=60)
                shared_memory = self.dream_cycle.memory
            if shared_memory:
                from src.interfaces.message_handler import set_memory
                set_memory(shared_memory)

            run_agent_loop(
                heartbeat=self.heartbeat,
                router=getattr(self, "_shared_router", None),
                memory=shared_memory,
            )

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        except Exception as e:
            logger.error("Service error: %s", e, exc_info=True)
        finally:
            self.stop()

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
        """Initialize dream cycle with model router (API-first) and optional local model."""
        data_dir = _root / "data"
        self.core_goal_manager = CoreGoalManager(data_dir=data_dir)

        router = None

        # API-only: create router (requires OpenRouter API key).
        # SDXL image gen runs independently via diffusers/torch.
        try:
            from src.models.router import ModelRouter
            router = ModelRouter()
            logger.info("Model router initialized (API-first, default: Grok)")
        except Exception as e:
            logger.warning("Model router not available: %s", e)

        self._shared_router = router
        from src.utils.config import get_dream_cycle_config
        dc_cfg = get_dream_cycle_config()
        self.dream_cycle = DreamCycle(
            idle_threshold_seconds=dc_cfg["idle_threshold"],
            check_interval_seconds=dc_cfg["check_interval"],
        )

        if router:
            # Pass router FIRST so enable_autonomous_mode doesn't lazy-init a duplicate
            self.dream_cycle.set_router(router)
            self.dream_cycle.enable_autonomous_mode(self.core_goal_manager)
            logger.info("Dream cycle: autonomous mode enabled (API-first)")
        else:
            logger.info("Dream cycle: background processing only (no router available)")

    def stop(self) -> None:
        """Stop the service gracefully."""
        if not self.running:
            return

        logger.info("=" * 60)
        logger.info("Stopping Archi Service")
        logger.info("=" * 60)

        self.running = False

        # Signal all running PlanExecutors to stop at their next step
        # boundary.  This must happen BEFORE stopping the dream cycle /
        # worker pool, because the pool shutdown also signals — but by
        # that point a fast executor might have already checked and
        # cleared a non-sticky flag.  The "service_shutdown" message
        # activates the sticky shutdown mode so every concurrent
        # executor sees it.
        try:
            from src.core.plan_executor import signal_task_cancellation
            signal_task_cancellation("service_shutdown")
        except ImportError:
            pass

        # Stop voice interface
        if self.voice_interface:
            logger.info("Stopping voice interface...")
            self.voice_interface.stop_listening()

        # Stop dream cycle (waits for workers to finish current step)
        if self.dream_cycle:
            logger.info("Stopping dream cycle...")
            self.dream_cycle.stop_monitoring()

        # Explicitly close the Discord bot connection so it goes offline
        try:
            from src.interfaces.discord_bot import _bot_client, _bot_loop
            if _bot_client is not None and not _bot_client.is_closed():
                import asyncio
                logger.info("Closing Discord bot connection...")
                if _bot_loop and _bot_loop.is_running():
                    asyncio.run_coroutine_threadsafe(_bot_client.close(), _bot_loop)
                    if self.discord_bot_thread:
                        self.discord_bot_thread.join(timeout=5)
                    logger.info("Discord bot closed")
        except Exception as e:
            logger.debug("Discord bot close on shutdown: %s", e)

        # Stop all Playwright browsers — prevents EPIPE errors on shutdown.
        # The atexit handler in browser_control.py is the safety net, but we
        # also clean up here for orderly shutdown logging.
        try:
            from src.tools.browser_control import _cleanup_all_browsers
            logger.info("Cleaning up Playwright browsers...")
            _cleanup_all_browsers()
        except ImportError:
            pass  # Playwright not installed — nothing to clean up
        except Exception as e:
            logger.debug("Browser cleanup on shutdown: %s", e)

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


def _set_process_name(name: str = "Archi") -> None:
    """Set the process name so it appears as 'Archi' in Task Manager / ps."""
    import sys
    # 1. Windows: set the console title (visible in Task Manager "Window Title" column)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(name)
        except Exception:
            pass
    # 2. Cross-platform: setproctitle (shows as process name in Task Manager / ps)
    try:
        import setproctitle
        setproctitle.setproctitle(name)
    except ImportError:
        pass


def main() -> None:
    """Main entry point."""
    _set_process_name("Archi")

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
