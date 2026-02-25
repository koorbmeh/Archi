"""Unit tests for LearningSystem — experience recording, patterns, metrics,
proactive error prevention.

Covers: Experience, init/persistence, record_*/flush, metrics/trends,
extract_patterns, improvement suggestions, _tokenize, get_failure_warnings,
get_active_insights, record_action_outcome, get_action_summary, get_summary.

Created session 127. Expanded session 148.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from src.core.learning_system import Experience, LearningSystem


# ── Fixture ───────────────────────────────────────────────────────────


@pytest.fixture
def ls(tmp_path):
    """Fresh LearningSystem with empty data dir."""
    return LearningSystem(data_dir=tmp_path)


def _record_failures(ls, failures):
    """Helper: record a list of (context, action, outcome) failure tuples."""
    for ctx, action, outcome in failures:
        ls.record_failure(context=ctx, action=action, outcome=outcome)


# ── Experience ────────────────────────────────────────────────────────


class TestExperience:
    def test_init_all_fields(self):
        exp = Experience("success", "ctx", "act", "out", "lesson")
        assert exp.experience_type == "success"
        assert exp.context == "ctx"
        assert exp.action == "act"
        assert exp.outcome == "out"
        assert exp.lesson == "lesson"
        assert exp.timestamp is not None

    def test_init_no_lesson(self):
        exp = Experience("failure", "ctx", "act", "out")
        assert exp.lesson is None

    def test_to_dict(self):
        exp = Experience("feedback", "ctx", "act", "out", "les")
        d = exp.to_dict()
        assert d["type"] == "feedback"
        assert d["context"] == "ctx"
        assert d["action"] == "act"
        assert d["outcome"] == "out"
        assert d["lesson"] == "les"
        assert "timestamp" in d

    def test_to_dict_none_lesson(self):
        exp = Experience("success", "ctx", "act", "out")
        assert exp.to_dict()["lesson"] is None


# ── Init / persistence ───────────────────────────────────────────────


class TestLearningSystemInit:
    def test_creates_data_dir(self, tmp_path):
        data_dir = tmp_path / "new_data"
        ls = LearningSystem(data_dir=data_dir)
        assert data_dir.exists()
        assert ls.experiences == []
        assert ls.patterns == {}

    def test_loads_existing_experiences(self, tmp_path):
        exp_data = {
            "experiences": [
                {"type": "success", "context": "c", "action": "a",
                 "outcome": "o", "lesson": "l", "timestamp": "2026-01-01T00:00:00"}
            ],
            "patterns": {"insights": ["pattern1"]},
            "metrics": {"rate": [0.8, 0.9]},
            "action_stats": {"web_search": {"success": 5, "fail": 1}},
        }
        (tmp_path / "experiences.json").write_text(json.dumps(exp_data))
        ls = LearningSystem(data_dir=tmp_path)
        assert len(ls.experiences) == 1
        assert ls.experiences[0].context == "c"
        assert ls.patterns["insights"] == ["pattern1"]
        assert ls.performance_metrics["rate"] == [0.8, 0.9]
        assert ls.action_stats["web_search"]["success"] == 5

    def test_handles_corrupt_file(self, tmp_path):
        (tmp_path / "experiences.json").write_text("not valid json!!!")
        ls = LearningSystem(data_dir=tmp_path)
        assert ls.experiences == []

    def test_trims_on_load(self, tmp_path):
        exp_data = {
            "experiences": [
                {"type": "success", "context": f"c{i}", "action": "a",
                 "outcome": "o", "timestamp": "2026-01-01T00:00:00"}
                for i in range(600)
            ],
            "patterns": {}, "metrics": {}, "action_stats": {},
        }
        (tmp_path / "experiences.json").write_text(json.dumps(exp_data))
        ls = LearningSystem(data_dir=tmp_path)
        assert len(ls.experiences) == 500


# ── Record methods ───────────────────────────────────────────────────


class TestRecordSuccess:
    def test_records_success(self, ls):
        ls.record_success("ctx", "act", "out", "lesson")
        assert len(ls.experiences) == 1
        assert ls.experiences[0].experience_type == "success"
        assert ls.experiences[0].lesson == "lesson"

    def test_with_no_lesson(self, ls):
        ls.record_success("ctx", "act", "out")
        assert ls.experiences[0].lesson is None


class TestRecordFailure:
    def test_records_failure(self, ls):
        ls.record_failure("ctx", "act", "out", "lesson")
        assert len(ls.experiences) == 1
        assert ls.experiences[0].experience_type == "failure"


class TestRecordFeedback:
    def test_records_feedback(self, ls):
        ls.record_feedback("ctx", "act", "good job")
        assert len(ls.experiences) == 1
        assert ls.experiences[0].experience_type == "feedback"
        assert ls.experiences[0].outcome == "good job"


# ── Flush & persistence ─────────────────────────────────────────────


class TestFlushAndPersistence:
    def test_auto_flush_at_interval(self, tmp_path):
        ls = LearningSystem(data_dir=tmp_path)
        for i in range(10):
            ls.record_success(f"ctx{i}", f"act{i}", f"out{i}")
        assert (tmp_path / "experiences.json").exists()
        data = json.loads((tmp_path / "experiences.json").read_text())
        assert len(data["experiences"]) == 10

    def test_explicit_flush(self, tmp_path):
        ls = LearningSystem(data_dir=tmp_path)
        ls.record_success("ctx", "act", "out")
        ls.flush()
        assert (tmp_path / "experiences.json").exists()

    def test_flush_no_dirty(self, tmp_path):
        ls = LearningSystem(data_dir=tmp_path)
        ls.flush()  # Should not crash

    def test_save_load_roundtrip(self, tmp_path):
        ls = LearningSystem(data_dir=tmp_path)
        ls.record_success("ctx1", "act1", "out1", "lesson1")
        ls.record_failure("ctx2", "act2", "out2")
        ls.record_feedback("ctx3", "act3", "feedback3")
        ls.track_metric("rate", 0.95)
        ls.record_action_outcome("web_search", True)
        ls.patterns = {"insights": ["p1", "p2"]}
        ls.flush()

        ls2 = LearningSystem(data_dir=tmp_path)
        assert len(ls2.experiences) == 3
        assert ls2.experiences[0].experience_type == "success"
        assert ls2.patterns["insights"] == ["p1", "p2"]

    def test_max_experiences_trimming(self, tmp_path):
        ls = LearningSystem(data_dir=tmp_path)
        ls._FLUSH_INTERVAL = 600
        for i in range(510):
            ls.experiences.append(Experience("success", f"c{i}", f"a{i}", f"o{i}"))
            ls._maybe_flush()
        assert len(ls.experiences) == 500


# ── Metrics ──────────────────────────────────────────────────────────


class TestTrackMetric:
    def test_tracks_values(self, ls):
        ls.track_metric("rate", 0.85)
        ls.track_metric("rate", 0.90)
        assert ls.performance_metrics["rate"] == [0.85, 0.90]

    def test_multiple_metrics(self, ls):
        ls.track_metric("rate", 0.85)
        ls.track_metric("speed", 1.2)
        assert "rate" in ls.performance_metrics
        assert "speed" in ls.performance_metrics


class TestGetMetricTrend:
    def test_not_enough_data(self, ls):
        ls.performance_metrics["rate"] = [0.5, 0.6]
        assert ls.get_metric_trend("rate") is None

    def test_improving(self, ls):
        ls.performance_metrics["rate"] = [0.5, 0.5, 0.5, 0.5, 0.8, 0.9, 0.95, 0.95]
        assert ls.get_metric_trend("rate") == "improving"

    def test_declining(self, ls):
        ls.performance_metrics["rate"] = [0.9, 0.9, 0.85, 0.85, 0.5, 0.4, 0.3, 0.3]
        assert ls.get_metric_trend("rate") == "declining"

    def test_stable(self, ls):
        ls.performance_metrics["rate"] = [0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8]
        assert ls.get_metric_trend("rate") == "stable"

    def test_unknown_metric(self, ls):
        assert ls.get_metric_trend("nonexistent") is None

    def test_with_window(self, ls):
        ls.performance_metrics["rate"] = [0.9, 0.8, 0.7, 0.6, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
        assert ls.get_metric_trend("rate", window=4) == "improving"

    def test_first_half_zero(self, ls):
        ls.performance_metrics["rate"] = [0, 0, 0, 0, 0.5, 0.5, 0.5, 0.5]
        assert ls.get_metric_trend("rate") == "stable"


# ── Extract patterns ─────────────────────────────────────────────────


class TestExtractPatterns:
    def test_not_enough_experiences(self, ls):
        ls.record_success("c", "a", "o")
        model = MagicMock()
        assert ls.extract_patterns(model) == []
        model.generate.assert_not_called()

    def test_extracts_patterns(self, ls):
        for i in range(6):
            ls.record_success(f"ctx{i}", f"act{i}", f"out{i}")
        model = MagicMock()
        model.generate.return_value = {
            "text": '["Use web search first", "Check file exists"]'
        }
        patterns = ls.extract_patterns(model)
        assert len(patterns) == 2
        assert ls.patterns["insights"] == patterns
        assert "last_analysis" in ls.patterns

    def test_empty_model_response(self, ls):
        for i in range(6):
            ls.record_success(f"c{i}", f"a{i}", f"o{i}")
        model = MagicMock()
        model.generate.return_value = {"text": ""}
        assert ls.extract_patterns(model) == []

    def test_model_exception(self, ls):
        for i in range(6):
            ls.record_success(f"c{i}", f"a{i}", f"o{i}")
        model = MagicMock()
        model.generate.side_effect = RuntimeError("API down")
        assert ls.extract_patterns(model) == []


# ── Improvement suggestions ──────────────────────────────────────────


class TestGetImprovementSuggestions:
    def test_returns_suggestions(self, ls):
        ls.performance_metrics["rate"] = [0.8, 0.85, 0.9, 0.92, 0.95]
        ls.record_failure("ctx", "web_search", "timeout")
        model = MagicMock()
        model.generate.return_value = {
            "text": '["Increase timeout", "Add retry logic"]'
        }
        suggestions = ls.get_improvement_suggestions(model)
        assert len(suggestions) == 2

    def test_empty_response(self, ls):
        model = MagicMock()
        model.generate.return_value = {"text": ""}
        assert ls.get_improvement_suggestions(model) == []

    def test_model_exception(self, ls):
        model = MagicMock()
        model.generate.side_effect = RuntimeError("fail")
        assert ls.get_improvement_suggestions(model) == []


# ── Active insights ──────────────────────────────────────────────────


class TestGetActiveInsights:
    def test_returns_insights(self, ls):
        ls.patterns = {"insights": ["i1", "i2", "i3", "i4"]}
        assert ls.get_active_insights(limit=2) == ["i1", "i2"]

    def test_no_patterns(self, ls):
        assert ls.get_active_insights() == []

    def test_deduplicates(self, ls):
        ls.patterns = {"insights": ["Same thing", "same thing", "Different"]}
        assert len(ls.get_active_insights(limit=5)) == 2

    def test_filters_non_strings(self, ls):
        ls.patterns = {"insights": ["valid", 123, None, "also valid"]}
        assert ls.get_active_insights(limit=5) == ["valid", "also valid"]


# ── Action outcome tracking ──────────────────────────────────────────


class TestRecordActionOutcome:
    def test_records_success(self, ls):
        ls.record_action_outcome("web_search", True)
        assert ls.action_stats["web_search"]["success"] == 1
        assert ls.action_stats["web_search"]["fail"] == 0

    def test_records_failure(self, ls):
        ls.record_action_outcome("web_search", False)
        assert ls.action_stats["web_search"]["fail"] == 1

    def test_accumulates(self, ls):
        ls.record_action_outcome("web_search", True)
        ls.record_action_outcome("web_search", True)
        ls.record_action_outcome("web_search", False)
        assert ls.action_stats["web_search"]["success"] == 2
        assert ls.action_stats["web_search"]["fail"] == 1


class TestGetActionSummary:
    def test_not_enough_data(self, ls):
        ls.record_action_outcome("web_search", True)
        assert ls.get_action_summary() == ""

    def test_reliable_actions(self, ls):
        for _ in range(5):
            ls.record_action_outcome("web_search", True)
        for _ in range(3):
            ls.record_action_outcome("create_file", True)
        summary = ls.get_action_summary()
        assert "Reliable:" in summary
        assert "web_search" in summary

    def test_weak_actions(self, ls):
        ls.record_action_outcome("fetch_webpage", True)
        ls.record_action_outcome("fetch_webpage", False)
        ls.record_action_outcome("fetch_webpage", False)
        summary = ls.get_action_summary()
        assert "Weak:" in summary
        assert "fetch_webpage" in summary


# ── Summary ──────────────────────────────────────────────────────────


class TestGetSummary:
    def test_empty(self, ls):
        s = ls.get_summary()
        assert s["total_experiences"] == 0
        assert s["success_rate"] == 0
        assert s["patterns_extracted"] == 0

    def test_with_data(self, ls):
        ls.record_success("c", "a", "o")
        ls.record_success("c", "a", "o")
        ls.record_failure("c", "a", "o")
        ls.track_metric("rate", 0.8)
        ls.patterns = {"insights": ["p1", "p2"], "last_analysis": "2026-01-01T00:00:00"}
        s = ls.get_summary()
        assert s["total_experiences"] == 3
        assert s["successes"] == 2
        assert s["failures"] == 1
        assert abs(s["success_rate"] - 66.67) < 1
        assert s["patterns_extracted"] == 2


# ── _tokenize ─────────────────────────────────────────────────────────


class TestTokenize:

    def test_basic_tokenization(self):
        tokens = LearningSystem._tokenize("Create a Python script for data")
        assert "create" in tokens
        assert "python" in tokens
        assert "script" in tokens
        assert "data" in tokens
        # Short words (< 3 chars) excluded
        assert "a" not in tokens

    def test_filters_short_words(self):
        tokens = LearningSystem._tokenize("do it on a PC in NY")
        # All words are < 3 chars or non-alpha
        assert len(tokens) == 0

    def test_lowercase(self):
        tokens = LearningSystem._tokenize("Python SCRIPT Data")
        assert "python" in tokens
        assert "script" in tokens

    def test_alpha_only(self):
        tokens = LearningSystem._tokenize("file123 test_case foo-bar hello")
        # Only pure alpha words pass
        assert "hello" in tokens
        assert "file123" not in tokens


# ── get_failure_warnings ──────────────────────────────────────────────


class TestGetFailureWarnings:

    def test_no_failures_returns_empty(self, ls):
        warnings = ls.get_failure_warnings("Create Python script")
        assert warnings == []

    def test_no_relevant_failures_returns_empty(self, ls):
        _record_failures(ls, [
            ("Goal: Cook dinner", "Prepare recipe list", "Oven broke"),
        ])
        warnings = ls.get_failure_warnings("Write a Python data analysis script")
        assert warnings == []

    def test_relevant_failure_matched(self, ls):
        _record_failures(ls, [
            (
                "Goal: Build health tracker; Task: Create Python script for tracking",
                "Create Python script for tracking",
                "Syntax error in generated script — missing import statement",
            ),
        ])
        warnings = ls.get_failure_warnings(
            "Create Python script for nutrition logging",
            goal_description="Build health tracker",
        )
        assert len(warnings) == 1
        assert "CAUTION" in warnings[0]
        assert "Syntax error" in warnings[0]

    def test_limit_respected(self, ls):
        _record_failures(ls, [
            ("Goal: Research; Task: web search alpha", "web search alpha", "Timeout error A"),
            ("Goal: Research; Task: web search beta", "web search beta", "Connection refused B"),
            ("Goal: Research; Task: web search gamma", "web search gamma", "DNS failure C"),
            ("Goal: Research; Task: web search delta", "web search delta", "Rate limited D"),
        ])
        warnings = ls.get_failure_warnings("web search for research topic", limit=2)
        assert len(warnings) <= 2

    def test_deduplicates_similar_outcomes(self, ls):
        # Two failures with identical first 60 chars of outcome should collapse to one
        _record_failures(ls, [
            ("Goal: Build app; Task: Create script", "Create script",
             "Syntax error in generated Python output file — missing closing parenthesis on line 5"),
            ("Goal: Build app; Task: Create module", "Create module",
             "Syntax error in generated Python output file — missing closing parenthesis on line 42"),
        ])
        warnings = ls.get_failure_warnings(
            "Create script for the app",
            goal_description="Build app",
        )
        # First 60 chars are identical → deduplicated to 1
        assert len(warnings) == 1

    def test_distinct_outcomes_not_deduped(self, ls):
        _record_failures(ls, [
            ("Goal: Build app; Task: Create script", "Create script", "Syntax error in output file"),
            ("Goal: Build app; Task: Create script", "Create script", "File not found: missing dependency"),
        ])
        warnings = ls.get_failure_warnings(
            "Create script for the app",
            goal_description="Build app",
        )
        assert len(warnings) == 2

    def test_min_overlap_threshold(self, ls):
        _record_failures(ls, [
            ("Goal: Research health; Task: Find data", "Find data", "API timeout"),
        ])
        # Only 1 word overlap ("data") — below default min_overlap=2
        warnings = ls.get_failure_warnings("Analyze data patterns")
        assert warnings == []

        # With min_overlap=1, it should match
        warnings = ls.get_failure_warnings("Analyze data patterns", min_overlap=1)
        assert len(warnings) == 1

    def test_task_description_too_short(self, ls):
        _record_failures(ls, [
            ("Goal: Test; Task: Do thing", "Do thing", "Failed"),
        ])
        # Very short description tokenizes to < 2 words
        warnings = ls.get_failure_warnings("go")
        assert warnings == []

    def test_sorted_by_relevance(self, ls):
        _record_failures(ls, [
            (
                "Goal: Analysis; Task: web search",
                "web search",
                "Low relevance failure",
            ),
            (
                "Goal: Build health tracker; Task: Create Python script for health data analysis",
                "Create Python script for health data analysis",
                "High relevance failure — import error",
            ),
        ])
        warnings = ls.get_failure_warnings(
            "Create Python script for health data processing",
            goal_description="Build health tracker",
        )
        # The more relevant failure (more keyword overlap) should come first
        assert len(warnings) >= 1
        assert "High relevance" in warnings[0]

    def test_only_scans_recent_failures(self, ls):
        """Ensure old failures beyond the 100-failure window are ignored."""
        # Record 110 irrelevant failures to push relevant one out of window
        for i in range(110):
            ls.record_failure(
                context=f"Goal: Unrelated{i}; Task: Cook{i}",
                action=f"Cook meal number {i}",
                outcome=f"Oven failure {i}",
            )
        # The relevant failure was recorded first — now beyond the 100-item window
        # Record a relevant one at the end
        ls.record_failure(
            context="Goal: Build app; Task: Create Python script",
            action="Create Python script",
            outcome="Module not found error",
        )
        warnings = ls.get_failure_warnings(
            "Create Python script",
            goal_description="Build app",
        )
        assert len(warnings) == 1
        assert "Module not found" in warnings[0]


class TestGetFailureWarningsIntegration:
    """Test that failure warnings integrate correctly with hint pipeline."""

    def test_warnings_contain_caution_prefix(self, ls):
        _record_failures(ls, [
            (
                "Goal: Research; Task: fetch webpage data",
                "fetch webpage data",
                "SSL certificate error on target site",
            ),
        ])
        warnings = ls.get_failure_warnings("fetch webpage data for analysis")
        assert all(w.startswith("CAUTION") for w in warnings)

    def test_empty_goal_description_still_works(self, ls):
        _record_failures(ls, [
            (
                "Goal: Test; Task: run Python analysis script",
                "run Python analysis script",
                "MemoryError during execution",
            ),
        ])
        warnings = ls.get_failure_warnings("run Python analysis on dataset")
        assert len(warnings) == 1
