"""
Archi Service - Main service wrapper.

Runs Archi as a persistent background service with
health monitoring, heartbeat, and graceful shutdown.

Session 89: signal handling and MCP init moved here from agent_loop.py.
The heartbeat (formerly dream_cycle + agent_loop) is the single background loop.
"""

import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

# Ensure project root on path
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

from src.core.heartbeat import Heartbeat
from src.core.goal_manager import GoalManager as CoreGoalManager
from src.monitoring.health_check import health_check
from src.monitoring.cost_tracker import get_cost_tracker
from src.utils.paths import base_path

logger = logging.getLogger(__name__)


class ArchiService:
    """
    Main Archi service wrapper.

    Manages the heartbeat (background loop) and graceful shutdown.
    """

    def __init__(self) -> None:
        self.running = False
        self.heartbeat: Optional[Heartbeat] = None
        self.core_goal_manager: Optional[CoreGoalManager] = None
        self.discord_bot_thread: Optional[threading.Thread] = None
        self.voice_interface = None
        self._shared_router = None
        self._stop_event = threading.Event()
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

            # Initialize heartbeat (the single background loop)
            self._initialize_heartbeat()

            # MCP tool initialization (moved from agent_loop, session 89)
            try:
                from src.tools.tool_registry import get_shared_registry
                tool_registry = get_shared_registry()
                tool_registry.initialize_mcp()
                logger.info("MCP tools initialized")
            except Exception as e:
                logger.warning("MCP init failed: %s", e)

            # Startup recovery: prune duplicates + log goal status
            self._startup_recovery()

            # Run health check
            logger.info("Running initial health check...")
            health = health_check.check_all()
            logger.info("System status: %s", health["overall_status"])
            logger.info("Summary: %s", health["summary"])

            # Start heartbeat monitoring (runs in background thread)
            if self.heartbeat:
                logger.info("Starting heartbeat...")
                self.heartbeat.start()

            # Install signal handlers BEFORE starting the Discord bot so
            # they are never overridden by discord.py's own loop setup.
            # (Session 113: moved up from after memory-init.)
            self._install_signal_handlers()

            # Start Discord bot if token is set and discord.py is installed
            discord_token = os.environ.get("DISCORD_BOT_TOKEN")
            if discord_token:
                try:
                    import discord  # noqa: F401
                    from src.interfaces.discord_bot import init_discord_bot, run_bot

                    init_discord_bot(
                        self.core_goal_manager,
                        router=getattr(self, "_shared_router", None),
                        heartbeat=self.heartbeat,
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

            logger.info("Archi service started successfully")
            logger.info("Press Ctrl+C to stop")
            logger.info("=" * 60)

            # Share heartbeat's MemoryManager with message handler
            # (avoids loading sentence-transformers twice).
            shared_memory = None
            if self.heartbeat and self.heartbeat._memory_init_thread:
                self.heartbeat._memory_init_thread.join(timeout=60)
                shared_memory = self.heartbeat.memory
            if shared_memory:
                from src.interfaces.message_handler import set_memory
                set_memory(shared_memory)

            # Clean console banner (no log prefix) so the user sees a clear
            # "ready" signal.  The event is already logged above.
            sys.stdout.write("\n  ✦ Archi is ready.\n\n")
            sys.stdout.flush()

            # Block until shutdown signal, but periodically check that the
            # heartbeat thread is still alive.  If it dies silently (unhandled
            # exception, segfault in native code, etc.) we log a CRITICAL and
            # trigger a clean shutdown instead of hanging forever.
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=30)
                if not self._stop_event.is_set() and self.heartbeat:
                    _mt = self.heartbeat._monitor_thread
                    if _mt and not _mt.is_alive():
                        logger.critical(
                            "Heartbeat monitor thread died unexpectedly! "
                            "Triggering shutdown."
                        )
                        self._stop_event.set()

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        except Exception as e:
            logger.error("Service error: %s", e, exc_info=True)
        finally:
            self.stop()

    def _install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers for graceful shutdown."""
        def _handler(signum, frame):
            logger.info("Received signal %s; requesting graceful shutdown", signum)
            sys.stdout.write("\n  Ctrl+C received — shutting down gracefully...\n")
            sys.stdout.flush()
            self._stop_event.set()
            # Also trigger PlanExecutor cancellation
            try:
                from src.core.plan_executor import signal_task_cancellation
                signal_task_cancellation("shutdown")
            except ImportError:
                pass

        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
        except (ValueError, OSError):
            pass  # Some platforms don't support these

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

    def _startup_recovery(self) -> None:
        """Run startup recovery: prune duplicate goals, log status, test router."""
        if self.core_goal_manager:
            try:
                pruned = self.core_goal_manager.prune_duplicates()
                if pruned:
                    logger.info("Startup: pruned %d duplicate goals", pruned)
            except Exception as e:
                logger.warning("Goal pruning failed: %s", e)

            from src.core.agent_loop import startup_recovery
            startup_recovery(self.core_goal_manager)

        # Test router connectivity
        router = self._shared_router
        if router:
            try:
                logger.info("Testing API connectivity...")
                test_response = router.ping()
                logger.info(
                    "Router test: %s responded: %s",
                    test_response.get("model", "?"),
                    (test_response.get("text") or "").strip()[:80],
                )
                logger.info("Router test cost: $%.6f", test_response.get("cost_usd", 0))
            except Exception as e:
                logger.warning("Router test failed: %s", e)

    def _initialize_heartbeat(self) -> None:
        """Initialize heartbeat with model router (API-first)."""
        data_dir = _root / "data"
        self.core_goal_manager = CoreGoalManager(data_dir=data_dir)

        router = None

        # API-only: create router (requires OpenRouter API key).
        try:
            from src.models.router import ModelRouter
            router = ModelRouter()
            logger.info("Model router initialized (API-first, default: Grok)")
        except Exception as e:
            logger.warning("Model router not available: %s", e)
            try:
                from src.interfaces.discord_bot import send_notification
                send_notification(
                    "⚠️ No LLM API key found — running in limited mode "
                    "(no reasoning, no background cycles). Set XAI_API_KEY or "
                    "OPENROUTER_API_KEY in .env and restart."
                )
            except Exception:
                pass

        self._shared_router = router
        from src.utils.config import get_heartbeat_config
        hb_cfg = get_heartbeat_config()
        self.heartbeat = Heartbeat(
            interval_seconds=hb_cfg["interval"],
            min_interval=hb_cfg.get("min_interval", 300),
            max_interval=hb_cfg.get("max_interval", 7200),
        )

        if router:
            self.heartbeat.set_router(router)
            self.heartbeat.enable_autonomous_mode(self.core_goal_manager)
            logger.info("Heartbeat: autonomous mode enabled (API-first)")
        else:
            logger.info("Heartbeat: background processing only (no router available)")

    def stop(self) -> None:
        """Stop the service gracefully."""
        if not self.running:
            return

        logger.info("=" * 60)
        logger.info("Stopping Archi Service")
        logger.info("=" * 60)

        self.running = False

        # Signal all running PlanExecutors to stop at their next step
        # boundary.  Must happen BEFORE stopping the heartbeat / worker
        # pool so every concurrent executor sees the sticky flag.
        try:
            from src.core.plan_executor import signal_task_cancellation
            signal_task_cancellation("service_shutdown")
        except ImportError:
            pass

        # Close all LLM client HTTP transports.  This is the KEY step
        # for clean shutdown: it immediately fails any in-flight httpx
        # requests, unblocking ThreadPoolExecutor worker threads.
        if self._shared_router:
            try:
                self._shared_router.close()
            except Exception as e:
                logger.debug("Router close error: %s", e)

        # Stop voice interface
        if self.voice_interface:
            logger.info("Stopping voice interface...")
            self.voice_interface.stop_listening()

        # Stop heartbeat (waits for workers to finish current step)
        if self.heartbeat:
            logger.info("Stopping heartbeat...")
            self.heartbeat.stop()

        # Shut down MCP tools
        try:
            from src.tools.tool_registry import get_shared_registry
            get_shared_registry().shutdown_mcp()
        except Exception as e:
            logger.debug("MCP shutdown: %s", e)

        # Close Discord bot and wait for its thread to exit (with timeout)
        try:
            from src.interfaces.discord_bot import close_bot
            logger.info("Closing Discord bot connection...")
            close_bot()
            if self.discord_bot_thread and self.discord_bot_thread.is_alive():
                self.discord_bot_thread.join(timeout=8)
                if self.discord_bot_thread.is_alive():
                    logger.warning("Discord bot thread did not exit within 8 s")
        except Exception as e:
            logger.debug("Discord bot close on shutdown: %s", e)

        # Stop all Playwright browsers
        try:
            from src.tools.browser_control import _cleanup_all_browsers
            logger.info("Cleaning up Playwright browsers...")
            _cleanup_all_browsers()
        except ImportError:
            pass
        except Exception as e:
            logger.debug("Browser cleanup on shutdown: %s", e)

        # Save core goal manager state
        if self.core_goal_manager:
            try:
                logger.info("Saving goal state...")
                self.core_goal_manager.save_state()
            except Exception as e:
                logger.warning("Failed to save goal state: %s", e)

        logger.info("Skipping final health check (shutdown)")

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
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(name)
        except Exception:
            pass
    try:
        import setproctitle
        setproctitle.setproctitle(name)
    except ImportError:
        pass


def main() -> None:
    """Main entry point."""
    _set_process_name("Archi")

    log_dir = _root / "logs"
    log_dir.mkdir(exist_ok=True)
    (log_dir / "system").mkdir(exist_ok=True)

    log_file = log_dir / "archi_service.log"

    # QueueHandler + QueueListener prevents Windows console blocking.
    import queue
    from logging.handlers import QueueHandler, QueueListener

    _log_queue: queue.Queue = queue.Queue(-1)

    _console_handler = logging.StreamHandler()
    _file_handler = logging.FileHandler(log_file, encoding="utf-8")
    _formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    _console_handler.setFormatter(_formatter)
    _file_handler.setFormatter(_formatter)

    _queue_handler = QueueHandler(_log_queue)

    logging.basicConfig(
        level=logging.INFO,
        handlers=[_queue_handler],
    )

    _log_listener = QueueListener(
        _log_queue, _console_handler, _file_handler, respect_handler_level=True,
    )
    _log_listener.start()

    for name in ("urllib3", "httpcore", "httpx", "sentence_transformers", "huggingface_hub"):
        logging.getLogger(name).setLevel(logging.WARNING)

    service = ArchiService()
    try:
        service.start()
    finally:
        _log_listener.stop()


if __name__ == "__main__":
    main()
