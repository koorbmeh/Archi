"""
Dream Cycle Engine - Proactive background processing.

Archi runs "dream cycles" when idle, processing queued tasks,
improving itself, and pursuing long-term goals autonomously.
"""

import logging
import time
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List

from src.core.goal_manager import GoalManager
from src.core.learning_system import LearningSystem
from src.models.local_model import LocalModel

logger = logging.getLogger(__name__)


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

        logger.info(
            f"Dream cycle initialized (idle threshold: {idle_threshold_seconds}s)"
        )

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
        logger.info(f"Queued task: {task.get('description', 'Unknown')}")

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
        """Stop dream cycle monitoring."""
        self.stop_flag.set()
        if self.dream_thread:
            self.dream_thread.join(timeout=5)
        logger.info("Dream cycle monitoring stopped")

    def _monitor_loop(self):
        """Background thread that monitors for idle periods."""
        while not self.stop_flag.is_set():
            if self.is_idle() and not self.is_dreaming:
                logger.info("Idle detected, starting dream cycle")
                self._run_dream_cycle()

            time.sleep(self.check_interval)

    def _run_dream_cycle(self):
        """Execute a dream cycle (background processing)."""
        self.is_dreaming = True
        dream_start = datetime.now()

        try:
            logger.info("=== DREAM CYCLE START ===")

            # Process queued tasks
            tasks_processed = self._process_task_queue()

            # Review recent history (learning)
            insights = self._review_history()

            # Plan future work
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
                f"=== DREAM CYCLE END (duration: {dream_duration:.1f}s) ==="
            )

        except Exception as e:
            logger.error(f"Dream cycle error: {e}", exc_info=True)
        finally:
            self.is_dreaming = False
            self.stop_flag.clear()

    def _process_task_queue(self) -> int:
        """Process queued background tasks."""
        processed = 0

        # First, process manual queue
        while self.task_queue and not self.stop_flag.is_set():
            task = self.task_queue.pop(0)

            try:
                logger.info(f"Processing queued task: {task.get('description')}")
                processed += 1

            except Exception as e:
                logger.error(f"Task processing error: {e}")

        # Then, autonomous goal-driven work
        if self.autonomous_mode and self.goal_manager and self.model:
            processed += self._execute_autonomous_tasks()

        return processed

    def _execute_autonomous_tasks(self) -> int:
        """Execute tasks from goal manager autonomously."""
        executed = 0
        max_tasks_per_dream = 3  # Limit to avoid long dream cycles

        # Decompose any undecomposed goals first (so tasks become available)
        undecomposed = [
            g for g in self.goal_manager.goals.values()
            if not g.is_decomposed and not g.is_complete()
        ]
        for goal in undecomposed:
            if self.stop_flag.is_set():
                break
            try:
                logger.info("Decomposing undecomposed goal: %s", goal.description)
                self.goal_manager.decompose_goal(goal.goal_id, self.model)
                self.goal_manager.save_state()
                break  # Decompose one per dream cycle
            except Exception as e:
                logger.error("Goal decomposition failed: %s", e, exc_info=True)

        while executed < max_tasks_per_dream and not self.stop_flag.is_set():
            task = self.goal_manager.get_next_task()

            if not task:
                logger.info("No ready tasks to execute")
                break

            logger.info(f"Autonomously executing: {task.description}")

            try:
                self.goal_manager.start_task(task.task_id)

                result = self._execute_task(task)

                self.goal_manager.complete_task(task.task_id, result)

                self.goal_manager.save_state()

                executed += 1
                logger.info(f"Task completed: {task.task_id}")

            except Exception as e:
                logger.error(f"Task execution failed: {e}")
                self.goal_manager.fail_task(task.task_id, str(e))
                break

        return executed

    def _get_router(self) -> Any:
        """Lazy-load ModelRouter for task execution."""
        if not hasattr(self, "_router") or self._router is None:
            try:
                import src.core.cuda_bootstrap  # noqa: F401
                from src.models.router import ModelRouter
                self._router = ModelRouter()
                logger.info("Dream cycle: model router initialized")
            except Exception as e:
                logger.warning("Dream cycle: router not available: %s", e)
                self._router = None
        return self._router

    def _execute_task(self, task: Any) -> dict:
        """
        Execute a single task autonomously using action_executor (connects to tools).

        Args:
            task: Task object to execute

        Returns:
            Execution result
        """
        logger.info(f"Executing task: {task.description}")

        router = self._get_router()
        if not router:
            return {
                "executed": False,
                "error": "Model router not available",
                "analysis": "",
                "timestamp": datetime.now().isoformat(),
            }

        try:
            from src.interfaces.action_executor import process_message

            # Task description as the message - action_executor parses intent and executes
            goal = self.goal_manager.goals[task.goal_id]
            message = (
                f"Complete this task: {task.description} "
                f"(Goal context: {goal.description})"
            )

            response_text, actions_taken, cost = process_message(
                message=message,
                router=router,
                history=[],
                source="dream_cycle",
                goal_manager=self.goal_manager,
            )

            # Consider success if we took actions, or got a non-error response
            success = len(actions_taken) > 0 or (
                response_text
                and "error" not in response_text.lower()
                and "couldn't" not in response_text.lower()
            )
            logger.info(
                "Task execution: %s (actions=%d)",
                "success" if success else "failed",
                len(actions_taken),
            )

            # Record experience for learning
            goal = self.goal_manager.goals[task.goal_id]
            context = f"Goal: {goal.description}; Task: {task.description}"
            if success:
                self.learning_system.record_success(
                    context=context,
                    action=task.description,
                    outcome=response_text[:200] if response_text else "Executed",
                    lesson=None,
                )
            else:
                self.learning_system.record_failure(
                    context=context,
                    action=task.description,
                    outcome=response_text[:200] if response_text else "No actions taken",
                    lesson=None,
                )

            return {
                "executed": success,
                "analysis": response_text,
                "actions_taken": [a.get("description", "") for a in actions_taken],
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
        except Exception as e:
            logger.debug("Learning system review skipped: %s", e)

        return insights

    def _plan_future_work(self) -> List[Dict[str, Any]]:
        """Plan what to work on next."""
        plans = []

        if self.stop_flag.is_set():
            return plans

        # Placeholder: Check goals, prioritize, schedule work
        logger.info("Planning future work...")

        return plans

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
        }
