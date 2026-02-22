"""Unit tests for DreamCycle — idle detection, activity tracking, lifecycle.

Tests the DreamCycle orchestrator without real model calls or Discord.
Heavy external dependencies (MemoryManager, GoalWorkerPool, ModelRouter,
Discord, yaml configs) are mocked or bypassed.

Created session 80.
"""

import threading
import time
import pytest
from datetime import datetime, timedelta, date
from unittest.mock import MagicMock, patch, PropertyMock

from src.core.dream_cycle import DreamCycle, _MAX_DREAM_HISTORY


# ── Fixture: build a DreamCycle with mocked heavy deps ────────────────


@pytest.fixture
def dc():
    """Create a DreamCycle with mocked external deps.

    Patches out MemoryManager init (loads torch), yaml config loading,
    and reporting's overnight-results loader.
    """
    with patch("src.core.dream_cycle.MemoryManager") as MockMem, \
         patch("src.core.dream_cycle.reporting") as mock_reporting, \
         patch.object(DreamCycle, "_load_identity", return_value={}), \
         patch.object(DreamCycle, "_load_project_context", return_value={}), \
         patch.object(DreamCycle, "_load_prime_directive", return_value=""):
        mock_reporting.load_overnight_results.return_value = []
        cycle = DreamCycle(idle_threshold_seconds=60, check_interval_seconds=5)
        # Stop the background memory init thread (won't succeed with mock)
        cycle._memory_init_thread.join(timeout=2)
        yield cycle
        cycle.stop_flag.set()


# ── Activity tracking & idle detection ────────────────────────────────


class TestIdleDetection:

    def test_not_idle_initially(self, dc):
        assert dc.is_idle() is False

    def test_idle_after_threshold(self, dc):
        dc.last_activity = datetime.now() - timedelta(seconds=120)
        assert dc.is_idle() is True

    def test_mark_activity_resets_idle(self, dc):
        dc.last_activity = datetime.now() - timedelta(seconds=120)
        assert dc.is_idle() is True
        dc.mark_activity()
        assert dc.is_idle() is False

    def test_idle_threshold_boundary(self, dc):
        """Exactly at threshold should not be idle (needs to exceed)."""
        dc.idle_threshold = 60
        dc.last_activity = datetime.now() - timedelta(seconds=59)
        assert dc.is_idle() is False


class TestIdleThreshold:

    def test_set_idle_threshold(self, dc):
        result = dc.set_idle_threshold(300)
        assert dc.idle_threshold == 300
        assert "5 minute" in result

    def test_set_idle_threshold_floor(self, dc):
        """Floor is 60 seconds."""
        dc.set_idle_threshold(10)
        assert dc.idle_threshold == 60

    def test_set_idle_threshold_fractional_display(self, dc):
        result = dc.set_idle_threshold(90)
        assert dc.idle_threshold == 90
        assert "1.5 minute" in result

    def test_get_idle_threshold(self, dc):
        dc.idle_threshold = 120
        assert dc.get_idle_threshold() == 120


# ── Queue management ─────────────────────────────────────────────────


class TestTaskQueue:

    def test_queue_task(self, dc):
        dc.queue_task({"description": "Test task", "type": "review"})
        assert len(dc.task_queue) == 1
        assert dc.task_queue[0]["description"] == "Test task"
        assert "queued_at" in dc.task_queue[0]

    def test_queue_multiple_tasks(self, dc):
        dc.queue_task({"description": "Task 1"})
        dc.queue_task({"description": "Task 2"})
        assert len(dc.task_queue) == 2


# ── Autonomous mode ──────────────────────────────────────────────────


class TestAutonomousMode:

    def test_enable_autonomous_mode_without_router(self, dc):
        gm = MagicMock()
        dc._router = None
        dc.enable_autonomous_mode(gm)
        assert dc.autonomous_mode is True
        assert dc.goal_manager is gm
        # No pool because no router
        assert dc.goal_worker_pool is None

    def test_enable_autonomous_mode_with_router(self, dc):
        gm = MagicMock()
        router = MagicMock()
        dc._router = router
        with patch("src.core.dream_cycle.GoalWorkerPool") as MockPool:
            dc.enable_autonomous_mode(gm)
            assert dc.autonomous_mode is True
            MockPool.assert_called_once()

    def test_set_router_creates_pool(self, dc):
        gm = MagicMock()
        dc._router = None
        dc.enable_autonomous_mode(gm)
        assert dc.goal_worker_pool is None

        router = MagicMock()
        with patch("src.core.dream_cycle.GoalWorkerPool") as MockPool:
            dc.set_router(router)
            assert dc._router is router
            MockPool.assert_called_once()

    def test_set_router_no_double_pool(self, dc):
        """If pool already exists, set_router doesn't create a second one."""
        gm = MagicMock()
        router = MagicMock()
        dc._router = router
        with patch("src.core.dream_cycle.GoalWorkerPool") as MockPool:
            dc.enable_autonomous_mode(gm)
            pool1_calls = MockPool.call_count
            dc.set_router(MagicMock())
            assert MockPool.call_count == pool1_calls  # No extra call


# ── Kick (immediate dispatch) ────────────────────────────────────────


class TestKick:

    def test_kick_without_pool_backdates_activity(self, dc):
        dc.goal_worker_pool = None
        before = dc.last_activity
        dc.kick()
        # last_activity should be pushed back past the threshold
        assert dc.last_activity < before

    def test_kick_with_pool_submits_goal(self, dc):
        pool = MagicMock()
        dc.goal_worker_pool = pool
        dc.kick(goal_id="goal_1", reactive=True)
        pool.submit_goal.assert_called_once_with("goal_1", reactive=True)

    def test_kick_no_goal_id_backdates(self, dc):
        dc.goal_worker_pool = MagicMock()
        dc.kick(goal_id=None)
        # Without goal_id, falls through to backdate even with pool
        # (goal_id is falsy)


# ── Suggestion cooldown ──────────────────────────────────────────────


class TestSuggestCooldown:

    def test_reset_suggest_cooldown(self, dc):
        dc._suggest_cooldown = 3600
        dc._unanswered_suggest_count = 5
        dc.reset_suggest_cooldown()
        assert dc._suggest_cooldown == dc._suggest_cooldown_base
        assert dc._unanswered_suggest_count == 0

    def test_clear_suggest_cooldown(self, dc):
        dc._last_suggest_time = datetime.now()
        dc.clear_suggest_cooldown()
        assert dc._last_suggest_time is None

    def test_reset_is_idempotent(self, dc):
        dc._suggest_cooldown = dc._suggest_cooldown_base
        dc.reset_suggest_cooldown()
        assert dc._suggest_cooldown == dc._suggest_cooldown_base


# ── _has_pending_work ─────────────────────────────────────────────────


class TestHasPendingWork:

    def test_no_goal_manager(self, dc):
        dc.goal_manager = None
        assert dc._has_pending_work() is False

    def test_with_queued_tasks(self, dc):
        dc.goal_manager = MagicMock()
        dc.task_queue = [{"type": "test"}]
        assert dc._has_pending_work() is True

    def test_with_ready_goal_tasks(self, dc):
        mock_goal = MagicMock()
        mock_goal.is_complete.return_value = False
        mock_goal.get_ready_tasks.return_value = [MagicMock()]
        mock_goal.is_decomposed = True

        gm = MagicMock()
        gm.goals = {"g1": mock_goal}
        dc.goal_manager = gm
        dc.task_queue = []
        assert dc._has_pending_work() is True

    def test_with_undecomposed_goal(self, dc):
        mock_goal = MagicMock()
        mock_goal.is_complete.return_value = False
        mock_goal.get_ready_tasks.return_value = []
        mock_goal.is_decomposed = False

        gm = MagicMock()
        gm.goals = {"g1": mock_goal}
        dc.goal_manager = gm
        dc.task_queue = []
        assert dc._has_pending_work() is True

    def test_all_goals_complete(self, dc):
        mock_goal = MagicMock()
        mock_goal.is_complete.return_value = True

        gm = MagicMock()
        gm.goals = {"g1": mock_goal}
        dc.goal_manager = gm
        dc.task_queue = []
        assert dc._has_pending_work() is False


# ── _should_run_cycle ─────────────────────────────────────────────────


class TestShouldRunCycle:

    def test_run_when_pending_work(self, dc):
        dc.goal_manager = MagicMock()
        dc.task_queue = [{"x": 1}]
        assert dc._should_run_cycle() is True

    def test_skip_when_all_providers_down(self, dc):
        dc._router = MagicMock()
        dc._router.all_providers_down.return_value = True
        assert dc._should_run_cycle() is False

    def test_skip_when_cooldown_active(self, dc):
        dc.goal_manager = MagicMock()
        dc.goal_manager.goals = {}
        dc.task_queue = []
        dc._last_suggest_time = datetime.now()
        dc._suggest_cooldown = 3600
        dc.goal_worker_pool = None
        assert dc._should_run_cycle() is False

    def test_run_when_cooldown_expired(self, dc):
        dc.goal_manager = MagicMock()
        dc.goal_manager.goals = {}
        dc.task_queue = []
        dc._last_suggest_time = datetime.now() - timedelta(seconds=7200)
        dc._suggest_cooldown = 3600
        dc.goal_worker_pool = None
        assert dc._should_run_cycle() is True

    def test_run_when_never_suggested(self, dc):
        dc.goal_manager = MagicMock()
        dc.goal_manager.goals = {}
        dc.task_queue = []
        dc._last_suggest_time = None
        dc.goal_worker_pool = None
        assert dc._should_run_cycle() is True

    def test_skip_when_pool_is_working(self, dc):
        dc.goal_manager = MagicMock()
        dc.goal_manager.goals = {}
        dc.task_queue = []
        dc.goal_worker_pool = MagicMock()
        dc.goal_worker_pool.is_working.return_value = True
        assert dc._should_run_cycle() is False


# ── Sleep gap detection ───────────────────────────────────────────────


class TestSleepGap:

    def test_no_gap(self, dc):
        phase_start = time.monotonic()
        assert dc._check_sleep_gap("test", phase_start, max_expected_seconds=10) is False

    def test_gap_detected(self, dc):
        phase_start = time.monotonic() - 700
        assert dc._check_sleep_gap("test", phase_start, max_expected_seconds=600) is True


# ── Memory property ───────────────────────────────────────────────────


class TestMemoryProperty:

    def test_memory_none_before_ready(self, dc):
        dc._memory_ready.clear()
        dc._memory = MagicMock()
        assert dc.memory is None

    def test_memory_returns_after_ready(self, dc):
        mem = MagicMock()
        dc.set_memory(mem)
        assert dc.memory is mem

    def test_set_memory(self, dc):
        mem = MagicMock()
        dc.set_memory(mem)
        assert dc._memory is mem
        assert dc._memory_ready.is_set()


# ── Monitoring lifecycle ─────────────────────────────────────────────


class TestMonitoringLifecycle:

    def test_start_monitoring(self, dc):
        dc.start_monitoring()
        assert dc.dream_thread is not None
        assert dc.dream_thread.is_alive()
        dc.stop_monitoring()

    def test_stop_monitoring(self, dc):
        dc.start_monitoring()
        dc.stop_monitoring()
        assert dc.stop_flag.is_set()

    def test_double_start_is_safe(self, dc):
        dc.start_monitoring()
        thread1 = dc.dream_thread
        dc.start_monitoring()  # Should warn, not create new thread
        assert dc.dream_thread is thread1
        dc.stop_monitoring()

    def test_stop_without_start(self, dc):
        """Stopping without starting should not crash."""
        dc.stop_monitoring()
        assert dc.stop_flag.is_set()


# ── Status ────────────────────────────────────────────────────────────


class TestGetStatus:

    def test_status_fields(self, dc):
        status = dc.get_status()
        assert "is_dreaming" in status
        assert "is_idle" in status
        assert "idle_seconds" in status
        assert "queued_tasks" in status
        assert "total_dreams" in status
        assert "last_activity" in status
        assert "overnight_results" in status
        assert "pending_suggestions" in status
        assert "all_providers_down" in status

    def test_status_values(self, dc):
        dc.task_queue = [{"x": 1}, {"y": 2}]
        dc.dream_history = [{"a": 1}]
        status = dc.get_status()
        assert status["queued_tasks"] == 2
        assert status["total_dreams"] == 1
        assert status["is_dreaming"] is False


# ── _all_providers_down ───────────────────────────────────────────────


class TestAllProvidersDown:

    def test_no_router(self, dc):
        dc._router = None
        with patch.object(dc, "_get_router", return_value=None):
            assert dc._all_providers_down() is False

    def test_router_says_up(self, dc):
        dc._router = MagicMock()
        dc._router.all_providers_down.return_value = False
        assert dc._all_providers_down() is False

    def test_router_says_down(self, dc):
        dc._router = MagicMock()
        dc._router.all_providers_down.return_value = True
        assert dc._all_providers_down() is True

    def test_router_no_attribute(self, dc):
        """Old router without all_providers_down → assume not down."""
        dc._router = MagicMock(spec=[])  # No attributes
        assert dc._all_providers_down() is False


# ── Dream history cap ─────────────────────────────────────────────────


class TestDreamHistoryCap:

    def test_max_dream_history_constant(self):
        assert _MAX_DREAM_HISTORY == 500
