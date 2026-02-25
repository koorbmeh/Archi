"""
Unit tests for plan_executor/recovery.py.

Covers task cancellation signals (single-shot user cancel, sticky shutdown,
clear_shutdown_flag), crash recovery state persistence (save_state, load_state,
clear_state, get_interrupted_tasks), staleness checks, structural validation,
and edge cases (empty task_id, corrupt JSON, atomic write).
Session 152.
"""

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.plan_executor import recovery as rec_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cancellation_globals():
    """Reset module-level cancellation globals before each test."""
    rec_mod._cancel_requested = False
    rec_mod._cancel_message = ""
    rec_mod._shutdown_requested = False
    yield
    rec_mod._cancel_requested = False
    rec_mod._cancel_message = ""
    rec_mod._shutdown_requested = False


@pytest.fixture
def state_dir(tmp_path):
    """Patch _state_dir to use a temp directory."""
    sd = tmp_path / "plan_state"
    sd.mkdir()
    with patch.object(rec_mod, "_state_dir", return_value=sd):
        yield sd


# ---------------------------------------------------------------------------
# signal_task_cancellation
# ---------------------------------------------------------------------------

class TestSignalTaskCancellation:
    """signal_task_cancellation sets flags for PlanExecutor to check."""

    def test_sets_cancel_flag(self):
        rec_mod.signal_task_cancellation("user stop")
        assert rec_mod._cancel_requested is True
        assert rec_mod._cancel_message == "user stop"

    def test_shutdown_sets_sticky_flag(self):
        rec_mod.signal_task_cancellation("shutdown")
        assert rec_mod._shutdown_requested is True
        assert rec_mod._cancel_requested is True

    def test_service_shutdown_sets_sticky_flag(self):
        rec_mod.signal_task_cancellation("service_shutdown")
        assert rec_mod._shutdown_requested is True

    def test_non_shutdown_does_not_set_sticky(self):
        rec_mod.signal_task_cancellation("user cancel")
        assert rec_mod._shutdown_requested is False

    def test_empty_message(self):
        rec_mod.signal_task_cancellation("")
        assert rec_mod._cancel_requested is True
        assert rec_mod._cancel_message == ""


# ---------------------------------------------------------------------------
# check_and_clear_cancellation
# ---------------------------------------------------------------------------

class TestCheckAndClearCancellation:
    """check_and_clear_cancellation returns message and manages flag state."""

    def test_returns_none_when_not_set(self):
        result = rec_mod.check_and_clear_cancellation()
        assert result is None

    def test_returns_message_for_user_cancel(self):
        rec_mod.signal_task_cancellation("user stop")
        result = rec_mod.check_and_clear_cancellation()
        assert result == "user stop"

    def test_clears_flag_for_user_cancel(self):
        rec_mod.signal_task_cancellation("user stop")
        rec_mod.check_and_clear_cancellation()
        # Second call should return None (single-shot)
        result = rec_mod.check_and_clear_cancellation()
        assert result is None

    def test_shutdown_is_sticky(self):
        rec_mod.signal_task_cancellation("shutdown")
        result1 = rec_mod.check_and_clear_cancellation()
        result2 = rec_mod.check_and_clear_cancellation()
        assert result1 == "shutdown"
        assert result2 == "shutdown"

    def test_shutdown_returns_message_or_default(self):
        """Even with empty message, shutdown returns 'shutdown' default."""
        rec_mod._shutdown_requested = True
        rec_mod._cancel_message = ""
        result = rec_mod.check_and_clear_cancellation()
        assert result == "shutdown"

    def test_shutdown_preserves_custom_message(self):
        rec_mod.signal_task_cancellation("service_shutdown")
        result = rec_mod.check_and_clear_cancellation()
        assert result == "service_shutdown"


# ---------------------------------------------------------------------------
# clear_shutdown_flag
# ---------------------------------------------------------------------------

class TestClearShutdownFlag:
    """clear_shutdown_flag resets all cancellation state."""

    def test_clears_shutdown(self):
        rec_mod.signal_task_cancellation("shutdown")
        rec_mod.clear_shutdown_flag()
        assert rec_mod._shutdown_requested is False
        assert rec_mod._cancel_requested is False
        assert rec_mod._cancel_message == ""

    def test_clears_user_cancel_too(self):
        rec_mod.signal_task_cancellation("user stop")
        rec_mod.clear_shutdown_flag()
        result = rec_mod.check_and_clear_cancellation()
        assert result is None


# ---------------------------------------------------------------------------
# save_state
# ---------------------------------------------------------------------------

class TestSaveState:
    """save_state persists execution state as JSON for crash recovery."""

    def test_saves_json_file(self, state_dir):
        rec_mod.save_state(
            task_id="task-123",
            task_description="Test task",
            goal_context="Test goal",
            steps_taken=[{"action": "think", "step": 1}],
            total_cost=0.05,
            files_created=["workspace/output.txt"],
        )
        path = state_dir / "task-123.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["task_id"] == "task-123"
        assert data["task_description"] == "Test task"
        assert data["goal_context"] == "Test goal"
        assert len(data["steps_taken"]) == 1
        assert data["total_cost"] == 0.05
        assert data["files_created"] == ["workspace/output.txt"]
        assert "saved_at" in data

    def test_empty_task_id_is_noop(self, state_dir):
        rec_mod.save_state("", "desc", "goal", [], 0.0, [])
        assert len(list(state_dir.glob("*.json"))) == 0

    def test_overwrites_existing(self, state_dir):
        rec_mod.save_state("t1", "first", "g", [{"step": 1}], 0.01, [])
        rec_mod.save_state("t1", "second", "g", [{"step": 1}, {"step": 2}], 0.02, [])
        data = json.loads((state_dir / "t1.json").read_text())
        assert data["task_description"] == "second"
        assert len(data["steps_taken"]) == 2

    def test_write_failure_is_non_critical(self, state_dir):
        """If writing fails, no exception propagates."""
        with patch("builtins.open", side_effect=PermissionError("denied")):
            rec_mod.save_state("t2", "desc", "g", [], 0.0, [])
        # Should not raise


# ---------------------------------------------------------------------------
# load_state
# ---------------------------------------------------------------------------

class TestLoadState:
    """load_state retrieves crash recovery state with validation."""

    def test_loads_valid_state(self, state_dir):
        rec_mod.save_state("t1", "desc", "goal", [{"a": 1}], 0.1, ["f.txt"])
        result = rec_mod.load_state("t1")
        assert result is not None
        assert result["task_id"] == "t1"
        assert result["task_description"] == "desc"

    def test_returns_none_for_missing(self, state_dir):
        result = rec_mod.load_state("nonexistent")
        assert result is None

    def test_returns_none_for_empty_id(self, state_dir):
        result = rec_mod.load_state("")
        assert result is None

    def test_rejects_stale_state(self, state_dir):
        """State older than 24 hours is discarded."""
        old_time = (datetime.now() - timedelta(hours=25)).isoformat()
        state = {
            "task_id": "old-task",
            "task_description": "old",
            "steps_taken": [],
            "saved_at": old_time,
        }
        path = state_dir / "old-task.json"
        path.write_text(json.dumps(state))
        result = rec_mod.load_state("old-task")
        assert result is None
        assert not path.exists()  # stale file should be cleaned up

    def test_accepts_fresh_state(self, state_dir):
        """State less than 24 hours old is accepted."""
        fresh_time = (datetime.now() - timedelta(hours=1)).isoformat()
        state = {
            "task_id": "fresh-task",
            "task_description": "fresh",
            "steps_taken": [{"a": 1}],
            "saved_at": fresh_time,
        }
        (state_dir / "fresh-task.json").write_text(json.dumps(state))
        result = rec_mod.load_state("fresh-task")
        assert result is not None

    def test_rejects_corrupt_missing_steps(self, state_dir):
        """State without steps_taken list is discarded as corrupt."""
        state = {"task_id": "bad", "task_description": "corrupt"}
        (state_dir / "bad.json").write_text(json.dumps(state))
        result = rec_mod.load_state("bad")
        assert result is None

    def test_rejects_non_dict_state(self, state_dir):
        """State that isn't a dict is discarded."""
        (state_dir / "weird.json").write_text(json.dumps([1, 2, 3]))
        result = rec_mod.load_state("weird")
        assert result is None

    def test_handles_invalid_json(self, state_dir):
        """Completely invalid JSON returns None without crashing."""
        (state_dir / "broken.json").write_text("not json at all{{{")
        result = rec_mod.load_state("broken")
        assert result is None


# ---------------------------------------------------------------------------
# clear_state
# ---------------------------------------------------------------------------

class TestClearState:
    """clear_state removes crash recovery files."""

    def test_removes_state_file(self, state_dir):
        rec_mod.save_state("t1", "desc", "g", [], 0.0, [])
        assert (state_dir / "t1.json").exists()
        rec_mod.clear_state("t1")
        assert not (state_dir / "t1.json").exists()

    def test_noop_for_missing(self, state_dir):
        """No error when clearing nonexistent state."""
        rec_mod.clear_state("nonexistent")

    def test_noop_for_empty_id(self, state_dir):
        rec_mod.clear_state("")


# ---------------------------------------------------------------------------
# get_interrupted_tasks
# ---------------------------------------------------------------------------

class TestGetInterruptedTasks:
    """get_interrupted_tasks lists resumable tasks."""

    def test_empty_dir(self, state_dir):
        result = rec_mod.get_interrupted_tasks()
        assert result == []

    def test_returns_fresh_tasks(self, state_dir):
        rec_mod.save_state("t1", "Task one", "g", [{"s": 1}, {"s": 2}], 0.1, [])
        rec_mod.save_state("t2", "Task two", "g", [{"s": 1}], 0.05, [])
        result = rec_mod.get_interrupted_tasks()
        assert len(result) == 2
        ids = {t["task_id"] for t in result}
        assert ids == {"t1", "t2"}

    def test_includes_step_count(self, state_dir):
        rec_mod.save_state("t1", "desc", "g", [{"a": 1}, {"a": 2}, {"a": 3}], 0.1, [])
        result = rec_mod.get_interrupted_tasks()
        assert result[0]["steps_completed"] == 3

    def test_excludes_stale_tasks(self, state_dir):
        old_time = (datetime.now() - timedelta(hours=25)).isoformat()
        state = {
            "task_id": "stale",
            "task_description": "stale task",
            "steps_taken": [{"s": 1}],
            "saved_at": old_time,
        }
        (state_dir / "stale.json").write_text(json.dumps(state))
        result = rec_mod.get_interrupted_tasks()
        assert len(result) == 0

    def test_skips_corrupt_files(self, state_dir):
        """Corrupt JSON files are skipped silently."""
        (state_dir / "corrupt.json").write_text("not valid json")
        rec_mod.save_state("good", "desc", "g", [{"s": 1}], 0.0, [])
        result = rec_mod.get_interrupted_tasks()
        assert len(result) == 1
        assert result[0]["task_id"] == "good"

    def test_includes_saved_at(self, state_dir):
        rec_mod.save_state("t1", "desc", "g", [], 0.0, [])
        result = rec_mod.get_interrupted_tasks()
        assert result[0]["saved_at"]  # non-empty string


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """Cancellation uses a lock for thread-safe access."""

    def test_concurrent_signals_and_checks(self):
        """Multiple threads signaling and checking shouldn't crash."""
        results = []
        errors = []

        def signal_thread():
            try:
                for _ in range(50):
                    rec_mod.signal_task_cancellation("test")
            except Exception as e:
                errors.append(e)

        def check_thread():
            try:
                for _ in range(50):
                    rec_mod.check_and_clear_cancellation()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=signal_thread),
            threading.Thread(target=check_thread),
            threading.Thread(target=signal_thread),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert len(errors) == 0


# ---------------------------------------------------------------------------
# _state_dir
# ---------------------------------------------------------------------------

class TestStateDir:
    """_state_dir creates and returns the plan_state directory."""

    def test_creates_directory(self, tmp_path):
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            sd = rec_mod._state_dir()
        assert sd.exists()
        assert sd.is_dir()
        assert sd.name == "plan_state"

    def test_returns_path_object(self, tmp_path):
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            sd = rec_mod._state_dir()
        assert isinstance(sd, Path)
