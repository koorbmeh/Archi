"""End-to-end tests for the Self-Extension system (Phase 5).

Tests the full cycle: plan → activate → goals → complete → advance → project complete.
Also tests failure propagation, edge cases, and resume logic.

Session 239: Self-Extension Phase 5.
"""

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.goal_manager import Goal, GoalManager, Task, TaskStatus
from src.core.strategic_planner import (
    ImplementationPlan,
    PhaseResult,
    PhaseTask,
    PlanPhase,
    StrategicPlanner,
    _save_plan,
    get_active_project,
    get_project,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _tmp_planner_dir(tmp_path, monkeypatch):
    """Redirect strategic planner persistence to tmp dir."""
    monkeypatch.setattr(
        "src.core.strategic_planner._base_path",
        lambda: tmp_path,
    )
    (tmp_path / "data" / "self_extension").mkdir(parents=True, exist_ok=True)
    (tmp_path / "claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / "claude" / "ARCHITECTURE.md").write_text("# Archi\n")
    yield


@pytest.fixture
def goal_manager(tmp_path):
    """GoalManager backed by tmp dir."""
    data_dir = tmp_path / "goals_data"
    data_dir.mkdir(exist_ok=True)
    return GoalManager(data_dir=data_dir)


@pytest.fixture
def mock_router():
    """Router mock for StrategicPlanner."""
    router = MagicMock()
    router.escalate_for_task.return_value.__enter__ = MagicMock(return_value={})
    router.escalate_for_task.return_value.__exit__ = MagicMock(return_value=False)
    return router


def _two_phase_plan(project_id="test_proj"):
    """Create a 2-phase plan with 2 tasks each."""
    return ImplementationPlan(
        project_id=project_id,
        title="Test Project",
        description="A test self-extension project",
        gap_name="test_gap",
        status="planned",
        phases=[
            PlanPhase(
                phase_number=1, title="Core", description="Build core",
                status="pending",
                tasks=[
                    PhaseTask(description="Create core module",
                              task_type="code", files_involved=["src/tools/test.py"]),
                    PhaseTask(description="Add unit tests",
                              task_type="test", files_involved=["tests/unit/test_test.py"]),
                ],
            ),
            PlanPhase(
                phase_number=2, title="Integration", description="Wire in",
                status="pending",
                tasks=[
                    PhaseTask(description="Add action handler",
                              task_type="integration", files_involved=["src/interfaces/action_dispatcher.py"]),
                ],
            ),
        ],
        current_phase=1,
        new_files=["src/tools/test.py"],
        modified_files=["src/interfaces/action_dispatcher.py"],
        created_at="2026-03-07T00:00:00",
        updated_at="2026-03-07T00:00:00",
    )


# ── End-to-End: Full Cycle ──────────────────────────────────────────


class TestFullCycleE2E:
    """Test the complete self-extension cycle using real GoalManager + StrategicPlanner."""

    def test_full_cycle_plan_activate_complete(self, goal_manager, mock_router):
        """Full cycle: plan → activate → create goals → complete goals →
        auto-mark phase tasks → advance → create phase 2 goals →
        complete → project complete."""
        plan = _two_phase_plan()
        _save_plan(plan)

        # Step 1: Activate the plan
        sp = StrategicPlanner(mock_router)
        result = sp.activate_plan("test_proj")
        assert result.action == "started"
        assert result.phase_number == 1
        assert len(result.goal_descriptions) == 2

        # Step 2: Create project-linked goals for Phase 1
        phase1_goals = []
        for desc in result.goal_descriptions:
            goal = goal_manager.create_goal(
                description=desc,
                user_intent="self-extension project",
                project_id="test_proj",
                project_phase=1,
            )
            assert goal is not None
            phase1_goals.append(goal)

        assert len(phase1_goals) == 2

        # Verify goals are linked to project
        linked = goal_manager.get_project_phase_goals("test_proj", 1)
        assert len(linked) == 2

        # Step 3: Simulate goal completion (add tasks + complete them)
        for goal in phase1_goals:
            task = Task(
                task_id=f"task_{goal_manager.next_task_id}",
                description=f"Execute: {goal.description}",
                goal_id=goal.goal_id,
            )
            goal_manager.next_task_id += 1
            goal.add_task(task)
            goal.is_decomposed = True
            goal_manager.save_state()
            goal_manager.start_task(task.task_id)
            goal_manager.complete_task(task.task_id, {"success": True})

        # Verify both goals are complete
        assert all(g.is_complete() for g in phase1_goals)

        # Step 4: Simulate _check_project_phase_completion
        # (normally called by GoalWorkerPool, here we do it directly)
        phase_goals = goal_manager.get_project_phase_goals("test_proj", 1)
        all_complete = all(g.is_complete() for g in phase_goals)
        assert all_complete

        # Mark phase tasks done
        proj_data = get_project("test_proj")
        plan_obj = ImplementationPlan.from_dict(proj_data)
        for idx, task in enumerate(plan_obj.phases[0].tasks):
            if not task.done:
                sp.mark_phase_task_done("test_proj", 1, idx)

        # Verify phase tasks are marked done
        proj_data = get_project("test_proj")
        assert all(t["done"] for t in proj_data["phases"][0]["tasks"])

        # Step 5: Advance to Phase 2
        advance_result = sp.advance_plan("test_proj")
        assert advance_result.action == "advanced"
        assert advance_result.phase_number == 2
        assert "Add action handler" in advance_result.goal_descriptions

        # Verify project state
        proj_data = get_project("test_proj")
        assert proj_data["current_phase"] == 2
        assert proj_data["phases"][0]["status"] == "completed"
        assert proj_data["phases"][1]["status"] == "in_progress"

        # Step 6: Create and complete Phase 2 goals
        for desc in advance_result.goal_descriptions:
            goal = goal_manager.create_goal(
                description=desc,
                user_intent="self-extension project",
                project_id="test_proj",
                project_phase=2,
            )
            assert goal is not None
            task = Task(
                task_id=f"task_{goal_manager.next_task_id}",
                description=f"Execute: {desc}",
                goal_id=goal.goal_id,
            )
            goal_manager.next_task_id += 1
            goal.add_task(task)
            goal.is_decomposed = True
            goal_manager.save_state()
            goal_manager.start_task(task.task_id)
            goal_manager.complete_task(task.task_id, {"success": True})

        # Mark Phase 2 tasks done
        sp.mark_phase_task_done("test_proj", 2, 0)

        # Step 7: Advance again — project should complete
        final_result = sp.advance_plan("test_proj")
        assert final_result.action == "completed"
        assert "completed" in final_result.message.lower()

        proj_data = get_project("test_proj")
        assert proj_data["status"] == "completed"


# ── Failure Propagation ─────────────────────────────────────────────


class TestFailurePropagation:
    """Test goal failure → phase failure → project pause."""

    def test_fail_phase_pauses_project(self, mock_router):
        """When fail_phase is called, the project pauses."""
        plan = _two_phase_plan()
        plan.status = "active"
        plan.phases[0].status = "in_progress"
        _save_plan(plan)

        sp = StrategicPlanner(mock_router)
        result = sp.fail_phase("test_proj", 1, "Goal task failed: Create core module")
        assert result.action == "failed"
        assert result.phase_number == 1
        assert "paused" in result.message.lower()

        proj = get_project("test_proj")
        assert proj["status"] == "paused"
        assert proj["phases"][0]["status"] == "failed"
        assert proj["pause_reason"] == "Goal task failed: Create core module"

    def test_fail_phase_not_active(self, mock_router):
        """fail_phase on a non-active project does nothing."""
        plan = _two_phase_plan()
        _save_plan(plan)  # status = "planned"

        sp = StrategicPlanner(mock_router)
        result = sp.fail_phase("test_proj", 1, "reason")
        assert result.action == "none"

    def test_fail_phase_missing_project(self, mock_router):
        sp = StrategicPlanner(mock_router)
        result = sp.fail_phase("nonexistent", 1, "reason")
        assert result.action == "none"

    def test_resume_after_failure(self, mock_router):
        """Paused project can be resumed."""
        plan = _two_phase_plan()
        plan.status = "active"
        plan.phases[0].status = "in_progress"
        _save_plan(plan)

        sp = StrategicPlanner(mock_router)
        sp.fail_phase("test_proj", 1, "task failed")

        # Verify paused
        assert get_project("test_proj")["status"] == "paused"

        # Resume
        result = sp.resume_project("test_proj")
        assert result.action == "started"
        assert len(result.goal_descriptions) == 2  # Both tasks undone

        proj = get_project("test_proj")
        assert proj["status"] == "active"
        assert proj["phases"][0]["status"] == "in_progress"

    def test_resume_not_paused(self, mock_router):
        plan = _two_phase_plan()
        _save_plan(plan)

        sp = StrategicPlanner(mock_router)
        result = sp.resume_project("test_proj")
        assert result.action == "none"

    def test_resume_missing_project(self, mock_router):
        sp = StrategicPlanner(mock_router)
        result = sp.resume_project("nope")
        assert result.action == "none"

    def test_worker_pool_failure_propagation(self, goal_manager, mock_router):
        """GoalWorkerPool._check_project_phase_failure propagates to planner."""
        plan = _two_phase_plan()
        plan.status = "active"
        plan.phases[0].status = "in_progress"
        _save_plan(plan)

        # Create a project-linked goal with a failed task
        goal = goal_manager.create_goal(
            description="Create core module",
            user_intent="self-extension project",
            project_id="test_proj",
            project_phase=1,
        )
        task = Task(
            task_id=f"task_{goal_manager.next_task_id}",
            description="Execute: Create core module",
            goal_id=goal.goal_id,
        )
        goal_manager.next_task_id += 1
        goal.add_task(task)
        goal.is_decomposed = True
        goal_manager.save_state()

        # Fail the task
        goal_manager.start_task(task.task_id)
        goal_manager.fail_task(task.task_id, "API call failed")

        # Now simulate what GoalWorkerPool does
        from src.core.goal_worker_pool import GoalWorkerPool
        pool = GoalWorkerPool(
            goal_manager=goal_manager,
            router=mock_router,
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        orch_result = {"tasks_completed": 0, "tasks_failed": 1}
        pool._check_project_phase_failure(goal, orch_result)

        # Verify project is paused
        proj = get_project("test_proj")
        assert proj["status"] == "paused"
        assert proj["phases"][0]["status"] == "failed"

        pool.shutdown(timeout=1)

    def test_worker_pool_no_failure_when_tasks_pending(self, goal_manager, mock_router):
        """Don't fail phase if there are still pending tasks."""
        plan = _two_phase_plan()
        plan.status = "active"
        plan.phases[0].status = "in_progress"
        _save_plan(plan)

        goal = goal_manager.create_goal(
            description="Create core module",
            user_intent="self-extension project",
            project_id="test_proj",
            project_phase=1,
        )
        # Two tasks: one fails, one still pending
        task1 = Task(task_id="t1", description="First", goal_id=goal.goal_id)
        task2 = Task(task_id="t2", description="Second", goal_id=goal.goal_id)
        goal.tasks = [task1, task2]
        goal.is_decomposed = True
        goal_manager.save_state()

        task1.status = TaskStatus.FAILED
        task1.error = "broken"
        # task2 is still PENDING

        from src.core.goal_worker_pool import GoalWorkerPool
        pool = GoalWorkerPool(
            goal_manager=goal_manager,
            router=mock_router,
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        pool._check_project_phase_failure(goal, {"tasks_failed": 1})

        # Project should still be active (task2 is pending)
        proj = get_project("test_proj")
        assert proj["status"] == "active"

        pool.shutdown(timeout=1)


# ── Edge Cases ──────────────────────────────────────────────────────


class TestProjectGoalEdgeCases:
    """Edge cases for project-linked goals."""

    def test_duplicate_project_goal_rejected(self, goal_manager):
        """Creating a project goal with same description as existing is rejected."""
        goal1 = goal_manager.create_goal(
            description="Create core module",
            user_intent="self-extension project",
            project_id="proj1",
            project_phase=1,
        )
        assert goal1 is not None

        # Same description, same project — should be rejected as duplicate
        goal2 = goal_manager.create_goal(
            description="Create core module",
            user_intent="self-extension project",
            project_id="proj1",
            project_phase=1,
        )
        assert goal2 is None

    def test_same_description_different_projects(self, goal_manager):
        """Same description for different project IDs — both kept (dedup is
        description-based, not project-aware, so first creates, second is dup)."""
        goal1 = goal_manager.create_goal(
            description="Create foo module",
            user_intent="self-extension project",
            project_id="proj_a",
            project_phase=1,
        )
        assert goal1 is not None

        # Same description — dedup fires regardless of project_id
        goal2 = goal_manager.create_goal(
            description="Create foo module",
            user_intent="self-extension project",
            project_id="proj_b",
            project_phase=1,
        )
        # This is the current behavior: description-match dedup blocks it
        assert goal2 is None

    def test_regular_goal_same_description_as_project_goal(self, goal_manager):
        """Regular (non-project) goal with same description as project goal is deduped."""
        # Create project goal first
        goal1 = goal_manager.create_goal(
            description="Research music APIs",
            user_intent="self-extension project",
            project_id="music_gen",
            project_phase=1,
        )
        assert goal1 is not None

        # Regular goal with same description — dedup fires
        goal2 = goal_manager.create_goal(
            description="Research music APIs",
            user_intent="User requested",
        )
        assert goal2 is None

    def test_project_goal_after_regular_goal(self, goal_manager):
        """Project goal with same description as existing regular goal is deduped."""
        goal1 = goal_manager.create_goal(
            description="Build a music generator",
            user_intent="User requested",
        )
        assert goal1 is not None

        goal2 = goal_manager.create_goal(
            description="Build a music generator",
            user_intent="self-extension project",
            project_id="music_gen",
            project_phase=1,
        )
        assert goal2 is None

    def test_get_project_phase_goals_filters_correctly(self, goal_manager):
        """get_project_phase_goals returns only goals for specified project+phase."""
        goal_manager.create_goal("Task A", "ext", project_id="p1", project_phase=1)
        goal_manager.create_goal("Task B", "ext", project_id="p1", project_phase=1)
        goal_manager.create_goal("Task C", "ext", project_id="p1", project_phase=2)
        goal_manager.create_goal("Task D", "ext", project_id="p2", project_phase=1)
        goal_manager.create_goal("Regular task", "user")

        p1_phase1 = goal_manager.get_project_phase_goals("p1", 1)
        assert len(p1_phase1) == 2
        assert all(g.project_id == "p1" and g.project_phase == 1 for g in p1_phase1)

        p1_phase2 = goal_manager.get_project_phase_goals("p1", 2)
        assert len(p1_phase2) == 1

        p2_phase1 = goal_manager.get_project_phase_goals("p2", 1)
        assert len(p2_phase1) == 1

        # Non-existent
        assert goal_manager.get_project_phase_goals("p3", 1) == []

    def test_project_goal_serialization_roundtrip(self, goal_manager):
        """Project fields survive save/load cycle."""
        goal = goal_manager.create_goal(
            description="Roundtrip test goal",
            user_intent="self-extension",
            project_id="rt_proj",
            project_phase=3,
        )
        assert goal is not None
        goal_manager.save_state()

        # Reload from disk
        gm2 = GoalManager(data_dir=goal_manager.data_dir)
        reloaded = gm2.goals.get(goal.goal_id)
        assert reloaded is not None
        assert reloaded.project_id == "rt_proj"
        assert reloaded.project_phase == 3

    def test_completed_project_goal_not_duplicate_blocked(self, goal_manager):
        """Once a project goal completes, creating a new one with same desc works."""
        goal = goal_manager.create_goal(
            description="Phase 1 task A",
            user_intent="self-extension",
            project_id="p1",
            project_phase=1,
        )
        # Add a task and complete it
        task = Task(task_id="t1", description="do it", goal_id=goal.goal_id)
        goal.add_task(task)
        goal.is_decomposed = True
        goal_manager.complete_task("t1", {"success": True})

        assert goal.is_complete()

        # Now creating a new goal with same description should work
        # because _find_duplicate skips completed goals
        goal2 = goal_manager.create_goal(
            description="Phase 1 task A",
            user_intent="self-extension",
            project_id="p1",
            project_phase=2,
        )
        assert goal2 is not None


# ── Advance plan with paused status ─────────────────────────────────


class TestAdvancePlanPaused:
    """Ensure advance_plan doesn't proceed on paused projects."""

    def test_advance_paused_project_does_nothing(self, mock_router):
        plan = _two_phase_plan()
        plan.status = "paused"
        plan.phases[0].status = "failed"
        _save_plan(plan)

        sp = StrategicPlanner(mock_router)
        result = sp.advance_plan("test_proj")
        assert result.action == "none"
        assert "not active" in result.message.lower()
