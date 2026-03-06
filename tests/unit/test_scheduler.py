"""Unit tests for the scheduled task system (session 196)."""

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from src.core.scheduler import (
    ScheduledTask,
    TaskStats,
    compute_next_run,
    validate_cron,
    load_schedule,
    save_schedule,
    create_task,
    modify_task,
    remove_task,
    list_tasks,
    get_task,
    check_due_tasks,
    advance_task,
    record_engagement,
    get_ignored_tasks,
    is_quiet_hours,
    check_fire_rate,
    format_task_list,
    slugify,
    MAX_TASKS,
    MAX_FIRES_PER_HOUR,
    _DT_FMT,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def tmp_schedule(tmp_path, monkeypatch):
    """Redirect schedule file to a temp directory."""
    schedule_file = tmp_path / "data" / "scheduled_tasks.json"
    schedule_file.parent.mkdir(parents=True, exist_ok=True)
    schedule_file.write_text("[]")
    monkeypatch.setattr(
        "src.core.scheduler._schedule_path",
        lambda: str(schedule_file),
    )
    return schedule_file


@pytest.fixture
def sample_task() -> ScheduledTask:
    """A sample task for testing."""
    return ScheduledTask(
        id="test-reminder",
        description="Test reminder",
        cron="0 9 * * *",
        next_run_at="2026-03-06T09:00:00",
        action="notify",
        payload="Time to test!",
        created_by="user",
        enabled=True,
        on_miss="skip",
        created_at="2026-03-05T10:00:00",
    )


# ── Data model tests ─────────────────────────────────────────────────

class TestScheduledTask:
    def test_from_dict_roundtrip(self, sample_task):
        d = sample_task.to_dict()
        restored = ScheduledTask.from_dict(d)
        assert restored.id == sample_task.id
        assert restored.description == sample_task.description
        assert restored.cron == sample_task.cron
        assert restored.action == sample_task.action
        assert restored.stats.times_fired == 0

    def test_stats_from_dict(self):
        d = {
            "id": "t1", "description": "Test", "cron": "0 9 * * *",
            "next_run_at": "2026-03-06T09:00:00", "action": "notify",
            "payload": "", "created_by": "user", "enabled": True,
            "on_miss": "skip", "created_at": "2026-03-05T10:00:00",
            "stats": {"times_fired": 5, "times_acknowledged": 3,
                       "times_ignored": 2, "last_fired": "2026-03-05T09:00:00",
                       "last_acknowledged": "2026-03-05T09:01:00"},
        }
        task = ScheduledTask.from_dict(d)
        assert task.stats.times_fired == 5
        assert task.stats.times_acknowledged == 3

    def test_auto_created_at(self):
        task = ScheduledTask(id="t", description="t", cron="0 9 * * *",
                             next_run_at="2026-01-01T00:00:00")
        assert task.created_at  # Should be auto-set

    def test_stats_dict_coercion(self):
        """Stats passed as dict should be converted to TaskStats."""
        task = ScheduledTask(
            id="t", description="t", cron="0 9 * * *",
            next_run_at="2026-01-01T00:00:00",
            stats={"times_fired": 10, "times_acknowledged": 7,
                    "times_ignored": 3, "last_fired": None,
                    "last_acknowledged": None},
        )
        assert isinstance(task.stats, TaskStats)
        assert task.stats.times_fired == 10


# ── Cron helpers ──────────────────────────────────────────────────────

class TestCronHelpers:
    def test_validate_valid_cron(self):
        assert validate_cron("0 9 * * *")
        assert validate_cron("15 16 * * *")
        assert validate_cron("0 8 * * 1-5")
        assert validate_cron("30 7 1 * *")

    def test_validate_invalid_cron(self):
        assert not validate_cron("not a cron")
        assert not validate_cron("")
        assert not validate_cron("60 25 * * *")

    def test_compute_next_run_basic(self):
        after = datetime(2026, 3, 5, 10, 0, 0)
        result = compute_next_run("0 12 * * *", after=after)
        assert result == "2026-03-05T12:00:00"

    def test_compute_next_run_wraps_day(self):
        after = datetime(2026, 3, 5, 20, 0, 0)
        result = compute_next_run("0 9 * * *", after=after)
        assert result == "2026-03-06T09:00:00"

    def test_compute_next_run_invalid_cron_fallback(self):
        after = datetime(2026, 3, 5, 10, 0, 0)
        result = compute_next_run("bad cron", after=after)
        # Should fallback to +1h
        expected = (after + timedelta(hours=1)).strftime(_DT_FMT)
        assert result == expected


# ── Persistence tests ─────────────────────────────────────────────────

class TestPersistence:
    def test_load_empty(self, tmp_schedule):
        tasks = load_schedule()
        assert tasks == []

    def test_save_and_load(self, tmp_schedule, sample_task):
        save_schedule([sample_task])
        loaded = load_schedule()
        assert len(loaded) == 1
        assert loaded[0].id == "test-reminder"
        assert loaded[0].stats.times_fired == 0

    def test_load_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.core.scheduler._schedule_path",
            lambda: str(tmp_path / "nonexistent.json"),
        )
        assert load_schedule() == []

    def test_load_corrupt_file(self, tmp_schedule):
        tmp_schedule.write_text("not json")
        assert load_schedule() == []


# ── CRUD tests ────────────────────────────────────────────────────────

class TestCRUD:
    def test_create_task(self, tmp_schedule):
        task = create_task("daily-stretch", "Stretch reminder", "15 16 * * *")
        assert task is not None
        assert task.id == "daily-stretch"
        # Verify persisted
        loaded = load_schedule()
        assert len(loaded) == 1

    def test_create_duplicate_fails(self, tmp_schedule):
        create_task("t1", "First", "0 9 * * *")
        result = create_task("t1", "Duplicate", "0 10 * * *")
        assert result is None
        assert len(load_schedule()) == 1

    def test_create_invalid_cron_fails(self, tmp_schedule):
        result = create_task("bad", "Bad cron", "not valid")
        assert result is None
        assert len(load_schedule()) == 0

    def test_create_at_capacity(self, tmp_schedule):
        for i in range(MAX_TASKS):
            create_task(f"t{i}", f"Task {i}", "0 9 * * *")
        result = create_task("overflow", "One too many", "0 9 * * *")
        assert result is None

    def test_modify_task(self, tmp_schedule):
        create_task("t1", "Original", "0 9 * * *")
        task = modify_task("t1", description="Updated", cron="0 10 * * *")
        assert task is not None
        assert task.description == "Updated"
        assert "10:00" in task.next_run_at

    def test_modify_nonexistent(self, tmp_schedule):
        assert modify_task("ghost", description="nope") is None

    def test_modify_invalid_cron(self, tmp_schedule):
        create_task("t1", "Test", "0 9 * * *")
        assert modify_task("t1", cron="bad") is None

    def test_remove_task(self, tmp_schedule):
        create_task("t1", "Test", "0 9 * * *")
        assert remove_task("t1")
        assert len(load_schedule()) == 0

    def test_remove_nonexistent(self, tmp_schedule):
        assert not remove_task("ghost")

    def test_list_tasks(self, tmp_schedule):
        create_task("t1", "First", "0 9 * * *")
        create_task("t2", "Second", "0 10 * * *")
        tasks = list_tasks()
        assert len(tasks) == 2

    def test_list_tasks_exclude_disabled(self, tmp_schedule):
        create_task("t1", "Enabled", "0 9 * * *")
        create_task("t2", "Disabled", "0 10 * * *", enabled=False)
        enabled = list_tasks(include_disabled=False)
        assert len(enabled) == 1
        assert enabled[0].id == "t1"

    def test_get_task(self, tmp_schedule):
        create_task("t1", "Test", "0 9 * * *")
        task = get_task("t1")
        assert task is not None
        assert task.id == "t1"
        assert get_task("ghost") is None


# ── Due task checking ─────────────────────────────────────────────────

class TestDueTasks:
    def test_check_due_basic(self, tmp_schedule):
        # Create a task with next_run in the past
        task = create_task("past", "Past task", "0 9 * * *")
        # Manually set next_run_at to the past
        tasks = load_schedule()
        tasks[0].next_run_at = "2020-01-01T00:00:00"
        save_schedule(tasks)

        due = check_due_tasks()
        assert len(due) == 1
        assert due[0].id == "past"

    def test_check_due_skips_disabled(self, tmp_schedule):
        create_task("disabled", "Disabled", "0 9 * * *", enabled=False)
        tasks = load_schedule()
        tasks[0].next_run_at = "2020-01-01T00:00:00"
        save_schedule(tasks)

        due = check_due_tasks()
        assert len(due) == 0

    def test_check_due_future_not_returned(self, tmp_schedule):
        create_task("future", "Future task", "0 9 * * *")
        # Default next_run_at is in the future
        due = check_due_tasks(now=datetime(2020, 1, 1))
        assert len(due) == 0


# ── Advance / fire ────────────────────────────────────────────────────

class TestAdvance:
    def test_advance_updates_stats(self, tmp_schedule):
        create_task("t1", "Test", "0 9 * * *")
        advance_task("t1")

        task = get_task("t1")
        assert task.stats.times_fired == 1
        assert task.stats.last_fired is not None

    def test_advance_updates_next_run(self, tmp_schedule):
        # Use an hourly cron so next_run always shifts forward
        create_task("t1", "Test", "0 * * * *")
        tasks = load_schedule()
        # Force next_run to a past time so advance computes from "now"
        tasks[0].next_run_at = "2020-01-01T00:00:00"
        save_schedule(tasks)
        advance_task("t1")
        updated = get_task("t1").next_run_at
        assert updated > "2020-01-01T00:00:00"  # Should have moved forward


# ── Engagement tracking ──────────────────────────────────────────────

class TestEngagement:
    def test_record_acknowledged(self, tmp_schedule):
        create_task("t1", "Test", "0 9 * * *")
        record_engagement("t1", acknowledged=True)
        task = get_task("t1")
        assert task.stats.times_acknowledged == 1
        assert task.stats.last_acknowledged is not None

    def test_record_ignored(self, tmp_schedule):
        create_task("t1", "Test", "0 9 * * *")
        record_engagement("t1", acknowledged=False)
        task = get_task("t1")
        assert task.stats.times_ignored == 1

    def test_get_ignored_tasks(self, tmp_schedule):
        task = create_task("ignored", "Ignored task", "0 9 * * *")
        # Manually set stats to simulate ignored task
        tasks = load_schedule()
        tasks[0].stats = TaskStats(
            times_fired=10, times_acknowledged=2, times_ignored=8,
            last_fired="2026-03-05T09:00:00",
        )
        tasks[0].created_at = "2026-02-01T00:00:00"  # > 14 days ago
        save_schedule(tasks)

        ignored = get_ignored_tasks(threshold_days=14, ignore_rate=0.7)
        assert len(ignored) == 1
        assert ignored[0].id == "ignored"

    def test_get_ignored_respects_min_fires(self, tmp_schedule):
        create_task("low", "Low fires", "0 9 * * *")
        tasks = load_schedule()
        tasks[0].stats = TaskStats(times_fired=3, times_acknowledged=0, times_ignored=3)
        tasks[0].created_at = "2026-02-01T00:00:00"
        save_schedule(tasks)

        ignored = get_ignored_tasks(min_fires=5)
        assert len(ignored) == 0  # Not enough fires

    def test_get_ignored_respects_threshold_days(self, tmp_schedule):
        create_task("new", "New task", "0 9 * * *")
        tasks = load_schedule()
        tasks[0].stats = TaskStats(times_fired=10, times_acknowledged=1, times_ignored=9)
        # Created today — shouldn't be considered for retirement yet
        save_schedule(tasks)

        ignored = get_ignored_tasks(threshold_days=14)
        assert len(ignored) == 0


# ── Quiet hours & rate limiting ───────────────────────────────────────

class TestSafety:
    def test_quiet_hours_late_night(self):
        assert is_quiet_hours(datetime(2026, 3, 5, 23, 30))

    def test_quiet_hours_early_morning(self):
        assert is_quiet_hours(datetime(2026, 3, 5, 4, 0))

    def test_not_quiet_hours_daytime(self):
        assert not is_quiet_hours(datetime(2026, 3, 5, 12, 0))

    def test_check_fire_rate_under(self):
        tasks = [ScheduledTask(
            id="t1", description="t", cron="0 * * * *",
            next_run_at="2026-01-01T00:00:00",
            stats=TaskStats(times_fired=1, last_fired="2020-01-01T00:00:00"),
        )]
        assert check_fire_rate(tasks)

    def test_check_fire_rate_over(self):
        now_str = datetime.now().strftime(_DT_FMT)
        tasks = [
            ScheduledTask(
                id=f"t{i}", description="t", cron="* * * * *",
                next_run_at="2026-01-01T00:00:00",
                stats=TaskStats(times_fired=1, last_fired=now_str),
            )
            for i in range(MAX_FIRES_PER_HOUR)
        ]
        assert not check_fire_rate(tasks)


# ── Formatting ────────────────────────────────────────────────────────

class TestFormatting:
    def test_format_empty(self):
        assert format_task_list([]) == "No scheduled tasks."

    def test_format_with_tasks(self, sample_task):
        result = format_task_list([sample_task])
        assert "test-reminder" in result
        assert "Test reminder" in result

    def test_slugify_basic(self):
        assert slugify("Remind me to stretch") == "remind-me-to-stretch"

    def test_slugify_special_chars(self):
        assert slugify("Hello!!! World???") == "hello-world"

    def test_slugify_empty(self):
        assert slugify("") == "task"

    def test_slugify_long(self):
        long = "a" * 100
        assert len(slugify(long)) <= 60


# ── Heartbeat integration ────────────────────────────────────────────

class TestHeartbeatIntegration:
    def test_check_scheduled_tasks_no_due(self, tmp_schedule):
        """Heartbeat check with no due tasks should be a no-op."""
        from src.core.heartbeat import Heartbeat
        hb = Heartbeat.__new__(Heartbeat)
        hb.stop_flag = MagicMock()
        hb.stop_flag.is_set.return_value = False
        hb.goal_manager = None

        # Should not raise
        hb._check_scheduled_tasks()

    @patch("src.core.scheduler.check_due_tasks")
    @patch("src.core.scheduler.load_schedule")
    @patch("src.core.scheduler.check_fire_rate", return_value=True)
    def test_check_scheduled_tasks_fires_notify(
        self, mock_rate, mock_load, mock_due, tmp_schedule
    ):
        from src.core.heartbeat import Heartbeat
        hb = Heartbeat.__new__(Heartbeat)
        hb.stop_flag = MagicMock()
        hb.stop_flag.is_set.return_value = False
        hb.goal_manager = None

        task = ScheduledTask(
            id="test", description="Test", cron="0 9 * * *",
            next_run_at="2020-01-01T00:00:00", action="notify",
            payload="Hello!",
        )
        mock_due.return_value = [task]
        mock_load.return_value = [task]

        with patch.object(hb, "_fire_scheduled_task") as mock_fire:
            hb._check_scheduled_tasks()
            mock_fire.assert_called_once_with(task)


# ── Action dispatcher integration ────────────────────────────────────

class TestDispatcherIntegration:
    def test_create_schedule_handler(self, tmp_schedule):
        from src.interfaces.action_dispatcher import _handle_create_schedule
        params = {
            "description": "Stretch reminder",
            "cron": "15 16 * * *",
            "action": "notify",
            "payload": "Time to stretch!",
        }
        response, actions, cost = _handle_create_schedule(params, {})
        assert "on schedule" in response.lower() or "got it" in response.lower()
        assert len(actions) == 1

    def test_create_schedule_missing_desc(self, tmp_schedule):
        from src.interfaces.action_dispatcher import _handle_create_schedule
        response, actions, cost = _handle_create_schedule({}, {})
        assert "need to know" in response.lower()

    def test_create_schedule_bad_cron(self, tmp_schedule):
        from src.interfaces.action_dispatcher import _handle_create_schedule
        params = {"description": "Test", "cron": "bad"}
        response, actions, cost = _handle_create_schedule(params, {})
        assert "couldn't parse" in response.lower()

    def test_list_schedule_handler(self, tmp_schedule):
        from src.interfaces.action_dispatcher import _handle_list_schedule
        create_task("t1", "Test", "0 9 * * *")
        response, actions, cost = _handle_list_schedule({}, {})
        assert "t1" in response

    def test_remove_schedule_handler(self, tmp_schedule):
        from src.interfaces.action_dispatcher import _handle_remove_schedule
        create_task("t1", "Test", "0 9 * * *")
        response, actions, cost = _handle_remove_schedule({"task_id": "t1"}, {})
        assert "removed" in response.lower()
        assert len(load_schedule()) == 0

    def test_modify_schedule_handler(self, tmp_schedule):
        from src.interfaces.action_dispatcher import _handle_modify_schedule
        create_task("t1", "Test", "0 9 * * *")
        params = {"task_id": "t1", "cron": "0 10 * * *"}
        response, actions, cost = _handle_modify_schedule(params, {})
        assert "updated" in response.lower()
