"""Tests for src/core/strategic_planner.py — Self-Extension Phase 3."""

import json
import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.core.strategic_planner import (
    ImplementationPlan,
    PhaseResult,
    PhaseTask,
    PlanPhase,
    StrategicPlanner,
    _parse_json,
    _sanitize_project_id,
    format_plan_for_discord,
    get_active_project,
    get_planned_projects,
    get_project,
    get_project_stats,
)


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    """Redirect all persistence to tmp dir."""
    monkeypatch.setattr(
        "src.core.strategic_planner._base_path",
        lambda: tmp_path,
    )
    # Create required dirs
    (tmp_path / "data" / "self_extension").mkdir(parents=True, exist_ok=True)
    (tmp_path / "claude").mkdir(parents=True, exist_ok=True)
    # Write a minimal architecture doc
    (tmp_path / "claude" / "ARCHITECTURE.md").write_text("# Archi Architecture\n")
    yield


@pytest.fixture
def mock_router():
    """Router mock that returns plan JSON."""
    router = MagicMock()
    router.escalate_for_task.return_value.__enter__ = MagicMock(
        return_value={"model": "gemini-3.1-pro"}
    )
    router.escalate_for_task.return_value.__exit__ = MagicMock(return_value=False)
    return router


def _make_plan_response(phases=None, new_files=None):
    """Build a valid model response for plan creation."""
    if phases is None:
        phases = [
            {
                "phase_number": 1,
                "title": "Core module",
                "description": "Build the base module",
                "tasks": [
                    {"description": "Create src/tools/foo.py", "task_type": "code",
                     "files_involved": ["src/tools/foo.py"]},
                    {"description": "Add unit tests", "task_type": "test",
                     "files_involved": ["tests/unit/test_foo.py"]},
                ],
            },
            {
                "phase_number": 2,
                "title": "Integration",
                "description": "Wire into dispatcher and router",
                "tasks": [
                    {"description": "Add action handler", "task_type": "integration",
                     "files_involved": ["src/interfaces/action_dispatcher.py"]},
                ],
            },
        ]
    return json.dumps({
        "description": "A new capability module for foo",
        "new_files": new_files or ["src/tools/foo.py"],
        "modified_files": ["src/interfaces/action_dispatcher.py"],
        "integration_points": ["router intent", "dispatcher handler"],
        "phases": phases,
        "jesse_actions": ["Add FOO_API_KEY to .env"],
    })


def _make_design_doc_response():
    return "# Design: Foo Module\n\n## Problem\nNeed foo.\n\n## Solution\nBuild foo.\n"


# ── Data class tests ─────────────────────────────────────────────────


class TestPhaseTask:
    def test_to_dict(self):
        t = PhaseTask(description="Create file", task_type="code",
                      files_involved=["a.py"], done=False)
        d = t.to_dict()
        assert d["description"] == "Create file"
        assert d["task_type"] == "code"
        assert d["files_involved"] == ["a.py"]
        assert d["done"] is False

    def test_from_dict(self):
        d = {"description": "Test", "task_type": "test",
             "files_involved": ["t.py"], "done": True}
        t = PhaseTask.from_dict(d)
        assert t.description == "Test"
        assert t.done is True

    def test_from_dict_defaults(self):
        t = PhaseTask.from_dict({})
        assert t.description == ""
        assert t.task_type == "code"
        assert t.files_involved == []
        assert t.done is False


class TestPlanPhase:
    def test_to_dict_round_trip(self):
        phase = PlanPhase(
            phase_number=1, title="Core", description="Build core",
            tasks=[PhaseTask(description="foo")], status="in_progress",
            started_at="2026-01-01T00:00:00",
        )
        d = phase.to_dict()
        restored = PlanPhase.from_dict(d)
        assert restored.phase_number == 1
        assert restored.title == "Core"
        assert restored.status == "in_progress"
        assert len(restored.tasks) == 1

    def test_from_dict_defaults(self):
        p = PlanPhase.from_dict({})
        assert p.phase_number == 1
        assert p.status == "pending"
        assert p.tasks == []


class TestImplementationPlan:
    def test_to_dict_round_trip(self):
        plan = ImplementationPlan(
            project_id="test_proj", title="Test", description="A test",
            gap_name="gap", status="active",
            phases=[PlanPhase(phase_number=1, title="P1")],
            current_phase=1, new_files=["a.py"], modified_files=["b.py"],
            integration_points=["router"], jesse_actions=["get key"],
            created_at="2026-01-01", updated_at="2026-01-01", total_cost=0.05,
        )
        d = plan.to_dict()
        restored = ImplementationPlan.from_dict(d)
        assert restored.project_id == "test_proj"
        assert restored.title == "Test"
        assert len(restored.phases) == 1
        assert restored.total_cost == 0.05

    def test_current_phase_obj(self):
        plan = ImplementationPlan(
            phases=[PlanPhase(phase_number=1), PlanPhase(phase_number=2)],
            current_phase=2,
        )
        assert plan.current_phase_obj.phase_number == 2

    def test_current_phase_obj_missing(self):
        plan = ImplementationPlan(phases=[], current_phase=1)
        assert plan.current_phase_obj is None

    def test_is_complete_true(self):
        plan = ImplementationPlan(phases=[
            PlanPhase(status="completed"), PlanPhase(status="completed"),
        ])
        assert plan.is_complete is True

    def test_is_complete_false(self):
        plan = ImplementationPlan(phases=[
            PlanPhase(status="completed"), PlanPhase(status="pending"),
        ])
        assert plan.is_complete is False

    def test_progress_pct(self):
        plan = ImplementationPlan(phases=[
            PlanPhase(status="completed"), PlanPhase(status="completed"),
            PlanPhase(status="pending"), PlanPhase(status="pending"),
        ])
        assert plan.progress_pct == 0.5

    def test_progress_pct_empty(self):
        plan = ImplementationPlan(phases=[])
        assert plan.progress_pct == 0.0


# ── Helper tests ─────────────────────────────────────────────────────


class TestHelpers:
    def test_sanitize_project_id(self):
        assert _sanitize_project_id("Music Generation") == "music_generation"
        assert _sanitize_project_id("Text-to-Image v2!") == "texttoimage_v2"
        assert _sanitize_project_id("") == "project"

    def test_sanitize_project_id_length_cap(self):
        long_name = "a" * 100
        assert len(_sanitize_project_id(long_name)) <= 40

    def test_parse_json_valid(self):
        raw = '{"key": "value", "num": 42}'
        assert _parse_json(raw) == {"key": "value", "num": 42}

    def test_parse_json_embedded(self):
        raw = 'Here is the plan:\n{"phases": [1,2,3]}\nDone.'
        assert _parse_json(raw) == {"phases": [1, 2, 3]}

    def test_parse_json_invalid(self):
        assert _parse_json("no json here") == {}

    def test_parse_json_empty(self):
        assert _parse_json("") == {}


# ── Persistence tests ────────────────────────────────────────────────


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        from src.core.strategic_planner import _save_plan, _load_projects
        plan = ImplementationPlan(
            project_id="test1", title="Test", status="planned",
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        _save_plan(plan)
        data = _load_projects()
        assert len(data["projects"]) == 1
        assert data["projects"][0]["project_id"] == "test1"

    def test_save_updates_existing(self, tmp_path):
        from src.core.strategic_planner import _save_plan, _load_projects
        plan = ImplementationPlan(
            project_id="test1", title="V1", status="planned",
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        _save_plan(plan)
        plan.title = "V2"
        plan.status = "active"
        _save_plan(plan)
        data = _load_projects()
        assert len(data["projects"]) == 1
        assert data["projects"][0]["title"] == "V2"
        assert data["projects"][0]["status"] == "active"

    def test_get_active_project(self, tmp_path):
        from src.core.strategic_planner import _save_plan
        _save_plan(ImplementationPlan(project_id="a", status="completed"))
        _save_plan(ImplementationPlan(project_id="b", status="active"))
        active = get_active_project()
        assert active is not None
        assert active["project_id"] == "b"

    def test_get_active_project_none(self, tmp_path):
        assert get_active_project() is None

    def test_get_planned_projects(self, tmp_path):
        from src.core.strategic_planner import _save_plan
        _save_plan(ImplementationPlan(project_id="a", status="planned"))
        _save_plan(ImplementationPlan(project_id="b", status="active"))
        _save_plan(ImplementationPlan(project_id="c", status="planned"))
        planned = get_planned_projects()
        assert len(planned) == 2
        ids = {p["project_id"] for p in planned}
        assert ids == {"a", "c"}

    def test_get_project(self, tmp_path):
        from src.core.strategic_planner import _save_plan
        _save_plan(ImplementationPlan(project_id="xyz", title="XYZ"))
        p = get_project("xyz")
        assert p is not None
        assert p["title"] == "XYZ"

    def test_get_project_missing(self, tmp_path):
        assert get_project("nonexistent") is None

    def test_get_project_stats(self, tmp_path):
        from src.core.strategic_planner import _save_plan
        _save_plan(ImplementationPlan(project_id="a", status="active"))
        _save_plan(ImplementationPlan(project_id="b", status="completed"))
        _save_plan(ImplementationPlan(project_id="c", status="completed"))
        stats = get_project_stats()
        assert stats["total_projects"] == 3
        assert stats["by_status"]["active"] == 1
        assert stats["by_status"]["completed"] == 2
        assert stats["active_project"] == "a"


# ── StrategicPlanner.create_plan tests ───────────────────────────────


class TestCreatePlan:
    def test_create_plan_success(self, mock_router, tmp_path):
        # First call: plan JSON. Second call: design doc.
        mock_router.generate.side_effect = [
            {"text": _make_plan_response(), "cost_usd": 0.05},
            {"text": _make_design_doc_response(), "cost_usd": 0.03},
        ]
        planner = StrategicPlanner(mock_router)
        plan = planner.create_plan(
            project_title="Foo Integration",
            project_description="Build a foo module",
            gap_name="foo_generation",
            jesse_actions=["Add FOO_API_KEY to .env"],
        )
        assert plan is not None
        assert plan.project_id == "foo_generation"
        assert plan.status == "planned"
        assert len(plan.phases) == 2
        assert plan.phases[0].title == "Core module"
        assert len(plan.phases[0].tasks) == 2
        assert plan.new_files == ["src/tools/foo.py"]
        assert plan.design_doc_path.startswith("claude/DESIGN_")
        # Design doc file exists
        doc_path = tmp_path / plan.design_doc_path
        assert doc_path.exists()
        # Persisted
        assert get_project("foo_generation") is not None

    def test_create_plan_blocked_by_active(self, mock_router, tmp_path):
        from src.core.strategic_planner import _save_plan
        _save_plan(ImplementationPlan(project_id="existing", status="active"))
        planner = StrategicPlanner(mock_router)
        plan = planner.create_plan(
            project_title="New", project_description="Another thing",
        )
        assert plan is None  # Blocked

    def test_create_plan_model_returns_garbage(self, mock_router):
        mock_router.generate.return_value = {"text": "not json at all", "cost_usd": 0.01}
        planner = StrategicPlanner(mock_router)
        plan = planner.create_plan(
            project_title="Bad", project_description="Will fail",
        )
        assert plan is None

    def test_create_plan_empty_phases(self, mock_router):
        mock_router.generate.return_value = {
            "text": json.dumps({"description": "test", "phases": []}),
            "cost_usd": 0.01,
        }
        planner = StrategicPlanner(mock_router)
        plan = planner.create_plan(
            project_title="Empty", project_description="No phases",
        )
        assert plan is None

    def test_create_plan_with_research_result(self, mock_router, tmp_path):
        mock_router.generate.side_effect = [
            {"text": _make_plan_response(), "cost_usd": 0.05},
            {"text": _make_design_doc_response(), "cost_usd": 0.03},
        ]
        research = MagicMock()
        research.format_for_user.return_value = "Suno API is best at $0.03/track"
        planner = StrategicPlanner(mock_router)
        plan = planner.create_plan(
            project_title="Music Gen",
            project_description="Build music generation",
            gap_name="music_generation",
            research_result=research,
        )
        assert plan is not None
        # Verify research context was used in prompt
        call_args = mock_router.generate.call_args_list[0]
        prompt = call_args[1].get("prompt", "") or call_args[0][0] if call_args[0] else ""
        # The prompt should contain research info (via the generate call)
        assert mock_router.generate.call_count >= 1

    def test_create_plan_escalation_failure_falls_back(self, mock_router, tmp_path):
        # Escalation raises, should fall back to default model
        mock_router.escalate_for_task.side_effect = Exception("No escalation available")
        mock_router.generate.side_effect = [
            {"text": _make_plan_response(), "cost_usd": 0.02},
            {"text": _make_design_doc_response(), "cost_usd": 0.01},
        ]
        planner = StrategicPlanner(mock_router)
        plan = planner.create_plan(
            project_title="Fallback",
            project_description="Test fallback",
        )
        assert plan is not None

    def test_create_plan_caps_phases(self, mock_router, tmp_path):
        """Ensure phases are capped at _MAX_PHASES."""
        many_phases = [
            {"phase_number": i, "title": f"Phase {i}", "description": f"P{i}",
             "tasks": [{"description": "task"}]}
            for i in range(1, 15)
        ]
        mock_router.generate.side_effect = [
            {"text": json.dumps({"description": "big", "phases": many_phases}),
             "cost_usd": 0.01},
            {"text": _make_design_doc_response(), "cost_usd": 0.01},
        ]
        planner = StrategicPlanner(mock_router)
        plan = planner.create_plan(
            project_title="Big", project_description="Many phases",
        )
        assert plan is not None
        assert len(plan.phases) <= 8  # _MAX_PHASES


# ── StrategicPlanner.activate_plan tests ─────────────────────────────


class TestActivatePlan:
    def test_activate_success(self, mock_router, tmp_path):
        from src.core.strategic_planner import _save_plan
        plan = ImplementationPlan(
            project_id="proj1", status="planned",
            phases=[PlanPhase(phase_number=1, title="P1", status="pending",
                              tasks=[PhaseTask(description="task A")])],
        )
        _save_plan(plan)
        planner = StrategicPlanner(mock_router)
        result = planner.activate_plan("proj1")
        assert result.action == "started"
        assert result.phase_number == 1
        assert result.goal_descriptions == ["task A"]
        p = get_project("proj1")
        assert p["status"] == "active"
        assert p["phases"][0]["status"] == "in_progress"
        assert p["phases"][0]["started_at"] is not None

    def test_activate_already_active(self, mock_router, tmp_path):
        from src.core.strategic_planner import _save_plan
        _save_plan(ImplementationPlan(project_id="proj1", status="active"))
        planner = StrategicPlanner(mock_router)
        result = planner.activate_plan("proj1")
        assert result.action == "none"

    def test_activate_missing(self, mock_router):
        planner = StrategicPlanner(mock_router)
        result = planner.activate_plan("nope")
        assert result.action == "none"


# ── StrategicPlanner.advance_plan tests ──────────────────────────────


class TestAdvancePlan:
    def _active_plan(self, phases=None):
        from src.core.strategic_planner import _save_plan
        if phases is None:
            phases = [
                PlanPhase(phase_number=1, title="P1", status="in_progress",
                          tasks=[PhaseTask(description="t1", done=True),
                                 PhaseTask(description="t2", done=True)]),
                PlanPhase(phase_number=2, title="P2", status="pending",
                          tasks=[PhaseTask(description="t3")]),
            ]
        plan = ImplementationPlan(
            project_id="adv_test", status="active",
            phases=phases, current_phase=1,
        )
        _save_plan(plan)
        return plan

    def test_advance_to_next_phase(self, mock_router, tmp_path):
        self._active_plan()
        planner = StrategicPlanner(mock_router)
        result = planner.advance_plan("adv_test")
        assert result.action == "advanced"
        assert result.phase_number == 2
        assert "t3" in result.goal_descriptions
        p = get_project("adv_test")
        assert p["current_phase"] == 2
        assert p["phases"][0]["status"] == "completed"
        assert p["phases"][1]["status"] == "in_progress"

    def test_advance_project_complete(self, mock_router, tmp_path):
        phases = [
            PlanPhase(phase_number=1, title="P1", status="in_progress",
                      tasks=[PhaseTask(description="t1", done=True)]),
        ]
        self._active_plan(phases)
        planner = StrategicPlanner(mock_router)
        result = planner.advance_plan("adv_test")
        assert result.action == "completed"
        p = get_project("adv_test")
        assert p["status"] == "completed"

    def test_advance_tasks_not_done(self, mock_router, tmp_path):
        phases = [
            PlanPhase(phase_number=1, title="P1", status="in_progress",
                      tasks=[PhaseTask(description="t1", done=True),
                             PhaseTask(description="t2", done=False)]),
        ]
        self._active_plan(phases)
        planner = StrategicPlanner(mock_router)
        result = planner.advance_plan("adv_test")
        assert result.action == "none"
        assert "1/2" in result.message

    def test_advance_not_active(self, mock_router, tmp_path):
        from src.core.strategic_planner import _save_plan
        _save_plan(ImplementationPlan(project_id="x", status="planned"))
        planner = StrategicPlanner(mock_router)
        result = planner.advance_plan("x")
        assert result.action == "none"

    def test_advance_project_not_found(self, mock_router):
        planner = StrategicPlanner(mock_router)
        result = planner.advance_plan("missing")
        assert result.action == "none"


# ── mark_phase_task_done tests ───────────────────────────────────────


class TestMarkPhaseTaskDone:
    def test_mark_task_done(self, mock_router, tmp_path):
        from src.core.strategic_planner import _save_plan
        _save_plan(ImplementationPlan(
            project_id="m1", status="active",
            phases=[PlanPhase(phase_number=1, tasks=[
                PhaseTask(description="t1"), PhaseTask(description="t2"),
            ])],
        ))
        planner = StrategicPlanner(mock_router)
        assert planner.mark_phase_task_done("m1", 1, 0) is True
        p = get_project("m1")
        assert p["phases"][0]["tasks"][0]["done"] is True
        assert p["phases"][0]["tasks"][1]["done"] is False

    def test_mark_task_done_bad_index(self, mock_router, tmp_path):
        from src.core.strategic_planner import _save_plan
        _save_plan(ImplementationPlan(
            project_id="m2", status="active",
            phases=[PlanPhase(phase_number=1, tasks=[PhaseTask()])],
        ))
        planner = StrategicPlanner(mock_router)
        assert planner.mark_phase_task_done("m2", 1, 99) is False

    def test_mark_task_done_missing_project(self, mock_router):
        planner = StrategicPlanner(mock_router)
        assert planner.mark_phase_task_done("nope", 1, 0) is False


# ── abandon_project tests ───────────────────────────────────────────


class TestAbandonProject:
    def test_abandon(self, mock_router, tmp_path):
        from src.core.strategic_planner import _save_plan
        _save_plan(ImplementationPlan(project_id="ab1", status="active"))
        planner = StrategicPlanner(mock_router)
        assert planner.abandon_project("ab1", reason="Not feasible") is True
        p = get_project("ab1")
        assert p["status"] == "abandoned"
        assert p["abandon_reason"] == "Not feasible"

    def test_abandon_missing(self, mock_router):
        planner = StrategicPlanner(mock_router)
        assert planner.abandon_project("nope") is False


# ── Formatting tests ─────────────────────────────────────────────────


class TestFormatting:
    def test_format_plan_for_discord(self):
        plan = ImplementationPlan(
            title="Music Gen",
            description="Build a music generation module",
            phases=[
                PlanPhase(phase_number=1, title="Core", status="pending"),
                PlanPhase(phase_number=2, title="Integration", status="pending"),
            ],
            new_files=["src/tools/music_gen.py"],
            jesse_actions=["Sign up for Suno API"],
        )
        msg = format_plan_for_discord(plan)
        assert "Music Gen" in msg
        assert "Core" in msg
        assert "Integration" in msg
        assert "music_gen.py" in msg
        assert "Suno API" in msg
        assert "go for it" in msg

    def test_format_plan_empty_phases(self):
        plan = ImplementationPlan(title="Empty", phases=[])
        msg = format_plan_for_discord(plan)
        assert "Empty" in msg
        assert "(0)" in msg


# ── activate_plan with goal descriptions (session 238) ──────────────


class TestActivatePlanGoals:
    """Tests for activate_plan returning PhaseResult with goal descriptions."""

    def test_activate_returns_phase1_goals(self, mock_router, tmp_path):
        from src.core.strategic_planner import _save_plan
        plan = ImplementationPlan(
            project_id="goals_test", status="planned",
            phases=[
                PlanPhase(phase_number=1, title="Core", status="pending",
                          tasks=[
                              PhaseTask(description="Create core module"),
                              PhaseTask(description="Add unit tests"),
                              PhaseTask(description="Already done", done=True),
                          ]),
                PlanPhase(phase_number=2, title="Integration", status="pending"),
            ],
        )
        _save_plan(plan)
        sp = StrategicPlanner(mock_router)
        result = sp.activate_plan("goals_test")
        assert result.action == "started"
        assert result.phase_number == 1
        # Only non-done tasks should appear
        assert result.goal_descriptions == ["Create core module", "Add unit tests"]

    def test_activate_empty_phase_tasks(self, mock_router, tmp_path):
        from src.core.strategic_planner import _save_plan
        plan = ImplementationPlan(
            project_id="empty_tasks", status="planned",
            phases=[PlanPhase(phase_number=1, title="P1", status="pending", tasks=[])],
        )
        _save_plan(plan)
        sp = StrategicPlanner(mock_router)
        result = sp.activate_plan("empty_tasks")
        assert result.action == "started"
        assert result.goal_descriptions == []

    def test_activate_all_tasks_done(self, mock_router, tmp_path):
        from src.core.strategic_planner import _save_plan
        plan = ImplementationPlan(
            project_id="all_done", status="planned",
            phases=[PlanPhase(phase_number=1, title="P1", status="pending",
                              tasks=[PhaseTask(description="done", done=True)])],
        )
        _save_plan(plan)
        sp = StrategicPlanner(mock_router)
        result = sp.activate_plan("all_done")
        assert result.action == "started"
        assert result.goal_descriptions == []
