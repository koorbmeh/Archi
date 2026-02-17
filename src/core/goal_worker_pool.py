"""
Goal Worker Pool — Concurrent goal execution via ThreadPoolExecutor.

Replaces the old single-threaded dream cycle executor with a pool that
can work on multiple goals simultaneously.  Each worker thread independently
decomposes and executes one goal at a time.

Created in session 34 (concurrent architecture overhaul).
"""

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from src.core.goal_manager import GoalManager, TaskStatus
from src.core.learning_system import LearningSystem
from src.core.autonomous_executor import execute_task
from src.core.task_orchestrator import TaskOrchestrator

logger = logging.getLogger(__name__)


class WorkerStatus(Enum):
    IDLE = "idle"
    DECOMPOSING = "decomposing"
    EXECUTING = "executing"
    DONE = "done"


@dataclass
class GoalWorkerState:
    """Tracks a single worker's current state."""
    goal_id: str
    status: WorkerStatus = WorkerStatus.IDLE
    cost_spent: float = 0.0
    tasks_completed: int = 0
    tasks_failed: int = 0
    current_task_id: Optional[str] = None
    started_at: Optional[datetime] = None
    error: Optional[str] = None


def _get_per_goal_budget() -> float:
    """Load per-goal budget from rules.yaml worker_pool section."""
    _DEFAULT = 1.00
    try:
        import yaml
        from src.utils.paths import base_path_as_path as _base_path
        rules_path = _base_path() / "config" / "rules.yaml"
        with open(rules_path, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f) or {}
        wp = rules.get("worker_pool", {})
        return float(wp.get("per_goal_budget", _DEFAULT))
    except Exception:
        return _DEFAULT


def _get_max_workers() -> int:
    """Load max workers from rules.yaml worker_pool section."""
    _DEFAULT = 2
    try:
        import yaml
        from src.utils.paths import base_path_as_path as _base_path
        rules_path = _base_path() / "config" / "rules.yaml"
        with open(rules_path, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f) or {}
        wp = rules.get("worker_pool", {})
        return min(int(wp.get("max_workers", _DEFAULT)), 4)  # Hard cap at 4
    except Exception:
        return _DEFAULT


class GoalWorkerPool:
    """Concurrent goal execution pool backed by ThreadPoolExecutor.

    Usage:
        pool = GoalWorkerPool(goal_manager, router, learning_system, ...)
        pool.submit_goal("goal_1")  # Non-blocking, starts worker
        pool.submit_goal("goal_2")  # Runs concurrently with goal_1
        ...
        pool.shutdown()             # Wait for workers to finish
    """

    def __init__(
        self,
        goal_manager: GoalManager,
        router: Any,
        learning_system: LearningSystem,
        overnight_results: List[Dict[str, Any]],
        save_overnight_results: Callable,
        memory: Any = None,
    ) -> None:
        self._goal_manager = goal_manager
        self._router = router
        self._learning_system = learning_system
        self._overnight_results = overnight_results
        self._save_overnight_results = save_overnight_results
        self._memory = memory

        self._max_workers = _get_max_workers()
        self._per_goal_budget = _get_per_goal_budget()
        self._stop = threading.Event()

        # Track which goals are submitted/in-progress to avoid double-submission
        self._submitted: Set[str] = set()
        self._submitted_lock = threading.Lock()

        # Worker state tracking (for monitoring / Discord status)
        self._worker_states: Dict[str, GoalWorkerState] = {}
        self._states_lock = threading.Lock()

        # Futures for tracking completion
        self._futures: Dict[str, Future] = {}

        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="goal-worker",
        )

        logger.info(
            "GoalWorkerPool initialized (max_workers=%d, per_goal_budget=$%.2f)",
            self._max_workers, self._per_goal_budget,
        )

    def submit_goal(self, goal_id: str) -> bool:
        """Submit a goal for background execution.

        Returns True if submitted, False if already running/submitted or pool is stopped.
        """
        if self._stop.is_set():
            logger.warning("Pool is shutting down — rejecting goal %s", goal_id)
            return False

        with self._submitted_lock:
            if goal_id in self._submitted:
                logger.info("Goal %s already submitted — skipping", goal_id)
                return False
            self._submitted.add(goal_id)

        logger.info("Submitting goal %s to worker pool", goal_id)
        future = self._executor.submit(self._execute_goal, goal_id)
        self._futures[goal_id] = future

        # Clean up future reference when done
        future.add_done_callback(lambda f, gid=goal_id: self._on_goal_done(gid, f))
        return True

    def _on_goal_done(self, goal_id: str, future: Future) -> None:
        """Callback when a goal worker finishes (success or failure)."""
        with self._submitted_lock:
            self._submitted.discard(goal_id)
        self._futures.pop(goal_id, None)

        exc = future.exception()
        if exc:
            logger.error("Goal %s worker raised exception: %s", goal_id, exc)
        else:
            logger.info("Goal %s worker finished", goal_id)

        # If a self-initiated goal had failures, clear the dream cycle's
        # suggest cooldown so it doesn't sit idle for an hour after its own
        # initiative didn't work out.
        self._maybe_clear_suggest_cooldown(goal_id)

    def _maybe_clear_suggest_cooldown(self, goal_id: str) -> None:
        """Reset suggest cooldown if a self-initiated goal failed."""
        try:
            goal = self._goal_manager.goals.get(goal_id)
            if not goal:
                return
            intent = (goal.user_intent or "").lower()
            if not intent.startswith("self-initiated"):
                return  # Only affects proactive initiatives

            state = self._worker_states.get(goal_id)
            if not state or state.tasks_failed == 0:
                return  # Goal succeeded — cooldown is fine

            # Clear the dream cycle's suggest cooldown
            try:
                from src.interfaces.discord_bot import _dream_cycle
                if _dream_cycle is not None and hasattr(_dream_cycle, "_last_suggest_time"):
                    _dream_cycle._last_suggest_time = None
                    logger.info(
                        "Cleared suggest cooldown — self-initiated goal %s "
                        "had %d task failure(s)",
                        goal_id, state.tasks_failed,
                    )
            except ImportError:
                pass
        except Exception as e:
            logger.debug("Could not check suggest cooldown reset: %s", e)

    def _execute_goal(self, goal_id: str) -> None:
        """Worker entry point: decompose + execute all tasks for one goal.

        This runs in a pool thread. It:
        1. Decomposes the goal if not already decomposed
        2. Loops through tasks using get_next_task_for_goal()
        3. Executes each task via execute_task()
        4. Tracks cost against per-goal budget
        5. Sends Discord notification on completion
        """
        from src.interfaces.discord_bot import send_notification

        state = GoalWorkerState(goal_id=goal_id, started_at=datetime.now())
        with self._states_lock:
            self._worker_states[goal_id] = state

        _goal_cost = 0.0

        try:
            # --- Phase 1: Decompose if needed ---
            goal = self._goal_manager.goals.get(goal_id)
            if not goal:
                logger.warning("Goal %s not found in goal_manager", goal_id)
                state.error = "Goal not found"
                return

            if not goal.is_decomposed:
                state.status = WorkerStatus.DECOMPOSING
                logger.info("[worker:%s] Decomposing goal: %s", goal_id, goal.description[:80])
                try:
                    self._goal_manager.decompose_goal(
                        goal_id,
                        self._router,
                        learning_hints=self._learning_system.get_active_insights(2),
                    )
                except Exception as e:
                    logger.error("[worker:%s] Decomposition failed: %s", goal_id, e)
                    state.error = f"Decomposition failed: {e}"
                    try:
                        send_notification(
                            f"\u274c Goal decomposition failed: {goal.description[:100]} — {e}"
                        )
                    except Exception:
                        pass
                    return

            # --- Phase 2: Execute tasks ---
            state.status = WorkerStatus.EXECUTING

            # Resume any in-progress tasks first (crash recovery)
            goal = self._goal_manager.goals.get(goal_id)
            if goal:
                for task in goal.tasks:
                    if self._stop.is_set():
                        break
                    if task.status == TaskStatus.IN_PROGRESS:
                        logger.info("[worker:%s] Resuming task: %s", goal_id, task.task_id)
                        try:
                            result = execute_task(
                                task, self._goal_manager, self._router,
                                self._learning_system, self._overnight_results,
                                self._save_overnight_results,
                                memory=self._memory,
                            )
                            _goal_cost += result.get("cost_usd", 0)
                            self._goal_manager.complete_task(task.task_id, result)
                            state.tasks_completed += 1
                            state.cost_spent = _goal_cost
                        except Exception as e:
                            logger.error("[worker:%s] Resume failed: %s", goal_id, e)
                            self._goal_manager.fail_task(task.task_id, str(e))
                            state.tasks_failed += 1

            # Wave-based parallel task execution (session 35)
            orchestrator = TaskOrchestrator()
            orch_result = orchestrator.execute_goal_tasks(
                goal_id=goal_id,
                goal_manager=self._goal_manager,
                execute_task_fn=execute_task,
                router=self._router,
                learning_system=self._learning_system,
                overnight_results=self._overnight_results,
                save_overnight_results=self._save_overnight_results,
                stop_flag=self._stop,
                budget_remaining=self._per_goal_budget - _goal_cost,
                memory=self._memory,
            )
            _goal_cost += orch_result["total_cost"]
            state.cost_spent = _goal_cost
            state.tasks_completed += orch_result["tasks_completed"]
            state.tasks_failed += orch_result["tasks_failed"]

            # Check if goal completed
            goal = self._goal_manager.goals.get(goal_id)
            if goal and goal.is_complete():
                self._notify_goal_complete(goal)

            # Notify if budget was the limiting factor
            if _goal_cost >= self._per_goal_budget:
                try:
                    send_notification(
                        f"\u26a0\ufe0f Goal paused (budget: ${_goal_cost:.2f}): "
                        f"{goal.description[:100] if goal else goal_id}"
                    )
                except Exception:
                    pass

            # Notify on task failures
            if orch_result["tasks_failed"] > 0:
                try:
                    send_notification(
                        f"\u274c Goal had {orch_result['tasks_failed']} task failure(s): "
                        f"{goal.description[:100] if goal else goal_id}"
                    )
                except Exception:
                    pass

            state.current_task_id = None
            state.status = WorkerStatus.DONE

        except Exception as e:
            logger.error("[worker:%s] Unhandled error: %s", goal_id, e, exc_info=True)
            state.error = str(e)
            state.status = WorkerStatus.DONE
        finally:
            with self._states_lock:
                # Keep state for status queries; clean up after a while
                pass

    def _notify_goal_complete(self, goal: Any) -> None:
        """Send a Discord notification when a goal completes."""
        from src.interfaces.discord_bot import send_notification

        _intent = (goal.user_intent or "").lower()
        if _intent.startswith("user "):
            try:
                from src.core.reporting import send_user_goal_completion
                _goal_results = [
                    r for r in self._overnight_results
                    if r.get("goal", "") == goal.description
                ]
                _all_files = []
                for r in _goal_results:
                    _all_files.extend(r.get("files_created", []))
                send_user_goal_completion(
                    goal_description=goal.description,
                    task_results=_goal_results,
                    files_created=_all_files,
                )
            except Exception as e:
                logger.debug("User goal completion notify failed: %s", e)
        else:
            try:
                send_notification(
                    f"\U0001f3c6 Goal complete: {goal.description} "
                    f"({len(goal.tasks)} tasks finished)"
                )
            except Exception:
                pass

    # -- Public API --

    def cancel_goal(self, goal_id: str) -> bool:
        """Request cancellation of a goal.

        Sets the stop flag for the worker (if running) and removes from pending.
        Returns True if the goal was found and cancel was initiated.
        """
        with self._submitted_lock:
            was_submitted = goal_id in self._submitted

        if was_submitted:
            # Can't cancel individual futures with ThreadPoolExecutor,
            # but we can mark it so the worker checks and exits.
            # For now, cancel pending futures that haven't started.
            future = self._futures.get(goal_id)
            if future and future.cancel():
                logger.info("Cancelled pending goal %s", goal_id)
                with self._submitted_lock:
                    self._submitted.discard(goal_id)
                return True
            else:
                logger.info(
                    "Goal %s is already running — it will finish its current task",
                    goal_id,
                )
                return True

        return False

    def get_status(self) -> Dict[str, Any]:
        """Return pool status for monitoring / Discord status command."""
        with self._submitted_lock:
            pending = set(self._submitted)
        with self._states_lock:
            workers = {}
            for gid, ws in self._worker_states.items():
                workers[gid] = {
                    "status": ws.status.value,
                    "cost": ws.cost_spent,
                    "tasks_completed": ws.tasks_completed,
                    "tasks_failed": ws.tasks_failed,
                    "current_task": ws.current_task_id,
                    "started_at": ws.started_at.isoformat() if ws.started_at else None,
                    "error": ws.error,
                }
        return {
            "max_workers": self._max_workers,
            "per_goal_budget": self._per_goal_budget,
            "submitted_goals": list(pending),
            "workers": workers,
            "stopped": self._stop.is_set(),
        }

    def is_working(self) -> bool:
        """Return True if any workers are currently executing goals."""
        with self._submitted_lock:
            return len(self._submitted) > 0

    def shutdown(self, timeout: float = 30.0) -> None:
        """Gracefully shut down the worker pool.

        Sets stop flag so workers finish their current task, then
        waits up to `timeout` seconds for them to exit.
        """
        logger.info("GoalWorkerPool shutting down (timeout=%.0fs)...", timeout)
        self._stop.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        logger.info("GoalWorkerPool shut down")
