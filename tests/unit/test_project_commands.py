"""Tests for Discord project management commands (add/remove/list/resume/status).

Tests the parser (_parse_project_command) and handler (_handle_project_command)
from discord_bot.py.
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from src.interfaces.discord_bot import (
    _parse_project_command,
    _handle_project_command,
    _handle_resume_project,
    _handle_ext_project_status,
)


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


# ── Resume / status parser tests ─────────────────────────────────

class TestParseResumeAndStatus:
    """Tests for resume project and project status parsing."""

    def test_resume_project(self):
        assert _parse_project_command("resume project") == ("resume", None)

    def test_retry_project(self):
        assert _parse_project_command("retry project") == ("resume", None)

    def test_resume_the_project(self):
        assert _parse_project_command("resume the project") == ("resume", None)

    def test_unpause_project(self):
        assert _parse_project_command("unpause project") == ("resume", None)

    def test_restart_project(self):
        assert _parse_project_command("restart project") == ("resume", None)

    def test_project_status(self):
        assert _parse_project_command("project status") == ("ext_status", None)

    def test_status_of_project(self):
        assert _parse_project_command("status of the project") == ("ext_status", None)

    def test_hows_the_project(self):
        assert _parse_project_command("how's the project") == ("ext_status", None)

    def test_how_is_the_project(self):
        assert _parse_project_command("how is the project") == ("ext_status", None)


# ── Resume project handler tests ─────────────────────────────────

class TestHandleResumeProject:
    """Tests for _handle_resume_project with mocked strategic planner."""

    def test_no_paused_projects(self):
        with patch("src.interfaces.discord_bot._get_router", return_value=MagicMock()):
            with patch("src.core.strategic_planner.get_paused_projects", return_value=[]):
                result = _handle_resume_project()
                assert "No paused projects" in result

    def test_resume_success(self):
        paused_proj = {
            "project_id": "test_proj",
            "title": "Test Project",
            "status": "paused",
        }
        mock_result = MagicMock()
        mock_result.action = "started"
        mock_result.phase_number = 2
        mock_result.goal_descriptions = ["Task A", "Task B"]

        with patch("src.interfaces.discord_bot._get_router", return_value=MagicMock()), \
             patch("src.core.strategic_planner.get_paused_projects", return_value=[paused_proj]), \
             patch("src.core.strategic_planner.StrategicPlanner.resume_project", return_value=mock_result), \
             patch("src.interfaces.discord_bot._heartbeat", None):
            result = _handle_resume_project()
            assert "Project resumed" in result
            assert "Phase 2" in result
            assert "2 task(s)" in result

    def test_resume_creates_goals(self):
        paused_proj = {
            "project_id": "test_proj",
            "title": "Test Project",
            "status": "paused",
        }
        mock_result = MagicMock()
        mock_result.action = "started"
        mock_result.phase_number = 2
        mock_result.goal_descriptions = ["Task A"]

        mock_gm = MagicMock()
        mock_hb = MagicMock()
        mock_hb.goal_manager = mock_gm

        with patch("src.interfaces.discord_bot._get_router", return_value=MagicMock()), \
             patch("src.core.strategic_planner.get_paused_projects", return_value=[paused_proj]), \
             patch("src.core.strategic_planner.StrategicPlanner.resume_project", return_value=mock_result), \
             patch("src.interfaces.discord_bot._heartbeat", mock_hb):
            result = _handle_resume_project()
            mock_gm.create_goal.assert_called_once_with(
                description="Task A",
                user_intent="Self-extension: test_proj",
                priority=4,
                project_id="test_proj",
                project_phase=2,
            )

    def test_resume_failure(self):
        paused_proj = {"project_id": "test_proj", "title": "Test", "status": "paused"}
        mock_result = MagicMock()
        mock_result.action = "none"
        mock_result.message = "Cannot resume: status is active"

        with patch("src.interfaces.discord_bot._get_router", return_value=MagicMock()), \
             patch("src.core.strategic_planner.get_paused_projects", return_value=[paused_proj]), \
             patch("src.core.strategic_planner.StrategicPlanner.resume_project", return_value=mock_result):
            result = _handle_resume_project()
            assert "Couldn't resume" in result


# ── Project status handler tests ─────────────────────────────────

class TestHandleExtProjectStatus:
    """Tests for _handle_ext_project_status."""

    def test_no_projects(self):
        with patch("src.core.strategic_planner.get_active_project", return_value=None), \
             patch("src.core.strategic_planner.get_paused_projects", return_value=[]), \
             patch("src.core.strategic_planner.get_planned_projects", return_value=[]):
            result = _handle_ext_project_status()
            assert "No self-extension projects" in result

    def test_active_project(self):
        active = {
            "project_id": "web_browsing",
            "title": "Web Browsing",
            "current_phase": 2,
            "phases": [
                {"status": "completed", "phase_number": 1},
                {"status": "in_progress", "phase_number": 2},
                {"status": "pending", "phase_number": 3},
            ],
        }
        with patch("src.core.strategic_planner.get_active_project", return_value=active), \
             patch("src.core.strategic_planner.get_paused_projects", return_value=[]), \
             patch("src.core.strategic_planner.get_planned_projects", return_value=[]):
            result = _handle_ext_project_status()
            assert "Active" in result
            assert "Web Browsing" in result
            assert "Phase 2/3" in result

    def test_paused_project(self):
        paused = [{
            "project_id": "email_v2",
            "title": "Email V2",
            "pause_reason": "SMTP auth failed",
        }]
        with patch("src.core.strategic_planner.get_active_project", return_value=None), \
             patch("src.core.strategic_planner.get_paused_projects", return_value=paused), \
             patch("src.core.strategic_planner.get_planned_projects", return_value=[]):
            result = _handle_ext_project_status()
            assert "Paused" in result
            assert "Email V2" in result
            assert "resume project" in result

    def test_planned_project(self):
        planned = [{"project_id": "music_gen", "title": "Music Generation"}]
        with patch("src.core.strategic_planner.get_active_project", return_value=None), \
             patch("src.core.strategic_planner.get_paused_projects", return_value=[]), \
             patch("src.core.strategic_planner.get_planned_projects", return_value=planned):
            result = _handle_ext_project_status()
            assert "Planned" in result
            assert "Music Generation" in result
            assert "go for it" in result
