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
    _gather_scheduling_evidence,
    _get_active_project_names,
    _get_completed_goal_summaries,
    _get_existing_reports,
    _get_existing_schedule_descriptions,
    _is_saturated_topic,
    _opportunity_type_to_category,
    _save_to_backlog,
    check_retirement_candidates,
    count_active_goals,
    create_proposed_schedules,
    format_retirement_message,
    format_schedule_proposal_message,
    is_duplicate_goal,
    is_goal_relevant,
    prune_stale_goals,
    _repair_blocked_tasks,
    suggest_scheduled_tasks,
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
        """Interests from UserModel should enable relevance matching."""
        from src.core.user_model import get_user_model, _reset_for_testing
        import tempfile, os
        _reset_for_testing()
        with tempfile.TemporaryDirectory() as td:
            um = get_user_model.__wrapped__() if hasattr(get_user_model, '__wrapped__') else None
            # Directly set interests on the singleton for this test
            from src.core import user_model as _um_mod
            old = _um_mod._instance
            _um_mod._instance = None
            try:
                test_um = _um_mod.UserModel(data_dir=Path(td))
                test_um.interests = ["machine learning", "health optimization"]
                _um_mod._instance = test_um
                ctx = {}
                assert is_goal_relevant("Build a machine learning classifier", ctx)
            finally:
                _um_mod._instance = old

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
        from src.core.goal_manager import TaskStatus
        failed1 = MagicMock()
        failed1.task_id = "t1"
        failed1.status = TaskStatus.FAILED
        failed1.dependencies = []
        failed2 = MagicMock()
        failed2.task_id = "t2"
        failed2.status = TaskStatus.FAILED
        failed2.dependencies = []

        goal = _make_goal(
            is_decomposed=True,
            tasks=[failed1, failed2],
            created_at=datetime.now(),
        )
        goal.is_complete.return_value = False
        gm = _make_goal_manager({"g1": goal})
        assert prune_stale_goals(gm) == 1

    def test_repairs_and_prunes_goals_with_blocked_pending_tasks(self):
        """Pending tasks that depend on failed tasks get repaired to BLOCKED,
        making the goal all-terminal and prunable (session 204)."""
        from src.core.goal_manager import TaskStatus
        failed = MagicMock()
        failed.task_id = "t1"
        failed.status = TaskStatus.FAILED
        failed.dependencies = []
        pending = MagicMock()
        pending.task_id = "t2"
        pending.status = TaskStatus.PENDING
        pending.dependencies = ["t1"]

        goal = _make_goal(is_decomposed=True, tasks=[failed, pending])
        goal.is_complete.return_value = False
        gm = _make_goal_manager({"g1": goal})
        # Repair marks pending→BLOCKED, then all-terminal → prune
        assert prune_stale_goals(gm) == 1

    def test_keeps_goals_with_independent_pending_tasks(self):
        """Pending tasks with no failed dependencies are not repaired."""
        from src.core.goal_manager import TaskStatus
        failed = MagicMock()
        failed.task_id = "t1"
        failed.status = TaskStatus.FAILED
        failed.dependencies = []
        pending = MagicMock()
        pending.task_id = "t2"
        pending.status = TaskStatus.PENDING
        pending.dependencies = []  # no dependency on failed task

        goal = _make_goal(is_decomposed=True, tasks=[failed, pending])
        goal.is_complete.return_value = False
        gm = _make_goal_manager({"g1": goal})
        # pending task is independent, so goal is NOT all-terminal
        assert prune_stale_goals(gm) == 0

    def test_prunes_mixed_failed_blocked_goal(self):
        from src.core.goal_manager import TaskStatus
        completed = MagicMock()
        completed.task_id = "t1"
        completed.status = TaskStatus.COMPLETED
        completed.dependencies = []
        failed = MagicMock()
        failed.task_id = "t2"
        failed.status = TaskStatus.FAILED
        failed.dependencies = []
        blocked = MagicMock()
        blocked.task_id = "t3"
        blocked.status = TaskStatus.BLOCKED
        blocked.dependencies = ["t2"]

        goal = _make_goal(is_decomposed=True, tasks=[completed, failed, blocked])
        goal.is_complete.return_value = False
        gm = _make_goal_manager({"g1": goal})
        assert prune_stale_goals(gm) == 1

    def test_prunes_empty_decomposed_goal(self):
        goal = _make_goal(
            is_decomposed=True, tasks=[],
            created_at=datetime.now() - timedelta(hours=2),
        )
        goal.is_complete.return_value = False
        gm = _make_goal_manager({"g1": goal})
        assert prune_stale_goals(gm) == 1

    def test_keeps_recent_empty_decomposed_goal(self):
        goal = _make_goal(
            is_decomposed=True, tasks=[],
            created_at=datetime.now() - timedelta(minutes=30),
        )
        goal.is_complete.return_value = False
        gm = _make_goal_manager({"g1": goal})
        assert prune_stale_goals(gm) == 0

    def test_skips_recent_completed_goals(self):
        """Completed goals younger than 7 days are kept."""
        task = MagicMock()
        task.completed_at = datetime.now() - timedelta(days=3)
        old_complete = _make_goal(
            is_complete_val=True,
            is_decomposed=True,
            tasks=[task],
            created_at=datetime.now() - timedelta(days=3),
        )
        gm = _make_goal_manager({"g1": old_complete})
        assert prune_stale_goals(gm) == 0

    def test_prunes_old_completed_goals(self):
        """Completed goals older than 7 days are pruned (session 222)."""
        task = MagicMock()
        task.completed_at = datetime.now() - timedelta(days=10)
        old_complete = _make_goal(
            is_complete_val=True,
            is_decomposed=True,
            tasks=[task],
            created_at=datetime.now() - timedelta(days=10),
        )
        gm = _make_goal_manager({"g1": old_complete})
        assert prune_stale_goals(gm) == 1
        gm.remove_goal.assert_called_once_with("g1")

    def test_prunes_old_completed_goal_uses_task_completion_time(self):
        """Uses last task completion time, not goal creation time (session 222)."""
        # Goal created 10 days ago but last task completed 2 days ago — keep it
        task = MagicMock()
        task.completed_at = datetime.now() - timedelta(days=2)
        goal = _make_goal(
            is_complete_val=True,
            is_decomposed=True,
            tasks=[task],
            created_at=datetime.now() - timedelta(days=10),
        )
        gm = _make_goal_manager({"g1": goal})
        assert prune_stale_goals(gm) == 0


# ---- _repair_blocked_tasks ----

class TestRepairBlockedTasks:
    """Tests for _repair_blocked_tasks() (session 204)."""

    def test_repairs_pending_task_with_failed_dependency(self):
        from src.core.goal_manager import TaskStatus
        failed = MagicMock()
        failed.task_id = "t1"
        failed.status = TaskStatus.FAILED
        failed.dependencies = []
        pending = MagicMock()
        pending.task_id = "t2"
        pending.status = TaskStatus.PENDING
        pending.dependencies = ["t1"]

        goal = _make_goal(is_decomposed=True, tasks=[failed, pending])
        gm = _make_goal_manager({"g1": goal})
        repaired = _repair_blocked_tasks(gm)
        assert repaired == 1
        assert pending.status == TaskStatus.BLOCKED

    def test_transitive_repair(self):
        """t1 failed → t2 pending(deps=[t1]) → t3 pending(deps=[t2])"""
        from src.core.goal_manager import TaskStatus
        t1 = MagicMock(task_id="t1", status=TaskStatus.FAILED, dependencies=[])
        t2 = MagicMock(task_id="t2", status=TaskStatus.PENDING, dependencies=["t1"])
        t3 = MagicMock(task_id="t3", status=TaskStatus.PENDING, dependencies=["t2"])

        goal = _make_goal(is_decomposed=True, tasks=[t1, t2, t3])
        gm = _make_goal_manager({"g1": goal})
        assert _repair_blocked_tasks(gm) == 2
        assert t2.status == TaskStatus.BLOCKED
        assert t3.status == TaskStatus.BLOCKED

    def test_no_repair_when_no_failures(self):
        from src.core.goal_manager import TaskStatus
        t1 = MagicMock(task_id="t1", status=TaskStatus.COMPLETED, dependencies=[])
        t2 = MagicMock(task_id="t2", status=TaskStatus.PENDING, dependencies=["t1"])

        goal = _make_goal(is_decomposed=True, tasks=[t1, t2])
        gm = _make_goal_manager({"g1": goal})
        assert _repair_blocked_tasks(gm) == 0
        assert t2.status == TaskStatus.PENDING

    def test_independent_pending_not_repaired(self):
        from src.core.goal_manager import TaskStatus
        t1 = MagicMock(task_id="t1", status=TaskStatus.FAILED, dependencies=[])
        t2 = MagicMock(task_id="t2", status=TaskStatus.PENDING, dependencies=[])

        goal = _make_goal(is_decomposed=True, tasks=[t1, t2])
        gm = _make_goal_manager({"g1": goal})
        assert _repair_blocked_tasks(gm) == 0
        assert t2.status == TaskStatus.PENDING


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

    def setup_method(self):
        """Reset UserModel singleton so tests don't depend on data files."""
        from src.core.user_model import _reset_for_testing
        _reset_for_testing()

    def teardown_method(self):
        from src.core.user_model import _reset_for_testing
        _reset_for_testing()

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
        from src.core import user_model as _um_mod
        import tempfile
        # Use a clean temp dir so UserModel has no interests
        with tempfile.TemporaryDirectory() as td:
            _um_mod._instance = _um_mod.UserModel(data_dir=Path(td))
            try:
                gm = _make_goal_manager({})
                ctx = {}  # Empty = cold start
                ideas = [self._make_idea("Random exploration task")]
                idea_history = MagicMock()
                idea_history.is_stale.return_value = False
                filtered = _filter_ideas(ideas, gm, ctx, None, idea_history)
                assert len(filtered) == 1
            finally:
                _um_mod._instance = None

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

    def test_life_category_bypasses_relevance_filter(self):
        """Ideas with life categories (Health, Puppy, Fitness) bypass relevance/purpose filters."""
        from src.core import user_model as _um_mod
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            _um_mod._instance = _um_mod.UserModel(data_dir=Path(td))
            _um_mod._instance.interests = ["Health & fitness"]
            try:
                gm = _make_goal_manager({})
                ctx = {"active_projects": {"archi": {"description": "AI agent"}}}
                # This idea is about health, not the Archi project — would normally be filtered
                ideas = [self._make_idea(
                    "Draft a 5-minute morning stretch routine",
                    category="Health",
                )]
                idea_history = MagicMock()
                idea_history.is_stale.return_value = False
                filtered = _filter_ideas(ideas, gm, ctx, None, idea_history)
                assert len(filtered) == 1
            finally:
                _um_mod._instance = None

    def test_life_category_puppy(self):
        """Puppy category ideas bypass filters."""
        from src.core import user_model as _um_mod
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            _um_mod._instance = _um_mod.UserModel(data_dir=Path(td))
            try:
                gm = _make_goal_manager({})
                ctx = {"active_projects": {"archi": {"description": "AI agent"}}}
                ideas = [self._make_idea(
                    "Create a puppy socialization checklist",
                    category="Puppy",
                )]
                idea_history = MagicMock()
                idea_history.is_stale.return_value = False
                filtered = _filter_ideas(ideas, gm, ctx, None, idea_history)
                assert len(filtered) == 1
            finally:
                _um_mod._instance = None

    def test_saturated_topic_filtered(self):
        """Ideas matching 2+ saturated keywords are filtered out."""
        gm = _make_goal_manager({})
        ideas = [self._make_idea(
            "Create a puppy stretch routine for morning walks",
            category="Health",
        )]
        idea_history = MagicMock()
        idea_history.is_stale.return_value = False
        idea_history.get_saturated_topics.return_value = ["puppy", "stretch", "walking", "morning"]
        filtered = _filter_ideas(ideas, gm, {}, None, idea_history)
        assert len(filtered) == 0

    def test_single_saturated_keyword_passes(self):
        """Ideas with only 1 saturated keyword are NOT filtered (need 2+)."""
        gm = _make_goal_manager({})
        ideas = [self._make_idea(
            "Create a puppy socialization guide",
            category="Puppy",
        )]
        idea_history = MagicMock()
        idea_history.is_stale.return_value = False
        idea_history.get_saturated_topics.return_value = ["puppy", "stretch", "walking"]
        filtered = _filter_ideas(ideas, gm, {}, None, idea_history)
        assert len(filtered) == 1

    def test_non_life_category_still_filtered(self):
        """Non-life categories still go through relevance/purpose filters."""
        from src.core import user_model as _um_mod
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            _um_mod._instance = _um_mod.UserModel(data_dir=Path(td))
            try:
                gm = _make_goal_manager({})
                ctx = {"active_projects": {"archi": {"description": "AI agent"}}}
                # Generic Capability category — should be filtered (no project match)
                ideas = [self._make_idea(
                    "Plan a vacation to Europe",
                    category="Capability",
                )]
                idea_history = MagicMock()
                idea_history.is_stale.return_value = False
                filtered = _filter_ideas(ideas, gm, ctx, None, idea_history)
                assert len(filtered) == 0
            finally:
                _um_mod._instance = None


# ---- _is_saturated_topic ----

class TestIsSaturatedTopic:
    """Tests for _is_saturated_topic()."""

    def test_no_saturated_keywords(self):
        ih = MagicMock()
        ih.get_saturated_topics.return_value = []
        assert _is_saturated_topic("anything goes", ih) is False

    def test_two_hits_returns_true(self):
        ih = MagicMock()
        ih.get_saturated_topics.return_value = ["puppy", "stretch", "fitness"]
        assert _is_saturated_topic("Daily puppy stretch guide", ih) is True

    def test_one_hit_returns_false(self):
        ih = MagicMock()
        ih.get_saturated_topics.return_value = ["puppy", "stretch", "fitness"]
        assert _is_saturated_topic("Puppy socialization tips", ih) is False

    def test_case_insensitive(self):
        ih = MagicMock()
        ih.get_saturated_topics.return_value = ["puppy", "walking"]
        assert _is_saturated_topic("PUPPY WALKING routine", ih) is True


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

    def test_returns_max_1_idea(self, tmp_path):
        """Quality over quantity: suggest_work returns at most 1 idea (session 223)."""
        router = MagicMock()
        gm = _make_goal_manager({})
        ls = MagicMock()
        stop = threading.Event()

        # Mock scanner to return many ideas with high scores
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
        assert len(ideas) <= 1

    def test_low_score_returns_nothing(self, tmp_path):
        """If top idea score is below threshold, return nothing (session 223)."""
        router = MagicMock()
        gm = _make_goal_manager({})
        ls = MagicMock()
        stop = threading.Event()

        # Mock scanner with low-score ideas
        mock_opps = []
        for i in range(3):
            opp = MagicMock()
            opp.type = "build"
            opp.description = f"Low value idea {i}"
            opp.user_value = f"Minor improvement {i}"
            opp.target_files = [f"workspace/mod{i}.py"]
            opp.value_score = 0.3  # Low value
            opp.estimated_hours = 2.0  # High effort → score = 0.15
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
        assert ideas == []


# ── Adaptive retirement (session 199) ────────────────────────────────

class TestAdaptiveRetirement:
    def _make_ignored_task(self, task_id="stretch", description="Stretch reminder",
                           created_by="user", ack=1, ignored=9):
        """Create a mock ScheduledTask that looks ignored."""
        task = MagicMock()
        task.id = task_id
        task.description = description
        task.created_by = created_by
        task.enabled = True
        task.stats = MagicMock()
        task.stats.times_acknowledged = ack
        task.stats.times_ignored = ignored
        return task

    @patch("src.core.scheduler.modify_task")
    @patch("src.core.scheduler.get_ignored_tasks")
    def test_user_task_proposed_not_retired(self, mock_get, mock_update):
        """User-created tasks should be proposed, not auto-retired."""
        mock_get.return_value = [self._make_ignored_task(created_by="user")]
        results = check_retirement_candidates()
        assert len(results) == 1
        assert results[0]["action"] == "propose"
        assert results[0]["created_by"] == "user"
        mock_update.assert_not_called()

    @patch("src.core.scheduler.modify_task")
    @patch("src.core.scheduler.get_ignored_tasks")
    def test_archi_task_auto_retired(self, mock_get, mock_update):
        """Archi-created tasks should be auto-disabled."""
        mock_get.return_value = [self._make_ignored_task(
            task_id="archi-idea", created_by="archi")]
        results = check_retirement_candidates()
        assert len(results) == 1
        assert results[0]["action"] == "retired"
        mock_update.assert_called_once_with("archi-idea", enabled=False)

    @patch("src.core.scheduler.get_ignored_tasks")
    def test_empty_when_no_ignored(self, mock_get):
        mock_get.return_value = []
        assert check_retirement_candidates() == []

    @patch("src.core.scheduler.modify_task")
    @patch("src.core.scheduler.get_ignored_tasks")
    def test_mixed_tasks(self, mock_get, mock_update):
        """Both user and Archi tasks handled correctly in same batch."""
        mock_get.return_value = [
            self._make_ignored_task("user-task", "User task", "user"),
            self._make_ignored_task("archi-task", "Archi task", "archi"),
        ]
        results = check_retirement_candidates()
        assert len(results) == 2
        proposed = [r for r in results if r["action"] == "propose"]
        retired = [r for r in results if r["action"] == "retired"]
        assert len(proposed) == 1
        assert len(retired) == 1

    def test_format_retirement_message_proposed(self):
        candidates = [{"action": "propose", "description": "Stretch", "ignore_rate": 0.8}]
        msg = format_retirement_message(candidates)
        assert "Stretch" in msg
        assert "80%" in msg

    def test_format_retirement_message_retired(self):
        candidates = [{"action": "retired", "description": "Daily summary"}]
        msg = format_retirement_message(candidates)
        assert "Daily summary" in msg
        assert "turned off" in msg

    def test_format_retirement_message_mixed(self):
        candidates = [
            {"action": "retired", "description": "Auto task"},
            {"action": "propose", "description": "User task", "ignore_rate": 0.75},
        ]
        msg = format_retirement_message(candidates)
        assert "Auto task" in msg
        assert "User task" in msg


# ── Autonomous scheduling tests (session 199) ──────────────────────


class TestSuggestScheduledTasks:
    """Tests for autonomous scheduling — pattern detection and proposal."""

    @patch("src.core.idea_generator._last_schedule_propose", None)
    @patch("src.core.idea_generator._gather_scheduling_evidence")
    def test_returns_empty_when_no_evidence(self, mock_evidence):
        """No proposals when there's no evidence."""
        mock_evidence.return_value = ""
        router = MagicMock()
        assert suggest_scheduled_tasks(router) == []
        router.generate.assert_not_called()

    def test_returns_empty_when_no_router(self):
        """No proposals without a router."""
        assert suggest_scheduled_tasks(None) == []

    @patch("src.core.idea_generator._last_schedule_propose", None)
    @patch("src.core.idea_generator._get_existing_schedule_descriptions", return_value=[])
    @patch("src.core.idea_generator._gather_scheduling_evidence", return_value="Some evidence")
    @patch("src.core.idea_generator._model_schedule_proposal")
    def test_delegates_to_model(self, mock_model, mock_evidence, mock_existing):
        """Evidence is passed to the model for analysis."""
        mock_model.return_value = [{"task_id": "test", "description": "test"}]
        router = MagicMock()
        result = suggest_scheduled_tasks(router)
        mock_model.assert_called_once_with(router, "Some evidence", [])
        assert len(result) == 1

    @patch("src.core.idea_generator._last_schedule_propose", None)
    @patch("src.core.idea_generator._gather_scheduling_evidence", return_value="data")
    @patch("src.core.idea_generator._get_existing_schedule_descriptions", return_value=[])
    @patch("src.core.idea_generator._model_schedule_proposal", return_value=[])
    def test_cooldown_prevents_repeat(self, mock_model, mock_existing, mock_evidence):
        """Cooldown prevents rapid repeated proposals."""
        import src.core.idea_generator as ig
        router = MagicMock()
        suggest_scheduled_tasks(router)
        # Second call should be blocked by cooldown
        result = suggest_scheduled_tasks(router)
        assert result == []
        assert mock_model.call_count == 1  # Only first call went through
        ig._last_schedule_propose = None  # Reset


class TestGatherSchedulingEvidence:
    @patch("src.core.journal.get_recent_entries", side_effect=Exception("no journal"))
    def test_graceful_on_journal_error(self, _mock):
        """Should not crash if journal fails."""
        result = _gather_scheduling_evidence()
        assert isinstance(result, str)

    @patch("src.core.journal.get_recent_entries")
    def test_includes_task_entries(self, mock_entries):
        mock_entries.return_value = [
            {"type": "task_completed", "time": "2026-03-05T10:00:00", "content": "Researched APIs"},
            {"type": "conversation", "time": "2026-03-05T11:00:00", "content": "Asked about weather"},
        ]
        result = _gather_scheduling_evidence()
        assert "Researched APIs" in result
        assert "Asked about weather" in result


class TestGetExistingScheduleDescriptions:
    @patch("src.core.scheduler.load_schedule")
    def test_returns_lowercase_descriptions(self, mock_load):
        t1 = MagicMock()
        t1.description = "Morning Stretch"
        t2 = MagicMock()
        t2.description = "Evening Review"
        mock_load.return_value = [t1, t2]
        result = _get_existing_schedule_descriptions()
        assert result == ["morning stretch", "evening review"]

    @patch("src.core.scheduler.load_schedule", side_effect=Exception("oops"))
    def test_returns_empty_on_error(self, _mock):
        assert _get_existing_schedule_descriptions() == []


class TestCreateProposedSchedules:
    @patch("src.core.scheduler.create_task")
    def test_notify_tasks_proposed_not_created(self, mock_create):
        """Notify tasks returned for user approval, not auto-created."""
        proposals = [{"task_id": "reminder", "description": "Stretch", "cron": "0 15 * * *",
                      "action": "notify", "payload": "Time to stretch!"}]
        created, proposed = create_proposed_schedules(proposals)
        assert len(proposed) == 1
        assert len(created) == 0
        mock_create.assert_not_called()

    @patch("src.core.scheduler.create_task")
    def test_goal_tasks_created_silently(self, mock_create):
        """Non-notify tasks are auto-created."""
        mock_create.return_value = MagicMock()
        proposals = [{"task_id": "weekly-review", "description": "Code review",
                      "cron": "0 9 * * 1", "action": "create_goal",
                      "payload": "Review recent code changes"}]
        created, proposed = create_proposed_schedules(proposals)
        assert len(created) == 1
        assert len(proposed) == 0
        mock_create.assert_called_once()

    @patch("src.core.scheduler.create_task", return_value=None)
    def test_failed_create_not_in_results(self, mock_create):
        """Failed creations don't appear in results."""
        proposals = [{"task_id": "bad", "description": "Bad", "cron": "invalid",
                      "action": "create_goal", "payload": "X"}]
        created, proposed = create_proposed_schedules(proposals)
        assert len(created) == 0

    @patch("src.core.scheduler.create_task")
    def test_mixed_proposals(self, mock_create):
        """Mixed notify + create_goal proposals handled correctly."""
        mock_create.return_value = MagicMock()
        proposals = [
            {"task_id": "notify-1", "description": "Reminder", "cron": "0 9 * * *",
             "action": "notify", "payload": "Hey"},
            {"task_id": "goal-1", "description": "Review", "cron": "0 10 * * 1",
             "action": "create_goal", "payload": "Do review"},
        ]
        created, proposed = create_proposed_schedules(proposals)
        assert len(created) == 1
        assert len(proposed) == 1


class TestFormatScheduleProposalMessage:
    def test_empty_proposals(self):
        assert format_schedule_proposal_message([]) == ""

    def test_single_proposal(self):
        proposals = [{"description": "Morning stretch", "cron": "0 8 * * *",
                      "reasoning": "You stretch every morning around 8"}]
        msg = format_schedule_proposal_message(proposals)
        assert "Morning stretch" in msg
        assert "0 8 * * *" in msg
        assert "You stretch every morning around 8" in msg
        assert "set any of these up" in msg

    def test_multiple_proposals(self):
        proposals = [
            {"description": "Task A", "cron": "0 9 * * *", "reasoning": ""},
            {"description": "Task B", "cron": "0 17 * * 5", "reasoning": "Friday pattern"},
        ]
        msg = format_schedule_proposal_message(proposals)
        assert "Task A" in msg
        assert "Task B" in msg


# ── Interest-driven exploration (session 202) ───────────────────

class TestExploreInterest:
    def test_no_router_returns_none(self):
        from src.core.idea_generator import explore_interest
        assert explore_interest(None) is None

    @patch("src.core.idea_generator.get_user_name", return_value="Jesse")
    def test_no_interests_returns_none(self, _name):
        from src.core.idea_generator import explore_interest
        with patch("src.core.worldview.get_interests", return_value=[]):
            assert explore_interest(MagicMock()) is None

    @patch("src.core.idea_generator.get_user_name", return_value="Jesse")
    def test_interesting_exploration_returns_result(self, _name):
        from src.core.idea_generator import explore_interest
        mock_router = MagicMock()
        mock_router.generate.return_value = {
            "text": json.dumps({
                "found_interesting": True,
                "summary": "Found a surprising connection between sleep and memory consolidation.",
                "commentary": "This changes how I think about rest.",
                "new_questions": ["How does napping compare?"],
                "connects_to": ["memory", "health"],
            }),
        }
        interests = [{"topic": "sleep science", "curiosity_level": 0.8, "notes": ""}]
        with patch("src.core.worldview.get_interests", return_value=interests), \
             patch("src.core.worldview.add_interest") as mock_add, \
             patch("src.core.worldview.get_worldview_context", return_value=""), \
             patch("src.core.journal.add_entry"):
            result = explore_interest(mock_router)
            assert result is not None
            assert result["topic"] == "sleep science"
            assert "memory" in result["summary"].lower()
            # Should add related interests
            assert mock_add.call_count >= 1

    @patch("src.core.idea_generator.get_user_name", return_value="Jesse")
    def test_uninteresting_exploration_returns_none(self, _name):
        from src.core.idea_generator import explore_interest
        mock_router = MagicMock()
        mock_router.generate.return_value = {
            "text": json.dumps({"found_interesting": False}),
        }
        interests = [{"topic": "APIs", "curiosity_level": 0.6, "notes": ""}]
        with patch("src.core.worldview.get_interests", return_value=interests), \
             patch("src.core.worldview.add_interest"), \
             patch("src.core.worldview.get_worldview_context", return_value=""), \
             patch("src.core.journal.add_entry"):
            result = explore_interest(mock_router)
            assert result is None

    @patch("src.core.idea_generator.get_user_name", return_value="Jesse")
    def test_router_failure_returns_none(self, _name):
        from src.core.idea_generator import explore_interest
        mock_router = MagicMock()
        mock_router.generate.side_effect = Exception("API error")
        interests = [{"topic": "quantum", "curiosity_level": 0.7, "notes": ""}]
        with patch("src.core.worldview.get_interests", return_value=interests), \
             patch("src.core.worldview.get_worldview_context", return_value=""):
            result = explore_interest(mock_router)
            assert result is None


# ── Interest Picking / Saturation (session 217) ──────────────────

class TestPickExplorationInterest:
    def test_returns_none_for_empty_list(self):
        from src.core.idea_generator import _pick_exploration_interest
        assert _pick_exploration_interest([]) is None

    def test_returns_single_interest(self):
        from src.core.idea_generator import _pick_exploration_interest
        interests = [{"topic": "astronomy", "curiosity_level": 0.6}]
        with patch("src.core.idea_history.get_idea_history") as mock_hist:
            mock_hist.return_value.get_saturated_topics.return_value = []
            result = _pick_exploration_interest(interests)
            assert result["topic"] == "astronomy"

    def test_filters_saturated_interests(self):
        from src.core.idea_generator import _pick_exploration_interest
        interests = [
            {"topic": "health and wellness", "curiosity_level": 0.5},
            {"topic": "astronomy", "curiosity_level": 0.5},
        ]
        with patch("src.core.idea_history.get_idea_history") as mock_hist:
            mock_hist.return_value.get_saturated_topics.return_value = ["health", "wellness"]
            result = _pick_exploration_interest(interests)
            assert result["topic"] == "astronomy"

    def test_falls_back_to_all_if_everything_saturated(self):
        from src.core.idea_generator import _pick_exploration_interest
        interests = [
            {"topic": "health and wellness", "curiosity_level": 0.5},
        ]
        with patch("src.core.idea_history.get_idea_history") as mock_hist:
            mock_hist.return_value.get_saturated_topics.return_value = ["health", "wellness"]
            result = _pick_exploration_interest(interests)
            assert result is not None  # falls back rather than returning None

    def test_prefers_least_recently_explored(self):
        from src.core.idea_generator import _pick_exploration_interest
        interests = [
            {"topic": "music", "curiosity_level": 0.5, "last_explored": "2026-03-06"},
            {"topic": "art", "curiosity_level": 0.5, "last_explored": "2026-03-01"},
            {"topic": "coding", "curiosity_level": 0.5},  # never explored
        ]
        with patch("src.core.idea_history.get_idea_history") as mock_hist:
            mock_hist.return_value.get_saturated_topics.return_value = []
            result = _pick_exploration_interest(interests)
            assert result["topic"] == "coding"  # never explored = earliest

    def test_curiosity_tier_grouping(self):
        from src.core.idea_generator import _pick_exploration_interest
        interests = [
            {"topic": "A", "curiosity_level": 0.8, "last_explored": "2026-03-06"},
            {"topic": "B", "curiosity_level": 0.75, "last_explored": ""},  # in tier, never explored
            {"topic": "C", "curiosity_level": 0.3, "last_explored": ""},  # too low curiosity
        ]
        with patch("src.core.idea_history.get_idea_history") as mock_hist:
            mock_hist.return_value.get_saturated_topics.return_value = []
            result = _pick_exploration_interest(interests)
            assert result["topic"] == "B"  # same tier as A, but less recently explored

    def test_filters_by_notes_keywords(self):
        """Notes field keywords match saturated topics even when topic name doesn't."""
        from src.core.idea_generator import _pick_exploration_interest
        interests = [
            {"topic": "health and wellness", "curiosity_level": 0.5,
             "notes": "Emerged from task: beginner training for female Border Collie"},
            {"topic": "astronomy", "curiosity_level": 0.5, "notes": ""},
        ]
        with patch("src.core.idea_history.get_idea_history") as mock_hist:
            # "training" and "female" appear in notes, matching saturated keywords
            mock_hist.return_value.get_saturated_topics.return_value = [
                "training", "female", "puppy", "collie", "border",
            ]
            result = _pick_exploration_interest(interests)
            assert result["topic"] == "astronomy"

    def test_filters_child_of_saturated_parent(self):
        """Child interests (notes='Related to X') filtered when parent is saturated."""
        from src.core.idea_generator import _pick_exploration_interest
        interests = [
            {"topic": "health and wellness", "curiosity_level": 0.5,
             "notes": "Emerged from task: training female puppy"},
            {"topic": "osteoporosis prevention", "curiosity_level": 0.4,
             "notes": "Related to health and wellness"},
            {"topic": "software development", "curiosity_level": 0.4, "notes": ""},
        ]
        with patch("src.core.idea_history.get_idea_history") as mock_hist:
            mock_hist.return_value.get_saturated_topics.return_value = [
                "training", "female", "puppy",
            ]
            result = _pick_exploration_interest(interests)
            # Both health and its child osteoporosis should be filtered
            assert result["topic"] == "software development"

    def test_single_keyword_overlap_not_filtered(self):
        """Interests with only 1 saturated keyword match should NOT be filtered."""
        from src.core.idea_generator import _pick_exploration_interest
        interests = [
            {"topic": "hormone-influenced fitness adaptations", "curiosity_level": 0.5,
             "notes": "Related to health and wellness"},
            {"topic": "astronomy", "curiosity_level": 0.4, "notes": ""},
        ]
        with patch("src.core.idea_history.get_idea_history") as mock_hist:
            # Only "fitness" matches — below threshold of 2
            mock_hist.return_value.get_saturated_topics.return_value = ["fitness", "puppy", "stretch"]
            result = _pick_exploration_interest(interests)
            assert result["topic"] == "hormone-influenced fitness adaptations"


# ── Personal Projects (session 203) ──────────────────────────────

class TestProposePersonalProject:
    @patch("src.core.idea_generator.get_user_name", return_value="Jesse")
    def test_propose_creates_project(self, _name):
        from src.core.idea_generator import propose_personal_project
        mock_router = MagicMock()
        mock_router.generate.return_value = {
            "text": json.dumps({
                "start_project": True,
                "title": "API Documentation Patterns",
                "description": "Catalog common API patterns for faster task execution",
            }),
        }
        interests = [{"topic": "API design", "curiosity_level": 0.7, "notes": "Interesting",
                       "last_explored": "2026-03-01"}]
        with patch("src.core.worldview.get_interests", return_value=interests), \
             patch("src.core.worldview.get_personal_projects", return_value=[]), \
             patch("src.core.worldview.add_personal_project", return_value={"title": "API Documentation Patterns"}) as mock_add, \
             patch("src.core.journal.add_entry"):
            result = propose_personal_project(mock_router)
            assert result is not None
            mock_add.assert_called_once()

    @patch("src.core.idea_generator.get_user_name", return_value="Jesse")
    def test_no_proposal_when_not_interesting(self, _name):
        from src.core.idea_generator import propose_personal_project
        mock_router = MagicMock()
        mock_router.generate.return_value = {
            "text": json.dumps({"start_project": False}),
        }
        interests = [{"topic": "boring topic", "curiosity_level": 0.6, "notes": "",
                       "last_explored": "2026-03-01"}]
        with patch("src.core.worldview.get_interests", return_value=interests), \
             patch("src.core.worldview.get_personal_projects", return_value=[]):
            result = propose_personal_project(mock_router)
            assert result is None

    def test_no_proposal_without_router(self):
        from src.core.idea_generator import propose_personal_project
        assert propose_personal_project(None) is None

    @patch("src.core.idea_generator.get_user_name", return_value="Jesse")
    def test_skips_interests_with_existing_projects(self, _name):
        from src.core.idea_generator import propose_personal_project
        mock_router = MagicMock()
        interests = [{"topic": "API design", "curiosity_level": 0.7, "notes": "",
                       "last_explored": "2026-03-01"}]
        existing_projects = [{"origin_interest": "API design", "status": "active"}]
        with patch("src.core.worldview.get_interests", return_value=interests), \
             patch("src.core.worldview.get_personal_projects", return_value=existing_projects):
            result = propose_personal_project(mock_router)
            assert result is None
            mock_router.generate.assert_not_called()


class TestWorkOnPersonalProject:
    @patch("src.core.idea_generator.get_user_name", return_value="Jesse")
    def test_work_session_updates_project(self, _name):
        from src.core.idea_generator import work_on_personal_project
        mock_router = MagicMock()
        mock_router.generate.return_value = {
            "text": json.dumps({
                "progress_note": "Identified 3 common API patterns in recent tasks",
                "share_worthy": True,
                "share_message": "Found some interesting API patterns",
                "should_continue": True,
                "next_step": "Analyze which patterns are most useful",
            }),
        }
        projects = [{"title": "API KB", "description": "Build KB", "status": "active",
                      "progress_notes": [], "work_sessions": 2, "last_worked": "2026-03-01"}]
        with patch("src.core.worldview.get_personal_projects", return_value=projects), \
             patch("src.core.worldview.update_personal_project") as mock_update, \
             patch("src.core.worldview.get_worldview_context", return_value=""), \
             patch("src.core.journal.add_entry"):
            result = work_on_personal_project(mock_router)
            assert result is not None
            assert result["share_worthy"] is True
            assert "API patterns" in result["progress"]
            mock_update.assert_called_once()

    @patch("src.core.idea_generator.get_user_name", return_value="Jesse")
    def test_completes_stalled_project(self, _name):
        from src.core.idea_generator import work_on_personal_project
        mock_router = MagicMock()
        mock_router.generate.return_value = {
            "text": json.dumps({
                "progress_note": "This project isn't going anywhere useful",
                "share_worthy": False,
                "share_message": "",
                "should_continue": False,
                "next_step": "",
            }),
        }
        projects = [{"title": "Stalled proj", "description": "Nothing useful",
                      "status": "active", "progress_notes": [], "work_sessions": 5,
                      "last_worked": "2026-02-20"}]
        with patch("src.core.worldview.get_personal_projects", return_value=projects), \
             patch("src.core.worldview.update_personal_project") as mock_update, \
             patch("src.core.worldview.get_worldview_context", return_value=""), \
             patch("src.core.journal.add_entry"):
            result = work_on_personal_project(mock_router)
            assert result is not None
            # Should have been called with status="completed"
            call_kwargs = mock_update.call_args
            assert call_kwargs[1].get("status") == "completed" or \
                   (len(call_kwargs[0]) > 2 and call_kwargs[0][2] == "completed")

    def test_no_work_without_projects(self):
        from src.core.idea_generator import work_on_personal_project
        mock_router = MagicMock()
        with patch("src.core.worldview.get_personal_projects", return_value=[]):
            result = work_on_personal_project(mock_router)
            assert result is None


class TestMetaCognition:
    @patch("src.core.idea_generator.get_user_name", return_value="Jesse")
    def test_generates_observations(self, _name):
        from src.core.idea_generator import generate_meta_cognition
        mock_router = MagicMock()
        mock_router.generate.return_value = {
            "text": json.dumps({
                "observations": [
                    {"pattern": "I over-estimate complexity on research tasks",
                     "category": "estimation",
                     "adjustment": "Try simpler approaches first"},
                ],
            }),
        }
        with patch("src.core.behavioral_rules.load", return_value={
                 "avoidance": [{"pattern": "Avoid long searches"}],
                 "preference": [{"pattern": "Prefer concise output"}],
             }), \
             patch("src.core.worldview.get_taste_context", return_value="research works well"), \
             patch("src.core.journal.get_recent_entries", return_value=[
                 {"type": "task_completed"}, {"type": "task_completed"},
             ]), \
             patch("src.core.worldview.get_meta_context", return_value=""), \
             patch("src.core.worldview.add_meta_observation") as mock_add, \
             patch("src.core.worldview.update_meta_adjustment") as mock_adj, \
             patch("src.core.journal.add_entry"):
            result = generate_meta_cognition(mock_router)
            assert result is not None
            assert len(result) == 1
            mock_add.assert_called_once()
            mock_adj.assert_called_once()

    def test_no_meta_without_router(self):
        from src.core.idea_generator import generate_meta_cognition
        assert generate_meta_cognition(None) is None

    def test_insufficient_evidence_returns_none(self):
        from src.core.idea_generator import generate_meta_cognition
        mock_router = MagicMock()
        # Only one evidence source — need at least 2
        with patch("src.core.behavioral_rules.load", side_effect=ImportError), \
             patch("src.core.worldview.get_taste_context", return_value=""), \
             patch("src.core.journal.get_recent_entries", return_value=[]), \
             patch("src.core.worldview.get_meta_context", return_value=""):
            result = generate_meta_cognition(mock_router)
            assert result is None
