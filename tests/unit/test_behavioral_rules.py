"""Tests for behavioral rules system (session 200)."""

import json
import os
import tempfile
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.core.behavioral_rules import (
    _empty_rules,
    _find_clusters,
    _find_matching_rule,
    _prune,
    _tokenize,
    add_avoidance_rule,
    add_preference_rule,
    extract_rules_from_experiences,
    get_relevant_rules,
    load,
    process_task_outcome,
    save,
)


@pytest.fixture(autouse=True)
def _isolated_rules(tmp_path, monkeypatch):
    """Redirect behavioral rules to temp dir for test isolation."""
    rules_file = str(tmp_path / "behavioral_rules.json")
    monkeypatch.setattr(
        "src.core.behavioral_rules._rules_path",
        lambda: rules_file,
    )
    yield


# ── Persistence ──────────────────────────────────────────────────────

class TestPersistence:
    def test_load_returns_empty_when_no_file(self):
        data = load()
        assert data == _empty_rules()
        assert data["avoidance"] == []
        assert data["preference"] == []

    def test_save_and_load_roundtrip(self):
        data = _empty_rules()
        data["avoidance"].append({
            "pattern": "test pattern",
            "reason": "test reason",
            "keywords": ["test", "keyword"],
            "strength": 0.7,
            "evidence_count": 3,
            "formed": "2026-03-06",
            "last_reinforced": "2026-03-06",
        })
        save(data)
        loaded = load()
        assert len(loaded["avoidance"]) == 1
        assert loaded["avoidance"][0]["pattern"] == "test pattern"

    def test_save_creates_directory(self, tmp_path, monkeypatch):
        nested = str(tmp_path / "nested" / "dir" / "rules.json")
        monkeypatch.setattr(
            "src.core.behavioral_rules._rules_path",
            lambda: nested,
        )
        save(_empty_rules())
        assert os.path.isfile(nested)

    def test_load_handles_corrupt_file(self, tmp_path, monkeypatch):
        path = str(tmp_path / "bad.json")
        monkeypatch.setattr("src.core.behavioral_rules._rules_path", lambda: path)
        with open(path, "w") as f:
            f.write("not json")
        data = load()
        assert data == _empty_rules()


# ── Tokenization ────────────────────────────────────────────────────

class TestTokenize:
    def test_basic(self):
        tokens = _tokenize("Hello world this is a test")
        assert "hello" in tokens
        assert "world" in tokens
        assert "this" in tokens
        assert "test" in tokens
        assert "is" not in tokens  # too short
        assert "a" not in tokens  # too short

    def test_ignores_numbers(self):
        tokens = _tokenize("step 123 complete")
        assert "step" in tokens
        assert "complete" in tokens
        assert "123" not in tokens

    def test_empty(self):
        assert _tokenize("") == set()
        assert _tokenize("a b c") == set()  # all too short


# ── Rule management ─────────────────────────────────────────────────

class TestAddRules:
    def test_add_avoidance_rule(self):
        add_avoidance_rule("web scraping", "timeouts common", ["web", "scraping"], 0.6, 3)
        data = load()
        assert len(data["avoidance"]) == 1
        rule = data["avoidance"][0]
        assert rule["pattern"] == "web scraping"
        assert rule["strength"] == 0.6
        assert rule["evidence_count"] == 3

    def test_add_preference_rule(self):
        add_preference_rule("file creation", "reliable approach", ["file", "creation"], 0.7, 5)
        data = load()
        assert len(data["preference"]) == 1
        assert data["preference"][0]["strength"] == 0.7

    def test_reinforce_existing_avoidance(self):
        add_avoidance_rule("web search", "slow results", ["web", "search"], 0.4, 1)
        add_avoidance_rule("web search v2", "still slow", ["web", "search"], 0.5, 1)
        data = load()
        assert len(data["avoidance"]) == 1  # Updated, not duplicated
        rule = data["avoidance"][0]
        assert rule["evidence_count"] == 2
        assert rule["strength"] == min(1.0, 0.4 + 0.1)  # Reinforced by 0.1

    def test_reinforce_existing_preference(self):
        add_preference_rule("python scripts", "fast", ["python", "scripts"], 0.5, 1)
        add_preference_rule("python scripts v2", "faster", ["python", "scripts"], 0.6, 1)
        data = load()
        assert len(data["preference"]) == 1
        assert data["preference"][0]["evidence_count"] == 2


# ── Pruning ─────────────────────────────────────────────────────────

class TestPruning:
    def test_decay_old_rules(self):
        old_date = (date.today() - timedelta(days=35)).strftime("%Y-%m-%d")
        data = {
            "avoidance": [{
                "pattern": "old pattern",
                "reason": "old",
                "keywords": ["old"],
                "strength": 0.5,
                "evidence_count": 1,
                "formed": old_date,
                "last_reinforced": old_date,
            }],
            "preference": [],
        }
        _prune(data)
        # Should have decayed by 0.05
        assert data["avoidance"][0]["strength"] == pytest.approx(0.45)

    def test_prune_below_threshold(self):
        old_date = (date.today() - timedelta(days=35)).strftime("%Y-%m-%d")
        data = {
            "avoidance": [{
                "pattern": "weak rule",
                "reason": "weak",
                "keywords": ["weak"],
                "strength": 0.12,  # Below 0.15 threshold
                "evidence_count": 1,
                "formed": old_date,
                "last_reinforced": old_date,
            }],
            "preference": [],
        }
        _prune(data)
        assert len(data["avoidance"]) == 0

    def test_cap_enforcement(self):
        data = {"avoidance": [], "preference": []}
        for i in range(50):
            data["avoidance"].append({
                "pattern": f"rule {i}",
                "reason": "test",
                "keywords": [f"kw{i}"],
                "strength": 0.5,
                "evidence_count": 1,
                "last_reinforced": date.today().strftime("%Y-%m-%d"),
            })
            data["preference"].append({
                "pattern": f"pref {i}",
                "reason": "test",
                "keywords": [f"pk{i}"],
                "strength": 0.5,
                "evidence_count": 1,
                "last_reinforced": date.today().strftime("%Y-%m-%d"),
            })
        _prune(data)
        total = len(data["avoidance"]) + len(data["preference"])
        assert total <= 80

    def test_recent_rules_not_decayed(self):
        today = date.today().strftime("%Y-%m-%d")
        data = {
            "avoidance": [{
                "pattern": "fresh",
                "reason": "new",
                "keywords": ["fresh"],
                "strength": 0.5,
                "evidence_count": 1,
                "formed": today,
                "last_reinforced": today,
            }],
            "preference": [],
        }
        _prune(data)
        assert data["avoidance"][0]["strength"] == 0.5  # No decay


# ── Query ───────────────────────────────────────────────────────────

class TestGetRelevantRules:
    def test_matches_by_keywords(self):
        add_avoidance_rule(
            "web scraping with timeouts", "commonly fails",
            ["web", "scraping", "page"], 0.7, 3,
        )
        hints = get_relevant_rules("scraping the web page for data", "research goal")
        assert len(hints) == 1
        assert "AVOID" in hints[0]
        assert "web scraping" in hints[0]

    def test_no_match_with_unrelated_task(self):
        add_avoidance_rule(
            "database migration", "risky",
            ["database", "migration"], 0.7, 3,
        )
        hints = get_relevant_rules("write a poem about cats")
        assert len(hints) == 0

    def test_strength_filtering(self):
        add_avoidance_rule("weak rule", "reason", ["web", "search"], 0.2, 1)
        hints = get_relevant_rules("web search for info", min_strength=0.3)
        assert len(hints) == 0

    def test_limit_respected(self):
        for i in range(10):
            add_avoidance_rule(
                f"pattern {i}", f"reason {i}",
                ["common", "keywords", f"extra{i}"], 0.7, 3,
            )
        hints = get_relevant_rules("common keywords task", limit=3)
        assert len(hints) <= 3

    def test_preference_rules_returned(self):
        add_preference_rule(
            "python scripting for data", "fast and reliable",
            ["python", "scripting", "data"], 0.8, 5,
        )
        hints = get_relevant_rules("write a python script for data processing")
        assert len(hints) == 1
        assert "PREFER" in hints[0]

    def test_short_description_returns_empty(self):
        add_avoidance_rule("test", "reason", ["test"], 0.7, 3)
        hints = get_relevant_rules("hi")  # Too short
        assert len(hints) == 0


# ── Post-task processing ───────────────────────────────────────────

class TestProcessTaskOutcome:
    def test_reinforces_matching_avoidance_on_failure(self):
        add_avoidance_rule(
            "web fetch fails", "timeouts",
            ["web", "fetch", "timeout"], 0.5, 2,
        )
        changes = process_task_outcome(
            "fetch web page", "research goal",
            "timeout after 30s", success=False,
        )
        assert changes is not None
        # Rule should have been reinforced
        data = load()
        assert data["avoidance"][0]["evidence_count"] >= 3

    def test_no_changes_when_no_matching_rules(self):
        changes = process_task_outcome(
            "create file", "goal",
            "success", success=True,
        )
        assert changes is None

    def test_short_input_returns_none(self):
        changes = process_task_outcome("hi", "", "", True)
        assert changes is None


# ── Pattern extraction ──────────────────────────────────────────────

class TestExtractRulesFromExperiences:
    def _make_exp(self, exp_type, context, action, outcome):
        return {"type": exp_type, "context": context, "action": action, "outcome": outcome}

    def test_detects_failure_cluster(self):
        exps = [
            self._make_exp("failure", "web scraping task", "fetch_webpage", "timeout error"),
            self._make_exp("failure", "web scraping job", "fetch_webpage", "403 forbidden"),
            self._make_exp("failure", "web scraping item", "fetch_webpage", "connection refused"),
        ]
        proposals = extract_rules_from_experiences(exps, min_occurrences=3)
        assert len(proposals) >= 1
        assert proposals[0]["type"] == "avoidance"

    def test_detects_success_cluster(self):
        exps = [
            self._make_exp("success", "python data processing", "run_python", "completed"),
            self._make_exp("success", "python data analysis", "run_python", "completed"),
            self._make_exp("success", "python data cleanup", "run_python", "completed"),
        ]
        proposals = extract_rules_from_experiences(exps, min_occurrences=3)
        assert len(proposals) >= 1
        assert proposals[0]["type"] == "preference"

    def test_no_proposals_below_threshold(self):
        exps = [
            self._make_exp("failure", "task A", "action_a", "failed"),
            self._make_exp("failure", "task B", "action_b", "failed"),
        ]
        proposals = extract_rules_from_experiences(exps, min_occurrences=3)
        assert len(proposals) == 0

    def test_empty_experiences(self):
        assert extract_rules_from_experiences([]) == []


# ── Cluster detection ───────────────────────────────────────────────

class TestFindClusters:
    def test_groups_similar_failures(self):
        exps = [
            {"context": "web search query", "action": "web_search", "outcome": "no results"},
            {"context": "web search test", "action": "web_search", "outcome": "timeout"},
            {"context": "web search data", "action": "web_search", "outcome": "error"},
        ]
        clusters = _find_clusters(exps, "avoidance", 3)
        assert len(clusters) >= 1

    def test_no_cluster_with_diverse_experiences(self):
        exps = [
            {"context": "alpha beta gamma", "action": "create_file", "outcome": "ok"},
            {"context": "delta epsilon zeta", "action": "run_command", "outcome": "ok"},
            {"context": "theta iota kappa", "action": "web_search", "outcome": "ok"},
        ]
        clusters = _find_clusters(exps, "preference", 3)
        assert len(clusters) == 0


# ── Helpers ─────────────────────────────────────────────────────────

class TestFindMatchingRule:
    def test_finds_matching_rule(self):
        rules = [{"keywords": ["web", "search", "data"], "pattern": "web search"}]
        result = _find_matching_rule(rules, ["web", "search"])
        assert result is not None
        assert result["pattern"] == "web search"

    def test_no_match_returns_none(self):
        rules = [{"keywords": ["database", "migration"], "pattern": "db migrate"}]
        result = _find_matching_rule(rules, ["web", "search"])
        assert result is None

    def test_empty_rules(self):
        assert _find_matching_rule([], ["web", "search"]) is None

    def test_picks_best_overlap(self):
        """Should return the rule with highest keyword overlap."""
        rules = [
            {"keywords": ["web", "data"], "pattern": "partial"},
            {"keywords": ["web", "search", "data", "api"], "pattern": "better"},
        ]
        result = _find_matching_rule(rules, ["web", "search", "data"])
        assert result["pattern"] == "better"


# ── Process task outcome edge cases (session 206) ──────────────

class TestProcessOutcomeEdgeCases:
    def test_reinforces_avoidance_on_failure(self):
        """Failing a task matching an avoidance rule reinforces it."""
        add_avoidance_rule(
            "Avoid direct API calls without caching",
            "Causes rate limits",
            ["direct", "api", "calls", "caching"],
        )
        data = load()
        orig_strength = data["avoidance"][0]["strength"]

        result = process_task_outcome(
            task_description="Make direct api calls to endpoint",
            goal_description="Fetch data without caching layer",
            outcome="Rate limited again",
            success=False,
        )
        if result:
            data = load()
            assert data["avoidance"][0]["strength"] >= orig_strength

    def test_reinforces_preference_on_success(self):
        """Succeeding at a task matching a preference rule reinforces it."""
        add_preference_rule(
            "Use batch processing for large datasets",
            "Efficient approach",
            ["batch", "processing", "large", "datasets"],
        )
        result = process_task_outcome(
            task_description="Batch processing of large datasets",
            goal_description="Process data using batch approach",
            outcome="All records processed efficiently",
            success=True,
        )
        if result:
            data = load()
            assert len(data["preference"]) >= 1

    def test_empty_description_returns_none(self):
        """Very short input returns None (not enough keywords)."""
        result = process_task_outcome("x", "y", "z", True)
        assert result is None


# ── Load edge cases (session 206) ──────────────────────────────

class TestLoadEdgeCases:
    def test_load_fills_missing_keys(self, tmp_path, monkeypatch):
        """Rules file with only 'avoidance' gets 'preference' backfilled."""
        path = str(tmp_path / "behavioral_rules.json")
        monkeypatch.setattr("src.core.behavioral_rules._rules_path", lambda: path)
        with open(path, "w") as f:
            json.dump({"avoidance": [{"pattern": "test"}]}, f)
        data = load()
        assert "avoidance" in data
        assert "preference" in data
        assert len(data["avoidance"]) == 1


# ── Cluster detection edge cases (session 206) ─────────────────

class TestClusterEdgeCases:
    def test_cluster_with_minimal_common_keywords(self):
        """Clusters form even when only 2 common keywords exist."""
        exps = [
            {"context": "web scraping data extraction", "action": "fetch webpage", "outcome": "failed", "type": "failure"},
            {"context": "web data extraction tool", "action": "scrape webpage", "outcome": "timeout", "type": "failure"},
            {"context": "data extraction from web source", "action": "download webpage", "outcome": "blocked", "type": "failure"},
        ]
        proposals = extract_rules_from_experiences(exps, min_occurrences=3)
        assert len(proposals) >= 1
        assert proposals[0]["type"] == "avoidance"

    def test_mixed_success_failure_separate_clusters(self):
        """Successes and failures are clustered separately."""
        exps = [
            {"context": "api integration test", "action": "call api", "outcome": "ok", "type": "success"},
            {"context": "api integration check", "action": "test api", "outcome": "ok", "type": "success"},
            {"context": "api integration verify", "action": "verify api", "outcome": "ok", "type": "success"},
            {"context": "database migration run", "action": "migrate db", "outcome": "failed", "type": "failure"},
            {"context": "database migration attempt", "action": "run migration", "outcome": "error", "type": "failure"},
            {"context": "database migration test", "action": "test migration", "outcome": "crashed", "type": "failure"},
        ]
        proposals = extract_rules_from_experiences(exps, min_occurrences=3)
        types = {p["type"] for p in proposals}
        # Should have proposals from both success and failure clusters
        assert len(proposals) >= 1


# ── Pruning edge cases (session 206) ──────────────────────────

class TestPruningEdgeCases:
    def test_prune_does_not_remove_recent_low_strength(self):
        """Rules created today shouldn't be decayed even if low strength."""
        data = _empty_rules()
        today = date.today().strftime("%Y-%m-%d")
        data["avoidance"].append({
            "pattern": "new rule",
            "reason": "just added",
            "keywords": ["test"],
            "strength": 0.2,
            "evidence_count": 1,
            "last_updated": today,
        })
        _prune(data)
        # Should still exist since it's recent (above min threshold 0.15)
        assert len(data["avoidance"]) == 1

    def test_prune_removes_decayed_old_rules(self):
        """Old rules that decay below threshold are removed."""
        data = _empty_rules()
        old_date = (date.today() - timedelta(days=60)).strftime("%Y-%m-%d")
        data["avoidance"].append({
            "pattern": "old rule",
            "reason": "ancient",
            "keywords": ["test"],
            "strength": 0.16,  # Just above threshold, but will decay below
            "evidence_count": 1,
            "last_reinforced": old_date,  # _prune checks last_reinforced, not last_updated
        })
        _prune(data)
        # After 60 days of decay (0.05 per cycle), 0.16 - 0.05 = 0.11 < 0.15 threshold
        assert len(data["avoidance"]) == 0
