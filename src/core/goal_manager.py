"""
Goal Manager - Decompose and track complex goals.

Breaks user goals into actionable subtasks, tracks progress,
and manages dependencies.
"""

import json
import logging
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.utils.parsing import extract_json_array as _extract_json_array

logger = logging.getLogger(__name__)

class TaskStatus(Enum):
    """Status of a task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"

class Task:
    """A single actionable task."""

    def __init__(
        self,
        task_id: str,
        description: str,
        goal_id: str,
        priority: int = 5,
        dependencies: Optional[List[str]] = None,
        estimated_duration_minutes: int = 30,
    ):
        self.task_id = task_id
        self.description = description
        self.goal_id = goal_id
        self.priority = priority
        self.dependencies = dependencies or []
        self.estimated_duration_minutes = estimated_duration_minutes
        self.status = TaskStatus.PENDING
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None

    def can_start(self, completed_task_ids: set) -> bool:
        """Check if all dependencies are completed."""
        return all(dep_id in completed_task_ids for dep_id in self.dependencies)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "task_id": self.task_id,
            "description": self.description,
            "goal_id": self.goal_id,
            "priority": self.priority,
            "dependencies": self.dependencies,
            "estimated_duration_minutes": self.estimated_duration_minutes,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result": self.result,
            "error": self.error,
        }

class Goal:
    """A high-level goal that can be decomposed into tasks."""

    def __init__(
        self,
        goal_id: str,
        description: str,
        user_intent: str,
        priority: int = 5,
    ):
        self.goal_id = goal_id
        self.description = description
        self.user_intent = user_intent
        self.priority = priority
        self.created_at = datetime.now()
        self.tasks: List[Task] = []
        self.is_decomposed = False
        self.completion_percentage = 0.0

    def add_task(self, task: Task) -> None:
        """Add a task to this goal."""
        self.tasks.append(task)

    def get_ready_tasks(self) -> List[Task]:
        """Get tasks that are ready to execute (dependencies met)."""
        completed_ids = {
            t.task_id for t in self.tasks if t.status == TaskStatus.COMPLETED
        }

        return [
            t
            for t in self.tasks
            if t.status == TaskStatus.PENDING and t.can_start(completed_ids)
        ]

    def update_progress(self) -> None:
        """Update completion percentage based on task status."""
        if not self.tasks:
            self.completion_percentage = 0.0
            return

        completed = sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)
        self.completion_percentage = (completed / len(self.tasks)) * 100.0

    def is_complete(self) -> bool:
        """Check if all tasks are completed.

        A goal with no tasks is NOT complete — it hasn't been decomposed yet.
        (Python's all() returns True for empty iterables, which previously
        caused every undecomposed goal to look 'complete' and get skipped.)
        """
        return bool(self.tasks) and all(
            t.status == TaskStatus.COMPLETED for t in self.tasks
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "goal_id": self.goal_id,
            "description": self.description,
            "user_intent": self.user_intent,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
            "is_decomposed": self.is_decomposed,
            "completion_percentage": self.completion_percentage,
            "tasks": [t.to_dict() for t in self.tasks],
        }

class GoalManager:
    """
    Manages goals and their decomposition into tasks.

    Takes high-level user goals, breaks them down into actionable
    tasks with dependencies, and tracks progress.
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else Path("data")
        self.data_dir.mkdir(exist_ok=True)

        self.goals: Dict[str, Goal] = {}
        self.next_goal_id = 1
        self.next_task_id = 1

        self._load_state()
        logger.info("Goal Manager initialized")

    def _load_state(self) -> None:
        """Load goals and tasks from disk."""
        state_file = self.data_dir / "goals_state.json"
        if not state_file.exists():
            logger.info("No existing goals state found")
            return

        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.next_goal_id = data.get("next_goal_id", 1)
            self.next_task_id = data.get("next_task_id", 1)

            for goal_data in data.get("goals", []):
                goal = Goal(
                    goal_id=goal_data["goal_id"],
                    description=goal_data["description"],
                    user_intent=goal_data.get("user_intent", ""),
                    priority=goal_data.get("priority", 5),
                )
                if goal_data.get("created_at"):
                    try:
                        goal.created_at = datetime.fromisoformat(goal_data["created_at"])
                    except (ValueError, TypeError):
                        pass
                goal.is_decomposed = goal_data.get("is_decomposed", False)
                goal.completion_percentage = goal_data.get("completion_percentage", 0.0)

                for task_data in goal_data.get("tasks", []):
                    task = Task(
                        task_id=task_data["task_id"],
                        description=task_data["description"],
                        goal_id=goal.goal_id,
                        priority=task_data.get("priority", 5),
                        dependencies=task_data.get("dependencies", []),
                        estimated_duration_minutes=task_data.get(
                            "estimated_duration_minutes", 30
                        ),
                    )
                    status_str = task_data.get("status", "pending")
                    try:
                        task.status = TaskStatus(status_str)
                    except ValueError:
                        task.status = TaskStatus.PENDING
                    if task_data.get("created_at"):
                        try:
                            task.created_at = datetime.fromisoformat(task_data["created_at"])
                        except (ValueError, TypeError):
                            pass
                    if task_data.get("started_at"):
                        try:
                            task.started_at = datetime.fromisoformat(task_data["started_at"])
                        except (ValueError, TypeError):
                            pass
                    if task_data.get("completed_at"):
                        try:
                            task.completed_at = datetime.fromisoformat(task_data["completed_at"])
                        except (ValueError, TypeError):
                            pass
                    task.result = task_data.get("result")
                    task.error = task_data.get("error")
                    goal.add_task(task)

                self.goals[goal.goal_id] = goal

            logger.info("Loaded %d goals from disk", len(self.goals))

        except Exception as e:
            logger.error("Error loading goals state: %s", e, exc_info=True)

    def prune_duplicates(self) -> int:
        """Remove duplicate and redundant goals, keeping the oldest of each group.

        Uses fuzzy matching (substring containment + word overlap > 0.6).
        Only prunes goals that have NOT been decomposed or completed.
        Returns the number of goals removed.
        """
        _STOP = {"a", "an", "the", "and", "or", "to", "for", "in", "of", "on", "with", "is", "by"}
        keep: Dict[str, str] = {}  # normalized_key -> goal_id (first seen wins)
        to_remove = []

        # Process in creation order (oldest first = keep oldest)
        sorted_goals = sorted(self.goals.values(), key=lambda g: g.created_at)
        for g in sorted_goals:
            desc_lower = g.description.lower().strip()
            desc_words = set(desc_lower.split()) - _STOP

            is_dup = False
            for kept_desc, kept_id in list(keep.items()):
                kept_words = set(kept_desc.split()) - _STOP
                # Substring match
                if desc_lower in kept_desc or kept_desc in desc_lower:
                    is_dup = True
                    break
                # Word overlap (Jaccard > 0.6)
                if desc_words and kept_words:
                    overlap = len(desc_words & kept_words)
                    union = len(desc_words | kept_words)
                    if union > 0 and overlap / union > 0.6:
                        is_dup = True
                        break

            if is_dup and not g.is_decomposed and not g.is_complete():
                to_remove.append(g.goal_id)
            else:
                keep[desc_lower] = g.goal_id

        for gid in to_remove:
            del self.goals[gid]

        if to_remove:
            self.save_state()
            logger.info(
                "Pruned %d duplicate goals (kept %d)", len(to_remove), len(self.goals)
            )
        return len(to_remove)

    def create_goal(
        self,
        description: str,
        user_intent: str,
        priority: int = 5,
    ) -> Goal:
        """
        Create a new goal.

        Args:
            description: What needs to be achieved
            user_intent: Why the user wants this
            priority: 1-10 (10 = highest)

        Returns:
            Goal object
        """
        goal_id = f"goal_{self.next_goal_id}"
        self.next_goal_id += 1

        goal = Goal(goal_id, description, user_intent, priority)
        self.goals[goal_id] = goal

        logger.info("Created goal: %s - %s", goal_id, description)
        self.save_state()
        return goal

    def decompose_goal(self, goal_id: str, model: Any) -> List[Task]:
        """
        Decompose a goal into actionable tasks using AI.

        Args:
            goal_id: Goal to decompose
            model: AI model with generate(prompt, max_tokens, temperature) -> {text}

        Returns:
            List of generated tasks
        """
        goal = self.goals.get(goal_id)
        if not goal:
            raise ValueError(f"Goal not found: {goal_id}")

        if goal.is_decomposed:
            logger.warning("Goal %s already decomposed", goal_id)
            return goal.tasks

        logger.info("Decomposing goal: %s", goal.description)

        prompt = f"""Break down this goal into specific, actionable tasks.

Goal: {goal.description}
User Intent: {goal.user_intent}

Create a task list with:
1. Clear, specific task descriptions
2. Estimated duration in minutes
3. Dependencies (use indices 0, 1, 2 for tasks that must complete first - 0 is first task)
4. Priority (1-10)

Return ONLY a JSON array of tasks:
[
  {{
    "description": "Task description",
    "estimated_duration_minutes": 30,
    "dependencies": [],
    "priority": 5
  }}
]

Be specific and actionable. Each task should be something that can be completed in one work session."""

        response = model.generate(
            prompt, max_tokens=1000, temperature=0.7, stop=[]
        )

        if not response.get("success", True):
            raise RuntimeError(
                f"Model generation failed: {response.get('error', 'Unknown error')}"
            )

        text = response.get("text", "").strip()
        if not text:
            raise RuntimeError(
                f"Model returned empty response. success={response.get('success')}, "
                f"error={response.get('error')}"
            )

        try:
            task_data = _extract_json_array(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse task list: %s", e)
            logger.error("Response: %s...", text[:500])
            raise

        if not isinstance(task_data, list):
            raise ValueError("Model response must be a JSON array")

        task_id_map: Dict[int, str] = {}  # index -> task_id

        for idx, task_info in enumerate(task_data):
            if not isinstance(task_info, dict):
                continue

            task_id = f"task_{self.next_task_id}"
            self.next_task_id += 1
            task_id_map[idx] = task_id

            # Resolve dependencies: "0", "1", 0, 1 or "task_1" -> task_1, task_2
            raw_deps = task_info.get("dependencies", [])
            resolved_deps: List[str] = []
            for d in raw_deps:
                dep_idx: Optional[int] = None
                if isinstance(d, int) and 0 <= d < idx:
                    dep_idx = d
                elif isinstance(d, str):
                    if d.isdigit():
                        di = int(d)
                        if 0 <= di < idx:
                            dep_idx = di
                    elif d.startswith("task_") and d[5:].isdigit():
                        dep_idx = int(d[5:]) - 1
                        if dep_idx < 0 or dep_idx >= idx:
                            dep_idx = None
                if dep_idx is not None and dep_idx in task_id_map:
                    resolved_deps.append(task_id_map[dep_idx])

            task = Task(
                task_id=task_id,
                description=task_info.get("description", "Unnamed task"),
                goal_id=goal_id,
                priority=task_info.get("priority", 5),
                dependencies=resolved_deps,
                estimated_duration_minutes=task_info.get(
                    "estimated_duration_minutes", 30
                ),
            )

            goal.add_task(task)
            logger.info("  Created task: %s - %s", task_id, task.description)

        goal.is_decomposed = True
        logger.info("Goal decomposed into %d tasks", len(goal.tasks))

        return goal.tasks

    def get_next_task(self) -> Optional[Task]:
        """
        Get the next task to work on (highest priority, dependencies met).

        Returns:
            Task to execute, or None if nothing ready
        """
        all_ready_tasks: List[Task] = []

        for goal in self.goals.values():
            if goal.is_complete():
                continue

            ready = goal.get_ready_tasks()
            all_ready_tasks.extend(ready)

        if not all_ready_tasks:
            return None

        all_ready_tasks.sort(
            key=lambda t: (
                -t.priority,
                -self.goals[t.goal_id].priority,
            )
        )

        return all_ready_tasks[0]

    def start_task(self, task_id: str) -> None:
        """Mark a task as in progress."""
        for goal in self.goals.values():
            for task in goal.tasks:
                if task.task_id == task_id:
                    task.status = TaskStatus.IN_PROGRESS
                    task.started_at = datetime.now()
                    logger.info("Started task: %s", task_id)
                    self.save_state()
                    return

        raise ValueError(f"Task not found: {task_id}")

    def complete_task(self, task_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        """Mark a task as completed."""
        for goal in self.goals.values():
            for task in goal.tasks:
                if task.task_id == task_id:
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = datetime.now()
                    task.result = result
                    goal.update_progress()
                    logger.info(
                        "Completed task: %s (%.1f%% of goal)",
                        task_id,
                        goal.completion_percentage,
                    )
                    self.save_state()
                    return

        raise ValueError(f"Task not found: {task_id}")

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark a task as failed."""
        for goal in self.goals.values():
            for task in goal.tasks:
                if task.task_id == task_id:
                    task.status = TaskStatus.FAILED
                    task.error = error
                    goal.update_progress()
                    logger.error("Task failed: %s - %s", task_id, error)
                    self.save_state()
                    return

        raise ValueError(f"Task not found: {task_id}")

    def get_status(self) -> Dict[str, Any]:
        """Get overall status of all goals and tasks."""
        return {
            "total_goals": len(self.goals),
            "active_goals": sum(
                1 for g in self.goals.values() if not g.is_complete()
            ),
            "total_tasks": sum(len(g.tasks) for g in self.goals.values()),
            "pending_tasks": sum(
                sum(1 for t in g.tasks if t.status == TaskStatus.PENDING)
                for g in self.goals.values()
            ),
            "in_progress_tasks": sum(
                sum(1 for t in g.tasks if t.status == TaskStatus.IN_PROGRESS)
                for g in self.goals.values()
            ),
            "completed_tasks": sum(
                sum(1 for t in g.tasks if t.status == TaskStatus.COMPLETED)
                for g in self.goals.values()
            ),
            "goals": [g.to_dict() for g in self.goals.values()],
        }

    def save_state(self) -> None:
        """Save goals and tasks to disk."""
        state_file = self.data_dir / "goals_state.json"

        state = {
            "next_goal_id": self.next_goal_id,
            "next_task_id": self.next_task_id,
            "goals": [g.to_dict() for g in self.goals.values()],
        }

        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

        logger.info("Saved goal state to %s", state_file)
