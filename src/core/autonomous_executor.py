"""
Autonomous Executor — Task execution engine for heartbeat cycles.

Handles queued task processing, autonomous goal-driven execution,
task execution via PlanExecutor, and follow-up task extraction.
Split from dream_cycle.py (now heartbeat.py) in session 11. Reworked in session 31.
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from src.core.goal_manager import GoalManager, TaskStatus
from src.core.learning_system import LearningSystem
from src.utils.config import get_user_name


def _resolve_project_path(goal_description: str, task_description: str) -> Optional[str]:
    """Match a goal/task to an active project and return the project's workspace path.

    Reads active_projects from data/project_context.json and checks if the goal or
    task description mentions the project name, focus areas, or keywords.

    Returns e.g. "workspace/projects/Health_Optimization" or None.
    """
    try:
        from src.utils.project_context import load
        context = load()

        active_projects = context.get("active_projects", {})
        if not active_projects:
            return None

        combined = f"{goal_description} {task_description}".lower()

        for key, val in active_projects.items():
            if not isinstance(val, dict):
                continue
            project_path = val.get("path", "")
            if not project_path:
                continue

            keywords = [key.lower().replace("_", " ")]
            desc = val.get("description", "")
            if desc:
                keywords.append(desc.lower())
            for fa in val.get("focus_areas", []):
                keywords.append(fa.lower())

            if any(kw in combined for kw in keywords):
                return project_path

        return None
    except Exception:
        return None

logger = logging.getLogger(__name__)


def _parse_defer_delta(error_str: str):
    """Parse a deferral error string into a timedelta.

    Recognises 'tomorrow', '~2 hour'/'couple hour', '~1 hour'/'hour'.
    Falls back to 1 hour for unrecognised patterns.
    """
    from datetime import timedelta
    err = error_str.lower()
    if "tomorrow" in err:
        return timedelta(days=1)
    if "~2 hour" in err or "couple hour" in err:
        return timedelta(hours=2)
    return timedelta(hours=1)


def _get_dream_cycle_budget() -> float:
    """Load per-cycle budget limit via centralised config loader."""
    from src.utils.config import get_dream_cycle_budget
    return get_dream_cycle_budget()


def process_task_queue(
    task_queue: List[Dict[str, Any]],
    goal_manager: Optional[GoalManager],
    router: Any,
    learning_system: LearningSystem,
    stop_flag: Any,
    autonomous_mode: bool,
    overnight_results: List[Dict[str, Any]],
    save_overnight_results: Callable,
    memory: Any = None,
) -> int:
    """Process queued background tasks, then run autonomous goal-driven work.

    Returns number of tasks processed.
    """
    processed = 0

    # First, execute manual queue tasks
    while task_queue and not stop_flag.is_set():
        task = task_queue.pop(0)
        try:
            desc = task.get("description", "") or str(task.get("type", "unknown"))
            logger.info("Executing queued task: %s", desc)
            result = _execute_queued_task(task, router, goal_manager)
            if result.get("executed"):
                processed += 1
        except Exception as e:
            logger.error("Task processing error: %s", e)

    # Then, autonomous goal-driven work (needs router)
    if autonomous_mode and goal_manager and router:
        processed += _execute_autonomous_tasks(
            goal_manager=goal_manager,
            router=router,
            learning_system=learning_system,
            stop_flag=stop_flag,
            overnight_results=overnight_results,
            save_overnight_results=save_overnight_results,
            memory=memory,
        )

    return processed


def _execute_queued_task(
    task: Dict[str, Any],
    router: Any,
    goal_manager: Optional[GoalManager],
) -> Dict[str, Any]:
    """Execute a manual queue task via process_message."""
    if not router:
        return {"executed": False, "error": "Router not available"}

    desc = task.get("description", "") or str(task.get("type", "unknown"))
    message = f"Complete this task: {desc}"

    try:
        from src.interfaces.message_handler import process_message

        response_text, actions_taken, cost = process_message(
            message=message,
            router=router,
            history=[],
            source="dream_cycle_queue",
            goal_manager=goal_manager,
        )

        success = len(actions_taken) > 0
        return {
            "executed": success,
            "response": response_text,
            "actions_taken": [a.get("description", "") for a in actions_taken],
            "cost_usd": cost,
        }
    except Exception as e:
        logger.error("Queued task execution failed: %s", e)
        return {"executed": False, "error": str(e)}


def _get_max_parallel_tasks() -> int:
    """Load max_parallel_tasks from heartbeat config."""
    from src.utils.config import get_heartbeat_config
    return get_heartbeat_config().get("max_parallel_tasks", 3)


def _get_ready_wave(goal_manager: GoalManager, max_tasks: int) -> List[Any]:
    """Collect ready tasks across all goals for parallel execution.

    Returns up to max_tasks ready tasks, sorted by priority (highest first,
    user-requested goals boosted). Tasks within the returned wave are
    independent and can run concurrently.
    """
    with goal_manager._lock:
        all_ready = []
        for goal in goal_manager.goals.values():
            if goal.is_complete():
                continue
            all_ready.extend(goal.get_ready_tasks())

        if not all_ready:
            return []

        def _sort_key(t):
            goal = goal_manager.goals.get(t.goal_id)
            if not goal:
                return (1, -t.priority, 0)
            _intent = (goal.user_intent or "").lower()
            is_user = 0 if _intent.startswith("user ") else 1
            return (is_user, -t.priority, -goal.priority)

        all_ready.sort(key=_sort_key)
        return all_ready[:max_tasks]


def _resume_interrupted_tasks(
    goal_manager: GoalManager,
    router: Any,
    learning_system: LearningSystem,
    stop_flag: Any,
    overnight_results: List[Dict[str, Any]],
    save_overnight_results: Callable,
    memory: Any,
    max_tasks: int,
    cycle_budget: float,
) -> tuple:
    """Resume tasks that were in-progress when we crashed/restarted.

    Returns (executed_count, cycle_cost).
    """
    executed = 0
    cycle_cost = 0.0

    for goal in list(goal_manager.goals.values()):
        if stop_flag.is_set() or executed >= max_tasks:
            break
        for task in goal.tasks:
            if task.status == TaskStatus.IN_PROGRESS:
                logger.info("Resuming interrupted task: %s (%s)", task.description, task.task_id)
                try:
                    result = execute_task(
                        task, goal_manager, router, learning_system,
                        overnight_results, save_overnight_results,
                        memory=memory,
                    )
                    cycle_cost += result.get("cost_usd", 0)
                    goal_manager.complete_task(task.task_id, result)
                    goal_manager.save_state()
                    executed += 1
                    if cycle_cost >= cycle_budget:
                        logger.warning(
                            "Dream cycle budget hit ($%.4f >= $%.2f) during resume",
                            cycle_cost, cycle_budget,
                        )
                        return executed, cycle_cost
                except Exception as e:
                    logger.error("Interrupted task resume failed: %s", e)
                    goal_manager.fail_task(task.task_id, str(e))

    return executed, cycle_cost


def _decompose_pending_goals(
    goal_manager: GoalManager,
    router: Any,
    learning_system: LearningSystem,
    stop_flag: Any,
) -> int:
    """Decompose any undecomposed goals, returning count of goals decomposed."""
    total_goals = len(goal_manager.goals)
    undecomposed = [
        g for g in goal_manager.goals.values()
        if not g.is_decomposed and not g.is_complete()
    ]
    if total_goals == 0:
        logger.info("Dream cycle: no goals in goal_manager")
    elif not undecomposed:
        logger.info(
            "Dream cycle: %d goals but all decomposed or complete", total_goals,
        )

    decomposed_count = 0
    for goal in undecomposed[:5]:
        if stop_flag.is_set():
            break
        try:
            logger.info("Decomposing undecomposed goal: %s", goal.description)
            # Resolve project path and inject file listing so Architect uses full paths
            _decomp_brief = None
            _proj = _resolve_project_path(goal.description, goal.description)
            if _proj:
                try:
                    from src.utils.project_context import scan_project_files
                    _files = scan_project_files(_proj)
                    if _files:
                        _decomp_brief = (
                            f"Project path: {_proj}\n"
                            f"Existing files: {', '.join(_files[:20])}\n"
                            f"Use full paths like {_proj}/filename.ext in task descriptions."
                        )
                except Exception:
                    pass
            goal_manager.decompose_goal(
                goal.goal_id,
                router,
                learning_hints=learning_system.get_active_insights(2),
                discovery_brief=_decomp_brief,
            )
            goal_manager.save_state()
            decomposed_count += 1
            logger.info(
                "Decomposed goal '%s' into %d task(s)",
                goal.description, len(goal.tasks),
            )
        except Exception as e:
            logger.error("Goal decomposition failed: %s", e, exc_info=True)
    if decomposed_count:
        logger.info("Decomposed %d goals this cycle", decomposed_count)
    return decomposed_count


def _execute_autonomous_tasks(
    goal_manager: GoalManager,
    router: Any,
    learning_system: LearningSystem,
    stop_flag: Any,
    overnight_results: List[Dict[str, Any]],
    save_overnight_results: Callable,
    memory: Any = None,
) -> int:
    """Execute tasks from goal manager autonomously.

    Runs continuously until the time cap, cost cap, or task cap is reached.
    The per-cycle cost cap (from rules.yaml dream_cycle_budget) prevents a
    single hallucination loop from burning through the entire daily budget.
    """
    _dream_start = time.monotonic()
    _MAX_DREAM_MINUTES = 120  # API-only: let budget cap be the real safety net
    max_tasks_per_dream = 50  # Safety hard cap
    _cycle_budget = _get_dream_cycle_budget()
    # Accumulate per-goal task results so later tasks know what earlier ones did.
    _goal_task_context: Dict[str, List[str]] = {}
    # Cross-goal cycle observations — compressed notes from ALL completed tasks
    # this cycle, shared across goals so later goals don't repeat earlier work.
    _cycle_observations: List[str] = []

    # Phase 1: Resume interrupted tasks
    executed, _cycle_cost = _resume_interrupted_tasks(
        goal_manager, router, learning_system, stop_flag,
        overnight_results, save_overnight_results, memory,
        max_tasks_per_dream, _cycle_budget,
    )
    if _cycle_cost >= _cycle_budget:
        return executed

    # Phase 2: Decompose pending goals
    _decompose_pending_goals(goal_manager, router, learning_system, stop_flag)

    # Phase 3: Main execution loop — parallel wave-based (session 120)
    _consecutive_failures = 0
    _MAX_CONSECUTIVE_FAILURES = 3
    _max_parallel = _get_max_parallel_tasks()
    _cost_lock = threading.Lock()
    _results_lock = threading.Lock()

    while executed < max_tasks_per_dream and not stop_flag.is_set():
        _elapsed_min = (time.monotonic() - _dream_start) / 60.0
        if _elapsed_min >= _MAX_DREAM_MINUTES:
            logger.info(
                "Dream cycle time cap reached (%.1f min, %d tasks done)",
                _elapsed_min, executed,
            )
            break

        if _cycle_cost >= _cycle_budget:
            logger.warning(
                "Dream cycle budget reached ($%.4f >= $%.2f, %d tasks done)",
                _cycle_cost, _cycle_budget, executed,
            )
            break

        # Collect a wave of independent tasks
        remaining_slots = max_tasks_per_dream - executed
        wave_size = min(_max_parallel, remaining_slots)
        wave = _get_ready_wave(goal_manager, wave_size)
        if not wave:
            logger.info("No ready tasks to execute")
            break

        if len(wave) == 1:
            # Single task — run inline (no thread overhead)
            task = wave[0]
            logger.info("Autonomously executing: %s", task.description)
            _wave_result = _run_single_task(
                task, goal_manager, router, learning_system,
                overnight_results, save_overnight_results,
                memory, _goal_task_context, _results_lock,
                cycle_observations=_cycle_observations,
            )
            _cycle_cost += _wave_result.get("cost", 0)
            executed += 1
            # Accumulate cycle observation for cross-goal context
            _obs = _wave_result.get("observation")
            if _obs:
                _cycle_observations.append(_obs)
            if _wave_result.get("failed"):
                _consecutive_failures += 1
                if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    logger.warning(
                        "Stopping: %d consecutive task failures",
                        _consecutive_failures,
                    )
                    break
                time.sleep(1)
            else:
                _consecutive_failures = 0
        else:
            # Multiple independent tasks — execute in parallel
            logger.info(
                "Executing wave of %d parallel tasks: %s",
                len(wave),
                ", ".join(t.task_id for t in wave),
            )
            wave_cost, wave_executed, wave_failures, _wave_obs = _execute_wave(
                wave=wave,
                goal_manager=goal_manager,
                router=router,
                learning_system=learning_system,
                stop_flag=stop_flag,
                overnight_results=overnight_results,
                save_overnight_results=save_overnight_results,
                memory=memory,
                goal_task_context=_goal_task_context,
                cost_lock=_cost_lock,
                results_lock=_results_lock,
                max_workers=len(wave),
                cycle_observations=_cycle_observations,
            )
            # Accumulate wave observations for cross-goal context
            _cycle_observations.extend(_wave_obs)
            _cycle_cost += wave_cost
            executed += wave_executed

            if wave_failures >= wave_executed:
                # All tasks in wave failed
                _consecutive_failures += wave_failures
                if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    logger.warning(
                        "Stopping: %d consecutive task failures",
                        _consecutive_failures,
                    )
                    break
                time.sleep(1)
            else:
                _consecutive_failures = 0

        logger.info(
            "Wave done: %d tasks executed, $%.4f spent this cycle",
            executed, _cycle_cost,
        )

    return executed


def _safe_goal_desc(goal_manager: Optional[GoalManager], task: Any) -> str:
    """Safely extract goal description for a task, returning '' on any error."""
    try:
        if goal_manager and hasattr(task, "goal_id"):
            _g = goal_manager.goals.get(task.goal_id)
            return _g.description if _g else ""
    except Exception:
        pass
    return ""


def _locked_append(
    results_lock: Optional[threading.Lock],
    overnight_results: List[Dict[str, Any]],
    save_overnight_results: Callable,
    entry: Dict[str, Any],
) -> None:
    """Append an entry to overnight_results with optional lock protection."""
    if results_lock:
        results_lock.acquire()
    try:
        overnight_results.append(entry)
        save_overnight_results()
    except Exception as e:
        logger.warning("Failed to save overnight result: %s", e)
    finally:
        if results_lock:
            results_lock.release()


def _compress_task_observation(
    task_desc: str,
    goal_desc: str,
    result: Dict[str, Any],
) -> str:
    """Compress a completed task's result into a 1-line observation note.

    Used for sibling context (within-goal) and cycle observations (cross-goal).
    Captures: outcome, key deliverables, cost, and any notable issues.

    Example:
      "[DONE] Research vitamin D → Created protocol.md (5 steps, $0.03)"
      "[FAIL] Build tracker → Syntax error in step 4 ($0.01)"
    """
    success = result.get("executed", result.get("success", False))
    status = "DONE" if success else "FAIL"
    task_brief = task_desc[:60]
    cost = result.get("cost_usd", result.get("total_cost", 0))

    parts = [f"[{status}] {task_brief}"]

    # Add key deliverables
    files = result.get("files_created", [])
    if files:
        file_names = [os.path.basename(f) for f in files[:3]]
        parts.append(f"→ {', '.join(file_names)}")

    # Add step count and cost
    steps = result.get("steps_taken", [])
    step_count = len(steps) if isinstance(steps, list) else 0
    parts.append(f"({step_count} steps, ${cost:.3f})")

    # Add brief analysis if no files (research-only tasks)
    if not files:
        analysis = result.get("analysis", "")
        if analysis and analysis != "No steps executed":
            # Extract the Done: summary if present
            if "Done: " in analysis:
                done_text = analysis.split("Done: ", 1)[1][:80]
                parts.insert(1, f"→ {done_text}")

    return " ".join(parts)


def _cap_hints(
    hints: List[str],
    max_chars: int = 3000,
) -> List[str]:
    """Cap total hint size to a character budget, trimming lowest-priority first.

    Priority order (highest kept last → trimmed first):
      1. Architect spec hints (FILES TO CREATE, EXPECTED OUTPUT, etc.) — highest
      2. Sibling task context / cycle observations
      3. Project path / file mappings
      4. Prior research from memory
      5. Learning insights — lowest priority

    Trims from the beginning of the list (lowest priority hints appear first
    due to _gather_execution_hints ordering).
    """
    total = sum(len(h) for h in hints)
    if total <= max_chars:
        return hints

    # Categorize hints by priority for smart trimming
    HIGH_MARKERS = ("FILES TO CREATE:", "EXPECTED OUTPUT:", "INPUTS NEEDED:", "INTERFACES:")
    MED_MARKERS = ("EARLIER TASKS", "CYCLE CONTEXT", "FILE OUTPUT:", "FILES IN THIS PROJECT:", "CAUTION")
    LOW_MARKERS = ("PRIOR RESEARCH", "EXISTING FILES")

    def _priority(h: str) -> int:
        upper = h[:50].upper()
        if any(m in upper for m in HIGH_MARKERS):
            return 3
        if any(m in upper for m in MED_MARKERS):
            return 2
        if any(m in upper for m in LOW_MARKERS):
            return 1
        return 0  # Learning insights, action summaries, etc.

    # Sort by priority (low first), preserving order within same priority
    indexed = [(i, h, _priority(h)) for i, h in enumerate(hints)]
    indexed.sort(key=lambda x: (x[2], x[0]))

    # Trim from lowest priority until we fit
    kept_indices = set(range(len(hints)))
    running_total = total
    for idx, hint_text, prio in indexed:
        if running_total <= max_chars:
            break
        running_total -= len(hint_text)
        kept_indices.discard(idx)
        logger.debug("Hint trimmed (prio=%d, %d chars): %s...", prio, len(hint_text), hint_text[:40])

    # Return in original order
    result = [hints[i] for i in sorted(kept_indices)]

    trimmed_count = len(hints) - len(result)
    if trimmed_count:
        logger.info(
            "Hint budget: trimmed %d of %d hints (%d → %d chars)",
            trimmed_count, len(hints), total, sum(len(h) for h in result),
        )

    return result


def _build_step_summary(steps: List[Dict[str, Any]]) -> str:
    """Build a human-readable summary string from PlanExecutor step dicts."""
    descriptions = []
    for s in steps:
        act = s.get("action", "?")
        if act == "done":
            descriptions.append(f"Done: {s.get('summary', '')}")
        elif act == "think":
            pass
        elif act == "web_search":
            descriptions.append(f"Searched: {s.get('params', {}).get('query', '')}")
        elif act == "create_file":
            descriptions.append(f"Created: {s.get('params', {}).get('path', '')}")
        elif act == "read_file":
            descriptions.append(f"Read: {s.get('params', {}).get('path', '')}")
    return "; ".join(descriptions) if descriptions else "No steps executed"


def _run_single_task(
    task: Any,
    goal_manager: GoalManager,
    router: Any,
    learning_system: LearningSystem,
    overnight_results: List[Dict[str, Any]],
    save_overnight_results: Callable,
    memory: Any,
    goal_task_context: Dict[str, List[str]],
    results_lock: Optional[threading.Lock] = None,
    cycle_observations: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute a single task, handling start/complete/fail lifecycle.

    Returns dict with 'cost', 'failed', and 'observation' keys.
    """
    try:
        goal_manager.start_task(task.task_id)

        _sibling_context = goal_task_context.get(task.goal_id, [])
        result = execute_task(
            task, goal_manager, router, learning_system,
            overnight_results, save_overnight_results,
            memory=memory,
            sibling_task_summaries=_sibling_context,
            results_lock=results_lock,
            cycle_observations=cycle_observations,
        )
        cost = result.get("cost_usd", 0)

        # Check if task was deferred
        if result.get("deferred"):
            _delta = _parse_defer_delta(result.get("error", ""))
            task.deferred_until = datetime.now() + _delta
            task.status = TaskStatus.PENDING
            goal_manager.save_state()
            logger.info(
                "Task deferred: %s (resume after %s)",
                task.task_id, task.deferred_until.isoformat(),
            )
            return {"cost": cost, "failed": False}

        # Accumulate sibling context for subsequent waves
        _analysis = result.get("analysis", "")
        if _analysis and _analysis != "No steps executed":
            goal_task_context.setdefault(task.goal_id, []).append(
                f"[{task.description[:80]}] {_analysis[:200]}"
            )

        # Produce compressed observation for cross-goal context
        _goal_desc = _safe_goal_desc(goal_manager, task)
        _observation = _compress_task_observation(
            task.description, _goal_desc, result,
        )

        goal_manager.complete_task(task.task_id, result)
        goal_manager.save_state()
        logger.info("Task completed: %s", task.task_id)
        return {"cost": cost, "failed": False, "observation": _observation}

    except Exception as e:
        logger.error("Task execution failed: %s", e)
        goal_manager.fail_task(task.task_id, str(e))
        _locked_append(results_lock, overnight_results, save_overnight_results, {
            "task": task.description,
            "goal": _safe_goal_desc(goal_manager, task),
            "success": False,
            "summary": f"Exception in _run_single_task: {str(e)[:200]}",
            "cost": 0,
            "timestamp": datetime.now().isoformat(),
        })
        _fail_obs = f"[FAIL] {task.description[:60]} → Exception: {str(e)[:80]}"
        return {"cost": 0, "failed": True, "observation": _fail_obs}


def _execute_wave(
    wave: List[Any],
    goal_manager: GoalManager,
    router: Any,
    learning_system: LearningSystem,
    stop_flag: Any,
    overnight_results: List[Dict[str, Any]],
    save_overnight_results: Callable,
    memory: Any,
    goal_task_context: Dict[str, List[str]],
    cost_lock: threading.Lock,
    results_lock: threading.Lock,
    max_workers: int,
    cycle_observations: Optional[List[str]] = None,
) -> tuple:
    """Execute a wave of independent tasks concurrently.

    Returns (total_cost, tasks_executed, failures).
    """
    wave_cost = 0.0
    wave_executed = 0
    wave_failures = 0
    _wave_observations: List[str] = []

    # Mark all tasks as in-progress before spawning threads
    for task in wave:
        try:
            goal_manager.start_task(task.task_id)
        except Exception as e:
            logger.error("Could not start task %s: %s", task.task_id, e)

    def _worker(task):
        """Thread worker: execute one task and return result dict."""
        logger.info("Parallel worker executing: %s", task.description)
        try:
            _sibling_context = goal_task_context.get(task.goal_id, [])
            result = execute_task(
                task, goal_manager, router, learning_system,
                overnight_results, save_overnight_results,
                memory=memory,
                sibling_task_summaries=_sibling_context,
                results_lock=results_lock,
                cycle_observations=cycle_observations,
            )
            return {"task": task, "result": result, "error": None}
        except Exception as e:
            logger.error("Parallel task %s failed: %s", task.task_id, e)
            return {"task": task, "result": None, "error": str(e)}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_worker, task): task for task in wave}

        for future in as_completed(futures):
            wave_executed += 1
            outcome = future.result()
            task = outcome["task"]
            result = outcome["result"]
            error = outcome["error"]

            if error or result is None:
                goal_manager.fail_task(task.task_id, error or "Unknown error")
                wave_failures += 1
                continue

            cost = result.get("cost_usd", 0)
            with cost_lock:
                wave_cost += cost

            # Handle deferred tasks
            if result.get("deferred"):
                _delta = _parse_defer_delta(result.get("error", ""))
                task.deferred_until = datetime.now() + _delta
                task.status = TaskStatus.PENDING
                goal_manager.save_state()
                logger.info(
                    "Task deferred: %s (resume after %s)",
                    task.task_id, task.deferred_until.isoformat(),
                )
                continue

            # Accumulate sibling context for subsequent waves
            _analysis = result.get("analysis", "")
            if _analysis and _analysis != "No steps executed":
                goal_task_context.setdefault(task.goal_id, []).append(
                    f"[{task.description[:80]}] {_analysis[:200]}"
                )

            # Produce compressed observation for cross-goal context
            _goal_desc = _safe_goal_desc(goal_manager, task)
            _obs = _compress_task_observation(task.description, _goal_desc, result)
            _wave_observations.append(_obs)

            goal_manager.complete_task(task.task_id, result)
            goal_manager.save_state()
            logger.info("Parallel task completed: %s", task.task_id)

    return wave_cost, wave_executed, wave_failures, _wave_observations


def _hints_from_memory(task: Any, goal: Any, memory: Any) -> List[str]:
    """Retrieve prior research from long-term memory relevant to this task."""
    if not memory:
        return []
    try:
        query = f"{goal.description} {task.description}"
        related = memory.retrieve_relevant(query, n_results=3)
        semantic = related.get("semantic", [])
        relevant = [m for m in semantic if m.get("distance", 2.0) < 1.0]
        if not relevant:
            return []
        parts = []
        for m in relevant[:3]:
            text_preview = m["text"][:300]
            meta = m.get("metadata", {})
            src = meta.get("goal_description", meta.get("type", ""))
            parts.append(f"[{src[:60]}] {text_preview}" if src else text_preview)
        logger.info(
            "Injected %d prior research memories for task: %s",
            len(relevant), task.description[:60],
        )
        return [
            "PRIOR RESEARCH (already completed — do NOT repeat, "
            "build on these findings instead):\n"
            + "\n---\n".join(parts)
        ]
    except Exception as me:
        logger.debug("Memory query skipped: %s", me)
        return []


def _hints_from_project_path(task: Any, goal: Any) -> List[str]:
    """Resolve project path and gather file mappings."""
    _project_path = _resolve_project_path(goal.description, task.description)
    if not _project_path:
        return []
    result = [
        f"FILE OUTPUT: Save all reports and research files under "
        f"{_project_path}/ (NOT workspace/reports/). "
        f"This task belongs to the project at {_project_path}."
    ]
    logger.info("Project path resolved for task: %s", _project_path)
    try:
        from src.utils.project_context import scan_project_files
        _existing = scan_project_files(_project_path)
        if _existing:
            result.append(f"FILES IN THIS PROJECT: {', '.join(_existing[:15])}")
            _mapped = []
            for _ef in _existing:
                _basename = os.path.basename(_ef).lower()
                _name_no_ext = os.path.splitext(_basename)[0].replace("_", " ")
                if _basename in task.description.lower() or _name_no_ext in task.description.lower():
                    _mapped.append(f"{_basename} -> {_ef}")
            if _mapped:
                result.append(
                    "FILE PATH MAPPING (use the full paths on the right):\n"
                    + "\n".join(f"  {m}" for m in _mapped[:10])
                )
    except Exception as _pfe:
        logger.debug("Project file scan skipped: %s", _pfe)
    return result


def _hints_from_architect_spec(task: Any) -> List[str]:
    """Build hints from task's Architect spec fields."""
    result: List[str] = []
    if task.files_to_create:
        result.append(
            f"FILES TO CREATE: {', '.join(task.files_to_create)}. "
            f"Create these exact files as your deliverables."
        )
    if task.inputs:
        result.append(
            f"INPUTS NEEDED: {', '.join(task.inputs)}. "
            f"Gather these before building."
        )
    if task.expected_output:
        result.append(
            f"EXPECTED OUTPUT: {task.expected_output}. "
            f"This is your success criterion — verify it before calling done."
        )
    if task.interfaces:
        result.append(
            f"INTERFACES: {', '.join(task.interfaces)}. "
            f"Ensure compatibility with these connections."
        )
    return result


def _gather_execution_hints(
    task: Any, goal: Any, learning_system: LearningSystem,
    memory: Any, sibling_task_summaries: Optional[List[str]],
    cycle_observations: Optional[List[str]] = None,
) -> List[str]:
    """Build the full list of context hints for PlanExecutor.

    Gathers from: learning system, memory, file tracker, sibling tasks,
    cycle observations, project path, and Architect specs.
    Applies a character budget cap to prevent context bloat.
    """
    hints = learning_system.get_active_insights(2)
    action_summary = learning_system.get_action_summary()
    if action_summary:
        hints.append(action_summary)

    hints.extend(learning_system.get_failure_warnings(
        task_description=task.description,
        goal_description=goal.description,
    ))

    hints.extend(_hints_from_memory(task, goal, memory))

    # Existing artifacts from file tracker
    try:
        from src.core.file_tracker import FileTracker
        _known = FileTracker().get_files_by_keywords(
            f"{goal.description} {task.description}"
        )
        if _known:
            hints.append(
                "EXISTING FILES (already created — use/update instead of new):\n"
                + "\n".join(f"- {f}" for f in _known[:5])
            )
            logger.info("Injected %d known files for task: %s", len(_known), task.description[:60])
    except Exception as _fte:
        logger.debug("File tracker lookup skipped: %s", _fte)

    if sibling_task_summaries:
        hints.append(
            "EARLIER TASKS IN THIS GOAL (already completed this cycle — "
            "build on these results, do NOT repeat their work):\n"
            + "\n".join(f"- {s}" for s in sibling_task_summaries[-5:])
        )
        logger.info(
            "Injected %d sibling task summaries for task: %s",
            len(sibling_task_summaries), task.description[:60],
        )

    if cycle_observations:
        hints.append(
            "CYCLE CONTEXT (other goals already completed this dream cycle — "
            "don't duplicate their work):\n"
            + "\n".join(f"- {o}" for o in cycle_observations[-8:])
        )
        logger.info(
            "Injected %d cycle observations for task: %s",
            min(len(cycle_observations), 8), task.description[:60],
        )

    hints.extend(_hints_from_project_path(task, goal))
    hints.extend(_hints_from_architect_spec(task))

    return _cap_hints(hints)


def _run_qa_gate(
    task: Any, goal: Any, result: dict, hints: List[str],
    cost: float, analysis: str, steps: list,
    router: Any, learning_system: LearningSystem, approval_callback: Any,
) -> tuple:
    """Run QA evaluation on a successful task, retrying once on rejection.

    Returns (result, success, cost, analysis, steps) — potentially updated
    if the QA retry replaced the original result.
    """
    from src.core.plan_executor import PlanExecutor
    success = True

    try:
        from src.core.qa_evaluator import evaluate_task as _qa_evaluate, MAX_QA_RETRIES
        qa_result = _qa_evaluate(
            task_description=task.description,
            goal_description=goal.description,
            execution_result=result,
            router=router,
        )
        cost += qa_result.get("cost", 0)

        if qa_result["verdict"] == "reject":
            from src.core.qa_evaluator import format_issues
            logger.info(
                "QA REJECTED task '%s': %s",
                task.description[:60],
                "; ".join(format_issues(qa_result["issues"])[:3]),
            )
            # Check for skip conditions
            from src.core.plan_executor import check_and_clear_cancellation
            _shutdown = check_and_clear_cancellation()
            if _shutdown:
                logger.info("Skipping QA retry for '%s' — shutdown in progress", task.description[:60])

            _repeated_abort = any(
                s.get("repeated_error_abort") for s in result.get("steps_taken", [])
            )
            if _repeated_abort:
                logger.info("Skipping QA retry for '%s' — repeated-error abort", task.description[:60])

            # Mark as failure when retry is skipped (otherwise success=True
            # leaks through and the learning system records a false positive)
            if _shutdown or _repeated_abort:
                success = False
                result["success"] = False

            # Retry with escalation if no skip condition
            if not _shutdown and not _repeated_abort:
                result, success, cost, analysis, steps = _qa_retry(
                    task, goal, result, hints, cost, analysis, steps,
                    qa_result, router, learning_system, approval_callback,
                )

        elif qa_result["verdict"] == "fail":
            logger.info(
                "QA FAILED task '%s': %s",
                task.description[:60],
                "; ".join(format_issues(qa_result["issues"])[:3]),
            )
            success = False
            result["success"] = False

        else:
            logger.info("QA accepted task '%s'", task.description[:60])

    except Exception as qa_err:
        logger.debug("QA evaluation skipped: %s", qa_err)

    return result, success, cost, analysis, steps


def _qa_retry(
    task, goal, result, hints, cost, analysis, steps,
    qa_result, router, learning_system, approval_callback,
) -> tuple:
    """Execute a QA retry with Gemini escalation and prior-attempt context.

    Returns (result, success, cost, analysis, steps).
    """
    from src.core.plan_executor import PlanExecutor

    from src.core.qa_evaluator import format_issues_for_retry

    _qa_hints = list(hints) if hints else []
    # Summarize what the failed attempt did
    _prior_parts = []
    for _ps in result.get("steps_taken", []):
        _pa = _ps.get("action", "?")
        if _pa == "web_search":
            _prior_parts.append(f"searched: {_ps.get('params', {}).get('query', '')}")
        elif _pa in ("create_file", "write_source"):
            _prior_parts.append(f"wrote: {_ps.get('params', {}).get('path', '')}")
        elif _pa == "done":
            _prior_parts.append(f"claimed done: {_ps.get('summary', '')[:80]}")
    _prior_files = result.get("files_created", [])
    if _prior_parts:
        _qa_hints.append(f"PRIOR ATTEMPT (failed QA): {'; '.join(_prior_parts[:8])}")
    if _prior_files:
        _qa_hints.append(f"FILES ALREADY CREATED: {', '.join(_prior_files[:5])}")

    # Build targeted feedback from structured issues
    _structured_feedback = format_issues_for_retry(qa_result.get("issues", []))
    _feedback = _structured_feedback or qa_result.get("feedback", "")[:500]
    _qa_hints.append(
        f"QA FEEDBACK (your previous attempt was rejected): {_feedback}"
    )

    # Add step-level guidance for issues that reference specific steps
    _step_issues = [
        i for i in qa_result.get("issues", [])
        if isinstance(i, dict) and i.get("step") is not None
    ]
    if _step_issues:
        _step_hints = [
            f"Step {i['step']}: [{i.get('type', '?')}] {i.get('detail', '')[:120]}"
            for i in _step_issues[:5]
        ]
        _qa_hints.append(
            "STEP-LEVEL ISSUES (fix these specific steps):\n"
            + "\n".join(f"  - {h}" for h in _step_hints)
        )

    with router.escalate_for_task("gemini-3.1-pro") as _esc:
        _retry_executor = PlanExecutor(
            router=router,
            learning_system=learning_system,
            hints=_qa_hints,
            approval_callback=approval_callback,
        )
        _retry_result = _retry_executor.execute(
            task_description=task.description,
            goal_context=goal.description,
            task_id=f"{task.task_id}_qa_retry",
        )
    cost += _retry_result.get("total_cost", 0)

    if _retry_result.get("success", False):
        result = _retry_result
        steps = _retry_result.get("steps_taken", [])
        analysis = _build_step_summary(steps) or analysis
        logger.info("QA retry succeeded for task '%s' (escalated to Claude)", task.description[:60])
        return result, True, cost, analysis, steps

    logger.info(
        "QA retry also failed for task '%s' (even with Claude) — keeping original result",
        task.description[:60],
    )
    return result, True, cost, analysis, steps


def _record_task_result(
    task: Any, goal: Any, result: dict,
    success: bool, analysis: str, steps: list, cost: float,
    learning_system: LearningSystem, memory: Any,
    overnight_results: List[Dict[str, Any]],
    save_overnight_results: Callable,
    results_lock: Optional[threading.Lock],
) -> bool:
    """Record task outcome for learning, morning report, file tracking, and memory.

    Returns the 'learning_success' boolean (True if the task is considered
    successful for learning purposes).
    """
    # Determine learning success — use verified status as ground truth
    _verified = result.get("verified", False)
    _has_files = bool(result.get("files_created"))
    _learning_success = success and (_verified or not _has_files)
    if not _learning_success and success:
        logger.info(
            "Task had successful steps but verification failed "
            "(verified=%s) — recording as failure for learning",
            _verified,
        )

    # Record for learning system
    context = f"Goal: {goal.description}; Task: {task.description}"
    if _learning_success:
        learning_system.record_success(
            context=context, action=task.description,
            outcome=analysis[:200], lesson=None,
        )
    else:
        learning_system.record_failure(
            context=context, action=task.description,
            outcome=analysis[:200], lesson=None,
        )

    # Collect for morning report
    _locked_append(results_lock, overnight_results, save_overnight_results, {
        "task": task.description,
        "goal": goal.description,
        "success": success,
        "verified": _verified,
        "files_created": result.get("files_created", []),
        "steps": len(steps),
        "summary": analysis[:300],
        "cost": cost,
        "timestamp": datetime.now().isoformat(),
    })

    # Track created files for stale-file cleanup
    if result.get("files_created"):
        try:
            from src.core.file_tracker import FileTracker
            _tracker = FileTracker()
            for _fpath in result["files_created"]:
                _tracker.record_file_created(
                    _fpath, goal_id=task.goal_id, goal_description=goal.description,
                )
        except Exception as fte:
            logger.debug("File tracking skipped: %s", fte)

    # Store in long-term memory (both successes and failures)
    _store_task_memory(task, goal, result, analysis, steps, cost, _learning_success, memory)

    return _learning_success


def _notify_task_completion(
    task: Any, success: bool, cost: float, result: dict,
) -> None:
    """Send a brief Discord notification when a task finishes.

    Silently swallows errors — notifications are best-effort.
    Added session 161 because the user had no visibility into task
    completions during live overnight runs.
    """
    try:
        from src.interfaces.discord_bot import send_notification
        _desc = task.description[:80]
        _steps = result.get("total_steps") or len(result.get("steps_taken", []))
        _dur_ms = result.get("duration_ms", 0)
        _dur_s = _dur_ms / 1000 if _dur_ms else 0

        if success:
            _files = result.get("files_created", [])
            _files_note = f", {len(_files)} file(s)" if _files else ""
            _msg = (
                f"\u2705 **Task done**: {_desc}\n"
                f"   {_steps} steps, ${cost:.4f}{_files_note}"
            )
            if _dur_s:
                _msg += f", {_dur_s:.0f}s"
        else:
            _msg = (
                f"\u274c **Task failed**: {_desc}\n"
                f"   {_steps} steps, ${cost:.4f}"
            )
            if _dur_s:
                _msg += f", {_dur_s:.0f}s"

        send_notification(_msg)
    except Exception as e:
        logger.debug("Task completion notification skipped: %s", e)


def _store_task_memory(
    task: Any, goal: Any, result: dict,
    analysis: str, steps: list, cost: float,
    learning_success: bool, memory: Any,
) -> None:
    """Store task outcome in long-term memory for future context."""
    if not memory:
        return
    try:
        _files = result.get("files_created", [])
        _file_names = [os.path.basename(f) for f in _files[:5]]
        if learning_success:
            memory_text = (
                f"Task completed successfully: {task.description}\n"
                f"Goal: {goal.description}\n"
                f"Result: {analysis[:500]}\n"
                f"Files: {', '.join(_file_names)}"
            )
            mem_type = "research_result"
        else:
            _error_info = result.get("error", "")
            _last_steps = steps[-3:] if steps else []
            _last_actions = "; ".join(s.get("action", "?") for s in _last_steps)
            memory_text = (
                f"Task FAILED: {task.description}\n"
                f"Goal: {goal.description}\n"
                f"Error: {_error_info[:300]}\n"
                f"Last actions: {_last_actions}\n"
                f"Summary: {analysis[:300]}"
            )
            mem_type = "task_failure"

        memory.store_long_term(
            text=memory_text,
            memory_type=mem_type,
            metadata={
                "goal_id": task.goal_id,
                "task_id": task.task_id,
                "goal_description": goal.description,
                "task_description": task.description,
                "files_created": _file_names,
                "cost_usd": cost,
                "success": learning_success,
            },
        )
        logger.info(
            "Stored %s in long-term memory: %s",
            "research result" if learning_success else "failure lesson",
            task.description[:60],
        )
    except Exception as mse:
        logger.debug("Memory storage skipped: %s", mse)


def _handle_follow_ups(
    task: Any, goal: Any, result: dict,
    router: Any, goal_manager: GoalManager,
    overnight_results: List[Dict[str, Any]],
    save_overnight_results: Callable,
    results_lock: Optional[threading.Lock],
) -> None:
    """Extract follow-up tasks and evaluate for interesting findings."""
    # Follow-up task extraction
    try:
        follow_up_tasks = extract_follow_up_tasks(
            files_created=result["files_created"],
            task=task, goal=goal,
            router=router, goal_manager=goal_manager,
        )
        if follow_up_tasks:
            if results_lock:
                results_lock.acquire()
            try:
                overnight_results[-1]["follow_up_tasks"] = [
                    t.task_id for t in follow_up_tasks
                ]
                save_overnight_results()
            finally:
                if results_lock:
                    results_lock.release()
    except Exception as fue:
        logger.debug("Follow-up task extraction skipped: %s", fue)

    # Interesting findings evaluation
    try:
        from src.core.interesting_findings import get_findings_queue
        ifq = get_findings_queue()
        _finding_id = ifq.evaluate_and_queue(
            task_result=result,
            files_created=result["files_created"],
            goal_desc=goal.description,
            task_desc=task.description,
            router=router,
        )
        if _finding_id:
            try:
                _finding = next(
                    (f for f in ifq.findings if f.get("id") == _finding_id), None,
                )
                if _finding:
                    from src.core.reporting import send_finding_notification
                    send_finding_notification(
                        goal_desc=goal.description,
                        finding_summary=_finding["summary"],
                        files_created=result.get("files_created", []),
                    )
            except Exception as nfe:
                logger.debug("Finding notification skipped: %s", nfe)
    except Exception as ife:
        logger.debug("Interesting finding eval skipped: %s", ife)


def execute_task(
    task: Any,
    goal_manager: GoalManager,
    router: Any,
    learning_system: LearningSystem,
    overnight_results: List[Dict[str, Any]],
    save_overnight_results: Callable,
    memory: Any = None,
    sibling_task_summaries: Optional[List[str]] = None,
    results_lock: Optional[threading.Lock] = None,
    cycle_observations: Optional[List[str]] = None,
) -> dict:
    """Execute a single task autonomously using PlanExecutor.

    Orchestrates: hint gathering → PlanExecutor execution → QA gate →
    result recording → follow-up extraction.

    Args:
        cycle_observations: Compressed observation notes from other goals
            completed earlier in this dream cycle. Used to prevent duplicate
            work across goals.

    Returns:
        Execution result dict with executed, analysis, steps, cost, timestamp.
    """
    logger.info("Executing task (multi-step): %s", task.description)

    try:
        from src.core.plan_executor import PlanExecutor

        goal = goal_manager.goals[task.goal_id]
        hints = _gather_execution_hints(
            task, goal, learning_system, memory, sibling_task_summaries,
            cycle_observations=cycle_observations,
        )

        # Resolve approval callback for source modifications
        try:
            from src.interfaces.discord_bot import request_source_approval
            _approval_cb = request_source_approval
        except ImportError:
            _approval_cb = None

        # Execute via PlanExecutor
        executor = PlanExecutor(
            router=router,
            learning_system=learning_system,
            hints=hints if hints else None,
            approval_callback=_approval_cb,
        )
        result = executor.execute(
            task_description=task.description,
            goal_context=goal.description,
            task_id=task.task_id,
        )

        success = result.get("success", False)
        steps = result.get("steps_taken", [])
        cost = result.get("total_cost", 0)
        analysis = _build_step_summary(steps)

        logger.info(
            "Task execution: %s (%d steps, $%.4f)",
            "success" if success else "failed", len(steps), cost,
        )

        # QA evaluation with retry on rejection
        if success:
            result, success, cost, analysis, steps = _run_qa_gate(
                task, goal, result, hints, cost, analysis, steps,
                router, learning_system, _approval_cb,
            )

        # Record results (learning, morning report, file tracking, memory)
        _learning_success = _record_task_result(
            task, goal, result, success, analysis, steps, cost,
            learning_system, memory,
            overnight_results, save_overnight_results, results_lock,
        )

        # Task completion Discord notification — disabled session 166.
        # Per-task notifications were too noisy; goal-level notifications
        # from goal_worker_pool._notify_goal_result() are sufficient.
        # _notify_task_completion(task, success, cost, result)

        # Follow-up tasks and interesting findings
        if _learning_success and result.get("files_created"):
            _handle_follow_ups(
                task, goal, result, router, goal_manager,
                overnight_results, save_overnight_results, results_lock,
            )

        return {
            "executed": success,
            "analysis": analysis,
            "steps_taken": [s.get("action", "?") for s in steps],
            "cost_usd": cost,
            "timestamp": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error("Task execution error: %s", e, exc_info=True)
        try:
            if goal_manager and hasattr(task, "goal_id"):
                goal = goal_manager.goals.get(task.goal_id)
                if goal:
                    learning_system.record_failure(
                        context=f"Goal: {goal.description}; Task: {task.description}",
                        action=task.description,
                        outcome=str(e), lesson=None,
                    )
        except Exception as ler:
            logger.warning("Could not record failure for learning: %s", ler)

        _locked_append(results_lock, overnight_results, save_overnight_results, {
            "task": task.description,
            "goal": _safe_goal_desc(goal_manager, task),
            "success": False,
            "verified": False,
            "files_created": [],
            "steps": 0,
            "summary": f"Execution error: {str(e)[:200]}",
            "cost": 0,
            "timestamp": datetime.now().isoformat(),
        })

        return {
            "executed": False,
            "error": str(e),
            "analysis": "",
            "timestamp": datetime.now().isoformat(),
        }


# -- Follow-up task extraction (within same goal) ----------------------------


def _build_follow_up_prompt(task: Any, goal: Any, file_contents: list) -> str:
    """Build the prompt for follow-up task extraction."""
    findings_text = "\n\n".join(
        f"--- {name} ---\n{content}" for name, content in file_contents
    )
    existing_task_descs = "\n".join(f"- {t.description}" for t in goal.tasks)
    user_name = get_user_name()
    return f"""You just completed a task within a larger goal for {user_name}.

Goal: {goal.description}
Completed task: {task.description}

Existing tasks in this goal (DO NOT duplicate):
{existing_task_descs}

Work output from completed task:
{findings_text}

Based on this output, are there 0-2 additional tasks that should be done
WITHIN THE SCOPE of this goal? These should be natural next steps that
the original task decomposition didn't anticipate.

RULES:
1. Tasks must be WITHIN the original goal's scope — not tangential or new topics
2. Tasks must use available tools (web_search, create_file, read_file, etc.)
3. DO NOT suggest tasks that duplicate existing ones above
4. If the goal is essentially complete, return an empty array []
5. Keep tasks concrete and actionable

Return ONLY a JSON array (0-2 items):
[
  {{"description": "Specific actionable task within the goal scope"}}
]
JSON only:"""


def extract_follow_up_tasks(
    files_created: list,
    task: Any,
    goal: Any,
    router: Any,
    goal_manager: GoalManager,
) -> list:
    """Analyze completed task output and add 0-2 follow-up tasks to the SAME goal.

    Unlike the old extract_follow_up_goals(), this does NOT create new goals.
    It adds tasks to the existing goal, keeping work within the user's original
    request scope. New tasks depend on the completed task.

    Returns:
        List of created Task objects (may be empty).
    """
    if not router or not goal_manager:
        return []

    # Read up to 3 created files (truncated to 1500 chars each)
    file_contents = []
    for fpath in files_created[:3]:
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()[:1500]
            file_contents.append((os.path.basename(fpath), content))
        except Exception:
            continue

    if not file_contents:
        return []

    # Don't add follow-ups if the goal already has many tasks
    pending_tasks = sum(
        1 for t in goal.tasks if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)
    )
    if pending_tasks >= 3:
        logger.info("Skipping follow-up tasks: goal already has %d pending tasks", pending_tasks)
        return []

    prompt = _build_follow_up_prompt(task, goal, file_contents)

    try:
        resp = router.generate(
            prompt=prompt, max_tokens=300, temperature=0.3,
        )
        text = resp.get("text", "")

        from src.utils.parsing import extract_json_array
        ideas = extract_json_array(text)

        if not isinstance(ideas, list):
            return []

        task_descriptions = []
        for idea in ideas[:2]:
            if not isinstance(idea, dict):
                continue
            desc = (idea.get("description") or "").strip()
            if desc:
                task_descriptions.append(desc)

        if not task_descriptions:
            return []

        created = goal_manager.add_follow_up_tasks(
            goal_id=goal.goal_id,
            task_descriptions=task_descriptions,
            after_task_id=task.task_id,
        )
        logger.info(
            "Added %d follow-up tasks to goal '%s'",
            len(created), goal.description[:60],
        )
        return created

    except Exception as e:
        logger.debug("Follow-up task extraction failed: %s", e)
        return []
