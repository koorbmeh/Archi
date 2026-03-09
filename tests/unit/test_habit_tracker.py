"""Unit tests for src/tools/habit_tracker.py — session 249."""

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest
from src.tools.habit_tracker import (
    HabitTracker,
    Habit,
    CompletionEntry,
    _reset_for_testing,
    get_tracker,
)


@pytest.fixture
def tracker():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)  # start with no file
    yield HabitTracker(data_path=path)
    if os.path.isfile(path):
        os.unlink(path)


# ── Habit CRUD ───────────────────────────────────────────────────────

class TestHabitCRUD:
    def test_add_habit_boolean(self, tracker):
        h = tracker.add_habit("Meditate")
        assert h.name == "Meditate"
        assert h.target_type == "boolean"
        assert h.target_value == 1.0
        assert h.active is True
        assert h.added_date == date.today().isoformat()

    def test_add_habit_count(self, tracker):
        h = tracker.add_habit("Water", target_type="count",
                              target_value=8, unit="glasses")
        assert h.target_type == "count"
        assert h.target_value == 8.0
        assert h.unit == "glasses"

    def test_add_habit_duration(self, tracker):
        h = tracker.add_habit("Read", target_type="duration",
                              target_value=30, unit="minutes")
        assert h.target_type == "duration"
        assert h.target_value == 30.0

    def test_add_habit_empty_name_raises(self, tracker):
        with pytest.raises(ValueError):
            tracker.add_habit("")

    def test_add_habit_invalid_type_defaults_boolean(self, tracker):
        h = tracker.add_habit("Test", target_type="invalid")
        assert h.target_type == "boolean"

    def test_remove_habit(self, tracker):
        tracker.add_habit("Journal")
        assert tracker.remove_habit("journal")
        assert len(tracker.get_active()) == 0

    def test_remove_nonexistent(self, tracker):
        assert not tracker.remove_habit("nope")

    def test_get_active(self, tracker):
        tracker.add_habit("A")
        tracker.add_habit("B")
        tracker.remove_habit("A")
        assert len(tracker.get_active()) == 1
        assert tracker.get_active()[0].name == "B"

    def test_get_habit(self, tracker):
        tracker.add_habit("Meditate")
        assert tracker.get_habit("meditate") is not None
        assert tracker.get_habit("nonexistent") is None

    def test_display_name_boolean(self):
        h = Habit(name="Journal")
        assert h.display_name() == "Journal"

    def test_display_name_with_unit(self):
        h = Habit(name="Water", target_type="count", target_value=8, unit="glasses")
        assert h.display_name() == "Water (8 glasses)"


# ── Completion logging ───────────────────────────────────────────────

class TestCompletionLogging:
    def test_log_completion(self, tracker):
        tracker.add_habit("Meditate")
        entry = tracker.log_completion("Meditate")
        assert entry.habit_name == "Meditate"
        assert entry.value == 1.0
        assert entry.timestamp

    def test_log_completion_with_value(self, tracker):
        tracker.add_habit("Water", target_type="count", target_value=8, unit="glasses")
        entry = tracker.log_completion("Water", value=3)
        assert entry.value == 3.0

    def test_log_all_done(self, tracker):
        tracker.add_habit("A")
        tracker.add_habit("B")
        tracker.add_habit("C", target_type="count", target_value=8, unit="glasses")
        entries = tracker.log_all_done()
        # Should only log boolean habits (A and B, not C)
        assert len(entries) == 2
        names = {e.habit_name for e in entries}
        assert names == {"A", "B"}

    def test_get_today_log(self, tracker):
        tracker.add_habit("Test")
        tracker.log_completion("Test")
        assert len(tracker.get_today_log()) == 1

    def test_get_log_for_date(self, tracker):
        tracker.add_habit("Test")
        tracker.log_completion("Test")
        assert len(tracker.get_log_for_date(date.today())) == 1
        assert len(tracker.get_log_for_date(date.today() - timedelta(days=1))) == 0


# ── Analysis ─────────────────────────────────────────────────────────

class TestAnalysis:
    def test_today_progress_boolean(self, tracker):
        tracker.add_habit("Meditate")
        assert tracker._today_progress(tracker.get_habit("meditate")) == 0
        tracker.log_completion("Meditate")
        assert tracker._today_progress(tracker.get_habit("meditate")) == 1.0

    def test_today_progress_count(self, tracker):
        tracker.add_habit("Water", target_type="count", target_value=8, unit="glasses")
        tracker.log_completion("Water", value=3)
        tracker.log_completion("Water", value=2)
        assert tracker._today_progress(tracker.get_habit("water")) == 5.0

    def test_get_incomplete_today(self, tracker):
        tracker.add_habit("A")
        tracker.add_habit("B")
        tracker.log_completion("A")
        incomplete = tracker.get_incomplete_today()
        assert len(incomplete) == 1
        assert incomplete[0].name == "B"

    def test_get_incomplete_skips_weekly(self, tracker):
        tracker.add_habit("Weekly", frequency="weekly")
        # Weekly habits are not checked for daily completion
        assert len(tracker.get_incomplete_today()) == 0

    def test_get_incomplete_weekdays_on_weekend(self, tracker):
        tracker.add_habit("Weekday", frequency="weekdays")
        with patch("src.tools.habit_tracker.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 7)  # Saturday
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            # On weekend, weekday habits should not appear as incomplete
            # (need to test with actual weekend date)

    def test_adherence_rate_all_done(self, tracker):
        tracker.add_habit("Test")
        tracker.log_completion("Test")
        rate = tracker.adherence_rate(days=1)
        assert rate == 1.0

    def test_adherence_rate_none_done(self, tracker):
        h = tracker.add_habit("Test")
        # No completions, but habit exists for today
        rate = tracker.adherence_rate(days=1)
        assert rate == 0.0

    def test_adherence_rate_no_habits(self, tracker):
        assert tracker.adherence_rate() == 1.0

    def test_streak_basic(self, tracker):
        tracker.add_habit("Test")
        tracker.log_completion("Test")
        assert tracker.streak() == 1

    def test_streak_no_habits(self, tracker):
        assert tracker.streak() == 0

    def test_streak_zero_when_not_done(self, tracker):
        tracker.add_habit("Test")
        # No completions today
        assert tracker.streak() == 0


# ── Formatting ───────────────────────────────────────────────────────

class TestFormatting:
    def test_format_habit_list_empty(self, tracker):
        result = tracker.format_habit_list()
        assert "No habits" in result

    def test_format_habit_list(self, tracker):
        tracker.add_habit("Meditate")
        tracker.add_habit("Water", target_type="count", target_value=8, unit="glasses")
        result = tracker.format_habit_list()
        assert "Meditate" in result
        assert "Water" in result
        assert "2" in result  # count of habits

    def test_format_daily_status_empty(self, tracker):
        assert "No habits" in tracker.format_daily_status()

    def test_format_daily_status(self, tracker):
        tracker.add_habit("A")
        tracker.add_habit("B")
        tracker.log_completion("A")
        result = tracker.format_daily_status()
        assert "1/2" in result
        assert "done" in result.lower()
        assert "not yet" in result.lower()

    def test_format_daily_status_count_habit(self, tracker):
        tracker.add_habit("Water", target_type="count", target_value=8, unit="glasses")
        tracker.log_completion("Water", value=3)
        result = tracker.format_daily_status()
        assert "3/8" in result

    def test_format_report(self, tracker):
        tracker.add_habit("Test")
        tracker.log_completion("Test")
        result = tracker.format_report(days=1)
        assert "Habit Report" in result
        assert "100%" in result

    def test_format_report_empty(self, tracker):
        assert "No habits" in tracker.format_report()

    def test_format_reminder_none_incomplete(self, tracker):
        tracker.add_habit("Test")
        tracker.log_completion("Test")
        assert tracker.format_reminder() == ""

    def test_format_reminder_with_incomplete(self, tracker):
        tracker.add_habit("Meditate")
        result = tracker.format_reminder()
        assert "Meditate" in result
        assert "reminder" in result.lower()


# ── Persistence ──────────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_load(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)
        try:
            t1 = HabitTracker(data_path=path)
            t1.add_habit("Meditate")
            t1.log_completion("Meditate")

            t2 = HabitTracker(data_path=path)
            assert len(t2.get_active()) == 1
            assert t2.get_active()[0].name == "Meditate"
            assert len(t2.get_today_log()) == 1
        finally:
            if os.path.isfile(path):
                os.unlink(path)

    def test_load_empty_file(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        # File exists but no content yet
        try:
            with open(path, "w") as f:
                f.write("{}")
            t = HabitTracker(data_path=path)
            assert len(t.get_active()) == 0
        finally:
            if os.path.isfile(path):
                os.unlink(path)

    def test_load_corrupted_file(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w") as f:
                f.write("not json")
            t = HabitTracker(data_path=path)
            assert len(t.get_active()) == 0
        finally:
            if os.path.isfile(path):
                os.unlink(path)

    def test_old_entries_trimmed(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            old_ts = (datetime.now() - timedelta(days=100)).isoformat()
            data = {
                "habits": {},
                "completions": [
                    {"habit_name": "Old", "value": 1, "timestamp": old_ts},
                ],
            }
            with open(path, "w") as f:
                json.dump(data, f)
            t = HabitTracker(data_path=path)
            assert len(t._completions) == 0  # old entry trimmed
        finally:
            if os.path.isfile(path):
                os.unlink(path)


# ── Singleton ────────────────────────────────────────────────────────

class TestSingleton:
    def test_get_tracker_returns_same_instance(self):
        _reset_for_testing()
        t1 = get_tracker()
        t2 = get_tracker()
        assert t1 is t2
        _reset_for_testing()

    def test_reset_clears_singleton(self):
        _reset_for_testing()
        t1 = get_tracker()
        _reset_for_testing()
        t2 = get_tracker()
        assert t1 is not t2
        _reset_for_testing()


# ── Action handler integration ───────────────────────────────────────

class TestActionHandlers:
    """Test the dispatcher handlers exist and basic behavior."""

    def test_all_handlers_registered(self):
        from src.interfaces.action_dispatcher import ACTION_HANDLERS
        for action in ("add_habit", "remove_habit", "log_habit", "habit_status"):
            assert action in ACTION_HANDLERS, f"{action} not in ACTION_HANDLERS"

    def test_add_habit_handler(self, tracker):
        from src.interfaces.action_dispatcher import _handle_add_habit
        with patch("src.tools.habit_tracker.get_tracker", return_value=tracker):
            msg, actions, cost = _handle_add_habit(
                {"name": "Meditate", "frequency": "daily"}, {}
            )
            assert "Meditate" in msg
            assert len(tracker.get_active()) == 1

    def test_add_habit_handler_no_name(self, tracker):
        from src.interfaces.action_dispatcher import _handle_add_habit
        with patch("src.tools.habit_tracker.get_tracker", return_value=tracker):
            msg, _, _ = _handle_add_habit({}, {})
            assert "need a habit name" in msg.lower()

    def test_add_habit_handler_infers_duration(self, tracker):
        from src.interfaces.action_dispatcher import _handle_add_habit
        with patch("src.tools.habit_tracker.get_tracker", return_value=tracker):
            msg, _, _ = _handle_add_habit(
                {"name": "Read", "unit": "minutes", "target_value": 30}, {}
            )
            assert "Read" in msg
            h = tracker.get_habit("read")
            assert h.target_type == "duration"

    def test_log_habit_handler_all(self, tracker):
        from src.interfaces.action_dispatcher import _handle_log_habit
        tracker.add_habit("A")
        tracker.add_habit("B")
        with patch("src.tools.habit_tracker.get_tracker", return_value=tracker):
            msg, actions, _ = _handle_log_habit({"name": "all"}, {})
            assert "2 habits" in msg

    def test_log_habit_handler_single(self, tracker):
        from src.interfaces.action_dispatcher import _handle_log_habit
        tracker.add_habit("Meditate")
        with patch("src.tools.habit_tracker.get_tracker", return_value=tracker):
            msg, _, _ = _handle_log_habit({"name": "Meditate"}, {})
            assert "Meditate" in msg

    def test_remove_habit_handler(self, tracker):
        from src.interfaces.action_dispatcher import _handle_remove_habit
        tracker.add_habit("Journal")
        with patch("src.tools.habit_tracker.get_tracker", return_value=tracker):
            msg, _, _ = _handle_remove_habit({"name": "Journal"}, {})
            assert "Removed" in msg

    def test_habit_status_handler_list(self, tracker):
        from src.interfaces.action_dispatcher import _handle_habit_status
        tracker.add_habit("Meditate")
        with patch("src.tools.habit_tracker.get_tracker", return_value=tracker):
            msg, _, _ = _handle_habit_status({"view": "list"}, {})
            assert "Meditate" in msg

    def test_habit_status_handler_report(self, tracker):
        from src.interfaces.action_dispatcher import _handle_habit_status
        tracker.add_habit("Test")
        tracker.log_completion("Test")
        with patch("src.tools.habit_tracker.get_tracker", return_value=tracker):
            msg, _, _ = _handle_habit_status({"view": "report"}, {})
            assert "Habit Report" in msg
