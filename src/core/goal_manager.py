"""
Goal Manager - Decompose and track complex goals.

Breaks user goals into actionable subtasks, tracks progress,
and manages dependencies.
"""

import json
import logging
import threading
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.utils.parsing import extract_json_array as _extract_json_array
from src.utils.config import get_user_name

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
        # Phase 5 Architect spec fields
        files_to_create: Optional[List[str]] = None,
        inputs: Optional[List[str]] = None,
        expected_output: Optional[str] = None,
        interfaces: Optional[List[str]] = None,
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
        self.deferred_until: Optional[datetime] = None  # Resume after this time
        # Architect spec fields (Phase 5) — concrete specs for workers
        self.files_to_create: List[str] = files_to_create or []
        self.inputs: List[str] = inputs or []
        self.expected_output: str = expected_output or ""
        self.interfaces: List[str] = interfaces or []

    def can_start(self, completed_task_ids: set) -> bool:
        """Check if all dependencies are completed."""
        return all(dep_id in completed_task_ids for dep_id in self.dependencies)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        d = {
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
            "deferred_until": self.deferred_until.isoformat() if self.deferred_until else None,
        }
        # Phase 5 Architect spec fields (only include if populated)
        if self.files_to_create:
            d["files_to_create"] = self.files_to_create
        if self.inputs:
            d["inputs"] = self.inputs
        if self.expected_output:
            d["expected_output"] = self.expected_output
        if self.interfaces:
            d["interfaces"] = self.interfaces
        return d

class Goal:
    """A high-level goal that can be decomposed into tasks."""

    def __init__(
        self,
        goal_id: str,
        description: str,
        user_intent: str,
        priority: int = 5,
        project_id: str = "",
        project_phase: int = 0,
    ):
        self.goal_id = goal_id
        self.description = description
        self.user_intent = user_intent
        self.priority = priority
        self.created_at = datetime.now()
        self.tasks: List[Task] = []
        self.is_decomposed = False
        self.completion_percentage = 0.0
        # Self-extension project tracking (Phase 4, session 238)
        self.project_id = project_id
        self.project_phase = project_phase

    def add_task(self, task: Task) -> None:
        """Add a task to this goal."""
        self.tasks.append(task)

    def get_ready_tasks(self) -> List[Task]:
        """Get tasks that are ready to execute (dependencies met, not deferred)."""
        now = datetime.now()
        completed_ids = {
            t.task_id for t in self.tasks if t.status == TaskStatus.COMPLETED
        }

        return [
            t
            for t in self.tasks
            if t.status == TaskStatus.PENDING
            and t.can_start(completed_ids)
            and (t.deferred_until is None or t.deferred_until <= now)
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

    def get_execution_waves(self) -> List[List["Task"]]:
        """Return tasks grouped into parallel execution waves.

        Wave N contains all tasks whose dependencies are fully satisfied
        by tasks in waves 0..N-1.  Tasks within a wave are independent
        and can run simultaneously.

        Useful for logging, debugging, and estimating total execution time.
        """
        completed: set = set()
        remaining = [t for t in self.tasks if t.status != TaskStatus.COMPLETED]
        waves: List[List["Task"]] = []

        # Include already-completed tasks in the "completed" set
        for t in self.tasks:
            if t.status == TaskStatus.COMPLETED:
                completed.add(t.task_id)

        while remaining:
            wave = [t for t in remaining if t.can_start(completed)]
            if not wave:
                break  # Deadlock or all remaining tasks have unmet deps
            waves.append(wave)
            for t in wave:
                completed.add(t.task_id)
            remaining = [t for t in remaining if t.task_id not in completed]

        return waves

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        d = {
            "goal_id": self.goal_id,
            "description": self.description,
            "user_intent": self.user_intent,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
            "is_decomposed": self.is_decomposed,
            "completion_percentage": self.completion_percentage,
            "tasks": [t.to_dict() for t in self.tasks],
        }
        # Only include project fields if set (avoids bloating non-project goals)
        if self.project_id:
            d["project_id"] = self.project_id
            d["project_phase"] = self.project_phase
        return d

def _build_decomposition_prompt(
    goal_description: str,
    goal_user_intent: str,
    learning_hints: Optional[List[str]] = None,
    discovery_brief: Optional[str] = None,
    user_prefs: Optional[str] = None,
) -> str:
    """Build the Architect prompt for goal decomposition.

    Assembles type-aware hints, learning context, discovery brief,
    and user preferences into a single prompt string.
    """
    # Build optional learning context
    hints_block = ""
    if learning_hints:
        hints_block = "\n\nLessons from past work (apply these):\n" + "\n".join(
            f"- {h}" for h in learning_hints[:3]
        ) + "\n"

    # Discovery brief block (Phase 5)
    discovery_block = ""
    if discovery_brief:
        discovery_block = (
            f"\nPROJECT CONTEXT (from Discovery scan):\n{discovery_brief}\n\n"
            "Use this context to ground your task specs. Reference actual files that exist.\n"
            "Follow the patterns and conventions described above. Don't duplicate existing work.\n"
        )

    # User Model preferences block (Phase 5)
    prefs_block = ""
    if user_prefs:
        prefs_block = f"\n{user_prefs}\n"

    # Output format preference (session 184)
    output_fmt_block = ""
    try:
        from src.core.user_model import get_user_model
        output_fmt_block = get_user_model().get_output_format_context()
    except Exception:
        pass

    # Type-aware decomposition hints (session 42)
    type_hints = _get_type_hints(goal_description)

    return f"""You are the Architect. Break this goal into 2-4 tasks with CONCRETE SPECS.
{type_hints}
Goal: {goal_description}
User Intent: {goal_user_intent}
{hints_block}{discovery_block}{prefs_block}{output_fmt_block}
MY AVAILABLE TOOLS (only use these):
- web_search: Search the web for information
- fetch_webpage: Read a specific URL's content
- create_file: Create/write a text file in workspace/
- read_file: Read a file's contents
- list_files: List directory contents
- append_file: Add content to an existing file
- write_source: Write Python source code
- run_python: Execute a Python snippet (can call built-in modules below)

MY BUILT-IN PYTHON MODULES (use via run_python, NOT web_search):
- src.monitoring.system_monitor.SystemMonitor: check_health() -> CPU, memory, disk, temp; log_metrics()
- src.monitoring.health_check.health_check: check_all() -> models, cache, storage status
- src.monitoring.cost_tracker.get_cost_tracker(): get_summary(), check_budget()
- src.monitoring.performance_monitor.performance_monitor: get_stats()

IMPORTANT: For system health, cost tracking, or performance monitoring tasks,
use run_python to call these modules directly. Do NOT web_search for this info.

I CANNOT: send emails, access databases, install software, make purchases, access external accounts, use APIs that need auth, or interact with GUI applications.

Return ONLY a JSON array (2-4 tasks, no more). Each task MUST include specs:
[
  {{
    "description": "Specific task description",
    "files_to_create": ["workspace/projects/X/output.py"],
    "inputs": ["existing_file.json", "web research on topic X"],
    "expected_output": "A working Python script that does X, tested with run_python",
    "interfaces": ["Imports data from task 0's output.json"],
    "estimated_duration_minutes": 15,
    "dependencies": [],
    "priority": 5
  }}
]

SPEC FIELDS — fill these for EVERY task:
- files_to_create: List of file paths this task will create or modify.
- inputs: What this task needs to start (files to read, data to research, user info to collect).
- expected_output: What "done" looks like — specific, verifiable. "A working X" not "research Y".
- interfaces: How this task connects to other tasks (reads their output, provides data for them).

CRITICAL — PRODUCE DELIVERABLES, DON'T JUST DESCRIBE THEM:
- Each task MUST produce a concrete deliverable the user can use or read.
- For research/information goals: the FINAL task should compile findings into the user's
  preferred output format (see OUTPUT FORMAT PREFERENCE above if present).
- Code (.py) is a MEANS to build things, not always the deliverable itself.
- Research is a MEANS. Every task that researches must also DELIVER using what it learned.

PARALLELISM — THINK ABOUT WHAT CAN RUN AT THE SAME TIME:
- Independent tasks get empty "dependencies" arrays — they run IN PARALLEL.
- Use "dependencies": [0] only if a task truly needs task 0's output.
- PREFER parallel structure. Don't chain tasks unless one needs the other's output.

CODE SIZE — KEEP write_source SMALL:
- Under 80 lines per write_source call. Break larger programs into multiple tasks.
- Good: "Write a focused 40-line script that does X, test it"
- Bad: "Write a complete CLI tool with config, input, error handling, and output"

ask_user — DON'T DUPLICATE QUESTIONS:
- Only ONE task should ask_user for a given piece of information.
- Other tasks depend on that task and read its output file.

Keep tasks concrete and achievable with the tools above."""


def _get_type_hints(goal_description: str) -> str:
    """Return type-aware decomposition hints based on goal type inference."""
    try:
        from src.core.opportunity_scanner import infer_opportunity_type
        opp_type = infer_opportunity_type(goal_description)
        if opp_type == "build":
            return (
                "\nTHIS IS A BUILD GOAL — PRODUCE CODE OR DATA STRUCTURES:\n"
                "- First task MUST create a working .py script, .json schema, or functional data file.\n"
                "- Use write_source + run_python to build and test. Iterate until it works.\n"
                "- Research is a MEANS — search only to inform what you build, not as the deliverable.\n"
                "- Follow-up tasks: test with real data, enhance, integrate with existing project files.\n"
            )
        if opp_type == "ask":
            user_name = get_user_name()
            return (
                f"\nTHIS IS A DATA-COLLECTION GOAL — START BY ASKING {user_name.upper()}:\n"
                f"- First task MUST use ask_user to request information from {user_name} "
                "(supplements, preferences, schedule, etc.)\n"
                f"- Second task: process {user_name}'s response into a structured format "
                "(.json, .py, database).\n"
                f"- DO NOT research what {user_name} already knows — ask him directly.\n"
                f"- If {user_name} doesn't respond, create a template he can fill in later.\n"
            )
        if opp_type == "fix":
            return (
                "\nTHIS IS A FIX GOAL — DIAGNOSE THEN SOLVE:\n"
                "- First task: read the relevant source file and error logs to understand the bug.\n"
                "- Second task: implement the fix using edit_file (preferred) or write_source.\n"
                "- Third task: test the fix with run_python or run_command (pytest).\n"
                "- DO NOT just describe the fix — actually implement it in code.\n"
            )
        if opp_type == "connect":
            return (
                "\nTHIS IS AN INTEGRATION GOAL — WIRE THINGS TOGETHER:\n"
                "- First task: read existing code/files to understand what needs connecting.\n"
                "- Second task: write integration code (a new script, a config change, "
                "a tool registration).\n"
                "- Test the integration works end-to-end before calling done.\n"
            )
    except ImportError:
        pass
    return ""


def _parse_and_create_tasks(
    task_data: list, goal: "Goal", manager: "GoalManager",
) -> List["Task"]:
    """Parse model JSON output into Task objects and add them to the goal.

    Resolves dependency indices (int, str digit, or "task_N") to real task IDs.
    Normalises Architect spec fields (lists, strings).

    Returns list of created Task objects.
    """
    task_id_map: Dict[int, str] = {}  # index -> task_id
    created: List[Task] = []

    for idx, task_info in enumerate(task_data):
        if not isinstance(task_info, dict):
            continue

        task_id = f"task_{manager.next_task_id}"
        manager.next_task_id += 1
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

        # Parse Architect spec fields (Phase 5) — normalise types
        files_to_create = task_info.get("files_to_create", [])
        if not isinstance(files_to_create, list):
            files_to_create = [str(files_to_create)] if files_to_create else []
        inputs = task_info.get("inputs", [])
        if not isinstance(inputs, list):
            inputs = [str(inputs)] if inputs else []
        expected_output = task_info.get("expected_output", "")
        if not isinstance(expected_output, str):
            expected_output = str(expected_output)
        interfaces = task_info.get("interfaces", [])
        if not isinstance(interfaces, list):
            interfaces = [str(interfaces)] if interfaces else []

        task = Task(
            task_id=task_id,
            description=task_info.get("description", "Unnamed task"),
            goal_id=goal.goal_id,
            priority=task_info.get("priority", 5),
            dependencies=resolved_deps,
            estimated_duration_minutes=task_info.get(
                "estimated_duration_minutes", 30
            ),
            files_to_create=files_to_create,
            inputs=inputs,
            expected_output=expected_output,
            interfaces=interfaces,
        )

        goal.add_task(task)
        created.append(task)
        _spec = ""
        if task.files_to_create:
            _spec = f" [files: {', '.join(task.files_to_create[:3])}]"
        logger.info("  Created task: %s - %s%s", task_id, task.description[:80], _spec)

    return created


class GoalManager:
    """
    Manages goals and their decomposition into tasks.

    Takes high-level user goals, breaks them down into actionable
    tasks with dependencies, and tracks progress.
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else Path("data")
        self.data_dir.mkdir(exist_ok=True)

        self._lock = threading.RLock()  # Protects goals dict and ID counters
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
                    project_id=goal_data.get("project_id", ""),
                    project_phase=goal_data.get("project_phase", 0),
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
                        # Phase 5 Architect spec fields
                        files_to_create=task_data.get("files_to_create"),
                        inputs=task_data.get("inputs"),
                        expected_output=task_data.get("expected_output"),
                        interfaces=task_data.get("interfaces"),
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
                    if task_data.get("deferred_until"):
                        try:
                            task.deferred_until = datetime.fromisoformat(task_data["deferred_until"])
                        except (ValueError, TypeError):
                            pass
                    goal.add_task(task)

                self.goals[goal.goal_id] = goal

            logger.info("Loaded %d goals from disk", len(self.goals))

        except Exception as e:
            logger.error("Error loading goals state: %s", e, exc_info=True)

    # Stop words for fuzzy description matching
    _STOP_WORDS = {"a", "an", "the", "and", "or", "to", "for", "in", "of", "on", "with", "is", "by"}

    @staticmethod
    def _descriptions_match(desc_a: str, desc_b: str) -> bool:
        """Check if two goal descriptions are duplicates.

        Uses substring containment or word overlap (Jaccard > 0.6).
        """
        a = desc_a.lower().strip()
        b = desc_b.lower().strip()
        # Substring match
        if a in b or b in a:
            return True
        # Word overlap (Jaccard > 0.6)
        words_a = set(a.split()) - GoalManager._STOP_WORDS
        words_b = set(b.split()) - GoalManager._STOP_WORDS
        if words_a and words_b:
            overlap = len(words_a & words_b)
            union = len(words_a | words_b)
            if union > 0 and overlap / union > 0.6:
                return True
        return False

    def _find_duplicate(self, description: str) -> Optional[str]:
        """Return the goal_id of an existing non-complete goal that matches
        *description*, or None.  Checks all active goals (including decomposed
        / in-progress ones) so we never spin up redundant work.
        """
        for g in self.goals.values():
            if g.is_complete():
                continue
            if self._descriptions_match(description, g.description):
                return g.goal_id
        return None

    def prune_duplicates(self) -> int:
        """Remove duplicate and redundant goals, keeping the oldest of each group.

        Uses fuzzy matching (substring containment + word overlap > 0.6).
        Only prunes goals that have NOT been decomposed or completed.
        Returns the number of goals removed.
        """
        with self._lock:
            keep: Dict[str, str] = {}  # normalized_key -> goal_id (first seen wins)
            to_remove = []

            # Process in creation order (oldest first = keep oldest)
            sorted_goals = sorted(self.goals.values(), key=lambda g: g.created_at)
            for g in sorted_goals:
                desc_lower = g.description.lower().strip()

                is_dup = any(
                    self._descriptions_match(desc_lower, kept_desc)
                    for kept_desc in keep
                )

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
        project_id: str = "",
        project_phase: int = 0,
    ) -> Optional[Goal]:
        """
        Create a new goal, unless a duplicate already exists.

        Args:
            description: What needs to be achieved
            user_intent: Why the user wants this
            priority: 1-10 (10 = highest)
            project_id: Self-extension project ID (if project-linked)
            project_phase: Phase number within the project

        Returns:
            Goal object, or None if a duplicate was detected
        """
        with self._lock:
            existing = self._find_duplicate(description)
            if existing:
                logger.info(
                    "Skipping duplicate goal '%s' — matches existing %s",
                    description[:60], existing,
                )
                return None

            goal_id = f"goal_{self.next_goal_id}"
            self.next_goal_id += 1

            goal = Goal(goal_id, description, user_intent, priority,
                        project_id=project_id, project_phase=project_phase)
            self.goals[goal_id] = goal

            logger.info("Created goal: %s - %s", goal_id, description)
            self.save_state()
            return goal

    def decompose_goal(
        self,
        goal_id: str,
        model: Any,
        learning_hints: Optional[List[str]] = None,
        discovery_brief: Optional[str] = None,
        user_prefs: Optional[str] = None,
    ) -> List[Task]:
        """
        Decompose a goal into actionable tasks with concrete specs (Architect).

        Phase 5 enhancement: produces specs per task (files_to_create, inputs,
        expected_output, interfaces) so workers execute against a spec instead
        of discovering what to build mid-execution. Optionally receives a
        Discovery brief and User Model preferences.

        Args:
            goal_id: Goal to decompose
            model: AI model with generate(prompt, max_tokens, temperature) -> {text}
            learning_hints: Insights from the learning system
            discovery_brief: Project context brief from Discovery phase
            user_prefs: User Model context string (preferences, corrections, style)

        Returns:
            List of generated tasks
        """
        # --- Lock: read goal state ---
        with self._lock:
            goal = self.goals.get(goal_id)
            if not goal:
                raise ValueError(f"Goal not found: {goal_id}")

            if goal.is_decomposed:
                logger.warning("Goal %s already decomposed", goal_id)
                return list(goal.tasks)

            # Snapshot what we need for the prompt
            goal_description = goal.description
            goal_user_intent = goal.user_intent

        logger.info("Decomposing goal (Architect): %s", goal_description)

        prompt = _build_decomposition_prompt(
            goal_description, goal_user_intent,
            learning_hints, discovery_brief, user_prefs,
        )

        # API-first: goal decomposition routes to Grok.
        # Increased max_tokens for richer Architect specs
        response = model.generate(
            prompt, max_tokens=1500, temperature=0.7,
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

        # --- Lock: mutate goal state with new tasks ---
        with self._lock:
            # Re-fetch goal under lock (could have been modified)
            goal = self.goals.get(goal_id)
            if not goal:
                raise ValueError(f"Goal not found: {goal_id} (removed during decomposition)")
            if goal.is_decomposed:
                logger.warning("Goal %s decomposed by another thread", goal_id)
                return list(goal.tasks)

            tasks = _parse_and_create_tasks(task_data, goal, self)
            goal.is_decomposed = True
            self.save_state()
            logger.info("Goal decomposed into %d tasks", len(goal.tasks))

            return list(goal.tasks)

    def add_follow_up_tasks(
        self,
        goal_id: str,
        task_descriptions: List[str],
        after_task_id: str,
    ) -> List[Task]:
        """Add follow-up tasks to an existing goal.

        New tasks depend on after_task_id so they execute after it completes.
        Updates progress and saves state.

        Args:
            goal_id: Goal to add tasks to
            task_descriptions: List of task description strings
            after_task_id: Task ID that the new tasks depend on

        Returns:
            List of created Task objects
        """
        with self._lock:
            goal = self.goals.get(goal_id)
            if not goal:
                raise ValueError(f"Goal not found: {goal_id}")

            created = []
            for desc in task_descriptions:
                task_id = f"task_{self.next_task_id}"
                self.next_task_id += 1

                task = Task(
                    task_id=task_id,
                    description=desc,
                    goal_id=goal_id,
                    priority=5,
                    dependencies=[after_task_id],
                    estimated_duration_minutes=30,
                )
                goal.add_task(task)
                created.append(task)
                logger.info("  Added follow-up task: %s - %s", task_id, desc)

            goal.update_progress()
            self.save_state()
            logger.info(
                "Added %d follow-up tasks to goal %s", len(created), goal_id,
            )
            return created

    def get_next_task(self) -> Optional[Task]:
        """
        Get the next task to work on (highest priority, dependencies met).

        Returns:
            Task to execute, or None if nothing ready
        """
        with self._lock:
            all_ready_tasks: List[Task] = []

            for goal in self.goals.values():
                if goal.is_complete():
                    continue

                ready = goal.get_ready_tasks()
                all_ready_tasks.extend(ready)

            if not all_ready_tasks:
                return None

            def _sort_key(t: Task) -> tuple:
                goal = self.goals[t.goal_id]
                # User-requested goals get priority boost (sort first)
                _intent = (goal.user_intent or "").lower()
                is_user = 0 if _intent.startswith("user ") else 1
                return (is_user, -t.priority, -goal.priority)

            all_ready_tasks.sort(key=_sort_key)

            return all_ready_tasks[0]

    def get_next_task_for_goal(self, goal_id: str) -> Optional[Task]:
        """
        Get the next ready task for a *specific* goal.

        Used by the worker pool so each worker only picks tasks
        from the goal it owns — no cross-goal task stealing.

        Returns:
            Task to execute, or None if nothing ready for this goal
        """
        with self._lock:
            goal = self.goals.get(goal_id)
            if not goal or goal.is_complete():
                return None

            ready = goal.get_ready_tasks()
            if not ready:
                return None

            # Sort by priority (highest first)
            ready.sort(key=lambda t: -t.priority)
            return ready[0]

    def _find_task(self, task_id: str) -> tuple:
        """Find a task by ID across all goals. Caller must hold self._lock.

        Returns (goal, task) or raises ValueError.
        """
        for goal in self.goals.values():
            for task in goal.tasks:
                if task.task_id == task_id:
                    return goal, task
        raise ValueError(f"Task not found: {task_id}")

    def start_task(self, task_id: str) -> None:
        """Mark a task as in progress."""
        with self._lock:
            _goal, task = self._find_task(task_id)
            task.status = TaskStatus.IN_PROGRESS
            task.started_at = datetime.now()
            logger.info("Started task: %s", task_id)
            self.save_state()

    def complete_task(self, task_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        """Mark a task as completed."""
        with self._lock:
            goal, task = self._find_task(task_id)
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            task.result = result
            goal.update_progress()
            logger.info(
                "Completed task: %s (%.1f%% of goal)",
                task_id, goal.completion_percentage,
            )
            self.save_state()

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark a task as failed and cascade-block dependents."""
        with self._lock:
            goal, task = self._find_task(task_id)
            task.status = TaskStatus.FAILED
            task.error = error
            blocked = self._cascade_block_dependents(goal, task_id)
            goal.update_progress()
            logger.error("Task failed: %s - %s", task_id, error)
            if blocked:
                logger.info("Blocked %d dependent task(s): %s", len(blocked), blocked)
            self.save_state()

    def _cascade_block_dependents(
        self, goal: "Goal", failed_task_id: str,
    ) -> List[str]:
        """Mark all tasks transitively depending on failed_task_id as BLOCKED."""
        queue = [failed_task_id]
        blocked: List[str] = []
        seen = {failed_task_id}
        while queue:
            tid = queue.pop(0)
            for t in goal.tasks:
                if t.task_id in seen or t.status != TaskStatus.PENDING:
                    continue
                if tid in t.dependencies:
                    t.status = TaskStatus.BLOCKED
                    t.error = f"Dependency {tid} failed"
                    blocked.append(t.task_id)
                    seen.add(t.task_id)
                    queue.append(t.task_id)
        return blocked

    def get_project_phase_goals(self, project_id: str, phase: int) -> List[Goal]:
        """Get all goals linked to a specific project phase.

        Used by Phase 4 (multi-cycle project execution) to check
        whether all goals for a phase are complete.
        """
        with self._lock:
            return [
                g for g in self.goals.values()
                if g.project_id == project_id and g.project_phase == phase
            ]

    def get_status(self) -> Dict[str, Any]:
        """Get overall status of all goals and tasks."""
        with self._lock:
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

    def remove_goal(self, goal_id: str) -> bool:
        """Remove a goal by ID, thread-safe. Returns True if removed."""
        with self._lock:
            if goal_id in self.goals:
                del self.goals[goal_id]
                return True
        return False

    def save_state(self) -> None:
        """Save goals and tasks to disk.

        Uses RLock so it's safe to call from within other locked methods
        (e.g. complete_task -> save_state) and also standalone.
        """
        with self._lock:
            state_file = self.data_dir / "goals_state.json"

            state = {
                "next_goal_id": self.next_goal_id,
                "next_task_id": self.next_task_id,
                "goals": [g.to_dict() for g in self.goals.values()],
            }

            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)

            logger.info("Saved goal state to %s", state_file)
