"""Unit tests for project_sync — syncing user signals to project_context.json."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.utils.project_sync import (
    _detect_intent, _match_project, sync_signals_to_project_context,
)

_SAMPLE_PROJECTS = {
    "health_optimization": {
        "path": "workspace/projects/Health_Optimization",
        "description": "Health tracking",
        "priority": "medium",
        "focus_areas": [],
        "autonomous_tasks": [],
    },
    "archi_self_improvement": {
        "path": "workspace/projects/Archi_Self_Improvement",
        "description": "Archi development",
        "priority": "medium",
        "focus_areas": [],
        "autonomous_tasks": [],
    },
}

_SAMPLE_CONTEXT = {
    "version": 2,
    "focus_areas": [],
    "interests": [],
    "current_projects": [],
    "active_projects": dict(_SAMPLE_PROJECTS),
    "last_updated": "2026-02-20T00:00:00",
}


# ---- _detect_intent tests ----

class TestDetectIntent:
    def test_deactivate_phrases(self):
        assert _detect_intent("i'm done with this") == "deactivate"
        assert _detect_intent("not doing that anymore") == "deactivate"
        assert _detect_intent("i'm no longer interested") == "deactivate"

    def test_boost_phrases(self):
        assert _detect_intent("focus on this more") == "boost"
        assert _detect_intent("prioritize the project") == "boost"

    def test_new_interest_phrases(self):
        assert _detect_intent("i'm interested in woodworking") == "new_interest"
        assert _detect_intent("want to try meditation") == "new_interest"

    def test_no_match(self):
        assert _detect_intent("the weather is nice") is None
        assert _detect_intent("what time is it") is None


# ---- _match_project tests ----

class TestMatchProject:
    def test_exact_key_match(self):
        assert _match_project("health_optimization is going well", _SAMPLE_PROJECTS) == "health_optimization"

    def test_partial_name_match(self):
        assert _match_project("the health project", _SAMPLE_PROJECTS) == "health_optimization"

    def test_archi_match(self):
        assert _match_project("archi improvements", _SAMPLE_PROJECTS) == "archi_self_improvement"

    def test_no_match(self):
        assert _match_project("something unrelated", _SAMPLE_PROJECTS) is None

    def test_short_words_ignored(self):
        # Words under 4 chars shouldn't match (avoids false positives)
        assert _match_project("the cat sat", {"the_cat": {"path": "x"}}) is None


# ---- sync_signals_to_project_context tests ----

class TestSyncSignals:
    def _make_ctx(self):
        """Return a fresh copy of sample context."""
        return json.loads(json.dumps(_SAMPLE_CONTEXT))

    def test_deactivate_project(self):
        ctx = self._make_ctx()
        signals = [{"type": "preference", "text": "I'm done with the health project"}]
        with patch("src.utils.project_sync.project_context") as mock_pc:
            mock_pc.load.return_value = ctx
            sync_signals_to_project_context(signals)
            mock_pc.save.assert_called_once()
            saved = mock_pc.save.call_args[0][0]
            assert saved["active_projects"]["health_optimization"]["priority"] == "inactive"

    def test_boost_project(self):
        ctx = self._make_ctx()
        signals = [{"type": "preference", "text": "I want to focus on archi more"}]
        with patch("src.utils.project_sync.project_context") as mock_pc:
            mock_pc.load.return_value = ctx
            sync_signals_to_project_context(signals)
            mock_pc.save.assert_called_once()
            saved = mock_pc.save.call_args[0][0]
            assert saved["active_projects"]["archi_self_improvement"]["priority"] == "high"

    def test_new_interest(self):
        """new_interest signals go to user_model, not project_context save."""
        ctx = self._make_ctx()
        signals = [{"type": "preference", "text": "I'm interested in woodworking"}]
        with patch("src.utils.project_sync.project_context") as mock_pc, \
             patch("src.core.user_model.get_user_model") as mock_um:
            mock_pc.load.return_value = ctx
            mock_model = MagicMock()
            mock_um.return_value = mock_model
            sync_signals_to_project_context(signals)
            # Interests now go to user_model, not project_context
            mock_model.add_interest.assert_called_once_with("woodworking")
            mock_pc.save.assert_not_called()

    def test_no_change_no_save(self):
        ctx = self._make_ctx()
        signals = [{"type": "preference", "text": "the weather is nice"}]
        with patch("src.utils.project_sync.project_context") as mock_pc:
            mock_pc.load.return_value = ctx
            sync_signals_to_project_context(signals)
            mock_pc.save.assert_not_called()

    def test_non_preference_signals_ignored(self):
        ctx = self._make_ctx()
        signals = [{"type": "correction", "text": "done with health project"}]
        with patch("src.utils.project_sync.project_context") as mock_pc:
            mock_pc.load.return_value = ctx
            sync_signals_to_project_context(signals)
            mock_pc.save.assert_not_called()

    def test_empty_signals(self):
        sync_signals_to_project_context([])  # should not raise
        sync_signals_to_project_context(None)  # should not raise

    def test_already_inactive_no_save(self):
        ctx = self._make_ctx()
        ctx["active_projects"]["health_optimization"]["priority"] = "inactive"
        signals = [{"type": "preference", "text": "done with health"}]
        with patch("src.utils.project_sync.project_context") as mock_pc:
            mock_pc.load.return_value = ctx
            sync_signals_to_project_context(signals)
            mock_pc.save.assert_not_called()
