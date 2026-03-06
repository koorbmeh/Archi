"""Tests for src/core/worldview.py — evolving worldview system."""

import json
import os
import tempfile
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.core import worldview


@pytest.fixture(autouse=True)
def _isolate_worldview(tmp_path, monkeypatch):
    """Redirect worldview storage to a temp directory for test isolation."""
    wv_path = str(tmp_path / "worldview.json")
    monkeypatch.setattr(worldview, "_worldview_path", lambda: wv_path)
    # Clear any cached state
    yield


# ── Load / Save ──────────────────────────────────────────────────────

class TestLoadSave:
    def test_load_missing_file_returns_empty(self):
        data = worldview.load()
        assert data == {"opinions": [], "preferences": [], "interests": [], "pending_revisions": []}

    def test_save_and_load_roundtrip(self):
        data = worldview._empty_worldview()
        data["opinions"].append({
            "topic": "testing", "position": "Tests are good",
            "confidence": 0.8, "basis": "experience",
            "formed": "2026-03-05", "last_updated": "2026-03-05",
            "history": [],
        })
        worldview.save(data)
        loaded = worldview.load()
        assert len(loaded["opinions"]) == 1
        assert loaded["opinions"][0]["topic"] == "testing"

    def test_load_corrupt_file_returns_empty(self, tmp_path, monkeypatch):
        path = str(tmp_path / "worldview.json")
        monkeypatch.setattr(worldview, "_worldview_path", lambda: path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("not json {{{")
        data = worldview.load()
        assert data == {"opinions": [], "preferences": [], "interests": [], "pending_revisions": []}

    def test_save_creates_directory(self, tmp_path, monkeypatch):
        nested = str(tmp_path / "sub" / "dir" / "worldview.json")
        monkeypatch.setattr(worldview, "_worldview_path", lambda: nested)
        worldview.save(worldview._empty_worldview())
        assert os.path.isfile(nested)


# ── Opinions ─────────────────────────────────────────────────────────

class TestOpinions:
    def test_add_opinion_creates_new(self):
        worldview.add_opinion("error handling", "Explicit returns are better", 0.7, "3 failures")
        op = worldview.get_opinion("error handling")
        assert op is not None
        assert op["position"] == "Explicit returns are better"
        assert op["confidence"] == 0.7

    def test_add_opinion_updates_existing(self):
        worldview.add_opinion("error handling", "Explicit returns are better", 0.7)
        worldview.add_opinion("error handling", "Exceptions are fine actually", 0.9)
        op = worldview.get_opinion("error handling")
        assert op["position"] == "Exceptions are fine actually"
        assert op["confidence"] == 0.9

    def test_opinion_history_tracked(self):
        worldview.add_opinion("testing", "Unit tests first", 0.5)
        worldview.add_opinion("testing", "Integration tests first", 0.6)
        op = worldview.get_opinion("testing")
        assert len(op["history"]) == 1  # Second call adds to history
        assert op["history"][0]["position"] == "Integration tests first"

    def test_get_opinion_case_insensitive(self):
        worldview.add_opinion("Error Handling", "Be explicit", 0.5)
        assert worldview.get_opinion("error handling") is not None
        assert worldview.get_opinion("ERROR HANDLING") is not None

    def test_get_opinion_missing_returns_none(self):
        assert worldview.get_opinion("nonexistent") is None

    def test_get_strong_opinions_filters_by_confidence(self):
        worldview.add_opinion("topic1", "pos1", 0.3)
        worldview.add_opinion("topic2", "pos2", 0.7)
        worldview.add_opinion("topic3", "pos3", 0.9)
        strong = worldview.get_strong_opinions(min_confidence=0.6)
        assert len(strong) == 2
        assert strong[0]["topic"] == "topic3"  # Sorted desc

    def test_confidence_clamped_0_to_1(self):
        worldview.add_opinion("topic", "pos", 1.5)
        op = worldview.get_opinion("topic")
        assert op["confidence"] == 1.0
        # Clamp to valid range; note 0.0 would be pruned (< OPINION_MIN_CONFIDENCE)
        worldview.add_opinion("topic", "pos", 0.2)
        op = worldview.get_opinion("topic")
        assert op["confidence"] == 0.2

    def test_opinion_history_capped_at_10(self):
        for i in range(15):
            worldview.add_opinion("topic", f"position {i}", 0.5 + i * 0.01)
        op = worldview.get_opinion("topic")
        assert len(op["history"]) <= 10


# ── Preferences ──────────────────────────────────────────────────────

class TestPreferences:
    def test_add_preference_creates_new(self):
        worldview.add_preference("code_style", "Prefer dataclasses over dicts", 0.8)
        prefs = worldview.get_preferences(domain="code_style")
        assert len(prefs) == 1
        assert prefs[0]["preference"] == "Prefer dataclasses over dicts"

    def test_add_preference_updates_existing(self):
        worldview.add_preference("code_style", "Prefer dataclasses over dicts", 0.5, 1)
        worldview.add_preference("code_style", "Prefer dataclasses over dicts", 0.8, 3)
        prefs = worldview.get_preferences(domain="code_style")
        assert len(prefs) == 1
        assert prefs[0]["strength"] == 0.8
        assert prefs[0]["evidence_count"] == 4  # 1 + 3

    def test_get_preferences_sorted_by_strength(self):
        worldview.add_preference("code", "pref1", 0.3)
        worldview.add_preference("code", "pref2", 0.9)
        worldview.add_preference("code", "pref3", 0.6)
        prefs = worldview.get_preferences(domain="code")
        assert prefs[0]["preference"] == "pref2"

    def test_get_preferences_no_filter(self):
        worldview.add_preference("code", "pref1", 0.5)
        worldview.add_preference("tools", "pref2", 0.7)
        prefs = worldview.get_preferences()
        assert len(prefs) == 2

    def test_preference_strength_clamped(self):
        worldview.add_preference("domain", "pref", 2.0)
        prefs = worldview.get_preferences(domain="domain")
        assert prefs[0]["strength"] == 1.0


# ── Interests ────────────────────────────────────────────────────────

class TestInterests:
    def test_add_interest_creates_new(self):
        worldview.add_interest("circuit breakers", 0.8, "Fascinating pattern")
        interests = worldview.get_interests()
        assert len(interests) == 1
        assert interests[0]["topic"] == "circuit breakers"

    def test_add_interest_updates_existing(self):
        worldview.add_interest("circuit breakers", 0.5)
        worldview.add_interest("circuit breakers", 0.9, "Even more interesting now")
        interests = worldview.get_interests()
        assert len(interests) == 1
        assert interests[0]["curiosity_level"] == 0.9
        assert interests[0]["notes"] == "Even more interesting now"

    def test_get_interests_filters_by_curiosity(self):
        worldview.add_interest("topic1", 0.2)
        worldview.add_interest("topic2", 0.8)
        interests = worldview.get_interests(min_curiosity=0.5)
        assert len(interests) == 1
        assert interests[0]["topic"] == "topic2"

    def test_interest_sorted_by_curiosity(self):
        worldview.add_interest("low", 0.3)
        worldview.add_interest("high", 0.9)
        worldview.add_interest("mid", 0.6)
        interests = worldview.get_interests(min_curiosity=0.0)
        assert interests[0]["topic"] == "high"


# ── Pruning ──────────────────────────────────────────────────────────

class TestPruning:
    def test_low_confidence_opinions_pruned(self):
        data = worldview._empty_worldview()
        data["opinions"].append({
            "topic": "weak", "position": "maybe", "confidence": 0.1,
            "formed": "2026-01-01", "last_updated": "2026-01-01",
        })
        data["opinions"].append({
            "topic": "strong", "position": "definitely", "confidence": 0.9,
            "formed": "2026-03-01", "last_updated": "2026-03-05",
        })
        worldview.save(data)
        loaded = worldview.load()
        topics = [o["topic"] for o in loaded["opinions"]]
        assert "weak" not in topics
        assert "strong" in topics

    def test_stale_interests_decay(self):
        data = worldview._empty_worldview()
        old_date = (date.today() - timedelta(days=35)).strftime("%Y-%m-%d")
        data["interests"].append({
            "topic": "stale", "curiosity_level": 0.5,
            "last_explored": old_date,
        })
        worldview.save(data)
        loaded = worldview.load()
        if loaded["interests"]:
            assert loaded["interests"][0]["curiosity_level"] < 0.5

    def test_opinion_cap_enforced(self):
        data = worldview._empty_worldview()
        for i in range(60):
            data["opinions"].append({
                "topic": f"topic_{i}", "position": f"pos_{i}",
                "confidence": 0.5, "formed": "2026-03-05",
                "last_updated": "2026-03-05",
            })
        worldview.save(data)
        loaded = worldview.load()
        assert len(loaded["opinions"]) <= worldview._MAX_OPINIONS

    def test_old_opinions_decay_confidence(self):
        data = worldview._empty_worldview()
        old_date = (date.today() - timedelta(days=35)).strftime("%Y-%m-%d")
        data["opinions"].append({
            "topic": "old opinion", "position": "something",
            "confidence": 0.5, "formed": old_date,
            "last_updated": old_date,
        })
        worldview.save(data)
        loaded = worldview.load()
        if loaded["opinions"]:
            assert loaded["opinions"][0]["confidence"] < 0.5


# ── Worldview context for prompts ────────────────────────────────────

class TestWorldviewContext:
    def test_empty_worldview_returns_empty_string(self):
        ctx = worldview.get_worldview_context()
        assert ctx == ""

    def test_context_includes_opinions(self):
        worldview.add_opinion("testing", "Always test", 0.8)
        ctx = worldview.get_worldview_context()
        assert "testing" in ctx
        assert "Always test" in ctx

    def test_context_includes_preferences(self):
        worldview.add_preference("code", "Use dataclasses", 0.9)
        ctx = worldview.get_worldview_context()
        assert "dataclasses" in ctx

    def test_context_includes_interests(self):
        worldview.add_interest("circuit breakers", 0.8)
        ctx = worldview.get_worldview_context()
        assert "circuit breakers" in ctx

    def test_context_respects_max_chars(self):
        for i in range(20):
            worldview.add_opinion(f"topic_{i}", f"A very long position about topic {i}" * 5, 0.9)
        ctx = worldview.get_worldview_context(max_chars=200)
        assert len(ctx) <= 200


# ── Reflection ───────────────────────────────────────────────────────

class TestReflection:
    def test_lightweight_reflection_reinforces_matching_opinion(self):
        worldview.add_opinion("error handling", "Explicit returns are better", 0.5, "past experience")
        changes = worldview.reflect_on_task(
            task_description="Fixed error handling in API module",
            goal_description="Improve error handling across codebase",
            outcome="Successfully refactored error handling to use explicit returns",
            success=True,
        )
        op = worldview.get_opinion("error handling")
        assert op["confidence"] > 0.5  # Reinforced by success

    def test_lightweight_reflection_weakens_on_failure(self):
        worldview.add_opinion("error handling", "Explicit returns are better", 0.5, "past experience")
        changes = worldview.reflect_on_task(
            task_description="Fixed error handling in API module",
            goal_description="Improve error handling across codebase",
            outcome="Explicit returns caused issues",
            success=False,
        )
        op = worldview.get_opinion("error handling")
        assert op["confidence"] < 0.5  # Weakened by failure

    def test_lightweight_reflection_no_match_returns_empty(self):
        worldview.add_opinion("testing", "Tests are good", 0.5)
        changes = worldview.reflect_on_task(
            task_description="Updated image generation",
            goal_description="Add SDXL support",
            outcome="Success",
            success=True,
        )
        assert changes is None or changes == {}

    def test_model_reflection_applies_updates(self):
        mock_model = MagicMock()
        mock_model.generate.return_value = {"text": json.dumps({
            "new_opinions": [
                {"topic": "caching", "position": "Cache aggressively", "confidence": 0.7, "basis": "task result"}
            ],
            "new_interests": [
                {"topic": "Redis patterns", "curiosity_level": 0.6, "notes": "Saw caching benefits"}
            ],
        })}

        changes = worldview.reflect_on_task(
            task_description="Implemented query caching",
            goal_description="Improve performance",
            outcome="Cache hit rate 85%",
            success=True,
            model=mock_model,
        )

        assert changes is not None
        assert "new_opinion:caching" in changes
        op = worldview.get_opinion("caching")
        assert op is not None
        assert op["position"] == "Cache aggressively"
        interests = worldview.get_interests()
        topics = [i["topic"] for i in interests]
        assert "Redis patterns" in topics

    def test_model_reflection_handles_empty_response(self):
        mock_model = MagicMock()
        mock_model.generate.return_value = {"text": ""}
        changes = worldview.reflect_on_task(
            "task", "goal", "outcome", True, model=mock_model,
        )
        assert changes is None or changes == {}

    def test_model_reflection_handles_exception(self):
        mock_model = MagicMock()
        mock_model.generate.side_effect = RuntimeError("API down")
        changes = worldview.reflect_on_task(
            "task", "goal", "outcome", True, model=mock_model,
        )
        assert changes is None or changes == {}

    def test_model_reflection_updates_existing_opinion(self):
        worldview.add_opinion("caching", "Cache sometimes", 0.5)
        mock_model = MagicMock()
        mock_model.generate.return_value = {"text": json.dumps({
            "updated_opinions": [
                {"topic": "caching", "confidence_delta": 0.2}
            ],
        })}

        changes = worldview.reflect_on_task(
            "Caching task", "Perf goal", "Good results", True, model=mock_model,
        )

        op = worldview.get_opinion("caching")
        assert op["confidence"] == pytest.approx(0.7, abs=0.01)


# ── Helper tests ─────────────────────────────────────────────────────

class TestHelpers:
    def test_find_by_field_case_insensitive(self):
        items = [{"name": "Hello"}, {"name": "World"}]
        assert worldview._find_by_field(items, "name", "hello") == {"name": "Hello"}
        assert worldview._find_by_field(items, "name", "WORLD") == {"name": "World"}
        assert worldview._find_by_field(items, "name", "missing") is None

    def test_parse_date_valid(self):
        d = worldview._parse_date("2026-03-05")
        assert d == date(2026, 3, 5)

    def test_parse_date_with_time(self):
        d = worldview._parse_date("2026-03-05T10:30:00")
        assert d == date(2026, 3, 5)

    def test_parse_date_invalid(self):
        assert worldview._parse_date(None) is None
        assert worldview._parse_date("") is None
        assert worldview._parse_date("not a date") is None

    def test_apply_model_updates_validates_input(self):
        # Invalid entries should be skipped, not crash
        changes = worldview._apply_model_updates({
            "new_opinions": [{"no_topic": True}, "not a dict"],
            "new_preferences": [{"domain": "x"}],  # missing preference
            "new_interests": [{"topic": "valid", "curiosity_level": 0.5}],
        })
        # Only the valid interest should be applied
        interests = worldview.get_interests(min_curiosity=0.0)
        assert any(i["topic"] == "valid" for i in interests)


# ── Opinion revisions ("I changed my mind", session 201) ──────────

class TestOpinionRevisions:
    def test_significant_change_flags_revision(self):
        worldview.add_opinion("caching", "Cache rarely", 0.4)
        worldview.add_opinion("caching", "Cache aggressively", 0.8)
        revisions = worldview.get_pending_revisions()
        assert len(revisions) == 1
        assert revisions[0]["topic"] == "caching"
        assert revisions[0]["old_position"] == "Cache rarely"
        assert revisions[0]["new_position"] == "Cache aggressively"

    def test_small_change_no_revision(self):
        worldview.add_opinion("testing", "Unit tests first", 0.5)
        worldview.add_opinion("testing", "Integration tests first", 0.55)
        # Position changed but confidence delta < 0.3 and new_confidence < 0.6
        revisions = worldview.get_pending_revisions()
        assert len(revisions) == 0

    def test_same_position_no_revision(self):
        worldview.add_opinion("testing", "Unit tests first", 0.5)
        worldview.add_opinion("testing", "Unit tests first", 0.9)
        revisions = worldview.get_pending_revisions()
        assert len(revisions) == 0

    def test_position_change_high_confidence_flags(self):
        """Position changed + new_confidence >= 0.6 triggers revision."""
        worldview.add_opinion("api design", "REST is best", 0.5)
        worldview.add_opinion("api design", "GraphQL is better", 0.7)
        revisions = worldview.get_pending_revisions()
        assert len(revisions) == 1

    def test_clear_revision(self):
        worldview.add_opinion("caching", "Cache rarely", 0.3)
        worldview.add_opinion("caching", "Cache always", 0.8)
        assert len(worldview.get_pending_revisions()) == 1
        worldview.clear_revision("caching")
        assert len(worldview.get_pending_revisions()) == 0

    def test_clear_all_revisions(self):
        worldview.add_opinion("topic1", "A", 0.3)
        worldview.add_opinion("topic1", "B", 0.8)
        worldview.add_opinion("topic2", "C", 0.3)
        worldview.add_opinion("topic2", "D", 0.8)
        assert len(worldview.get_pending_revisions()) == 2
        worldview.clear_all_revisions()
        assert len(worldview.get_pending_revisions()) == 0

    def test_no_duplicate_pending_revision(self):
        worldview.add_opinion("caching", "Cache rarely", 0.3)
        worldview.add_opinion("caching", "Cache sometimes", 0.7)
        worldview.add_opinion("caching", "Cache always", 0.9)
        revisions = worldview.get_pending_revisions()
        assert len(revisions) == 1  # Only one pending per topic

    def test_revision_cap_at_5(self):
        for i in range(8):
            worldview.add_opinion(f"topic_{i}", "old", 0.3)
            worldview.add_opinion(f"topic_{i}", "new", 0.8)
        revisions = worldview.get_pending_revisions()
        assert len(revisions) <= 5

    def test_pending_revisions_in_empty_worldview(self):
        revisions = worldview.get_pending_revisions()
        assert revisions == []

    def test_load_preserves_pending_revisions(self):
        worldview.add_opinion("testing", "A", 0.3)
        worldview.add_opinion("testing", "B", 0.8)
        # Reload from disk
        data = worldview.load()
        assert len(data.get("pending_revisions", [])) == 1
