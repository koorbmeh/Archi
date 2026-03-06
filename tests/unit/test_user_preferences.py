"""Unit tests for src/core/user_preferences.py.

Covers: UserPreferences class (persistence, add_note, dedup, querying,
format_for_prompt), detect_preference_signals(), extract_and_record(),
_signal_to_category(), singleton lifecycle.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.user_preferences import (
    CATEGORIES,
    UserPreferences,
    _reset_for_testing,
    _signal_to_category,
    detect_preference_signals,
    extract_and_record,
    get_preferences,
)


# ---- Fixtures ----

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset singleton before and after each test."""
    _reset_for_testing()
    yield
    _reset_for_testing()


@pytest.fixture
def prefs(tmp_path):
    """Create a UserPreferences instance with a temp data dir."""
    return UserPreferences(data_dir=tmp_path)


# ---- Singleton ----

class TestSingleton:
    """Singleton lifecycle tests."""

    def test_get_preferences_returns_instance(self, tmp_path):
        with patch("src.core.user_preferences._base_path", return_value=tmp_path):
            p1 = get_preferences()
            p2 = get_preferences()
        assert p1 is p2

    def test_reset_clears_singleton(self, tmp_path):
        with patch("src.core.user_preferences._base_path", return_value=tmp_path):
            p1 = get_preferences()
            _reset_for_testing()
            p2 = get_preferences()
        assert p1 is not p2


# ---- Persistence ----

class TestPersistence:
    """Load/save roundtrip tests."""

    def test_init_no_file(self, prefs):
        """New instance with no file starts empty."""
        assert len(prefs.notes) == 0

    def test_save_and_reload(self, tmp_path):
        """Notes survive save/reload cycle."""
        p1 = UserPreferences(data_dir=tmp_path)
        p1.add_note("health", "Takes creatine daily", tags=["creatine"])
        p1.save()
        p2 = UserPreferences(data_dir=tmp_path)
        assert len(p2.notes) == 1
        assert p2.notes[0]["text"] == "Takes creatine daily"

    def test_corrupted_file_handled(self, tmp_path):
        """Corrupted JSON loads gracefully."""
        (tmp_path / "user_preferences.json").write_text("{bad json!!")
        p = UserPreferences(data_dir=tmp_path)
        assert len(p.notes) == 0

    def test_save_updates_last_updated(self, prefs):
        assert prefs.last_updated == ""
        prefs.save()
        assert prefs.last_updated != ""

    def test_atomic_write_cleans_temp(self, tmp_path):
        """Temp file should not linger after successful save."""
        p = UserPreferences(data_dir=tmp_path)
        p.add_note("general", "test note")
        p.save()
        assert not (tmp_path / "user_preferences.tmp").exists()


# ---- add_note ----

class TestAddNote:
    """Tests for add_note()."""

    def test_basic_add(self, prefs):
        nid = prefs.add_note("health", "Started magnesium before bed", tags=["magnesium", "sleep"])
        assert nid is not None
        assert nid.startswith("note_")
        assert len(prefs.notes) == 1

    def test_empty_text_rejected(self, prefs):
        assert prefs.add_note("health", "") is None
        assert prefs.add_note("health", "   ") is None
        assert len(prefs.notes) == 0

    def test_invalid_category_defaults_to_general(self, prefs):
        nid = prefs.add_note("invalid_cat", "some note")
        assert prefs.notes[0]["category"] == "general"

    def test_category_normalized(self, prefs):
        prefs.add_note("  Health  ", "note text")
        assert prefs.notes[0]["category"] == "health"

    def test_tags_normalized(self, prefs):
        prefs.add_note("health", "test", tags=["  Creatine  ", "", "SLEEP"])
        assert prefs.notes[0]["tags"] == ["creatine", "sleep"]

    def test_source_recorded(self, prefs):
        prefs.add_note("health", "test", source="discord")
        assert prefs.notes[0]["source"] == "discord"

    def test_flush_interval(self, tmp_path):
        """Notes auto-save after _FLUSH_INTERVAL additions."""
        p = UserPreferences(data_dir=tmp_path)
        p.add_note("general", "note 1")
        p.add_note("general", "note 2")
        # Not saved yet (interval is 3)
        p2 = UserPreferences(data_dir=tmp_path)
        assert len(p2.notes) == 0
        # Third add triggers flush
        p.add_note("general", "note 3")
        p3 = UserPreferences(data_dir=tmp_path)
        assert len(p3.notes) == 3


# ---- Deduplication ----

class TestDeduplication:
    """Tests for _find_duplicate() via add_note()."""

    def test_high_tag_overlap_updates_existing(self, prefs):
        nid1 = prefs.add_note("health", "Takes creatine 5g daily", tags=["creatine", "dose"])
        nid2 = prefs.add_note("health", "Increased creatine to 10g", tags=["creatine", "dose", "increase"])
        # Should update existing, not create new
        assert nid1 == nid2
        assert len(prefs.notes) == 1
        assert "10g" in prefs.notes[0]["text"]

    def test_different_category_no_dedup(self, prefs):
        prefs.add_note("health", "Takes creatine", tags=["creatine"])
        prefs.add_note("supplement", "Takes creatine", tags=["creatine"])
        assert len(prefs.notes) == 2

    def test_low_overlap_creates_new(self, prefs):
        prefs.add_note("health", "Takes creatine", tags=["creatine", "muscle"])
        prefs.add_note("health", "Sleep is poor", tags=["sleep", "insomnia"])
        assert len(prefs.notes) == 2

    def test_no_tags_no_dedup(self, prefs):
        prefs.add_note("general", "note one")
        prefs.add_note("general", "note two")
        assert len(prefs.notes) == 2

    def test_superseded_notes_skipped(self, prefs):
        prefs.add_note("health", "Old note", tags=["creatine", "dose"])
        prefs.notes[0]["superseded_by"] = "newer_note"
        prefs.add_note("health", "New creatine note", tags=["creatine", "dose"])
        # Should create new since existing is superseded
        assert len(prefs.notes) == 2


# ---- Querying ----

class TestGetRelevant:
    """Tests for get_relevant()."""

    def test_tag_matching(self, prefs):
        prefs.add_note("health", "Takes creatine 5g", tags=["creatine", "muscle"])
        prefs.add_note("health", "Sleeps at 10pm", tags=["sleep", "schedule"])
        results = prefs.get_relevant("creatine dosing")
        assert len(results) == 1
        assert "creatine" in results[0]["text"].lower()

    def test_text_matching(self, prefs):
        prefs.add_note("health", "Protein shake after workout")
        results = prefs.get_relevant("workout routine")
        assert len(results) == 1

    def test_tags_weighted_higher(self, prefs):
        prefs.add_note("health", "Some note about creatine", tags=["creatine"])
        prefs.add_note("health", "The word creatine appears here too")
        results = prefs.get_relevant("creatine")
        # Tag match (3 points) should rank above text match (1 point)
        assert results[0]["tags"] == ["creatine"]

    def test_limit_respected(self, prefs):
        for i in range(10):
            prefs.add_note("general", f"note about topic {i}", tags=[f"tag{i}", "shared"])
        results = prefs.get_relevant("shared", limit=3)
        assert len(results) == 3

    def test_superseded_excluded(self, prefs):
        prefs.add_note("health", "Old creatine note", tags=["creatine"])
        prefs.notes[0]["superseded_by"] = "newer"
        results = prefs.get_relevant("creatine")
        assert len(results) == 0

    def test_no_match(self, prefs):
        prefs.add_note("health", "creatine", tags=["creatine"])
        results = prefs.get_relevant("python programming")
        assert len(results) == 0


class TestGetRecent:
    """Tests for get_recent()."""

    def test_returns_newest_first(self, prefs):
        prefs.add_note("general", "first")
        prefs.add_note("general", "second")
        prefs.add_note("general", "third")
        # Ensure deterministic ordering (datetime.now() can have poor
        # resolution on Windows, yielding identical timestamps)
        for i, note in enumerate(prefs.notes):
            note["created_at"] = f"2026-01-01T00:00:0{i}"
        recent = prefs.get_recent(limit=2)
        assert len(recent) == 2
        assert recent[0]["text"] == "third"

    def test_excludes_superseded(self, prefs):
        prefs.add_note("general", "old note")
        prefs.notes[0]["superseded_by"] = "newer"
        prefs.add_note("general", "active note")
        recent = prefs.get_recent()
        assert len(recent) == 1
        assert recent[0]["text"] == "active note"


class TestGetAllForCategory:
    """Tests for get_all_for_category()."""

    def test_filters_by_category(self, prefs):
        prefs.add_note("health", "health note")
        prefs.add_note("fitness", "fitness note")
        prefs.add_note("health", "another health note")
        results = prefs.get_all_for_category("health")
        assert len(results) == 2

    def test_excludes_superseded(self, prefs):
        prefs.add_note("health", "old")
        prefs.notes[0]["superseded_by"] = "newer"
        results = prefs.get_all_for_category("health")
        assert len(results) == 0


# ---- format_for_prompt ----

class TestFormatForPrompt:
    """Tests for format_for_prompt()."""

    def test_empty_when_no_notes(self, prefs):
        assert prefs.format_for_prompt() == ""

    def test_includes_user_name(self, prefs):
        prefs.add_note("health", "Likes morning walks")
        with patch("src.core.user_preferences.get_user_name", return_value="Jesse"):
            result = prefs.format_for_prompt()
        assert "Jesse" in result

    def test_truncates_long_notes(self, prefs):
        long_text = "A" * 200
        prefs.add_note("general", long_text)
        result = prefs.format_for_prompt()
        assert "..." in result

    def test_budget_limit(self, prefs):
        for i in range(20):
            prefs.add_note("general", f"This is a moderately long note about topic {i} that takes some space")
        result = prefs.format_for_prompt()
        assert len(result) <= 700  # ~600 char budget + header

    def test_skips_empty_text_notes(self, prefs):
        prefs.notes.append({"text": "", "created_at": "2026-01-01"})
        prefs.add_note("general", "real note")
        result = prefs.format_for_prompt()
        assert "real note" in result


# ---- detect_preference_signals ----

class TestDetectPreferenceSignals:
    """Tests for detect_preference_signals()."""

    def test_short_message_ignored(self):
        assert detect_preference_signals("hi") == []
        assert detect_preference_signals("") == []

    def test_supplement_detection_with_context(self):
        signals = detect_preference_signals(
            "I started taking creatine for muscle recovery."
        )
        assert len(signals) >= 1
        assert any(s["pattern"] == "supplement" for s in signals)

    def test_supplement_without_health_context_ignored(self):
        """Supplement pattern without health keywords should not match."""
        signals = detect_preference_signals(
            "I started using the new IDE for programming."
        )
        supplement_signals = [s for s in signals if s["pattern"] == "supplement"]
        assert len(supplement_signals) == 0

    def test_reaction_pattern(self):
        signals = detect_preference_signals(
            "Caffeine gives me anxiety and jitters."
        )
        assert any(s["pattern"] == "reaction" for s in signals)

    def test_preference_pattern(self):
        signals = detect_preference_signals(
            "I prefer working out in the morning."
        )
        assert any(s["pattern"] == "preference" for s in signals)

    def test_experience_pattern(self):
        signals = detect_preference_signals(
            "I noticed that magnesium helps me sleep better."
        )
        assert any(s["pattern"] == "experience" for s in signals)

    def test_max_5_signals(self):
        # A message with many patterns
        msg = (
            "I love coffee. I hate tea. I prefer water. "
            "I like juice. I enjoy milk. I dislike soda. "
            "I avoid energy drinks."
        )
        signals = detect_preference_signals(msg)
        assert len(signals) <= 5

    def test_short_match_text_ignored(self):
        """Matches with text <= 3 chars should be ignored."""
        signals = detect_preference_signals("I like it, but that's ok.")
        # "it" is too short (<=3 chars), should not match preference
        pref_signals = [s for s in signals if s["pattern"] == "preference"]
        assert all(len(s["match_text"]) > 3 for s in pref_signals)


class TestSignalToCategory:
    """Tests for _signal_to_category()."""

    def test_known_patterns(self):
        assert _signal_to_category("supplement") == "supplement"
        assert _signal_to_category("reaction") == "reaction"
        assert _signal_to_category("preference") == "preference"
        assert _signal_to_category("experience") == "health"

    def test_unknown_pattern(self):
        assert _signal_to_category("unknown") == "general"


# ---- extract_and_record ----

class TestExtractAndRecord:
    """Tests for extract_and_record()."""

    def test_no_signals_returns_empty(self, tmp_path):
        with patch("src.core.user_preferences._base_path", return_value=tmp_path):
            result = extract_and_record("Hello, how are you?")
        assert result == []

    def test_rule_based_recording(self, tmp_path):
        with patch("src.core.user_preferences._base_path", return_value=tmp_path):
            result = extract_and_record(
                "I prefer working out in the morning.",
                source="discord",
            )
        assert len(result) >= 1

    def test_model_refinement_used_when_available(self, tmp_path):
        mock_router = MagicMock()
        mock_router.generate.return_value = {
            "text": '[{"category": "preference", "text": "Prefers morning workouts", "tags": ["workout", "morning"]}]'
        }
        with patch("src.core.user_preferences._base_path", return_value=tmp_path):
            result = extract_and_record(
                "I prefer working out in the morning.",
                router=mock_router,
            )
        assert len(result) >= 1
        mock_router.generate.assert_called_once()

    def test_model_failure_falls_back_to_rules(self, tmp_path):
        mock_router = MagicMock()
        mock_router.generate.side_effect = RuntimeError("API down")
        with patch("src.core.user_preferences._base_path", return_value=tmp_path):
            result = extract_and_record(
                "I prefer working out in the morning.",
                router=mock_router,
            )
        # Should still record via rule-based fallback
        assert len(result) >= 1

    def test_model_returns_empty_array(self, tmp_path):
        mock_router = MagicMock()
        mock_router.generate.return_value = {"text": "[]"}
        with patch("src.core.user_preferences._base_path", return_value=tmp_path):
            result = extract_and_record(
                "I prefer working out in the morning.",
                router=mock_router,
            )
        # Empty model result means no model note IDs, falls through to rule-based
        assert len(result) >= 1

    def test_too_many_signals_skips_model(self, tmp_path):
        """More than 3 signals bypasses model refinement."""
        mock_router = MagicMock()
        msg = (
            "I love running. I hate swimming. I prefer cycling. "
            "I enjoy hiking. All are great exercises."
        )
        with patch("src.core.user_preferences._base_path", return_value=tmp_path):
            signals = detect_preference_signals(msg)
            if len(signals) > 3:
                extract_and_record(msg, router=mock_router)
                mock_router.generate.assert_not_called()


# ---- Categories constant ----

class TestCategories:
    """Tests for CATEGORIES tuple."""

    def test_expected_categories_present(self):
        expected = {"supplement", "health", "fitness", "food", "preference", "reaction", "general"}
        assert expected.issubset(set(CATEGORIES))

    def test_no_duplicates(self):
        assert len(CATEGORIES) == len(set(CATEGORIES))
