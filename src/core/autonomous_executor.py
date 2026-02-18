"""
Autonomous Executor — Task execution engine for dream cycles.

Handles queued task processing, autonomous goal-driven execution,
task execution via PlanExecutor, and follow-up task extraction.
Split from dream_cycle.py in session 11. Reworked in session 31.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.core.goal_manager import GoalManager, TaskStatus
from src.core.idea_generator import MAX_ACTIVE_GOALS, count_active_goals
from src.core.learning_system import LearningSystem
from src.utils.paths import base_path_as_path as _base_path


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


def _get_dream_cycle_budget() -> float:
    """Load per-cycle budget limit from rules.yaml."""
    _DEFAULT = 0.50
    try:
        import yaml
        rules_path = _base_path() / "config" / "rules.yaml"
        with open(rules_path, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f) or {}
        for rule in rules.get("non_override_rules", []):
            if rule.get("name") == "dream_cycle_budget" and rule.get("enabled", True):
                return float(rule.get("limit", _DEFAULT))
    except Exception:
        pass
    return _DEFAULT


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
    from src.interfaces.discord_bot import send_notification

    def _notify(text: str) -> None:
        try:
            send_notification(text)
        except Exception:
            pass

    executed = 0
    _dream_start = time.monotonic()
    _MAX_DREAM_MINUTES = 120  # API-only: let budget cap be the real safety net
    max_tasks_per_dream = 50  # Safety hard cap
    _cycle_budget = _get_dream_cycle_budget()
    _cycle_cost = 0.0
    # Accumulate per-goal task results so later tasks know what earlier ones did.
    # Maps goal_id -> list of short summaries from completed tasks this cycle.
    _goal_task_context: Dict[str, List[str]] = {}

    # Resume any tasks that were in-progress when we crashed/restarted.
    for goal in list(goal_manager.goals.values()):
        if stop_flag.is_set() or executed >= max_tasks_per_dream:
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
                    _cycle_cost += result.get("cost_usd", 0)
                    goal_manager.complete_task(task.task_id, result)
                    goal_manager.save_state()
                    executed += 1
                    if result.get("executed") and goal.is_complete():
                        _notify(
                            f"\U0001f3c6 Goal complete: {goal.description} "
                            f"({len(goal.tasks)} tasks finished)",
                        )
                    if _cycle_cost >= _cycle_budget:
                        logger.warning(
                            "Dream cycle budget hit ($%.4f >= $%.2f) during resume",
                            _cycle_cost, _cycle_budget,
                        )
                        return executed
                except Exception as e:
                    logger.error("Interrupted task resume failed: %s", e)
                    goal_manager.fail_task(task.task_id, str(e))

    # Decompose any undecomposed goals first
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
            goal_manager.decompose_goal(
                goal.goal_id,
                router,
                learning_hints=learning_system.get_active_insights(2),
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

    # Main execution loop
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

        task = goal_manager.get_next_task()
        if not task:
            logger.info("No ready tasks to execute")
            break

        logger.info("Autonomously executing: %s", task.description)

        try:
            goal_manager.start_task(task.task_id)

            # Pass context from earlier tasks in the same goal
            _sibling_context = _goal_task_context.get(task.goal_id, [])
            result = execute_task(
                task, goal_manager, router, learning_system,
                overnight_results, save_overnight_results,
                memory=memory,
                sibling_task_summaries=_sibling_context,
            )
            _cycle_cost += result.get("cost_usd", 0)

            # Accumulate this task's result for sibling context
            _analysis = result.get("analysis", "")
            if _analysis and _analysis != "No steps executed":
                _goal_task_context.setdefault(task.goal_id, []).append(
                    f"[{task.description[:80]}] {_analysis[:200]}"
                )

            goal_manager.complete_task(task.task_id, result)
            goal_manager.save_state()
            executed += 1
            logger.info("Task completed: %s ($%.4f this cycle)", task.task_id, _cycle_cost)

            if result.get("executed"):
                goal = goal_manager.goals.get(task.goal_id)
                if goal and goal.is_complete():
                    # Check if this was a user-requested goal — send richer follow-up
                    _intent = (goal.user_intent or "").lower()
                    if _intent.startswith("user "):
                        try:
                            from src.core.reporting import send_user_goal_completion
                            # Gather all task results for this goal from overnight_results
                            _goal_results = [
                                r for r in overnight_results
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
                        except Exception as ugce:
                            logger.debug("User goal completion notify failed: %s", ugce)
                    else:
                        _notify(
                            f"\U0001f3c6 Goal complete: {goal.description} "
                            f"({len(goal.tasks)} tasks finished)",
                        )

        except Exception as e:
            logger.error("Task execution failed: %s", e)
            goal_manager.fail_task(task.task_id, str(e))
            _notify(f"\u274c Task failed: {task.description} — {e}")
            break

    return executed


def execute_task(
    task: Any,
    goal_manager: GoalManager,
    router: Any,
    learning_system: LearningSystem,
    overnight_results: List[Dict[str, Any]],
    save_overnight_results: Callable,
    memory: Any = None,
    sibling_task_summaries: Optional[List[str]] = None,
) -> dict:
    """Execute a single task autonomously using PlanExecutor.

    Chains multiple steps: research -> create files -> verify -> done.
    Records results for learning and morning report.
    Queries long-term memory for related prior work and injects as context.
    Stores successful research results in long-term memory after completion.

    Args:
        task: Task object to execute
        goal_manager: Goal manager for context
        router: Model router for API calls
        learning_system: Learning system for recording outcomes
        overnight_results: Accumulator for overnight work results
        save_overnight_results: Callback to persist results to disk
        memory: Optional MemoryManager for long-term research recall
        sibling_task_summaries: Optional summaries from earlier tasks in the
            same goal (this cycle). Gives the model context about what sibling
            tasks already accomplished so it can build on their work.

    Returns:
        Execution result dict with executed, analysis, steps, cost, timestamp.
    """
    logger.info("Executing task (multi-step): %s", task.description)

    try:
        from src.core.plan_executor import PlanExecutor

        goal = goal_manager.goals[task.goal_id]
        hints = learning_system.get_active_insights(2)
        action_summary = learning_system.get_action_summary()
        if action_summary:
            hints.append(action_summary)

        # Query long-term memory for related prior research
        _prior_research_hint = ""
        if memory:
            try:
                query = f"{goal.description} {task.description}"
                related = memory.retrieve_relevant(query, n_results=3)
                semantic = related.get("semantic", [])
                # Filter to reasonably relevant results (cosine distance < 1.0)
                relevant = [m for m in semantic if m.get("distance", 2.0) < 1.0]
                if relevant:
                    parts = []
                    for m in relevant[:3]:
                        text_preview = m["text"][:300]
                        meta = m.get("metadata", {})
                        src = meta.get("goal_description", meta.get("type", ""))
                        if src:
                            parts.append(f"[{src[:60]}] {text_preview}")
                        else:
                            parts.append(text_preview)
                    _prior_research_hint = (
                        "PRIOR RESEARCH (already completed — do NOT repeat, "
                        "build on these findings instead):\n"
                        + "\n---\n".join(parts)
                    )
                    hints.append(_prior_research_hint)
                    logger.info(
                        "Injected %d prior research memories for task: %s",
                        len(relevant), task.description[:60],
                    )
            except Exception as me:
                logger.debug("Memory query skipped: %s", me)

        # Inject sibling task context (what earlier tasks in this goal already did)
        if sibling_task_summaries:
            _sibling_hint = (
                "EARLIER TASKS IN THIS GOAL (already completed this cycle — "
                "build on these results, do NOT repeat their work):\n"
                + "\n".join(f"- {s}" for s in sibling_task_summaries[-5:])
            )
            hints.append(_sibling_hint)
            logger.info(
                "Injected %d sibling task summaries for task: %s",
                len(sibling_task_summaries), task.description[:60],
            )

        # Resolve project path — tell PlanExecutor where to save files
        _project_path = _resolve_project_path(goal.description, task.description)
        if _project_path:
            hints.append(
                f"FILE OUTPUT: Save all reports and research files under "
                f"{_project_path}/ (NOT workspace/reports/). "
                f"This task belongs to the project at {_project_path}."
            )
            logger.info("Project path resolved for task: %s", _project_path)

        # Pass the Discord approval callback for source modifications
        try:
            from src.interfaces.discord_bot import request_source_approval
            _approval_cb = request_source_approval
        except ImportError:
            _approval_cb = None

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

        # Build human-readable summary
        step_descriptions = []
        for s in steps:
            act = s.get("action", "?")
            if act == "done":
                step_descriptions.append(f"Done: {s.get('summary', '')}")
            elif act == "think":
                pass
            elif act == "web_search":
                q = s.get("params", {}).get("query", "")
                step_descriptions.append(f"Searched: {q}")
            elif act == "create_file":
                p = s.get("params", {}).get("path", "")
                step_descriptions.append(f"Created: {p}")
            elif act == "read_file":
                p = s.get("params", {}).get("path", "")
                step_descriptions.append(f"Read: {p}")
        analysis = "; ".join(step_descriptions) if step_descriptions else "No steps executed"

        logger.info(
            "Task execution: %s (%d steps, $%.4f)",
            "success" if success else "failed", len(steps), cost,
        )

        # Record for learning — use verified status as ground truth
        _verified = result.get("verified", False)
        _has_files = bool(result.get("files_created"))
        _learning_success = success and (_verified or not _has_files)
        if not _learning_success and success:
            logger.info(
                "Task had successful steps but verification failed "
                "(verified=%s) — recording as failure for learning",
                _verified,
            )
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
        overnight_results.append({
            "task": task.description,
            "goal": goal.description,
            "success": success,
            "verified": result.get("verified", False),
            "files_created": result.get("files_created", []),
            "steps": len(steps),
            "summary": analysis[:300],
            "cost": cost,
            "timestamp": datetime.now().isoformat(),
        })
        save_overnight_results()

        # Track created files for stale-file cleanup
        if result.get("files_created"):
            try:
                from src.core.file_tracker import FileTracker
                _tracker = FileTracker()
                for _fpath in result["files_created"]:
                    _tracker.record_file_created(_fpath, goal_id=task.goal_id)
            except Exception as fte:
                logger.debug("File tracking skipped: %s", fte)

        # Store task results in long-term memory for future recall.
        # We store BOTH successes (for reuse) and failures (to avoid repeating mistakes).
        if memory:
            try:
                _files = result.get("files_created", [])
                _file_names = [os.path.basename(f) for f in _files[:5]]
                if _learning_success:
                    memory_text = (
                        f"Task completed successfully: {task.description}\n"
                        f"Goal: {goal.description}\n"
                        f"Result: {analysis[:500]}\n"
                        f"Files: {', '.join(_file_names)}"
                    )
                    mem_type = "research_result"
                else:
                    # Extract what went wrong so we don't repeat the same mistake
                    _error_info = result.get("error", "")
                    _last_steps = steps[-3:] if steps else []
                    _last_actions = "; ".join(
                        s.get("action", "?") for s in _last_steps
                    )
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
                        "success": _learning_success,
                    },
                )
                logger.info(
                    "Stored %s in long-term memory: %s",
                    "research result" if _learning_success else "failure lesson",
                    task.description[:60],
                )
            except Exception as mse:
                logger.debug("Memory storage skipped: %s", mse)

        # Extract follow-up tasks within the same goal
        if _learning_success and result.get("files_created"):
            try:
                follow_up_tasks = extract_follow_up_tasks(
                    files_created=result["files_created"],
                    task=task,
                    goal=goal,
                    router=router,
                    goal_manager=goal_manager,
                )
                if follow_up_tasks:
                    overnight_results[-1]["follow_up_tasks"] = [
                        t.task_id for t in follow_up_tasks
                    ]
                    save_overnight_results()
            except Exception as fue:
                logger.debug("Follow-up task extraction skipped: %s", fue)

            # Evaluate for interesting findings and notify proactively
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
                # Send proactive Discord notification if finding was queued
                if _finding_id:
                    try:
                        _finding = next(
                            (f for f in ifq.findings if f.get("id") == _finding_id),
                            None,
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
            logger.debug("Could not record failure for learning: %s", ler)
        return {
            "executed": False,
            "error": str(e),
            "analysis": "",
            "timestamp": datetime.now().isoformat(),
        }


# -- Follow-up task extraction (within same goal) ----------------------------


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

    findings_text = "\n\n".join(
        f"--- {name} ---\n{content}" for name, content in file_contents
    )

    # List existing tasks so the model doesn't suggest duplicates
    existing_task_descs = "\n".join(
        f"- {t.description}" for t in goal.tasks
    )

    prompt = f"""You just completed a task within a larger goal for Jesse.

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
