"""Tests for Discord project management commands (add/remove/list).

Tests the parser (_parse_project_command) and handler (_handle_project_command)
from discord_bot.py.
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from src.interfaces.discord_bot import _parse_project_command, _handle_project_command


# ── Parser tests ──────────────────────────────────────────────────

class TestParseProjectCommand:
    """Tests for _parse_project_command regex matching."""

    def test_list_projects(self):
        assert _parse_project_command("list projects") == ("list", None)

    def test_show_projects(self):
        assert _parse_project_command("show projects") == ("list", None)

    def test_what_projects(self):
        assert _parse_project_command("what projects") == ("list", None)

    def test_what_are_my_projects(self):
        assert _parse_project_command("what are my projects") == ("list", None)

    def test_show_active_projects(self):
        assert _parse_project_command("show active projects") == ("list", None)

    def test_add_project(self):
        assert _parse_project_command("add project health tracker") == ("add", "health tracker")

    def test_add_project_with_called(self):
        assert _parse_project_command("add a project called meal planner") == ("add", "meal planner")

    def test_create_project(self):
        assert _parse_project_command("create project budget tool") == ("add", "budget tool")

    def test_polite_add_project(self):
        assert _parse_project_command("can you add a project called fitness?") == ("add", "fitness")

    def test_please_add_project(self):
        assert _parse_project_command("please add project journal") == ("add", "journal")

    def test_remove_project(self):
        assert _parse_project_command("remove project health_tracker") == ("remove", "health_tracker")

    def test_delete_project(self):
        assert _parse_project_command("delete project old_app") == ("remove", "old_app")

    def test_drop_the_project(self):
        assert _parse_project_command("drop the project meal planner") == ("remove", "meal planner")

    def test_reverse_remove(self):
        assert _parse_project_command("remove the health tracker project") == ("remove", "health tracker")

    def test_deactivate_project(self):
        assert _parse_project_command("deactivate project stale_idea") == ("remove", "stale_idea")

    def test_no_project_keyword(self):
        assert _parse_project_command("hello there") is None

    def test_project_in_sentence_no_command(self):
        assert _parse_project_command("tell me about your project work") is None

    def test_empty_name_rejected(self):
        # "add project" with nothing after should not match empty name
        result = _parse_project_command("add project")
        # The regex captures empty string → stripped to "" → falsy → returns None
        assert result is None


# ── Handler tests ─────────────────────────────────────────────────

class TestHandleProjectCommand:
    """Tests for _handle_project_command using mocked project_context."""

    @pytest.fixture(autouse=True)
    def mock_project_context(self):
        """Mock project_context.load/save."""
        self._ctx = {"active_projects": {}, "version": 2}

        def _load():
            return dict(self._ctx)

        def _save(ctx):
            self._ctx = ctx
            return True

        with patch("src.utils.project_context.load", side_effect=_load), \
             patch("src.utils.project_context.save", side_effect=_save):
            yield

    def test_list_empty(self):
        result = _handle_project_command("list", None)
        assert "No active projects" in result
        assert "add project" in result

    def test_list_with_projects(self):
        self._ctx["active_projects"] = {
            "health": {"description": "Health tracker", "priority": "high"},
            "budget": {"description": "Budget app", "priority": "medium"},
        }
        result = _handle_project_command("list", None)
        assert "health" in result
        assert "budget" in result
        assert "Active projects" in result

    def test_add_new_project(self):
        result = _handle_project_command("add", "Meal Planner")
        assert "Added project **meal_planner**" in result
        assert "meal_planner" in self._ctx["active_projects"]
        proj = self._ctx["active_projects"]["meal_planner"]
        assert proj["description"] == "Meal Planner"
        assert proj["priority"] == "medium"
        assert "workspace/projects/Meal_Planner" in proj["path"]

    def test_add_duplicate_project(self):
        self._ctx["active_projects"]["health"] = {"description": "Health"}
        result = _handle_project_command("add", "health")
        assert "already exists" in result

    def test_remove_existing_project(self):
        self._ctx["active_projects"]["health"] = {"description": "Health tracker"}
        result = _handle_project_command("remove", "health")
        assert "Removed project **health**" in result
        assert "health" not in self._ctx["active_projects"]

    def test_remove_nonexistent_project(self):
        result = _handle_project_command("remove", "nonexistent")
        assert "No project matching" in result

    def test_remove_fuzzy_match(self):
        self._ctx["active_projects"]["health_tracker"] = {"description": "Health"}
        result = _handle_project_command("remove", "health tracker")
        assert "Removed project **health_tracker**" in result

    def test_remove_ambiguous_match(self):
        self._ctx["active_projects"]["health_a"] = {"description": "A"}
        self._ctx["active_projects"]["health_b"] = {"description": "B"}
        result = _handle_project_command("remove", "health")
        assert "Multiple matches" in result

    def test_add_normalizes_key(self):
        _handle_project_command("add", "My Cool Project")
        assert "my_cool_project" in self._ctx["active_projects"]
