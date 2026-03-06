"""
Goal Worker Pool — Concurrent goal execution via ThreadPoolExecutor.

Replaces the old single-threaded heartbeat executor with a pool that
can work on multiple goals simultaneously.  Each worker thread independently
decomposes and executes one goal at a time.

Phase 5 additions:
- Discovery phase: scans project files before Architect runs.
- Request prioritization: reactive (user) goals preempt proactive (background) goals.
- DAG scheduler: event-driven task execution via TaskOrchestrator.

Created in session 34. Enhanced session 53 (Phase 5: Planning + Scheduling).
"""

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from src.core.goal_manager import GoalManager, TaskStatus
from src.core.learning_system import LearningSystem
from src.core.autonomous_executor import execute_task
from src.core.task_orchestrator import TaskOrchestrator
from src.utils.config import get_user_name

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
    reactive: bool = False  # Phase 5: True = user-requested, False = proactive/initiative
    discovery_cost: float = 0.0  # Phase 5: cost of discovery phase


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
        on_clear_suggest_cooldown: Optional[Callable] = None,
    ) -> None:
        self._goal_manager = goal_manager
        self._router = router
        self._learning_system = learning_system
        self._overnight_results = overnight_results
        self._save_overnight_results = save_overnight_results
        self._memory = memory
        self._on_clear_suggest_cooldown = on_clear_suggest_cooldown

        self._max_workers = _get_max_workers()
        self._per_goal_budget = _get_per_goal_budget()
        self._stop = threading.Event()

        # Track which goals are submitted/in-progress to avoid double-submission
        self._submitted: Set[str] = set()
        self._submitted_lock = threading.Lock()

        # Per-goal stop flags for cancellation (Critical 2 fix).
        # Each running goal gets its own Event; cancel_goal() sets it.
        self._goal_stop_flags: Dict[str, threading.Event] = {}
        self._goal_flags_lock = threading.Lock()

        # Worker state tracking (for monitoring / Discord status)
        self._worker_states: Dict[str, GoalWorkerState] = {}

        # Timestamp of most recent goal-completion notification (session 194).
        # The heartbeat checks this to avoid sending a work suggestion
        # immediately after a goal result notification — prevents duplicate
        # messages about the same topic.
        self.last_goal_notification_time: float = 0.0
        self._states_lock = threading.Lock()

        # Futures for tracking completion
        self._futures: Dict[str, Future] = {}

        # Proactive executor for background/initiative goals
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="goal-worker",
        )
        # Dedicated reactive executor: user-requested goals start immediately
        # without waiting for proactive tasks to release a slot (session 58).
        self._reactive_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="goal-reactive",
        )

        logger.info(
            "GoalWorkerPool initialized (max_workers=%d, reactive=1, per_goal_budget=$%.2f)",
            self._max_workers, self._per_goal_budget,
        )

    def submit_goal(self, goal_id: str, reactive: bool = False) -> bool:
        """Submit a goal for background execution.

        Args:
            goal_id: Goal to execute.
            reactive: True for user-requested goals (higher priority).
                      Reactive goals are submitted first to the executor,
                      ensuring they get the next available worker slot.

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

        priority_tag = "REACTIVE" if reactive else "proactive"
        pool = self._reactive_executor if reactive else self._executor
        logger.info("Submitting goal %s to worker pool [%s]", goal_id, priority_tag)
        future = pool.submit(self._execute_goal, goal_id, reactive)
        self._futures[goal_id] = future

        # Clean up future reference when done
        future.add_done_callback(lambda f, gid=goal_id: self._on_goal_done(gid, f))
        return True

    def _on_goal_done(self, goal_id: str, future: Future) -> None:
        """Callback when a goal worker finishes (success or failure)."""
        with self._submitted_lock:
            self._submitted.discard(goal_id)
        self._futures.pop(goal_id, None)

        if future.cancelled():
            logger.info("Goal %s worker was cancelled (shutdown)", goal_id)
            return

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

            if self._on_clear_suggest_cooldown:
                self._on_clear_suggest_cooldown()
                logger.info(
                    "Cleared suggest cooldown — self-initiated goal %s "
                    "had %d task failure(s)",
                    goal_id, state.tasks_failed,
                )
        except Exception as e:
            logger.debug("Could not check suggest cooldown reset: %s", e)

    def _execute_goal(self, goal_id: str, reactive: bool = False) -> None:
        """Worker entry point: discover → decompose → execute → QA → notify.

        Delegates to phase-specific methods for testability.
        """
        goal_stop = threading.Event()
        with self._goal_flags_lock:
            self._goal_stop_flags[goal_id] = goal_stop

        state = GoalWorkerState(
            goal_id=goal_id, started_at=datetime.now(), reactive=reactive,
        )
        with self._states_lock:
            self._worker_states[goal_id] = state

        try:
            goal = self._goal_manager.goals.get(goal_id)
            if not goal:
                logger.warning("Goal %s not found in goal_manager", goal_id)
                state.error = "Goal not found"
                return

            discovery_brief = None
            if not goal.is_decomposed:
                discovery_brief, user_prefs, disc_cost = self._phase_discover(goal_id, goal)
                state.cost_spent += disc_cost
                state.discovery_cost = disc_cost
                if not self._phase_decompose(goal_id, goal, state, discovery_brief, user_prefs):
                    return

            state.status = WorkerStatus.EXECUTING
            orch_result = self._phase_execute(goal_id, state, goal_stop)

            integrator_summary = ""
            goal = self._goal_manager.goals.get(goal_id)
            if goal and goal.is_complete() and orch_result["tasks_completed"] > 0:
                integrator_summary = self._phase_qa_pipeline(
                    goal_id, goal, state, orch_result, goal_stop, discovery_brief,
                )

            goal = self._goal_manager.goals.get(goal_id)
            work_done = (orch_result["tasks_completed"] + orch_result["tasks_failed"]) > 0
            if goal and work_done:
                self._notify_goal_result(
                    goal, orch_result, state.cost_spent, self._per_goal_budget,
                    integrator_summary=integrator_summary,
                )
            elif goal and not work_done:
                logger.debug("[worker:%s] No tasks executed — skipping notification", goal_id)

            state.current_task_id = None
            state.status = WorkerStatus.DONE

        except Exception as e:
            logger.error("[worker:%s] Unhandled error: %s", goal_id, e, exc_info=True)
            state.error = str(e)
            state.status = WorkerStatus.DONE
        finally:
            with self._goal_flags_lock:
                self._goal_stop_flags.pop(goal_id, None)
            self._cleanup_stale_states()

    # -- Phase methods (called by _execute_goal) --

    def _phase_discover(self, goal_id: str, goal: Any):
        """Phase 1: Discovery scan + user model context.

        Returns (discovery_brief, user_prefs, cost).
        """
        discovery_brief = None
        user_prefs = None
        cost = 0.0

        try:
            from src.core.discovery import discover_project
            from src.utils.project_context import load as load_project_context
            project_context = load_project_context()
            disc_result = discover_project(
                goal_description=goal.description,
                project_context=project_context,
                router=self._router,
            )
            if disc_result:
                discovery_brief = disc_result["brief"]
                cost = disc_result.get("cost", 0)
                logger.info(
                    "[worker:%s] Discovery: %d files found, %d read, brief=%d chars ($%.4f)",
                    goal_id, disc_result["files_found"], disc_result["files_read"],
                    len(discovery_brief), cost,
                )
        except Exception as disc_err:
            logger.debug("[worker:%s] Discovery skipped: %s", goal_id, disc_err)

        try:
            from src.core.user_model import get_user_model
            um = get_user_model()
            if um:
                user_prefs = um.get_context_for_decomposition() or None
        except Exception as um_err:
            logger.debug("[worker:%s] User model skipped: %s", goal_id, um_err)

        return discovery_brief, user_prefs, cost

    def _phase_decompose(
        self, goal_id: str, goal: Any, state: GoalWorkerState,
        discovery_brief: Optional[str], user_prefs: Optional[str],
    ) -> bool:
        """Phase 2: Architect decomposition. Returns True on success."""
        state.status = WorkerStatus.DECOMPOSING
        logger.info("[worker:%s] Architect decomposing: %s", goal_id, goal.description[:80])
        _start = time.monotonic()
        try:
            self._goal_manager.decompose_goal(
                goal_id, self._router,
                learning_hints=self._learning_system.get_active_insights(2),
                discovery_brief=discovery_brief,
                user_prefs=user_prefs,
            )
            elapsed = time.monotonic() - _start
            logger.info("[worker:%s] Decomposition complete (%.1fs)", goal_id, elapsed)
            if elapsed > 120:
                logger.warning(
                    "[worker:%s] Decomposition took %.0fs — possible freeze detected",
                    goal_id, elapsed,
                )
            return True
        except Exception as e:
            logger.error("[worker:%s] Decomposition failed: %s", goal_id, e)
            state.error = f"Decomposition failed: {e}"
            try:
                from src.interfaces.discord_bot import send_notification
                from src.core.notification_formatter import format_decomposition_failure
                fmt = format_decomposition_failure(goal.description, self._router)
                send_notification(fmt["message"])
            except Exception as notify_err:
                logger.debug(
                    "[worker:%s] Decomposition failure notification skipped: %s",
                    goal_id, notify_err,
                )
            return False

    def _phase_execute(
        self, goal_id: str, state: GoalWorkerState, goal_stop: threading.Event,
    ) -> Dict[str, Any]:
        """Phase 3: Resume in-progress tasks + DAG-scheduled execution.

        Returns the orchestrator result dict. Updates state in place.
        """
        goal = self._goal_manager.goals.get(goal_id)
        if goal:
            for task in goal.tasks:
                if self._is_cancelled(goal_stop):
                    break
                if task.status == TaskStatus.IN_PROGRESS:
                    logger.info("[worker:%s] Resuming task: %s", goal_id, task.task_id)
                    try:
                        result = execute_task(
                            task, self._goal_manager, self._router,
                            self._learning_system, self._overnight_results,
                            self._save_overnight_results, memory=self._memory,
                        )
                        state.cost_spent += result.get("cost_usd", 0)
                        self._goal_manager.complete_task(task.task_id, result)
                        state.tasks_completed += 1
                    except Exception as e:
                        logger.error("[worker:%s] Resume failed: %s", goal_id, e)
                        self._goal_manager.fail_task(task.task_id, str(e))
                        state.tasks_failed += 1

        orchestrator = TaskOrchestrator()
        orch_result = orchestrator.execute_goal_tasks(
            goal_id=goal_id, goal_manager=self._goal_manager,
            execute_task_fn=execute_task, router=self._router,
            learning_system=self._learning_system,
            overnight_results=self._overnight_results,
            save_overnight_results=self._save_overnight_results,
            stop_flag=goal_stop,
            budget_remaining=self._per_goal_budget - state.cost_spent,
            memory=self._memory,
        )
        state.cost_spent += orch_result["total_cost"]
        state.tasks_completed += orch_result["tasks_completed"]
        state.tasks_failed += orch_result["tasks_failed"]
        return orch_result

    def _build_qa_context(self, goal: Any):
        """Gather goal results, files, and task dicts for QA pipeline stages.

        Returns (goal_results, all_files, task_dicts).
        """
        goal_results = [
            r for r in self._overnight_results
            if r.get("goal", "") == goal.description
        ]
        all_files: List[str] = []
        for r in goal_results:
            all_files.extend(r.get("files_created", []))

        task_dicts = []
        for task in goal.tasks:
            td = {
                "description": task.description,
                "result": task.result or {},
                "files_to_create": task.files_to_create,
                "expected_output": task.expected_output,
                "interfaces": task.interfaces,
            }
            for r in goal_results:
                if r.get("task", "") == task.description:
                    td["result"] = {
                        **td["result"],
                        "success": r.get("success", False),
                        "summary": r.get("summary", ""),
                        "files_created": r.get("files_created", []),
                    }
                    break
            task_dicts.append(td)

        return goal_results, all_files, task_dicts

    def _phase_qa_pipeline(
        self, goal_id: str, goal: Any, state: GoalWorkerState,
        orch_result: Dict[str, Any], goal_stop: threading.Event,
        discovery_brief: Optional[str],
    ) -> str:
        """Phase 4: Integrator → Goal QA → Critic → Remediation.

        Returns integrator_summary. Updates state and orch_result in place.
        """
        goal_results, all_files, task_dicts = self._build_qa_context(goal)
        integrator_summary = ""

        # 1. Integrator — cross-task synthesis
        try:
            from src.core.integrator import integrate_goal
            int_result = integrate_goal(
                goal_description=goal.description, tasks=task_dicts,
                files_created=all_files, router=self._router,
                discovery_brief=discovery_brief,
            )
            integrator_summary = int_result.get("summary", "")
            state.cost_spent += int_result.get("cost", 0)
            if int_result["issues_found"]:
                logger.info(
                    "[worker:%s] Integrator found %d issue(s): %s",
                    goal_id, len(int_result["issues_found"]),
                    "; ".join(i[:60] for i in int_result["issues_found"][:3]),
                )
            if integrator_summary:
                logger.info("[worker:%s] Integrator summary: %s", goal_id, integrator_summary[:200])
        except Exception as int_err:
            logger.debug("[worker:%s] Integrator skipped: %s", goal_id, int_err)

        # 2. Goal-level QA — conformance check
        try:
            from src.core.qa_evaluator import evaluate_goal as qa_evaluate_goal
            goal_qa = qa_evaluate_goal(
                goal_description=goal.description, tasks=task_dicts,
                files_created=all_files, integrator_summary=integrator_summary,
                router=self._router,
            )
            state.cost_spent += goal_qa.get("cost", 0)
            if goal_qa["verdict"] == "reject" and goal_qa["issues"]:
                from src.core.qa_evaluator import format_issues
                _formatted = format_issues(goal_qa["issues"])
                logger.info(
                    "[worker:%s] Goal QA rejected: %s",
                    goal_id, "; ".join(i[:60] for i in _formatted[:3]),
                )
                self._record_qa_rejection(goal_id, goal, _formatted)
            else:
                logger.info("[worker:%s] Goal QA: accepted", goal_id)
        except Exception as qa_err:
            logger.debug("[worker:%s] Goal-level QA skipped: %s", goal_id, qa_err)

        # 3. Critic — adversarial review + remediation
        try:
            from src.core.critic import critique_goal
            critic_result = critique_goal(
                goal_description=goal.description, task_results=goal_results,
                files_created=all_files, router=self._router,
            )
            state.cost_spent += critic_result.get("cost", 0)
            self._handle_critic_result(
                goal_id, goal, state, orch_result, critic_result, goal_stop,
            )
        except Exception as crit_err:
            logger.debug("[worker:%s] Critic skipped: %s", goal_id, crit_err)

        return integrator_summary

    def _record_qa_rejection(self, goal_id: str, goal: Any, formatted_issues: list) -> None:
        """Record QA failure in idea history for picked suggestions."""
        if "picked suggestion" not in (goal.user_intent or "").lower():
            return
        try:
            from src.core.idea_history import get_idea_history
            reason = "QA rejected: " + "; ".join(i[:80] for i in formatted_issues[:3])
            get_idea_history().record_auto_filtered(goal.description, reason, "QA")
        except Exception as hist_err:
            logger.debug("[worker:%s] QA auto-filter record skipped: %s", goal_id, hist_err)

    def _handle_critic_result(
        self, goal_id: str, goal: Any, state: GoalWorkerState,
        orch_result: Dict[str, Any], critic_result: Dict[str, Any],
        goal_stop: threading.Event,
    ) -> None:
        """Process critic verdict: remediation for significant, log for minor."""
        if critic_result["severity"] == "significant" and critic_result["remediation_tasks"]:
            logger.info(
                "[worker:%s] Critic found significant concerns — adding %d remediation task(s)",
                goal_id, len(critic_result["remediation_tasks"]),
            )
            # Find last completed task to chain remediation after
            completed = [t for t in goal.tasks if t.status.value == "completed"]
            after_id = completed[-1].task_id if completed else goal.tasks[-1].task_id
            self._goal_manager.add_follow_up_tasks(
                goal_id=goal_id, task_descriptions=critic_result["remediation_tasks"],
                after_task_id=after_id,
            )
            _rem_orch = TaskOrchestrator()
            _rem_result = _rem_orch.execute_goal_tasks(
                goal_id=goal_id, goal_manager=self._goal_manager,
                execute_task_fn=execute_task, router=self._router,
                learning_system=self._learning_system,
                overnight_results=self._overnight_results,
                save_overnight_results=self._save_overnight_results,
                stop_flag=goal_stop,
                budget_remaining=self._per_goal_budget - state.cost_spent,
                memory=self._memory,
            )
            state.cost_spent += _rem_result["total_cost"]
            state.tasks_completed += _rem_result["tasks_completed"]
            state.tasks_failed += _rem_result["tasks_failed"]
            orch_result["tasks_completed"] += _rem_result["tasks_completed"]
            orch_result["tasks_failed"] += _rem_result["tasks_failed"]
            logger.info(
                "[worker:%s] Remediation pass: %d completed, %d failed",
                goal_id, _rem_result["tasks_completed"], _rem_result["tasks_failed"],
            )
        elif critic_result["severity"] == "minor":
            from src.core.critic import format_concerns
            _formatted = format_concerns(critic_result["concerns"])
            logger.info(
                "[worker:%s] Critic: minor concerns — %s",
                goal_id, "; ".join(c[:60] for c in _formatted[:3]),
            )
        else:
            logger.info("[worker:%s] Critic: no concerns", goal_id)

    def _is_cancelled(self, goal_stop: threading.Event) -> bool:
        """Check whether the pool is shutting down or the goal was cancelled."""
        return self._stop.is_set() or goal_stop.is_set()

    def _cleanup_stale_states(self, max_age_secs: float = 3600) -> None:
        """Remove DONE worker states older than *max_age_secs* (default 1 h).

        Called after each goal finishes so the dict doesn't grow indefinitely.
        Recent DONE states are kept for status queries / Discord display.
        """
        now = datetime.now()
        stale: list[str] = []
        with self._states_lock:
            for gid, ws in self._worker_states.items():
                if ws.status != WorkerStatus.DONE:
                    continue
                if ws.started_at and (now - ws.started_at).total_seconds() > max_age_secs:
                    stale.append(gid)
            for gid in stale:
                del self._worker_states[gid]
        if stale:
            logger.debug("Cleaned up %d stale worker state(s)", len(stale))

    def _gather_goal_summaries(
        self, goal: Any, integrator_summary: str = "",
    ) -> tuple:
        """Collect task summaries and files for a goal's notification.

        Returns (summaries, files) where summaries is a list of "Done:" texts
        (or a single integrator summary if available), and files is a flat list
        of all created file paths.
        """
        goal_results = [
            r for r in self._overnight_results
            if r.get("goal", "") == goal.description
        ]
        summaries = []
        files = []
        for r in goal_results:
            raw = r.get("summary", "")
            if "Done: " in raw:
                done_text = raw.split("Done: ", 1)[1].strip()
                if len(done_text) > 20:
                    summaries.append(done_text)
            files.extend(r.get("files_created", []))
        # Integrator summary replaces individual task summaries when available
        if integrator_summary and len(integrator_summary) > 20:
            summaries = [integrator_summary]
        return summaries, files

    def _is_goal_significant(
        self, goal: Any, orch_result: Dict[str, Any],
    ) -> bool:
        """Whether a goal warrants a feedback prompt (≥3 tasks or ≥10 min)."""
        total_tasks = orch_result["tasks_completed"] + orch_result["tasks_failed"]
        if total_tasks >= 3:
            return True
        state = self._worker_states.get(goal.goal_id)
        if state and state.started_at:
            elapsed = (datetime.now() - state.started_at).total_seconds()
            if elapsed >= 600:
                return True
        return False

    def _notify_goal_result(
        self, goal: Any, orch_result: Dict[str, Any],
        total_cost: float, budget_limit: float,
        integrator_summary: str = "",
    ) -> None:
        """Send ONE consolidated Discord notification for a goal's outcome."""
        from src.interfaces.discord_bot import send_notification
        from src.core.notification_formatter import format_goal_completion

        completed = orch_result["tasks_completed"]
        failed = orch_result["tasks_failed"]
        _intent = (goal.user_intent or "").lower()
        is_user_requested = _intent.startswith("user ")

        summaries, files = self._gather_goal_summaries(goal, integrator_summary)

        fmt = format_goal_completion(
            goal_description=goal.description,
            tasks_completed=completed,
            tasks_failed=failed,
            total_cost=total_cost,
            task_summaries=summaries,
            files_created=files,
            is_user_requested=is_user_requested,
            hit_budget=total_cost >= budget_limit,
            is_significant=self._is_goal_significant(goal, orch_result),
            router=self._router,
        )

        _track = {
            "goal": goal.description[:200],
            "event": "goal_completion",
            "summary": summaries[0][:200] if summaries else "",
            "tasks_completed": completed,
            "tasks_failed": failed,
            "user_requested": is_user_requested,
        }

        try:
            send_notification(fmt["message"], track_context=_track)
            self.last_goal_notification_time = time.monotonic()  # session 194
        except Exception as notify_err:
            logger.warning("[worker:%s] Goal completion notification failed: %s", goal.goal_id, notify_err)

    # -- Public API --

    def cancel_goal(self, goal_id: str) -> bool:
        """Request cancellation of a goal.

        Sets the stop flag for the worker (if running) and removes from pending.
        Returns True if the goal was found and cancel was initiated.
        """
        with self._submitted_lock:
            was_submitted = goal_id in self._submitted

        if was_submitted:
            # Try to cancel queued-but-not-started futures first
            future = self._futures.get(goal_id)
            if future and future.cancel():
                logger.info("Cancelled pending goal %s", goal_id)
                with self._submitted_lock:
                    self._submitted.discard(goal_id)
                return True
            # Already running — set the per-goal stop flag so the worker
            # bails out at the next phase/task boundary.
            with self._goal_flags_lock:
                flag = self._goal_stop_flags.get(goal_id)
            if flag:
                flag.set()
                logger.info("Set cancel flag for running goal %s", goal_id)
            else:
                logger.info("Goal %s running but no stop flag found", goal_id)
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

    def shutdown(self, timeout: float = 10.0) -> None:
        """Shut down the worker pool.

        By the time this is called, ArchiService has already closed the
        LLM client HTTP transports — so any in-flight API calls will
        fail immediately with a transport-closed exception, unblocking
        worker threads.  We wait briefly for them to finish.
        """
        active = []
        with self._states_lock:
            for gid, ws in self._worker_states.items():
                if ws.status in (WorkerStatus.EXECUTING, WorkerStatus.DECOMPOSING):
                    active.append(gid)

        if active:
            logger.info(
                "GoalWorkerPool shutting down — %d active worker(s): %s",
                len(active), ", ".join(active),
            )
        else:
            logger.info("GoalWorkerPool shutting down (no active workers)")

        self._stop.set()

        # Set all per-goal stop flags so running workers exit promptly
        with self._goal_flags_lock:
            for flag in self._goal_stop_flags.values():
                flag.set()

        # Also trigger PlanExecutor's cancellation so running tasks bail out
        # at their next step boundary instead of continuing the full loop.
        try:
            from src.core.plan_executor import signal_task_cancellation
            signal_task_cancellation("shutdown")
        except ImportError:
            logger.debug("signal_task_cancellation unavailable during shutdown")

        # Wait for workers — they should unblock quickly since the HTTP
        # transports are already closed.  cancel_futures=True drops any
        # queued-but-not-started work.
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._reactive_executor.shutdown(wait=True, cancel_futures=True)

        logger.info("GoalWorkerPool shut down")
