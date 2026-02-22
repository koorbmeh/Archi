"""
Goal Worker Pool — Concurrent goal execution via ThreadPoolExecutor.

Replaces the old single-threaded dream cycle executor with a pool that
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

    def _execute_goal(self, goal_id: str, reactive: bool = False) -> None:
        """Worker entry point: discover + decompose + execute all tasks for one goal.

        Phase 5 pipeline:
        1. Discovery: scan project files to build a context brief
        2. Architect: decompose goal into tasks with concrete specs
        3. DAG Scheduler: execute tasks event-driven as dependencies clear
        4. Critic: adversarial review and optional remediation pass

        Args:
            goal_id: Goal to execute.
            reactive: True for user-requested goals (logged for priority tracking).
        """
        from src.interfaces.discord_bot import send_notification

        state = GoalWorkerState(
            goal_id=goal_id, started_at=datetime.now(), reactive=reactive,
        )
        with self._states_lock:
            self._worker_states[goal_id] = state

        _goal_cost = 0.0

        try:
            # --- Phase 0: Validate goal ---
            goal = self._goal_manager.goals.get(goal_id)
            if not goal:
                logger.warning("Goal %s not found in goal_manager", goal_id)
                state.error = "Goal not found"
                return

            discovery_brief = None  # Set at top scope for Integrator access

            if not goal.is_decomposed:
                # --- Phase 1: Discovery (Phase 5) ---
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
                        disc_cost = disc_result.get("cost", 0)
                        _goal_cost += disc_cost
                        state.discovery_cost = disc_cost
                        state.cost_spent = _goal_cost
                        logger.info(
                            "[worker:%s] Discovery: %d files found, %d read, brief=%d chars ($%.4f)",
                            goal_id,
                            disc_result["files_found"],
                            disc_result["files_read"],
                            len(discovery_brief),
                            disc_cost,
                        )
                except Exception as disc_err:
                    logger.debug("[worker:%s] Discovery skipped: %s", goal_id, disc_err)

                # --- Phase 1.5: User Model context ---
                user_prefs = None
                try:
                    from src.core.user_model import get_user_model
                    um = get_user_model()
                    if um:
                        prefs_parts = []
                        if um.preferences:
                            prefs_parts.append("Jesse's known preferences:")
                            for p in um.preferences[-5:]:
                                _val = p.get("value", p.get("text", str(p)))
                                _key = p.get("key", p.get("category", ""))
                                prefs_parts.append(f"  - {_key}: {_val}" if _key else f"  - {_val}")
                        if um.corrections:
                            prefs_parts.append("Past corrections from Jesse:")
                            for c in um.corrections[-3:]:
                                _txt = c.get("text", c.get("value", str(c)))
                                prefs_parts.append(f"  - {_txt}")
                        if prefs_parts:
                            user_prefs = "\n".join(prefs_parts)
                except Exception as um_err:
                    logger.debug("[worker:%s] User model skipped: %s", goal_id, um_err)

                # --- Phase 2: Architect (Decompose with specs) ---
                state.status = WorkerStatus.DECOMPOSING
                logger.info("[worker:%s] Architect decomposing: %s", goal_id, goal.description[:80])
                _decomp_start = time.monotonic()
                try:
                    self._goal_manager.decompose_goal(
                        goal_id,
                        self._router,
                        learning_hints=self._learning_system.get_active_insights(2),
                        discovery_brief=discovery_brief,
                        user_prefs=user_prefs,
                    )
                    _decomp_elapsed = time.monotonic() - _decomp_start
                    logger.info(
                        "[worker:%s] Decomposition complete (%.1fs)",
                        goal_id, _decomp_elapsed,
                    )
                    if _decomp_elapsed > 120:
                        logger.warning(
                            "[worker:%s] Decomposition took %.0fs — possible freeze detected",
                            goal_id, _decomp_elapsed,
                        )
                except Exception as e:
                    logger.error("[worker:%s] Decomposition failed: %s", goal_id, e)
                    state.error = f"Decomposition failed: {e}"
                    try:
                        from src.core.notification_formatter import format_decomposition_failure
                        fmt = format_decomposition_failure(goal.description, self._router)
                        send_notification(fmt["message"])
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

            # Event-driven DAG task execution (Phase 5, session 53)
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

            # ── Phase 6 post-completion pipeline ─────────────────
            # Integrator → Goal QA → Critic → Notify
            # Each stage adds cost and may add remediation tasks.
            integrator_summary = ""
            goal = self._goal_manager.goals.get(goal_id)
            if goal and goal.is_complete() and orch_result["tasks_completed"] > 0:
                # Gather common data for all post-completion stages
                _goal_results = [
                    r for r in self._overnight_results
                    if r.get("goal", "") == goal.description
                ]
                _all_files = []
                for r in _goal_results:
                    _all_files.extend(r.get("files_created", []))

                # Build task dicts with spec fields for Integrator + Goal QA
                _task_dicts = []
                for task in goal.tasks:
                    _td = {
                        "description": task.description,
                        "result": task.result or {},
                        "files_to_create": task.files_to_create,
                        "expected_output": task.expected_output,
                        "interfaces": task.interfaces,
                    }
                    # Merge overnight_results data into result
                    for r in _goal_results:
                        if r.get("task", "") == task.description:
                            _td["result"] = {
                                **_td["result"],
                                "success": r.get("success", False),
                                "summary": r.get("summary", ""),
                                "files_created": r.get("files_created", []),
                            }
                            break
                    _task_dicts.append(_td)

                # 1. Integrator (Phase 6) — cross-task synthesis
                try:
                    from src.core.integrator import integrate_goal
                    int_result = integrate_goal(
                        goal_description=goal.description,
                        tasks=_task_dicts,
                        files_created=_all_files,
                        router=self._router,
                        discovery_brief=discovery_brief,
                    )
                    integrator_summary = int_result.get("summary", "")
                    _goal_cost += int_result.get("cost", 0)
                    state.cost_spent = _goal_cost

                    if int_result["issues_found"]:
                        logger.info(
                            "[worker:%s] Integrator found %d issue(s): %s",
                            goal_id, len(int_result["issues_found"]),
                            "; ".join(i[:60] for i in int_result["issues_found"][:3]),
                        )
                    if integrator_summary:
                        logger.info(
                            "[worker:%s] Integrator summary: %s",
                            goal_id, integrator_summary[:200],
                        )
                except Exception as int_err:
                    logger.debug("[worker:%s] Integrator skipped: %s", goal_id, int_err)

                # 2. Goal-level QA (Phase 6) — conformance check
                try:
                    from src.core.qa_evaluator import evaluate_goal as qa_evaluate_goal
                    goal_qa = qa_evaluate_goal(
                        goal_description=goal.description,
                        tasks=_task_dicts,
                        files_created=_all_files,
                        integrator_summary=integrator_summary,
                        router=self._router,
                    )
                    _goal_cost += goal_qa.get("cost", 0)
                    state.cost_spent = _goal_cost

                    if goal_qa["verdict"] == "reject" and goal_qa["issues"]:
                        logger.info(
                            "[worker:%s] Goal QA rejected: %s",
                            goal_id, "; ".join(i[:60] for i in goal_qa["issues"][:3]),
                        )
                        # Record QA failure in idea history so future brainstorms
                        # know this idea was tried and the implementation failed.
                        if "picked suggestion" in (goal.user_intent or "").lower():
                            try:
                                from src.core.idea_history import IdeaHistory
                                reason = "QA rejected: " + "; ".join(
                                    i[:80] for i in goal_qa["issues"][:3]
                                )
                                IdeaHistory().record_auto_filtered(
                                    goal.description, reason, "QA"
                                )
                            except Exception:
                                pass
                    else:
                        logger.info("[worker:%s] Goal QA: accepted", goal_id)
                except Exception as qa_err:
                    logger.debug("[worker:%s] Goal-level QA skipped: %s", goal_id, qa_err)

                # 3. Critic (Phase 2, enhanced Phase 6 with User Model)
                try:
                    from src.core.critic import critique_goal
                    critic_result = critique_goal(
                        goal_description=goal.description,
                        task_results=_goal_results,
                        files_created=_all_files,
                        router=self._router,
                    )
                    _goal_cost += critic_result.get("cost", 0)
                    state.cost_spent = _goal_cost

                    if critic_result["severity"] == "significant" and critic_result["remediation_tasks"]:
                        logger.info(
                            "[worker:%s] Critic found significant concerns — adding %d remediation task(s)",
                            goal_id, len(critic_result["remediation_tasks"]),
                        )
                        _remediation_descs = critic_result["remediation_tasks"]
                        self._goal_manager.add_follow_up_tasks(
                            goal_id=goal_id,
                            task_descriptions=_remediation_descs,
                        )
                        # Re-run orchestrator for the remediation pass
                        _remediation_orch = TaskOrchestrator()
                        _rem_result = _remediation_orch.execute_goal_tasks(
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
                        _goal_cost += _rem_result["total_cost"]
                        state.cost_spent = _goal_cost
                        state.tasks_completed += _rem_result["tasks_completed"]
                        state.tasks_failed += _rem_result["tasks_failed"]
                        orch_result["tasks_completed"] += _rem_result["tasks_completed"]
                        orch_result["tasks_failed"] += _rem_result["tasks_failed"]
                        logger.info(
                            "[worker:%s] Remediation pass: %d completed, %d failed",
                            goal_id, _rem_result["tasks_completed"], _rem_result["tasks_failed"],
                        )
                    elif critic_result["severity"] == "minor":
                        logger.info(
                            "[worker:%s] Critic: minor concerns — %s",
                            goal_id, "; ".join(c[:60] for c in critic_result["concerns"][:3]),
                        )
                    else:
                        logger.info("[worker:%s] Critic: no concerns", goal_id)

                except Exception as crit_err:
                    logger.debug("[worker:%s] Critic skipped: %s", goal_id, crit_err)

            # Send ONE consolidated notification per goal (not per task)
            goal = self._goal_manager.goals.get(goal_id)
            if goal:
                self._notify_goal_result(
                    goal, orch_result, _goal_cost, self._per_goal_budget,
                    integrator_summary=integrator_summary,
                )

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

    def _notify_goal_result(
        self, goal: Any, orch_result: Dict[str, Any],
        total_cost: float, budget_limit: float,
        integrator_summary: str = "",
    ) -> None:
        """Send ONE consolidated Discord notification for a goal's outcome.

        Uses the Notification Formatter (Phase 3) for natural, varied messages.
        Falls back to deterministic formatting if the formatter model call fails.
        Phase 6: includes Integrator summary for richer notifications.
        """
        from src.interfaces.discord_bot import send_notification
        from src.core.notification_formatter import format_goal_completion

        completed = orch_result["tasks_completed"]
        failed = orch_result["tasks_failed"]
        hit_budget = total_cost >= budget_limit
        _intent = (goal.user_intent or "").lower()
        is_user_requested = _intent.startswith("user ")

        # Gather task results for this goal
        _goal_results = [
            r for r in self._overnight_results
            if r.get("goal", "") == goal.description
        ]

        # Extract "Done:" summaries and files
        _summaries = []
        _all_files = []
        for r in _goal_results:
            summary = r.get("summary", "")
            if "Done: " in summary:
                done_text = summary.split("Done: ", 1)[1].strip()
                if len(done_text) > 20:
                    _summaries.append(done_text)
            _all_files.extend(r.get("files_created", []))

        # Phase 6: If we have an Integrator summary, use it as the primary
        # summary instead of individual task "Done:" lines. It's more
        # coherent and describes how the pieces fit together.
        if integrator_summary and len(integrator_summary) > 20:
            _summaries = [integrator_summary]

        # Determine if this goal is "significant" (warrants feedback prompt)
        _elapsed = 0.0
        _state = self._worker_states.get(goal.goal_id)
        if _state and _state.started_at:
            _elapsed = (datetime.now() - _state.started_at).total_seconds()
        is_significant = (completed + failed >= 3) or (_elapsed >= 600)

        fmt = format_goal_completion(
            goal_description=goal.description,
            tasks_completed=completed,
            tasks_failed=failed,
            total_cost=total_cost,
            task_summaries=_summaries,
            files_created=_all_files,
            is_user_requested=is_user_requested,
            hit_budget=hit_budget,
            is_significant=is_significant,
            router=self._router,
        )

        # Build tracking context for reaction-based feedback
        _track = {
            "goal": goal.description[:200],
            "event": "goal_completion",
            "summary": _summaries[0][:200] if _summaries else "",
            "tasks_completed": completed,
            "tasks_failed": failed,
            "user_requested": is_user_requested,
        }

        try:
            send_notification(fmt["message"], track_context=_track)
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

        # Also trigger PlanExecutor's cancellation so running tasks bail out
        # at their next step boundary instead of continuing the full loop.
        try:
            from src.core.plan_executor import signal_task_cancellation
            signal_task_cancellation("shutdown")
        except ImportError:
            pass

        # Wait for workers — they should unblock quickly since the HTTP
        # transports are already closed.  cancel_futures=True drops any
        # queued-but-not-started work.
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._reactive_executor.shutdown(wait=True, cancel_futures=True)

        logger.info("GoalWorkerPool shut down")
