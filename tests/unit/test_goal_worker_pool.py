"""Unit tests for GoalWorkerPool — budget enforcement and cancellation.

Tests:
  - Per-goal budget loading from rules.yaml
  - Per-goal cancellation via stop flags
  - Pool shutdown sets all stop flags
  - Submit rejection when pool is stopped
  - Worker state management

Created session 72.
"""

import threading
import pytest
from unittest.mock import MagicMock, patch

from src.core.goal_worker_pool import GoalWorkerPool, _get_per_goal_budget, _get_max_workers


@pytest.fixture
def rules_yaml(tmp_path):
    """Create a rules.yaml with known budget values."""
    rules = tmp_path / "config" / "rules.yaml"
    rules.parent.mkdir(parents=True)
    rules.write_text(
        "worker_pool:\n"
        "  max_workers: 3\n"
        "  per_goal_budget: 0.75\n",
        encoding="utf-8",
    )
    return tmp_path


def _safe_shutdown(pool):
    """Shutdown a pool, ignoring errors (for test cleanup)."""
    try:
        pool._stop.set()
        with pool._goal_flags_lock:
            for flag in pool._goal_stop_flags.values():
                flag.set()
        try:
            from src.core.plan_executor import signal_task_cancellation
            signal_task_cancellation("shutdown")
        except ImportError:
            pass
        pool._executor.shutdown(wait=False, cancel_futures=True)
        pool._reactive_executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


class TestBudgetLoading:
    """Tests for per-goal budget configuration."""

    def test_budget_from_rules_yaml(self, rules_yaml, monkeypatch):
        """Per-goal budget is loaded from rules.yaml."""
        monkeypatch.setattr(
            "src.utils.paths.base_path_as_path", lambda: rules_yaml,
        )
        budget = _get_per_goal_budget()
        assert budget == 0.75

    def test_budget_default_on_missing_yaml(self, tmp_path, monkeypatch):
        """Missing rules.yaml → default budget ($1.00)."""
        monkeypatch.setattr(
            "src.utils.paths.base_path_as_path", lambda: tmp_path,
        )
        budget = _get_per_goal_budget()
        assert budget == 1.00

    def test_max_workers_capped_at_4(self, tmp_path, monkeypatch):
        """max_workers has a hard cap of 4 even if config says higher."""
        rules = tmp_path / "config" / "rules.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "worker_pool:\n"
            "  max_workers: 10\n"
            "  per_goal_budget: 1.00\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.utils.paths.base_path_as_path", lambda: tmp_path,
        )
        workers = _get_max_workers()
        assert workers <= 4

    def test_max_workers_default(self, tmp_path, monkeypatch):
        """Missing config → default 2 workers."""
        monkeypatch.setattr(
            "src.utils.paths.base_path_as_path", lambda: tmp_path,
        )
        workers = _get_max_workers()
        assert workers == 2


class TestCancellation:
    """Tests for per-goal cancellation and pool shutdown."""

    @pytest.fixture
    def pool(self):
        """Create a GoalWorkerPool with mocked dependencies."""
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_submit_rejected_when_stopped(self, pool):
        """Submitting a goal after stop flag is set gets rejected."""
        pool._stop.set()
        assert pool.submit_goal("goal_1") is False

    def test_double_submit_rejected(self, pool):
        """Submitting the same goal twice is rejected (already in _submitted)."""
        # Manually add to submitted set to simulate first submit
        with pool._submitted_lock:
            pool._submitted.add("goal_1")
        assert pool.submit_goal("goal_1") is False

    def test_cancel_goal_sets_flag_when_running(self, pool):
        """cancel_goal() sets the per-goal stop flag when goal is submitted and running."""
        # Simulate a running goal: add to _submitted and create a stop flag
        flag = threading.Event()
        with pool._submitted_lock:
            pool._submitted.add("goal_1")
        with pool._goal_flags_lock:
            pool._goal_stop_flags["goal_1"] = flag
        result = pool.cancel_goal("goal_1")
        assert result is True
        assert flag.is_set()

    def test_cancel_nonexistent_goal_returns_false(self, pool):
        """Cancelling a goal that doesn't exist returns False."""
        result = pool.cancel_goal("nonexistent_goal")
        assert result is False

    def test_shutdown_sets_global_stop(self, pool):
        """shutdown() sets the global stop flag."""
        pool.shutdown()
        assert pool._stop.is_set()

    def test_shutdown_sets_all_goal_flags(self, pool):
        """shutdown() sets all per-goal stop flags."""
        flag1 = threading.Event()
        flag2 = threading.Event()
        with pool._goal_flags_lock:
            pool._goal_stop_flags["g1"] = flag1
            pool._goal_stop_flags["g2"] = flag2
        pool.shutdown()
        assert flag1.is_set()
        assert flag2.is_set()

    def test_is_working_false_initially(self, pool):
        """Pool reports not working when no goals are submitted."""
        assert pool.is_working() is False

    def test_is_working_true_when_submitted(self, pool):
        """Pool reports working when goals are in _submitted."""
        with pool._submitted_lock:
            pool._submitted.add("goal_x")
        assert pool.is_working() is True


class TestWorkerState:
    """Tests for worker state tracking."""

    @pytest.fixture
    def pool(self):
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_get_status_returns_valid_dict(self, pool):
        """get_status() returns valid dict even with no goals."""
        status = pool.get_status()
        assert "max_workers" in status
        assert "submitted_goals" in status
        assert isinstance(status["submitted_goals"], list)
        assert "per_goal_budget" in status
        assert "stopped" in status

    def test_pool_reports_max_workers(self, pool):
        """Status includes max_workers configuration."""
        status = pool.get_status()
        assert status["max_workers"] >= 1

    def test_status_shows_stopped(self, pool):
        """Status reflects stopped state after shutdown."""
        assert pool.get_status()["stopped"] is False
        pool._stop.set()
        assert pool.get_status()["stopped"] is True
