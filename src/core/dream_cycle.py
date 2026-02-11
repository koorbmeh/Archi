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

    def _execute_task(self, task: Any) -> dict:
        """
        Execute a single task autonomously.

        Args:
            task: Task object to execute

        Returns:
            Execution result
        """
        logger.info(f"Executing task: {task.description}")

        goal = self.goal_manager.goals[task.goal_id]
        prompt = f"""You are an autonomous AI agent executing a task.

Task: {task.description}
Goal: {goal.description}

Analyze what needs to be done and provide:
1. Steps to complete this task
2. Tools/resources needed
3. Expected outcome
4. Any blockers or concerns

Respond in JSON format:
{{
  "steps": ["step1", "step2", ...],
  "tools_needed": ["tool1", "tool2", ...],
  "expected_outcome": "description",
  "blockers": ["blocker1", ...],
  "can_complete_now": true/false,
  "reasoning": "explanation"
}}"""

        response = self.model.generate(
            prompt, max_tokens=500, temperature=0.3, stop=[]
        )

        return {
            "analysis": response.get("text", ""),
            "executed": False,  # Placeholder - actual execution comes later
            "timestamp": datetime.now().isoformat(),
        }

    def _review_history(self) -> List[str]:
        """Review recent actions and extract insights."""
        insights = []

        if self.stop_flag.is_set():
            return insights

        # Placeholder: Analyze logs, find patterns, learn from mistakes
        logger.info("Reviewing recent history for insights...")

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
