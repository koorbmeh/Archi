"""
Unit tests for plan_executor/safety.py.

Covers safety config loading (_load_safety_config, _get_safety), protection
checks (_check_protected, _requires_approval, _check_pre_approved), path
resolution (_strip_absolute_prefix, _resolve_workspace_path, _resolve_project_path,
_search_workspace), source code safety (_backup_file, _syntax_check), and
error classification (_classify_error).
Session 152.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.plan_executor import safety as safety_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_safety_cache():
    """Reset the cached safety config before each test."""
    safety_mod._safety_config_cache = None
    yield
    safety_mod._safety_config_cache = None


# ---------------------------------------------------------------------------
# _load_safety_config / _get_safety
# ---------------------------------------------------------------------------

class TestLoadSafetyConfig:
    """_load_safety_config loads from rules.yaml with defaults fallback."""

    def test_returns_dict_with_all_keys(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            config = safety_mod._load_safety_config()
        assert "protected_paths" in config
        assert "blocked_commands" in config
        assert "approval_required_paths" in config
        assert "allowed_commands" in config

    def test_falls_back_to_defaults_on_missing_yaml(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            config = safety_mod._load_safety_config()
        assert config["protected_paths"] == safety_mod._DEFAULT_PROTECTED_PATHS
        assert config["blocked_commands"] == safety_mod._DEFAULT_BLOCKED_COMMANDS
        assert config["allowed_commands"] == safety_mod._DEFAULT_ALLOWED_COMMANDS

    def test_caches_result(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            c1 = safety_mod._load_safety_config()
            c2 = safety_mod._load_safety_config()
        assert c1 is c2

    def test_loads_from_yaml(self, tmp_path):
        rules_yaml = tmp_path / "config" / "rules.yaml"
        rules_yaml.parent.mkdir(parents=True)
        rules_yaml.write_text(
            "protected_files:\n  - src/secret.py\n"
            "blocked_commands:\n  - rm -rf\n"
            "allowed_commands:\n  - git\n  - ls\n"
            "approval_required_paths:\n  - src/\n"
        )
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            config = safety_mod._load_safety_config()
        assert "src/secret.py" in config["protected_paths"]
        assert "rm -rf" in config["blocked_commands"]
        assert "git" in config["allowed_commands"]

    def test_partial_yaml_uses_defaults_for_missing_keys(self, tmp_path):
        rules_yaml = tmp_path / "config" / "rules.yaml"
        rules_yaml.parent.mkdir(parents=True)
        rules_yaml.write_text("protected_files:\n  - src/special.py\n")
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            config = safety_mod._load_safety_config()
        assert "src/special.py" in config["protected_paths"]
        # Other keys should keep defaults
        assert config["blocked_commands"] == safety_mod._DEFAULT_BLOCKED_COMMANDS
        assert config["allowed_commands"] == safety_mod._DEFAULT_ALLOWED_COMMANDS


class TestGetSafety:
    """_get_safety is a lazy accessor for safety config values."""

    def test_returns_protected_paths(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            result = safety_mod._get_safety("protected_paths")
        assert isinstance(result, frozenset)

    def test_returns_allowed_commands(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            result = safety_mod._get_safety("allowed_commands")
        assert "git" in result


# ---------------------------------------------------------------------------
# _check_protected
# ---------------------------------------------------------------------------

class TestCheckProtected:
    """_check_protected raises ValueError for protected files."""

    def test_protected_file_raises(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            with pytest.raises(ValueError, match="Protected file"):
                safety_mod._check_protected("src/core/plan_executor/executor.py")

    def test_non_protected_file_passes(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            safety_mod._check_protected("src/tools/web_search_tool.py")  # Should not raise

    def test_strips_leading_slash(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            with pytest.raises(ValueError, match="Protected file"):
                safety_mod._check_protected("/src/core/plan_executor/safety.py")

    def test_normalizes_backslashes(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            with pytest.raises(ValueError, match="Protected file"):
                safety_mod._check_protected("src\\core\\plan_executor\\recovery.py")


# ---------------------------------------------------------------------------
# _requires_approval
# ---------------------------------------------------------------------------

class TestRequiresApproval:
    """_requires_approval checks if a path needs user approval."""

    def test_src_path_requires_approval(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            assert safety_mod._requires_approval("src/tools/foo.py") is True

    def test_workspace_path_does_not(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            assert safety_mod._requires_approval("workspace/output.txt") is False

    def test_strips_leading_slash(self):
        with patch("src.utils.paths.base_path", return_value="/fake"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            assert safety_mod._requires_approval("/src/core/test.py") is True


# ---------------------------------------------------------------------------
# _check_pre_approved
# ---------------------------------------------------------------------------

class TestCheckPreApproved:
    """_check_pre_approved checks for and consumes pre-approval files."""

    def test_returns_true_and_removes_file(self, tmp_path):
        pa_dir = tmp_path / "data" / "pre_approved"
        pa_dir.mkdir(parents=True)
        pa_file = pa_dir / "src_tools_foo.py.txt"
        pa_file.write_text("approved")
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = safety_mod._check_pre_approved("src/tools/foo.py")
        assert result is True
        assert not pa_file.exists()

    def test_returns_false_when_no_file(self, tmp_path):
        pa_dir = tmp_path / "data" / "pre_approved"
        pa_dir.mkdir(parents=True)
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = safety_mod._check_pre_approved("src/tools/bar.py")
        assert result is False

    def test_handles_exception_gracefully(self):
        with patch("src.utils.paths.base_path", side_effect=RuntimeError("boom")):
            result = safety_mod._check_pre_approved("src/test.py")
        assert result is False


# ---------------------------------------------------------------------------
# _strip_absolute_prefix
# ---------------------------------------------------------------------------

class TestStripAbsolutePrefix:
    """_strip_absolute_prefix handles Windows paths and base_path echoes."""

    def test_strips_windows_drive(self):
        with patch("src.utils.paths.base_path", return_value="/project"):
            result = safety_mod._strip_absolute_prefix("C:/Users/Jesse/Archi/workspace/test.txt")
        assert not result.startswith("C:")
        assert not result.startswith("/")

    def test_strips_base_path_prefix(self):
        """When the LLM echoes the full project root, it gets stripped."""
        with patch("src.utils.paths.base_path", return_value="/home/user/Archi"):
            # Input without leading slash — simulates "home/user/Archi/workspace/test.txt"
            # which is what you get after Windows drive stripping or manual path joining
            result = safety_mod._strip_absolute_prefix("home/user/Archi/workspace/test.txt")
        assert result == "workspace/test.txt"

    def test_passthrough_relative_path(self):
        with patch("src.utils.paths.base_path", return_value="/project"):
            result = safety_mod._strip_absolute_prefix("workspace/output.txt")
        assert result == "workspace/output.txt"

    def test_normalizes_backslashes(self):
        with patch("src.utils.paths.base_path", return_value="/project"):
            result = safety_mod._strip_absolute_prefix("workspace\\projects\\test.txt")
        # Should have forward slashes after processing
        assert "\\" not in result or os.sep == "\\"


# ---------------------------------------------------------------------------
# _resolve_workspace_path
# ---------------------------------------------------------------------------

class TestResolveWorkspacePath:
    """_resolve_workspace_path enforces workspace boundary."""

    def test_adds_workspace_prefix(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = safety_mod._resolve_workspace_path("test.txt")
        assert "workspace" in result

    def test_keeps_existing_workspace_prefix(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = safety_mod._resolve_workspace_path("workspace/test.txt")
        # Should not double up workspace/workspace/ in the relative portion
        rel = os.path.relpath(result, str(tmp_path))
        assert not rel.startswith(os.path.join("workspace", "workspace"))

    def test_rejects_path_escape(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            with pytest.raises(ValueError, match="escapes workspace"):
                safety_mod._resolve_workspace_path("../../etc/passwd")

    def test_resolves_symlinks_for_boundary_check(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        # Create a symlink inside workspace pointing outside
        outside = tmp_path / "outside"
        outside.mkdir()
        link = ws / "escape_link"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported")
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            with pytest.raises(ValueError, match="escapes workspace"):
                safety_mod._resolve_workspace_path("workspace/escape_link/secret.txt")


# ---------------------------------------------------------------------------
# _resolve_project_path
# ---------------------------------------------------------------------------

class TestResolveProjectPath:
    """_resolve_project_path resolves paths within project root."""

    def test_resolves_src_path(self, tmp_path):
        src = tmp_path / "src" / "tools"
        src.mkdir(parents=True)
        target = src / "test.py"
        target.write_text("pass")
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = safety_mod._resolve_project_path("src/tools/test.py")
        assert result.endswith("test.py")

    def test_rejects_escape(self, tmp_path):
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            with pytest.raises(ValueError, match="escapes project root"):
                safety_mod._resolve_project_path("../../etc/passwd")

    def test_bare_filename_searched_in_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        target = ws / "report.txt"
        target.write_text("data")
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = safety_mod._resolve_project_path("report.txt")
        assert "workspace" in result


# ---------------------------------------------------------------------------
# _search_workspace
# ---------------------------------------------------------------------------

class TestSearchWorkspace:
    """_search_workspace finds files in workspace/ for bare filenames."""

    def test_finds_in_workspace_root(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "report.txt").write_text("data")
        result = safety_mod._search_workspace(str(tmp_path), "report.txt")
        assert result is not None
        assert "report.txt" in result

    def test_finds_in_workspace_projects(self, tmp_path):
        proj = tmp_path / "workspace" / "projects" / "myproject"
        proj.mkdir(parents=True)
        (proj / "output.md").write_text("data")
        result = safety_mod._search_workspace(str(tmp_path), "output.md")
        assert result is not None
        assert "output.md" in result

    def test_returns_none_when_not_found(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = safety_mod._search_workspace(str(tmp_path), "missing.txt")
        assert result is None

    def test_prefers_workspace_root_over_projects(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "test.txt").write_text("root")
        proj = ws / "projects" / "p1"
        proj.mkdir(parents=True)
        (proj / "test.txt").write_text("project")
        result = safety_mod._search_workspace(str(tmp_path), "test.txt")
        # Should find the workspace root version first
        assert "projects" not in result


# ---------------------------------------------------------------------------
# _backup_file
# ---------------------------------------------------------------------------

class TestBackupFile:
    """_backup_file creates timestamped backups before modifications."""

    def test_creates_backup(self, tmp_path):
        target = tmp_path / "src" / "test.py"
        target.parent.mkdir(parents=True)
        target.write_text("original")
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = safety_mod._backup_file(str(target))
        assert result is not None
        assert os.path.exists(result)
        with open(result) as f:
            assert f.read() == "original"

    def test_returns_none_for_missing_file(self, tmp_path):
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = safety_mod._backup_file(str(tmp_path / "nonexistent.py"))
        assert result is None

    def test_backup_in_source_backups_dir(self, tmp_path):
        target = tmp_path / "test.py"
        target.write_text("code")
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = safety_mod._backup_file(str(target))
        assert "source_backups" in result

    def test_handles_copy_failure(self, tmp_path):
        target = tmp_path / "test.py"
        target.write_text("code")
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)), \
             patch("shutil.copy2", side_effect=PermissionError("denied")):
            result = safety_mod._backup_file(str(target))
        assert result is None


# ---------------------------------------------------------------------------
# _syntax_check
# ---------------------------------------------------------------------------

class TestSyntaxCheck:
    """_syntax_check validates Python syntax."""

    def test_valid_python_returns_none(self, tmp_path):
        target = tmp_path / "good.py"
        target.write_text("x = 1\nprint(x)")
        result = safety_mod._syntax_check(str(target))
        assert result is None

    def test_invalid_python_returns_error(self, tmp_path):
        target = tmp_path / "bad.py"
        target.write_text("def foo(:\n    pass")
        result = safety_mod._syntax_check(str(target))
        assert result is not None
        assert "SyntaxError" in result or "syntax" in result.lower()

    def test_non_python_always_passes(self, tmp_path):
        target = tmp_path / "data.json"
        target.write_text("{invalid json")
        result = safety_mod._syntax_check(str(target))
        assert result is None

    def test_txt_file_passes(self, tmp_path):
        target = tmp_path / "readme.txt"
        target.write_text("not python at all {{{")
        result = safety_mod._syntax_check(str(target))
        assert result is None


# ---------------------------------------------------------------------------
# _classify_error
# ---------------------------------------------------------------------------

class TestClassifyError:
    """_classify_error categorizes errors for recovery routing."""

    def test_permanent_protected_file(self):
        cls, hint = safety_mod._classify_error("write_source", "Protected file cannot be modified")
        assert cls == "permanent"
        assert hint == ""

    def test_permanent_blocked_command(self):
        cls, _ = safety_mod._classify_error("run_command", "Command blocked for safety: rm -rf")
        assert cls == "permanent"

    def test_permanent_modification_denied(self):
        cls, _ = safety_mod._classify_error("edit_file", "Source modification denied by user")
        assert cls == "permanent"

    def test_permanent_no_approval_channel(self):
        cls, _ = safety_mod._classify_error("write_source", "No approval channel available")
        assert cls == "permanent"

    def test_permanent_already_denied(self):
        cls, _ = safety_mod._classify_error("write_source", "Source was already denied in this task")
        assert cls == "permanent"

    def test_transient_timeout(self):
        cls, _ = safety_mod._classify_error("web_search", "Connection timed out")
        assert cls == "transient"

    def test_transient_rate_limit(self):
        cls, _ = safety_mod._classify_error("web_search", "429 rate limit exceeded")
        assert cls == "transient"

    def test_transient_503(self):
        cls, _ = safety_mod._classify_error("fetch_webpage", "503 Service Unavailable")
        assert cls == "transient"

    def test_transient_connection_refused(self):
        cls, _ = safety_mod._classify_error("fetch_webpage", "Connection refused")
        assert cls == "transient"

    def test_transient_ssl_error(self):
        cls, _ = safety_mod._classify_error("fetch_webpage", "SSL certificate verify failed")
        assert cls == "transient"

    def test_mechanical_file_not_found(self):
        cls, hint = safety_mod._classify_error("read_file", "File not found: workspace/missing.txt")
        assert cls == "mechanical"
        assert "list_files" in hint

    def test_mechanical_syntax_error(self):
        cls, hint = safety_mod._classify_error("write_source", "Syntax error after edit (rolled back)")
        assert cls == "mechanical"
        assert "syntax" in hint.lower()

    def test_mechanical_find_not_found(self):
        """Edit file 'find' not-found error is mechanical — but hits 'not found' branch first."""
        cls, hint = safety_mod._classify_error("edit_file", "'find' string not found in test.py")
        assert cls == "mechanical"
        # The "not found" pattern matches before the "find"+"not found in" pattern,
        # so we get the file-not-found hint (list_files suggestion)
        assert "list_files" in hint

    def test_mechanical_not_a_directory(self):
        cls, hint = safety_mod._classify_error("list_files", "Not a directory: test.py")
        assert cls == "mechanical"
        assert "parent directory" in hint

    def test_mechanical_path_escapes(self):
        cls, hint = safety_mod._classify_error("create_file", "Path escapes workspace: ../secret")
        assert cls == "mechanical"
        assert "boundaries" in hint

    def test_mechanical_empty_field(self):
        cls, hint = safety_mod._classify_error("web_search", "Empty search query")
        assert cls == "mechanical"
        assert "empty" in hint.lower()

    def test_mechanical_unknown_error(self):
        """Unknown errors are classified as mechanical with empty hint."""
        cls, hint = safety_mod._classify_error("run_python", "Something weird happened")
        assert cls == "mechanical"
        assert hint == ""
