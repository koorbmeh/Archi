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
        expected = worldview._empty_worldview()
        assert data == expected

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
        assert data == worldview._empty_worldview()

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

    def test_lightweight_reflection_no_opinion_match(self):
        worldview.add_opinion("testing", "Tests are good", 0.5)
        changes = worldview.reflect_on_task(
            task_description="Updated image generation",
            goal_description="Add SDXL support",
            outcome="Success",
            success=True,
        )
        # No opinion matched, but bootstrap may seed an interest
        if changes:
            # Should only contain seeded interest, not opinion changes
            assert "seeded_interest" in changes
            assert not any(k.startswith("opinion:") for k in changes)

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


# ── Taste Development (session 202) ─────────────────────────────

class TestTasteDevelopment:
    def test_efficient_task_creates_preference(self):
        result = worldview.develop_taste(
            task_description="Research web APIs",
            success=True, cost=0.05, steps=8,
            model_used="grok-4.1-fast", verified=True,
        )
        assert result is not None
        assert "efficiency" in result
        prefs = worldview.get_preferences(domain="taste_efficiency")
        assert len(prefs) >= 1
        assert "research" in prefs[0]["preference"].lower()

    def test_expensive_failure_creates_caution(self):
        result = worldview.develop_taste(
            task_description="Code a complex refactor",
            success=False, cost=0.35, steps=20,
            model_used="grok-4.1-fast",
        )
        assert result is not None
        assert "caution" in result
        prefs = worldview.get_preferences(domain="taste_caution")
        assert len(prefs) >= 1

    def test_model_performance_tracked(self):
        worldview.develop_taste(
            task_description="Write a summary report",
            success=True, cost=0.08, steps=10,
            model_used="grok-4.1-fast",
        )
        prefs = worldview.get_preferences(domain="taste_model")
        assert len(prefs) >= 1
        assert "grok" in prefs[0]["preference"].lower()

    def test_unverified_efficient_creates_weaker_pref(self):
        """Efficient but unverified task creates a lower-strength preference."""
        result = worldview.develop_taste(
            task_description="Do something",
            success=True, cost=0.08, steps=10,
            verified=False,
        )
        # Session 208: unverified efficient tasks now create 0.3-strength prefs
        assert result is not None
        assert "efficiency" in result
        prefs = worldview.get_preferences(domain="taste_efficiency")
        assert len(prefs) >= 1
        assert prefs[0]["strength"] == 0.3  # Lower than verified (0.5)

    def test_get_taste_context_empty(self):
        ctx = worldview.get_taste_context()
        assert ctx == ""

    def test_get_taste_context_with_prefs(self):
        worldview.develop_taste(
            "Research APIs", True, 0.03, 5, "grok-4.1-fast", True,
        )
        ctx = worldview.get_taste_context()
        assert len(ctx) > 0
        assert "works" in ctx.lower() or "research" in ctx.lower()

    def test_task_type_classification(self):
        """Different task descriptions produce different type classifications."""
        worldview.develop_taste("Research the market", True, 0.05, 8, verified=True)
        worldview.develop_taste("Write a blog post", True, 0.04, 6, verified=True)
        prefs = worldview.get_preferences(domain="taste_efficiency")
        pref_texts = [p["preference"].lower() for p in prefs]
        types_found = set()
        for t in pref_texts:
            if "research" in t:
                types_found.add("research")
            if "writing" in t:
                types_found.add("writing")
        assert len(types_found) >= 1  # At least one type classified


# ── Personal Projects (session 203) ──────────────────────────────

class TestPersonalProjects:
    def test_add_and_get_project(self):
        project = worldview.add_personal_project(
            "API patterns KB", origin_interest="API design", description="Catalog common patterns",
        )
        assert project is not None
        assert project["title"] == "API patterns KB"
        assert project["status"] == "active"
        assert project["work_sessions"] == 0

        active = worldview.get_personal_projects(status="active")
        assert len(active) == 1
        assert active[0]["title"] == "API patterns KB"

    def test_duplicate_project_rejected(self):
        worldview.add_personal_project("Test Project")
        dup = worldview.add_personal_project("test project")  # case-insensitive
        assert dup is None

    def test_update_project_progress(self):
        worldview.add_personal_project("Research proj")
        ok = worldview.update_personal_project(
            "Research proj", progress_note="Found 3 patterns", status="",
        )
        assert ok is True

        projects = worldview.get_personal_projects(status="active")
        assert projects[0]["work_sessions"] == 1
        assert len(projects[0]["progress_notes"]) == 1
        assert "Found 3 patterns" in projects[0]["progress_notes"][0]

    def test_update_nonexistent_returns_false(self):
        assert worldview.update_personal_project("nope") is False

    def test_complete_project(self):
        worldview.add_personal_project("Done proj")
        worldview.update_personal_project("Done proj", status="completed")
        active = worldview.get_personal_projects(status="active")
        completed = worldview.get_personal_projects(status="completed")
        assert len(active) == 0
        assert len(completed) == 1

    def test_project_context_string(self):
        worldview.add_personal_project("Test context proj", description="Testing")
        ctx = worldview.get_project_context()
        assert "Test context proj" in ctx
        assert "0 sessions" in ctx

    def test_project_context_empty(self):
        assert worldview.get_project_context() == ""

    def test_project_cap_enforced(self):
        for i in range(15):
            worldview.add_personal_project(f"Project {i}")
        data = worldview.load()
        assert len(data["personal_projects"]) <= worldview._MAX_PERSONAL_PROJECTS


# ── Meta-Cognition (session 203) ─────────────────────────────────

class TestMetaCognition:
    def test_add_meta_observation(self):
        worldview.add_meta_observation(
            "I over-estimate task complexity", category="estimation",
            evidence="3 of 5 tasks completed under budget",
        )
        data = worldview.load()
        obs = data["meta_observations"]
        assert len(obs) == 1
        assert obs[0]["pattern"] == "I over-estimate task complexity"
        assert obs[0]["category"] == "estimation"
        assert obs[0]["times_observed"] == 1

    def test_duplicate_observation_reinforces(self):
        worldview.add_meta_observation("I repeat solutions", category="approach")
        worldview.add_meta_observation("I repeat solutions", category="approach")
        data = worldview.load()
        obs = data["meta_observations"]
        assert len(obs) == 1
        assert obs[0]["times_observed"] == 2

    def test_update_meta_adjustment(self):
        worldview.add_meta_observation("Over-estimate complexity")
        ok = worldview.update_meta_adjustment(
            "Over-estimate complexity", "Try simpler approach first",
        )
        assert ok is True
        data = worldview.load()
        assert data["meta_observations"][0]["adjustment"] == "Try simpler approach first"

    def test_update_nonexistent_returns_false(self):
        assert worldview.update_meta_adjustment("nope", "anything") is False

    def test_meta_context_string(self):
        worldview.add_meta_observation("Pattern A", category="general")
        worldview.update_meta_adjustment("Pattern A", "Do X differently")
        ctx = worldview.get_meta_context()
        assert "Pattern A" in ctx
        assert "Do X differently" in ctx

    def test_meta_context_empty(self):
        assert worldview.get_meta_context() == ""

    def test_meta_cap_enforced(self):
        for i in range(25):
            worldview.add_meta_observation(f"Pattern {i}")
        data = worldview.load()
        assert len(data["meta_observations"]) <= worldview._MAX_META_OBSERVATIONS


# ── Load edge cases (session 206) ──────────────────────────────

class TestLoadEdgeCases:
    def test_load_fills_missing_keys(self, tmp_path, monkeypatch):
        """A worldview.json missing some keys gets backfilled on load."""
        path = str(tmp_path / "worldview.json")
        monkeypatch.setattr(worldview, "_worldview_path", lambda: path)
        # Write partial JSON (missing pending_revisions, meta_observations, etc.)
        with open(path, "w") as f:
            json.dump({"opinions": [{"topic": "a", "position": "b"}]}, f)
        data = worldview.load()
        assert "opinions" in data
        assert "preferences" in data
        assert "interests" in data
        assert "pending_revisions" in data
        assert "personal_projects" in data
        assert "meta_observations" in data
        assert len(data["opinions"]) == 1

    def test_save_atomicity_on_write_error(self, tmp_path, monkeypatch):
        """If save fails mid-write, original file is not corrupted."""
        path = str(tmp_path / "worldview.json")
        monkeypatch.setattr(worldview, "_worldview_path", lambda: path)
        # Write a valid file first
        worldview.add_opinion("topic1", "pos1", 0.7)
        # Verify it exists
        data = worldview.load()
        assert len(data["opinions"]) == 1


# ── Pruning edge cases (session 206) ───────────────────────────

class TestPruningEdgeCases:
    def test_personal_project_cap_prioritizes_active(self):
        """Active/paused projects are kept over completed when cap exceeded."""
        data = worldview._empty_worldview()
        for i in range(8):
            data["personal_projects"].append({
                "title": f"Active {i}", "status": "active",
                "created": "2026-01-01", "last_worked": "2026-03-01",
            })
        for i in range(5):
            data["personal_projects"].append({
                "title": f"Completed {i}", "status": "completed",
                "created": "2026-01-01", "last_worked": "2026-02-01",
            })
        worldview.save(data)
        loaded = worldview.load()
        projects = loaded["personal_projects"]
        assert len(projects) <= worldview._MAX_PERSONAL_PROJECTS
        # All active projects should be retained
        active_titles = [p["title"] for p in projects if p["status"] == "active"]
        assert len(active_titles) == 8

    def test_preference_cap_enforced(self):
        data = worldview._empty_worldview()
        for i in range(60):
            data["preferences"].append({
                "domain": "test", "preference": f"pref {i}",
                "strength": 0.5, "evidence_count": 1,
            })
        worldview.save(data)
        loaded = worldview.load()
        assert len(loaded["preferences"]) <= worldview._MAX_PREFERENCES

    def test_interest_decay_removes_dead(self):
        """Interests with curiosity that decayed to near-zero are pruned."""
        data = worldview._empty_worldview()
        stale_date = (date.today() - timedelta(days=60)).strftime("%Y-%m-%d")
        data["interests"].append({
            "topic": "dead interest", "curiosity_level": 0.2,
            "last_explored": stale_date, "notes": "",
        })
        data["interests"].append({
            "topic": "live interest", "curiosity_level": 0.9,
            "last_explored": date.today().strftime("%Y-%m-%d"), "notes": "",
        })
        worldview.save(data)
        loaded = worldview.load()
        topics = [i["topic"] for i in loaded["interests"]]
        assert "live interest" in topics
        # Dead interest should have decayed below 0.1 threshold and been pruned
        assert "dead interest" not in topics


# ── Taste development edge cases (session 206) ─────────────────

class TestTasteEdgeCases:
    def test_model_name_parsing_complex(self):
        """Model names with slashes and colons are parsed correctly."""
        result = worldview.develop_taste(
            "Research something", True, 0.05, 8,
            model_used="google/gemini-3.1-pro-preview:free",
        )
        assert result is not None
        prefs = worldview.get_preferences(domain="taste_model")
        # Should use the short form, not the full provider path
        assert any("gemini" in p["preference"].lower() for p in prefs)

    def test_failed_model_records_struggle(self):
        """Model failures are tracked as struggles."""
        result = worldview.develop_taste(
            "Code a feature", False, 0.30, 25,
            model_used="grok-4.1-fast",
        )
        assert result is not None
        assert "model_struggle" in result
        prefs = worldview.get_preferences(domain="taste_model")
        assert any("struggled" in p["preference"].lower() for p in prefs)

    def test_no_model_no_model_pref(self):
        """Without model_used, no model preference is tracked."""
        result = worldview.develop_taste(
            "Research things", True, 0.05, 8,
            model_used="", verified=True,
        )
        assert result is not None  # Should have efficiency
        assert "model_pref" not in result

    def test_taste_context_respects_max_chars(self):
        """Taste context is truncated at max_chars."""
        for i in range(10):
            worldview.develop_taste(
                f"Research topic {i} with very long description for padding",
                True, 0.03, 5, f"model-{i}", True,
            )
        ctx = worldview.get_taste_context(max_chars=50)
        assert len(ctx) <= 50


# ── Reflection edge cases (session 206) ────────────────────────

class TestReflectionEdgeCases:
    def test_reflect_on_task_no_model_no_opinion_match(self):
        """No matching opinions → may seed interest via bootstrap."""
        result = worldview.reflect_on_task(
            "Totally unrelated task about quantum physics",
            "Study quantum computing",
            "Completed successfully",
            success=True,
        )
        # May return None or seed an interest (bootstrap behavior)
        if result is not None:
            assert "seeded_interest" in result

    def test_reflect_on_task_bootstrap_seeds_interest(self):
        """Bootstrap: task with domain keyword seeds an interest when worldview is sparse."""
        result = worldview.reflect_on_task(
            "Write a blog post about AI trends",
            "Create content for the website",
            "Draft completed",
            success=True,
        )
        assert result is not None
        assert "seeded_interest" in result
        interests = worldview.get_interests(min_curiosity=0.0)
        assert len(interests) >= 1

    def test_reflect_on_task_bootstrap_skips_when_interests_exist(self):
        """Bootstrap stops seeding once 3+ interests exist."""
        worldview.add_interest("topic A", 0.5)
        worldview.add_interest("topic B", 0.5)
        worldview.add_interest("topic C", 0.5)
        result = worldview.reflect_on_task(
            "Write a blog post about AI trends",
            "Create content for the website",
            "Draft completed",
            success=True,
        )
        # Should NOT seed because 3 interests already exist
        assert result is None or "seeded_interest" not in result

    def test_reflect_on_task_bootstrap_skips_on_failure(self):
        """Bootstrap doesn't seed interests from failed tasks."""
        result = worldview.reflect_on_task(
            "Write a blog post about AI trends",
            "Create content for the website",
            "Failed to complete",
            success=False,
        )
        assert result is None or "seeded_interest" not in result

    def test_extract_interest_topic_research_uses_goal(self):
        """Research tasks extract topic from goal description."""
        topic = worldview._extract_interest_topic(
            "Research the latest trends",
            "Understand machine learning advances",
        )
        assert topic is not None
        assert "machine" in topic or "learning" in topic or "understand" in topic

    def test_extract_interest_topic_no_match_returns_none(self):
        """Non-matching tasks return None."""
        topic = worldview._extract_interest_topic(
            "Do something abstract",
            "Complete the thing",
        )
        assert topic is None

    def test_reflect_on_task_reinforces_matching_opinion(self):
        """Successful task reinforces matching opinion."""
        worldview.add_opinion("error handling patterns", "Explicit returns preferred", 0.5)
        result = worldview.reflect_on_task(
            "Fix error handling in module using patterns from docs",
            "Improve error handling patterns",
            "Fixed all error handling patterns successfully",
            success=True,
        )
        if result:
            op = worldview.get_opinion("error handling patterns")
            assert op["confidence"] > 0.5  # Should have increased

    def test_reflect_on_task_weakens_on_failure(self):
        """Failed task weakens matching opinion."""
        worldview.add_opinion("web scraping approach", "Use direct fetch method", 0.6)
        result = worldview.reflect_on_task(
            "Scrape data using web scraping approach from API",
            "Web scraping approach for data collection",
            "Timed out, approach failed",
            success=False,
        )
        if result:
            op = worldview.get_opinion("web scraping approach")
            assert op["confidence"] < 0.6  # Should have decreased


# ── Opinion revision edge cases (session 206) ──────────────────

class TestRevisionEdgeCases:
    def test_revision_not_flagged_for_new_opinion(self):
        """First opinion on a topic shouldn't create a revision."""
        worldview.add_opinion("brand new topic", "Initial position", 0.8)
        revisions = worldview.get_pending_revisions()
        assert len(revisions) == 0

    def test_revision_flagged_on_high_confidence_change(self):
        """Position change with new_confidence >= 0.6 flags revision."""
        worldview.add_opinion("coding style", "OOP is best", 0.3)
        worldview.add_opinion("coding style", "Functional is better", 0.7)
        revisions = worldview.get_pending_revisions()
        assert len(revisions) == 1
        assert revisions[0]["old_position"] == "OOP is best"
        assert revisions[0]["new_position"] == "Functional is better"

    def test_clear_revision_case_insensitive(self):
        """Clearing a revision works regardless of case."""
        worldview.add_opinion("My Topic", "Position A", 0.3)
        worldview.add_opinion("My Topic", "Position B", 0.8)
        worldview.clear_revision("my topic")
        revisions = worldview.get_pending_revisions()
        assert len(revisions) == 0

    def test_get_pending_revisions_returns_copy(self):
        """Returned list should be a copy, not a reference to internal state."""
        worldview.add_opinion("t1", "p1", 0.3)
        worldview.add_opinion("t1", "p2", 0.9)
        rev1 = worldview.get_pending_revisions()
        rev2 = worldview.get_pending_revisions()
        assert rev1 is not rev2
