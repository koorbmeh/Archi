"""Unit tests for autonomous_executor.py.

Tests budget loading, project path resolution, task queue processing,
deferred task handling, follow-up task extraction gating, and parallel
wave execution (session 120).

Created session 74.
"""

import json
import os
import threading
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from src.core.autonomous_executor import (
    _build_step_summary,
    _get_dream_cycle_budget,
    _get_max_parallel_tasks,
    _get_ready_wave,
    _locked_append,
    _parse_defer_delta,
    _run_single_task,
    _safe_goal_desc,
    _execute_wave,
    _resolve_project_path,
    process_task_queue,
    extract_follow_up_tasks,
)
from src.core.goal_manager import Goal, GoalManager, Task, TaskStatus


# ── Budget loading tests ─────────────────────────────────────────────


class TestGetDreamCycleBudget:
    """Tests for _get_dream_cycle_budget()."""

    def test_returns_default_on_missing_file(self):
        with patch("src.utils.config.get_dream_cycle_budget", return_value=0.50):
            assert _get_dream_cycle_budget() == 0.50

    def test_reads_budget_from_yaml(self):
        with patch("src.utils.config.get_dream_cycle_budget", return_value=0.75):
            assert _get_dream_cycle_budget() == 0.75

    def test_disabled_rule_returns_default(self):
        # Disabled rule — centralised config returns default
        with patch("src.utils.config.get_dream_cycle_budget", return_value=0.50):
            assert _get_dream_cycle_budget() == 0.50

    def test_missing_rule_returns_default(self):
        with patch("src.utils.config.get_dream_cycle_budget", return_value=0.50):
            assert _get_dream_cycle_budget() == 0.50


# ── Project path resolution tests ────────────────────────────────────


class TestResolveProjectPath:
    """Tests for _resolve_project_path()."""

    def test_matches_project_by_name(self):
        context = {
            "active_projects": {
                "Health_Optimization": {
                    "path": "workspace/projects/Health_Optimization",
                    "description": "health and wellness tracking",
                    "focus_areas": ["supplements", "exercise"],
                },
            },
        }
        with patch("src.utils.project_context.load", return_value=context):
            result = _resolve_project_path(
                "Improve health optimization tools",
                "Research supplement interactions",
            )
            assert result == "workspace/projects/Health_Optimization"

    def test_matches_by_focus_area(self):
        context = {
            "active_projects": {
                "Health_Optimization": {
                    "path": "workspace/projects/Health_Optimization",
                    "description": "health tracking",
                    "focus_areas": ["supplements", "exercise"],
                },
            },
        }
        with patch("src.utils.project_context.load", return_value=context):
            result = _resolve_project_path("General goal", "Research supplements dosing")
            assert result == "workspace/projects/Health_Optimization"

    def test_no_match_returns_none(self):
        context = {
            "active_projects": {
                "Health_Optimization": {
                    "path": "workspace/projects/Health_Optimization",
                    "description": "health tracking",
                    "focus_areas": ["supplements"],
                },
            },
        }
        with patch("src.utils.project_context.load", return_value=context):
            result = _resolve_project_path("Unrelated goal", "Build a game engine")
            assert result is None

    def test_no_active_projects_returns_none(self):
        with patch("src.utils.project_context.load", return_value={"active_projects": {}}):
            assert _resolve_project_path("Any", "Task") is None

    def test_exception_returns_none(self):
        with patch("src.utils.project_context.load", side_effect=RuntimeError("boom")):
            assert _resolve_project_path("Any", "Task") is None

    def test_non_dict_project_skipped(self):
        context = {
            "active_projects": {
                "Bad_Entry": "just a string, not a dict",
                "Good_Entry": {
                    "path": "workspace/projects/Good",
                    "description": "good project",
                    "focus_areas": [],
                },
            },
        }
        with patch("src.utils.project_context.load", return_value=context):
            result = _resolve_project_path("good project", "task")
            assert result == "workspace/projects/Good"


# ── process_task_queue tests ─────────────────────────────────────────


class TestProcessTaskQueue:
    """Tests for process_task_queue()."""

    def test_empty_queue_returns_zero(self):
        stop = threading.Event()
        result = process_task_queue(
            task_queue=[],
            goal_manager=None,
            router=MagicMock(),
            learning_system=MagicMock(),
            stop_flag=stop,
            autonomous_mode=False,
            overnight_results=[],
            save_overnight_results=lambda: None,
        )
        assert result == 0

    def test_stop_flag_halts_processing(self):
        stop = threading.Event()
        stop.set()
        queue = [{"description": "task1"}, {"description": "task2"}]
        result = process_task_queue(
            task_queue=queue,
            goal_manager=None,
            router=MagicMock(),
            learning_system=MagicMock(),
            stop_flag=stop,
            autonomous_mode=False,
            overnight_results=[],
            save_overnight_results=lambda: None,
        )
        assert result == 0
        # Queue should still have items since we stopped immediately
        assert len(queue) == 2

    def test_non_autonomous_skips_goal_work(self):
        """When autonomous_mode=False, only manual queue tasks run."""
        stop = threading.Event()
        gm = MagicMock()
        result = process_task_queue(
            task_queue=[],
            goal_manager=gm,
            router=MagicMock(),
            learning_system=MagicMock(),
            stop_flag=stop,
            autonomous_mode=False,
            overnight_results=[],
            save_overnight_results=lambda: None,
        )
        # Should not call _execute_autonomous_tasks
        assert result == 0


# ── extract_follow_up_tasks tests ────────────────────────────────────


class TestExtractFollowUpTasks:
    """Tests for extract_follow_up_tasks()."""

    def test_no_router_returns_empty(self):
        result = extract_follow_up_tasks(
            files_created=["/tmp/a.txt"],
            task=MagicMock(),
            goal=MagicMock(),
            router=None,
            goal_manager=MagicMock(),
        )
        assert result == []

    def test_no_goal_manager_returns_empty(self):
        result = extract_follow_up_tasks(
            files_created=["/tmp/a.txt"],
            task=MagicMock(),
            goal=MagicMock(),
            router=MagicMock(),
            goal_manager=None,
        )
        assert result == []

    def test_no_readable_files_returns_empty(self, tmp_path):
        """If no files can be read, skip extraction."""
        result = extract_follow_up_tasks(
            files_created=[str(tmp_path / "nonexistent.txt")],
            task=MagicMock(),
            goal=MagicMock(tasks=[]),
            router=MagicMock(),
            goal_manager=MagicMock(),
        )
        assert result == []

    def test_too_many_pending_tasks_skips(self, tmp_path):
        """If goal already has 3+ pending tasks, skip follow-up extraction."""
        from src.core.goal_manager import TaskStatus
        f = tmp_path / "output.txt"
        f.write_text("Some content here")

        mock_task_pending = MagicMock()
        mock_task_pending.status = TaskStatus.PENDING
        mock_goal = MagicMock()
        mock_goal.tasks = [mock_task_pending] * 4  # 4 pending tasks

        result = extract_follow_up_tasks(
            files_created=[str(f)],
            task=MagicMock(goal_id="g1", task_id="t1"),
            goal=mock_goal,
            router=MagicMock(),
            goal_manager=MagicMock(),
        )
        assert result == []

    def test_successful_extraction(self, tmp_path):
        """When router returns valid follow-up tasks, they get added."""
        from src.core.goal_manager import TaskStatus
        f = tmp_path / "report.md"
        f.write_text("# Research Report\nFindings about X.")

        mock_task = MagicMock()
        mock_task.goal_id = "g1"
        mock_task.task_id = "t1"
        mock_task.description = "Research X"

        completed_task = MagicMock()
        completed_task.status = TaskStatus.COMPLETED
        mock_goal = MagicMock()
        mock_goal.goal_id = "g1"
        mock_goal.description = "Research and document X"
        mock_goal.tasks = [completed_task]

        router = MagicMock()
        router.generate.return_value = {
            "text": '[{"description": "Create summary slides from findings"}]',
        }

        created_tasks = [MagicMock(task_id="t2")]
        gm = MagicMock()
        gm.add_follow_up_tasks.return_value = created_tasks

        with patch("src.utils.parsing.extract_json_array", return_value=[{"description": "Create summary slides from findings"}]):
            result = extract_follow_up_tasks(
                files_created=[str(f)],
                task=mock_task,
                goal=mock_goal,
                router=router,
                goal_manager=gm,
            )
            assert len(result) == 1
            gm.add_follow_up_tasks.assert_called_once()

    def test_empty_model_response_returns_empty(self, tmp_path):
        from src.core.goal_manager import TaskStatus
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')

        completed = MagicMock()
        completed.status = TaskStatus.COMPLETED
        mock_goal = MagicMock()
        mock_goal.tasks = [completed]

        router = MagicMock()
        router.generate.return_value = {"text": "[]"}

        with patch("src.utils.parsing.extract_json_array", return_value=[]):
            result = extract_follow_up_tasks(
                files_created=[str(f)],
                task=MagicMock(goal_id="g1", task_id="t1"),
                goal=mock_goal,
                router=router,
                goal_manager=MagicMock(),
            )
            assert result == []


# ── Defer delta parsing tests (session 122) ───────────────────────────


class TestParseDeferDelta:
    """Tests for _parse_defer_delta() — extracted helper (session 122)."""

    def test_tomorrow(self):
        assert _parse_defer_delta("come back tomorrow") == timedelta(days=1)

    def test_two_hours(self):
        assert _parse_defer_delta("try again in ~2 hours") == timedelta(hours=2)

    def test_couple_hours(self):
        assert _parse_defer_delta("couple hours from now") == timedelta(hours=2)

    def test_one_hour_explicit(self):
        assert _parse_defer_delta("retry in ~1 hour") == timedelta(hours=1)

    def test_unknown_defaults_to_one_hour(self):
        assert _parse_defer_delta("some random error") == timedelta(hours=1)

    def test_empty_string_defaults_to_one_hour(self):
        assert _parse_defer_delta("") == timedelta(hours=1)


# ── Parallel wave execution tests (session 120) ──────────────────────


def _make_goal_manager(tmp_path) -> GoalManager:
    """Create a GoalManager with a temporary data dir."""
    return GoalManager(data_dir=tmp_path / "data")


def _make_task(task_id, goal_id, status=TaskStatus.PENDING, deps=None, priority=5):
    """Create a Task with given attributes."""
    t = Task(task_id=task_id, description=f"Task {task_id}", goal_id=goal_id,
             priority=priority, dependencies=deps or [])
    t.status = status
    return t


class TestGetMaxParallelTasks:
    """Tests for _get_max_parallel_tasks()."""

    def test_returns_config_value(self):
        with patch("src.utils.config.get_heartbeat_config",
                    return_value={"max_parallel_tasks": 5}):
            assert _get_max_parallel_tasks() == 5

    def test_returns_default_when_missing(self):
        with patch("src.utils.config.get_heartbeat_config",
                    return_value={}):
            assert _get_max_parallel_tasks() == 3


class TestGetReadyWave:
    """Tests for _get_ready_wave()."""

    def test_empty_when_no_goals(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        assert _get_ready_wave(gm, 3) == []

    def test_returns_ready_tasks(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test goal", "user request")
        goal.is_decomposed = True
        t1 = _make_task("t1", "g1")
        t2 = _make_task("t2", "g1")
        goal.tasks = [t1, t2]
        gm.goals["g1"] = goal

        wave = _get_ready_wave(gm, 5)
        assert len(wave) == 2
        assert {t.task_id for t in wave} == {"t1", "t2"}

    def test_respects_max_tasks_limit(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test goal", "user request")
        goal.is_decomposed = True
        for i in range(5):
            goal.tasks.append(_make_task(f"t{i}", "g1"))
        gm.goals["g1"] = goal

        wave = _get_ready_wave(gm, 2)
        assert len(wave) == 2

    def test_skips_tasks_with_unmet_deps(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test goal", "user request")
        goal.is_decomposed = True
        t1 = _make_task("t1", "g1")
        t2 = _make_task("t2", "g1", deps=["t1"])  # depends on t1
        goal.tasks = [t1, t2]
        gm.goals["g1"] = goal

        wave = _get_ready_wave(gm, 5)
        assert len(wave) == 1
        assert wave[0].task_id == "t1"

    def test_includes_tasks_with_completed_deps(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test goal", "user request")
        goal.is_decomposed = True
        t1 = _make_task("t1", "g1", status=TaskStatus.COMPLETED)
        t2 = _make_task("t2", "g1", deps=["t1"])
        goal.tasks = [t1, t2]
        gm.goals["g1"] = goal

        wave = _get_ready_wave(gm, 5)
        assert len(wave) == 1
        assert wave[0].task_id == "t2"

    def test_skips_complete_goals(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Done goal", "user request")
        goal.is_decomposed = True
        t1 = _make_task("t1", "g1", status=TaskStatus.COMPLETED)
        goal.tasks = [t1]
        gm.goals["g1"] = goal

        assert _get_ready_wave(gm, 5) == []

    def test_cross_goal_wave(self, tmp_path):
        """Ready tasks from multiple goals appear in the same wave."""
        gm = _make_goal_manager(tmp_path)
        g1 = Goal("g1", "Goal 1", "user request")
        g1.is_decomposed = True
        g1.tasks = [_make_task("t1", "g1")]
        g2 = Goal("g2", "Goal 2", "user request")
        g2.is_decomposed = True
        g2.tasks = [_make_task("t2", "g2")]
        gm.goals["g1"] = g1
        gm.goals["g2"] = g2

        wave = _get_ready_wave(gm, 5)
        assert len(wave) == 2

    def test_user_intent_priority_boost(self, tmp_path):
        """Tasks from user-requested goals sort before auto-generated ones."""
        gm = _make_goal_manager(tmp_path)
        g_auto = Goal("g_auto", "Auto goal", "suggestion")
        g_auto.is_decomposed = True
        g_auto.tasks = [_make_task("t_auto", "g_auto", priority=10)]
        g_user = Goal("g_user", "User goal", "user requested this")
        g_user.is_decomposed = True
        g_user.tasks = [_make_task("t_user", "g_user", priority=1)]
        gm.goals["g_auto"] = g_auto
        gm.goals["g_user"] = g_user

        wave = _get_ready_wave(gm, 5)
        # User goal's task should be first despite lower priority number
        assert wave[0].task_id == "t_user"


class TestRunSingleTask:
    """Tests for _run_single_task()."""

    def test_successful_task(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test", "user")
        goal.is_decomposed = True
        task = _make_task("t1", "g1")
        goal.tasks = [task]
        gm.goals["g1"] = goal

        mock_result = {
            "cost_usd": 0.05,
            "analysis": "Did the thing",
            "success": True,
            "steps_taken": [],
        }

        with patch("src.core.autonomous_executor.execute_task", return_value=mock_result):
            result = _run_single_task(
                task, gm, MagicMock(), MagicMock(),
                [], lambda: None, None, {},
            )
            assert result["cost"] == 0.05
            assert result["failed"] is False
            assert task.status == TaskStatus.COMPLETED

    def test_failed_task(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test", "user")
        goal.is_decomposed = True
        task = _make_task("t1", "g1")
        goal.tasks = [task]
        gm.goals["g1"] = goal

        with patch("src.core.autonomous_executor.execute_task",
                    side_effect=RuntimeError("boom")):
            result = _run_single_task(
                task, gm, MagicMock(), MagicMock(),
                [], lambda: None, None, {},
            )
            assert result["failed"] is True
            assert result["cost"] == 0
            assert task.status == TaskStatus.FAILED

    def test_deferred_task(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test", "user")
        goal.is_decomposed = True
        task = _make_task("t1", "g1")
        goal.tasks = [task]
        gm.goals["g1"] = goal

        mock_result = {
            "cost_usd": 0.01,
            "deferred": True,
            "error": "user said come back in ~1 hour",
        }

        with patch("src.core.autonomous_executor.execute_task", return_value=mock_result):
            result = _run_single_task(
                task, gm, MagicMock(), MagicMock(),
                [], lambda: None, None, {},
            )
            assert result["failed"] is False
            assert task.status == TaskStatus.PENDING
            assert task.deferred_until is not None

    def test_accumulates_sibling_context(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test", "user")
        goal.is_decomposed = True
        task = _make_task("t1", "g1")
        goal.tasks = [task]
        gm.goals["g1"] = goal

        context = {}
        mock_result = {
            "cost_usd": 0.02,
            "analysis": "Found important data about X",
            "success": True,
            "steps_taken": [],
        }

        with patch("src.core.autonomous_executor.execute_task", return_value=mock_result):
            _run_single_task(
                task, gm, MagicMock(), MagicMock(),
                [], lambda: None, None, context,
            )
            assert "g1" in context
            assert "Found important data" in context["g1"][0]


class TestExecuteWave:
    """Tests for _execute_wave() — concurrent task execution."""

    def test_parallel_execution(self, tmp_path):
        """Multiple tasks execute concurrently and all complete."""
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test", "user")
        goal.is_decomposed = True
        t1 = _make_task("t1", "g1")
        t2 = _make_task("t2", "g1")
        goal.tasks = [t1, t2]
        gm.goals["g1"] = goal

        mock_result = {
            "cost_usd": 0.03,
            "analysis": "Done",
            "success": True,
            "steps_taken": [],
        }

        with patch("src.core.autonomous_executor.execute_task", return_value=mock_result):
            cost, executed, failures = _execute_wave(
                wave=[t1, t2],
                goal_manager=gm,
                router=MagicMock(),
                learning_system=MagicMock(),
                stop_flag=threading.Event(),
                overnight_results=[],
                save_overnight_results=lambda: None,
                memory=None,
                goal_task_context={},
                cost_lock=threading.Lock(),
                results_lock=threading.Lock(),
                max_workers=2,
            )
            assert executed == 2
            assert failures == 0
            assert cost == pytest.approx(0.06)
            assert t1.status == TaskStatus.COMPLETED
            assert t2.status == TaskStatus.COMPLETED

    def test_failure_isolation(self, tmp_path):
        """One task failing doesn't prevent the other from completing."""
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test", "user")
        goal.is_decomposed = True
        t1 = _make_task("t1", "g1")
        t2 = _make_task("t2", "g1")
        goal.tasks = [t1, t2]
        gm.goals["g1"] = goal

        call_count = 0

        def _mock_execute(task, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if task.task_id == "t1":
                raise RuntimeError("t1 exploded")
            return {
                "cost_usd": 0.04,
                "analysis": "t2 done fine",
                "success": True,
                "steps_taken": [],
            }

        with patch("src.core.autonomous_executor.execute_task", side_effect=_mock_execute):
            cost, executed, failures = _execute_wave(
                wave=[t1, t2],
                goal_manager=gm,
                router=MagicMock(),
                learning_system=MagicMock(),
                stop_flag=threading.Event(),
                overnight_results=[],
                save_overnight_results=lambda: None,
                memory=None,
                goal_task_context={},
                cost_lock=threading.Lock(),
                results_lock=threading.Lock(),
                max_workers=2,
            )
            assert executed == 2
            assert failures == 1
            assert t1.status == TaskStatus.FAILED
            assert t2.status == TaskStatus.COMPLETED
            assert cost == pytest.approx(0.04)

    def test_cost_accumulation_thread_safe(self, tmp_path):
        """Cost tracking across concurrent tasks is accurate."""
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test", "user")
        goal.is_decomposed = True
        tasks = []
        for i in range(3):
            t = _make_task(f"t{i}", "g1")
            tasks.append(t)
            goal.tasks.append(t)
        gm.goals["g1"] = goal

        def _mock_execute(task, *args, **kwargs):
            return {
                "cost_usd": 0.10,
                "analysis": "Done",
                "success": True,
                "steps_taken": [],
            }

        with patch("src.core.autonomous_executor.execute_task", side_effect=_mock_execute):
            cost, executed, failures = _execute_wave(
                wave=tasks,
                goal_manager=gm,
                router=MagicMock(),
                learning_system=MagicMock(),
                stop_flag=threading.Event(),
                overnight_results=[],
                save_overnight_results=lambda: None,
                memory=None,
                goal_task_context={},
                cost_lock=threading.Lock(),
                results_lock=threading.Lock(),
                max_workers=3,
            )
            assert executed == 3
            assert failures == 0
            assert cost == pytest.approx(0.30)

    def test_deferred_in_wave(self, tmp_path):
        """A deferred task in a wave doesn't count as a failure."""
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test", "user")
        goal.is_decomposed = True
        t1 = _make_task("t1", "g1")
        goal.tasks = [t1]
        gm.goals["g1"] = goal

        def _mock_execute(task, *args, **kwargs):
            return {
                "cost_usd": 0.01,
                "deferred": True,
                "error": "tomorrow",
            }

        with patch("src.core.autonomous_executor.execute_task", side_effect=_mock_execute):
            cost, executed, failures = _execute_wave(
                wave=[t1],
                goal_manager=gm,
                router=MagicMock(),
                learning_system=MagicMock(),
                stop_flag=threading.Event(),
                overnight_results=[],
                save_overnight_results=lambda: None,
                memory=None,
                goal_task_context={},
                cost_lock=threading.Lock(),
                results_lock=threading.Lock(),
                max_workers=1,
            )
            assert executed == 1
            assert failures == 0
            assert t1.status == TaskStatus.PENDING
            assert t1.deferred_until is not None


# ── Utility helper tests (session 123) ────────────────────────────────


class TestSafeGoalDesc:
    """Tests for _safe_goal_desc() — safe goal description extraction."""

    def test_returns_description(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "My goal description", "user")
        gm.goals["g1"] = goal
        task = _make_task("t1", "g1")
        assert _safe_goal_desc(gm, task) == "My goal description"

    def test_missing_goal_returns_empty(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        task = _make_task("t1", "g_nonexistent")
        assert _safe_goal_desc(gm, task) == ""

    def test_none_goal_manager_returns_empty(self):
        task = _make_task("t1", "g1")
        assert _safe_goal_desc(None, task) == ""

    def test_task_without_goal_id_returns_empty(self, tmp_path):
        gm = _make_goal_manager(tmp_path)
        task = MagicMock(spec=[])  # no goal_id attribute
        assert _safe_goal_desc(gm, task) == ""


class TestLockedAppend:
    """Tests for _locked_append() — thread-safe overnight results append."""

    def test_appends_entry(self):
        results = []
        saved = []
        _locked_append(None, results, lambda: saved.append(True), {"key": "val"})
        assert len(results) == 1
        assert results[0] == {"key": "val"}
        assert len(saved) == 1

    def test_uses_lock(self):
        lock = threading.Lock()
        results = []
        _locked_append(lock, results, lambda: None, {"a": 1})
        assert len(results) == 1
        # Lock should be released after call
        assert lock.acquire(blocking=False)
        lock.release()

    def test_save_error_doesnt_crash(self):
        """If save_overnight_results raises, the entry is still appended."""
        results = []
        def _boom():
            raise RuntimeError("disk full")
        # Should not raise
        _locked_append(None, results, _boom, {"task": "test"})
        assert len(results) == 1


class TestBuildStepSummary:
    """Tests for _build_step_summary() — human-readable step descriptions."""

    def test_empty_steps(self):
        assert _build_step_summary([]) == "No steps executed"

    def test_done_step(self):
        steps = [{"action": "done", "summary": "Task complete"}]
        assert _build_step_summary(steps) == "Done: Task complete"

    def test_think_step_excluded(self):
        steps = [{"action": "think"}]
        assert _build_step_summary(steps) == "No steps executed"

    def test_web_search_step(self):
        steps = [{"action": "web_search", "params": {"query": "python asyncio"}}]
        assert _build_step_summary(steps) == "Searched: python asyncio"

    def test_create_file_step(self):
        steps = [{"action": "create_file", "params": {"path": "/tmp/out.txt"}}]
        assert _build_step_summary(steps) == "Created: /tmp/out.txt"

    def test_read_file_step(self):
        steps = [{"action": "read_file", "params": {"path": "/tmp/in.txt"}}]
        assert _build_step_summary(steps) == "Read: /tmp/in.txt"

    def test_multiple_steps(self):
        steps = [
            {"action": "web_search", "params": {"query": "test"}},
            {"action": "think"},
            {"action": "create_file", "params": {"path": "/tmp/report.md"}},
            {"action": "done", "summary": "Finished"},
        ]
        result = _build_step_summary(steps)
        assert "Searched: test" in result
        assert "Created: /tmp/report.md" in result
        assert "Done: Finished" in result
        assert "think" not in result.lower()

    def test_unknown_action_ignored(self):
        steps = [{"action": "unknown_action", "params": {}}]
        assert _build_step_summary(steps) == "No steps executed"


class TestRunSingleTaskOvernightResults:
    """Tests for _run_single_task() recording failures to overnight_results (session 123)."""

    def test_failure_records_overnight_result(self, tmp_path):
        """When execute_task raises, the failure appears in overnight_results."""
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test goal", "user")
        goal.is_decomposed = True
        task = _make_task("t1", "g1")
        goal.tasks = [task]
        gm.goals["g1"] = goal

        overnight_results = []
        saved = []

        with patch("src.core.autonomous_executor.execute_task",
                    side_effect=RuntimeError("kaboom")):
            result = _run_single_task(
                task, gm, MagicMock(), MagicMock(),
                overnight_results, lambda: saved.append(True), None, {},
            )
            assert result["failed"] is True
            assert len(overnight_results) == 1
            assert overnight_results[0]["success"] is False
            assert "kaboom" in overnight_results[0]["summary"]
            assert overnight_results[0]["goal"] == "Test goal"
            assert len(saved) == 1  # save was called

    def test_failure_records_with_lock(self, tmp_path):
        """Lock is properly acquired and released during failure recording."""
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test goal", "user")
        goal.is_decomposed = True
        task = _make_task("t1", "g1")
        goal.tasks = [task]
        gm.goals["g1"] = goal

        lock = threading.Lock()
        overnight_results = []

        with patch("src.core.autonomous_executor.execute_task",
                    side_effect=RuntimeError("boom")):
            _run_single_task(
                task, gm, MagicMock(), MagicMock(),
                overnight_results, lambda: None, None, {},
                results_lock=lock,
            )
            assert len(overnight_results) == 1
            # Lock should be released
            assert lock.acquire(blocking=False)
            lock.release()
