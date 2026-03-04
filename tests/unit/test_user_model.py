"""Unit tests for UserModel — facts, preferences, context generation, persistence.

Tests the new facts category (session 93) alongside existing preference/correction/
pattern/style categories, signal extraction, dedup, pruning, and context methods.
"""

import json
import pytest
from pathlib import Path

from src.core.user_model import (
    UserModel,
    extract_user_signals,
    get_user_model,
    _reset_for_testing,
    _text_similar,
    _MAX_PER_CATEGORY,
    _MAX_FACTS,
)


@pytest.fixture
def tmp_model(tmp_path):
    """Fresh UserModel with a temp data directory."""
    _reset_for_testing()
    return UserModel(data_dir=tmp_path)


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the singleton before/after each test."""
    _reset_for_testing()
    yield
    _reset_for_testing()


# ── Adding entries ───────────────────────────────────────────────


class TestAddFact:

    def test_add_fact_stores_entry(self, tmp_model):
        tmp_model.add_fact("32 years old")
        assert len(tmp_model.facts) == 1
        assert tmp_model.facts[0]["text"] == "32 years old"
        assert tmp_model.facts[0]["source"] == "router"

    def test_add_fact_custom_source(self, tmp_model):
        tmp_model.add_fact("Plays guitar", source="manual")
        assert tmp_model.facts[0]["source"] == "manual"

    def test_add_fact_strips_whitespace(self, tmp_model):
        tmp_model.add_fact("  5'10\" tall  ")
        assert tmp_model.facts[0]["text"] == "5'10\" tall"

    def test_add_fact_ignores_empty(self, tmp_model):
        tmp_model.add_fact("")
        tmp_model.add_fact("   ")
        assert len(tmp_model.facts) == 0

    def test_add_fact_dedup(self, tmp_model):
        tmp_model.add_fact("32 years old")
        tmp_model.add_fact("32 years old")
        assert len(tmp_model.facts) == 1

    def test_add_fact_similar_dedup(self, tmp_model):
        tmp_model.add_fact("Jesse is 32 years old")
        tmp_model.add_fact("Jesse is 32 years old now")
        # High word overlap → deduped
        assert len(tmp_model.facts) == 1

    def test_add_fact_different_facts_kept(self, tmp_model):
        tmp_model.add_fact("32 years old")
        tmp_model.add_fact("5'10\" tall")
        tmp_model.add_fact("Weighs 175 lbs")
        assert len(tmp_model.facts) == 3


class TestAddPreference:

    def test_add_preference(self, tmp_model):
        tmp_model.add_preference("Prefers tabs over spaces")
        assert len(tmp_model.preferences) == 1


class TestAddCorrection:

    def test_add_correction(self, tmp_model):
        tmp_model.add_correction("Don't use bullet points")
        assert len(tmp_model.corrections) == 1


# ── Pruning ──────────────────────────────────────────────────────


class TestPruning:

    def test_facts_pruned_at_max(self, tmp_model):
        # Bypass dedup by adding entries directly
        for i in range(_MAX_FACTS + 10):
            tmp_model.facts.append({"text": f"fact-{i}", "source": "test", "ts": ""})
        tmp_model._dirty = True
        # Trigger prune via _add
        tmp_model.add_fact("final fact that triggers prune")
        assert len(tmp_model.facts) == _MAX_FACTS

    def test_preferences_pruned_at_max(self, tmp_model):
        for i in range(_MAX_PER_CATEGORY + 5):
            tmp_model.preferences.append({"text": f"pref-{i}", "source": "test", "ts": ""})
        tmp_model._dirty = True
        tmp_model.add_preference("final pref that triggers prune")
        assert len(tmp_model.preferences) == _MAX_PER_CATEGORY

    def test_facts_higher_cap_than_others(self):
        assert _MAX_FACTS > _MAX_PER_CATEGORY


# ── Persistence ──────────────────────────────────────────────────


class TestPersistence:

    def test_save_and_load(self, tmp_path):
        m1 = UserModel(data_dir=tmp_path)
        m1.add_fact("32 years old")
        m1.add_preference("Prefers dark mode")
        m1.add_correction("Don't use emojis")
        m1.add_pattern("Approves health goals")
        m1.add_style_note("Casual tone")

        m2 = UserModel(data_dir=tmp_path)
        assert len(m2.facts) == 1
        assert m2.facts[0]["text"] == "32 years old"
        assert len(m2.preferences) == 1
        assert len(m2.corrections) == 1
        assert len(m2.patterns) == 1
        assert len(m2.style) == 1

    def test_version_2_saved(self, tmp_path):
        m = UserModel(data_dir=tmp_path)
        m.add_fact("test")
        data = json.loads((tmp_path / "user_model.json").read_text())
        assert data["version"] == 2

    def test_backward_compat_no_facts(self, tmp_path):
        """Loading a v1 file (no facts key) works fine."""
        v1_data = {
            "version": 1,
            "preferences": [{"text": "dark mode", "source": "router", "ts": "2025-01-01"}],
            "corrections": [],
            "patterns": [],
            "style": [],
        }
        (tmp_path / "user_model.json").write_text(json.dumps(v1_data))
        m = UserModel(data_dir=tmp_path)
        assert len(m.facts) == 0
        assert len(m.preferences) == 1

    def test_no_save_if_not_dirty(self, tmp_path):
        m = UserModel(data_dir=tmp_path)
        file = tmp_path / "user_model.json"
        assert not file.exists()
        m.save()  # Nothing dirty → no file
        assert not file.exists()


# ── Context methods ──────────────────────────────────────────────


class TestGetContextForChat:

    def test_empty_model_returns_empty(self, tmp_model):
        assert tmp_model.get_context_for_chat() == ""

    def test_includes_facts(self, tmp_model):
        tmp_model.add_fact("32 years old")
        tmp_model.add_fact("Half Filipino")
        ctx = tmp_model.get_context_for_chat()
        assert "32 years old" in ctx
        assert "Half Filipino" in ctx

    def test_includes_preferences(self, tmp_model):
        tmp_model.add_preference("Prefers dark mode")
        ctx = tmp_model.get_context_for_chat()
        assert "Prefers: Prefers dark mode" in ctx

    def test_includes_style(self, tmp_model):
        tmp_model.add_style_note("Casual tone")
        ctx = tmp_model.get_context_for_chat()
        assert "Casual tone" in ctx

    def test_header(self, tmp_model):
        tmp_model.add_fact("test fact")
        ctx = tmp_model.get_context_for_chat()
        assert ctx.startswith("What you know about Jesse:")

    def test_truncated_at_2000(self, tmp_model):
        for i in range(50):
            tmp_model.add_fact(f"This is a long unique fact number {i} with extra details and padding text")
        ctx = tmp_model.get_context_for_chat()
        assert len(ctx) <= 2000


class TestGetContextForRouter:

    def test_includes_facts(self, tmp_model):
        tmp_model.add_fact("32 years old")
        ctx = tmp_model.get_context_for_router()
        assert "Fact: 32 years old" in ctx

    def test_includes_preferences(self, tmp_model):
        tmp_model.add_preference("Tabs over spaces")
        ctx = tmp_model.get_context_for_router()
        assert "Prefers: Tabs over spaces" in ctx

    def test_truncated_at_600(self, tmp_model):
        for i in range(30):
            tmp_model.add_fact(f"Long fact number {i} with a lot of detail")
        ctx = tmp_model.get_context_for_router()
        assert len(ctx) <= 600


class TestGetContextForDiscovery:

    def test_includes_facts(self, tmp_model):
        tmp_model.add_fact("Works in finance")
        ctx = tmp_model.get_context_for_discovery()
        assert "Works in finance" in ctx

    def test_truncated_at_400(self, tmp_model):
        for i in range(30):
            tmp_model.add_fact(f"Long fact number {i} with a lot of detail and padding")
        ctx = tmp_model.get_context_for_discovery()
        assert len(ctx) <= 400


class TestGetContextForFormatter:

    def test_includes_style(self, tmp_model):
        tmp_model.add_style_note("Keep it casual")
        ctx = tmp_model.get_context_for_formatter()
        assert "Keep it casual" in ctx


# ── get_all ──────────────────────────────────────────────────────


class TestGetAll:

    def test_includes_all_categories(self, tmp_model):
        tmp_model.add_fact("32 years old")
        tmp_model.add_preference("dark mode")
        tmp_model.add_correction("no emojis")
        tmp_model.add_pattern("approves health")
        tmp_model.add_style_note("casual")
        all_data = tmp_model.get_all()
        assert "facts" in all_data
        assert len(all_data["facts"]) == 1
        assert len(all_data["preferences"]) == 1
        assert len(all_data["corrections"]) == 1
        assert len(all_data["patterns"]) == 1
        assert len(all_data["style"]) == 1


# ── Signal extraction ────────────────────────────────────────────


class TestExtractUserSignals:

    def test_fact_signal(self, tmp_model):
        # Inject the tmp_model as the singleton
        import src.core.user_model as um_mod
        um_mod._instance = tmp_model

        extract_user_signals("I'm 32 years old", {
            "intent": "greeting",
            "user_signals": [
                {"type": "fact", "text": "32 years old"},
            ],
        })
        assert len(tmp_model.facts) == 1
        assert tmp_model.facts[0]["text"] == "32 years old"

    def test_preference_signal(self, tmp_model):
        import src.core.user_model as um_mod
        um_mod._instance = tmp_model

        extract_user_signals("I prefer dark mode", {
            "user_signals": [
                {"type": "preference", "text": "Prefers dark mode"},
            ],
        })
        assert len(tmp_model.preferences) == 1

    def test_multiple_signals(self, tmp_model):
        import src.core.user_model as um_mod
        um_mod._instance = tmp_model

        extract_user_signals("I'm 32, 5'10\", and I prefer dark mode", {
            "user_signals": [
                {"type": "fact", "text": "32 years old"},
                {"type": "fact", "text": "5'10\" tall"},
                {"type": "preference", "text": "Prefers dark mode"},
            ],
        })
        assert len(tmp_model.facts) == 2
        assert len(tmp_model.preferences) == 1

    def test_cap_at_5_signals(self, tmp_model):
        import src.core.user_model as um_mod
        um_mod._instance = tmp_model

        extract_user_signals("lots of info", {
            "user_signals": [
                {"type": "fact", "text": f"Fact {i}"} for i in range(10)
            ],
        })
        assert len(tmp_model.facts) == 5  # Capped at 5

    def test_no_signals_field(self, tmp_model):
        """No crash when user_signals is missing; returns empty list."""
        result = extract_user_signals("hello", {"intent": "greeting"})
        assert result == []

    def test_empty_signals(self, tmp_model):
        """Empty signals list returns empty config_requests."""
        result = extract_user_signals("hello", {"user_signals": []})
        assert result == []

    def test_unknown_signal_type_ignored(self, tmp_model):
        import src.core.user_model as um_mod
        um_mod._instance = tmp_model

        extract_user_signals("test", {
            "user_signals": [{"type": "unknown", "text": "something"}],
        })
        assert len(tmp_model.facts) == 0
        assert len(tmp_model.preferences) == 0

    def test_config_request_returns_list(self, tmp_model):
        """config_request signals are returned as a list."""
        import src.core.user_model as um_mod
        um_mod._instance = tmp_model

        result = extract_user_signals("add humor to your prime directive", {
            "user_signals": [
                {"type": "config_request", "text": "Add humor to prime directive"},
            ],
        })
        assert result == ["Add humor to prime directive"]

    def test_config_request_also_stored_as_correction(self, tmp_model):
        """config_request signals are also stored as corrections for memory."""
        import src.core.user_model as um_mod
        um_mod._instance = tmp_model

        extract_user_signals("add humor to your prime directive", {
            "user_signals": [
                {"type": "config_request", "text": "Add humor to prime directive"},
            ],
        })
        assert len(tmp_model.corrections) == 1
        assert tmp_model.corrections[0]["source"] == "config_request"

    def test_config_request_mixed_with_regular_signals(self, tmp_model):
        """config_request and regular signals processed together."""
        import src.core.user_model as um_mod
        um_mod._instance = tmp_model

        result = extract_user_signals("I'm 32, also update your rules", {
            "user_signals": [
                {"type": "fact", "text": "32 years old"},
                {"type": "config_request", "text": "Update rules.yaml"},
            ],
        })
        assert len(tmp_model.facts) == 1
        assert result == ["Update rules.yaml"]

    def test_no_config_requests_returns_empty(self, tmp_model):
        """Normal signals return empty config_requests list."""
        import src.core.user_model as um_mod
        um_mod._instance = tmp_model

        result = extract_user_signals("I prefer dark mode", {
            "user_signals": [
                {"type": "preference", "text": "Prefers dark mode"},
            ],
        })
        assert result == []

    def test_no_signals_returns_empty_list(self, tmp_model):
        """Missing user_signals field returns empty list (not None)."""
        result = extract_user_signals("hello", {"intent": "greeting"})
        assert result == []


# ── Text similarity ──────────────────────────────────────────────


class TestTextSimilar:

    def test_identical(self):
        assert _text_similar("hello world", "hello world") is True

    def test_different(self):
        assert _text_similar("hello world", "goodbye moon") is False

    def test_high_overlap(self):
        assert _text_similar("Jesse is 32 years old", "Jesse is 32 years old now") is True

    def test_empty_strings(self):
        assert _text_similar("", "hello") is False
        assert _text_similar("hello", "") is False
        assert _text_similar("", "") is False


# ── Categories constant ──────────────────────────────────────────


class TestCategories:

    def test_categories_includes_facts(self):
        assert "facts" in UserModel._CATEGORIES

    def test_all_categories_present(self):
        expected = {"facts", "preferences", "corrections", "patterns", "style", "tone_feedback"}
        assert set(UserModel._CATEGORIES) == expected


# ── Tone feedback (session 98) ──────────────────────────────────


class TestToneFeedback:

    def test_add_tone_feedback(self, tmp_model):
        tmp_model.add_tone_feedback("positive", "Great banter about woodworking")
        assert len(tmp_model.tone_feedback) == 1
        assert tmp_model.tone_feedback[0]["text"] == "positive: Great banter about woodworking"
        assert tmp_model.tone_feedback[0]["source"] == "reaction"

    def test_tone_feedback_persists(self, tmp_model):
        tmp_model.add_tone_feedback("negative", "Too formal response")
        data = json.loads(tmp_model._file.read_text())
        assert "tone_feedback" in data
        assert len(data["tone_feedback"]) == 1

    def test_tone_guidance_insufficient_data(self, tmp_model):
        tmp_model.add_tone_feedback("positive", "msg1")
        assert tmp_model._get_tone_guidance() == ""

    def test_tone_guidance_positive_trend(self, tmp_model):
        for i in range(8):
            tmp_model.add_tone_feedback("positive", f"msg{i}")
        for i in range(2):
            tmp_model.add_tone_feedback("negative", f"bad{i}")
        guidance = tmp_model._get_tone_guidance()
        assert "keep it up" in guidance.lower()

    def test_tone_guidance_negative_trend(self, tmp_model):
        for i in range(2):
            tmp_model.add_tone_feedback("positive", f"msg{i}")
        for i in range(8):
            tmp_model.add_tone_feedback("negative", f"bad{i}")
        guidance = tmp_model._get_tone_guidance()
        assert "concise" in guidance.lower() or "direct" in guidance.lower()

    def test_tone_guidance_mixed_signals(self, tmp_model):
        for i in range(5):
            tmp_model.add_tone_feedback("positive", f"good{i}")
        for i in range(5):
            tmp_model.add_tone_feedback("negative", f"bad{i}")
        assert tmp_model._get_tone_guidance() == ""

    def test_tone_in_get_all(self, tmp_model):
        tmp_model.add_tone_feedback("positive", "test")
        all_data = tmp_model.get_all()
        assert "tone_feedback" in all_data
        assert len(all_data["tone_feedback"]) == 1

    def test_tone_in_chat_context(self, tmp_model):
        # With enough feedback, tone guidance appears in chat context
        for i in range(8):
            tmp_model.add_tone_feedback("positive", f"msg{i}")
        context = tmp_model.get_context_for_chat()
        assert "Tone:" in context


class TestSuggestionStyleAndOutputFormat:
    """Tests for suggestion_style and output_format fields (session 184)."""

    @pytest.fixture
    def tmp_model(self, tmp_path):
        _reset_for_testing()
        model = UserModel(data_dir=tmp_path)
        yield model
        _reset_for_testing()

    def test_suggestion_style_default_empty(self, tmp_model):
        assert tmp_model.suggestion_style == ""

    def test_set_suggestion_style(self, tmp_model):
        tmp_model.set_suggestion_style("Prefers practical life content")
        assert tmp_model.suggestion_style == "Prefers practical life content"

    def test_set_suggestion_style_persists(self, tmp_path):
        _reset_for_testing()
        m1 = UserModel(data_dir=tmp_path)
        m1.set_suggestion_style("Prefers practical life content")
        # Load fresh
        m2 = UserModel(data_dir=tmp_path)
        assert m2.suggestion_style == "Prefers practical life content"
        _reset_for_testing()

    def test_output_format_default_empty(self, tmp_model):
        assert tmp_model.output_format == ""

    def test_set_output_format(self, tmp_model):
        tmp_model.set_output_format("Discord text 2000 chars max")
        assert tmp_model.output_format == "Discord text 2000 chars max"

    def test_set_output_format_persists(self, tmp_path):
        _reset_for_testing()
        m1 = UserModel(data_dir=tmp_path)
        m1.set_output_format("Discord text 2000 chars max; HTML for longer reports")
        m2 = UserModel(data_dir=tmp_path)
        assert m2.output_format == "Discord text 2000 chars max; HTML for longer reports"
        _reset_for_testing()

    def test_get_suggestion_context(self, tmp_model):
        tmp_model.set_suggestion_style("Prefers practical life content")
        tmp_model.set_output_format("Discord text 2000 chars max")
        ctx = tmp_model.get_suggestion_context()
        assert "SUGGESTION STYLE" in ctx
        assert "OUTPUT FORMAT" in ctx
        assert "practical life content" in ctx

    def test_get_suggestion_context_empty(self, tmp_model):
        ctx = tmp_model.get_suggestion_context()
        assert ctx == ""

    def test_get_output_format_context(self, tmp_model):
        tmp_model.set_output_format("Discord text 2000 chars max")
        ctx = tmp_model.get_output_format_context()
        assert "OUTPUT FORMAT PREFERENCE" in ctx
        assert "Discord text" in ctx

    def test_get_output_format_context_empty(self, tmp_model):
        ctx = tmp_model.get_output_format_context()
        assert ctx == ""

    def test_get_all_includes_new_fields(self, tmp_model):
        tmp_model.set_suggestion_style("life content")
        tmp_model.set_output_format("discord text")
        all_data = tmp_model.get_all()
        assert all_data["suggestion_style"] == "life content"
        assert all_data["output_format"] == "discord text"

    def test_no_op_when_same_value(self, tmp_model):
        tmp_model.set_suggestion_style("test")
        tmp_model._dirty = False
        tmp_model.set_suggestion_style("test")  # Same value
        assert not tmp_model._dirty  # Should not mark dirty
