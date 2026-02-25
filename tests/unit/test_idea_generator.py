"""Unit tests for src/core/idea_generator.py.

Covers: is_goal_relevant, is_duplicate_goal, count_active_goals,
prune_stale_goals, _get_active_project_names, _get_existing_reports,
_get_completed_goal_summaries, _filter_ideas, _save_to_backlog,
_opportunity_type_to_category, suggest_work, _brainstorm_fallback.

Note: is_purpose_driven is covered in test_purpose_driven_goals.py.
"""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.core.idea_generator import (
    MAX_ACTIVE_GOALS,
    _filter_ideas,
    _get_active_project_names,
    _get_completed_goal_summaries,
    _get_existing_reports,
    _opportunity_type_to_category,
    _save_to_backlog,
    count_active_goals,
    is_duplicate_goal,
    is_goal_relevant,
    prune_stale_goals,
    suggest_work,
)


# ---- Helpers ----

def _make_goal(description="test goal", is_complete_val=False, is_decomposed=True,
               tasks=None, created_at=None):
    """Create a mock Goal object."""
    g = MagicMock()
    g.description = description
    g.is_complete.return_value = is_complete_val
    g.is_decomposed = is_decomposed
    g.tasks = tasks or []
    g.created_at = created_at or datetime.now()
    g.goal_id = f"goal_{id(g)}"
    return g


def _make_goal_manager(goals_dict=None):
    """Create a mock GoalManager with given goals."""
    gm = MagicMock()
    gm.goals = goals_dict or {}
    return gm


# ---- _get_active_project_names ----

class TestGetActiveProjectNames:
    """Tests for _get_active_project_names()."""

    def test_empty_context(self):
        assert _get_active_project_names({}) == []

    def test_active_projects_dict(self):
        ctx = {
            "active_projects": {
                "health_opt": {
                    "description": "Health Optimization",
                    "path": "/workspace/projects/Health",
                }
            }
        }
        names = _get_active_project_names(ctx)
        assert "health opt" in names
        assert "health optimization" in names
        assert "/workspace/projects/health" in names

    def test_current_projects_list(self):
        ctx = {"current_projects": ["MyApp", "DataPipeline"]}
        names = _get_active_project_names(ctx)
        assert "myapp" in names
        assert "datapipeline" in names

    def test_combined(self):
        ctx = {
            "active_projects": {"proj_a": {"description": "Project A"}},
            "current_projects": ["proj_b"],
        }
        names = _get_active_project_names(ctx)
        assert len(names) >= 3  # key, desc, current


# ---- _get_existing_reports ----

class TestGetExistingReports:
    """Tests for _get_existing_reports()."""

    def test_no_reports_dir(self, tmp_path):
        with patch("src.core.idea_generator._base_path", return_value=tmp_path):
            assert _get_existing_reports() == []

    def test_with_reports(self, tmp_path):
        reports_dir = tmp_path / "workspace" / "reports"
        reports_dir.mkdir(parents=True)
        (reports_dir / "report1.md").write_text("content")
        (reports_dir / "report2.md").write_text("content")
        with patch("src.core.idea_generator._base_path", return_value=tmp_path):
            result = _get_existing_reports()
        assert len(result) == 2

    def test_max_30_reports(self, tmp_path):
        reports_dir = tmp_path / "workspace" / "reports"
        reports_dir.mkdir(parents=True)
        for i in range(35):
            (reports_dir / f"report_{i}.md").write_text("content")
        with patch("src.core.idea_generator._base_path", return_value=tmp_path):
            result = _get_existing_reports()
        assert len(result) <= 30


# ---- _get_completed_goal_summaries ----

class TestGetCompletedGoalSummaries:
    """Tests for _get_completed_goal_summaries()."""

    def test_none_manager(self):
        assert _get_completed_goal_summaries(None) == []

    def test_completed_goals(self):
        gm = _make_goal_manager({
            "g1": _make_goal("Build health dashboard", is_complete_val=True),
            "g2": _make_goal("Research sleep", is_complete_val=True),
            "g3": _make_goal("Active task", is_complete_val=False),
        })
        result = _get_completed_goal_summaries(gm)
        assert len(result) == 2

    def test_truncates_long_descriptions(self):
        gm = _make_goal_manager({
            "g1": _make_goal("A" * 200, is_complete_val=True),
        })
        result = _get_completed_goal_summaries(gm)
        assert len(result[0]) <= 100

    def test_last_15_only(self):
        goals = {f"g{i}": _make_goal(f"Goal {i}", is_complete_val=True) for i in range(20)}
        gm = _make_goal_manager(goals)
        result = _get_completed_goal_summaries(gm)
        assert len(result) <= 15


# ---- is_goal_relevant ----

class TestIsGoalRelevant:
    """Tests for is_goal_relevant()."""

    def test_self_improvement_always_relevant(self):
        assert is_goal_relevant("Fix error handling in plan_executor", {})
        assert is_goal_relevant("Refactor the logging system", {})
        assert is_goal_relevant("Optimize src/core/heartbeat.py performance", {})

    def test_file_path_always_relevant(self):
        assert is_goal_relevant("Update workspace/projects/Health/notes.md", {})
        assert is_goal_relevant("Create analysis.py for data processing", {})

    def test_project_word_match(self):
        ctx = {
            "active_projects": {
                "health_optimization": {
                    "description": "Health Optimization Project",
                    "path": "/workspace/projects/Health",
                    "focus_areas": ["supplements", "sleep"],
                }
            }
        }
        assert is_goal_relevant("Research supplements for muscle recovery", ctx)

    def test_interest_match(self):
        ctx = {"interests": ["machine learning", "health optimization"]}
        assert is_goal_relevant("Build a machine learning classifier", ctx)

    def test_irrelevant_goal(self):
        ctx = {
            "active_projects": {"health": {"description": "Health"}},
            "interests": ["coding"],
        }
        assert not is_goal_relevant("Plan a vacation to Europe", ctx)

    def test_empty_context_strict(self):
        """With no projects or interests, non-self-improvement goals fail."""
        assert not is_goal_relevant("Random task about cooking", {})

    def test_focus_area_match(self):
        ctx = {
            "active_projects": {
                "proj": {"focus_areas": ["nutrition", "exercise"]},
            }
        }
        assert is_goal_relevant("Create nutrition tracking spreadsheet", ctx)


# ---- is_duplicate_goal ----

class TestIsDuplicateGoal:
    """Tests for is_duplicate_goal()."""

    def test_none_manager(self):
        assert not is_duplicate_goal("anything", None)

    def test_exact_match(self):
        gm = _make_goal_manager({"g1": _make_goal("Build health dashboard")})
        assert is_duplicate_goal("Build health dashboard", gm)

    def test_case_insensitive(self):
        gm = _make_goal_manager({"g1": _make_goal("Build Health Dashboard")})
        assert is_duplicate_goal("build health dashboard", gm)

    def test_substring_containment(self):
        gm = _make_goal_manager({"g1": _make_goal("Build a comprehensive health dashboard with charts")})
        assert is_duplicate_goal("Build a comprehensive health dashboard", gm)

    def test_reverse_substring(self):
        gm = _make_goal_manager({"g1": _make_goal("Build dashboard")})
        assert is_duplicate_goal("Build dashboard with extra features", gm)

    def test_high_jaccard_overlap(self):
        gm = _make_goal_manager({
            "g1": _make_goal("Update health supplements tracking document"),
        })
        assert is_duplicate_goal("Update supplements health tracking file", gm)

    def test_low_overlap_not_duplicate(self):
        gm = _make_goal_manager({"g1": _make_goal("Build health dashboard")})
        assert not is_duplicate_goal("Write Python automation script", gm)

    def test_stop_words_excluded(self):
        """Stop words (a, the, and, etc.) should not count toward overlap."""
        gm = _make_goal_manager({"g1": _make_goal("Create the health report")})
        # "the" and "a" shouldn't inflate Jaccard
        assert not is_duplicate_goal("Create a python script", gm)

    def test_checks_completed_goals_too(self):
        gm = _make_goal_manager({
            "g1": _make_goal("Research creatine timing", is_complete_val=True),
        })
        assert is_duplicate_goal("Research creatine timing", gm)


# ---- count_active_goals ----

class TestCountActiveGoals:
    """Tests for count_active_goals()."""

    def test_none_manager(self):
        assert count_active_goals(None) == 0

    def test_mixed_goals(self):
        gm = _make_goal_manager({
            "g1": _make_goal(is_complete_val=False),
            "g2": _make_goal(is_complete_val=True),
            "g3": _make_goal(is_complete_val=False),
        })
        assert count_active_goals(gm) == 2


# ---- prune_stale_goals ----

class TestPruneStaleGoals:
    """Tests for prune_stale_goals()."""

    def test_none_manager(self):
        assert prune_stale_goals(None) == 0

    def test_prunes_old_undecomposed(self):
        old_goal = _make_goal(
            is_decomposed=False,
            created_at=datetime.now() - timedelta(hours=72),
        )
        gm = _make_goal_manager({"g1": old_goal})
        pruned = prune_stale_goals(gm)
        assert pruned == 1
        gm.remove_goal.assert_called_once_with("g1")
        gm.save_state.assert_called_once()

    def test_keeps_recent_undecomposed(self):
        recent = _make_goal(is_decomposed=False, created_at=datetime.now())
        gm = _make_goal_manager({"g1": recent})
        assert prune_stale_goals(gm) == 0

    def test_prunes_all_failed_tasks(self):
        failed_task = MagicMock()
        failed_task.status = MagicMock()
        failed_task.status.name = "FAILED"
        # Need to import TaskStatus for comparison
        from src.core.goal_manager import TaskStatus
        failed_task.status = TaskStatus.FAILED

        goal = _make_goal(
            is_decomposed=True,
            tasks=[failed_task, failed_task],
            created_at=datetime.now(),
        )
        goal.is_complete.return_value = False
        gm = _make_goal_manager({"g1": goal})
        assert prune_stale_goals(gm) == 1

    def test_keeps_goals_with_some_success(self):
        from src.core.goal_manager import TaskStatus
        failed = MagicMock()
        failed.status = TaskStatus.FAILED
        pending = MagicMock()
        pending.status = TaskStatus.PENDING

        goal = _make_goal(is_decomposed=True, tasks=[failed, pending])
        goal.is_complete.return_value = False
        gm = _make_goal_manager({"g1": goal})
        assert prune_stale_goals(gm) == 0

    def test_skips_completed_goals(self):
        old_complete = _make_goal(
            is_complete_val=True,
            is_decomposed=False,
            created_at=datetime.now() - timedelta(hours=72),
        )
        gm = _make_goal_manager({"g1": old_complete})
        assert prune_stale_goals(gm) == 0


# ---- _opportunity_type_to_category ----

class TestOpportunityTypeToCategory:
    """Tests for _opportunity_type_to_category()."""

    def test_known_types(self):
        assert _opportunity_type_to_category("build") == "Capability"
        assert _opportunity_type_to_category("ask") == "Agency"
        assert _opportunity_type_to_category("fix") == "Resilience"
        assert _opportunity_type_to_category("connect") == "Agency"
        assert _opportunity_type_to_category("improve") == "Capability"

    def test_unknown_type(self):
        assert _opportunity_type_to_category("unknown") == "Capability"


# ---- _save_to_backlog ----

class TestSaveToBacklog:
    """Tests for _save_to_backlog()."""

    def test_creates_backlog_file(self, tmp_path):
        with patch("src.core.idea_generator._base_path", return_value=tmp_path):
            (tmp_path / "data").mkdir(exist_ok=True)
            ideas = [{"description": "Test idea", "score": 5.0}]
            _save_to_backlog(ideas, datetime.now())
            backlog_path = tmp_path / "data" / "idea_backlog.json"
            assert backlog_path.exists()
            data = json.loads(backlog_path.read_text())
            assert len(data["ideas"]) == 1

    def test_appends_to_existing(self, tmp_path):
        (tmp_path / "data").mkdir(exist_ok=True)
        backlog_path = tmp_path / "data" / "idea_backlog.json"
        backlog_path.write_text(json.dumps({
            "ideas": [{"description": "Old idea"}],
            "last_suggest": "2026-01-01",
        }))
        with patch("src.core.idea_generator._base_path", return_value=tmp_path):
            _save_to_backlog([{"description": "New idea"}], datetime.now())
            data = json.loads(backlog_path.read_text())
        assert len(data["ideas"]) == 2


# ---- _filter_ideas ----

class TestFilterIdeas:
    """Tests for _filter_ideas()."""

    def _make_idea(self, desc="Test idea", category="Capability", opportunity_type=None, score=5.0):
        idea = {"description": desc, "category": category, "score": score}
        if opportunity_type:
            idea["opportunity_type"] = opportunity_type
        return idea

    def test_filters_duplicates(self):
        gm = _make_goal_manager({"g1": _make_goal("Build health dashboard")})
        ideas = [self._make_idea("Build health dashboard")]
        idea_history = MagicMock()
        idea_history.is_stale.return_value = False
        filtered = _filter_ideas(ideas, gm, {}, None, idea_history)
        assert len(filtered) == 0

    def test_filters_irrelevant(self):
        gm = _make_goal_manager({})
        ctx = {"active_projects": {"health": {"description": "Health"}}}
        ideas = [self._make_idea("Plan European vacation")]
        idea_history = MagicMock()
        idea_history.is_stale.return_value = False
        filtered = _filter_ideas(ideas, gm, ctx, None, idea_history)
        assert len(filtered) == 0

    def test_scanner_ideas_skip_relevance_check(self):
        """Ideas from scanner (with opportunity_type) bypass relevance filter."""
        gm = _make_goal_manager({})
        ctx = {"active_projects": {"health": {"description": "Health"}}}
        ideas = [self._make_idea(
            "Optimize database queries",
            opportunity_type="improve",
        )]
        idea_history = MagicMock()
        idea_history.is_stale.return_value = False
        filtered = _filter_ideas(ideas, gm, ctx, None, idea_history)
        assert len(filtered) == 1

    def test_cold_start_relaxes_filters(self):
        """With no projects/interests, relevance and purpose filters are relaxed."""
        gm = _make_goal_manager({})
        ctx = {}  # Empty = cold start
        ideas = [self._make_idea("Random exploration task")]
        idea_history = MagicMock()
        idea_history.is_stale.return_value = False
        filtered = _filter_ideas(ideas, gm, ctx, None, idea_history)
        assert len(filtered) == 1

    def test_stale_ideas_filtered(self):
        gm = _make_goal_manager({})
        ideas = [self._make_idea("Old rejected idea")]
        idea_history = MagicMock()
        idea_history.is_stale.return_value = True
        idea_history.times_rejected.return_value = 3
        filtered = _filter_ideas(ideas, gm, {}, None, idea_history)
        assert len(filtered) == 0

    def test_already_researched_filtered(self):
        gm = _make_goal_manager({})
        memory = MagicMock()
        memory.retrieve_relevant.return_value = {
            "semantic": [{"distance": 0.3}],  # < 0.5 threshold
        }
        ideas = [self._make_idea("Research creatine timing", opportunity_type="build")]
        idea_history = MagicMock()
        idea_history.is_stale.return_value = False
        filtered = _filter_ideas(ideas, gm, {}, memory, idea_history)
        assert len(filtered) == 0

    def test_empty_description_skipped(self):
        ideas = [{"description": "", "category": "Capability"}]
        idea_history = MagicMock()
        filtered = _filter_ideas(ideas, _make_goal_manager({}), {}, None, idea_history)
        assert len(filtered) == 0


# ---- suggest_work ----

class TestSuggestWork:
    """Tests for suggest_work()."""

    def test_cooldown_respected(self):
        router = MagicMock()
        gm = _make_goal_manager({})
        ls = MagicMock()
        stop = threading.Event()
        last = datetime.now()  # Just suggested
        ideas, ts = suggest_work(router, gm, ls, {}, last, stop, cooldown_secs=600)
        assert ideas == []
        assert ts == last

    def test_no_router(self):
        stop = threading.Event()
        ideas, _ = suggest_work(None, _make_goal_manager({}), MagicMock(), {}, None, stop)
        assert ideas == []

    def test_no_goal_manager(self):
        stop = threading.Event()
        ideas, _ = suggest_work(MagicMock(), None, MagicMock(), {}, None, stop)
        assert ideas == []

    def test_stop_flag_respected(self):
        stop = threading.Event()
        stop.set()
        ideas, _ = suggest_work(MagicMock(), _make_goal_manager({}), MagicMock(), {}, None, stop)
        assert ideas == []

    def test_returns_max_5_ideas(self, tmp_path):
        router = MagicMock()
        gm = _make_goal_manager({})
        ls = MagicMock()
        stop = threading.Event()

        # Mock scanner to return many ideas
        mock_opps = []
        for i in range(8):
            opp = MagicMock()
            opp.type = "build"
            opp.description = f"Fix error in src/core/module_{i}.py"
            opp.user_value = f"Improved module {i}"
            opp.target_files = [f"workspace/projects/mod{i}.py"]
            opp.value_score = 8
            opp.estimated_hours = 1.0
            opp.reasoning = "test"
            opp.source = "scanner"
            mock_opps.append(opp)

        idea_history = MagicMock()
        idea_history.is_stale.return_value = False
        idea_history.get_rejection_context.return_value = ""
        idea_history.get_accepted_context.return_value = ""

        with (
            patch("src.core.idea_generator._base_path", return_value=tmp_path),
            patch("src.core.opportunity_scanner.scan_all", return_value=mock_opps),
            patch("src.core.idea_generator.get_idea_history", return_value=idea_history),
        ):
            (tmp_path / "data").mkdir(exist_ok=True)
            ideas, ts = suggest_work(router, gm, ls, {}, None, stop, cooldown_secs=0)
        assert len(ideas) <= 5
