"""Unit tests for GoalWorkerPool — budget enforcement, cancellation, phase methods.

Tests:
  - Per-goal budget loading from rules.yaml
  - Per-goal cancellation via stop flags
  - Pool shutdown sets all stop flags
  - Submit rejection when pool is stopped
  - Worker state management
  - Phase method decomposition (discovery, decompose, execute, QA pipeline)

Created session 72. Extended session 131 (phase method tests).
"""

import threading
from datetime import datetime

import pytest
from unittest.mock import MagicMock, patch

from src.core.goal_worker_pool import (
    GoalWorkerPool, GoalWorkerState, WorkerStatus,
    _get_per_goal_budget, _get_max_workers,
)


@pytest.fixture
def rules_yaml(tmp_path):
    """Create a rules.yaml with known budget values."""
    rules = tmp_path / "config" / "rules.yaml"
    rules.parent.mkdir(parents=True)
    rules.write_text(
        "worker_pool:\n"
        "  max_workers: 3\n"
        "  per_goal_budget: 0.75\n",
        encoding="utf-8",
    )
    return tmp_path


def _safe_shutdown(pool):
    """Shutdown a pool, ignoring errors (for test cleanup)."""
    try:
        pool._stop.set()
        with pool._goal_flags_lock:
            for flag in pool._goal_stop_flags.values():
                flag.set()
        try:
            from src.core.plan_executor import signal_task_cancellation
            signal_task_cancellation("shutdown")
        except ImportError:
            pass
        pool._executor.shutdown(wait=False, cancel_futures=True)
        pool._reactive_executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


class TestBudgetLoading:
    """Tests for per-goal budget configuration."""

    def test_budget_from_rules_yaml(self, rules_yaml, monkeypatch):
        """Per-goal budget is loaded from rules.yaml."""
        monkeypatch.setattr(
            "src.utils.paths.base_path_as_path", lambda: rules_yaml,
        )
        budget = _get_per_goal_budget()
        assert budget == 0.75

    def test_budget_default_on_missing_yaml(self, tmp_path, monkeypatch):
        """Missing rules.yaml → default budget ($1.00)."""
        monkeypatch.setattr(
            "src.utils.paths.base_path_as_path", lambda: tmp_path,
        )
        budget = _get_per_goal_budget()
        assert budget == 1.00

    def test_max_workers_capped_at_4(self, tmp_path, monkeypatch):
        """max_workers has a hard cap of 4 even if config says higher."""
        rules = tmp_path / "config" / "rules.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "worker_pool:\n"
            "  max_workers: 10\n"
            "  per_goal_budget: 1.00\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.utils.paths.base_path_as_path", lambda: tmp_path,
        )
        workers = _get_max_workers()
        assert workers <= 4

    def test_max_workers_default(self, tmp_path, monkeypatch):
        """Missing config → default 2 workers."""
        monkeypatch.setattr(
            "src.utils.paths.base_path_as_path", lambda: tmp_path,
        )
        workers = _get_max_workers()
        assert workers == 2


class TestCancellation:
    """Tests for per-goal cancellation and pool shutdown."""

    @pytest.fixture
    def pool(self):
        """Create a GoalWorkerPool with mocked dependencies."""
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_submit_rejected_when_stopped(self, pool):
        """Submitting a goal after stop flag is set gets rejected."""
        pool._stop.set()
        assert pool.submit_goal("goal_1") is False

    def test_double_submit_rejected(self, pool):
        """Submitting the same goal twice is rejected (already in _submitted)."""
        # Manually add to submitted set to simulate first submit
        with pool._submitted_lock:
            pool._submitted.add("goal_1")
        assert pool.submit_goal("goal_1") is False

    def test_cancel_goal_sets_flag_when_running(self, pool):
        """cancel_goal() sets the per-goal stop flag when goal is submitted and running."""
        # Simulate a running goal: add to _submitted and create a stop flag
        flag = threading.Event()
        with pool._submitted_lock:
            pool._submitted.add("goal_1")
        with pool._goal_flags_lock:
            pool._goal_stop_flags["goal_1"] = flag
        result = pool.cancel_goal("goal_1")
        assert result is True
        assert flag.is_set()

    def test_cancel_nonexistent_goal_returns_false(self, pool):
        """Cancelling a goal that doesn't exist returns False."""
        result = pool.cancel_goal("nonexistent_goal")
        assert result is False

    def test_shutdown_sets_global_stop(self, pool):
        """shutdown() sets the global stop flag."""
        pool.shutdown()
        assert pool._stop.is_set()

    def test_shutdown_sets_all_goal_flags(self, pool):
        """shutdown() sets all per-goal stop flags."""
        flag1 = threading.Event()
        flag2 = threading.Event()
        with pool._goal_flags_lock:
            pool._goal_stop_flags["g1"] = flag1
            pool._goal_stop_flags["g2"] = flag2
        pool.shutdown()
        assert flag1.is_set()
        assert flag2.is_set()

    def test_is_working_false_initially(self, pool):
        """Pool reports not working when no goals are submitted."""
        assert pool.is_working() is False

    def test_is_working_true_when_submitted(self, pool):
        """Pool reports working when goals are in _submitted."""
        with pool._submitted_lock:
            pool._submitted.add("goal_x")
        assert pool.is_working() is True


class TestWorkerState:
    """Tests for worker state tracking."""

    @pytest.fixture
    def pool(self):
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_get_status_returns_valid_dict(self, pool):
        """get_status() returns valid dict even with no goals."""
        status = pool.get_status()
        assert "max_workers" in status
        assert "submitted_goals" in status
        assert isinstance(status["submitted_goals"], list)
        assert "per_goal_budget" in status
        assert "stopped" in status

    def test_pool_reports_max_workers(self, pool):
        """Status includes max_workers configuration."""
        status = pool.get_status()
        assert status["max_workers"] >= 1

    def test_status_shows_stopped(self, pool):
        """Status reflects stopped state after shutdown."""
        assert pool.get_status()["stopped"] is False
        pool._stop.set()
        assert pool.get_status()["stopped"] is True


# -- Phase method tests (session 131) --

class TestGetContextForDecomposition:
    """Tests for UserModel.get_context_for_decomposition() — consolidated from _format_user_prefs (session 158)."""

    def test_prefs_and_corrections(self):
        """Formats both preferences and corrections."""
        from src.core.user_model import UserModel
        um = UserModel.__new__(UserModel)
        um.preferences = [{"key": "style", "value": "concise"}]
        um.corrections = [{"text": "don't use emojis"}]
        with patch("src.core.user_model.get_user_name", return_value="Jesse"):
            result = um.get_context_for_decomposition()
        assert "Jesse's known preferences" in result
        assert "concise" in result
        assert "don't use emojis" in result

    def test_empty_model_returns_empty(self):
        """Empty user model returns empty string."""
        from src.core.user_model import UserModel
        um = UserModel.__new__(UserModel)
        um.preferences = []
        um.corrections = []
        result = um.get_context_for_decomposition()
        assert result == ""

    def test_prefs_only(self):
        """Only preferences, no corrections."""
        from src.core.user_model import UserModel
        um = UserModel.__new__(UserModel)
        um.preferences = [{"key": "", "value": "dark mode"}]
        um.corrections = []
        with patch("src.core.user_model.get_user_name", return_value="Jesse"):
            result = um.get_context_for_decomposition()
        assert "dark mode" in result
        assert "corrections" not in result.lower()


class TestPhaseDiscover:
    """Tests for _phase_discover method."""

    @pytest.fixture
    def pool(self):
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_discovery_skipped_on_import_error(self, pool):
        """Discovery gracefully skips if modules unavailable."""
        goal = MagicMock()
        goal.description = "test goal"
        mock_um = MagicMock()
        mock_um.get_context_for_decomposition.return_value = ""
        with patch("src.core.user_model.get_user_model", return_value=mock_um):
            brief, prefs, cost = pool._phase_discover("g1", goal)
        # Should not crash; returns defaults
        assert cost >= 0.0

    def test_discovery_returns_brief_and_cost(self, pool):
        """Successful discovery returns brief and cost."""
        goal = MagicMock()
        goal.description = "test goal"
        disc_result = {
            "brief": "project uses Python",
            "cost": 0.05,
            "files_found": 10,
            "files_read": 3,
        }
        mock_um = MagicMock()
        mock_um.get_context_for_decomposition.return_value = ""
        with patch("src.core.user_model.get_user_model", return_value=mock_um):
            with patch("src.core.discovery.discover_project", return_value=disc_result):
                with patch("src.utils.project_context.load", return_value={}):
                    brief, prefs, cost = pool._phase_discover("g1", goal)
        assert brief == "project uses Python"
        assert cost == 0.05


class TestPhaseDecompose:
    """Tests for _phase_decompose method."""

    @pytest.fixture
    def pool(self):
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_success_returns_true(self, pool):
        """Successful decomposition returns True."""
        goal = MagicMock()
        goal.description = "build a feature"
        state = GoalWorkerState(goal_id="g1")
        result = pool._phase_decompose("g1", goal, state, None, None)
        assert result is True
        assert state.status == WorkerStatus.DECOMPOSING
        pool._goal_manager.decompose_goal.assert_called_once()

    def test_failure_returns_false(self, pool):
        """Failed decomposition returns False and sets error."""
        pool._goal_manager.decompose_goal.side_effect = RuntimeError("model error")
        goal = MagicMock()
        goal.description = "build a feature"
        state = GoalWorkerState(goal_id="g1")
        with patch("src.interfaces.discord_bot.send_notification"):
            with patch("src.core.notification_formatter.format_decomposition_failure", return_value={"message": "oops"}):
                result = pool._phase_decompose("g1", goal, state, None, None)
        assert result is False
        assert "Decomposition failed" in state.error

    def test_failure_notification_skipped_gracefully(self, pool):
        """Decomposition failure with notification error doesn't crash."""
        pool._goal_manager.decompose_goal.side_effect = RuntimeError("fail")
        goal = MagicMock()
        goal.description = "test"
        state = GoalWorkerState(goal_id="g1")
        # Both notification imports will fail — should still return False
        result = pool._phase_decompose("g1", goal, state, None, None)
        assert result is False


class TestPhaseExecute:
    """Tests for _phase_execute method."""

    @pytest.fixture
    def pool(self):
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_returns_orchestrator_result(self, pool):
        """Phase execute returns orchestrator result and updates state."""
        orch_result = {"total_cost": 0.10, "tasks_completed": 2, "tasks_failed": 0}
        goal = MagicMock()
        goal.tasks = []  # No in-progress tasks to resume
        pool._goal_manager.goals = {"g1": goal}
        state = GoalWorkerState(goal_id="g1")
        goal_stop = threading.Event()

        with patch("src.core.goal_worker_pool.TaskOrchestrator") as mock_orch_cls:
            mock_orch_cls.return_value.execute_goal_tasks.return_value = orch_result
            result = pool._phase_execute("g1", state, goal_stop)

        assert result["tasks_completed"] == 2
        assert state.cost_spent == 0.10
        assert state.tasks_completed == 2

    def test_resumes_in_progress_tasks(self, pool):
        """Resumes tasks with IN_PROGRESS status before orchestrator."""
        from src.core.goal_manager import TaskStatus
        task = MagicMock()
        task.status = TaskStatus.IN_PROGRESS
        task.task_id = "t1"
        goal = MagicMock()
        goal.tasks = [task]
        pool._goal_manager.goals = {"g1": goal}
        state = GoalWorkerState(goal_id="g1")
        goal_stop = threading.Event()

        with patch("src.core.goal_worker_pool.execute_task", return_value={"cost_usd": 0.02}):
            with patch("src.core.goal_worker_pool.TaskOrchestrator") as mock_orch_cls:
                mock_orch_cls.return_value.execute_goal_tasks.return_value = {
                    "total_cost": 0, "tasks_completed": 0, "tasks_failed": 0,
                }
                pool._phase_execute("g1", state, goal_stop)

        assert state.tasks_completed == 1
        assert state.cost_spent == 0.02
        pool._goal_manager.complete_task.assert_called_once_with("t1", {"cost_usd": 0.02})


class TestBuildQaContext:
    """Tests for _build_qa_context helper."""

    @pytest.fixture
    def pool(self):
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[
                {"goal": "test goal", "task": "task A", "success": True,
                 "summary": "Done: did A", "files_created": ["a.py"]},
                {"goal": "test goal", "task": "task B", "success": False,
                 "summary": "Failed", "files_created": []},
                {"goal": "other goal", "task": "task C", "success": True,
                 "summary": "Done: C", "files_created": ["c.py"]},
            ],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_filters_by_goal_description(self, pool):
        """Only results matching goal description are included."""
        task_a = MagicMock()
        task_a.description = "task A"
        task_a.result = {}
        task_a.files_to_create = []
        task_a.expected_output = ""
        task_a.interfaces = []
        goal = MagicMock()
        goal.description = "test goal"
        goal.tasks = [task_a]

        goal_results, all_files, task_dicts = pool._build_qa_context(goal)
        assert len(goal_results) == 2  # task A and task B
        assert "a.py" in all_files
        assert "c.py" not in all_files

    def test_merges_overnight_data_into_task_dict(self, pool):
        """Task dict result is enriched with overnight_results data."""
        task_a = MagicMock()
        task_a.description = "task A"
        task_a.result = {"original": True}
        task_a.files_to_create = []
        task_a.expected_output = ""
        task_a.interfaces = []
        goal = MagicMock()
        goal.description = "test goal"
        goal.tasks = [task_a]

        _, _, task_dicts = pool._build_qa_context(goal)
        assert task_dicts[0]["result"]["success"] is True
        assert task_dicts[0]["result"]["summary"] == "Done: did A"


class TestRecordQaRejection:
    """Tests for _record_qa_rejection helper."""

    @pytest.fixture
    def pool(self):
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_skips_non_suggestion_goals(self, pool):
        """No-op for goals not from picked suggestions."""
        goal = MagicMock()
        goal.user_intent = "user requested"
        pool._record_qa_rejection("g1", goal, ["issue 1"])
        # Should not crash, no idea_history interaction

    def test_records_for_picked_suggestion(self, pool):
        """Records in idea history for picked suggestion goals."""
        goal = MagicMock()
        goal.user_intent = "Picked suggestion"
        goal.description = "test goal"
        with patch("src.core.idea_history.get_idea_history") as mock_hist:
            pool._record_qa_rejection("g1", goal, ["issue 1", "issue 2"])
            mock_hist.return_value.record_auto_filtered.assert_called_once()
            call_args = mock_hist.return_value.record_auto_filtered.call_args
            assert "QA rejected" in call_args[0][1]


class TestHandleCriticResult:
    """Tests for _handle_critic_result method."""

    @pytest.fixture
    def pool(self):
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_no_concerns_logs_only(self, pool):
        """'none' severity just logs, no remediation."""
        state = GoalWorkerState(goal_id="g1")
        orch_result = {"tasks_completed": 1, "tasks_failed": 0}
        critic_result = {"severity": "none", "concerns": [], "remediation_tasks": []}
        goal_stop = threading.Event()
        pool._handle_critic_result("g1", MagicMock(), state, orch_result, critic_result, goal_stop)
        pool._goal_manager.add_follow_up_tasks.assert_not_called()

    def test_significant_triggers_remediation(self, pool):
        """Significant concerns add follow-up tasks and re-run orchestrator."""
        state = GoalWorkerState(goal_id="g1")
        orch_result = {"tasks_completed": 2, "tasks_failed": 0}
        critic_result = {
            "severity": "significant",
            "concerns": [],
            "remediation_tasks": ["fix thing"],
        }
        goal_stop = threading.Event()
        rem_result = {"total_cost": 0.05, "tasks_completed": 1, "tasks_failed": 0}

        with patch("src.core.goal_worker_pool.TaskOrchestrator") as mock_orch_cls:
            mock_orch_cls.return_value.execute_goal_tasks.return_value = rem_result
            pool._handle_critic_result(
                "g1", MagicMock(), state, orch_result, critic_result, goal_stop,
            )

        pool._goal_manager.add_follow_up_tasks.assert_called_once()
        assert state.tasks_completed == 1
        assert orch_result["tasks_completed"] == 3  # 2 original + 1 remediation

    def test_minor_concerns_no_remediation(self, pool):
        """Minor concerns are logged but don't trigger remediation."""
        state = GoalWorkerState(goal_id="g1")
        orch_result = {"tasks_completed": 1, "tasks_failed": 0}
        critic_result = {
            "severity": "minor",
            "concerns": [{"type": "quality_concern", "detail": "could be better"}],
            "remediation_tasks": [],
        }
        goal_stop = threading.Event()
        with patch("src.core.critic.format_concerns", return_value=["could be better"]):
            pool._handle_critic_result(
                "g1", MagicMock(), state, orch_result, critic_result, goal_stop,
            )
        pool._goal_manager.add_follow_up_tasks.assert_not_called()


class TestGatherGoalSummaries:
    """Tests for _gather_goal_summaries method."""

    @pytest.fixture
    def pool(self):
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_extracts_done_summaries(self, pool):
        """Extracts text after 'Done: ' from task summaries."""
        goal = MagicMock(description="Research vitamins")
        pool._overnight_results = [
            {"goal": "Research vitamins", "summary": "Done: Wrote protocol document with 5 sections", "files_created": ["a.md"]},
            {"goal": "Research vitamins", "summary": "Done: Short", "files_created": []},
        ]
        summaries, files = pool._gather_goal_summaries(goal)
        assert len(summaries) == 1  # "Short" is ≤20 chars, excluded
        assert "protocol document" in summaries[0]
        assert files == ["a.md"]

    def test_collects_all_files(self, pool):
        """Files from all matching results are collected."""
        goal = MagicMock(description="Build app")
        pool._overnight_results = [
            {"goal": "Build app", "summary": "", "files_created": ["x.py", "y.py"]},
            {"goal": "Build app", "summary": "", "files_created": ["z.py"]},
            {"goal": "Other goal", "summary": "", "files_created": ["nope.py"]},
        ]
        _, files = pool._gather_goal_summaries(goal)
        assert files == ["x.py", "y.py", "z.py"]

    def test_integrator_summary_replaces_task_summaries(self, pool):
        """Integrator summary overrides individual Done: summaries."""
        goal = MagicMock(description="Research vitamins")
        pool._overnight_results = [
            {"goal": "Research vitamins", "summary": "Done: Wrote a really long protocol document about vitamins", "files_created": []},
        ]
        summaries, _ = pool._gather_goal_summaries(goal, "Comprehensive vitamin analysis with 3 sections covering dosage, timing, and interactions")
        assert len(summaries) == 1
        assert "Comprehensive" in summaries[0]

    def test_short_integrator_summary_ignored(self, pool):
        """Integrator summary ≤20 chars is ignored."""
        goal = MagicMock(description="Research")
        pool._overnight_results = [
            {"goal": "Research", "summary": "Done: Wrote a full detailed research paper on the topic", "files_created": []},
        ]
        summaries, _ = pool._gather_goal_summaries(goal, "Too short")
        assert len(summaries) == 1
        assert "research paper" in summaries[0]

    def test_empty_overnight_results(self, pool):
        """No matching results → empty summaries and files."""
        goal = MagicMock(description="Unknown goal")
        pool._overnight_results = []
        summaries, files = pool._gather_goal_summaries(goal)
        assert summaries == []
        assert files == []


class TestIsGoalSignificant:
    """Tests for _is_goal_significant method."""

    @pytest.fixture
    def pool(self):
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_significant_by_task_count(self, pool):
        """≥3 total tasks → significant."""
        goal = MagicMock(goal_id="g1")
        assert pool._is_goal_significant(goal, {"tasks_completed": 3, "tasks_failed": 0})

    def test_significant_by_elapsed_time(self, pool):
        """≥600s elapsed → significant."""
        from datetime import timedelta
        goal = MagicMock(goal_id="g1")
        state = GoalWorkerState(goal_id="g1")
        state.started_at = datetime.now() - timedelta(seconds=700)
        pool._worker_states["g1"] = state
        assert pool._is_goal_significant(goal, {"tasks_completed": 1, "tasks_failed": 0})

    def test_not_significant_small_quick(self, pool):
        """<3 tasks and <600s → not significant."""
        from datetime import timedelta
        goal = MagicMock(goal_id="g1")
        state = GoalWorkerState(goal_id="g1")
        state.started_at = datetime.now() - timedelta(seconds=60)
        pool._worker_states["g1"] = state
        assert not pool._is_goal_significant(goal, {"tasks_completed": 1, "tasks_failed": 0})

    def test_significant_with_failures(self, pool):
        """Failed tasks count toward the ≥3 threshold."""
        goal = MagicMock(goal_id="g1")
        assert pool._is_goal_significant(goal, {"tasks_completed": 1, "tasks_failed": 2})

    def test_no_worker_state_not_significant(self, pool):
        """No worker state tracked → only task count matters."""
        goal = MagicMock(goal_id="g_missing")
        assert not pool._is_goal_significant(goal, {"tasks_completed": 1, "tasks_failed": 0})


class TestExecuteGoalNotification:
    """Tests for _execute_goal notification behavior (session 176 fix).

    Verifies that _execute_goal() skips notifications when no tasks were
    executed (work_done=0), and still notifies when tasks were executed.
    """

    @pytest.fixture
    def pool(self):
        pool = GoalWorkerPool(
            goal_manager=MagicMock(),
            router=MagicMock(),
            learning_system=MagicMock(),
            overnight_results=[],
            save_overnight_results=MagicMock(),
        )
        yield pool
        _safe_shutdown(pool)

    def test_skips_notification_when_no_tasks_executed(self, pool):
        """_execute_goal() does not call _notify_goal_result when work_done=0."""
        goal = MagicMock(goal_id="g1", description="test goal")
        goal.is_decomposed = True
        goal.is_complete.return_value = True
        pool._goal_manager.goals = {"g1": goal}

        # Phase execute returns 0 tasks completed and 0 failed
        with patch.object(pool, "_phase_execute", return_value={
            "tasks_completed": 0, "tasks_failed": 0,
        }), patch.object(pool, "_phase_qa_pipeline", return_value=""), \
                patch.object(pool, "_notify_goal_result") as mock_notify, \
                patch.object(pool, "_cleanup_stale_states"):
            pool._execute_goal("g1")
            mock_notify.assert_not_called()

    def test_notifies_when_tasks_completed(self, pool):
        """_execute_goal() calls _notify_goal_result when tasks were executed."""
        goal = MagicMock(goal_id="g2", description="test goal")
        goal.is_decomposed = True
        goal.is_complete.return_value = True
        pool._goal_manager.goals = {"g2": goal}

        with patch.object(pool, "_phase_execute", return_value={
            "tasks_completed": 2, "tasks_failed": 0,
        }), patch.object(pool, "_phase_qa_pipeline", return_value="summary"), \
                patch.object(pool, "_notify_goal_result") as mock_notify, \
                patch.object(pool, "_cleanup_stale_states"):
            pool._execute_goal("g2")
            mock_notify.assert_called_once()

    def test_notifies_when_tasks_failed(self, pool):
        """_execute_goal() calls _notify_goal_result even when all tasks failed
        (work_done > 0 because tasks_failed > 0)."""
        goal = MagicMock(goal_id="g3", description="test goal")
        goal.is_decomposed = True
        goal.is_complete.return_value = True
        pool._goal_manager.goals = {"g3": goal}

        with patch.object(pool, "_phase_execute", return_value={
            "tasks_completed": 0, "tasks_failed": 1,
        }), patch.object(pool, "_phase_qa_pipeline", return_value=""), \
                patch.object(pool, "_notify_goal_result") as mock_notify, \
                patch.object(pool, "_cleanup_stale_states"):
            pool._execute_goal("g3")
            mock_notify.assert_called_once()

    def test_last_goal_notification_time_initialized(self, pool):
        """Session 194: pool has last_goal_notification_time initialized to 0.0."""
        assert hasattr(pool, "last_goal_notification_time")
        assert pool.last_goal_notification_time == 0.0
        assert isinstance(pool.last_goal_notification_time, float)
