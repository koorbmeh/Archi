"""Unit tests for src/core/opportunity_scanner.py.

Covers: Opportunity dataclass, _read_vision_file, combine_and_rank,
infer_opportunity_type, scan_projects, scan_errors, scan_capabilities,
scan_user_context, scan_all.
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.opportunity_scanner import (
    Opportunity,
    _read_vision_file,
    _vision_cache,
    _vision_cache_ts,
    combine_and_rank,
    infer_opportunity_type,
    scan_all,
    scan_capabilities,
    scan_errors,
    scan_projects,
    scan_user_context,
)


@pytest.fixture(autouse=True)
def _clear_vision_cache():
    """Clear vision cache before each test."""
    _vision_cache.clear()
    _vision_cache_ts.clear()
    yield
    _vision_cache.clear()
    _vision_cache_ts.clear()


# ---- TestOpportunity ----

class TestOpportunity:
    def test_defaults(self):
        opp = Opportunity(type="build", description="Do a thing")
        assert opp.type == "build"
        assert opp.description == "Do a thing"
        assert opp.target_files == []
        assert opp.value_score == 5
        assert opp.estimated_hours == 1.0
        assert opp.source == ""

    def test_all_fields(self):
        opp = Opportunity(
            type="fix", description="Fix bug", target_files=["src/a.py"],
            value_score=8, estimated_hours=0.5, user_value="stability",
            source="error_pattern", reasoning="repeated crash",
        )
        assert opp.value_score == 8
        assert opp.source == "error_pattern"


# ---- TestInferOpportunityType ----

class TestInferOpportunityType:
    def test_fix_keywords(self):
        assert infer_opportunity_type("Fix the login error") == "fix"
        assert infer_opportunity_type("repair database crash") == "fix"
        assert infer_opportunity_type("handle the bug in parser") == "fix"

    def test_ask_keywords(self):
        assert infer_opportunity_type("Ask Jesse about supplements") == "ask"
        assert infer_opportunity_type("collect user preferences") == "ask"

    def test_connect_keywords(self):
        assert infer_opportunity_type("connect Discord to tracker") == "connect"
        assert infer_opportunity_type("integrate health data sources") == "connect"
        assert infer_opportunity_type("enable voice commands") == "connect"

    def test_improve_keywords(self):
        assert infer_opportunity_type("improve response quality") == "improve"
        assert infer_opportunity_type("optimize dream cycle timing") == "improve"
        assert infer_opportunity_type("enhance the notification system") == "improve"

    def test_defaults_to_build(self):
        assert infer_opportunity_type("create a new dashboard") == "build"
        assert infer_opportunity_type("something random") == "build"

    def test_case_insensitive(self):
        assert infer_opportunity_type("FIX THE BUG") == "fix"
        assert infer_opportunity_type("IMPROVE performance") == "improve"


# ---- TestCombineAndRank ----

class TestCombineAndRank:
    def test_empty_input(self):
        assert combine_and_rank([]) == []

    def test_returns_sorted_by_value_per_hour(self):
        opps = [
            Opportunity(type="build", description="low value slow", value_score=2, estimated_hours=4.0),
            Opportunity(type="fix", description="high value fast", value_score=10, estimated_hours=0.5),
            Opportunity(type="improve", description="medium value medium", value_score=5, estimated_hours=1.0),
        ]
        ranked = combine_and_rank(opps)
        assert ranked[0].description == "high value fast"  # 10/0.5 = 20
        assert ranked[1].description == "medium value medium"  # 5/1.0 = 5

    def test_deduplicates_by_word_overlap(self):
        opps = [
            Opportunity(type="build", description="build a health tracker for supplements", value_score=5),
            Opportunity(type="build", description="build a supplement tracker for health", value_score=7),
        ]
        ranked = combine_and_rank(opps)
        assert len(ranked) == 1
        # Should keep the higher-scored one
        assert ranked[0].value_score == 7

    def test_keeps_distinct_opportunities(self):
        opps = [
            Opportunity(type="build", description="create database schema"),
            Opportunity(type="fix", description="repair login error handler"),
        ]
        ranked = combine_and_rank(opps)
        assert len(ranked) == 2

    def test_limits_to_7_results(self):
        opps = [
            Opportunity(type="build", description=f"unique thing number {i}", value_score=i + 1)
            for i in range(15)
        ]
        ranked = combine_and_rank(opps)
        assert len(ranked) <= 7

    def test_dedup_keeps_higher_scored(self):
        opps = [
            Opportunity(type="build", description="fix the broken parser module", value_score=3),
            Opportunity(type="fix", description="fix the broken parser module completely", value_score=9),
        ]
        ranked = combine_and_rank(opps)
        assert len(ranked) == 1
        assert ranked[0].value_score == 9


# ---- TestReadVisionFile ----

class TestReadVisionFile:
    def test_reads_readme(self, tmp_path):
        proj = tmp_path / "my_proj"
        proj.mkdir()
        (proj / "README.md").write_text("# My Project\nDescription here")
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = _read_vision_file("my_proj")
        assert "My Project" in result

    def test_prefers_project_overview(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "PROJECT OVERVIEW.md").write_text("Vision content")
        (proj / "README.md").write_text("Readme content")
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = _read_vision_file("proj")
        assert "Vision" in result

    def test_returns_empty_for_nonexistent_path(self, tmp_path):
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = _read_vision_file("nonexistent")
        assert result == ""

    def test_returns_empty_for_no_vision_files(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "code.py").write_text("x = 1")
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = _read_vision_file("proj")
        assert result == ""

    def test_caches_result(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "README.md").write_text("cached content")
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            first = _read_vision_file("proj")
            # Modify file — cache should still return original
            (proj / "README.md").write_text("modified")
            second = _read_vision_file("proj")
        assert first == second == "cached content"

    def test_truncates_to_2000_chars(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "README.md").write_text("x" * 5000)
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = _read_vision_file("proj")
        assert len(result) <= 2000

    def test_searches_subdirectories(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        sub = proj / "docs"
        sub.mkdir()
        (sub / "VISION.md").write_text("Sub vision")
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = _read_vision_file("proj")
        assert "Sub vision" in result


# ---- TestScanProjects ----

class TestScanProjects:
    def test_returns_empty_for_no_active_projects(self):
        result = scan_projects({}, MagicMock())
        assert result == []

    def test_returns_empty_for_empty_active_projects(self):
        result = scan_projects({"active_projects": {}}, MagicMock())
        assert result == []

    def test_skips_non_dict_project_entries(self):
        ctx = {"active_projects": {"bad": "not_a_dict"}}
        result = scan_projects(ctx, MagicMock())
        assert result == []

    def test_skips_projects_without_path(self):
        ctx = {"active_projects": {"proj": {"description": "test"}}}
        result = scan_projects(ctx, MagicMock())
        assert result == []

    def test_returns_opportunities_from_model(self, tmp_path):
        proj = tmp_path / "test_proj"
        proj.mkdir()
        (proj / "README.md").write_text("# Test\nBuild a tracker")

        ctx = {"active_projects": {
            "test_proj": {"description": "Test project", "path": "test_proj"}
        }}
        router = MagicMock()
        router.generate.return_value = {
            "text": json.dumps([{
                "type": "build",
                "description": "Build supplement tracker",
                "target_file": "workspace/projects/test_proj/tracker.json",
                "value": 8,
                "hours": 1.0,
                "user_value": "track health data",
                "reasoning": "vision mentions tracking",
            }])
        }

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path), \
             patch("src.utils.project_context.scan_project_files", return_value=["README.md"]):
            result = scan_projects(ctx, router)

        assert len(result) == 1
        assert result[0].type == "build"
        assert result[0].source == "project_gap"
        assert result[0].value_score == 8

    def test_handles_model_failure(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "README.md").write_text("content")

        ctx = {"active_projects": {"proj": {"description": "test", "path": "proj"}}}
        router = MagicMock()
        router.generate.side_effect = RuntimeError("API down")

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path), \
             patch("src.utils.project_context.scan_project_files", return_value=["README.md"]):
            result = scan_projects(ctx, router)
        assert result == []

    def test_clamps_value_score(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "README.md").write_text("content")

        ctx = {"active_projects": {"proj": {"description": "test", "path": "proj"}}}
        router = MagicMock()
        router.generate.return_value = {
            "text": json.dumps([{
                "type": "build", "description": "thing",
                "value": 99, "hours": 0.01,
            }])
        }

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path), \
             patch("src.utils.project_context.scan_project_files", return_value=["README.md"]):
            result = scan_projects(ctx, router)
        assert result[0].value_score == 10  # Clamped to max
        assert result[0].estimated_hours == 0.2  # Clamped to min

    def test_skips_empty_descriptions(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "README.md").write_text("content")

        ctx = {"active_projects": {"proj": {"description": "test", "path": "proj"}}}
        router = MagicMock()
        router.generate.return_value = {
            "text": json.dumps([{"type": "build", "description": "", "value": 5}])
        }

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path), \
             patch("src.utils.project_context.scan_project_files", return_value=["README.md"]):
            result = scan_projects(ctx, router)
        assert result == []

    def test_limits_to_3_per_project(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "README.md").write_text("content")

        ctx = {"active_projects": {"proj": {"description": "test", "path": "proj"}}}
        router = MagicMock()
        items = [{"type": "build", "description": f"thing {i}", "value": 5} for i in range(10)]
        router.generate.return_value = {"text": json.dumps(items)}

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path), \
             patch("src.utils.project_context.scan_project_files", return_value=["README.md"]):
            result = scan_projects(ctx, router)
        assert len(result) <= 3


# ---- TestScanErrors ----

class TestScanErrors:
    def test_returns_empty_when_no_errors_dir(self, tmp_path):
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = scan_errors(MagicMock())
        assert result == []

    def test_returns_empty_when_fewer_than_3_errors(self, tmp_path):
        errors_dir = tmp_path / "logs" / "errors"
        errors_dir.mkdir(parents=True)
        from datetime import datetime
        today = datetime.now().date().isoformat()
        (errors_dir / f"{today}.log").write_text("ERROR one\nERROR two\n")

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = scan_errors(MagicMock())
        assert result == []

    def test_returns_opportunities_from_model(self, tmp_path):
        errors_dir = tmp_path / "logs" / "errors"
        errors_dir.mkdir(parents=True)
        from datetime import datetime
        today = datetime.now().date().isoformat()
        lines = "\n".join(f"2026-02-24 ERROR module.func: failure {i}" for i in range(5))
        (errors_dir / f"{today}.log").write_text(lines)

        router = MagicMock()
        router.generate.return_value = {
            "text": json.dumps([{
                "type": "fix", "description": "Fix repeated timeout in router",
                "target_file": "src/models/router.py", "value": 7, "hours": 0.5,
                "user_value": "stability", "reasoning": "timeout pattern",
            }])
        }

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = scan_errors(router)
        assert len(result) == 1
        assert result[0].type == "fix"
        assert result[0].source == "error_pattern"

    def test_deduplicates_error_lines(self, tmp_path):
        errors_dir = tmp_path / "logs" / "errors"
        errors_dir.mkdir(parents=True)
        from datetime import datetime
        today = datetime.now().date().isoformat()
        # Same error repeated with different timestamps
        lines = "\n".join(
            f"2026-02-24T10:0{i}:00.000 ERROR same error message" for i in range(10)
        )
        (errors_dir / f"{today}.log").write_text(lines)

        router = MagicMock()
        router.generate.return_value = {"text": "[]"}

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = scan_errors(router)
        # Should have parsed but found no opportunities from empty array
        assert result == []

    def test_handles_model_failure(self, tmp_path):
        errors_dir = tmp_path / "logs" / "errors"
        errors_dir.mkdir(parents=True)
        from datetime import datetime
        today = datetime.now().date().isoformat()
        lines = "\n".join(f"2026-02-24 ERROR failure {i}" for i in range(5))
        (errors_dir / f"{today}.log").write_text(lines)

        router = MagicMock()
        router.generate.side_effect = RuntimeError("fail")

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = scan_errors(router)
        assert result == []

    def test_limits_to_2_opportunities(self, tmp_path):
        errors_dir = tmp_path / "logs" / "errors"
        errors_dir.mkdir(parents=True)
        from datetime import datetime
        today = datetime.now().date().isoformat()
        lines = "\n".join(f"2026-02-24 ERROR failure {i}" for i in range(5))
        (errors_dir / f"{today}.log").write_text(lines)

        router = MagicMock()
        items = [{"type": "fix", "description": f"Fix {i}", "value": 5} for i in range(10)]
        router.generate.return_value = {"text": json.dumps(items)}

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = scan_errors(router)
        assert len(result) <= 2


# ---- TestScanCapabilities ----

class TestScanCapabilities:
    def _make_goal_manager(self, task_descriptions=None):
        gm = MagicMock()
        tasks = []
        for desc in (task_descriptions or []):
            t = MagicMock()
            t.description = desc
            tasks.append(t)
        goal = MagicMock()
        goal.tasks = tasks
        gm.goals = {"g1": goal}
        return gm

    def test_returns_empty_when_all_capabilities_used(self):
        gm = self._make_goal_manager([
            "ask_user about preferences",
            "run_python script",
            "web_search for info",
            "create_file document",
            "fetch_webpage article",
        ])
        ctx = {"active_projects": {"proj": {"description": "test"}}}
        result = scan_capabilities(ctx, gm, MagicMock())
        assert result == []

    def test_returns_empty_for_no_projects(self):
        result = scan_capabilities({}, None, MagicMock())
        assert result == []

    def test_returns_opportunities_for_unused_caps(self):
        gm = self._make_goal_manager(["ask_user about stuff"])
        # Only ask_user is used; others are unused
        ctx = {"active_projects": {"proj": {"description": "health tracker"}}}
        router = MagicMock()
        router.generate.return_value = {
            "text": json.dumps([{
                "type": "connect", "description": "Run Python analysis on health data",
                "value": 7, "hours": 0.5,
            }])
        }
        result = scan_capabilities(ctx, gm, router)
        assert len(result) >= 1
        assert result[0].source == "unused_capability"

    def test_handles_model_failure(self):
        ctx = {"active_projects": {"proj": {"description": "test"}}}
        router = MagicMock()
        router.generate.side_effect = RuntimeError("fail")
        result = scan_capabilities(ctx, None, router)
        assert result == []


# ---- TestScanUserContext ----

class TestScanUserContext:
    def test_returns_empty_with_no_data(self, tmp_path):
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = scan_user_context(None, MagicMock())
        assert result == []

    def test_reads_conversation_log(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        entries = [
            json.dumps({"user_message": f"message {i}", "ts": "2026-02-24"})
            for i in range(5)
        ]
        (logs_dir / "conversations.jsonl").write_text("\n".join(entries))

        router = MagicMock()
        router.generate.return_value = {
            "text": json.dumps([{
                "type": "build", "description": "Build reminder system",
                "value": 7, "hours": 1.0,
            }])
        }

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = scan_user_context(None, router)
        assert len(result) >= 1

    def test_uses_memory_topics(self, tmp_path):
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            memory = MagicMock()
            memory.retrieve_relevant.return_value = {
                "semantic": [{"text": "health tracking important", "distance": 0.5,
                              "metadata": {"task_description": "health tracker"}}]
            }
            router = MagicMock()
            router.generate.return_value = {
                "text": json.dumps([{
                    "type": "build", "description": "Build health tracker",
                    "value": 8, "hours": 1.0,
                }])
            }
            result = scan_user_context(memory, router)
        assert len(result) >= 1

    def test_handles_model_failure(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        entries = [json.dumps({"user_message": f"msg {i}"}) for i in range(5)]
        (logs_dir / "conversations.jsonl").write_text("\n".join(entries))

        router = MagicMock()
        router.generate.side_effect = RuntimeError("fail")

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = scan_user_context(None, router)
        assert result == []

    def test_limits_to_2_opportunities(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        entries = [json.dumps({"user_message": f"msg {i}"}) for i in range(5)]
        (logs_dir / "conversations.jsonl").write_text("\n".join(entries))

        router = MagicMock()
        items = [{"type": "build", "description": f"thing {i}", "value": 5} for i in range(10)]
        router.generate.return_value = {"text": json.dumps(items)}

        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = scan_user_context(None, router)
        assert len(result) <= 2

    def test_handles_malformed_jsonl(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "conversations.jsonl").write_text(
            "not json\n{bad\n" + json.dumps({"user_message": "valid"}) + "\n"
        )
        router = MagicMock()
        router.generate.return_value = {
            "text": json.dumps([{"type": "build", "description": "thing", "value": 5}])
        }
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            result = scan_user_context(None, router)
        # Should not crash, may find 0 or 1 opportunities depending on threshold
        assert isinstance(result, list)

    def test_filters_high_distance_memories(self, tmp_path):
        with patch("src.core.opportunity_scanner._base_path", return_value=tmp_path):
            memory = MagicMock()
            memory.retrieve_relevant.return_value = {
                "semantic": [{"text": "irrelevant", "distance": 1.5, "metadata": {}}]
            }
            # No conversations either → should return empty
            result = scan_user_context(memory, MagicMock())
        assert result == []


# ---- TestScanAll ----

class TestScanAll:
    def test_combines_all_scanners(self):
        router = MagicMock()
        # All scanners will return empty due to missing data
        with patch("src.core.opportunity_scanner.scan_projects", return_value=[
            Opportunity(type="build", description="Build thing", value_score=7)
        ]), patch("src.core.opportunity_scanner.scan_errors", return_value=[
            Opportunity(type="fix", description="Fix error", value_score=5)
        ]), patch("src.core.opportunity_scanner.scan_capabilities", return_value=[]), \
             patch("src.core.opportunity_scanner.scan_user_context", return_value=[]):
            result = scan_all({"active_projects": {}}, router, None, MagicMock())
        assert len(result) == 2

    def test_skips_user_context_when_no_memory(self):
        with patch("src.core.opportunity_scanner.scan_projects", return_value=[]), \
             patch("src.core.opportunity_scanner.scan_errors", return_value=[]), \
             patch("src.core.opportunity_scanner.scan_capabilities", return_value=[]), \
             patch("src.core.opportunity_scanner.scan_user_context") as mock_uc:
            result = scan_all({}, MagicMock(), None, None)
            mock_uc.assert_not_called()

    def test_isolates_scanner_failures(self):
        with patch("src.core.opportunity_scanner.scan_projects", side_effect=RuntimeError("boom")), \
             patch("src.core.opportunity_scanner.scan_errors", return_value=[
                 Opportunity(type="fix", description="Fix thing", value_score=6)
             ]), \
             patch("src.core.opportunity_scanner.scan_capabilities", return_value=[]):
            result = scan_all({}, MagicMock())
        # scan_projects failed but scan_errors succeeded
        assert len(result) == 1
