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
    _cap_hints,
    _compress_task_observation,
    _decompose_pending_goals,
    _get_dream_cycle_budget,
    _get_max_parallel_tasks,
    _get_ready_wave,
    _locked_append,
    _parse_defer_delta,
    _resume_interrupted_tasks,
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
            cost, executed, failures, _obs = _execute_wave(
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
            cost, executed, failures, _obs = _execute_wave(
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
            cost, executed, failures, _obs = _execute_wave(
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

    def test_wave_observations_collected(self, tmp_path):
        """Completed tasks in a wave produce observations for cycle context."""
        gm = _make_goal_manager(tmp_path)
        goal = Goal("g1", "Test goal", "user")
        goal.is_decomposed = True
        t1 = _make_task("t1", "g1")
        t2 = _make_task("t2", "g1")
        goal.tasks = [t1, t2]
        gm.goals["g1"] = goal

        def _mock_execute(task, *args, **kwargs):
            return {
                "cost_usd": 0.02,
                "analysis": f"{task.task_id} completed",
                "success": True,
                "steps_taken": [{"action": "done"}],
            }

        with patch("src.core.autonomous_executor.execute_task", side_effect=_mock_execute):
            cost, executed, failures, wave_obs = _execute_wave(
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
            assert len(wave_obs) == 2
            # Each observation should be a non-empty string from _compress_task_observation
            for obs in wave_obs:
                assert isinstance(obs, str)
                assert len(obs) > 0

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
            cost, executed, failures, _obs = _execute_wave(
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


# ── Dream session compression tests (session 124) ────────────────────


class TestCompressTaskObservation:
    """Tests for _compress_task_observation() — 1-line task summaries."""

    def test_successful_task_with_files(self):
        result = {
            "executed": True,
            "cost_usd": 0.03,
            "files_created": ["/path/to/report.md", "/path/to/data.json"],
            "steps_taken": [{"action": "web_search"}, {"action": "create_file"}, {"action": "done"}],
        }
        obs = _compress_task_observation("Research vitamin D", "Health goal", result)
        assert "[DONE]" in obs
        assert "Research vitamin D" in obs
        assert "report.md" in obs
        assert "$0.030" in obs
        assert "3 steps" in obs

    def test_failed_task(self):
        result = {
            "executed": False,
            "cost_usd": 0.01,
            "files_created": [],
            "steps_taken": [{"action": "web_search"}],
        }
        obs = _compress_task_observation("Build tracker", "Project goal", result)
        assert "[FAIL]" in obs
        assert "Build tracker" in obs

    def test_research_only_task_with_analysis(self):
        result = {
            "executed": True,
            "cost_usd": 0.02,
            "files_created": [],
            "steps_taken": [{"action": "web_search"}, {"action": "done"}],
            "analysis": "Done: Found 5 relevant studies on sleep optimization",
        }
        obs = _compress_task_observation("Research sleep", "Health", result)
        assert "[DONE]" in obs
        assert "Found 5 relevant" in obs

    def test_truncates_long_descriptions(self):
        long_desc = "A" * 200
        result = {"executed": True, "cost_usd": 0, "files_created": [], "steps_taken": []}
        obs = _compress_task_observation(long_desc, "Goal", result)
        assert len(obs) < 300


class TestCapHints:
    """Tests for _cap_hints() — hint budget management."""

    def test_under_budget_returns_all(self):
        hints = ["hint 1", "hint 2", "hint 3"]
        result = _cap_hints(hints, max_chars=1000)
        assert result == hints

    def test_trims_lowest_priority_first(self):
        hints = [
            "Low priority learning insight that is quite verbose and takes up space",
            "PRIOR RESEARCH (already completed): some research data here",
            "EARLIER TASKS IN THIS GOAL: completed stuff",
            "FILES TO CREATE: important.py, critical.md",
        ]
        # Set budget to force trimming
        total = sum(len(h) for h in hints)
        result = _cap_hints(hints, max_chars=total - 50)
        # Should have trimmed at least one hint
        assert len(result) < len(hints)
        # High-priority architect spec should survive
        assert any("FILES TO CREATE" in h for h in result)

    def test_preserves_order(self):
        hints = ["A" * 100, "B" * 100, "EARLIER TASKS: C" * 5, "FILES TO CREATE: D"]
        result = _cap_hints(hints, max_chars=250)
        # Order should be preserved for surviving hints
        indices = [hints.index(h) for h in result]
        assert indices == sorted(indices)

    def test_empty_list(self):
        assert _cap_hints([], max_chars=100) == []

    def test_single_hint_over_budget(self):
        hints = ["A" * 5000]
        result = _cap_hints(hints, max_chars=3000)
        assert result == []  # Only hint exceeds budget, gets trimmed

    def test_architect_specs_highest_priority(self):
        hints = [
            "learning insight " * 20,  # ~320 chars, low priority
            "PRIOR RESEARCH: " + "x" * 200,  # ~215 chars, low-med priority
            "EXPECTED OUTPUT: Must produce a CSV with headers",
            "FILES TO CREATE: output.csv, summary.md",
        ]
        # Budget that can only fit ~2 hints
        result = _cap_hints(hints, max_chars=150)
        # Both architect specs should survive
        assert any("EXPECTED OUTPUT" in h for h in result)
        assert any("FILES TO CREATE" in h for h in result)


# -- Extracted hint helpers (session 131) --

class TestHintsFromMemory:
    """Tests for _hints_from_memory helper."""

    def test_no_memory_returns_empty(self):
        from src.core.autonomous_executor import _hints_from_memory
        task = MagicMock(description="write report")
        goal = MagicMock(description="research vitamins")
        assert _hints_from_memory(task, goal, None) == []

    def test_returns_prior_research_hint(self):
        from src.core.autonomous_executor import _hints_from_memory
        task = MagicMock(description="write report")
        goal = MagicMock(description="research vitamins")
        memory = MagicMock()
        memory.retrieve_relevant.return_value = {
            "semantic": [
                {"text": "Vitamin D is important", "distance": 0.5, "metadata": {"type": "research"}},
            ]
        }
        result = _hints_from_memory(task, goal, memory)
        assert len(result) == 1
        assert "PRIOR RESEARCH" in result[0]
        assert "Vitamin D" in result[0]

    def test_filters_by_distance(self):
        from src.core.autonomous_executor import _hints_from_memory
        task = MagicMock(description="write report")
        goal = MagicMock(description="research vitamins")
        memory = MagicMock()
        memory.retrieve_relevant.return_value = {
            "semantic": [
                {"text": "irrelevant", "distance": 1.5, "metadata": {}},
            ]
        }
        assert _hints_from_memory(task, goal, memory) == []

    def test_graceful_on_exception(self):
        from src.core.autonomous_executor import _hints_from_memory
        task = MagicMock(description="write report")
        goal = MagicMock(description="research vitamins")
        memory = MagicMock()
        memory.retrieve_relevant.side_effect = RuntimeError("db error")
        assert _hints_from_memory(task, goal, memory) == []


class TestHintsFromProjectPath:
    """Tests for _hints_from_project_path helper."""

    def test_no_project_path_returns_empty(self):
        from src.core.autonomous_executor import _hints_from_project_path
        task = MagicMock(description="generic task")
        goal = MagicMock(description="generic goal")
        with patch("src.core.autonomous_executor._resolve_project_path", return_value=None):
            assert _hints_from_project_path(task, goal) == []

    def test_returns_file_output_hint(self):
        from src.core.autonomous_executor import _hints_from_project_path
        task = MagicMock(description="write analysis")
        goal = MagicMock(description="research project")
        with patch("src.core.autonomous_executor._resolve_project_path", return_value="/workspace/projects/research"):
            result = _hints_from_project_path(task, goal)
        assert len(result) >= 1
        assert "FILE OUTPUT" in result[0]
        assert "/workspace/projects/research" in result[0]


class TestHintsFromArchitectSpec:
    """Tests for _hints_from_architect_spec helper."""

    def test_all_fields_populated(self):
        from src.core.autonomous_executor import _hints_from_architect_spec
        task = MagicMock()
        task.files_to_create = ["report.md"]
        task.inputs = ["data.csv"]
        task.expected_output = "A summary report"
        task.interfaces = ["storage API"]
        result = _hints_from_architect_spec(task)
        assert len(result) == 4
        assert any("FILES TO CREATE" in h for h in result)
        assert any("INPUTS NEEDED" in h for h in result)
        assert any("EXPECTED OUTPUT" in h for h in result)
        assert any("INTERFACES" in h for h in result)

    def test_empty_fields_returns_empty(self):
        from src.core.autonomous_executor import _hints_from_architect_spec
        task = MagicMock()
        task.files_to_create = []
        task.inputs = []
        task.expected_output = ""
        task.interfaces = []
        assert _hints_from_architect_spec(task) == []

    def test_partial_fields(self):
        from src.core.autonomous_executor import _hints_from_architect_spec
        task = MagicMock()
        task.files_to_create = ["output.csv"]
        task.inputs = []
        task.expected_output = ""
        task.interfaces = []
        result = _hints_from_architect_spec(task)
        assert len(result) == 1
        assert "FILES TO CREATE" in result[0]


# ── _resume_interrupted_tasks tests ──────────────────────────────────


class TestResumeInterruptedTasks:
    """Tests for _resume_interrupted_tasks()."""

    def _make_goal(self, tasks):
        goal = MagicMock()
        goal.tasks = tasks
        return goal

    def _make_task(self, status=TaskStatus.IN_PROGRESS, desc="test task", tid="t1"):
        task = MagicMock()
        task.status = status
        task.description = desc
        task.task_id = tid
        return task

    def test_no_in_progress_tasks_returns_zero(self):
        gm = MagicMock()
        task = self._make_task(status=TaskStatus.PENDING)
        gm.goals = {"g1": self._make_goal([task])}
        stop = threading.Event()
        executed, cost = _resume_interrupted_tasks(
            gm, MagicMock(), MagicMock(), stop, [], lambda: None, None, 50, 0.50,
        )
        assert executed == 0
        assert cost == 0.0

    @patch("src.core.autonomous_executor.execute_task")
    def test_resumes_in_progress_task(self, mock_exec):
        mock_exec.return_value = {"cost_usd": 0.05, "status": "completed"}
        gm = MagicMock()
        task = self._make_task()
        gm.goals = {"g1": self._make_goal([task])}
        stop = threading.Event()
        executed, cost = _resume_interrupted_tasks(
            gm, MagicMock(), MagicMock(), stop, [], lambda: None, None, 50, 0.50,
        )
        assert executed == 1
        assert cost == 0.05
        gm.complete_task.assert_called_once_with("t1", mock_exec.return_value)
        gm.save_state.assert_called_once()

    @patch("src.core.autonomous_executor.execute_task")
    def test_stops_when_stop_flag_set(self, mock_exec):
        gm = MagicMock()
        task = self._make_task()
        gm.goals = {"g1": self._make_goal([task])}
        stop = threading.Event()
        stop.set()
        executed, cost = _resume_interrupted_tasks(
            gm, MagicMock(), MagicMock(), stop, [], lambda: None, None, 50, 0.50,
        )
        assert executed == 0
        mock_exec.assert_not_called()

    @patch("src.core.autonomous_executor.execute_task")
    def test_stops_at_max_tasks(self, mock_exec):
        mock_exec.return_value = {"cost_usd": 0.01}
        gm = MagicMock()
        t1 = self._make_task(tid="t1")
        t2 = self._make_task(tid="t2")
        # Tasks in separate goals — outer loop checks max_tasks per-goal
        gm.goals = {
            "g1": self._make_goal([t1]),
            "g2": self._make_goal([t2]),
        }
        stop = threading.Event()
        executed, cost = _resume_interrupted_tasks(
            gm, MagicMock(), MagicMock(), stop, [], lambda: None, None, 1, 0.50,
        )
        assert executed == 1
        assert mock_exec.call_count == 1

    @patch("src.core.autonomous_executor.execute_task")
    def test_stops_at_budget_limit(self, mock_exec):
        mock_exec.return_value = {"cost_usd": 0.60}
        gm = MagicMock()
        t1 = self._make_task(tid="t1")
        t2 = self._make_task(tid="t2")
        goal = self._make_goal([t1, t2])
        gm.goals = {"g1": goal}
        stop = threading.Event()
        executed, cost = _resume_interrupted_tasks(
            gm, MagicMock(), MagicMock(), stop, [], lambda: None, None, 50, 0.50,
        )
        assert executed == 1
        assert cost == 0.60

    @patch("src.core.autonomous_executor.execute_task")
    def test_exception_fails_task(self, mock_exec):
        mock_exec.side_effect = RuntimeError("boom")
        gm = MagicMock()
        task = self._make_task()
        gm.goals = {"g1": self._make_goal([task])}
        stop = threading.Event()
        executed, cost = _resume_interrupted_tasks(
            gm, MagicMock(), MagicMock(), stop, [], lambda: None, None, 50, 0.50,
        )
        assert executed == 0
        gm.fail_task.assert_called_once_with("t1", "boom")

    @patch("src.core.autonomous_executor.execute_task")
    def test_multiple_goals_multiple_tasks(self, mock_exec):
        mock_exec.return_value = {"cost_usd": 0.02}
        gm = MagicMock()
        t1 = self._make_task(tid="t1")
        t2 = self._make_task(status=TaskStatus.PENDING, tid="t2")
        t3 = self._make_task(tid="t3")
        gm.goals = {
            "g1": self._make_goal([t1, t2]),
            "g2": self._make_goal([t3]),
        }
        stop = threading.Event()
        executed, cost = _resume_interrupted_tasks(
            gm, MagicMock(), MagicMock(), stop, [], lambda: None, None, 50, 0.50,
        )
        # t1 and t3 are IN_PROGRESS, t2 is PENDING
        assert executed == 2
        assert cost == pytest.approx(0.04)


# ── _decompose_pending_goals tests ───────────────────────────────────


class TestDecomposePendingGoals:
    """Tests for _decompose_pending_goals()."""

    def _make_goal(self, desc="test goal", decomposed=False, complete=False):
        goal = MagicMock()
        goal.description = desc
        goal.is_decomposed = decomposed
        goal.is_complete.return_value = complete
        goal.goal_id = "g1"
        goal.tasks = [MagicMock(), MagicMock()]
        return goal

    def test_no_goals_returns_zero(self):
        gm = MagicMock()
        gm.goals = {}
        stop = threading.Event()
        result = _decompose_pending_goals(gm, MagicMock(), MagicMock(), stop)
        assert result == 0

    def test_all_decomposed_returns_zero(self):
        gm = MagicMock()
        gm.goals = {"g1": self._make_goal(decomposed=True)}
        stop = threading.Event()
        result = _decompose_pending_goals(gm, MagicMock(), MagicMock(), stop)
        assert result == 0
        gm.decompose_goal.assert_not_called()

    def test_all_complete_returns_zero(self):
        gm = MagicMock()
        gm.goals = {"g1": self._make_goal(complete=True)}
        stop = threading.Event()
        result = _decompose_pending_goals(gm, MagicMock(), MagicMock(), stop)
        assert result == 0

    @patch("src.core.autonomous_executor._resolve_project_path", return_value=None)
    def test_decomposes_undecomposed_goal(self, mock_resolve):
        gm = MagicMock()
        goal = self._make_goal()
        gm.goals = {"g1": goal}
        stop = threading.Event()
        ls = MagicMock()
        ls.get_active_insights.return_value = ["hint1"]
        router = MagicMock()
        result = _decompose_pending_goals(gm, router, ls, stop)
        assert result == 1
        gm.decompose_goal.assert_called_once()
        gm.save_state.assert_called_once()

    @patch("src.core.autonomous_executor._resolve_project_path", return_value="/fake/path")
    @patch("src.core.autonomous_executor.scan_project_files", create=True)
    def test_injects_project_context_when_path_found(self, mock_scan, mock_resolve):
        # Patch the import inside the function
        with patch.dict("sys.modules", {}):
            with patch("src.utils.project_context.scan_project_files", return_value=["a.py", "b.py"], create=True):
                gm = MagicMock()
                goal = self._make_goal()
                gm.goals = {"g1": goal}
                stop = threading.Event()
                ls = MagicMock()
                ls.get_active_insights.return_value = []
                _decompose_pending_goals(gm, MagicMock(), ls, stop)
                call_kwargs = gm.decompose_goal.call_args
                # discovery_brief should contain project path info
                brief = call_kwargs.kwargs.get("discovery_brief") or call_kwargs[1].get("discovery_brief")
                if brief:
                    assert "/fake/path" in brief

    def test_stops_when_stop_flag_set(self):
        gm = MagicMock()
        gm.goals = {"g1": self._make_goal()}
        stop = threading.Event()
        stop.set()
        result = _decompose_pending_goals(gm, MagicMock(), MagicMock(), stop)
        assert result == 0
        gm.decompose_goal.assert_not_called()

    @patch("src.core.autonomous_executor._resolve_project_path", return_value=None)
    def test_caps_at_five_goals(self, mock_resolve):
        gm = MagicMock()
        goals = {}
        for i in range(8):
            g = self._make_goal(desc=f"goal {i}")
            g.goal_id = f"g{i}"
            goals[f"g{i}"] = g
        gm.goals = goals
        stop = threading.Event()
        ls = MagicMock()
        ls.get_active_insights.return_value = []
        result = _decompose_pending_goals(gm, MagicMock(), ls, stop)
        assert result == 5
        assert gm.decompose_goal.call_count == 5

    @patch("src.core.autonomous_executor._resolve_project_path", return_value=None)
    def test_decomposition_error_continues_to_next(self, mock_resolve):
        gm = MagicMock()
        g1 = self._make_goal(desc="fail goal")
        g1.goal_id = "g1"
        g2 = self._make_goal(desc="ok goal")
        g2.goal_id = "g2"
        gm.goals = {"g1": g1, "g2": g2}
        gm.decompose_goal.side_effect = [RuntimeError("fail"), None]
        stop = threading.Event()
        ls = MagicMock()
        ls.get_active_insights.return_value = []
        result = _decompose_pending_goals(gm, MagicMock(), ls, stop)
        # First fails, second succeeds
        assert result == 1
        assert gm.decompose_goal.call_count == 2


# ── _store_task_memory tests ──────────────────────────────────────────


class TestStoreTaskMemory:
    """Tests for _store_task_memory()."""

    def _make_task_and_goal(self):
        task = MagicMock()
        task.description = "Write tests"
        task.task_id = "t1"
        task.goal_id = "g1"
        goal = MagicMock()
        goal.description = "Improve quality"
        return task, goal

    def test_no_memory_is_noop(self):
        from src.core.autonomous_executor import _store_task_memory
        task, goal = self._make_task_and_goal()
        # Should not raise
        _store_task_memory(task, goal, {}, "", [], 0, True, None)

    def test_stores_success_as_research_result(self):
        from src.core.autonomous_executor import _store_task_memory
        task, goal = self._make_task_and_goal()
        memory = MagicMock()
        result = {"files_created": ["/path/to/file.py"]}
        _store_task_memory(task, goal, result, "All good", [], 0.05, True, memory)
        memory.store_long_term.assert_called_once()
        call_kwargs = memory.store_long_term.call_args[1]
        assert call_kwargs["memory_type"] == "research_result"
        assert "successfully" in call_kwargs["text"]

    def test_stores_failure_as_task_failure(self):
        from src.core.autonomous_executor import _store_task_memory
        task, goal = self._make_task_and_goal()
        memory = MagicMock()
        result = {"error": "timeout"}
        steps = [{"action": "web_search"}, {"action": "create_file"}]
        _store_task_memory(task, goal, result, "Failed", steps, 0.10, False, memory)
        call_kwargs = memory.store_long_term.call_args[1]
        assert call_kwargs["memory_type"] == "task_failure"
        assert "FAILED" in call_kwargs["text"]
        assert "web_search" in call_kwargs["text"] or "create_file" in call_kwargs["text"]

    def test_memory_exception_is_swallowed(self):
        from src.core.autonomous_executor import _store_task_memory
        task, goal = self._make_task_and_goal()
        memory = MagicMock()
        memory.store_long_term.side_effect = RuntimeError("db error")
        # Should not raise
        _store_task_memory(task, goal, {}, "", [], 0, True, memory)

    def test_includes_file_names_in_metadata(self):
        from src.core.autonomous_executor import _store_task_memory
        task, goal = self._make_task_and_goal()
        memory = MagicMock()
        result = {"files_created": ["/a/b/report.py", "/a/b/data.json"]}
        _store_task_memory(task, goal, result, "Done", [], 0.02, True, memory)
        meta = memory.store_long_term.call_args[1]["metadata"]
        assert "report.py" in meta["files_created"]
        assert "data.json" in meta["files_created"]


# ── _build_follow_up_prompt tests ─────────────────────────────────────


class TestBuildFollowUpPrompt:
    """Tests for _build_follow_up_prompt()."""

    def test_includes_goal_and_task(self):
        from src.core.autonomous_executor import _build_follow_up_prompt
        task = MagicMock()
        task.description = "Research APIs"
        goal = MagicMock()
        goal.description = "Build integration"
        goal.tasks = []
        result = _build_follow_up_prompt(task, goal, [("api.py", "import requests")])
        assert "Build integration" in result
        assert "Research APIs" in result

    def test_includes_file_contents(self):
        from src.core.autonomous_executor import _build_follow_up_prompt
        task = MagicMock()
        task.description = "t"
        goal = MagicMock()
        goal.description = "g"
        goal.tasks = []
        result = _build_follow_up_prompt(
            task, goal, [("output.txt", "hello world")],
        )
        assert "output.txt" in result
        assert "hello world" in result

    def test_lists_existing_tasks(self):
        from src.core.autonomous_executor import _build_follow_up_prompt
        task = MagicMock()
        task.description = "t"
        goal = MagicMock()
        goal.description = "g"
        existing = MagicMock()
        existing.description = "Already done task"
        goal.tasks = [existing]
        result = _build_follow_up_prompt(task, goal, [("f.txt", "x")])
        assert "Already done task" in result

    def test_contains_dedup_instruction(self):
        from src.core.autonomous_executor import _build_follow_up_prompt
        task = MagicMock()
        task.description = "t"
        goal = MagicMock()
        goal.description = "g"
        goal.tasks = []
        result = _build_follow_up_prompt(task, goal, [("f.txt", "x")])
        assert "DO NOT duplicate" in result
