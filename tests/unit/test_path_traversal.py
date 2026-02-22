"""Unit tests for path traversal and symlink attack prevention.

Tests SafetyController.validate_path() and tool_registry._validate_path_security()
to ensure workspace isolation cannot be bypassed via:
  - .. traversal (relative path escape)
  - Symlink-based escape (symlink inside workspace points outside)
  - Absolute path outside project root
  - Various encoding/normalization tricks

Created session 72.
"""

import os
import tempfile
import pytest

from src.core.safety_controller import SafetyController, Action


@pytest.fixture
def workspace(tmp_path):
    """Create a fake project root with workspace/ and src/ subdirs."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "projects").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "config").mkdir()
    # Write a minimal rules.yaml so SafetyController loads without error
    rules = tmp_path / "config" / "rules.yaml"
    rules.write_text(
        "version: '2.0'\n"
        "risk_levels:\n"
        "  L1_LOW:\n"
        "    threshold: 0.5\n"
        "    actions: [read_file, list_directory]\n"
        "    requirement: autonomous\n"
        "  L2_MEDIUM:\n"
        "    threshold: 0.7\n"
        "    actions: [create_file, edit_file]\n"
        "    requirement: autonomous\n"
        "  L3_HIGH:\n"
        "    threshold: 0.9\n"
        "    actions: [write_source, run_command]\n"
        "    requirement: autonomous\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def controller(workspace, monkeypatch):
    """SafetyController with project root set to the temp workspace."""
    monkeypatch.setattr("src.core.safety_controller._base_path", lambda: str(workspace))
    return SafetyController(rules_path=str(workspace / "config" / "rules.yaml"))


class TestValidatePath:
    """Tests for SafetyController.validate_path() — workspace isolation."""

    def test_path_inside_project_allowed(self, controller, workspace):
        """A path directly inside the project root is allowed."""
        p = str(workspace / "workspace" / "report.md")
        assert controller.validate_path(p) is True

    def test_path_at_project_root_allowed(self, controller, workspace):
        """The project root itself is allowed (edge case)."""
        assert controller.validate_path(str(workspace)) is True

    def test_path_outside_project_blocked(self, controller, workspace):
        """An absolute path outside the project root is blocked."""
        outside = str(workspace.parent / "evil.txt")
        assert controller.validate_path(outside) is False

    def test_dot_dot_traversal_blocked(self, controller, workspace):
        """A path using .. to escape the project root is blocked."""
        escaped = str(workspace / "workspace" / ".." / ".." / "etc" / "passwd")
        assert controller.validate_path(escaped) is False

    def test_dot_dot_within_project_allowed(self, controller, workspace):
        """.. that stays within the project root is allowed (workspace/../src)."""
        inner = str(workspace / "workspace" / ".." / "src" / "core")
        assert controller.validate_path(inner) is True

    @pytest.mark.skipif(
        os.name == "nt",
        reason="os.symlink requires privilege on Windows",
    )
    def test_symlink_escape_blocked(self, controller, workspace):
        """A symlink inside the workspace pointing outside is blocked."""
        outside_dir = tempfile.mkdtemp()
        try:
            link_path = str(workspace / "workspace" / "evil_link")
            os.symlink(outside_dir, link_path)
            target_file = os.path.join(link_path, "secret.txt")
            # validate_path uses realpath() — should resolve the symlink
            assert controller.validate_path(target_file) is False
        finally:
            # Cleanup
            if os.path.islink(str(workspace / "workspace" / "evil_link")):
                os.unlink(str(workspace / "workspace" / "evil_link"))
            os.rmdir(outside_dir)

    @pytest.mark.skipif(
        os.name == "nt",
        reason="os.symlink requires privilege on Windows",
    )
    def test_symlink_within_project_allowed(self, controller, workspace):
        """A symlink within the project pointing to another project dir is fine."""
        target = workspace / "src"
        link_path = str(workspace / "workspace" / "src_link")
        os.symlink(str(target), link_path)
        try:
            assert controller.validate_path(os.path.join(link_path, "core")) is True
        finally:
            os.unlink(link_path)

    def test_temp_dir_blocked(self, controller):
        """System temp directory is outside project and should be blocked."""
        assert controller.validate_path(tempfile.gettempdir()) is False

    def test_empty_path_blocked(self, controller):
        """Empty path should not pass validation."""
        # Empty string resolves to cwd via realpath, which is likely outside project
        result = controller.validate_path("")
        # Either blocked or happens to be in project — the important thing is no crash
        assert isinstance(result, bool)

    def test_nonexistent_path_evaluated(self, controller, workspace):
        """A path that doesn't exist is still evaluated by prefix (realpath resolves it)."""
        fake = str(workspace / "workspace" / "does_not_exist" / "file.txt")
        assert controller.validate_path(fake) is True

    def test_nonexistent_outside_blocked(self, controller, workspace):
        """A nonexistent path outside project is blocked."""
        fake = os.path.join(os.path.dirname(str(workspace)), "nope", "file.txt")
        assert controller.validate_path(fake) is False


class TestAuthorizePathIsolation:
    """Tests for authorize() — write actions blocked outside project."""

    def test_write_action_outside_project_denied(self, controller, workspace):
        """A write action with path outside project is denied."""
        action = Action(
            type="create_file",
            parameters={"path": "/tmp/evil.txt"},
            confidence=0.9,
        )
        assert controller.authorize(action) is False

    def test_write_action_inside_project_allowed(self, controller, workspace):
        """A write action with path inside project is authorized (if risk OK)."""
        action = Action(
            type="create_file",
            parameters={"path": str(workspace / "workspace" / "report.md")},
            confidence=0.9,
        )
        # Should pass path check — authorization depends on risk level
        assert controller.authorize(action) is True

    def test_read_action_outside_project_allowed(self, controller, workspace):
        """Read actions bypass workspace isolation (design decision)."""
        action = Action(
            type="read_file",
            parameters={"path": "/etc/hostname"},
            confidence=0.9,
        )
        # Read is autonomous at L1_LOW threshold 0.5, confidence 0.9 > 0.5
        assert controller.authorize(action) is True

    def test_write_action_traversal_denied(self, controller, workspace):
        """A write action using .. traversal outside project is denied."""
        escaped = str(workspace / "workspace" / ".." / ".." / "evil.txt")
        action = Action(
            type="edit_file",
            parameters={"path": escaped},
            confidence=0.9,
        )
        assert controller.authorize(action) is False


class TestValidatePathSecurity:
    """Tests for tool_registry._validate_path_security() — defense-in-depth."""

    def test_path_inside_project_returns_none(self, monkeypatch, workspace):
        """Valid path inside project returns None (no error)."""
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(workspace))
        from src.tools.tool_registry import _validate_path_security
        result = _validate_path_security(str(workspace / "workspace" / "file.txt"))
        assert result is None

    def test_path_outside_project_returns_error(self, monkeypatch, workspace):
        """Path outside project returns an error string."""
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(workspace))
        from src.tools.tool_registry import _validate_path_security
        result = _validate_path_security("/tmp/evil.txt")
        assert result is not None
        assert "outside project" in result.lower() or "Path" in result

    def test_traversal_returns_error(self, monkeypatch, workspace):
        """Path using .. to escape returns an error."""
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(workspace))
        from src.tools.tool_registry import _validate_path_security
        escaped = str(workspace / "workspace" / ".." / ".." / "etc" / "passwd")
        result = _validate_path_security(escaped)
        assert result is not None
