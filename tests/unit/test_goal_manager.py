"""Unit tests for GoalManager — CRUD, decomposition, dependencies, pruning.

Tests Task, Goal, and GoalManager classes:
  - Task creation, serialization, dependency checking, deferred tasks
  - Goal creation, progress tracking, ready-task selection, execution waves
  - GoalManager CRUD (create, remove, status), state persistence
  - Goal decomposition (mocked model), dependency resolution
  - Follow-up task creation
  - Next-task selection (priority, user-intent boost)
  - Duplicate pruning (substring + Jaccard overlap)
  - Thread-safety basics (concurrent create + complete)

Created session 80.
"""

import json
import threading
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.goal_manager import (
    Task, TaskStatus, Goal, GoalManager,
    _build_decomposition_prompt, _get_type_hints, _parse_and_create_tasks,
)


# ── Task tests ────────────────────────────────────────────────────────


class TestTask:
    """Tests for the Task dataclass."""

    def test_task_defaults(self):
        t = Task(task_id="task_1", description="Do thing", goal_id="goal_1")
        assert t.task_id == "task_1"
        assert t.status == TaskStatus.PENDING
        assert t.priority == 5
        assert t.dependencies == []
        assert t.estimated_duration_minutes == 30
        assert t.started_at is None
        assert t.completed_at is None
        assert t.result is None
        assert t.error is None
        assert t.deferred_until is None
        assert t.files_to_create == []
        assert t.inputs == []
        assert t.expected_output == ""
        assert t.interfaces == []

    def test_task_with_architect_fields(self):
        t = Task(
            task_id="task_2", description="Build X", goal_id="goal_1",
            files_to_create=["output.py"], inputs=["data.json"],
            expected_output="A working script", interfaces=["Reads task_1 output"],
        )
        assert t.files_to_create == ["output.py"]
        assert t.inputs == ["data.json"]
        assert t.expected_output == "A working script"
        assert t.interfaces == ["Reads task_1 output"]

    def test_can_start_no_deps(self):
        t = Task(task_id="t1", description="x", goal_id="g1")
        assert t.can_start(set()) is True
        assert t.can_start({"t2"}) is True

    def test_can_start_with_deps_met(self):
        t = Task(task_id="t2", description="x", goal_id="g1", dependencies=["t1"])
        assert t.can_start({"t1"}) is True
        assert t.can_start({"t1", "t3"}) is True

    def test_can_start_with_deps_unmet(self):
        t = Task(task_id="t2", description="x", goal_id="g1", dependencies=["t1"])
        assert t.can_start(set()) is False
        assert t.can_start({"t3"}) is False

    def test_can_start_multi_deps(self):
        t = Task(task_id="t3", description="x", goal_id="g1", dependencies=["t1", "t2"])
        assert t.can_start({"t1"}) is False
        assert t.can_start({"t1", "t2"}) is True

    def test_to_dict_basic(self):
        t = Task(task_id="t1", description="Do it", goal_id="g1", priority=7)
        d = t.to_dict()
        assert d["task_id"] == "t1"
        assert d["description"] == "Do it"
        assert d["goal_id"] == "g1"
        assert d["priority"] == 7
        assert d["status"] == "pending"
        assert d["started_at"] is None
        assert d["completed_at"] is None
        assert d["deferred_until"] is None
        # Architect fields omitted when empty
        assert "files_to_create" not in d
        assert "inputs" not in d
        assert "expected_output" not in d
        assert "interfaces" not in d

    def test_to_dict_with_architect_fields(self):
        t = Task(
            task_id="t1", description="x", goal_id="g1",
            files_to_create=["a.py"], expected_output="works",
        )
        d = t.to_dict()
        assert d["files_to_create"] == ["a.py"]
        assert d["expected_output"] == "works"
        # Empty lists/strings omitted
        assert "inputs" not in d
        assert "interfaces" not in d

    def test_to_dict_with_timestamps(self):
        t = Task(task_id="t1", description="x", goal_id="g1")
        t.started_at = datetime(2025, 1, 1, 12, 0, 0)
        t.completed_at = datetime(2025, 1, 1, 12, 30, 0)
        t.deferred_until = datetime(2025, 1, 2, 8, 0, 0)
        d = t.to_dict()
        assert d["started_at"] == "2025-01-01T12:00:00"
        assert d["completed_at"] == "2025-01-01T12:30:00"
        assert d["deferred_until"] == "2025-01-02T08:00:00"


# ── Goal tests ────────────────────────────────────────────────────────


class TestGoal:
    """Tests for the Goal class."""

    def _make_goal(self, goal_id="g1", tasks=None):
        g = Goal(goal_id=goal_id, description="Test goal", user_intent="testing")
        for t in (tasks or []):
            g.add_task(t)
        return g

    def test_goal_defaults(self):
        g = Goal(goal_id="g1", description="Do X", user_intent="want X")
        assert g.goal_id == "g1"
        assert g.priority == 5
        assert g.tasks == []
        assert g.is_decomposed is False
        assert g.completion_percentage == 0.0

    def test_add_task(self):
        g = self._make_goal()
        t = Task(task_id="t1", description="x", goal_id="g1")
        g.add_task(t)
        assert len(g.tasks) == 1
        assert g.tasks[0].task_id == "t1"

    def test_is_complete_no_tasks(self):
        """Goal with no tasks is NOT complete (needs decomposition)."""
        g = self._make_goal()
        assert g.is_complete() is False

    def test_is_complete_all_done(self):
        t1 = Task(task_id="t1", description="x", goal_id="g1")
        t2 = Task(task_id="t2", description="y", goal_id="g1")
        t1.status = TaskStatus.COMPLETED
        t2.status = TaskStatus.COMPLETED
        g = self._make_goal(tasks=[t1, t2])
        assert g.is_complete() is True

    def test_is_complete_partial(self):
        t1 = Task(task_id="t1", description="x", goal_id="g1")
        t2 = Task(task_id="t2", description="y", goal_id="g1")
        t1.status = TaskStatus.COMPLETED
        t2.status = TaskStatus.PENDING
        g = self._make_goal(tasks=[t1, t2])
        assert g.is_complete() is False

    def test_update_progress_no_tasks(self):
        g = self._make_goal()
        g.update_progress()
        assert g.completion_percentage == 0.0

    def test_update_progress_partial(self):
        t1 = Task(task_id="t1", description="x", goal_id="g1")
        t2 = Task(task_id="t2", description="y", goal_id="g1")
        t1.status = TaskStatus.COMPLETED
        g = self._make_goal(tasks=[t1, t2])
        g.update_progress()
        assert g.completion_percentage == 50.0

    def test_update_progress_all_done(self):
        t1 = Task(task_id="t1", description="x", goal_id="g1")
        t1.status = TaskStatus.COMPLETED
        g = self._make_goal(tasks=[t1])
        g.update_progress()
        assert g.completion_percentage == 100.0

    def test_get_ready_tasks_no_deps(self):
        t1 = Task(task_id="t1", description="x", goal_id="g1")
        t2 = Task(task_id="t2", description="y", goal_id="g1")
        g = self._make_goal(tasks=[t1, t2])
        ready = g.get_ready_tasks()
        assert len(ready) == 2

    def test_get_ready_tasks_with_deps(self):
        t1 = Task(task_id="t1", description="x", goal_id="g1")
        t2 = Task(task_id="t2", description="y", goal_id="g1", dependencies=["t1"])
        g = self._make_goal(tasks=[t1, t2])
        ready = g.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t1"

    def test_get_ready_tasks_deps_met(self):
        t1 = Task(task_id="t1", description="x", goal_id="g1")
        t2 = Task(task_id="t2", description="y", goal_id="g1", dependencies=["t1"])
        t1.status = TaskStatus.COMPLETED
        g = self._make_goal(tasks=[t1, t2])
        ready = g.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t2"

    def test_get_ready_tasks_excludes_non_pending(self):
        t1 = Task(task_id="t1", description="x", goal_id="g1")
        t2 = Task(task_id="t2", description="y", goal_id="g1")
        t1.status = TaskStatus.IN_PROGRESS
        g = self._make_goal(tasks=[t1, t2])
        ready = g.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t2"

    def test_get_ready_tasks_deferred_future(self):
        """Deferred tasks with future deferred_until are not ready."""
        t1 = Task(task_id="t1", description="x", goal_id="g1")
        t1.deferred_until = datetime.now() + timedelta(hours=1)
        g = self._make_goal(tasks=[t1])
        ready = g.get_ready_tasks()
        assert len(ready) == 0

    def test_get_ready_tasks_deferred_past(self):
        """Deferred tasks whose time has passed ARE ready."""
        t1 = Task(task_id="t1", description="x", goal_id="g1")
        t1.deferred_until = datetime.now() - timedelta(hours=1)
        g = self._make_goal(tasks=[t1])
        ready = g.get_ready_tasks()
        assert len(ready) == 1

    def test_get_execution_waves_linear(self):
        """t1 -> t2 -> t3 = three waves."""
        t1 = Task(task_id="t1", description="a", goal_id="g1")
        t2 = Task(task_id="t2", description="b", goal_id="g1", dependencies=["t1"])
        t3 = Task(task_id="t3", description="c", goal_id="g1", dependencies=["t2"])
        g = self._make_goal(tasks=[t1, t2, t3])
        waves = g.get_execution_waves()
        assert len(waves) == 3
        assert [w[0].task_id for w in waves] == ["t1", "t2", "t3"]

    def test_get_execution_waves_parallel(self):
        """Three independent tasks = one wave."""
        t1 = Task(task_id="t1", description="a", goal_id="g1")
        t2 = Task(task_id="t2", description="b", goal_id="g1")
        t3 = Task(task_id="t3", description="c", goal_id="g1")
        g = self._make_goal(tasks=[t1, t2, t3])
        waves = g.get_execution_waves()
        assert len(waves) == 1
        assert len(waves[0]) == 3

    def test_get_execution_waves_diamond(self):
        """t1 -> t2, t1 -> t3, t2+t3 -> t4 = three waves."""
        t1 = Task(task_id="t1", description="a", goal_id="g1")
        t2 = Task(task_id="t2", description="b", goal_id="g1", dependencies=["t1"])
        t3 = Task(task_id="t3", description="c", goal_id="g1", dependencies=["t1"])
        t4 = Task(task_id="t4", description="d", goal_id="g1", dependencies=["t2", "t3"])
        g = self._make_goal(tasks=[t1, t2, t3, t4])
        waves = g.get_execution_waves()
        assert len(waves) == 3
        assert len(waves[0]) == 1  # t1
        assert len(waves[1]) == 2  # t2, t3
        assert len(waves[2]) == 1  # t4

    def test_get_execution_waves_with_completed(self):
        """Completed tasks are pre-satisfied; only remaining tasks form waves."""
        t1 = Task(task_id="t1", description="a", goal_id="g1")
        t2 = Task(task_id="t2", description="b", goal_id="g1", dependencies=["t1"])
        t1.status = TaskStatus.COMPLETED
        g = self._make_goal(tasks=[t1, t2])
        waves = g.get_execution_waves()
        assert len(waves) == 1
        assert waves[0][0].task_id == "t2"

    def test_to_dict(self):
        g = Goal(goal_id="g1", description="Do X", user_intent="want X", priority=8)
        d = g.to_dict()
        assert d["goal_id"] == "g1"
        assert d["description"] == "Do X"
        assert d["user_intent"] == "want X"
        assert d["priority"] == 8
        assert d["is_decomposed"] is False
        assert d["tasks"] == []


# ── GoalManager tests ─────────────────────────────────────────────────


class TestGoalManager:
    """Tests for GoalManager CRUD, persistence, and task lifecycle."""

    @pytest.fixture
    def gm(self, tmp_path):
        """Create a GoalManager with a temp data directory."""
        return GoalManager(data_dir=tmp_path)

    def test_create_goal(self, gm):
        goal = gm.create_goal("Build feature", "User wants it", priority=7)
        assert goal.goal_id == "goal_1"
        assert goal.description == "Build feature"
        assert goal.user_intent == "User wants it"
        assert goal.priority == 7
        assert "goal_1" in gm.goals

    def test_create_multiple_goals_increments_id(self, gm):
        g1 = gm.create_goal("A", "x")
        g2 = gm.create_goal("B", "y")
        assert g1.goal_id == "goal_1"
        assert g2.goal_id == "goal_2"
        assert gm.next_goal_id == 3

    def test_remove_goal(self, gm):
        gm.create_goal("A", "x")
        assert gm.remove_goal("goal_1") is True
        assert "goal_1" not in gm.goals

    def test_remove_nonexistent_goal(self, gm):
        assert gm.remove_goal("goal_999") is False

    def test_get_status_empty(self, gm):
        status = gm.get_status()
        assert status["total_goals"] == 0
        assert status["active_goals"] == 0
        assert status["total_tasks"] == 0

    def test_get_status_with_goals(self, gm):
        g = gm.create_goal("A", "x")
        t = Task(task_id="task_1", description="do", goal_id=g.goal_id)
        g.add_task(t)
        g.is_decomposed = True
        status = gm.get_status()
        assert status["total_goals"] == 1
        assert status["total_tasks"] == 1
        assert status["pending_tasks"] == 1

    def test_save_and_load_state(self, tmp_path):
        gm1 = GoalManager(data_dir=tmp_path)
        g = gm1.create_goal("Persist me", "testing")
        t = Task(task_id="task_1", description="do it", goal_id=g.goal_id)
        g.add_task(t)
        g.is_decomposed = True
        gm1.save_state()

        # Load into fresh instance
        gm2 = GoalManager(data_dir=tmp_path)
        assert len(gm2.goals) == 1
        loaded_goal = gm2.goals["goal_1"]
        assert loaded_goal.description == "Persist me"
        assert len(loaded_goal.tasks) == 1
        assert loaded_goal.tasks[0].description == "do it"
        assert loaded_goal.is_decomposed is True

    def test_save_load_preserves_task_status(self, tmp_path):
        gm1 = GoalManager(data_dir=tmp_path)
        g = gm1.create_goal("A", "x")
        t = Task(task_id="task_1", description="do", goal_id=g.goal_id)
        t.status = TaskStatus.COMPLETED
        t.completed_at = datetime(2025, 6, 1, 12, 0)
        g.add_task(t)
        gm1.save_state()

        gm2 = GoalManager(data_dir=tmp_path)
        loaded_t = gm2.goals["goal_1"].tasks[0]
        assert loaded_t.status == TaskStatus.COMPLETED
        assert loaded_t.completed_at == datetime(2025, 6, 1, 12, 0)

    def test_save_load_preserves_deferred_until(self, tmp_path):
        gm1 = GoalManager(data_dir=tmp_path)
        g = gm1.create_goal("A", "x")
        t = Task(task_id="task_1", description="do", goal_id=g.goal_id)
        t.deferred_until = datetime(2025, 12, 25, 8, 0)
        g.add_task(t)
        gm1.save_state()

        gm2 = GoalManager(data_dir=tmp_path)
        loaded_t = gm2.goals["goal_1"].tasks[0]
        assert loaded_t.deferred_until == datetime(2025, 12, 25, 8, 0)

    def test_save_load_preserves_architect_fields(self, tmp_path):
        gm1 = GoalManager(data_dir=tmp_path)
        g = gm1.create_goal("A", "x")
        t = Task(
            task_id="task_1", description="build", goal_id=g.goal_id,
            files_to_create=["out.py"], inputs=["data.json"],
            expected_output="script", interfaces=["task_0"],
        )
        g.add_task(t)
        gm1.save_state()

        gm2 = GoalManager(data_dir=tmp_path)
        loaded_t = gm2.goals["goal_1"].tasks[0]
        assert loaded_t.files_to_create == ["out.py"]
        assert loaded_t.inputs == ["data.json"]
        assert loaded_t.expected_output == "script"
        assert loaded_t.interfaces == ["task_0"]

    def test_start_task(self, gm):
        g = gm.create_goal("A", "x")
        t = Task(task_id="task_1", description="do", goal_id=g.goal_id)
        g.add_task(t)
        gm.start_task("task_1")
        assert t.status == TaskStatus.IN_PROGRESS
        assert t.started_at is not None

    def test_complete_task(self, gm):
        g = gm.create_goal("A", "x")
        t = Task(task_id="task_1", description="do", goal_id=g.goal_id)
        g.add_task(t)
        gm.complete_task("task_1", result={"output": "done"})
        assert t.status == TaskStatus.COMPLETED
        assert t.completed_at is not None
        assert t.result == {"output": "done"}
        assert g.completion_percentage == 100.0

    def test_fail_task(self, gm):
        g = gm.create_goal("A", "x")
        t = Task(task_id="task_1", description="do", goal_id=g.goal_id)
        g.add_task(t)
        gm.fail_task("task_1", error="broke")
        assert t.status == TaskStatus.FAILED
        assert t.error == "broke"

    def test_fail_task_cascades_to_dependents(self, gm):
        g = gm.create_goal("A", "x")
        t1 = Task(task_id="t1", description="step 1", goal_id=g.goal_id)
        t2 = Task(task_id="t2", description="step 2", goal_id=g.goal_id,
                  dependencies=["t1"])
        g.add_task(t1)
        g.add_task(t2)
        gm.fail_task("t1", error="broke")
        assert t2.status == TaskStatus.BLOCKED
        assert "t1" in t2.error

    def test_fail_task_cascades_transitively(self, gm):
        g = gm.create_goal("A", "x")
        t1 = Task(task_id="t1", description="a", goal_id=g.goal_id)
        t2 = Task(task_id="t2", description="b", goal_id=g.goal_id,
                  dependencies=["t1"])
        t3 = Task(task_id="t3", description="c", goal_id=g.goal_id,
                  dependencies=["t2"])
        g.add_task(t1)
        g.add_task(t2)
        g.add_task(t3)
        gm.fail_task("t1", error="broke")
        assert t2.status == TaskStatus.BLOCKED
        assert t3.status == TaskStatus.BLOCKED

    def test_fail_task_no_cascade_to_independent(self, gm):
        g = gm.create_goal("A", "x")
        t1 = Task(task_id="t1", description="a", goal_id=g.goal_id)
        t2 = Task(task_id="t2", description="b", goal_id=g.goal_id,
                  dependencies=["t1"])
        t3 = Task(task_id="t3", description="c", goal_id=g.goal_id)
        g.add_task(t1)
        g.add_task(t2)
        g.add_task(t3)
        gm.fail_task("t1", error="broke")
        assert t2.status == TaskStatus.BLOCKED
        assert t3.status == TaskStatus.PENDING  # independent, not blocked

    def test_find_task_not_found(self, gm):
        with pytest.raises(ValueError, match="Task not found"):
            gm._find_task("nonexistent")

    def test_start_task_not_found(self, gm):
        with pytest.raises(ValueError, match="Task not found"):
            gm.start_task("nonexistent")


# ── Startup recovery ─────────────────────────────────────────────────


class TestStartupRecovery:
    """Tests for startup_recovery() resetting stale IN_PROGRESS tasks."""

    def test_resets_in_progress_to_pending(self, tmp_path):
        from src.core.agent_loop import startup_recovery
        gm = GoalManager(data_dir=tmp_path)
        g = gm.create_goal("A", "x")
        t = Task(task_id="t1", description="stuck", goal_id=g.goal_id)
        g.add_task(t)
        gm.start_task("t1")
        assert t.status == TaskStatus.IN_PROGRESS
        startup_recovery(gm)
        assert t.status == TaskStatus.PENDING
        assert t.started_at is None

    def test_leaves_completed_and_failed_alone(self, tmp_path):
        from src.core.agent_loop import startup_recovery
        gm = GoalManager(data_dir=tmp_path)
        g = gm.create_goal("A", "x")
        t1 = Task(task_id="t1", description="done", goal_id=g.goal_id)
        t2 = Task(task_id="t2", description="broke", goal_id=g.goal_id)
        g.add_task(t1)
        g.add_task(t2)
        gm.complete_task("t1", result={"ok": True})
        gm.fail_task("t2", error="err")
        startup_recovery(gm)
        assert t1.status == TaskStatus.COMPLETED
        assert t2.status == TaskStatus.FAILED


# ── Project-linked goals (session 238 — Phase 4) ───────────────────────


class TestProjectLinkedGoals:
    """Tests for project_id / project_phase fields on Goal (session 238)."""

    def test_create_goal_with_project_fields(self, tmp_path):
        gm = GoalManager(data_dir=tmp_path)
        g = gm.create_goal(
            "Build music gen module",
            user_intent="Self-extension: music_generation",
            project_id="music_generation",
            project_phase=2,
        )
        assert g is not None
        assert g.project_id == "music_generation"
        assert g.project_phase == 2

    def test_project_fields_default_empty(self, tmp_path):
        gm = GoalManager(data_dir=tmp_path)
        g = gm.create_goal("Regular goal", "user request")
        assert g.project_id == ""
        assert g.project_phase == 0

    def test_project_fields_serialization(self, tmp_path):
        gm = GoalManager(data_dir=tmp_path)
        g = gm.create_goal(
            "Build integration", "Self-extension: proj",
            project_id="my_proj", project_phase=3,
        )
        d = g.to_dict()
        assert d["project_id"] == "my_proj"
        assert d["project_phase"] == 3

    def test_project_fields_not_in_dict_when_empty(self, tmp_path):
        gm = GoalManager(data_dir=tmp_path)
        g = gm.create_goal("Plain goal", "user request")
        d = g.to_dict()
        assert "project_id" not in d
        assert "project_phase" not in d

    def test_project_fields_persist_and_reload(self, tmp_path):
        gm = GoalManager(data_dir=tmp_path)
        gm.create_goal(
            "Task A", "Self-extension: proj",
            project_id="test_proj", project_phase=1,
        )
        gm.save_state()
        gm2 = GoalManager(data_dir=tmp_path)
        goals = list(gm2.goals.values())
        assert len(goals) == 1
        assert goals[0].project_id == "test_proj"
        assert goals[0].project_phase == 1

    def test_get_project_phase_goals(self, tmp_path):
        gm = GoalManager(data_dir=tmp_path)
        gm.create_goal("A", "ext", project_id="proj1", project_phase=1)
        gm.create_goal("B", "ext", project_id="proj1", project_phase=1)
        gm.create_goal("C", "ext", project_id="proj1", project_phase=2)
        gm.create_goal("D", "ext", project_id="proj2", project_phase=1)
        gm.create_goal("E", "user request")

        phase1 = gm.get_project_phase_goals("proj1", 1)
        assert len(phase1) == 2
        assert all(g.project_id == "proj1" and g.project_phase == 1 for g in phase1)

        phase2 = gm.get_project_phase_goals("proj1", 2)
        assert len(phase2) == 1

        proj2 = gm.get_project_phase_goals("proj2", 1)
        assert len(proj2) == 1

        empty = gm.get_project_phase_goals("proj1", 99)
        assert len(empty) == 0


# ── Next task selection ───────────────────────────────────────────────


class TestGetNextTask:
    """Tests for get_next_task and get_next_task_for_goal."""

    @pytest.fixture
    def gm(self, tmp_path):
        return GoalManager(data_dir=tmp_path)

    def test_no_goals_returns_none(self, gm):
        assert gm.get_next_task() is None

    def test_no_ready_tasks_returns_none(self, gm):
        g = gm.create_goal("A", "x")
        # No tasks added — not decomposed
        assert gm.get_next_task() is None

    def test_returns_ready_task(self, gm):
        g = gm.create_goal("A", "x")
        t = Task(task_id="task_1", description="do", goal_id=g.goal_id)
        g.add_task(t)
        result = gm.get_next_task()
        assert result is not None
        assert result.task_id == "task_1"

    def test_priority_ordering(self, gm):
        g = gm.create_goal("A", "x")
        t_low = Task(task_id="task_1", description="low", goal_id=g.goal_id, priority=3)
        t_high = Task(task_id="task_2", description="high", goal_id=g.goal_id, priority=9)
        g.add_task(t_low)
        g.add_task(t_high)
        result = gm.get_next_task()
        assert result.task_id == "task_2"

    def test_user_intent_boost(self, gm):
        """Goals with 'user ' prefix in user_intent sort first."""
        g_proactive = gm.create_goal("Proactive", "Self-initiated: help")
        t_p = Task(task_id="task_1", description="proactive", goal_id=g_proactive.goal_id, priority=9)
        g_proactive.add_task(t_p)

        g_user = gm.create_goal("User req", "user requested this")
        t_u = Task(task_id="task_2", description="user", goal_id=g_user.goal_id, priority=3)
        g_user.add_task(t_u)

        result = gm.get_next_task()
        # User-intent goal wins despite lower task priority
        assert result.task_id == "task_2"

    def test_skips_completed_goals(self, gm):
        g = gm.create_goal("A", "x")
        t = Task(task_id="task_1", description="do", goal_id=g.goal_id)
        t.status = TaskStatus.COMPLETED
        g.add_task(t)
        assert gm.get_next_task() is None

    def test_get_next_task_for_goal(self, gm):
        g = gm.create_goal("A", "x")
        t1 = Task(task_id="task_1", description="a", goal_id=g.goal_id, priority=3)
        t2 = Task(task_id="task_2", description="b", goal_id=g.goal_id, priority=8)
        g.add_task(t1)
        g.add_task(t2)
        result = gm.get_next_task_for_goal(g.goal_id)
        assert result.task_id == "task_2"  # Higher priority

    def test_get_next_task_for_goal_nonexistent(self, gm):
        assert gm.get_next_task_for_goal("nope") is None

    def test_get_next_task_for_goal_completed(self, gm):
        g = gm.create_goal("A", "x")
        t = Task(task_id="task_1", description="do", goal_id=g.goal_id)
        t.status = TaskStatus.COMPLETED
        g.add_task(t)
        assert gm.get_next_task_for_goal(g.goal_id) is None


# ── Decomposition ────────────────────────────────────────────────────


class TestDecomposition:
    """Tests for decompose_goal with mocked model."""

    @pytest.fixture
    def gm(self, tmp_path):
        return GoalManager(data_dir=tmp_path)

    def _mock_model(self, tasks_json):
        """Create a mock model that returns a JSON task array."""
        model = MagicMock()
        model.generate.return_value = {
            "text": json.dumps(tasks_json),
            "success": True,
        }
        return model

    def test_decompose_basic(self, gm):
        g = gm.create_goal("Build a tool", "user wants tool")
        model = self._mock_model([
            {"description": "Research", "priority": 5, "dependencies": [],
             "estimated_duration_minutes": 15,
             "files_to_create": [], "inputs": [], "expected_output": "notes",
             "interfaces": []},
            {"description": "Implement", "priority": 5, "dependencies": [0],
             "estimated_duration_minutes": 30,
             "files_to_create": ["tool.py"], "inputs": ["Research notes"],
             "expected_output": "working script", "interfaces": ["task 0"]},
        ])
        tasks = gm.decompose_goal(g.goal_id, model)
        assert len(tasks) == 2
        assert g.is_decomposed is True
        assert tasks[0].description == "Research"
        assert tasks[1].description == "Implement"
        # Dependency resolution: task_2 depends on task_1
        assert tasks[1].dependencies == [tasks[0].task_id]

    def test_decompose_already_decomposed(self, gm):
        g = gm.create_goal("A", "x")
        model = self._mock_model([{"description": "Do", "priority": 5, "dependencies": []}])
        gm.decompose_goal(g.goal_id, model)
        # Second call returns existing tasks without calling model again
        tasks = gm.decompose_goal(g.goal_id, model)
        assert model.generate.call_count == 1  # Only called once

    def test_decompose_nonexistent_goal(self, gm):
        model = self._mock_model([])
        with pytest.raises(ValueError, match="Goal not found"):
            gm.decompose_goal("goal_999", model)

    def test_decompose_model_failure(self, gm):
        g = gm.create_goal("A", "x")
        model = MagicMock()
        model.generate.return_value = {"success": False, "error": "API down"}
        with pytest.raises(RuntimeError, match="Model generation failed"):
            gm.decompose_goal(g.goal_id, model)

    def test_decompose_model_empty_response(self, gm):
        g = gm.create_goal("A", "x")
        model = MagicMock()
        model.generate.return_value = {"text": "", "success": True}
        with pytest.raises(RuntimeError, match="empty response"):
            gm.decompose_goal(g.goal_id, model)

    def test_decompose_invalid_json_produces_no_tasks(self, gm):
        """extract_json_array returns [] for unparseable text → 0 tasks, no crash."""
        g = gm.create_goal("A", "x")
        model = MagicMock()
        model.generate.return_value = {"text": "not json at all", "success": True}
        tasks = gm.decompose_goal(g.goal_id, model)
        assert tasks == []
        assert g.is_decomposed is True  # Still marked decomposed

    def test_decompose_parallel_tasks(self, gm):
        """Tasks with no dependencies should be independent."""
        g = gm.create_goal("A", "x")
        model = self._mock_model([
            {"description": "Task A", "priority": 5, "dependencies": []},
            {"description": "Task B", "priority": 5, "dependencies": []},
        ])
        tasks = gm.decompose_goal(g.goal_id, model)
        assert tasks[0].dependencies == []
        assert tasks[1].dependencies == []

    def test_decompose_architect_fields_parsed(self, gm):
        g = gm.create_goal("A", "x")
        model = self._mock_model([{
            "description": "Build", "priority": 5, "dependencies": [],
            "files_to_create": ["output.py"],
            "inputs": ["data.json"],
            "expected_output": "A working script",
            "interfaces": ["Reads input from task 0"],
        }])
        tasks = gm.decompose_goal(g.goal_id, model)
        assert tasks[0].files_to_create == ["output.py"]
        assert tasks[0].inputs == ["data.json"]
        assert tasks[0].expected_output == "A working script"

    def test_decompose_coerces_non_list_fields(self, gm):
        """Non-list files_to_create, inputs, interfaces get wrapped in list."""
        g = gm.create_goal("A", "x")
        model = self._mock_model([{
            "description": "Build", "priority": 5, "dependencies": [],
            "files_to_create": "single_file.py",
            "inputs": "one_input.json",
            "expected_output": 42,  # non-string
            "interfaces": "single interface",
        }])
        tasks = gm.decompose_goal(g.goal_id, model)
        assert tasks[0].files_to_create == ["single_file.py"]
        assert tasks[0].inputs == ["one_input.json"]
        assert tasks[0].expected_output == "42"
        assert tasks[0].interfaces == ["single interface"]


# ── Follow-up tasks ──────────────────────────────────────────────────


class TestFollowUpTasks:

    @pytest.fixture
    def gm(self, tmp_path):
        return GoalManager(data_dir=tmp_path)

    def test_add_follow_up_tasks(self, gm):
        g = gm.create_goal("A", "x")
        t = Task(task_id="task_1", description="do", goal_id=g.goal_id)
        g.add_task(t)
        g.is_decomposed = True

        follow_ups = gm.add_follow_up_tasks(
            g.goal_id, ["Fix bug", "Add tests"], after_task_id="task_1",
        )
        assert len(follow_ups) == 2
        assert follow_ups[0].dependencies == ["task_1"]
        assert follow_ups[1].dependencies == ["task_1"]
        assert len(g.tasks) == 3

    def test_add_follow_up_nonexistent_goal(self, gm):
        with pytest.raises(ValueError, match="Goal not found"):
            gm.add_follow_up_tasks("nope", ["x"], after_task_id="t1")


# ── Pruning ──────────────────────────────────────────────────────────


class TestPruneDuplicates:

    @pytest.fixture
    def gm(self, tmp_path):
        return GoalManager(data_dir=tmp_path)

    def test_no_duplicates(self, gm):
        gm.create_goal("Build a house", "x")
        gm.create_goal("Write a song", "y")
        removed = gm.prune_duplicates()
        assert removed == 0
        assert len(gm.goals) == 2

    def test_create_goal_rejects_duplicate(self, gm):
        """create_goal returns None for duplicates at creation time."""
        g1 = gm.create_goal("Research Python frameworks", "x")
        assert g1 is not None
        g2 = gm.create_goal("Research Python frameworks for web development", "y")
        assert g2 is None
        assert len(gm.goals) == 1

    def test_create_goal_allows_after_complete(self, gm):
        """Completed goals don't block new ones with same description."""
        g1 = gm.create_goal("Research Python frameworks", "x")
        t = Task(task_id="t1", description="done", goal_id=g1.goal_id)
        t.status = TaskStatus.COMPLETED
        g1.add_task(t)
        g2 = gm.create_goal("Research Python frameworks", "y")
        assert g2 is not None
        assert len(gm.goals) == 2

    def test_substring_duplicate(self, gm):
        """prune_duplicates still works for goals loaded from disk (bypass create-time dedup)."""
        # Manually insert both goals to simulate disk load
        from src.core.goal_manager import Goal
        g1 = Goal("goal_1", "Research Python frameworks", "x")
        g2 = Goal("goal_2", "Research Python frameworks for web development", "y")
        gm.goals = {"goal_1": g1, "goal_2": g2}
        removed = gm.prune_duplicates()
        assert removed == 1
        remaining = list(gm.goals.values())
        assert len(remaining) == 1
        assert remaining[0].description == "Research Python frameworks"

    def test_jaccard_duplicate(self, gm):
        # Manually insert to bypass create-time dedup
        from src.core.goal_manager import Goal
        g1 = Goal("goal_1", "build web scraper tool", "x")
        g2 = Goal("goal_2", "build fast web scraper tool", "y")
        gm.goals = {"goal_1": g1, "goal_2": g2}
        removed = gm.prune_duplicates()
        assert removed == 1

    def test_preserves_decomposed_goals(self, gm):
        # Manually insert to bypass create-time dedup
        from src.core.goal_manager import Goal
        g1 = Goal("goal_1", "Research X", "x")
        g2 = Goal("goal_2", "Research X thoroughly", "y")
        g2.is_decomposed = True  # Won't be pruned
        gm.goals = {"goal_1": g1, "goal_2": g2}
        removed = gm.prune_duplicates()
        assert removed == 0  # g2 is protected (decomposed)
        assert len(gm.goals) == 2

    def test_preserves_completed_goals(self, gm):
        from src.core.goal_manager import Goal
        g1 = Goal("goal_1", "Research X", "x")
        g2 = Goal("goal_2", "Research X thoroughly", "y")
        # Make g2 complete
        t = Task(task_id="t1", description="done", goal_id=g2.goal_id)
        t.status = TaskStatus.COMPLETED
        g2.add_task(t)
        gm.goals = {"goal_1": g1, "goal_2": g2}
        removed = gm.prune_duplicates()
        assert removed == 0
        assert len(gm.goals) == 2


# ── Thread safety (basic smoke test) ─────────────────────────────────


class TestThreadSafety:

    def test_concurrent_creates(self, tmp_path):
        gm = GoalManager(data_dir=tmp_path)
        errors = []

        def create_goals(start_idx):
            try:
                for i in range(10):
                    gm.create_goal(f"Goal {start_idx}-{i}", "test")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_goals, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(gm.goals) == 40

    def test_concurrent_complete(self, tmp_path):
        gm = GoalManager(data_dir=tmp_path)
        g = gm.create_goal("A", "x")
        tasks = []
        for i in range(20):
            t = Task(task_id=f"task_{i+1}", description=f"t{i}", goal_id=g.goal_id)
            g.add_task(t)
            tasks.append(t)
        gm.next_task_id = 21
        gm.save_state()

        errors = []

        def complete_task(task_id):
            try:
                gm.complete_task(task_id)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=complete_task, args=(f"task_{i+1}",))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert all(t.status == TaskStatus.COMPLETED for t in tasks)
        assert g.completion_percentage == 100.0


# ── Load state edge cases ────────────────────────────────────────────


class TestLoadStateEdgeCases:

    def test_load_empty_state_file(self, tmp_path):
        state_file = tmp_path / "goals_state.json"
        state_file.write_text("{}", encoding="utf-8")
        gm = GoalManager(data_dir=tmp_path)
        assert len(gm.goals) == 0

    def test_load_invalid_task_status(self, tmp_path):
        """Invalid task status string falls back to PENDING."""
        state = {
            "next_goal_id": 2, "next_task_id": 2,
            "goals": [{
                "goal_id": "goal_1", "description": "A",
                "user_intent": "x", "is_decomposed": True,
                "tasks": [{
                    "task_id": "task_1", "description": "do",
                    "status": "INVALID_STATUS",
                    "dependencies": [],
                }],
            }],
        }
        (tmp_path / "goals_state.json").write_text(json.dumps(state), encoding="utf-8")
        gm = GoalManager(data_dir=tmp_path)
        assert gm.goals["goal_1"].tasks[0].status == TaskStatus.PENDING

    def test_load_corrupt_json(self, tmp_path):
        """Corrupt JSON file doesn't crash — starts empty."""
        (tmp_path / "goals_state.json").write_text("{{bad json", encoding="utf-8")
        gm = GoalManager(data_dir=tmp_path)
        assert len(gm.goals) == 0

    def test_load_bad_datetime(self, tmp_path):
        """Bad datetime strings don't crash — just use defaults."""
        state = {
            "next_goal_id": 2, "next_task_id": 2,
            "goals": [{
                "goal_id": "goal_1", "description": "A",
                "user_intent": "x", "created_at": "not-a-date",
                "tasks": [{
                    "task_id": "task_1", "description": "do",
                    "created_at": "bad", "started_at": "bad",
                    "completed_at": "bad", "deferred_until": "bad",
                    "dependencies": [],
                }],
            }],
        }
        (tmp_path / "goals_state.json").write_text(json.dumps(state), encoding="utf-8")
        gm = GoalManager(data_dir=tmp_path)
        # Should load without crashing
        assert "goal_1" in gm.goals


# ── _get_type_hints tests ────────────────────────────────────────────


class TestGetTypeHints:
    """Tests for _get_type_hints() type-aware decomposition hints."""

    @patch("src.core.opportunity_scanner.infer_opportunity_type", return_value="build")
    def test_build_type(self, mock_infer):
        result = _get_type_hints("build a web scraper")
        assert "BUILD GOAL" in result
        assert "write_source" in result

    @patch("src.core.opportunity_scanner.infer_opportunity_type", return_value="ask")
    @patch("src.core.goal_manager.get_user_name", return_value="Jesse")
    def test_ask_type(self, mock_name, mock_infer):
        result = _get_type_hints("ask about supplements")
        assert "DATA-COLLECTION" in result
        assert "JESSE" in result

    @patch("src.core.opportunity_scanner.infer_opportunity_type", return_value="fix")
    def test_fix_type(self, mock_infer):
        result = _get_type_hints("fix the login bug")
        assert "FIX GOAL" in result
        assert "DIAGNOSE" in result

    @patch("src.core.opportunity_scanner.infer_opportunity_type", return_value="connect")
    def test_connect_type(self, mock_infer):
        result = _get_type_hints("integrate the API")
        assert "INTEGRATION GOAL" in result

    @patch("src.core.opportunity_scanner.infer_opportunity_type", return_value="research")
    def test_unknown_type_returns_empty(self, mock_infer):
        result = _get_type_hints("research AI trends")
        assert result == ""

    def test_import_error_returns_empty(self):
        import sys
        # Temporarily make opportunity_scanner unimportable
        real_mod = sys.modules.get("src.core.opportunity_scanner")
        sys.modules["src.core.opportunity_scanner"] = None  # type: ignore
        try:
            result = _get_type_hints("something")
            assert result == ""
        finally:
            if real_mod is not None:
                sys.modules["src.core.opportunity_scanner"] = real_mod
            else:
                sys.modules.pop("src.core.opportunity_scanner", None)


# ── _build_decomposition_prompt tests ────────────────────────────────


class TestBuildDecompositionPrompt:
    """Tests for _build_decomposition_prompt()."""

    @patch("src.core.goal_manager._get_type_hints", return_value="")
    def test_basic_prompt_structure(self, mock_hints):
        result = _build_decomposition_prompt("make a thing", "user wants a thing")
        assert "You are the Architect" in result
        assert "make a thing" in result
        assert "user wants a thing" in result
        assert "JSON array" in result

    @patch("src.core.goal_manager._get_type_hints", return_value="")
    def test_includes_learning_hints(self, mock_hints):
        result = _build_decomposition_prompt(
            "goal", "intent", learning_hints=["avoid loops", "test first"],
        )
        assert "avoid loops" in result
        assert "test first" in result
        assert "Lessons from past work" in result

    @patch("src.core.goal_manager._get_type_hints", return_value="")
    def test_caps_learning_hints_at_three(self, mock_hints):
        hints = ["h1", "h2", "h3", "h4", "h5"]
        result = _build_decomposition_prompt("goal", "intent", learning_hints=hints)
        assert "h3" in result
        assert "h4" not in result

    @patch("src.core.goal_manager._get_type_hints", return_value="")
    def test_includes_discovery_brief(self, mock_hints):
        result = _build_decomposition_prompt(
            "goal", "intent", discovery_brief="Project path: /foo\nFiles: a.py, b.py",
        )
        assert "PROJECT CONTEXT" in result
        assert "/foo" in result

    @patch("src.core.goal_manager._get_type_hints", return_value="")
    def test_includes_user_prefs(self, mock_hints):
        result = _build_decomposition_prompt(
            "goal", "intent", user_prefs="Prefers concise output",
        )
        assert "Prefers concise output" in result

    @patch("src.core.goal_manager._get_type_hints", return_value="")
    def test_no_optional_blocks_when_none(self, mock_hints):
        result = _build_decomposition_prompt("goal", "intent")
        assert "Lessons from past work" not in result
        assert "PROJECT CONTEXT" not in result

    @patch("src.core.goal_manager._get_type_hints", return_value="\nBUILD GOAL\n")
    def test_includes_type_hints(self, mock_hints):
        result = _build_decomposition_prompt("build a tool", "intent")
        assert "BUILD GOAL" in result


# ── _parse_and_create_tasks tests ────────────────────────────────────


class TestParseAndCreateTasks:
    """Tests for _parse_and_create_tasks()."""

    def _make_goal_and_manager(self, tmp_path):
        gm = GoalManager(data_dir=tmp_path)
        goal = gm.create_goal("Test goal", "user wants to test")
        return goal, gm

    def test_creates_task_from_valid_data(self, tmp_path):
        goal, gm = self._make_goal_and_manager(tmp_path)
        data = [{"description": "Do task A", "priority": 3}]
        tasks = _parse_and_create_tasks(data, goal, gm)
        assert len(tasks) == 1
        assert tasks[0].description == "Do task A"
        assert tasks[0].priority == 3
        assert len(goal.tasks) == 1

    def test_skips_non_dict_entries(self, tmp_path):
        goal, gm = self._make_goal_and_manager(tmp_path)
        data = ["not a dict", {"description": "Real task"}, 42]
        tasks = _parse_and_create_tasks(data, goal, gm)
        assert len(tasks) == 1
        assert tasks[0].description == "Real task"

    def test_resolves_int_dependencies(self, tmp_path):
        goal, gm = self._make_goal_and_manager(tmp_path)
        data = [
            {"description": "Task 0"},
            {"description": "Task 1", "dependencies": [0]},
        ]
        tasks = _parse_and_create_tasks(data, goal, gm)
        assert len(tasks) == 2
        assert tasks[0].task_id in tasks[1].dependencies

    def test_resolves_string_digit_dependencies(self, tmp_path):
        goal, gm = self._make_goal_and_manager(tmp_path)
        data = [
            {"description": "Task 0"},
            {"description": "Task 1", "dependencies": ["0"]},
        ]
        tasks = _parse_and_create_tasks(data, goal, gm)
        assert tasks[0].task_id in tasks[1].dependencies

    def test_resolves_task_n_dependencies(self, tmp_path):
        goal, gm = self._make_goal_and_manager(tmp_path)
        data = [
            {"description": "Task 0"},
            {"description": "Task 1", "dependencies": ["task_1"]},
        ]
        tasks = _parse_and_create_tasks(data, goal, gm)
        # "task_1" means index 0 (task_1 - 1 = 0)
        assert tasks[0].task_id in tasks[1].dependencies

    def test_ignores_forward_dependencies(self, tmp_path):
        goal, gm = self._make_goal_and_manager(tmp_path)
        data = [
            {"description": "Task 0", "dependencies": [1]},
            {"description": "Task 1"},
        ]
        tasks = _parse_and_create_tasks(data, goal, gm)
        assert tasks[0].dependencies == []

    def test_normalises_non_list_files_to_create(self, tmp_path):
        goal, gm = self._make_goal_and_manager(tmp_path)
        data = [{"description": "T", "files_to_create": "single.py"}]
        tasks = _parse_and_create_tasks(data, goal, gm)
        assert tasks[0].files_to_create == ["single.py"]

    def test_normalises_non_list_inputs(self, tmp_path):
        goal, gm = self._make_goal_and_manager(tmp_path)
        data = [{"description": "T", "inputs": "data.json"}]
        tasks = _parse_and_create_tasks(data, goal, gm)
        assert tasks[0].inputs == ["data.json"]

    def test_normalises_non_str_expected_output(self, tmp_path):
        goal, gm = self._make_goal_and_manager(tmp_path)
        data = [{"description": "T", "expected_output": 42}]
        tasks = _parse_and_create_tasks(data, goal, gm)
        assert tasks[0].expected_output == "42"

    def test_default_description_for_missing(self, tmp_path):
        goal, gm = self._make_goal_and_manager(tmp_path)
        data = [{"priority": 2}]
        tasks = _parse_and_create_tasks(data, goal, gm)
        assert tasks[0].description == "Unnamed task"

    def test_increments_task_ids(self, tmp_path):
        goal, gm = self._make_goal_and_manager(tmp_path)
        data = [{"description": "A"}, {"description": "B"}, {"description": "C"}]
        tasks = _parse_and_create_tasks(data, goal, gm)
        ids = [t.task_id for t in tasks]
        assert len(set(ids)) == 3  # all unique
