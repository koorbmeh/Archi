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
        self._overnight_results: List[Dict[str, Any]] = []  # Collects results for morning report

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
                # Don't start a dream cycle while image/video generation owns the GPU
                try:
                    from src.tools.image_gen import generating_in_progress as img_gen
                    if img_gen:
                        logger.debug("Idle but image generation in progress — skipping dream cycle")
                        time.sleep(self.check_interval)
                        continue
                except ImportError:
                    pass
                try:
                    from src.tools.video_gen import generating_in_progress as vid_gen
                    if vid_gen:
                        logger.debug("Idle but video generation in progress — skipping dream cycle")
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
            tasks_processed = self._process_task_queue()

            # Phase 3: Review recent history (learning)
            insights = self._review_history()

            # Phase 4: Plan future work
            plans = self._plan_future_work()

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

            # Only notify when real work was done (tasks executed).
            # Plans & insights are internal bookkeeping — not worth a DM.
            if tasks_processed > 0:
                _notify(
                    f"\U0001f4ad Dream cycle finished ({dream_duration:.0f}s): "
                    f"completed {tasks_processed} task(s)"
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
        """Execute tasks from goal manager autonomously."""
        executed = 0
        max_tasks_per_dream = 3  # Limit to avoid long dream cycles

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
        for goal in undecomposed:
            if self.stop_flag.is_set():
                break
            try:
                logger.info("Decomposing undecomposed goal: %s", goal.description)
                self.goal_manager.decompose_goal(goal.goal_id, self.model)
                self.goal_manager.save_state()
                logger.info(
                    "Decomposed goal '%s' into %d task(s)",
                    goal.description, len(goal.tasks),
                )
                break  # Decompose one per dream cycle
            except Exception as e:
                logger.error("Goal decomposition failed: %s", e, exc_info=True)

        while executed < max_tasks_per_dream and not self.stop_flag.is_set():
            task = self.goal_manager.get_next_task()

            if not task:
                logger.info("No ready tasks to execute")
                break

            logger.info("Autonomously executing: %s", task.description)

            try:
                self.goal_manager.start_task(task.task_id)

                result = self._execute_task(task)

                self.goal_manager.complete_task(task.task_id, result)

                self.goal_manager.save_state()

                executed += 1
                logger.info("Task completed: %s", task.task_id)

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
            executor = PlanExecutor(router=router)
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

            # Collect for morning report
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

    def _plan_future_work(self) -> List[Dict[str, Any]]:
        """Plan proactive work based on Prime Directive and identity config.

        Unlike the previous version which only returned plans, this now
        creates actual goals from the plans so they get executed in
        subsequent dream cycles.  Duplicate detection prevents the same
        plan from being re-created every cycle.
        """
        plans = []

        if self.stop_flag.is_set():
            return plans

        if not self.identity:
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

        # Convert plans into actual goals (with duplicate detection)
        if plans and self.goal_manager:
            existing_descriptions = {
                g.description.lower()
                for g in self.goal_manager.goals.values()
                if not g.is_complete()
            }
            for plan in plans:
                desc = plan["description"]
                if desc.lower() not in existing_descriptions:
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

        # Ask the model to brainstorm
        prompt = f"""You are Archi, an autonomous AI agent focused on improving Jesse's life.

Focus areas:
{chr(10).join('- ' + fa for fa in focus_areas)}

Your capabilities: web research, creating files/reports, analyzing data, organizing information.
You CANNOT: spend money, contact people, install software, or access external accounts.
{existing_block}

Generate 3-5 specific, actionable improvement ideas that you can work on TONIGHT while Jesse sleeps.
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

            # Create a goal for the highest-scoring idea
            best = scored[0]
            desc = best.get("description", "")
            category = best.get("category", "General")
            if desc:
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

        _notify(report, bypass_cooldown=True)
        logger.info("Morning report sent (%d chars)", len(report))

        # Reset overnight results
        self._overnight_results.clear()
        self._morning_report_sent = datetime.now().date()

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
