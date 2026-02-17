"""
Task Orchestrator — Wave-based parallel task execution within a goal.

Instead of executing tasks one at a time, the orchestrator identifies
independent tasks (no mutual dependencies) and runs them simultaneously
in a ThreadPoolExecutor.  Tasks are grouped into "waves":

  Wave 1: all tasks with no unmet dependencies → run in parallel
  Wave 2: tasks whose deps were all in Wave 1 → run in parallel
  ...and so on until the goal is complete or budget is exhausted.

Same API cost, faster wall-clock time.  Three independent 2-minute
tasks complete in 2 min instead of 6 min.

Created in session 35 (layered orchestration architecture).
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

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
    """Wave-based parallel task execution for a single goal.

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
        """Execute all tasks in a goal using wave-based parallel execution.

        Returns:
            dict with total_cost, tasks_completed, tasks_failed, waves_executed
        """
        total_cost = 0.0
        tasks_completed = 0
        tasks_failed = 0
        waves_executed = 0

        # Accumulated context from completed tasks — fed into later waves
        goal_task_context: List[str] = []

        while not stop_flag.is_set():
            # Budget check
            if total_cost >= budget_remaining:
                logger.warning(
                    "[orchestrator:%s] Budget exhausted ($%.4f >= $%.2f)",
                    goal_id, total_cost, budget_remaining,
                )
                break

            # Get all tasks ready to run (dependencies met)
            goal = goal_manager.goals.get(goal_id)
            if not goal:
                logger.warning("[orchestrator:%s] Goal not found", goal_id)
                break

            ready_tasks = goal.get_ready_tasks()
            if not ready_tasks:
                logger.info("[orchestrator:%s] No more ready tasks", goal_id)
                break

            waves_executed += 1
            wave_size = len(ready_tasks)

            if wave_size > 1 and self._max_parallel > 1:
                logger.info(
                    "[orchestrator:%s] WAVE %d: %d tasks in PARALLEL",
                    goal_id, waves_executed, wave_size,
                )
            else:
                logger.info(
                    "[orchestrator:%s] WAVE %d: %d task(s) sequential",
                    goal_id, waves_executed, wave_size,
                )

            # Mark all wave tasks as in-progress
            for task in ready_tasks:
                try:
                    goal_manager.start_task(task.task_id)
                except Exception as e:
                    logger.error(
                        "[orchestrator:%s] Failed to start task %s: %s",
                        goal_id, task.task_id, e,
                    )

            # Execute the wave
            wave_results = self._execute_wave(
                tasks=ready_tasks,
                goal_id=goal_id,
                goal_manager=goal_manager,
                execute_task_fn=execute_task_fn,
                router=router,
                learning_system=learning_system,
                overnight_results=overnight_results,
                save_overnight_results=save_overnight_results,
                goal_task_context=goal_task_context,
                memory=memory,
            )

            # Harvest results
            wave_had_failure = False
            for task in ready_tasks:
                result = wave_results.get(task.task_id, {})

                if result.get("error") and not result.get("executed"):
                    # Hard failure (exception in execute_task)
                    error_msg = result.get("error", "Unknown error")
                    logger.error(
                        "[orchestrator:%s] Task %s FAILED: %s",
                        goal_id, task.task_id, error_msg,
                    )
                    try:
                        goal_manager.fail_task(task.task_id, error_msg)
                    except Exception:
                        pass
                    tasks_failed += 1
                    wave_had_failure = True
                else:
                    # Task completed (may or may not have been "successful")
                    cost = result.get("cost_usd", 0)
                    analysis = result.get("analysis", "")
                    total_cost += cost

                    try:
                        goal_manager.complete_task(task.task_id, result)
                    except Exception as e:
                        logger.error(
                            "[orchestrator:%s] Failed to complete task %s: %s",
                            goal_id, task.task_id, e,
                        )
                    tasks_completed += 1

                    # Accumulate context for next wave's sibling hints
                    if analysis and analysis != "No steps executed":
                        goal_task_context.append(
                            f"[{task.description[:80]}] {analysis[:200]}"
                        )

                    logger.info(
                        "[orchestrator:%s] Task %s done ($%.4f, wave %d)",
                        goal_id, task.task_id, cost, waves_executed,
                    )

            # If any task in the wave failed, stop the goal
            # (downstream tasks likely depend on the failed one)
            if wave_had_failure:
                logger.warning(
                    "[orchestrator:%s] Stopping after wave %d failure",
                    goal_id, waves_executed,
                )
                break

        logger.info(
            "[orchestrator:%s] Done: %d waves, %d completed, %d failed, $%.4f",
            goal_id, waves_executed, tasks_completed, tasks_failed, total_cost,
        )

        return {
            "total_cost": total_cost,
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "waves_executed": waves_executed,
        }

    def _execute_wave(
        self,
        tasks: List[Task],
        goal_id: str,
        goal_manager: GoalManager,
        execute_task_fn: Callable,
        router: Any,
        learning_system: Any,
        overnight_results: List[Dict[str, Any]],
        save_overnight_results: Callable,
        goal_task_context: List[str],
        memory: Any,
    ) -> Dict[str, Dict[str, Any]]:
        """Execute a wave of tasks, potentially in parallel.

        All tasks in the wave receive the same sibling context snapshot
        (from previous waves). They can't see each other's results.

        Returns dict mapping task_id → execution result.
        """
        # Snapshot context BEFORE the wave — all tasks in this wave see the same history
        context_snapshot = list(goal_task_context)
        results: Dict[str, Dict[str, Any]] = {}

        if len(tasks) == 1 or self._max_parallel <= 1:
            # Sequential execution (single task or parallelism disabled)
            for task in tasks:
                results[task.task_id] = self._run_single_task(
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
            return results

        # Parallel execution
        effective_workers = min(self._max_parallel, len(tasks))
        logger.info(
            "[orchestrator:%s] Fanning out %d tasks across %d threads",
            goal_id, len(tasks), effective_workers,
        )

        with ThreadPoolExecutor(
            max_workers=effective_workers,
            thread_name_prefix=f"wave-{goal_id[:12]}",
        ) as wave_pool:
            futures = {}
            for task in tasks:
                future = wave_pool.submit(
                    self._run_single_task,
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
                futures[future] = task.task_id

            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    results[task_id] = future.result()
                except Exception as e:
                    logger.error(
                        "[orchestrator:%s] Task %s raised: %s",
                        goal_id, task_id, e, exc_info=True,
                    )
                    results[task_id] = {
                        "executed": False,
                        "error": str(e),
                        "cost_usd": 0,
                        "analysis": "",
                    }

        return results

    @staticmethod
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
