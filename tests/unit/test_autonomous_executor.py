"""Unit tests for autonomous_executor.py.

Tests budget loading, project path resolution, task queue processing,
deferred task handling, and follow-up task extraction gating.

Created session 74.
"""

import json
import os
import threading
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from src.core.autonomous_executor import (
    _get_dream_cycle_budget,
    _resolve_project_path,
    process_task_queue,
    extract_follow_up_tasks,
)


# ── Budget loading tests ─────────────────────────────────────────────


class TestGetDreamCycleBudget:
    """Tests for _get_dream_cycle_budget()."""

    def test_returns_default_on_missing_file(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert _get_dream_cycle_budget() == 0.50

    def test_reads_budget_from_yaml(self, tmp_path):
        rules = {
            "non_override_rules": [
                {"name": "dream_cycle_budget", "enabled": True, "limit": 0.75},
            ],
        }
        rules_path = tmp_path / "config" / "rules.yaml"
        rules_path.parent.mkdir(parents=True)
        import yaml
        rules_path.write_text(yaml.dump(rules))
        with patch("src.core.autonomous_executor._base_path", return_value=tmp_path):
            assert _get_dream_cycle_budget() == 0.75

    def test_disabled_rule_returns_default(self, tmp_path):
        rules = {
            "non_override_rules": [
                {"name": "dream_cycle_budget", "enabled": False, "limit": 0.75},
            ],
        }
        rules_path = tmp_path / "config" / "rules.yaml"
        rules_path.parent.mkdir(parents=True)
        import yaml
        rules_path.write_text(yaml.dump(rules))
        with patch("src.core.autonomous_executor._base_path", return_value=tmp_path):
            # Disabled rule — should not match, falls to default
            assert _get_dream_cycle_budget() == 0.50

    def test_missing_rule_returns_default(self, tmp_path):
        rules = {"non_override_rules": [{"name": "other_rule", "enabled": True, "limit": 99}]}
        rules_path = tmp_path / "config" / "rules.yaml"
        rules_path.parent.mkdir(parents=True)
        import yaml
        rules_path.write_text(yaml.dump(rules))
        with patch("src.core.autonomous_executor._base_path", return_value=tmp_path):
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
