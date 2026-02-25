"""Comprehensive unit tests for src/core/task_orchestrator.py.

Tests event-driven DAG task execution, configuration loading, task harvesting,
task finding, task submission, and the main orchestration loop.
"""

import threading
from concurrent.futures import Future
from typing import Dict, Any, List
from unittest.mock import patch, MagicMock, Mock

import pytest

from src.core.task_orchestrator import (
    _get_orchestrator_config,
    TaskOrchestrator,
    _run_single_task,
)


# ─────────────────────────────────────────────────────────────────────────────
# TestGetOrchestratorConfig
# ─────────────────────────────────────────────────────────────────────────────


class TestGetOrchestratorConfig:
    """Tests for _get_orchestrator_config() function."""

    def test_returns_defaults_when_yaml_not_found(self):
        """Returns default config when rules.yaml is not found."""
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = _get_orchestrator_config()
            assert result["enabled"] is True
            assert result["max_parallel_tasks_per_goal"] == 2

    def test_returns_defaults_on_exception(self):
        """Returns default config on any exception."""
        with patch("builtins.open", side_effect=Exception("Some error")):
            result = _get_orchestrator_config()
            assert result["enabled"] is True
            assert result["max_parallel_tasks_per_goal"] == 2

    def test_caps_max_parallel_at_4(self):
        """max_parallel_tasks_per_goal is capped at 4."""
        mock_yaml_data = {
            "task_orchestrator": {
                "enabled": True,
                "max_parallel_tasks_per_goal": 8,
            }
        }
        with patch("builtins.open", create=True):
            with patch("yaml.safe_load", return_value=mock_yaml_data):
                result = _get_orchestrator_config()
                assert result["max_parallel_tasks_per_goal"] == 4

    def test_loads_config_from_yaml(self):
        """Loads task_orchestrator config from rules.yaml."""
        mock_yaml_data = {
            "task_orchestrator": {
                "enabled": False,
                "max_parallel_tasks_per_goal": 3,
            }
        }
        with patch("builtins.open", create=True):
            with patch("yaml.safe_load", return_value=mock_yaml_data):
                result = _get_orchestrator_config()
                assert result["enabled"] is False
                assert result["max_parallel_tasks_per_goal"] == 3

    def test_uses_defaults_for_missing_keys(self):
        """Uses default values for missing keys in config."""
        mock_yaml_data = {
            "task_orchestrator": {
                "enabled": False,
                # missing max_parallel_tasks_per_goal
            }
        }
        with patch("builtins.open", create=True):
            with patch("yaml.safe_load", return_value=mock_yaml_data):
                result = _get_orchestrator_config()
                assert result["enabled"] is False
                assert result["max_parallel_tasks_per_goal"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# TestTaskOrchestratorInit
# ─────────────────────────────────────────────────────────────────────────────


class TestTaskOrchestratorInit:
    """Tests for TaskOrchestrator.__init__()."""

    def test_init_enabled_orchestrator(self):
        """Initializes with enabled=True and correct max_parallel."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            mock_cfg.return_value = {
                "enabled": True,
                "max_parallel_tasks_per_goal": 2,
            }
            orchestrator = TaskOrchestrator()
            assert orchestrator._enabled is True
            assert orchestrator._max_parallel == 2

    def test_init_disabled_orchestrator(self):
        """When disabled, sets _max_parallel to 1 (sequential fallback)."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            mock_cfg.return_value = {
                "enabled": False,
                "max_parallel_tasks_per_goal": 4,
            }
            orchestrator = TaskOrchestrator()
            assert orchestrator._enabled is False
            assert orchestrator._max_parallel == 1

    def test_init_respects_config_max_parallel(self):
        """Respects max_parallel value from config when enabled."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            mock_cfg.return_value = {
                "enabled": True,
                "max_parallel_tasks_per_goal": 3,
            }
            orchestrator = TaskOrchestrator()
            assert orchestrator._max_parallel == 3

    def test_init_logs_info(self):
        """Logs initialization info."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            with patch("src.core.task_orchestrator.logger") as mock_logger:
                mock_cfg.return_value = {
                    "enabled": True,
                    "max_parallel_tasks_per_goal": 2,
                }
                TaskOrchestrator()
                mock_logger.info.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# TestHarvestResult
# ─────────────────────────────────────────────────────────────────────────────


class TestHarvestResult:
    """Tests for TaskOrchestrator._harvest_result() static method."""

    def test_harvest_successful_result(self):
        """Extracts result from successful future."""
        expected_result = {
            "executed": True,
            "cost_usd": 0.50,
            "analysis": "Task completed successfully",
        }
        future = MagicMock(spec=Future)
        future.result.return_value = expected_result

        result = TaskOrchestrator._harvest_result(future, "goal_123", "task_456")

        assert result == expected_result
        future.result.assert_called_once()

    def test_harvest_handles_exception_from_future(self):
        """Catches exception from future and returns error dict."""
        future = MagicMock(spec=Future)
        future.result.side_effect = RuntimeError("Task execution failed")

        result = TaskOrchestrator._harvest_result(future, "goal_123", "task_456")

        assert result["executed"] is False
        assert "Task execution failed" in result["error"]
        assert result["cost_usd"] == 0
        assert result["analysis"] == ""

    def test_harvest_logs_exception(self):
        """Logs exception when harvesting fails."""
        future = MagicMock(spec=Future)
        future.result.side_effect = ValueError("Invalid task")

        with patch("src.core.task_orchestrator.logger") as mock_logger:
            TaskOrchestrator._harvest_result(future, "goal_123", "task_456")
            mock_logger.error.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# TestFindTask
# ─────────────────────────────────────────────────────────────────────────────


class TestFindTask:
    """Tests for TaskOrchestrator._find_task() static method."""

    def test_find_task_success(self):
        """Finds task by ID within goal."""
        task_to_find = MagicMock()
        task_to_find.task_id = "task_123"

        goal = MagicMock()
        goal.tasks = [
            MagicMock(task_id="task_001"),
            task_to_find,
            MagicMock(task_id="task_002"),
        ]

        goal_manager = MagicMock()
        goal_manager.goals = {"goal_456": goal}

        result = TaskOrchestrator._find_task(goal_manager, "goal_456", "task_123")

        assert result is task_to_find

    def test_find_task_not_found(self):
        """Returns None when task is not found."""
        goal = MagicMock()
        goal.tasks = [
            MagicMock(task_id="task_001"),
            MagicMock(task_id="task_002"),
        ]

        goal_manager = MagicMock()
        goal_manager.goals = {"goal_456": goal}

        result = TaskOrchestrator._find_task(goal_manager, "goal_456", "task_999")

        assert result is None

    def test_find_task_goal_not_found(self):
        """Returns None when goal is not found."""
        goal_manager = MagicMock()
        goal_manager.goals = {}

        result = TaskOrchestrator._find_task(goal_manager, "goal_999", "task_123")

        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# TestRunSingleTask
# ─────────────────────────────────────────────────────────────────────────────


class TestRunSingleTask:
    """Tests for _run_single_task() module-level function."""

    def test_delegates_to_execute_task_fn(self):
        """Calls execute_task_fn with correct arguments."""
        mock_task = MagicMock()
        mock_goal_manager = MagicMock()
        mock_execute_fn = MagicMock(return_value={"executed": True, "cost_usd": 0.50})
        mock_router = MagicMock()
        mock_learning = MagicMock()
        mock_save_fn = MagicMock()

        result = _run_single_task(
            task=mock_task,
            goal_manager=mock_goal_manager,
            execute_task_fn=mock_execute_fn,
            router=mock_router,
            learning_system=mock_learning,
            overnight_results=[],
            save_overnight_results=mock_save_fn,
            sibling_context=["context"],
            memory=None,
        )

        mock_execute_fn.assert_called_once()
        call_kwargs = mock_execute_fn.call_args[1]
        assert call_kwargs["task"] is mock_task
        assert call_kwargs["goal_manager"] is mock_goal_manager
        assert call_kwargs["router"] is mock_router
        assert call_kwargs["learning_system"] is mock_learning
        assert call_kwargs["sibling_task_summaries"] == ["context"]
        assert result == {"executed": True, "cost_usd": 0.50}

    def test_returns_result_from_execute_task_fn(self):
        """Returns the result dict from execute_task_fn."""
        expected_result = {
            "executed": True,
            "cost_usd": 1.25,
            "analysis": "Task completed",
            "error": None,
        }
        mock_execute_fn = MagicMock(return_value=expected_result)

        result = _run_single_task(
            task=MagicMock(),
            goal_manager=MagicMock(),
            execute_task_fn=mock_execute_fn,
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
            sibling_context=[],
            memory=None,
        )

        assert result == expected_result


# ─────────────────────────────────────────────────────────────────────────────
# TestSubmitReadyTasks
# ─────────────────────────────────────────────────────────────────────────────


class TestSubmitReadyTasks:
    """Tests for TaskOrchestrator._submit_ready_tasks() method."""

    def test_submits_ready_tasks(self):
        """Submits all ready tasks that aren't running."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            mock_cfg.return_value = {
                "enabled": True,
                "max_parallel_tasks_per_goal": 3,
            }
            orchestrator = TaskOrchestrator()

            # Create mock tasks
            task_1 = MagicMock()
            task_1.task_id = "task_1"
            task_2 = MagicMock()
            task_2.task_id = "task_2"

            # Create mock goal
            goal = MagicMock()
            goal.get_ready_tasks.return_value = [task_1, task_2]

            goal_manager = MagicMock()
            goal_manager.goals = {"goal_id": goal}

            # Mock thread pool with different futures for each submission
            mock_pool = MagicMock()
            mock_future1 = MagicMock(spec=Future)
            mock_future2 = MagicMock(spec=Future)
            mock_pool.submit.side_effect = [mock_future1, mock_future2]

            running_futures = {}

            orchestrator._submit_ready_tasks(
                pool=mock_pool,
                running_futures=running_futures,
                goal_id="goal_id",
                goal_manager=goal_manager,
                execute_task_fn=MagicMock(),
                router=MagicMock(),
                learning_system=MagicMock(),
                overnight_results=[],
                save_overnight_results=MagicMock(),
                goal_task_context=[],
                memory=None,
                budget_remaining=10.0,
            )

            # Both tasks should be submitted
            assert mock_pool.submit.call_count == 2
            assert len(running_futures) == 2

    def test_respects_max_parallel_slots(self):
        """Only submits up to max_parallel available slots."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            mock_cfg.return_value = {
                "enabled": True,
                "max_parallel_tasks_per_goal": 2,
            }
            orchestrator = TaskOrchestrator()

            # Create mock tasks
            task_1 = MagicMock()
            task_1.task_id = "task_1"
            task_2 = MagicMock()
            task_2.task_id = "task_2"
            task_3 = MagicMock()
            task_3.task_id = "task_3"

            goal = MagicMock()
            goal.get_ready_tasks.return_value = [task_1, task_2, task_3]

            goal_manager = MagicMock()
            goal_manager.goals = {"goal_id": goal}

            mock_pool = MagicMock()
            mock_future = MagicMock(spec=Future)
            mock_pool.submit.return_value = mock_future

            running_futures = {}

            orchestrator._submit_ready_tasks(
                pool=mock_pool,
                running_futures=running_futures,
                goal_id="goal_id",
                goal_manager=goal_manager,
                execute_task_fn=MagicMock(),
                router=MagicMock(),
                learning_system=MagicMock(),
                overnight_results=[],
                save_overnight_results=MagicMock(),
                goal_task_context=[],
                memory=None,
                budget_remaining=10.0,
            )

            # Only 2 tasks should be submitted (max_parallel=2)
            assert mock_pool.submit.call_count == 2

    def test_skips_already_running_tasks(self):
        """Does not submit tasks that are already running."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            mock_cfg.return_value = {
                "enabled": True,
                "max_parallel_tasks_per_goal": 3,
            }
            orchestrator = TaskOrchestrator()

            task_1 = MagicMock()
            task_1.task_id = "task_1"

            goal = MagicMock()
            goal.get_ready_tasks.return_value = [task_1]

            goal_manager = MagicMock()
            goal_manager.goals = {"goal_id": goal}

            mock_pool = MagicMock()
            mock_future = MagicMock(spec=Future)

            # task_1 is already running
            running_futures = {mock_future: "task_1"}

            orchestrator._submit_ready_tasks(
                pool=mock_pool,
                running_futures=running_futures,
                goal_id="goal_id",
                goal_manager=goal_manager,
                execute_task_fn=MagicMock(),
                router=MagicMock(),
                learning_system=MagicMock(),
                overnight_results=[],
                save_overnight_results=MagicMock(),
                goal_task_context=[],
                memory=None,
                budget_remaining=10.0,
            )

            # Should not submit again
            mock_pool.submit.assert_not_called()

    def test_does_not_submit_when_budget_is_zero(self):
        """Does not submit tasks when budget_remaining <= 0."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            mock_cfg.return_value = {
                "enabled": True,
                "max_parallel_tasks_per_goal": 2,
            }
            orchestrator = TaskOrchestrator()

            task = MagicMock()
            task.task_id = "task_1"

            goal = MagicMock()
            goal.get_ready_tasks.return_value = [task]

            goal_manager = MagicMock()
            goal_manager.goals = {"goal_id": goal}

            mock_pool = MagicMock()

            orchestrator._submit_ready_tasks(
                pool=mock_pool,
                running_futures={},
                goal_id="goal_id",
                goal_manager=goal_manager,
                execute_task_fn=MagicMock(),
                router=MagicMock(),
                learning_system=MagicMock(),
                overnight_results=[],
                save_overnight_results=MagicMock(),
                goal_task_context=[],
                memory=None,
                budget_remaining=0.0,
            )

            # Should not submit when budget is exhausted
            mock_pool.submit.assert_not_called()

    def test_does_not_submit_when_goal_not_found(self):
        """Does not submit tasks when goal is not found."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            mock_cfg.return_value = {
                "enabled": True,
                "max_parallel_tasks_per_goal": 2,
            }
            orchestrator = TaskOrchestrator()

            goal_manager = MagicMock()
            goal_manager.goals = {}  # No goals

            mock_pool = MagicMock()

            orchestrator._submit_ready_tasks(
                pool=mock_pool,
                running_futures={},
                goal_id="goal_id",
                goal_manager=goal_manager,
                execute_task_fn=MagicMock(),
                router=MagicMock(),
                learning_system=MagicMock(),
                overnight_results=[],
                save_overnight_results=MagicMock(),
                goal_task_context=[],
                memory=None,
                budget_remaining=10.0,
            )

            # Should not submit
            mock_pool.submit.assert_not_called()

    def test_calls_start_task_on_goal_manager(self):
        """Calls goal_manager.start_task() for each submitted task."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            mock_cfg.return_value = {
                "enabled": True,
                "max_parallel_tasks_per_goal": 2,
            }
            orchestrator = TaskOrchestrator()

            task_1 = MagicMock()
            task_1.task_id = "task_1"
            task_2 = MagicMock()
            task_2.task_id = "task_2"

            goal = MagicMock()
            goal.get_ready_tasks.return_value = [task_1, task_2]

            goal_manager = MagicMock()
            goal_manager.goals = {"goal_id": goal}

            mock_pool = MagicMock()
            mock_future = MagicMock(spec=Future)
            mock_pool.submit.return_value = mock_future

            orchestrator._submit_ready_tasks(
                pool=mock_pool,
                running_futures={},
                goal_id="goal_id",
                goal_manager=goal_manager,
                execute_task_fn=MagicMock(),
                router=MagicMock(),
                learning_system=MagicMock(),
                overnight_results=[],
                save_overnight_results=MagicMock(),
                goal_task_context=[],
                memory=None,
                budget_remaining=10.0,
            )

            # start_task should be called for each task
            assert goal_manager.start_task.call_count == 2
            goal_manager.start_task.assert_any_call("task_1")
            goal_manager.start_task.assert_any_call("task_2")


# ─────────────────────────────────────────────────────────────────────────────
# TestExecuteGoalTasks
# ─────────────────────────────────────────────────────────────────────────────


class TestExecuteGoalTasks:
    """Tests for TaskOrchestrator.execute_goal_tasks() main loop."""

    def test_happy_path_single_task_completes(self):
        """Happy path: single task completes successfully."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            with patch("src.core.task_orchestrator.ThreadPoolExecutor") as mock_executor:
                with patch("src.core.task_orchestrator.as_completed") as mock_as_completed:
                    mock_cfg.return_value = {
                        "enabled": True,
                        "max_parallel_tasks_per_goal": 2,
                    }
                    orchestrator = TaskOrchestrator()

                    # Setup task
                    task = MagicMock()
                    task.task_id = "task_1"
                    task.description = "Task 1"

                    goal = MagicMock()
                    goal.tasks = [task]
                    goal.get_ready_tasks.side_effect = [
                        [task],  # First call: task is ready
                        [],      # Second call: no more ready tasks
                    ]

                    goal_manager = MagicMock()
                    goal_manager.goals = {"goal_1": goal}

                    # Setup future
                    mock_future = MagicMock(spec=Future)
                    mock_future.result.return_value = {
                        "executed": True,
                        "cost_usd": 0.50,
                        "analysis": "Task result",
                        "error": None,
                    }

                    # Setup pool executor
                    mock_pool = MagicMock()
                    mock_pool.submit.return_value = mock_future
                    mock_pool.__enter__ = MagicMock(return_value=mock_pool)
                    mock_pool.__exit__ = MagicMock(return_value=None)
                    mock_executor.return_value = mock_pool

                    # Setup as_completed to return task once, then stop
                    def as_completed_side_effect(futures):
                        if futures:
                            yield mock_future

                    mock_as_completed.side_effect = as_completed_side_effect

                    stop_flag = threading.Event()

                    result = orchestrator.execute_goal_tasks(
                        goal_id="goal_1",
                        goal_manager=goal_manager,
                        execute_task_fn=MagicMock(),
                        router=MagicMock(),
                        learning_system=MagicMock(),
                        overnight_results=[],
                        save_overnight_results=MagicMock(),
                        stop_flag=stop_flag,
                        budget_remaining=10.0,
                        memory=None,
                    )

                    assert result["tasks_completed"] == 1
                    assert result["tasks_failed"] == 0
                    assert result["total_cost"] == 0.50

    def test_respects_stop_flag(self):
        """Stops execution when stop_flag is set."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            with patch("src.core.task_orchestrator.ThreadPoolExecutor") as mock_executor:
                with patch("src.core.task_orchestrator.as_completed") as mock_as_completed:
                    mock_cfg.return_value = {
                        "enabled": True,
                        "max_parallel_tasks_per_goal": 2,
                    }
                    orchestrator = TaskOrchestrator()

                    task = MagicMock()
                    task.task_id = "task_1"
                    goal = MagicMock()
                    goal.get_ready_tasks.return_value = [task]
                    goal_manager = MagicMock()
                    goal_manager.goals = {"goal_1": goal}

                    mock_pool = MagicMock()
                    mock_future = MagicMock(spec=Future)
                    mock_future.result.return_value = {
                        "executed": True,
                        "cost_usd": 0.25,
                        "analysis": "",
                        "error": None,
                    }
                    mock_pool.submit.return_value = mock_future
                    mock_pool.__enter__ = MagicMock(return_value=mock_pool)
                    mock_pool.__exit__ = MagicMock(return_value=None)
                    mock_executor.return_value = mock_pool

                    # Set stop flag immediately
                    stop_flag = threading.Event()
                    stop_flag.set()

                    # as_completed never called because stop flag is already set
                    mock_as_completed.return_value = []

                    result = orchestrator.execute_goal_tasks(
                        goal_id="goal_1",
                        goal_manager=goal_manager,
                        execute_task_fn=MagicMock(),
                        router=MagicMock(),
                        learning_system=MagicMock(),
                        overnight_results=[],
                        save_overnight_results=MagicMock(),
                        stop_flag=stop_flag,
                        budget_remaining=10.0,
                        memory=None,
                    )

                    # Should return 0 completed since stop flag prevents submission
                    assert result["tasks_completed"] == 0
                    assert result["tasks_failed"] == 0

    def test_stops_on_budget_exhaustion(self):
        """Stops execution when budget is exhausted."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            with patch("src.core.task_orchestrator.ThreadPoolExecutor") as mock_executor:
                with patch("src.core.task_orchestrator.as_completed") as mock_as_completed:
                    mock_cfg.return_value = {
                        "enabled": True,
                        "max_parallel_tasks_per_goal": 2,
                    }
                    orchestrator = TaskOrchestrator()

                    task = MagicMock()
                    task.task_id = "task_1"
                    task.description = "Task 1"
                    goal = MagicMock()
                    goal.tasks = [task]
                    goal.get_ready_tasks.return_value = [task]
                    goal_manager = MagicMock()
                    goal_manager.goals = {"goal_1": goal}

                    mock_pool = MagicMock()
                    mock_future = MagicMock(spec=Future)
                    # Cost exceeds budget
                    mock_future.result.return_value = {
                        "executed": True,
                        "cost_usd": 15.0,
                        "analysis": "Task result",
                        "error": None,
                    }
                    mock_pool.submit.return_value = mock_future
                    mock_pool.__enter__ = MagicMock(return_value=mock_pool)
                    mock_pool.__exit__ = MagicMock(return_value=None)
                    mock_executor.return_value = mock_pool

                    mock_as_completed.return_value = [mock_future]

                    stop_flag = threading.Event()

                    result = orchestrator.execute_goal_tasks(
                        goal_id="goal_1",
                        goal_manager=goal_manager,
                        execute_task_fn=MagicMock(),
                        router=MagicMock(),
                        learning_system=MagicMock(),
                        overnight_results=[],
                        save_overnight_results=MagicMock(),
                        stop_flag=stop_flag,
                        budget_remaining=10.0,
                        memory=None,
                    )

                    # Task completes but budget exceeded
                    assert result["tasks_completed"] == 1
                    assert result["total_cost"] == 15.0

    def test_stops_on_three_consecutive_failures(self):
        """Stops execution after 3 consecutive task failures."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            with patch("src.core.task_orchestrator.ThreadPoolExecutor") as mock_executor:
                with patch("src.core.task_orchestrator.as_completed") as mock_as_completed:
                    mock_cfg.return_value = {
                        "enabled": True,
                        "max_parallel_tasks_per_goal": 2,
                    }
                    orchestrator = TaskOrchestrator()

                    # Create 3 failed tasks
                    tasks = [MagicMock() for _ in range(3)]
                    for i, task in enumerate(tasks):
                        task.task_id = f"task_{i+1}"

                    goal = MagicMock()
                    goal.get_ready_tasks.return_value = tasks[:1]
                    goal_manager = MagicMock()
                    goal_manager.goals = {"goal_1": goal}

                    mock_pool = MagicMock()
                    mock_futures = []
                    for i in range(3):
                        fut = MagicMock(spec=Future)
                        fut.result.side_effect = RuntimeError(f"Task {i} failed")
                        mock_futures.append(fut)
                    mock_pool.submit.side_effect = mock_futures
                    mock_pool.__enter__ = MagicMock(return_value=mock_pool)
                    mock_pool.__exit__ = MagicMock(return_value=None)
                    mock_executor.return_value = mock_pool

                    # Simulate 3 task completions with failures
                    mock_as_completed.side_effect = [
                        [mock_futures[0]],
                        [mock_futures[1]],
                        [mock_futures[2]],
                    ]

                    stop_flag = threading.Event()

                    result = orchestrator.execute_goal_tasks(
                        goal_id="goal_1",
                        goal_manager=goal_manager,
                        execute_task_fn=MagicMock(),
                        router=MagicMock(),
                        learning_system=MagicMock(),
                        overnight_results=[],
                        save_overnight_results=MagicMock(),
                        stop_flag=stop_flag,
                        budget_remaining=100.0,
                        memory=None,
                    )

                    # 3 failures should trigger stop
                    assert result["tasks_failed"] == 3

    def test_no_ready_tasks_returns_empty_result(self):
        """Returns empty result when no ready tasks to start."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            with patch("src.core.task_orchestrator.ThreadPoolExecutor") as mock_executor:
                mock_cfg.return_value = {
                    "enabled": True,
                    "max_parallel_tasks_per_goal": 2,
                }
                orchestrator = TaskOrchestrator()

                goal = MagicMock()
                goal.get_ready_tasks.return_value = []  # No ready tasks
                goal_manager = MagicMock()
                goal_manager.goals = {"goal_1": goal}

                mock_pool = MagicMock()
                mock_pool.__enter__ = MagicMock(return_value=mock_pool)
                mock_pool.__exit__ = MagicMock(return_value=None)
                mock_executor.return_value = mock_pool

                stop_flag = threading.Event()

                result = orchestrator.execute_goal_tasks(
                    goal_id="goal_1",
                    goal_manager=goal_manager,
                    execute_task_fn=MagicMock(),
                    router=MagicMock(),
                    learning_system=MagicMock(),
                    overnight_results=[],
                    save_overnight_results=MagicMock(),
                    stop_flag=stop_flag,
                    budget_remaining=10.0,
                    memory=None,
                )

                assert result == {
                    "total_cost": 0,
                    "tasks_completed": 0,
                    "tasks_failed": 0,
                }

    def test_task_failure_calls_fail_task(self):
        """Calls goal_manager.fail_task() when task fails."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            with patch("src.core.task_orchestrator.ThreadPoolExecutor") as mock_executor:
                with patch("src.core.task_orchestrator.as_completed") as mock_as_completed:
                    mock_cfg.return_value = {
                        "enabled": True,
                        "max_parallel_tasks_per_goal": 2,
                    }
                    orchestrator = TaskOrchestrator()

                    task = MagicMock()
                    task.task_id = "task_1"

                    goal = MagicMock()
                    goal.get_ready_tasks.side_effect = [
                        [task],  # First call: task is ready
                        [],      # Second call: no more tasks (stop loop)
                    ]
                    goal_manager = MagicMock()
                    goal_manager.goals = {"goal_1": goal}

                    mock_pool = MagicMock()
                    mock_future = MagicMock(spec=Future)
                    mock_future.result.return_value = {
                        "executed": False,
                        "cost_usd": 0,
                        "analysis": "",
                        "error": "Task error",
                    }
                    mock_pool.submit.return_value = mock_future
                    mock_pool.__enter__ = MagicMock(return_value=mock_pool)
                    mock_pool.__exit__ = MagicMock(return_value=None)
                    mock_executor.return_value = mock_pool

                    def as_completed_side_effect(futures):
                        if futures:
                            yield mock_future

                    mock_as_completed.side_effect = as_completed_side_effect

                    stop_flag = threading.Event()

                    orchestrator.execute_goal_tasks(
                        goal_id="goal_1",
                        goal_manager=goal_manager,
                        execute_task_fn=MagicMock(),
                        router=MagicMock(),
                        learning_system=MagicMock(),
                        overnight_results=[],
                        save_overnight_results=MagicMock(),
                        stop_flag=stop_flag,
                        budget_remaining=10.0,
                        memory=None,
                    )

                    goal_manager.fail_task.assert_called_once_with(
                        "task_1", "Task error"
                    )

    def test_successful_task_calls_complete_task(self):
        """Calls goal_manager.complete_task() when task succeeds."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            with patch("src.core.task_orchestrator.ThreadPoolExecutor") as mock_executor:
                with patch("src.core.task_orchestrator.as_completed") as mock_as_completed:
                    mock_cfg.return_value = {
                        "enabled": True,
                        "max_parallel_tasks_per_goal": 2,
                    }
                    orchestrator = TaskOrchestrator()

                    task = MagicMock()
                    task.task_id = "task_1"
                    task.description = "Task 1"

                    goal = MagicMock()
                    goal.tasks = [task]
                    goal.get_ready_tasks.side_effect = [
                        [task],  # First call: task is ready
                        [],      # Second call: no more tasks (stop loop)
                    ]
                    goal_manager = MagicMock()
                    goal_manager.goals = {"goal_1": goal}

                    result_dict = {
                        "executed": True,
                        "cost_usd": 0.50,
                        "analysis": "Done",
                        "error": None,
                    }

                    mock_pool = MagicMock()
                    mock_future = MagicMock(spec=Future)
                    mock_future.result.return_value = result_dict
                    mock_pool.submit.return_value = mock_future
                    mock_pool.__enter__ = MagicMock(return_value=mock_pool)
                    mock_pool.__exit__ = MagicMock(return_value=None)
                    mock_executor.return_value = mock_pool

                    def as_completed_side_effect(futures):
                        if futures:
                            yield mock_future

                    mock_as_completed.side_effect = as_completed_side_effect

                    stop_flag = threading.Event()

                    orchestrator.execute_goal_tasks(
                        goal_id="goal_1",
                        goal_manager=goal_manager,
                        execute_task_fn=MagicMock(),
                        router=MagicMock(),
                        learning_system=MagicMock(),
                        overnight_results=[],
                        save_overnight_results=MagicMock(),
                        stop_flag=stop_flag,
                        budget_remaining=10.0,
                        memory=None,
                    )

                    goal_manager.complete_task.assert_called_once_with(
                        "task_1", result_dict
                    )

    def test_accumulates_context_from_completed_tasks(self):
        """Accumulates task context for sibling hints."""
        with patch("src.core.task_orchestrator._get_orchestrator_config") as mock_cfg:
            with patch("src.core.task_orchestrator.ThreadPoolExecutor") as mock_executor:
                with patch("src.core.task_orchestrator.as_completed") as mock_as_completed:
                    mock_cfg.return_value = {
                        "enabled": True,
                        "max_parallel_tasks_per_goal": 2,
                    }
                    orchestrator = TaskOrchestrator()

                    # Create first task
                    task1 = MagicMock()
                    task1.task_id = "task_1"
                    task1.description = "First task"

                    # Create second task
                    task2 = MagicMock()
                    task2.task_id = "task_2"
                    task2.description = "Second task"

                    goal = MagicMock()
                    goal.tasks = [task1, task2]
                    goal.get_ready_tasks.side_effect = [
                        [task1],  # First call: task1 is ready
                        [task2],  # Second call: task2 is ready
                        [],       # Third call: no more tasks
                    ]

                    goal_manager = MagicMock()
                    goal_manager.goals = {"goal_1": goal}

                    # Task 1 completes successfully
                    mock_future1 = MagicMock(spec=Future)
                    mock_future1.result.return_value = {
                        "executed": True,
                        "cost_usd": 0.25,
                        "analysis": "Task 1 analysis",
                        "error": None,
                    }

                    # Task 2 completes successfully
                    mock_future2 = MagicMock(spec=Future)
                    mock_future2.result.return_value = {
                        "executed": True,
                        "cost_usd": 0.25,
                        "analysis": "Task 2 analysis",
                        "error": None,
                    }

                    mock_pool = MagicMock()
                    mock_pool.submit.side_effect = [mock_future1, mock_future2]
                    mock_pool.__enter__ = MagicMock(return_value=mock_pool)
                    mock_pool.__exit__ = MagicMock(return_value=None)
                    mock_executor.return_value = mock_pool

                    # as_completed yields futures in sequence
                    call_count = [0]

                    def as_completed_side_effect(futures):
                        call_count[0] += 1
                        if call_count[0] == 1 and futures:
                            yield mock_future1
                        elif call_count[0] == 2 and futures:
                            yield mock_future2

                    mock_as_completed.side_effect = as_completed_side_effect

                    stop_flag = threading.Event()

                    orchestrator.execute_goal_tasks(
                        goal_id="goal_1",
                        goal_manager=goal_manager,
                        execute_task_fn=MagicMock(),
                        router=MagicMock(),
                        learning_system=MagicMock(),
                        overnight_results=[],
                        save_overnight_results=MagicMock(),
                        stop_flag=stop_flag,
                        budget_remaining=10.0,
                        memory=None,
                    )

                    # Verify context was passed to second task submission
                    # The context should include the first task's analysis
                    submit_calls = mock_pool.submit.call_args_list
                    # First task submit has empty context
                    assert submit_calls[0][1]["sibling_context"] == []
                    # Second task submit should have context from first task (with description prefix)
                    context_str = str(submit_calls[1][1]["sibling_context"])
                    assert "Task 1 analysis" in context_str
                    assert "First task" in context_str
