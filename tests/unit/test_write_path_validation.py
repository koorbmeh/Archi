"""Unit tests for write path validation — workspace/data boundary enforcement.

Tests tool_registry._validate_write_path() to ensure:
  - Writes to workspace/ and data/ are allowed
  - Writes to src/, config/, scripts/, etc. are blocked
  - .. traversal out of workspace is blocked
  - Symlink escape through write paths is blocked

Created session 72.
"""

import os
import tempfile
import pytest


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    """Create a fake project root with key directories."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "projects").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "core").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr("src.utils.paths.base_path", lambda: str(tmp_path))
    return tmp_path


class TestValidateWritePath:
    """Tests for _validate_write_path() — restricts create_file/append_file."""

    def _validate(self, path):
        """Import and call _validate_write_path. Returns None if OK, error string if blocked."""
        from src.tools.tool_registry import _validate_write_path
        return _validate_write_path(path)

    def test_workspace_file_allowed(self, project_root):
        """Writing to workspace/ is allowed."""
        p = str(project_root / "workspace" / "report.md")
        assert self._validate(p) is None

    def test_workspace_nested_allowed(self, project_root):
        """Writing to workspace/projects/subdir/ is allowed."""
        p = str(project_root / "workspace" / "projects" / "health" / "plan.txt")
        assert self._validate(p) is None

    def test_data_file_allowed(self, project_root):
        """Writing to data/ is allowed (runtime state)."""
        p = str(project_root / "data" / "goals_state.json")
        assert self._validate(p) is None

    def test_src_file_blocked(self, project_root):
        """Writing to src/ is blocked (must use write_source with approval)."""
        p = str(project_root / "src" / "core" / "heartbeat.py")
        result = self._validate(p)
        assert result is not None
        assert "restricted" in result.lower() or "workspace" in result.lower()

    def test_config_file_blocked(self, project_root):
        """Writing to config/ is blocked."""
        p = str(project_root / "config" / "rules.yaml")
        result = self._validate(p)
        assert result is not None

    def test_scripts_blocked(self, project_root):
        """Writing to scripts/ is blocked."""
        p = str(project_root / "scripts" / "install.py")
        result = self._validate(p)
        assert result is not None

    def test_project_root_file_blocked(self, project_root):
        """Writing directly to project root is blocked (not workspace/data)."""
        p = str(project_root / "sneaky_file.txt")
        result = self._validate(p)
        assert result is not None

    def test_outside_project_blocked(self, project_root):
        """Writing outside the project is blocked."""
        p = str(project_root.parent / "evil.txt")
        result = self._validate(p)
        assert result is not None

    def test_traversal_out_of_workspace(self, project_root):
        """Using .. to escape workspace/ into src/ is blocked."""
        p = str(project_root / "workspace" / ".." / "src" / "malicious.py")
        result = self._validate(p)
        assert result is not None

    def test_traversal_out_of_project(self, project_root):
        """Using .. to escape project entirely is blocked."""
        p = str(project_root / "workspace" / ".." / ".." / "etc" / "passwd")
        result = self._validate(p)
        assert result is not None

    def test_traversal_within_workspace_allowed(self, project_root):
        """.. that stays within workspace/ is allowed."""
        p = str(project_root / "workspace" / "projects" / ".." / "report.md")
        assert self._validate(p) is None

    @pytest.mark.skipif(
        os.name == "nt",
        reason="os.symlink requires privilege on Windows",
    )
    def test_symlink_escape_blocked(self, project_root):
        """A symlink inside workspace/ pointing outside the project is blocked."""
        outside = tempfile.mkdtemp()
        link = str(project_root / "workspace" / "escape_link")
        try:
            os.symlink(outside, link)
            target = os.path.join(link, "secret.txt")
            result = self._validate(target)
            # Should be blocked because realpath resolves outside project
            assert result is not None
        finally:
            if os.path.islink(link):
                os.unlink(link)
            os.rmdir(outside)

    @pytest.mark.skipif(
        os.name == "nt",
        reason="os.symlink requires privilege on Windows",
    )
    def test_symlink_to_data_allowed(self, project_root):
        """A symlink inside workspace/ pointing to data/ (still in project) is blocked
        because it resolves to data/ not workspace/ — but data/ is also an allowed write dir."""
        link = str(project_root / "workspace" / "data_link")
        os.symlink(str(project_root / "data"), link)
        try:
            target = os.path.join(link, "test.json")
            # Resolves to data/test.json — which is allowed
            assert self._validate(target) is None
        finally:
            os.unlink(link)

    def test_absolute_system_path_blocked(self, project_root):
        """Absolute system paths (like /tmp or C:\\Windows) are blocked."""
        assert self._validate(tempfile.gettempdir()) is not None
        assert self._validate("/etc/passwd") is not None
        if os.name == "nt":
            assert self._validate("C:\\Windows\\System32\\config") is not None


class TestFileWriteToolIntegration:
    """Integration tests for FileWriteTool using _validate_write_path."""

    def test_write_to_workspace_succeeds(self, project_root):
        """FileWriteTool successfully writes to workspace/."""
        from src.tools.tool_registry import FileWriteTool
        tool = FileWriteTool()
        target = str(project_root / "workspace" / "test_output.txt")
        result = tool.execute({"path": target, "content": "hello world"})
        assert result["success"] is True
        assert os.path.isfile(target)
        with open(target, "r") as f:
            assert f.read() == "hello world"

    def test_write_to_data_succeeds(self, project_root):
        """FileWriteTool successfully writes to data/."""
        from src.tools.tool_registry import FileWriteTool
        tool = FileWriteTool()
        target = str(project_root / "data" / "test_state.json")
        result = tool.execute({"path": target, "content": '{"test": true}'})
        assert result["success"] is True
        assert os.path.isfile(target)

    def test_write_to_src_fails(self, project_root):
        """FileWriteTool refuses to write to src/."""
        from src.tools.tool_registry import FileWriteTool
        tool = FileWriteTool()
        target = str(project_root / "src" / "evil.py")
        result = tool.execute({"path": target, "content": "import os; os.system('evil')"})
        assert result["success"] is False
        assert not os.path.exists(target)

    def test_write_creates_parent_dirs(self, project_root):
        """FileWriteTool creates parent directories in workspace/ automatically."""
        from src.tools.tool_registry import FileWriteTool
        tool = FileWriteTool()
        target = str(project_root / "workspace" / "new_project" / "deep" / "file.txt")
        result = tool.execute({"path": target, "content": "nested"})
        assert result["success"] is True
        assert os.path.isfile(target)

    def test_write_missing_path_param(self, project_root):
        """FileWriteTool returns error on missing path parameter."""
        from src.tools.tool_registry import FileWriteTool
        tool = FileWriteTool()
        result = tool.execute({"content": "no path"})
        assert result["success"] is False
        assert "Missing" in result["error"]
