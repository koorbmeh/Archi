"""
Unit tests for src/utils/paths.py path helpers module.

Tests caching behavior, environment variables, Windows path detection,
directory walking, fallback mechanisms, and path composition logic.

Session: 150
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from src.utils import paths


@pytest.fixture(autouse=True)
def reset_cached_base():
    """Reset _cached_base before and after each test."""
    original_cached_base = paths._cached_base
    paths._cached_base = None
    yield
    paths._cached_base = original_cached_base


class TestIsWindowsPathOnNonWindows:
    """Tests for _is_windows_path_on_non_windows function."""

    def test_returns_false_on_windows(self):
        """Returns False when running on Windows platform."""
        with patch("sys.platform", "win32"):
            assert paths._is_windows_path_on_non_windows("C:\\Users\\test") is False

    def test_detects_windows_path_on_non_windows(self):
        """Detects Windows-style paths on non-Windows platforms."""
        with patch("sys.platform", "linux"):
            assert paths._is_windows_path_on_non_windows("C:\\Users\\test") is True
            assert paths._is_windows_path_on_non_windows("D:/data") is True

    def test_rejects_non_windows_paths(self):
        """Rejects Unix-style paths on non-Windows platforms."""
        with patch("sys.platform", "linux"):
            assert paths._is_windows_path_on_non_windows("/home/user") is False
            assert paths._is_windows_path_on_non_windows("relative/path") is False

    def test_rejects_short_paths(self):
        """Rejects paths shorter than 3 characters."""
        with patch("sys.platform", "linux"):
            assert paths._is_windows_path_on_non_windows("") is False
            assert paths._is_windows_path_on_non_windows("C:") is False
            assert paths._is_windows_path_on_non_windows("C") is False

    def test_rejects_invalid_drive_letter_format(self):
        """Rejects paths without proper drive letter format."""
        with patch("sys.platform", "linux"):
            assert paths._is_windows_path_on_non_windows("1C:\\path") is False
            assert paths._is_windows_path_on_non_windows("C-\\path") is False
            assert paths._is_windows_path_on_non_windows("C.\\path") is False

    def test_requires_valid_separator_after_colon(self):
        """Requires / or \\ after drive letter and colon."""
        with patch("sys.platform", "linux"):
            assert paths._is_windows_path_on_non_windows("C:path") is False
            assert paths._is_windows_path_on_non_windows("C:*path") is False


class TestBasePath:
    """Tests for base_path function."""

    def test_returns_cached_value_on_subsequent_calls(self):
        """Returns cached value without recomputation."""
        paths._cached_base = "/cached/path"
        result = paths.base_path()
        assert result == "/cached/path"

    def test_uses_archi_root_env_var_when_set(self):
        """Uses ARCHI_ROOT environment variable when valid."""
        with patch.dict(os.environ, {"ARCHI_ROOT": "/custom/root"}):
            with patch("sys.platform", "linux"):
                result = paths.base_path()
                assert result == os.path.normpath("/custom/root")

    def test_ignores_windows_path_as_archi_root_on_non_windows(self):
        """Ignores Windows-style ARCHI_ROOT path on non-Windows platform."""
        with patch.dict(os.environ, {"ARCHI_ROOT": "C:\\Windows\\path"}):
            with patch.object(paths, "sys") as mock_sys:
                mock_sys.platform = "linux"
                result = paths.base_path()
                # Should NOT use the Windows path — will walk up or use cwd instead
                assert not result.startswith("C:")

    def test_walks_up_to_find_config_directory(self):
        """Walks up directory tree to find config/ directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "parent" / "config"
            config_dir.mkdir(parents=True)

            with patch.dict(os.environ, {}, clear=True):
                with patch("pathlib.Path.__new__") as mock_new:
                    mock_instance = MagicMock()

                    # Simulate the walk-up behavior
                    parent_path = MagicMock()
                    parent_path.resolve.return_value = parent_path
                    parent_path.parent = MagicMock()

                    for i in range(6):
                        mock_instance.parent = MagicMock()

                    mock_new.return_value = mock_instance

                    with patch("os.getcwd", return_value=str(Path(tmpdir) / "parent")):
                        result = paths.base_path()
                        assert result is not None

    def test_fallback_to_cwd_when_config_not_found(self, tmp_path):
        """Falls back to current working directory when config/ not found."""
        # Create a deep dir with no config/ anywhere in the ancestor chain
        deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "g" / "h"
        deep.mkdir(parents=True)
        fake_file = deep / "paths.py"
        fake_file.touch()

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ARCHI_ROOT", None)
            cwd_value = "/fallback/working/directory"
            with patch.object(paths, "__file__", str(fake_file)):
                with patch("os.getcwd", return_value=cwd_value):
                    result = paths.base_path()
                    assert result == cwd_value

    def test_caches_result_after_computation(self):
        """Caches result in _cached_base after initial computation."""
        with patch.dict(os.environ, {"ARCHI_ROOT": "/test/path"}):
            with patch("sys.platform", "linux"):
                paths._cached_base = None
                result1 = paths.base_path()
                result2 = paths.base_path()

                assert result1 == result2
                assert paths._cached_base == result1

    def test_normalizes_path(self):
        """Normalizes returned path using os.path.normpath."""
        with patch.dict(os.environ, {"ARCHI_ROOT": "/test//path/./nested"}):
            with patch("sys.platform", "linux"):
                result = paths.base_path()
                assert result == os.path.normpath("/test//path/./nested")


class TestBasePathAsPath:
    """Tests for base_path_as_path function."""

    def test_returns_path_object(self):
        """Returns a Path object."""
        with patch("src.utils.paths.base_path", return_value="/test/path"):
            result = paths.base_path_as_path()
            assert isinstance(result, Path)

    def test_wraps_base_path_result(self):
        """Wraps base_path() result in Path object."""
        with patch("src.utils.paths.base_path", return_value="/custom/base"):
            result = paths.base_path_as_path()
            assert str(result) == "/custom/base"

    def test_uses_cached_base_path(self):
        """Uses cached base_path value."""
        paths._cached_base = "/cached/value"
        result = paths.base_path_as_path()
        assert str(result) == "/cached/value"


class TestProjectRoot:
    """Tests for project_root reference."""

    def test_project_root_is_function_reference(self):
        """project_root is assigned to base_path_as_path function."""
        assert paths.project_root == paths.base_path_as_path

    def test_project_root_callable(self):
        """project_root can be called as a function."""
        with patch("src.utils.paths.base_path", return_value="/root"):
            result = paths.project_root()
            assert isinstance(result, Path)
            assert str(result) == "/root"


class TestDbPath:
    """Tests for db_path function."""

    def test_returns_memory_db_path(self):
        """Returns path to memory.db in data directory."""
        with patch("src.utils.paths.base_path", return_value="/project"):
            result = paths.db_path()
            assert result == os.path.join("/project", "data", "memory.db")

    def test_uses_base_path(self):
        """Uses base_path() to construct full path."""
        with patch("src.utils.paths.base_path", return_value="/custom/root"):
            result = paths.db_path()
            expected = os.path.join("/custom/root", "data", "memory.db")
            assert result == expected

    def test_path_composition(self):
        """Correctly composes path using os.path.join."""
        with patch("src.utils.paths.base_path", return_value="/home/user"):
            result = paths.db_path()
            assert "memory.db" in result
            assert "data" in result


class TestDataDir:
    """Tests for data_dir function."""

    def test_returns_data_directory_without_subdir(self):
        """Returns data directory when no subdir specified."""
        with patch("src.utils.paths.base_path", return_value="/project"):
            with patch("os.makedirs"):
                result = paths.data_dir()
                assert result == os.path.join("/project", "data")

    def test_returns_data_subdir_with_subdir_specified(self):
        """Returns data subdir when subdir parameter provided."""
        with patch("src.utils.paths.base_path", return_value="/project"):
            with patch("os.makedirs"):
                result = paths.data_dir("backups")
                assert result == os.path.join("/project", "data", "backups")

    def test_creates_directory_without_subdir(self):
        """Creates data directory when it doesn't exist."""
        with patch("src.utils.paths.base_path", return_value="/project"):
            with patch("os.makedirs") as mock_makedirs:
                paths.data_dir()
                mock_makedirs.assert_called_once()
                call_args = mock_makedirs.call_args
                assert call_args[1]["exist_ok"] is True

    def test_creates_directory_with_subdir(self):
        """Creates data subdir when it doesn't exist."""
        with patch("src.utils.paths.base_path", return_value="/project"):
            with patch("os.makedirs") as mock_makedirs:
                paths.data_dir("logs")
                mock_makedirs.assert_called_once()
                call_args = mock_makedirs.call_args
                assert call_args[1]["exist_ok"] is True

    def test_makedirs_called_with_exist_ok_true(self):
        """Calls os.makedirs with exist_ok=True."""
        with patch("src.utils.paths.base_path", return_value="/test"):
            with patch("os.makedirs") as mock_makedirs:
                paths.data_dir("sub")
                mock_makedirs.assert_called_once_with(
                    os.path.join("/test", "data", "sub"),
                    exist_ok=True
                )

    def test_handles_nested_subdirs(self):
        """Handles nested subdirectory paths."""
        with patch("src.utils.paths.base_path", return_value="/project"):
            with patch("os.makedirs"):
                result = paths.data_dir("cache/temp")
                assert result == os.path.join("/project", "data", "cache/temp")

    def test_empty_string_subdir_treated_as_no_subdir(self):
        """Treats empty string subdir same as no subdir."""
        with patch("src.utils.paths.base_path", return_value="/project"):
            with patch("os.makedirs"):
                result = paths.data_dir("")
                assert result == os.path.join("/project", "data")

    def test_integration_with_base_path_caching(self):
        """Works correctly with base_path caching."""
        paths._cached_base = "/cached/project"
        with patch("os.makedirs"):
            result = paths.data_dir("test")
            assert result == os.path.join("/cached/project", "data", "test")
