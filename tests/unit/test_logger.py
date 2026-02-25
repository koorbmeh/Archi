"""Unit tests for src/core/logger.py — Structured JSONL action logging and error logging.

Tests cover:
- ActionLogger.__init__(): initialization with base_path, directory setup, error logger configuration
- ActionLogger._ensure_dirs(): creation of logs/actions and logs/errors directories
- ActionLogger._action_file(): file handle management, automatic rotation by date
- ActionLogger.log_action(): JSONL record writing, field handling, extra kwargs, None value removal
- ActionLogger.close(): file handle closure, error handler cleanup, idempotency
- Error handling: OSError conditions, write failures

All filesystem operations use tmp_path fixture for isolation.
Date-based rotation is tested using unittest.mock.patch on datetime.now.
Global root logger state is cleaned up after each test to prevent handler pollution.
"""

import json
import logging
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
from io import StringIO

import pytest

from src.core.logger import ActionLogger


# ─────────────────────────────────────────────────────────────────────────────
# Test Fixtures and Helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_logger_dir(tmp_path):
    """Provide a temporary directory for logger testing."""
    return tmp_path


def cleanup_root_logger_handlers():
    """Remove any FileHandler added by ActionLogger to prevent test pollution."""
    root_logger = logging.getLogger()
    handlers_to_remove = [h for h in root_logger.handlers if isinstance(h, logging.FileHandler)]
    for handler in handlers_to_remove:
        root_logger.removeHandler(handler)
        handler.close()


# ─────────────────────────────────────────────────────────────────────────────
# TestInit
# ─────────────────────────────────────────────────────────────────────────────


class TestInit:
    """Test ActionLogger.__init__(): initialization, directory setup, error logger."""

    def test_init_with_explicit_base_path(self, tmp_logger_dir):
        """Initialize ActionLogger with explicit base_path."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            assert logger.base_path == str(tmp_logger_dir)
            assert logger._logs_dir == os.path.join(str(tmp_logger_dir), "logs")
            assert logger._actions_dir == os.path.join(str(tmp_logger_dir), "logs", "actions")
            assert logger._errors_dir == os.path.join(str(tmp_logger_dir), "logs", "errors")
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_init_creates_directories(self, tmp_logger_dir):
        """Initialize ActionLogger creates logs/actions and logs/errors directories."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            assert os.path.isdir(logger._actions_dir)
            assert os.path.isdir(logger._errors_dir)
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_init_sets_up_error_handler(self, tmp_logger_dir):
        """Initialize ActionLogger sets up error handler on root logger."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            assert logger._error_handler is not None
            assert isinstance(logger._error_handler, logging.FileHandler)
            assert logger._error_handler in logging.getLogger().handlers
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_init_initializes_state_variables(self, tmp_logger_dir):
        """Initialize ActionLogger initializes state variables correctly."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            assert logger._current_date is None
            assert logger._current_action_file is None
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_init_error_on_directory_creation_failure(self, tmp_logger_dir):
        """Initialize ActionLogger raises OSError when directory creation fails."""
        invalid_path = "/invalid/nonexistent/path/that/should/fail"
        with pytest.raises(OSError):
            logger = ActionLogger(base_path=invalid_path)
            logger.close()
            cleanup_root_logger_handlers()


# ─────────────────────────────────────────────────────────────────────────────
# TestEnsureDirs
# ─────────────────────────────────────────────────────────────────────────────


class TestEnsureDirs:
    """Test ActionLogger._ensure_dirs(): directory creation and idempotency."""

    def test_ensure_dirs_creates_actions_directory(self, tmp_logger_dir):
        """_ensure_dirs() creates logs/actions directory."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            actions_dir = os.path.join(str(tmp_logger_dir), "logs", "actions")
            assert os.path.isdir(actions_dir)
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_ensure_dirs_creates_errors_directory(self, tmp_logger_dir):
        """_ensure_dirs() creates logs/errors directory."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            errors_dir = os.path.join(str(tmp_logger_dir), "logs", "errors")
            assert os.path.isdir(errors_dir)
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_ensure_dirs_idempotent(self, tmp_logger_dir):
        """_ensure_dirs() can be called multiple times without error."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            # Call _ensure_dirs() again
            logger._ensure_dirs()
            # Verify directories still exist and are accessible
            assert os.path.isdir(logger._actions_dir)
            assert os.path.isdir(logger._errors_dir)
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_ensure_dirs_raises_on_permission_error(self, tmp_logger_dir):
        """_ensure_dirs() raises OSError if directory creation fails."""
        # Create a read-only parent directory to trigger permission error
        if os.name != "nt":  # Skip on Windows
            readonly_parent = os.path.join(str(tmp_logger_dir), "readonly")
            os.makedirs(readonly_parent, exist_ok=True)
            os.chmod(readonly_parent, 0o444)  # Read-only
            try:
                with pytest.raises(OSError):
                    logger = ActionLogger(base_path=os.path.join(readonly_parent, "subdir"))
                    logger.close()
                    cleanup_root_logger_handlers()
            finally:
                os.chmod(readonly_parent, 0o755)  # Restore permissions for cleanup


# ─────────────────────────────────────────────────────────────────────────────
# TestActionFile
# ─────────────────────────────────────────────────────────────────────────────


class TestActionFile:
    """Test ActionLogger._action_file(): file handle management and date rotation."""

    def test_action_file_returns_open_file(self, tmp_logger_dir):
        """_action_file() returns an open file handle."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            f = logger._action_file()
            assert f is not None
            assert not f.closed
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_action_file_name_contains_date(self, tmp_logger_dir):
        """_action_file() creates file named with today's date (YYYY-MM-DD.jsonl)."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            f = logger._action_file()
            assert f.name.endswith(f"{today}.jsonl")
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_action_file_reuses_same_handle_for_same_date(self, tmp_logger_dir):
        """_action_file() returns the same file handle for the same date."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            f1 = logger._action_file()
            f2 = logger._action_file()
            assert f1 is f2
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_action_file_rotates_on_date_change(self, tmp_logger_dir):
        """_action_file() rotates to new file when date changes."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            # Get file for initial date
            with patch("src.core.logger.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 1, 1, tzinfo=timezone.utc)
                mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
                f1 = logger._action_file()
                f1_name = f1.name

            # Simulate date change
            with patch("src.core.logger.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 1, 2, tzinfo=timezone.utc)
                mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
                f2 = logger._action_file()
                f2_name = f2.name

            assert f1_name != f2_name
            assert "2025-01-01" in f1_name
            assert "2025-01-02" in f2_name
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_action_file_closes_previous_file_on_rotation(self, tmp_logger_dir):
        """_action_file() closes previous file when rotating to new date."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            with patch("src.core.logger.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 1, 1, tzinfo=timezone.utc)
                mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
                f1 = logger._action_file()

            with patch("src.core.logger.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 1, 2, tzinfo=timezone.utc)
                mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
                f2 = logger._action_file()

            # First file should be closed
            assert f1.closed
            # Second file should be open
            assert not f2.closed
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_action_file_handles_close_error(self, tmp_logger_dir):
        """_action_file() handles OSError when closing previous file gracefully."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            logger._current_action_file = MagicMock()
            logger._current_action_file.close.side_effect = OSError("close failed")

            with patch("src.core.logger.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 1, 2, tzinfo=timezone.utc)
                mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
                # Should not raise, should handle gracefully
                f = logger._action_file()
                assert f is not None
        finally:
            logger.close()
            cleanup_root_logger_handlers()


# ─────────────────────────────────────────────────────────────────────────────
# TestLogAction
# ─────────────────────────────────────────────────────────────────────────────


class TestLogAction:
    """Test ActionLogger.log_action(): JSONL record writing, field handling, and error cases."""

    def test_log_action_writes_jsonl_record(self, tmp_logger_dir):
        """log_action() writes a valid JSONL record to the action log file."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            logger.log_action(
                action_type="test_action",
                parameters={"key": "value"},
                model_used="gpt-4",
                confidence=0.95,
                cost_usd=0.50,
                result="success",
                duration_ms=100.5,
            )

            # Read the file and verify content
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            action_file = os.path.join(logger._actions_dir, f"{today}.jsonl")
            with open(action_file, "r") as f:
                line = f.readline()
                record = json.loads(line)

            assert record["action_type"] == "test_action"
            assert record["parameters"] == {"key": "value"}
            assert record["model_used"] == "gpt-4"
            assert record["confidence"] == 0.95
            assert record["cost_usd"] == 0.50
            assert record["result"] == "success"
            assert record["duration_ms"] == 100.5
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_log_action_includes_timestamp(self, tmp_logger_dir):
        """log_action() includes timestamp in ISO 8601 format with Z suffix."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            logger.log_action(action_type="test")

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            action_file = os.path.join(logger._actions_dir, f"{today}.jsonl")
            with open(action_file, "r") as f:
                record = json.loads(f.readline())

            assert "timestamp" in record
            # Check ISO 8601 format: YYYY-MM-DDTHH:MM:SS.fffZ
            assert record["timestamp"].endswith("Z")
            assert "T" in record["timestamp"]
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_log_action_applies_defaults_to_optional_fields(self, tmp_logger_dir):
        """log_action() applies default values to optional fields when not provided."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            logger.log_action(action_type="minimal_action")

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            action_file = os.path.join(logger._actions_dir, f"{today}.jsonl")
            with open(action_file, "r") as f:
                record = json.loads(f.readline())

            assert record["parameters"] == {}
            assert record["model_used"] == "local"
            assert record["confidence"] == 0.0
            assert record["cost_usd"] == 0.0
            assert record["result"] == "success"
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_log_action_removes_none_values(self, tmp_logger_dir):
        """log_action() removes None values from the record for cleaner JSON."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            logger.log_action(
                action_type="test",
                duration_ms=None,
                error=None,
                confidence=None,
            )

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            action_file = os.path.join(logger._actions_dir, f"{today}.jsonl")
            with open(action_file, "r") as f:
                record = json.loads(f.readline())

            # None values should be removed
            assert "duration_ms" not in record
            assert "error" not in record
            # confidence=None is replaced with default 0.0
            assert record["confidence"] == 0.0
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_log_action_handles_extra_kwargs(self, tmp_logger_dir):
        """log_action() includes extra keyword arguments in the record."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            logger.log_action(
                action_type="test",
                custom_field="custom_value",
                extra_data={"nested": "data"},
            )

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            action_file = os.path.join(logger._actions_dir, f"{today}.jsonl")
            with open(action_file, "r") as f:
                record = json.loads(f.readline())

            assert record["custom_field"] == "custom_value"
            assert record["extra_data"] == {"nested": "data"}
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_log_action_with_error_field(self, tmp_logger_dir):
        """log_action() includes error field when provided."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            logger.log_action(
                action_type="failed_action",
                result="failure",
                error="Something went wrong",
            )

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            action_file = os.path.join(logger._actions_dir, f"{today}.jsonl")
            with open(action_file, "r") as f:
                record = json.loads(f.readline())

            assert record["result"] == "failure"
            assert record["error"] == "Something went wrong"
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_log_action_handles_non_serializable_objects(self, tmp_logger_dir):
        """log_action() handles non-serializable objects using default=str."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            class CustomObject:
                def __str__(self):
                    return "custom_string_representation"

            logger.log_action(
                action_type="test",
                custom_obj=CustomObject(),
            )

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            action_file = os.path.join(logger._actions_dir, f"{today}.jsonl")
            with open(action_file, "r") as f:
                record = json.loads(f.readline())

            # Should be converted to string
            assert record["custom_obj"] == "custom_string_representation"
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_log_action_appends_to_existing_file(self, tmp_logger_dir):
        """log_action() appends to existing file, creating multiple JSONL lines."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            logger.log_action(action_type="action1")
            logger.log_action(action_type="action2")
            logger.log_action(action_type="action3")

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            action_file = os.path.join(logger._actions_dir, f"{today}.jsonl")
            with open(action_file, "r") as f:
                lines = f.readlines()

            assert len(lines) == 3
            records = [json.loads(line) for line in lines]
            assert records[0]["action_type"] == "action1"
            assert records[1]["action_type"] == "action2"
            assert records[2]["action_type"] == "action3"
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_log_action_flushes_file(self, tmp_logger_dir):
        """log_action() flushes the file to ensure data is written immediately."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            logger.log_action(action_type="test")

            # Without close/flush, file might not be written yet
            # But log_action should flush, so we can read it immediately
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            action_file = os.path.join(logger._actions_dir, f"{today}.jsonl")

            # Open the file separately and verify content is there
            with open(action_file, "r") as f:
                content = f.read()

            assert len(content) > 0
            assert "test" in content
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_log_action_handles_write_error(self, tmp_logger_dir):
        """log_action() handles OSError gracefully when write fails."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            # Mock _action_file to raise OSError
            logger._action_file = MagicMock(side_effect=OSError("write failed"))

            # Should not raise, should log error instead
            logger.log_action(action_type="test")
            # If we got here, error was handled gracefully
        finally:
            logger.close()
            cleanup_root_logger_handlers()


# ─────────────────────────────────────────────────────────────────────────────
# TestClose
# ─────────────────────────────────────────────────────────────────────────────


class TestClose:
    """Test ActionLogger.close(): file closure, handler cleanup, and idempotency."""

    def test_close_closes_action_file(self, tmp_logger_dir):
        """close() closes the open action file."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        logger.log_action(action_type="test")
        f = logger._current_action_file
        assert not f.closed
        logger.close()
        assert f.closed
        cleanup_root_logger_handlers()

    def test_close_removes_error_handler(self, tmp_logger_dir):
        """close() removes the error FileHandler from root logger."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        root_logger = logging.getLogger()
        handler = logger._error_handler
        assert handler in root_logger.handlers
        logger.close()
        assert handler not in root_logger.handlers
        cleanup_root_logger_handlers()

    def test_close_is_idempotent(self, tmp_logger_dir):
        """close() can be called multiple times without error."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        logger.log_action(action_type="test")

        # Call close multiple times
        logger.close()
        logger.close()
        logger.close()

        # Should complete without error
        cleanup_root_logger_handlers()

    def test_close_clears_state(self, tmp_logger_dir):
        """close() clears internal state variables."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        logger.log_action(action_type="test")

        assert logger._current_action_file is not None
        assert logger._current_date is not None

        logger.close()

        assert logger._current_action_file is None
        assert logger._current_date is None
        assert logger._error_handler is None

        cleanup_root_logger_handlers()

    def test_close_handles_file_close_error(self, tmp_logger_dir):
        """close() handles OSError gracefully when closing file."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        logger.log_action(action_type="test")

        # Mock the close method to raise OSError
        logger._current_action_file.close = MagicMock(side_effect=OSError("close failed"))

        # Should not raise
        logger.close()

        cleanup_root_logger_handlers()


# ─────────────────────────────────────────────────────────────────────────────
# TestIntegration
# ─────────────────────────────────────────────────────────────────────────────


class TestIntegration:
    """Integration tests for ActionLogger with multiple operations."""

    def test_full_workflow(self, tmp_logger_dir):
        """Test a complete workflow: init, log actions, rotate, close."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            # Log multiple actions
            logger.log_action(
                action_type="initialize",
                parameters={"mode": "test"},
                model_used="test_model",
                confidence=0.99,
                cost_usd=0.01,
                duration_ms=10.5,
            )

            logger.log_action(
                action_type="process",
                parameters={"data": "sample"},
                result="success",
                duration_ms=50.0,
            )

            # Simulate date change and log another action
            with patch("src.core.logger.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 1, 2, tzinfo=timezone.utc)
                mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

                logger.log_action(
                    action_type="next_day",
                    result="success",
                )

            # Verify files were created
            actions_dir = logger._actions_dir
            files = os.listdir(actions_dir)
            assert len(files) == 2

            # Verify each file has valid JSONL
            for filename in files:
                with open(os.path.join(actions_dir, filename), "r") as f:
                    for line in f:
                        record = json.loads(line)
                        assert "timestamp" in record
                        assert "action_type" in record
        finally:
            logger.close()
            cleanup_root_logger_handlers()

    def test_error_logging_integration(self, tmp_logger_dir):
        """Test that error handler is set up correctly and root logger receives handlers."""
        logger = ActionLogger(base_path=str(tmp_logger_dir))
        try:
            root_logger = logging.getLogger()
            initial_handler_count = len(root_logger.handlers)

            # Error handler should have been added
            assert logger._error_handler is not None

            # Log an action and verify error handler is still there
            logger.log_action(action_type="test")
            assert logger._error_handler in root_logger.handlers
        finally:
            logger.close()
            cleanup_root_logger_handlers()
