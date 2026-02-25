"""
Task Orchestrator — Event-driven DAG task execution within a goal.

Phase 5 enhancement: replaces wave-based batching with event-driven scheduling.
When a task completes, immediately checks which pending tasks are now unblocked
and submits them. A task in "wave 2" can start as soon as its dependency in
"wave 1" finishes, without waiting for all of wave 1 to complete.

Same API cost, better wall-clock time on goals with staggered dependencies.
Priority preemption is handled at the GoalWorkerPool level (session 58):
reactive goals use a dedicated executor so they start immediately.

Created session 35 (wave-based). Rewritten session 53 (Phase 5: event-driven DAG).
"""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Set

from src.core.goal_manager import GoalManager, Task, TaskStatus

logger = logging.getLogger(__name__)


def _get_orchestrator_config() -> Dict[str, Any]:
    """Load task orchestrator config from rules.yaml."""
    defaults = {"enabled": True, "max_parallel_tasks_per_goal": 2}
    try:
        import yaml
        from src.utils.paths import base_path_as_path as _base_path
        rules_path = _base_path() / "config" / "rules.yaml"
        with open(rules_path, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f) or {}
        cfg = rules.get("task_orchestrator", {})
        return {
            "enabled": cfg.get("enabled", True),
            "max_parallel_tasks_per_goal": min(
                int(cfg.get("max_parallel_tasks_per_goal", 2)), 4
            ),
        }
    except Exception:
        return defaults


class TaskOrchestrator:
    """Event-driven DAG task execution for a single goal.

    Instead of wave-based batching, tasks fire as soon as their dependencies
    complete. Uses a persistent ThreadPoolExecutor with as_completed() to
    detect task completions and immediately submit newly unblocked tasks.

    Usage:
        orchestrator = TaskOrchestrator()
        result = orchestrator.execute_goal_tasks(
            goal_id=goal_id,
            goal_manager=gm,
            execute_task_fn=execute_task,
            ...
        )
    """

    def __init__(self) -> None:
        cfg = _get_orchestrator_config()
        self._enabled = cfg["enabled"]
        self._max_parallel = cfg["max_parallel_tasks_per_goal"]
        if not self._enabled:
            self._max_parallel = 1  # Sequential fallback
        logger.info(
            "TaskOrchestrator initialized (parallel=%s, max_tasks=%d)",
            self._enabled, self._max_parallel,
        )

    def execute_goal_tasks(
        self,
        goal_id: str,
        goal_manager: GoalManager,
        execute_task_fn: Callable,
        router: Any,
        learning_system: Any,
        overnight_results: List[Dict[str, Any]],
        save_overnight_results: Callable,
        stop_flag: threading.Event,
        budget_remaining: float,
        memory: Any = None,
    ) -> Dict[str, Any]:
        """Execute all tasks in a goal using event-driven DAG scheduling.

        When a task completes, immediately checks for newly unblocked tasks
        and submits them — no waiting for wave boundaries.

        Returns:
            dict with total_cost, tasks_completed, tasks_failed
        """
        total_cost = 0.0
        tasks_completed = 0
        tasks_failed = 0
        consecutive_failures = 0

        # Accumulated context from completed tasks — fed into later tasks
        goal_task_context: List[str] = []

        # Track which tasks are currently running
        running_futures: Dict[Future, str] = {}  # future -> task_id

        effective_workers = max(1, self._max_parallel)

        with ThreadPoolExecutor(
            max_workers=effective_workers,
            thread_name_prefix=f"dag-{goal_id[:12]}",
        ) as pool:
            # Seed: submit all initially ready tasks
            self._submit_ready_tasks(
                pool=pool,
                running_futures=running_futures,
                goal_id=goal_id,
                goal_manager=goal_manager,
                execute_task_fn=execute_task_fn,
                router=router,
                learning_system=learning_system,
                overnight_results=overnight_results,
                save_overnight_results=save_overnight_results,
                goal_task_context=goal_task_context,
                memory=memory,
                budget_remaining=budget_remaining - total_cost,
            )

            if not running_futures:
                logger.info("[orchestrator:%s] No ready tasks to start", goal_id)
                return {"total_cost": 0, "tasks_completed": 0, "tasks_failed": 0}

            # Event loop: wait for completions, submit newly unblocked tasks
            while running_futures and not stop_flag.is_set():
                # Wait for at least one future to complete
                done_futures = set()
                for future in as_completed(running_futures):
                    done_futures.add(future)
                    task_id = running_futures[future]

                    # Harvest result
                    result = self._harvest_result(future, goal_id, task_id)
                    cost = result.get("cost_usd", 0)
                    total_cost += cost

                    _has_error = result.get("error") and not result.get("executed")
                    _task_failed = _has_error or not result.get("executed", False)

                    if _task_failed:
                        error_msg = result.get("error", "Task did not complete successfully")
                        logger.warning(
                            "[orchestrator:%s] Task %s FAILED: %s",
                            goal_id, task_id, error_msg,
                        )
                        try:
                            goal_manager.fail_task(task_id, error_msg)
                        except Exception as ft_err:
                            logger.debug("[orchestrator:%s] fail_task(%s) error: %s", goal_id, task_id, ft_err)
                        tasks_failed += 1
                        consecutive_failures += 1
                    else:
                        analysis = result.get("analysis", "")
                        try:
                            goal_manager.complete_task(task_id, result)
                        except Exception as e:
                            logger.error(
                                "[orchestrator:%s] Failed to complete task %s: %s",
                                goal_id, task_id, e,
                            )
                        tasks_completed += 1
                        consecutive_failures = 0

                        # Accumulate context for sibling hints
                        task = self._find_task(goal_manager, goal_id, task_id)
                        desc = task.description[:80] if task else task_id
                        if analysis and analysis != "No steps executed":
                            goal_task_context.append(
                                f"[{desc}] {analysis[:200]}"
                            )

                        logger.info(
                            "[orchestrator:%s] Task %s done ($%.4f)",
                            goal_id, task_id, cost,
                        )

                    # Break out of as_completed to re-check ready tasks
                    break

                # Remove completed futures
                for f in done_futures:
                    running_futures.pop(f, None)

                # Stop if budget exhausted
                if total_cost >= budget_remaining:
                    logger.warning(
                        "[orchestrator:%s] Budget exhausted ($%.4f >= $%.2f)",
                        goal_id, total_cost, budget_remaining,
                    )
                    break

                # Stop if too many consecutive failures (API down, etc.)
                if consecutive_failures >= 3:
                    logger.warning(
                        "[orchestrator:%s] Stopping — %d consecutive failures",
                        goal_id, consecutive_failures,
                    )
                    break

                # Don't submit new tasks if shutdown was requested
                if stop_flag.is_set():
                    break

                # Submit newly unblocked tasks
                self._submit_ready_tasks(
                    pool=pool,
                    running_futures=running_futures,
                    goal_id=goal_id,
                    goal_manager=goal_manager,
                    execute_task_fn=execute_task_fn,
                    router=router,
                    learning_system=learning_system,
                    overnight_results=overnight_results,
                    save_overnight_results=save_overnight_results,
                    goal_task_context=goal_task_context,
                    memory=memory,
                    budget_remaining=budget_remaining - total_cost,
                )

        logger.info(
            "[orchestrator:%s] Done: %d completed, %d failed, $%.4f",
            goal_id, tasks_completed, tasks_failed, total_cost,
        )

        return {
            "total_cost": total_cost,
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
        }

    def _submit_ready_tasks(
        self,
        pool: ThreadPoolExecutor,
        running_futures: Dict[Future, str],
        goal_id: str,
        goal_manager: GoalManager,
        execute_task_fn: Callable,
        router: Any,
        learning_system: Any,
        overnight_results: List[Dict[str, Any]],
        save_overnight_results: Callable,
        goal_task_context: List[str],
        memory: Any,
        budget_remaining: float,
    ) -> None:
        """Submit all currently ready (unblocked) tasks that aren't already running."""
        if budget_remaining <= 0:
            return

        goal = goal_manager.goals.get(goal_id)
        if not goal:
            return

        ready_tasks = goal.get_ready_tasks()
        already_running = set(running_futures.values())

        # Respect max_parallel: only submit up to the limit
        slots_available = self._max_parallel - len(running_futures)

        for task in ready_tasks:
            if slots_available <= 0:
                break
            if task.task_id in already_running:
                continue

            # Mark as in-progress and submit
            try:
                goal_manager.start_task(task.task_id)
            except Exception as e:
                logger.error(
                    "[orchestrator:%s] Failed to start task %s: %s",
                    goal_id, task.task_id, e,
                )
                continue

            # Snapshot context at submission time
            context_snapshot = list(goal_task_context)

            future = pool.submit(
                _run_single_task,
                task=task,
                goal_manager=goal_manager,
                execute_task_fn=execute_task_fn,
                router=router,
                learning_system=learning_system,
                overnight_results=overnight_results,
                save_overnight_results=save_overnight_results,
                sibling_context=context_snapshot,
                memory=memory,
            )
            running_futures[future] = task.task_id
            slots_available -= 1

            logger.info(
                "[orchestrator:%s] Submitted task %s (%d running)",
                goal_id, task.task_id, len(running_futures),
            )

    @staticmethod
    def _harvest_result(
        future: Future, goal_id: str, task_id: str,
    ) -> Dict[str, Any]:
        """Extract result from a completed future, handling exceptions."""
        try:
            return future.result()
        except Exception as e:
            logger.error(
                "[orchestrator:%s] Task %s raised: %s",
                goal_id, task_id, e, exc_info=True,
            )
            return {
                "executed": False,
                "error": str(e),
                "cost_usd": 0,
                "analysis": "",
            }

    @staticmethod
    def _find_task(
        goal_manager: GoalManager, goal_id: str, task_id: str,
    ) -> Optional[Task]:
        """Find a task by ID within a goal."""
        goal = goal_manager.goals.get(goal_id)
        if not goal:
            return None
        for t in goal.tasks:
            if t.task_id == task_id:
                return t
        return None


def _run_single_task(
    task: Task,
    goal_manager: GoalManager,
    execute_task_fn: Callable,
    router: Any,
    learning_system: Any,
    overnight_results: List[Dict[str, Any]],
    save_overnight_results: Callable,
    sibling_context: List[str],
    memory: Any,
) -> Dict[str, Any]:
    """Execute one task via execute_task_fn. Thin wrapper for thread safety."""
    return execute_task_fn(
        task=task,
        goal_manager=goal_manager,
        router=router,
        learning_system=learning_system,
        overnight_results=overnight_results,
        save_overnight_results=save_overnight_results,
        memory=memory,
        sibling_task_summaries=sibling_context,
    )
