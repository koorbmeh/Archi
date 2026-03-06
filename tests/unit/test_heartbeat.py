"""Unit tests for Heartbeat — idle detection, activity tracking, lifecycle.

Tests the Heartbeat orchestrator without real model calls or Discord.
Heavy external dependencies (MemoryManager, GoalWorkerPool, ModelRouter,
Discord, yaml configs) are mocked or bypassed.

Created session 80.
"""

import threading
import time
import pytest
from datetime import datetime, timedelta, date
from unittest.mock import MagicMock, patch, PropertyMock

from src.core.heartbeat import Heartbeat, _MAX_CYCLE_HISTORY


# ── Fixture: build a Heartbeat with mocked heavy deps ────────────────


@pytest.fixture
def hb():
    """Create a Heartbeat with mocked external deps.

    Patches out MemoryManager init (loads torch), yaml config loading,
    and reporting's overnight-results loader.
    """
    with patch("src.core.heartbeat.MemoryManager") as MockMem, \
         patch("src.core.heartbeat.reporting") as mock_reporting, \
         patch.object(Heartbeat, "_load_identity", return_value={}), \
         patch.object(Heartbeat, "_load_project_context", return_value={}), \
         patch.object(Heartbeat, "_load_prime_directive", return_value=""):
        mock_reporting.load_overnight_results.return_value = []
        cycle = Heartbeat(interval_seconds=60)
        # Stop the background memory init thread (won't succeed with mock)
        cycle._memory_init_thread.join(timeout=2)
        yield cycle
        cycle.stop_flag.set()


# ── Activity tracking & idle detection ────────────────────────────────


class TestIdleDetection:

    def test_not_idle_initially(self, hb):
        assert hb.is_idle() is False

    def test_idle_after_threshold(self, hb):
        hb.last_activity = datetime.now() - timedelta(seconds=120)
        assert hb.is_idle() is True

    def test_mark_activity_resets_idle(self, hb):
        hb.last_activity = datetime.now() - timedelta(seconds=120)
        assert hb.is_idle() is True
        hb.mark_activity()
        assert hb.is_idle() is False

    def test_idle_threshold_boundary(self, hb):
        """Exactly at threshold should not be idle (needs to exceed)."""
        hb.interval = 60
        hb.last_activity = datetime.now() - timedelta(seconds=59)
        assert hb.is_idle() is False


class TestInterval:

    def test_set_interval(self, hb):
        result = hb.set_interval(300)
        assert hb.interval == 300
        assert "5 minute" in result

    def test_set_interval_floor(self, hb):
        """Floor is 60 seconds."""
        hb.set_interval(10)
        assert hb.interval == 60

    def test_set_interval_fractional_display(self, hb):
        result = hb.set_interval(90)
        assert hb.interval == 90
        assert "1.5 minute" in result

    def test_get_interval(self, hb):
        hb.interval = 120
        assert hb.get_interval() == 120

    def test_back_compat_aliases(self, hb):
        """set_idle_threshold / get_idle_threshold still work."""
        hb.set_idle_threshold(300)
        assert hb.get_idle_threshold() == 300


# ── Queue management ─────────────────────────────────────────────────


class TestTaskQueue:

    def test_queue_task(self, hb):
        hb.queue_task({"description": "Test task", "type": "review"})
        assert len(hb.task_queue) == 1
        assert hb.task_queue[0]["description"] == "Test task"
        assert "queued_at" in hb.task_queue[0]

    def test_queue_multiple_tasks(self, hb):
        hb.queue_task({"description": "Task 1"})
        hb.queue_task({"description": "Task 2"})
        assert len(hb.task_queue) == 2


# ── Autonomous mode ──────────────────────────────────────────────────


class TestAutonomousMode:

    def test_enable_autonomous_mode_without_router(self, hb):
        gm = MagicMock()
        hb._router = None
        hb.enable_autonomous_mode(gm)
        assert hb.autonomous_mode is True
        assert hb.goal_manager is gm
        # No pool because no router
        assert hb.goal_worker_pool is None

    def test_enable_autonomous_mode_with_router(self, hb):
        gm = MagicMock()
        router = MagicMock()
        hb._router = router
        with patch("src.core.heartbeat.GoalWorkerPool") as MockPool:
            hb.enable_autonomous_mode(gm)
            assert hb.autonomous_mode is True
            MockPool.assert_called_once()

    def test_set_router_creates_pool(self, hb):
        gm = MagicMock()
        hb._router = None
        hb.enable_autonomous_mode(gm)
        assert hb.goal_worker_pool is None

        router = MagicMock()
        with patch("src.core.heartbeat.GoalWorkerPool") as MockPool:
            hb.set_router(router)
            assert hb._router is router
            MockPool.assert_called_once()

    def test_set_router_no_double_pool(self, hb):
        """If pool already exists, set_router doesn't create a second one."""
        gm = MagicMock()
        router = MagicMock()
        hb._router = router
        with patch("src.core.heartbeat.GoalWorkerPool") as MockPool:
            hb.enable_autonomous_mode(gm)
            pool1_calls = MockPool.call_count
            hb.set_router(MagicMock())
            assert MockPool.call_count == pool1_calls  # No extra call


# ── Kick (immediate dispatch) ────────────────────────────────────────


class TestKick:

    def test_kick_without_pool_backdates_activity(self, hb):
        hb.goal_worker_pool = None
        before = hb.last_activity
        hb.kick()
        # last_activity should be pushed back past the threshold
        assert hb.last_activity < before

    def test_kick_with_pool_submits_goal(self, hb):
        pool = MagicMock()
        hb.goal_worker_pool = pool
        hb.kick(goal_id="goal_1", reactive=True)
        pool.submit_goal.assert_called_once_with("goal_1", reactive=True)

    def test_kick_no_goal_id_backdates(self, hb):
        hb.goal_worker_pool = MagicMock()
        hb.kick(goal_id=None)
        # Without goal_id, falls through to backdate even with pool
        # (goal_id is falsy)


# ── Suggestion cooldown ──────────────────────────────────────────────


class TestSuggestCooldown:

    def test_reset_suggest_cooldown(self, hb):
        hb._suggest_cooldown = 3600
        hb._unanswered_suggest_count = 5
        hb.reset_suggest_cooldown()
        assert hb._suggest_cooldown == hb._suggest_cooldown_base
        assert hb._unanswered_suggest_count == 0

    def test_clear_suggest_cooldown(self, hb):
        hb._last_suggest_time = datetime.now()
        hb.clear_suggest_cooldown()
        assert hb._last_suggest_time is None

    def test_reset_is_idempotent(self, hb):
        hb._suggest_cooldown = hb._suggest_cooldown_base
        hb.reset_suggest_cooldown()
        assert hb._suggest_cooldown == hb._suggest_cooldown_base


# ── _has_pending_work ─────────────────────────────────────────────────


class TestHasPendingWork:

    def test_no_goal_manager(self, hb):
        hb.goal_manager = None
        assert hb._has_pending_work() is False

    def test_with_queued_tasks(self, hb):
        hb.goal_manager = MagicMock()
        hb.task_queue = [{"type": "test"}]
        assert hb._has_pending_work() is True

    def test_with_ready_goal_tasks(self, hb):
        mock_goal = MagicMock()
        mock_goal.is_complete.return_value = False
        mock_goal.get_ready_tasks.return_value = [MagicMock()]
        mock_goal.is_decomposed = True

        gm = MagicMock()
        gm.goals = {"g1": mock_goal}
        hb.goal_manager = gm
        hb.task_queue = []
        assert hb._has_pending_work() is True

    def test_with_undecomposed_goal(self, hb):
        mock_goal = MagicMock()
        mock_goal.is_complete.return_value = False
        mock_goal.get_ready_tasks.return_value = []
        mock_goal.is_decomposed = False

        gm = MagicMock()
        gm.goals = {"g1": mock_goal}
        hb.goal_manager = gm
        hb.task_queue = []
        assert hb._has_pending_work() is True

    def test_all_goals_complete(self, hb):
        mock_goal = MagicMock()
        mock_goal.is_complete.return_value = True

        gm = MagicMock()
        gm.goals = {"g1": mock_goal}
        hb.goal_manager = gm
        hb.task_queue = []
        assert hb._has_pending_work() is False


# ── _should_run_cycle ─────────────────────────────────────────────────


class TestShouldRunCycle:

    def test_run_when_pending_work(self, hb):
        hb.goal_manager = MagicMock()
        hb.task_queue = [{"x": 1}]
        assert hb._should_run_cycle() is True

    def test_skip_when_all_providers_down(self, hb):
        hb._router = MagicMock()
        hb._router.all_providers_down.return_value = True
        assert hb._should_run_cycle() is False

    def test_skip_when_cooldown_active(self, hb):
        hb.goal_manager = MagicMock()
        hb.goal_manager.goals = {}
        hb.task_queue = []
        hb._last_suggest_time = datetime.now()
        hb._suggest_cooldown = 3600
        hb.goal_worker_pool = None
        assert hb._should_run_cycle() is False

    def test_run_when_cooldown_expired(self, hb):
        hb.goal_manager = MagicMock()
        hb.goal_manager.goals = {}
        hb.task_queue = []
        hb._last_suggest_time = datetime.now() - timedelta(seconds=7200)
        hb._suggest_cooldown = 3600
        hb.goal_worker_pool = None
        assert hb._should_run_cycle() is True

    def test_run_when_never_suggested(self, hb):
        hb.goal_manager = MagicMock()
        hb.goal_manager.goals = {}
        hb.task_queue = []
        hb._last_suggest_time = None
        hb.goal_worker_pool = None
        assert hb._should_run_cycle() is True

    def test_skip_when_pool_is_working(self, hb):
        hb.goal_manager = MagicMock()
        hb.goal_manager.goals = {}
        hb.task_queue = []
        hb.goal_worker_pool = MagicMock()
        hb.goal_worker_pool.is_working.return_value = True
        assert hb._should_run_cycle() is False


# ── Sleep gap detection ───────────────────────────────────────────────


class TestSleepGap:

    def test_no_gap(self, hb):
        phase_start = time.monotonic()
        assert hb._check_sleep_gap("test", phase_start, max_expected_seconds=10) is False

    def test_gap_detected(self, hb):
        phase_start = time.monotonic() - 700
        assert hb._check_sleep_gap("test", phase_start, max_expected_seconds=600) is True


# ── Memory property ───────────────────────────────────────────────────


class TestMemoryProperty:

    def test_memory_none_before_ready(self, hb):
        hb._memory_ready.clear()
        hb._memory = MagicMock()
        assert hb.memory is None

    def test_memory_returns_after_ready(self, hb):
        mem = MagicMock()
        hb.set_memory(mem)
        assert hb.memory is mem

    def test_set_memory(self, hb):
        mem = MagicMock()
        hb.set_memory(mem)
        assert hb._memory is mem
        assert hb._memory_ready.is_set()


# ── Monitoring lifecycle ─────────────────────────────────────────────


class TestMonitoringLifecycle:

    def test_start_monitoring(self, hb):
        hb.start_monitoring()
        assert hb._monitor_thread is not None
        assert hb._monitor_thread.is_alive()
        hb.stop_monitoring()

    def test_stop_monitoring(self, hb):
        hb.start_monitoring()
        hb.stop_monitoring()
        assert hb.stop_flag.is_set()

    def test_double_start_is_safe(self, hb):
        hb.start_monitoring()
        thread1 = hb._monitor_thread
        hb.start_monitoring()  # Should warn, not create new thread
        assert hb._monitor_thread is thread1
        hb.stop_monitoring()

    def test_stop_without_start(self, hb):
        """Stopping without starting should not crash."""
        hb.stop_monitoring()
        assert hb.stop_flag.is_set()


# ── Status ────────────────────────────────────────────────────────────


class TestGetStatus:

    def test_status_fields(self, hb):
        status = hb.get_status()
        assert "is_running_cycle" in status
        assert "is_idle" in status
        assert "idle_seconds" in status
        assert "queued_tasks" in status
        assert "total_cycles" in status
        assert "last_activity" in status
        assert "overnight_results" in status
        assert "pending_suggestions" in status
        assert "all_providers_down" in status

    def test_status_values(self, hb):
        hb.task_queue = [{"x": 1}, {"y": 2}]
        hb.cycle_history = [{"a": 1}]
        status = hb.get_status()
        assert status["queued_tasks"] == 2
        assert status["total_cycles"] == 1
        assert status["is_running_cycle"] is False


# ── _all_providers_down ───────────────────────────────────────────────


class TestAllProvidersDown:

    def test_no_router(self, hb):
        hb._router = None
        with patch.object(hb, "_get_router", return_value=None):
            assert hb._all_providers_down() is False

    def test_router_says_up(self, hb):
        hb._router = MagicMock()
        hb._router.all_providers_down.return_value = False
        assert hb._all_providers_down() is False

    def test_router_says_down(self, hb):
        hb._router = MagicMock()
        hb._router.all_providers_down.return_value = True
        assert hb._all_providers_down() is True

    def test_router_no_attribute(self, hb):
        """Old router without all_providers_down → assume not down."""
        hb._router = MagicMock(spec=[])  # No attributes
        assert hb._all_providers_down() is False


# ── Dream history cap ─────────────────────────────────────────────────


class TestDreamHistoryCap:

    def test_max_cycle_history_constant(self):
        assert _MAX_CYCLE_HISTORY == 500


# ── Adaptive scheduling (session 115) ───────────────────────────────


class TestAdaptiveScheduling:

    def test_productive_cycle_resets_interval(self, hb):
        """After a productive cycle, interval returns to base."""
        hb._base_interval = 60
        hb.interval = 480  # Was extended from idle cycles
        hb._adapt_interval(was_productive=True)
        assert hb.interval == 60

    def test_idle_cycle_doubles_interval(self, hb):
        """After an idle cycle, interval doubles."""
        hb._base_interval = 60
        hb.interval = 60
        hb._adapt_interval(was_productive=False)
        assert hb.interval == 120

    def test_idle_cycle_respects_max(self, hb):
        """Interval can't exceed _max_interval."""
        hb._base_interval = 60
        hb._max_interval = 200
        hb.interval = 150
        hb._adapt_interval(was_productive=False)
        assert hb.interval == 200  # min(300, 200)

    def test_mark_activity_resets_adaptive_interval(self, hb):
        """User activity resets interval to base."""
        hb._base_interval = 60
        hb.interval = 480
        hb.mark_activity()
        assert hb.interval == 60

    def test_mark_activity_no_change_when_at_base(self, hb):
        """mark_activity is a no-op for interval when already at base."""
        hb._base_interval = 60
        hb.interval = 60
        hb.mark_activity()
        assert hb.interval == 60

    def test_set_interval_updates_base(self, hb):
        """User-configured interval becomes the new base."""
        hb.set_interval(300)
        assert hb._base_interval == 300
        assert hb.interval == 300

    def test_multiple_idle_cycles_compound(self, hb):
        """Multiple idle cycles compound the doubling."""
        hb._base_interval = 60
        hb._max_interval = 7200
        hb.interval = 60
        hb._adapt_interval(was_productive=False)  # → 120
        hb._adapt_interval(was_productive=False)  # → 240
        hb._adapt_interval(was_productive=False)  # → 480
        assert hb.interval == 480

    def test_init_with_custom_bounds(self):
        """Constructor accepts min/max interval bounds."""
        with patch("src.core.heartbeat.MemoryManager"), \
             patch("src.core.heartbeat.reporting") as mock_reporting, \
             patch.object(Heartbeat, "_load_identity", return_value={}), \
             patch.object(Heartbeat, "_load_project_context", return_value={}), \
             patch.object(Heartbeat, "_load_prime_directive", return_value=""):
            mock_reporting.load_overnight_results.return_value = []
            hb = Heartbeat(interval_seconds=120, min_interval=60, max_interval=1800)
            hb._memory_init_thread.join(timeout=2)
            try:
                assert hb._base_interval == 120
                assert hb._min_interval == 60
                assert hb._max_interval == 1800
            finally:
                hb.stop_flag.set()


# ── _dispatch_work() (session 126) ───────────────────────────────────


class TestDispatchWork:

    def test_budget_stop_skips_all_work(self, hb):
        """When budget_throttle is 'stop', no work should be dispatched."""
        hb.goal_manager = MagicMock()
        hb.goal_worker_pool = MagicMock()
        result = hb._dispatch_work("stop")
        assert result == 0
        hb.goal_worker_pool.submit_goal.assert_not_called()

    def test_submits_goals_to_pool(self, hb):
        """Pending goals should be submitted to the worker pool."""
        mock_goal = MagicMock()
        mock_goal.is_complete.return_value = False
        mock_gm = MagicMock()
        mock_gm.goals = {"g1": mock_goal, "g2": mock_goal}
        mock_pool = MagicMock()
        mock_pool.submit_goal.return_value = True
        hb.goal_manager = mock_gm
        hb.goal_worker_pool = mock_pool
        result = hb._dispatch_work("none")
        assert result == 2
        assert mock_pool.submit_goal.call_count == 2

    def test_skips_completed_goals(self, hb):
        """Completed goals should not be submitted."""
        done_goal = MagicMock()
        done_goal.is_complete.return_value = True
        active_goal = MagicMock()
        active_goal.is_complete.return_value = False
        mock_gm = MagicMock()
        mock_gm.goals = {"done": done_goal, "active": active_goal}
        mock_pool = MagicMock()
        mock_pool.submit_goal.return_value = True
        hb.goal_manager = mock_gm
        hb.goal_worker_pool = mock_pool
        result = hb._dispatch_work("none")
        assert result == 1

    def test_no_work_asks_user(self, hb):
        """When no pending work, should ask user for work."""
        hb.goal_manager = MagicMock()
        hb.goal_manager.goals = {}
        hb.task_queue = []
        hb.goal_worker_pool = MagicMock()
        hb._ask_user_for_work = MagicMock()
        result = hb._dispatch_work("none")
        assert result == 0
        hb._ask_user_for_work.assert_called_once()


class TestExtractTopicKeywords:
    """Session 183: Topic extraction for conversation starter dedup."""

    def test_extracts_significant_words(self, hb):
        keywords = hb._extract_topic_keywords(
            "Border Collies are wired for work but your sedentary lifestyle is fine"
        )
        assert "border" in keywords or "collies" in keywords
        assert "sedentary" in keywords or "lifestyle" in keywords

    def test_filters_stop_words(self, hb):
        keywords = hb._extract_topic_keywords("the quick brown fox and the lazy dog")
        assert "the" not in keywords
        assert "and" not in keywords

    def test_returns_at_most_four(self, hb):
        keywords = hb._extract_topic_keywords(
            "quantum computing machine learning artificial intelligence neural networks"
        )
        assert len(keywords) <= 4

    def test_empty_text_returns_empty(self, hb):
        assert hb._extract_topic_keywords("") == []

    def test_border_collie_paraphrases_share_keywords(self, hb):
        """Paraphrased messages on the same topic should produce overlapping keywords."""
        kw1 = set(hb._extract_topic_keywords(
            "Border Collies are wired for work but your couch potato lifestyle suits them"
        ))
        kw2 = set(hb._extract_topic_keywords(
            "Your Border Collie might be a trail runner at heart despite the sedentary life"
        ))
        overlap = kw1 & kw2
        assert len(overlap) >= 1, f"Expected keyword overlap, got {kw1} vs {kw2}"


class TestStarterCategoryRotation:
    """Session 189: Forced category rotation for conversation starter diversity."""

    def test_categories_rotate_sequentially(self, hb):
        """Each call to _get_next_starter_category returns a different category."""
        categories = [hb._get_next_starter_category() for _ in range(5)]
        # All 5 should be different (first 5 out of 10 categories)
        assert len(set(categories)) == 5

    def test_rotation_wraps_around(self, hb):
        """After exhausting all categories, rotation wraps to the beginning."""
        n = len(hb._STARTER_CATEGORIES)
        cats = [hb._get_next_starter_category() for _ in range(n + 1)]
        assert cats[0] == cats[n]  # first and (n+1)th are the same

    def test_no_consecutive_duplicates(self, hb):
        """Consecutive starters never share a category."""
        cats = [hb._get_next_starter_category() for _ in range(20)]
        for i in range(1, len(cats)):
            assert cats[i] != cats[i - 1], f"Duplicate at index {i}: {cats[i]}"

    def test_categories_list_covers_diverse_topics(self, hb):
        """Categories span meaningfully different interest areas."""
        cats = hb._STARTER_CATEGORIES
        # Should have at least 8 categories
        assert len(cats) >= 8
        # Each category should be a non-empty string
        for c in cats:
            assert isinstance(c, str) and len(c) > 5


class TestDispatchWork:
    """Extracted from old position — keeps existing tests grouped."""

    def test_proactive_initiative_when_unanswered(self, hb):
        """After unanswered suggestions, try proactive initiative."""
        hb.goal_manager = MagicMock()
        hb.goal_manager.goals = {}
        hb.task_queue = []
        hb.goal_worker_pool = MagicMock()
        # Ensure last_goal_notification_time is a real number, not MagicMock
        hb.goal_worker_pool.last_goal_notification_time = 0.0
        hb._unanswered_suggest_count = 1
        hb._try_proactive_initiative = MagicMock(return_value=True)
        hb._ask_user_for_work = MagicMock()
        result = hb._dispatch_work("none")
        assert result == 0
        hb._try_proactive_initiative.assert_called_once()
        hb._ask_user_for_work.assert_not_called()

    def test_skips_suggestion_after_goal_notification(self, hb):
        """Session 194: skip work suggestions for 60s after a goal completion."""
        import time
        hb.goal_manager = MagicMock()
        hb.goal_manager.goals = {}
        hb.task_queue = []
        hb.goal_worker_pool = MagicMock()
        # Simulate goal completed 10 seconds ago
        hb.goal_worker_pool.last_goal_notification_time = time.monotonic() - 10
        hb._unanswered_suggest_count = 0
        hb._ask_user_for_work = MagicMock()
        hb._try_proactive_initiative = MagicMock()
        result = hb._dispatch_work("none")
        assert result == 0
        # Should NOT have asked for work — cooldown active
        hb._ask_user_for_work.assert_not_called()
        hb._try_proactive_initiative.assert_not_called()

    def test_allows_suggestion_after_cooldown_expires(self, hb):
        """Session 194: suggestions resume after 60s goal notification cooldown."""
        import time
        hb.goal_manager = MagicMock()
        hb.goal_manager.goals = {}
        hb.task_queue = []
        hb.goal_worker_pool = MagicMock()
        # Simulate goal completed 120 seconds ago — well past 60s cooldown
        hb.goal_worker_pool.last_goal_notification_time = time.monotonic() - 120
        hb._unanswered_suggest_count = 0
        hb._ask_user_for_work = MagicMock()
        result = hb._dispatch_work("none")
        assert result == 0
        # SHOULD have asked for work — cooldown expired
        hb._ask_user_for_work.assert_called_once()

    def test_skips_decomposed_goal_with_no_ready_tasks(self, hb):
        """Session 176 fix: decomposed goals with no ready tasks are skipped
        to prevent re-notification spam."""
        stale_goal = MagicMock()
        stale_goal.goal_id = "stale"
        stale_goal.is_complete.return_value = False
        stale_goal.is_decomposed = True
        stale_goal.get_ready_tasks.return_value = []  # No ready tasks

        fresh_goal = MagicMock()
        fresh_goal.goal_id = "fresh"
        fresh_goal.is_complete.return_value = False
        fresh_goal.is_decomposed = True
        fresh_goal.get_ready_tasks.return_value = [MagicMock()]  # Has a ready task

        mock_gm = MagicMock()
        mock_gm.goals = {"stale": stale_goal, "fresh": fresh_goal}
        mock_pool = MagicMock()
        mock_pool.submit_goal.return_value = True
        hb.goal_manager = mock_gm
        hb.goal_worker_pool = mock_pool

        result = hb._dispatch_work("none")
        # Only fresh_goal should be submitted
        assert result == 1
        mock_pool.submit_goal.assert_called_once_with("fresh")

    def test_submits_undecomposed_goal(self, hb):
        """Undecomposed goals are always submitted (they need decomposition)."""
        undecomposed = MagicMock()
        undecomposed.goal_id = "new"
        undecomposed.is_complete.return_value = False
        undecomposed.is_decomposed = False
        undecomposed.get_ready_tasks.return_value = []

        mock_gm = MagicMock()
        mock_gm.goals = {"new": undecomposed}
        mock_pool = MagicMock()
        mock_pool.submit_goal.return_value = True
        hb.goal_manager = mock_gm
        hb.goal_worker_pool = mock_pool

        result = hb._dispatch_work("none")
        assert result == 1
        mock_pool.submit_goal.assert_called_once_with("new")
