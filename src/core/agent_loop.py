"""
Main execution loop for Archi: emergency stop, hardware throttling,
adaptive heartbeat, trigger check (heartbeat + safety test actions).
Graceful shutdown on Ctrl+C.
"""
# Ensure CUDA is on PATH before any code loads the local model (llama_cpp DLLs).
import src.core.cuda_bootstrap  # noqa: F401

import logging
import os
import signal
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Union

import yaml

from src.core.heartbeat import AdaptiveHeartbeat
from src.core.logger import ActionLogger
from src.core.safety_controller import Action, SafetyController
from src.core.goal_manager import GoalManager
# timestamps module available for future use (dream cycles etc.)
# from src.maintenance.timestamps import load_timestamp, save_timestamp
from src.memory.memory_manager import MemoryManager
from src.models.router import ModelRouter
from src.monitoring.system_monitor import SystemMonitor
from src.tools.tool_registry import ToolRegistry
from src.utils.paths import base_path as _base_path

logger = logging.getLogger(__name__)


class EmergencyStop:
    """Check for EMERGENCY_STOP file; if present, agent must exit immediately."""

    def __init__(self, stop_file_path: Optional[str] = None) -> None:
        base = _base_path()
        self.stop_file = stop_file_path or os.path.join(base, "EMERGENCY_STOP")

    def check(self) -> bool:
        """Return True if emergency stop is triggered."""
        try:
            if os.path.isfile(self.stop_file):
                logger.critical("EMERGENCY STOP TRIGGERED: %s", self.stop_file)
                return True
            return False
        except OSError as e:
            logger.debug("Emergency stop check failed: %s", e)
            return False


def _load_monitoring_thresholds() -> dict:
    """Load monitoring section from config/rules.yaml."""
    base = _base_path()
    path = os.path.join(base, "config", "rules.yaml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("monitoring", {}) or {}
    except (OSError, yaml.YAMLError):
        return {}


def check_triggers() -> List[Union[dict, Action]]:
    """Periodic heartbeat (60s).  Returns a heartbeat dict for the agent loop."""
    now = time.monotonic()

    if not hasattr(check_triggers, "_last_trigger_time"):
        check_triggers._last_trigger_time = 0.0

    elapsed = now - check_triggers._last_trigger_time

    heartbeat_interval = 60.0
    if elapsed >= heartbeat_interval:
        check_triggers._last_trigger_time = now
        return [{"type": "heartbeat"}]
    return []


def startup_recovery(
    goal_manager: GoalManager,
) -> None:
    """Log goal status on startup."""
    logger.info("Running startup recovery check...")
    try:
        status = goal_manager.get_status()
        active = status.get("active_goals", 0)
        pending = status.get("pending_tasks", 0)
        if active:
            logger.info("Goals: %d active, %d pending tasks", active, pending)
    except Exception as e:
        logger.warning("Startup recovery: goal status check failed: %s", e)
    logger.info("Startup recovery complete")


def run_agent_loop(
    *,
    emergency_stop: Optional[EmergencyStop] = None,
    system_monitor: Optional[SystemMonitor] = None,
    heartbeat: Optional[AdaptiveHeartbeat] = None,
    action_logger: Optional[ActionLogger] = None,
    safety_controller: Optional[SafetyController] = None,
    local_model: Optional[Any] = None,
    router: Optional[Any] = None,
) -> None:
    """
    Main loop: emergency stop check, hardware throttle, trigger check,
    optional action logging for heartbeat, adaptive sleep. Graceful shutdown on SIGINT.
    """
    base = _base_path()
    emergency_stop = emergency_stop or EmergencyStop()
    action_logger = action_logger or ActionLogger()
    safety_controller = safety_controller or SafetyController()
    tool_registry = ToolRegistry()

    monitoring = _load_monitoring_thresholds()
    system_monitor = system_monitor or SystemMonitor(
        cpu_threshold=float(monitoring.get("cpu_threshold", 80)),
        memory_threshold=float(monitoring.get("memory_threshold", 90)),
        temp_threshold=float(monitoring.get("temp_threshold", 80)),
        disk_threshold=float(monitoring.get("disk_threshold", 90)),
    )
    heartbeat = heartbeat or AdaptiveHeartbeat()

    # Local model is loaded by ModelRouter on demand (no separate init needed)

    stop_event = threading.Event()

    def _signal_handler(signum: int, frame: Optional[object]) -> None:
        logger.info("Received signal %s; requesting graceful shutdown", signum)
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, OSError):
        # Some platforms don't support these
        pass

    logger.info("Archi agent loop starting (base_path=%s)", base)

    memory = MemoryManager()
    logger.info("Memory system initialized")

    goal_manager = GoalManager()

    # One-time cleanup: prune duplicate goals that accumulated from
    # _plan_future_work creating the same goals every dream cycle.
    try:
        pruned = goal_manager.prune_duplicates()
        if pruned:
            logger.info("Startup recovery: pruned %d duplicate goals", pruned)
    except Exception as e:
        logger.warning("Goal pruning failed: %s", e)

    if router is None:
        try:
            router = ModelRouter()
            logger.info("Model router initialized")
        except Exception as e:
            logger.warning("Model router not available: %s", e)
            router = None
    else:
        logger.info("Model router initialized (shared)")
    try:
        if router is not None:
            if router.local_available:
                logger.info("Router: local + OpenRouter ready")
            else:
                logger.info("Router: API-only mode (local model not available)")
            # One test query to verify routing in agent context.
            # prefer_local=True: never escalate to Grok (free test).
            # use_reasoning=False: simple arithmetic needs no chain-of-thought;
            # avoids <think>-only outputs that trigger low confidence and Grok escalation.
            logger.info("Testing router integration...")
            test_response = router.generate(
                "What is 2+2? Answer with just the number.",
                max_tokens=50,
                prefer_local=True,
                use_reasoning=False,
            )
            logger.info(
                "Router test: %s responded: %s",
                test_response.get("model", "?"),
                (test_response.get("text") or "").strip()[:80],
            )
            logger.info("Router test cost: $%.6f", test_response.get("cost_usd", 0))
    except Exception as e:
        logger.warning("Router test failed: %s", e)

    action_logger.log_action(
        action_type="system_start",
        parameters={"base_path": base},
        result="started",
    )

    startup_recovery(goal_manager)

    iteration = 0
    action_count = 0
    try:
        while not stop_event.is_set():
            iteration += 1

            if emergency_stop.check():
                logger.critical("Exiting due to emergency stop")
                break

            sleep_multiplier = 5.0 if system_monitor.should_throttle() else 1.0

            triggers = check_triggers()

            if triggers:
                heartbeat.record_system_event()
                for trigger in triggers:
                    start_time = time.perf_counter()
                    if isinstance(trigger, dict) and trigger.get("type") == "heartbeat":
                        action_logger.log_action(
                            action_type="heartbeat",
                            parameters={"iteration": iteration},
                            model_used="system",
                            confidence=1.0,
                            cost_usd=0.0,
                            result="success",
                            duration_ms=(time.perf_counter() - start_time) * 1000,
                        )
                        memory.store_action(
                            action_type="heartbeat",
                            parameters={"iteration": iteration},
                            result="success",
                            confidence=1.0,
                        )
                        action_count += 1
                    elif isinstance(trigger, Action):
                        authorized = safety_controller.authorize(trigger)
                        duration_ms = (time.perf_counter() - start_time) * 1000
                        if authorized:
                            heartbeat.record_user_interaction()
                            result = tool_registry.execute(
                                trigger.type, trigger.parameters
                            )
                            action_logger.log_action(
                                action_type=trigger.type,
                                parameters=trigger.parameters,
                                model_used="local",
                                confidence=trigger.confidence,
                                cost_usd=0.0,
                                result="success" if result.get("success") else "failure",
                                duration_ms=int(duration_ms),
                                error=result.get("error"),
                            )
                            memory.store_action(
                                action_type=trigger.type,
                                parameters=trigger.parameters,
                                result=result.get("success", False),
                                confidence=trigger.confidence,
                            )
                            action_count += 1
                        else:
                            action_logger.log_action(
                                action_type=trigger.type,
                                parameters=trigger.parameters,
                                model_used="safety_controller",
                                confidence=trigger.confidence,
                                cost_usd=0.0,
                                result="denied",
                                duration_ms=int(duration_ms),
                            )
                            memory.store_action(
                                action_type=trigger.type,
                                parameters=trigger.parameters,
                                result="denied",
                                confidence=trigger.confidence,
                            )
                            action_count += 1
                        if action_count > 0 and action_count % 100 == 0:
                            stats = memory.get_stats()
                            logger.info("Memory stats: %s", stats)
                            if router is not None:
                                logger.info("Router stats: %s", router.get_stats())
                # Log metrics to DB every 10 minutes (not every heartbeat)
                if action_count % 10 == 0:
                    try:
                        system_monitor.log_metrics()
                    except Exception as e:
                        logger.debug("Metrics log failed: %s", e)
            else:
                # Idle: no triggers; check for autonomous work from goal queue.
                # Tasks here are only discovered, not executed — execution
                # happens in dream cycles (after 5 min idle).  Only log once
                # per task to avoid spamming the terminal every 10 seconds.
                task = goal_manager.get_next_task()
                if task is not None:
                    _tid = task.task_id
                    if _tid != getattr(goal_manager, "_last_discovered_tid", None):
                        logger.info(
                            "Idle: next task queued — %s: %s (dream cycle will execute after 5 min idle)",
                            _tid, task.description[:80],
                        )
                        goal_manager._last_discovered_tid = _tid  # type: ignore[attr-defined]
                    memory.store_action(
                        action_type="goal_discovered",
                        parameters={
                            "task_id": task.task_id,
                            "goal_id": task.goal_id,
                            "description": task.description[:200],
                        },
                        result=False,
                        confidence=0.0,
                    )

            sleep_duration = heartbeat.get_sleep_duration() * sleep_multiplier
            logger.debug("Sleeping %.2f s (iteration %d)", sleep_duration, iteration)
            # Chunk sleep so Ctrl+C is seen within ~1s (Windows doesn't interrupt long waits)
            remaining = sleep_duration
            while remaining > 0 and not stop_event.is_set():
                chunk = min(1.0, remaining)
                if stop_event.wait(timeout=chunk):
                    break
                remaining -= chunk
            if stop_event.is_set():
                logger.info("Shutdown requested during sleep")
                break

    except Exception as e:
        logger.exception("Agent loop error: %s", e)
        action_logger.log_action(
            action_type="system_error",
            parameters={"error": str(e)},
            result="failure",
            error=str(e),
        )
    finally:
        action_logger.log_action(
            action_type="system_stop",
            parameters={"iteration": iteration},
            result="stopped",
        )
        action_logger.close()
        logger.info("Archi agent loop stopped")


def main() -> None:
    """Entry point: configure logging and run the agent loop."""
    base = _base_path()
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(base, ".env"))
    except ImportError:
        pass
    log_dir = os.path.join(base, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "system", f"{datetime.utcnow().strftime('%Y-%m-%d')}.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    # Reduce noise from third-party libs (startup is much quieter)
    for name in ("urllib3", "httpcore", "httpx", "sentence_transformers", "huggingface_hub"):
        logging.getLogger(name).setLevel(logging.WARNING)

    run_agent_loop()


if __name__ == "__main__":
    main()
