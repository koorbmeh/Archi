"""
Unit tests for utils/project_context.py.

Covers load, save, scan_project_files, auto_populate, and _extract_from_identity.
Session 151.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from src.utils import project_context as pc_mod


@pytest.fixture
def base_dir(tmp_path):
    """Set up a temporary base directory and patch base_path_as_path."""
    with patch.object(pc_mod, "_base_path", return_value=tmp_path):
        yield tmp_path


# ── TestLoad ──────────────────────────────────────────────────────


class TestLoad:
    """Tests for the load function."""

    def test_load_from_json_file(self, base_dir):
        data_dir = base_dir / "data"
        data_dir.mkdir()
        ctx = {"version": 2, "focus_areas": ["AI"]}
        (data_dir / "project_context.json").write_text(json.dumps(ctx), encoding="utf-8")
        result = pc_mod.load()
        assert result["version"] == 2
        assert result["focus_areas"] == ["AI"]

    def test_load_falls_back_to_identity(self, base_dir):
        """No project_context.json → extracts from identity yaml."""
        with patch.object(pc_mod, "_extract_from_identity", return_value={"version": 1}):
            result = pc_mod.load()
        assert result["version"] == 1

    def test_load_handles_corrupt_json(self, base_dir):
        data_dir = base_dir / "data"
        data_dir.mkdir()
        (data_dir / "project_context.json").write_text("not json", encoding="utf-8")
        with patch.object(pc_mod, "_extract_from_identity", return_value={}):
            result = pc_mod.load()
        assert result == {}

    def test_load_handles_empty_json(self, base_dir):
        """json.load of 'null' or empty → returns {}."""
        data_dir = base_dir / "data"
        data_dir.mkdir()
        (data_dir / "project_context.json").write_text("null", encoding="utf-8")
        with patch.object(pc_mod, "_extract_from_identity", return_value={}):
            result = pc_mod.load()
        assert result == {}


# ── TestSave ──────────────────────────────────────────────────────


class TestSave:
    """Tests for the save function."""

    def test_save_creates_file(self, base_dir):
        data_dir = base_dir / "data"
        data_dir.mkdir()
        ctx = {"focus_areas": ["AI"]}
        result = pc_mod.save(ctx)
        assert result is True
        saved = json.loads((data_dir / "project_context.json").read_text(encoding="utf-8"))
        assert saved["focus_areas"] == ["AI"]
        assert "last_updated" in saved
        assert saved["version"] == 1  # setdefault

    def test_save_preserves_existing_version(self, base_dir):
        data_dir = base_dir / "data"
        data_dir.mkdir()
        ctx = {"version": 3, "focus_areas": []}
        pc_mod.save(ctx)
        saved = json.loads((data_dir / "project_context.json").read_text(encoding="utf-8"))
        assert saved["version"] == 3

    def test_save_adds_last_updated(self, base_dir):
        data_dir = base_dir / "data"
        data_dir.mkdir()
        ctx = {}
        pc_mod.save(ctx)
        saved = json.loads((data_dir / "project_context.json").read_text(encoding="utf-8"))
        assert "last_updated" in saved

    def test_save_failure_returns_false(self, base_dir):
        # data dir doesn't exist and we mock Path to fail
        result = pc_mod.save({"test": True})
        assert result is False

    def test_save_atomic_write(self, base_dir):
        """Writes to .tmp first, then replaces."""
        data_dir = base_dir / "data"
        data_dir.mkdir()
        ctx = {"key": "value"}
        pc_mod.save(ctx)
        # After save, .tmp should not exist (replaced)
        assert not (data_dir / "project_context.tmp").exists()
        assert (data_dir / "project_context.json").exists()


# ── TestScanProjectFiles ──────────────────────────────────────────


class TestScanProjectFiles:
    """Tests for scan_project_files."""

    def test_scan_lists_files_and_dirs(self, base_dir):
        project_dir = base_dir / "workspace" / "projects" / "myproject"
        project_dir.mkdir(parents=True)
        (project_dir / "README.md").touch()
        (project_dir / "src").mkdir()
        (project_dir / "data.json").touch()
        result = pc_mod.scan_project_files("workspace/projects/myproject")
        assert "README.md" in result
        assert "data.json" in result
        assert "src/" in result

    def test_scan_skips_dotfiles(self, base_dir):
        project_dir = base_dir / "workspace" / "projects" / "myproject"
        project_dir.mkdir(parents=True)
        (project_dir / ".git").mkdir()
        (project_dir / ".env").touch()
        (project_dir / "visible.txt").touch()
        result = pc_mod.scan_project_files("workspace/projects/myproject")
        assert ".git/" not in result
        assert ".env" not in result
        assert "visible.txt" in result

    def test_scan_nonexistent_path(self, base_dir):
        result = pc_mod.scan_project_files("workspace/projects/nonexistent")
        assert result == []

    def test_scan_error_returns_empty(self, base_dir):
        with patch.object(pc_mod, "_base_path", side_effect=Exception("fail")):
            result = pc_mod.scan_project_files("any/path")
        assert result == []

    def test_scan_sorted_output(self, base_dir):
        project_dir = base_dir / "workspace" / "projects" / "myproject"
        project_dir.mkdir(parents=True)
        (project_dir / "z_file.txt").touch()
        (project_dir / "a_file.txt").touch()
        (project_dir / "m_file.txt").touch()
        result = pc_mod.scan_project_files("workspace/projects/myproject")
        assert result == ["a_file.txt", "m_file.txt", "z_file.txt"]


# ── TestAutoPopulate ──────────────────────────────────────────────


class TestAutoPopulate:
    """Tests for auto_populate."""

    def test_no_projects_dir(self, base_dir):
        """No workspace/projects/ → returns existing context."""
        with patch.object(pc_mod, "load", return_value={"version": 1}):
            result = pc_mod.auto_populate()
        assert result["version"] == 1

    def test_discovers_projects(self, base_dir):
        projects_dir = base_dir / "workspace" / "projects"
        projects_dir.mkdir(parents=True)
        (projects_dir / "MyProject").mkdir()
        (projects_dir / "MyProject" / "README.md").write_text("# MyProject Overview\nThis is a comprehensive project for testing purposes", encoding="utf-8")
        data_dir = base_dir / "data"
        data_dir.mkdir()

        with patch.object(pc_mod, "load", return_value={}):
            result = pc_mod.auto_populate()

        assert "myproject" in result["active_projects"]
        proj = result["active_projects"]["myproject"]
        assert proj["path"] == "workspace/projects/MyProject"
        assert proj["priority"] == "medium"

    def test_discovers_description_from_overview(self, base_dir):
        projects_dir = base_dir / "workspace" / "projects"
        projects_dir.mkdir(parents=True)
        proj_dir = projects_dir / "TestProj"
        proj_dir.mkdir()
        (proj_dir / "OVERVIEW.md").write_text(
            "# Test\nThis is a detailed project overview for testing purposes here",
            encoding="utf-8",
        )
        data_dir = base_dir / "data"
        data_dir.mkdir()

        with patch.object(pc_mod, "load", return_value={}):
            result = pc_mod.auto_populate()

        desc = result["active_projects"]["testproj"]["description"]
        assert "detailed project overview" in desc

    def test_skips_hidden_directories(self, base_dir):
        projects_dir = base_dir / "workspace" / "projects"
        projects_dir.mkdir(parents=True)
        (projects_dir / ".hidden").mkdir()
        (projects_dir / "visible").mkdir()
        data_dir = base_dir / "data"
        data_dir.mkdir()

        with patch.object(pc_mod, "load", return_value={}):
            result = pc_mod.auto_populate()

        assert ".hidden" not in result.get("active_projects", {})
        assert "visible" in result["active_projects"]

    def test_merges_with_existing_context(self, base_dir):
        projects_dir = base_dir / "workspace" / "projects"
        projects_dir.mkdir(parents=True)
        (projects_dir / "NewProject").mkdir()
        data_dir = base_dir / "data"
        data_dir.mkdir()

        existing = {
            "focus_areas": ["machine learning"],
            "interests": ["robotics"],
            "current_projects": ["legacy"],
            "active_projects": {
                "old_project": {"path": "workspace/projects/old", "description": "Old"}
            },
        }

        with patch.object(pc_mod, "load", return_value=existing):
            result = pc_mod.auto_populate()

        assert result["focus_areas"] == ["machine learning"]
        # interests is not a standard field in auto_populate output
        assert "old_project" in result["active_projects"]
        assert "newproject" in result["active_projects"]

    def test_existing_project_takes_precedence(self, base_dir):
        projects_dir = base_dir / "workspace" / "projects"
        projects_dir.mkdir(parents=True)
        (projects_dir / "MyProj").mkdir()
        data_dir = base_dir / "data"
        data_dir.mkdir()

        existing = {
            "active_projects": {
                "myproj": {"path": "custom/path", "description": "Custom desc", "priority": "high"}
            },
        }

        with patch.object(pc_mod, "load", return_value=existing):
            result = pc_mod.auto_populate()

        # Existing entry should take precedence over auto-discovered
        assert result["active_projects"]["myproj"]["description"] == "Custom desc"
        assert result["active_projects"]["myproj"]["priority"] == "high"

    def test_empty_projects_dir_returns_load(self, base_dir):
        """Projects dir exists but has no subdirectories."""
        projects_dir = base_dir / "workspace" / "projects"
        projects_dir.mkdir(parents=True)
        (projects_dir / "some_file.txt").touch()  # file, not dir

        with patch.object(pc_mod, "load", return_value={"version": 1}):
            result = pc_mod.auto_populate()
        assert result["version"] == 1

    def test_auto_populate_saves_context(self, base_dir):
        projects_dir = base_dir / "workspace" / "projects"
        projects_dir.mkdir(parents=True)
        (projects_dir / "TestProj").mkdir()
        data_dir = base_dir / "data"
        data_dir.mkdir()

        with patch.object(pc_mod, "load", return_value={}):
            with patch.object(pc_mod, "save", return_value=True) as mock_save:
                pc_mod.auto_populate()
                mock_save.assert_called_once()


# ── TestExtractFromIdentity ───────────────────────────────────────


class TestExtractFromIdentity:
    """Tests for _extract_from_identity."""

    def test_extracts_from_yaml(self, base_dir):
        import yaml
        config_dir = base_dir / "config"
        config_dir.mkdir()
        identity = {
            "focus_areas": ["testing"],
            "user_context": {
                "interests": ["AI", "gaming"],
                "current_projects": ["archi"],
                "active_projects": {"proj1": {"path": "workspace/projects/proj1"}},
            },
        }
        (config_dir / "archi_identity.yaml").write_text(
            yaml.dump(identity), encoding="utf-8"
        )
        result = pc_mod._extract_from_identity()
        assert result["version"] == 1
        assert result["focus_areas"] == ["testing"]
        # interests not in _extract_from_identity return (moved to worldview)
        assert "proj1" in result["active_projects"]

    def test_returns_empty_when_no_file(self, base_dir):
        result = pc_mod._extract_from_identity()
        assert result == {}

    def test_handles_corrupt_yaml(self, base_dir):
        config_dir = base_dir / "config"
        config_dir.mkdir()
        (config_dir / "archi_identity.yaml").write_text(
            "invalid: yaml: [broken", encoding="utf-8"
        )
        result = pc_mod._extract_from_identity()
        # Should handle gracefully — either parse partial or return {}
        assert isinstance(result, dict)

    def test_handles_empty_yaml(self, base_dir):
        config_dir = base_dir / "config"
        config_dir.mkdir()
        (config_dir / "archi_identity.yaml").write_text("", encoding="utf-8")
        result = pc_mod._extract_from_identity()
        assert result == {} or result.get("version") == 1

    def test_handles_missing_user_context(self, base_dir):
        import yaml
        config_dir = base_dir / "config"
        config_dir.mkdir()
        identity = {"focus_areas": ["testing"]}
        (config_dir / "archi_identity.yaml").write_text(
            yaml.dump(identity), encoding="utf-8"
        )
        result = pc_mod._extract_from_identity()
        assert result["focus_areas"] == ["testing"]
        assert result["current_projects"] == []
        assert result["active_projects"] == {}
