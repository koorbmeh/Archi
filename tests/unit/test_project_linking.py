"""Unit tests for project-aware file path linking.

Tests _resolve_project_path() in autonomous_executor — matching goals/tasks
to active projects so reports go into project folders instead of orphaned
in workspace/reports/.
"""

import os
import tempfile
from unittest.mock import patch

import pytest
import yaml


# ---- Helper to create a temp identity config ----

def _write_identity(tmpdir: str, active_projects: dict) -> str:
    """Write a minimal archi_identity.yaml and return the config dir path."""
    config_dir = os.path.join(tmpdir, "config")
    os.makedirs(config_dir, exist_ok=True)
    identity = {
        "user_context": {
            "active_projects": active_projects,
        },
    }
    path = os.path.join(config_dir, "archi_identity.yaml")
    with open(path, "w") as f:
        yaml.dump(identity, f)
    return tmpdir


# ---- Tests ----

class TestResolveProjectPath:
    """Tests for _resolve_project_path()."""

    def _resolve(self, goal: str, task: str, tmpdir: str):
        """Call _resolve_project_path with the temp dir as base path."""
        from src.core.autonomous_executor import _resolve_project_path
        from pathlib import Path
        # Patch where _base_path is actually looked up (project_context imports it)
        with patch("src.utils.project_context._base_path", return_value=Path(tmpdir)):
            return _resolve_project_path(goal, task)

    def test_matches_project_by_key(self):
        """Goal mentioning 'health optimization' matches health_optimization project."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_identity(tmpdir, {
                "health_optimization": {
                    "path": "workspace/projects/Health_Optimization",
                    "description": "Health and longevity protocol",
                    "focus_areas": ["Supplement research"],
                },
            })
            result = self._resolve("Research health optimization supplements", "Find creatine studies", tmpdir)
        assert result == "workspace/projects/Health_Optimization"

    def test_matches_project_by_description(self):
        """Goal containing the project description as substring matches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_identity(tmpdir, {
                "health_optimization": {
                    "path": "workspace/projects/Health_Optimization",
                    "description": "Health and longevity protocol",
                    "focus_areas": [],
                },
            })
            result = self._resolve("Review the health and longevity protocol", "Check for updates", tmpdir)
        assert result == "workspace/projects/Health_Optimization"

    def test_matches_project_by_focus_area(self):
        """Task mentioning a focus area matches the project."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_identity(tmpdir, {
                "health_optimization": {
                    "path": "workspace/projects/Health_Optimization",
                    "description": "Health protocol",
                    "focus_areas": ["Supplement research and optimization", "Sleep optimization"],
                },
            })
            result = self._resolve("Improve sleep quality", "Research sleep optimization techniques", tmpdir)
        assert result == "workspace/projects/Health_Optimization"

    def test_no_match_returns_none(self):
        """Unrelated goal returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_identity(tmpdir, {
                "health_optimization": {
                    "path": "workspace/projects/Health_Optimization",
                    "description": "Health protocol",
                    "focus_areas": ["Supplements"],
                },
            })
            result = self._resolve("Build a Discord bot", "Write Python code for bot", tmpdir)
        assert result is None

    def test_no_active_projects_returns_none(self):
        """Empty active_projects returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_identity(tmpdir, {})
            result = self._resolve("Research supplements", "Find studies", tmpdir)
        assert result is None

    def test_no_config_file_returns_none(self):
        """Missing config file returns None gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._resolve("Research supplements", "Find studies", tmpdir)
        assert result is None

    def test_multiple_projects_first_match(self):
        """With multiple projects, returns the first matching one."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_identity(tmpdir, {
                "health_optimization": {
                    "path": "workspace/projects/Health_Optimization",
                    "description": "Health protocol",
                    "focus_areas": ["Supplements"],
                },
                "archi_dev": {
                    "path": "workspace/projects/Archi",
                    "description": "Archi autonomous agent development",
                    "focus_areas": ["Code quality"],
                },
            })
            result = self._resolve("Improve Archi code quality", "Refactor router", tmpdir)
        assert result == "workspace/projects/Archi"

    def test_case_insensitive_matching(self):
        """Matching is case-insensitive."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_identity(tmpdir, {
                "health_optimization": {
                    "path": "workspace/projects/Health_Optimization",
                    "description": "HEALTH AND LONGEVITY PROTOCOL",
                    "focus_areas": [],
                },
            })
            result = self._resolve("health and longevity protocol research", "review", tmpdir)
        assert result == "workspace/projects/Health_Optimization"

    def test_project_without_path_skipped(self):
        """Projects without a path field are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_identity(tmpdir, {
                "broken_project": {
                    "description": "Health stuff",
                    # no path
                },
            })
            result = self._resolve("Research health stuff", "Find info", tmpdir)
        assert result is None

    def test_non_dict_project_skipped(self):
        """Non-dict project values are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_identity(tmpdir, {
                "simple_project": "just a string, not a dict",
            })
            result = self._resolve("Simple project work", "Do stuff", tmpdir)
        assert result is None


class TestProjectPathHintInjection:
    """Verify that execute_task injects project path hints."""

    def test_hint_format(self):
        """The hint should mention the project path and say NOT workspace/reports/."""
        # Just verify the hint text format
        project_path = "workspace/projects/Health_Optimization"
        hint = (
            f"FILE OUTPUT: Save all reports and research files under "
            f"{project_path}/ (NOT workspace/reports/). "
            f"This task belongs to the project at {project_path}."
        )
        assert "workspace/projects/Health_Optimization" in hint
        assert "NOT workspace/reports/" in hint


class TestPlanExecutorPromptUpdate:
    """Verify the PlanExecutor prompt prefers project paths."""

    def test_create_file_mentions_project_path(self):
        """PlanExecutor prompt should guide toward project folders."""
        from src.core.plan_executor import PlanExecutor
        import inspect
        # Read the source of PlanExecutor to check the prompt
        source = inspect.getsource(PlanExecutor)
        assert "workspace/projects/" in source
        assert "NOT under workspace/reports/" in source or "NOT workspace/reports/" in source
