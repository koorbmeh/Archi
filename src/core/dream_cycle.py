"""
Dream Cycle Engine - Proactive background processing.

Archi runs "dream cycles" when idle, processing queued tasks,
improving itself, and pursuing long-term goals autonomously.
"""

import json
import logging
import os
import time
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, Any, List

import yaml

from src.core.goal_manager import GoalManager, TaskStatus
from src.core.learning_system import LearningSystem
from src.models.local_model import LocalModel
from src.utils.paths import base_path_as_path as _base_path

# Default per-cycle budget (overridden by rules.yaml dream_cycle_budget)
_DEFAULT_CYCLE_BUDGET = 0.50


def _get_dream_cycle_budget() -> float:
    """Load per-cycle budget limit from rules.yaml."""
    try:
        import yaml as _yaml
        rules_path = _base_path() / "config" / "rules.yaml"
        with open(rules_path, "r", encoding="utf-8") as f:
            rules = _yaml.safe_load(f) or {}
        for rule in rules.get("non_override_rules", []):
            if rule.get("name") == "dream_cycle_budget" and rule.get("enabled", True):
                return float(rule.get("limit", _DEFAULT_CYCLE_BUDGET))
    except Exception:
        pass
    return _DEFAULT_CYCLE_BUDGET

logger = logging.getLogger(__name__)


_last_notify_time: float = 0.0
_NOTIFY_COOLDOWN: float = 60.0  # Minimum seconds between DMs


def _notify(text: str, bypass_cooldown: bool = False) -> None:
    """Send a Discord DM notification (best-effort, never raises).

    Enforces a cooldown between messages to avoid spamming the owner.
    Use bypass_cooldown=True only for high-value events (goal complete).
    """
    global _last_notify_time
    now = time.monotonic()
    if not bypass_cooldown and (now - _last_notify_time) < _NOTIFY_COOLDOWN:
        logger.debug("Notification suppressed (cooldown): %s", text[:80])
        return
    try:
        from src.interfaces.discord_bot import send_notification
        if send_notification(text):
            _last_notify_time = now
    except Exception as e:
        logger.debug("Discord notification skipped: %s", e)





class DreamCycle:
    """
    Manages Archi's proactive background processing.

    When idle, Archi:
    - Processes queued tasks
    - Reviews and learns from past actions
    - Plans future work
    - Improves its capabilities
    """

    def __init__(
        self,
        idle_threshold_seconds: int = 300,
        check_interval_seconds: int = 30,
    ):
        """
        Args:
            idle_threshold_seconds: How long to wait idle before dreaming (default 5min)
            check_interval_seconds: How often to check for idle (default 30s)
        """
        self.idle_threshold = idle_threshold_seconds
        self.check_interval = check_interval_seconds
        self.last_activity = datetime.now()
        self.is_dreaming = False
        self.dream_thread: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()
        self.task_queue: List[Dict[str, Any]] = []
        self.dream_history: List[Dict[str, Any]] = []

        # Autonomous execution components
        self.goal_manager: Optional[GoalManager] = None
        self.model: Optional[LocalModel] = None
        self.autonomous_mode = False
        self._router: Optional[Any] = None
        self.learning_system = LearningSystem()

        # Idea pipeline and morning report tracking
        self._morning_report_sent: Optional[date] = None
        self._last_brainstorm: Optional[datetime] = None
        self._overnight_results: List[Dict[str, Any]] = []
        self._overnight_results_path = _base_path() / "data" / "overnight_results.json"
        self._load_overnight_results()  # Restore from disk (survives restarts)

        # Hourly notification accumulator (replaces per-cycle spam)
        self._hourly_task_results: List[Dict[str, Any]] = []
        self._last_hourly_notify: float = time.monotonic()

        self.identity = self._load_identity()
        self.prime_directive = self._load_prime_directive()
        role = self.identity.get("identity", {}).get("role", "Archi")
        logger.info("Dream cycle initialized (idle threshold: %ds) — identity: %s", idle_threshold_seconds, role)

    def _load_identity(self) -> dict:
        """Load identity configuration from config/archi_identity.yaml."""
        base = _base_path()
        identity_file = base / "config" / "archi_identity.yaml"
        if not identity_file.exists():
            logger.warning("No identity file found at %s", identity_file)
            return {}
        try:
            with open(identity_file, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error("Error loading identity: %s", e)
            return {}

    def _load_prime_directive(self) -> str:
        """Load the Prime Directive text from config/prime_directive.txt."""
        base = _base_path()
        directive_file = base / "config" / "prime_directive.txt"
        if not directive_file.exists():
            logger.warning("No Prime Directive found at %s", directive_file)
            return ""
        try:
            with open(directive_file, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error("Error loading Prime Directive: %s", e)
            return ""

    def mark_activity(self):
        """Mark that user activity occurred (resets idle timer)."""
        self.last_activity = datetime.now()

        # If dreaming, interrupt gracefully
        if self.is_dreaming:
            logger.info("User activity detected, interrupting dream cycle")
            self.stop_flag.set()

    def is_idle(self) -> bool:
        """Check if system has been idle long enough to start dreaming."""
        idle_time = (datetime.now() - self.last_activity).total_seconds()
        return idle_time >= self.idle_threshold

    def enable_autonomous_mode(
        self, goal_manager: GoalManager, model: LocalModel
    ) -> None:
        """
        Enable autonomous task execution during dream cycles.

        Args:
            goal_manager: Goal manager with tasks to execute
            model: AI model for executing tasks
        """
        self.goal_manager = goal_manager
        self.model = model
        self.autonomous_mode = True
        logger.info("Autonomous execution mode ENABLED")

    def queue_task(self, task: Dict[str, Any]):
        """
        Add a task to the dream queue.

        Args:
            task: Dict with 'type', 'description', 'priority', 'data'
        """
        task["queued_at"] = datetime.now().isoformat()
        self.task_queue.append(task)
        logger.info("Queued task: %s", task.get("description", "Unknown"))

    def start_monitoring(self):
        """Start background thread that watches for idle periods."""
        if self.dream_thread and self.dream_thread.is_alive():
            logger.warning("Dream monitoring already running")
            return

        self.stop_flag.clear()
        self.dream_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.dream_thread.start()
        logger.info("Dream cycle monitoring started")

    def stop_monitoring(self):
        """Stop dream cycle monitoring and flush pending data."""
        self.stop_flag.set()
        if self.dream_thread:
            self.dream_thread.join(timeout=5)
        # Flush any unsaved learning experiences
        if self.learning_system:
            try:
                self.learning_system.flush()
            except Exception as e:
                logger.debug("Learning system flush failed: %s", e)
        logger.info("Dream cycle monitoring stopped")

    def _monitor_loop(self):
        """Background thread that monitors for idle periods."""
        while not self.stop_flag.is_set():
            if self.is_idle() and not self.is_dreaming:
                # Don't start a dream cycle while image generation owns the GPU
                try:
                    from src.tools.image_gen import generating_in_progress as img_gen
                    if img_gen:
                        logger.debug("Idle but image generation in progress — skipping dream cycle")
                        time.sleep(self.check_interval)
                        continue
                except ImportError:
                    pass
                logger.info("Idle detected, starting dream cycle")
                self._run_dream_cycle()

            time.sleep(self.check_interval)

    def _run_dream_cycle(self):
        """Execute a dream cycle (background processing).

        Phases:
          1. Morning report (if morning and not sent today)
          2. Idea brainstorming (periodic — generates new goals)
          3. Task queue processing + autonomous goal execution
          4. Learning review (every 3rd cycle)
          5. Future work planning
        """
        self.is_dreaming = True
        dream_start = datetime.now()

        try:
            logger.info("=== DREAM CYCLE START ===")

            # Phase 0: Morning report (send once per morning, 6-9 AM)
            current_hour = dream_start.hour
            if 6 <= current_hour <= 9 and self._morning_report_sent != dream_start.date():
                self._send_morning_report()

            # Phase 1: Idea brainstorming (generates new goals to work on)
            # Runs during night hours, at most once per 24 hours
            if not self.stop_flag.is_set():
                self._brainstorm_ideas()

            # Phase 2: Process queued tasks + autonomous goal execution
            _results_before = len(self._overnight_results)
            tasks_processed = self._process_task_queue()
            _results_after = len(self._overnight_results)
            # Grab the task summaries that were added THIS cycle
            _this_cycle_results = self._overnight_results[_results_before:_results_after]

            # Phase 3: Review recent history (learning)
            insights = self._review_history()

            # Phase 4: Plan future work
            plans = self._plan_future_work()

            # Phase 5: Periodic synthesis (every 10 cycles)
            if not self.stop_flag.is_set() and len(self.dream_history) % 10 == 0 and len(self.dream_history) > 0:
                try:
                    self._run_synthesis()
                except Exception as se:
                    logger.debug("Synthesis skipped: %s", se)

            dream_duration = (datetime.now() - dream_start).total_seconds()

            # Record dream cycle
            self.dream_history.append(
                {
                    "started_at": dream_start.isoformat(),
                    "duration_seconds": dream_duration,
                    "tasks_processed": tasks_processed,
                    "insights": insights,
                    "plans": plans,
                    "interrupted": self.stop_flag.is_set(),
                }
            )

            logger.info(
                "=== DREAM CYCLE END (duration: %.1fs) ===", dream_duration
            )

            # Persist dream cycle summary to a JSONL file so the chat system
            # can answer "what have you been doing?" even after restarts.
            try:
                log_path = _base_path() / "data" / "dream_log.jsonl"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                # Include actual task descriptions for the work-query fast-path
                task_summaries = []
                for r in _this_cycle_results:
                    task_summaries.append({
                        "task": r.get("task", ""),
                        "goal": r.get("goal", ""),
                        "success": r.get("success", False),
                        "files": [os.path.basename(f) for f in r.get("files_created", [])[:3]],
                    })
                entry = {
                    "ts": dream_start.isoformat(),
                    "duration_s": round(dream_duration, 1),
                    "tasks_done": tasks_processed,
                    "tasks": task_summaries,
                    "plans": len(plans),
                    "insights": len(insights) if isinstance(insights, list) else 0,
                }
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception:
                pass  # Best-effort logging

            # Accumulate results for hourly notification (replaces per-cycle spam).
            # Goal completions still notify immediately via bypass_cooldown.
            if _this_cycle_results:
                self._hourly_task_results.extend(_this_cycle_results)

            _HOURLY_INTERVAL = 3600  # 1 hour between summary notifications
            _since_last = time.monotonic() - self._last_hourly_notify
            if self._hourly_task_results and _since_last >= _HOURLY_INTERVAL:
                self._send_hourly_summary()
            elif tasks_processed > 0:
                # Log locally so terminal still shows activity
                logger.info(
                    "Dream cycle: %d tasks done (hourly summary in %.0f min)",
                    tasks_processed,
                    max(0, (_HOURLY_INTERVAL - _since_last) / 60),
                )

        except Exception as e:
            logger.error("Dream cycle error: %s", e, exc_info=True)
        finally:
            self.is_dreaming = False
            self.stop_flag.clear()
            # Reset idle timer so we wait the full threshold again before
            # the next dream cycle.  Without this, _monitor_loop sees
            # is_idle() == True immediately (last_activity never changed)
            # and fires another cycle every check_interval (30s).
            self.last_activity = datetime.now()

    def _process_task_queue(self) -> int:
        """Process queued background tasks."""
        processed = 0

        # First, actually execute manual queue tasks (was previously counted but not executed)
        while self.task_queue and not self.stop_flag.is_set():
            task = self.task_queue.pop(0)

            try:
                desc = task.get("description", "") or str(task.get("type", "unknown"))
                logger.info("Executing queued task: %s", desc)
                result = self._execute_queued_task(task)
                if result.get("executed"):
                    processed += 1
            except Exception as e:
                logger.error("Task processing error: %s", e)

        # Then, autonomous goal-driven work
        if self.autonomous_mode and self.goal_manager and self.model:
            processed += self._execute_autonomous_tasks()

        return processed

    def _execute_autonomous_tasks(self) -> int:
        """Execute tasks from goal manager autonomously.

        Runs continuously until the time cap, cost cap, or task cap is reached.
        The per-cycle cost cap (from rules.yaml dream_cycle_budget) prevents a
        single hallucination loop from burning through the entire daily budget.
        """
        executed = 0
        _dream_start = time.monotonic()
        _MAX_DREAM_MINUTES = 10  # Time cap per dream cycle (was: 3-task cap)
        max_tasks_per_dream = 50  # Safety hard cap (effectively unlimited)
        _cycle_budget = _get_dream_cycle_budget()
        _cycle_cost = 0.0  # Accumulates cost across all tasks this cycle

        # Resume any tasks that were in-progress when we crashed/restarted.
        # These won't be returned by get_next_task() (which only returns PENDING),
        # but the PlanExecutor crash-recovery will pick up where it left off.
        for goal in self.goal_manager.goals.values():
            if self.stop_flag.is_set() or executed >= max_tasks_per_dream:
                break
            for task in goal.tasks:
                if task.status == TaskStatus.IN_PROGRESS:
                    logger.info("Resuming interrupted task: %s (%s)", task.description, task.task_id)
                    try:
                        result = self._execute_task(task)
                        _cycle_cost += result.get("cost_usd", 0)
                        self.goal_manager.complete_task(task.task_id, result)
                        self.goal_manager.save_state()
                        executed += 1
                        # Check if goal is now complete
                        if result.get("executed") and goal.is_complete():
                            _notify(
                                f"\U0001f3c6 Goal complete: {goal.description} "
                                f"({len(goal.tasks)} tasks finished)",
                                bypass_cooldown=True,
                            )
                        # Check per-cycle budget
                        if _cycle_cost >= _cycle_budget:
                            logger.warning(
                                "Dream cycle budget hit ($%.4f >= $%.2f) during resume",
                                _cycle_cost, _cycle_budget,
                            )
                            return executed
                    except Exception as e:
                        logger.error("Interrupted task resume failed: %s", e)
                        self.goal_manager.fail_task(task.task_id, str(e))

        # Decompose any undecomposed goals first (so tasks become available)
        total_goals = len(self.goal_manager.goals)
        undecomposed = [
            g for g in self.goal_manager.goals.values()
            if not g.is_decomposed and not g.is_complete()
        ]
        if total_goals == 0:
            logger.info("Dream cycle: no goals in goal_manager")
        elif not undecomposed:
            logger.info(
                "Dream cycle: %d goals but all decomposed or complete",
                total_goals,
            )
        # Decompose up to 5 goals per dream cycle (was limited to 1, causing
        # a backlog of 100+ undecomposed goals that never got worked on)
        decomposed_count = 0
        for goal in undecomposed[:5]:
            if self.stop_flag.is_set():
                break
            try:
                logger.info("Decomposing undecomposed goal: %s", goal.description)
                self.goal_manager.decompose_goal(
                    goal.goal_id,
                    self.model,
                    learning_hints=self.learning_system.get_active_insights(2),
                )
                self.goal_manager.save_state()
                decomposed_count += 1
                logger.info(
                    "Decomposed goal '%s' into %d task(s)",
                    goal.description, len(goal.tasks),
                )
            except Exception as e:
                logger.error("Goal decomposition failed: %s", e, exc_info=True)
        if decomposed_count:
            logger.info("Decomposed %d goals this cycle", decomposed_count)

        while executed < max_tasks_per_dream and not self.stop_flag.is_set():
            # Time-based cap: stop after _MAX_DREAM_MINUTES
            _elapsed_min = (time.monotonic() - _dream_start) / 60.0
            if _elapsed_min >= _MAX_DREAM_MINUTES:
                logger.info(
                    "Dream cycle time cap reached (%.1f min, %d tasks done)",
                    _elapsed_min, executed,
                )
                break

            # Cost-based cap: stop before one bad cycle eats the daily budget
            if _cycle_cost >= _cycle_budget:
                logger.warning(
                    "Dream cycle budget reached ($%.4f >= $%.2f, %d tasks done)",
                    _cycle_cost, _cycle_budget, executed,
                )
                break

            task = self.goal_manager.get_next_task()

            if not task:
                logger.info("No ready tasks to execute")
                break

            logger.info("Autonomously executing: %s", task.description)

            try:
                self.goal_manager.start_task(task.task_id)

                result = self._execute_task(task)
                _cycle_cost += result.get("cost_usd", 0)

                self.goal_manager.complete_task(task.task_id, result)

                self.goal_manager.save_state()

                executed += 1
                logger.info("Task completed: %s ($%.4f this cycle)", task.task_id, _cycle_cost)

                # Check if the parent goal is now fully complete (high-value notification)
                if result.get("executed"):
                    goal = self.goal_manager.goals.get(task.goal_id)
                    if goal and goal.is_complete():
                        _notify(
                            f"\U0001f3c6 Goal complete: {goal.description} "
                            f"({len(goal.tasks)} tasks finished)",
                            bypass_cooldown=True,
                        )

            except Exception as e:
                logger.error("Task execution failed: %s", e)
                self.goal_manager.fail_task(task.task_id, str(e))
                _notify(f"\u274c Task failed: {task.description} — {e}")
                break

        return executed

    def _execute_queued_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a manual queue task via process_message."""
        router = self._get_router()
        if not router:
            return {"executed": False, "error": "Router not available"}

        desc = task.get("description", "") or str(task.get("type", "unknown"))
        message = f"Complete this task: {desc}"

        try:
            from src.interfaces.action_executor import process_message

            response_text, actions_taken, cost = process_message(
                message=message,
                router=router,
                history=[],
                source="dream_cycle_queue",
                goal_manager=self.goal_manager,
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

    def set_router(self, router: Any) -> None:
        """Use shared ModelRouter (avoids loading model again)."""
        self._router = router

    def _get_router(self) -> Any:
        """Return shared or lazy-load ModelRouter for task execution."""
        if not hasattr(self, "_router") or self._router is None:
            try:
                import src.core.cuda_bootstrap  # noqa: F401
                from src.models.router import ModelRouter
                self._router = ModelRouter()
                logger.info("Dream cycle: model router initialized (lazy)")
            except Exception as e:
                logger.warning("Dream cycle: router not available: %s", e)
                self._router = None
        return self._router

    def _execute_task(self, task: Any) -> dict:
        """
        Execute a single task autonomously using the multi-step PlanExecutor.

        Instead of firing one action through the intent parser, this chains
        multiple steps: research -> create files -> verify -> done.
        This is the core upgrade that enables meaningful overnight work.

        Args:
            task: Task object to execute

        Returns:
            Execution result dict with executed, analysis, steps, cost, timestamp.
        """
        logger.info("Executing task (multi-step): %s", task.description)

        router = self._get_router()
        if not router:
            return {
                "executed": False,
                "error": "Model router not available",
                "analysis": "",
                "timestamp": datetime.now().isoformat(),
            }

        try:
            from src.core.plan_executor import PlanExecutor

            goal = self.goal_manager.goals[task.goal_id]
            # Pass learning context so PlanExecutor can record action stats
            # and inject past insights into step prompts
            hints = self.learning_system.get_active_insights(2)
            action_summary = self.learning_system.get_action_summary()
            if action_summary:
                hints.append(action_summary)
            executor = PlanExecutor(
                router=router,
                learning_system=self.learning_system,
                hints=hints if hints else None,
            )
            result = executor.execute(
                task_description=task.description,
                goal_context=goal.description,
                task_id=task.task_id,  # Enables crash recovery
            )

            success = result.get("success", False)
            steps = result.get("steps_taken", [])
            cost = result.get("total_cost", 0)

            # Build human-readable summary of what happened
            step_descriptions = []
            for s in steps:
                act = s.get("action", "?")
                if act == "done":
                    step_descriptions.append(f"Done: {s.get('summary', '')}")
                elif act == "think":
                    pass  # Skip internal reasoning in summary
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

            # Record for learning
            context = f"Goal: {goal.description}; Task: {task.description}"
            if success:
                self.learning_system.record_success(
                    context=context,
                    action=task.description,
                    outcome=analysis[:200],
                    lesson=None,
                )
            else:
                self.learning_system.record_failure(
                    context=context,
                    action=task.description,
                    outcome=analysis[:200],
                    lesson=None,
                )

            # Collect for morning report (persisted to disk to survive restarts)
            self._overnight_results.append({
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
            self._save_overnight_results()

            # Extract follow-up goals from research findings (closes the loop)
            if success and result.get("files_created"):
                try:
                    follow_ups = self._extract_follow_up_goals(
                        files_created=result["files_created"],
                        task_desc=task.description,
                        goal_desc=goal.description,
                    )
                    if follow_ups:
                        self._overnight_results[-1]["follow_up_goals"] = follow_ups
                        self._save_overnight_results()
                except Exception as fue:
                    logger.debug("Follow-up extraction skipped: %s", fue)

                # Evaluate for interesting findings to surface to Jesse
                try:
                    from src.core.interesting_findings import get_findings_queue
                    ifq = get_findings_queue()
                    ifq.evaluate_and_queue(
                        task_result=result,
                        files_created=result["files_created"],
                        goal_desc=goal.description,
                        task_desc=task.description,
                        router=router,
                    )
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
                if self.goal_manager and hasattr(task, "goal_id"):
                    goal = self.goal_manager.goals.get(task.goal_id)
                    if goal:
                        self.learning_system.record_failure(
                            context=f"Goal: {goal.description}; Task: {task.description}",
                            action=task.description,
                            outcome=str(e),
                            lesson=None,
                        )
            except Exception as ler:
                logger.debug("Could not record failure for learning: %s", ler)
            return {
                "executed": False,
                "error": str(e),
                "analysis": "",
                "timestamp": datetime.now().isoformat(),
            }

    # -- Research follow-up pipeline ----------------------------------------

    def _extract_follow_up_goals(
        self,
        files_created: list,
        task_desc: str,
        goal_desc: str,
    ) -> list:
        """Analyze completed research files and create 0-2 follow-up goals.

        This is the core "research → new goals" loop.  When a task produces
        reports, we read them back, ask the model what natural next steps
        emerge, and create new goals for the best ones.

        Guardrails:
        - Max 2 follow-ups per task (prevents explosion)
        - Respects _MAX_ACTIVE_GOALS cap
        - Uses existing _is_duplicate_goal() fuzzy matching

        Returns:
            List of created goal IDs (may be empty).
        """
        router = self._get_router()
        if not router or not self.goal_manager:
            return []

        if self._count_active_goals() >= self._MAX_ACTIVE_GOALS:
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

        findings_text = "\n\n".join(
            f"--- {name} ---\n{content}" for name, content in file_contents
        )

        prompt = f"""You completed this research task for Jesse:

Goal: {goal_desc}
Task: {task_desc}

Research findings:
{findings_text}

Based on these findings, suggest 0-2 SPECIFIC follow-up goals that:
1. Build directly on what was discovered (not generic)
2. Are achievable with web research + file creation
3. Are DIFFERENT from the original goal

If no natural follow-up exists, return an empty array [].

Return ONLY a JSON array (0-2 items):
[
  {{"description": "Specific next goal", "reasoning": "How it builds on findings"}}
]
JSON only:"""

        try:
            resp = router.generate(
                prompt=prompt, max_tokens=400, temperature=0.4, prefer_local=True,
            )
            text = resp.get("text", "")

            from src.utils.parsing import extract_json_array
            ideas = extract_json_array(text)

            if not isinstance(ideas, list):
                return []

            created_ids = []
            for idea in ideas[:2]:
                if not isinstance(idea, dict):
                    continue
                desc = (idea.get("description") or "").strip()
                if not desc:
                    continue
                if self._is_duplicate_goal(desc):
                    logger.debug("Follow-up skipped (duplicate): %s", desc[:60])
                    continue
                if self._count_active_goals() >= self._MAX_ACTIVE_GOALS:
                    break

                reasoning = idea.get("reasoning", "")[:100]
                goal = self.goal_manager.create_goal(
                    description=desc,
                    user_intent=f"Follow-up from: {goal_desc[:60]} — {reasoning}",
                    priority=4,  # Lower than user goals (5) and brainstorm (7)
                )
                created_ids.append(goal.goal_id)
                logger.info(
                    "Created follow-up goal: %s -> %s", desc[:60], goal.goal_id,
                )

            return created_ids

        except Exception as e:
            logger.debug("Follow-up extraction failed: %s", e)
            return []

    # -- Synthesis engine ----------------------------------------------------

    def _run_synthesis(self) -> None:
        """Combine findings from multiple completed goals into higher-level insights.

        Runs every 10 dream cycles.  Reads completed goal descriptions,
        identifies themes, and optionally creates an integrative follow-up goal.
        Results saved to data/synthesis_log.json (append-only JSONL).
        """
        router = self._get_router()
        if not router or not self.goal_manager:
            return

        if self.stop_flag.is_set():
            return

        completed = [
            g for g in self.goal_manager.goals.values()
            if g.is_complete()
        ]
        if len(completed) < 2:
            logger.debug("Synthesis skipped: fewer than 2 completed goals")
            return

        logger.info("=== SYNTHESIS START (%d completed goals) ===", len(completed))

        goal_lines = "\n".join(
            f"- {g.description[:100]}" for g in completed[-8:]
        )

        prompt = f"""You are Archi, reviewing completed research and tasks for Jesse.

Completed goals:
{goal_lines}

Identify:
1. Common themes across this work
2. An integrated insight or action plan that combines multiple findings
3. One specific follow-up goal that SYNTHESIZES across multiple completed goals

Return ONLY a JSON object:
{{
  "theme": "Overarching theme in 1 sentence",
  "integrated_insight": "How these findings connect (2-3 sentences)",
  "follow_up_goal": "Specific synthesis goal description (or empty string if none)"
}}
JSON only:"""

        try:
            resp = router.generate(
                prompt=prompt, max_tokens=400, temperature=0.4, prefer_local=True,
            )
            text = resp.get("text", "")

            from src.core.plan_executor import _extract_json
            parsed = _extract_json(text)
            if not parsed:
                return

            # Save to synthesis log
            synthesis_path = _base_path() / "data" / "synthesis_log.json"
            synthesis_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now().isoformat(),
                "goals_synthesized": len(completed),
                **parsed,
            }
            with open(synthesis_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

            logger.info(
                "Synthesis: theme='%s'", parsed.get("theme", "")[:80],
            )

            # Create integrative follow-up goal if suggested
            follow_up = (parsed.get("follow_up_goal") or "").strip()
            if (
                follow_up
                and not self._is_duplicate_goal(follow_up)
                and self._count_active_goals() < self._MAX_ACTIVE_GOALS
            ):
                goal = self.goal_manager.create_goal(
                    description=follow_up,
                    user_intent=f"Synthesis: {parsed.get('theme', '')[:80]}",
                    priority=6,  # Between brainstorm (7) and follow-up (4)
                )
                logger.info(
                    "Created synthesis goal: %s -> %s",
                    follow_up[:60], goal.goal_id,
                )

        except Exception as e:
            logger.debug("Synthesis failed: %s", e)

    def _review_history(self) -> List[str]:
        """Review recent actions and extract insights via learning system."""
        insights = []

        if self.stop_flag.is_set():
            return insights

        # Skip learning if disabled (mitigates CUDA crashes on unstable GPUs)
        if os.environ.get("ARCHI_SKIP_LEARNING", "").lower() in ("1", "true", "yes"):
            return insights

        # Run learning only every 3rd dream cycle to reduce GPU load (CUDA stability)
        cycle_count = len(self.dream_history)
        if cycle_count > 0 and cycle_count % 3 != 0:
            return insights

        logger.info("Reviewing recent history for insights...")

        try:
            if (
                self.model
                and len(self.learning_system.experiences) >= 5
            ):
                patterns = self.learning_system.extract_patterns(self.model)
                if patterns:
                    insights.extend(patterns[:3])
                suggestions = self.learning_system.get_improvement_suggestions(
                    self.model
                )
                if suggestions:
                    insights.extend(suggestions[:2])
        except (RuntimeError, OSError, MemoryError) as e:
            logger.warning(
                "Learning system skipped (likely GPU/CUDA): %s", e, exc_info=False
            )
        except Exception as e:
            logger.debug("Learning system review skipped: %s", e)

        return insights

    # -- Goal hygiene ----------------------------------------------------------

    _MAX_ACTIVE_GOALS = 25  # Hard cap — refuse to create more

    def _is_duplicate_goal(self, description: str) -> bool:
        """Fuzzy duplicate detection for goal descriptions.

        Catches: exact matches, substring containment, and high word overlap.
        Much more aggressive than exact string match to prevent goal bloat.
        """
        if not self.goal_manager:
            return False
        desc_lower = description.lower().strip()
        desc_words = set(desc_lower.split())
        # Remove very common filler words for better overlap detection
        _STOP = {"a", "an", "the", "and", "or", "to", "for", "in", "of", "on", "with", "is", "by"}
        desc_sig = desc_words - _STOP

        for g in self.goal_manager.goals.values():
            if g.is_complete():
                continue
            existing = g.description.lower().strip()
            # Exact match
            if desc_lower == existing:
                return True
            # Substring containment (either direction)
            if desc_lower in existing or existing in desc_lower:
                return True
            # High word overlap (Jaccard > 0.6)
            existing_words = set(existing.split()) - _STOP
            if desc_sig and existing_words:
                overlap = len(desc_sig & existing_words)
                union = len(desc_sig | existing_words)
                if union > 0 and overlap / union > 0.6:
                    return True
        return False

    def _prune_stale_goals(self) -> int:
        """Remove old undecomposed or empty goals to keep the list manageable.

        Returns number of goals pruned.
        """
        if not self.goal_manager:
            return 0
        now = datetime.now()
        to_remove = []
        for gid, g in self.goal_manager.goals.items():
            if g.is_complete():
                continue
            age_hours = (now - g.created_at).total_seconds() / 3600
            # Prune undecomposed goals older than 48 hours
            if not g.is_decomposed and age_hours > 48:
                to_remove.append(gid)
            # Prune decomposed goals where ALL tasks failed
            elif g.is_decomposed and g.tasks and all(
                t.status == TaskStatus.FAILED for t in g.tasks
            ):
                to_remove.append(gid)
        for gid in to_remove:
            del self.goal_manager.goals[gid]
        if to_remove:
            self.goal_manager.save_state()
            logger.info("Pruned %d stale goals: %s", len(to_remove), to_remove)
        return len(to_remove)

    def _count_active_goals(self) -> int:
        """Count goals that are not complete."""
        if not self.goal_manager:
            return 0
        return sum(1 for g in self.goal_manager.goals.values() if not g.is_complete())

    # -- Planning -------------------------------------------------------------

    def _plan_future_work(self) -> List[Dict[str, Any]]:
        """Plan proactive work based on Prime Directive and identity config.

        Creates actual goals from plans, with robust duplicate detection and
        a hard cap on active goals to prevent unbounded growth.
        """
        plans = []

        if self.stop_flag.is_set():
            return plans

        if not self.identity:
            return plans

        # Prune stale goals first
        self._prune_stale_goals()

        # Don't create new goals if we're already at the cap
        active = self._count_active_goals()
        if active >= self._MAX_ACTIVE_GOALS:
            logger.info(
                "Skipping plan creation: %d active goals (cap=%d)",
                active, self._MAX_ACTIVE_GOALS,
            )
            return plans

        logger.info("Planning future work...")
        current_hour = datetime.now().hour
        proactive = self.identity.get("proactive_tasks", {})

        # During night hours (2-5 AM), do deeper research and optimization
        if 2 <= current_hour <= 5:
            research = proactive.get("research", [])
            if research:
                idx = len(self.dream_history) % len(research)
                task = research[idx]
                plans.append({"type": "research", "description": task, "priority": 5})
                logger.info("Planned research: %s", task)

            optimization = proactive.get("optimization", [])
            if optimization:
                idx = len(self.dream_history) % len(optimization)
                task = optimization[idx]
                plans.append({"type": "optimization", "description": task, "priority": 4})
                logger.info("Planned optimization: %s", task)

        # Monitoring every 5th cycle
        monitoring = proactive.get("monitoring", [])
        if monitoring and len(self.dream_history) % 5 == 0:
            task = monitoring[0]
            plans.append({"type": "monitoring", "description": task, "priority": 7})
            logger.info("Planned monitoring: %s", task)

        # Indexing/organization every 10 cycles
        indexing = proactive.get("indexing", [])
        if indexing and len(self.dream_history) % 10 == 0:
            task = indexing[0]
            plans.append({"type": "indexing", "description": task, "priority": 3})
            logger.info("Planned indexing: %s", task)

        # Convert plans into actual goals (with fuzzy duplicate detection + cap)
        if plans and self.goal_manager:
            for plan in plans:
                if self._count_active_goals() >= self._MAX_ACTIVE_GOALS:
                    logger.info("Goal cap reached, skipping remaining plans")
                    break
                desc = plan["description"]
                if self._is_duplicate_goal(desc):
                    logger.info("Skipping duplicate plan: %s", desc[:60])
                    continue
                try:
                    goal = self.goal_manager.create_goal(
                        description=desc,
                        user_intent=f"Proactive {plan['type']} (auto-planned from identity config)",
                        priority=plan.get("priority", 5),
                    )
                    logger.info(
                        "Created goal from plan: %s -> %s", desc[:60], goal.goal_id,
                    )
                except Exception as e:
                    logger.warning("Failed to create goal from plan: %s", e)

        return plans

    # -- Idea pipeline -----------------------------------------------------

    def _brainstorm_ideas(self) -> None:
        """Generate improvement ideas and create a goal for the best one.

        Runs at most once per 24 hours, during night hours (11 PM - 5 AM).
        Uses the focus areas from archi_identity.yaml to guide brainstorming.
        Scores ideas by estimated benefit-per-hour and picks the winner.
        """
        now = datetime.now()

        # Only brainstorm during night hours
        if not (23 <= now.hour or now.hour <= 5):
            return

        # At most once per 24 hours
        if self._last_brainstorm and (now - self._last_brainstorm).total_seconds() < 86400:
            return

        # Need a router and goal manager
        router = self._get_router()
        if not router or not self.goal_manager:
            return

        if self.stop_flag.is_set():
            return

        logger.info("=== IDEA BRAINSTORM START ===")
        self._last_brainstorm = now

        # Load focus areas and current goals for context
        focus_areas = self.identity.get("focus_areas", [])
        if not focus_areas:
            focus_areas = ["Health", "Wealth", "Happiness", "Capability"]

        existing_goals = [
            g.description for g in self.goal_manager.goals.values()
            if not g.is_complete()
        ]
        existing_block = ""
        if existing_goals:
            existing_block = "\n\nCurrent active goals (avoid duplicates):\n" + "\n".join(
                f"- {g}" for g in existing_goals[:10]
            )

        # Inject lessons learned from past work (closes the learning loop)
        lessons_block = ""
        try:
            insights = self.learning_system.get_active_insights(3)
            action_summary = self.learning_system.get_action_summary()
            if insights or action_summary:
                parts = []
                if insights:
                    parts.extend(f"- {i}" for i in insights)
                if action_summary:
                    parts.append(f"- Tool reliability: {action_summary}")
                lessons_block = "\n\nLessons from past work:\n" + "\n".join(parts)
        except Exception:
            pass

        # Include summaries of recently completed goals so new ideas build on prior work
        completed_block = ""
        try:
            completed_goals = [
                g for g in self.goal_manager.goals.values()
                if g.is_complete()
            ]
            if completed_goals:
                summaries = [g.description[:80] for g in completed_goals[-5:]]
                completed_block = "\n\nRecently completed work (build on these):\n" + "\n".join(
                    f"- {s}" for s in summaries
                )
        except Exception:
            pass

        # Ask the model to brainstorm
        prompt = f"""You are Archi, an autonomous AI agent focused on improving Jesse's life.

Focus areas:
{chr(10).join('- ' + fa for fa in focus_areas)}

Your capabilities: web research, creating files/reports, analyzing data, organizing information.
You CANNOT: spend money, contact people, install software, or access external accounts.
{existing_block}{lessons_block}{completed_block}

Generate 3-5 specific, actionable improvement ideas that you can work on TONIGHT while Jesse sleeps.
Each idea should BUILD ON prior completed work when possible, and apply lessons learned.
Each idea should be something you can actually DO with web research + file creation.

Return ONLY a JSON array:
[
  {{
    "category": "Health|Wealth|Happiness|Capability|Agency|Synthesis",
    "description": "Specific actionable task description",
    "benefit": 1-10,
    "estimated_hours": 0.1-2.0,
    "reasoning": "Why this is valuable"
  }}
]

Be creative but realistic about what you can accomplish overnight. Prefer research + report tasks.
JSON only:"""

        try:
            resp = router.generate(
                prompt=prompt, max_tokens=800, temperature=0.7, prefer_local=True,
            )
            text = resp.get("text", "")

            # Parse ideas
            from src.utils.parsing import extract_json_array
            ideas = extract_json_array(text)

            if not isinstance(ideas, list) or not ideas:
                logger.warning("Brainstorm produced no valid ideas")
                return

            # Score by benefit per hour and pick the best
            scored = []
            for idea in ideas:
                if not isinstance(idea, dict):
                    continue
                benefit = idea.get("benefit", 5)
                hours = max(idea.get("estimated_hours", 1), 0.1)
                score = benefit / hours
                idea["score"] = round(score, 1)
                scored.append(idea)

            scored.sort(key=lambda x: x["score"], reverse=True)

            # Save all ideas to the backlog
            backlog_path = _base_path() / "data" / "idea_backlog.json"
            backlog = {"ideas": [], "last_brainstorm": now.isoformat()}
            if backlog_path.exists():
                try:
                    with open(backlog_path, "r", encoding="utf-8") as f:
                        backlog = json.load(f)
                except Exception:
                    pass

            for idea in scored:
                backlog.setdefault("ideas", []).append({
                    **idea,
                    "created_at": now.isoformat(),
                    "status": "pending",
                })
            backlog["last_brainstorm"] = now.isoformat()

            with open(backlog_path, "w", encoding="utf-8") as f:
                json.dump(backlog, f, indent=2)

            # Create a goal for the highest-scoring idea (if not duplicate and under cap)
            best = scored[0]
            desc = best.get("description", "")
            category = best.get("category", "General")
            if desc and not self._is_duplicate_goal(desc) and self._count_active_goals() < self._MAX_ACTIVE_GOALS:
                goal = self.goal_manager.create_goal(
                    description=desc,
                    user_intent=f"Auto-brainstormed ({category}, score={best['score']}): {best.get('reasoning', '')}",
                    priority=7,  # High priority for overnight work
                )
                logger.info(
                    "Brainstorm winner: [%s] %s (score=%.1f) -> %s",
                    category, desc[:80], best["score"], goal.goal_id,
                )
                # Mark it in the backlog
                backlog["ideas"][-len(scored)]["status"] = "goal_created"
                backlog["ideas"][-len(scored)]["goal_id"] = goal.goal_id
                with open(backlog_path, "w", encoding="utf-8") as f:
                    json.dump(backlog, f, indent=2)

            logger.info(
                "=== IDEA BRAINSTORM END (%d ideas, best score=%.1f) ===",
                len(scored), scored[0]["score"] if scored else 0,
            )

        except Exception as e:
            logger.error("Brainstorm failed: %s", e, exc_info=True)

    # -- Morning report ----------------------------------------------------

    def _load_overnight_results(self) -> None:
        """Restore overnight results from disk (survives restarts)."""
        try:
            if self._overnight_results_path.exists():
                with open(self._overnight_results_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._overnight_results = data
                    logger.info("Loaded %d overnight results from disk", len(data))
        except Exception as e:
            logger.debug("Could not load overnight results: %s", e)

    def _save_overnight_results(self) -> None:
        """Persist overnight results to disk."""
        try:
            self._overnight_results_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._overnight_results_path, "w", encoding="utf-8") as f:
                json.dump(self._overnight_results, f, indent=2)
        except Exception as e:
            logger.debug("Could not save overnight results: %s", e)

    def _send_morning_report(self) -> None:
        """Compile and send a summary of overnight work via Discord DM.

        Runs once per morning (6-9 AM).  Collects all task results from
        the overnight session and formats them into a readable report.
        """
        if not self._overnight_results:
            logger.info("Morning report: nothing to report (no overnight work)")
            self._morning_report_sent = datetime.now().date()
            return

        logger.info("Compiling morning report (%d results)", len(self._overnight_results))

        lines = ["\U0001f305 **Good morning, Jesse! Here's what I worked on overnight:**\n"]

        successes = [r for r in self._overnight_results if r.get("success")]
        failures = [r for r in self._overnight_results if not r.get("success")]
        total_cost = sum(r.get("cost", 0) for r in self._overnight_results)

        if successes:
            lines.append(f"\u2705 **Completed ({len(successes)}):**")
            for r in successes:
                verified_tag = " \u2714\ufe0f" if r.get("verified") else ""
                lines.append(f"  \u2022 {r['task']}{verified_tag}")
                if r.get("summary"):
                    lines.append(f"    {r['summary'][:150]}")
                # Show files created
                files = r.get("files_created", [])
                if files:
                    filenames = [os.path.basename(f) for f in files[:3]]
                    lines.append(f"    \U0001f4c4 Files: {', '.join(filenames)}")

        if failures:
            lines.append(f"\n\u26a0\ufe0f **Needs attention ({len(failures)}):**")
            for r in failures:
                lines.append(f"  \u2022 {r['task']}")

        lines.append(f"\n\U0001f4b0 Cost: ${total_cost:.4f}")

        # Check idea backlog for new ideas
        try:
            backlog_path = _base_path() / "data" / "idea_backlog.json"
            if backlog_path.exists():
                with open(backlog_path, "r", encoding="utf-8") as f:
                    backlog = json.load(f)
                pending = [i for i in backlog.get("ideas", []) if i.get("status") == "pending"]
                if pending:
                    lines.append(f"\n\U0001f4a1 **Ideas in backlog:** {len(pending)}")
                    top3 = sorted(pending, key=lambda x: x.get("score", 0), reverse=True)[:3]
                    for idea in top3:
                        cat = idea.get("category", "?")
                        desc = idea.get("description", "")[:80]
                        lines.append(f"  \u2022 [{cat}] {desc}")
        except Exception:
            pass

        report = "\n".join(lines)

        # Append one interesting finding if available
        try:
            from src.core.interesting_findings import get_findings_queue
            ifq = get_findings_queue()
            finding = ifq.get_next_undelivered()
            if finding:
                report += f"\n\n\U0001f4a1 **Something interesting:** {finding['summary']}"
                ifq.mark_delivered(finding["id"])
        except Exception:
            pass

        _notify(report, bypass_cooldown=True)
        logger.info("Morning report sent (%d chars)", len(report))

        # Reset overnight results (memory + disk)
        self._overnight_results.clear()
        self._morning_report_sent = datetime.now().date()
        try:
            if self._overnight_results_path.exists():
                self._overnight_results_path.unlink()
        except Exception:
            pass

    def _send_hourly_summary(self) -> None:
        """Send a summary of accumulated dream-cycle work (hourly).

        Replaces the per-cycle spam with a single hourly digest.
        """
        results = self._hourly_task_results
        if not results:
            return

        successes = [r for r in results if r.get("success")]
        failures = [r for r in results if not r.get("success")]

        lines = []
        lines.append(
            f"\U0001f4cb **Hourly update** — {len(successes)} tasks completed"
            + (f", {len(failures)} failed" if failures else "")
        )

        for r in results[:10]:  # Cap at 10 to avoid huge messages
            icon = "\u2705" if r.get("success") else "\u274c"
            task_desc = r.get("task", "Unknown task")
            if len(task_desc) > 70:
                task_desc = task_desc[:67] + "..."
            lines.append(f"  {icon} {task_desc}")
            files = r.get("files_created", [])
            if files:
                names = [os.path.basename(f) for f in files[:3]]
                lines.append(f"    \U0001f4c4 {', '.join(names)}")

        if len(results) > 10:
            lines.append(f"  ... and {len(results) - 10} more")

        # Append one interesting finding if available
        try:
            from src.core.interesting_findings import get_findings_queue
            ifq = get_findings_queue()
            finding = ifq.get_next_undelivered()
            if finding:
                lines.append(f"\n\U0001f4a1 {finding['summary']}")
                ifq.mark_delivered(finding["id"])
        except Exception:
            pass

        _notify("\n".join(lines), bypass_cooldown=True)
        logger.info("Hourly summary sent (%d tasks)", len(results))

        self._hourly_task_results.clear()
        self._last_hourly_notify = time.monotonic()

    def get_status(self) -> Dict[str, Any]:
        """Get current dream cycle status."""
        idle_time = (datetime.now() - self.last_activity).total_seconds()

        return {
            "is_dreaming": self.is_dreaming,
            "is_idle": self.is_idle(),
            "idle_seconds": idle_time,
            "queued_tasks": len(self.task_queue),
            "total_dreams": len(self.dream_history),
            "last_activity": self.last_activity.isoformat(),
            "overnight_results": len(self._overnight_results),
            "morning_report_sent_today": self._morning_report_sent == datetime.now().date(),
            "last_brainstorm": self._last_brainstorm.isoformat() if self._last_brainstorm else None,
        }
