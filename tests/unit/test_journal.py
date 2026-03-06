"""Unit tests for journal.py — daily continuity-of-experience system.

Tests data persistence, entry recording, summary counters, query helpers,
orientation, pruning, and edge cases.

Created session 197.
"""

import json
import os
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from src.core.journal import (
    load_day,
    save_day,
    add_entry,
    get_recent_entries,
    get_day_summary,
    get_orientation,
    prune_old_journals,
    _empty_day,
    _journal_path,
    _MAX_ENTRIES_PER_DAY,
    _RETENTION_DAYS,
    _DATE_FMT,
    _DT_FMT,
)


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    """Redirect journal directory to a temp path for isolation."""
    journal_dir = tmp_path / "data" / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("src.core.journal._journal_dir", lambda: str(journal_dir))
    return journal_dir


# ── Empty day skeleton ───────────────────────────────────────────────

class TestEmptyDay:

    def test_skeleton_structure(self):
        d = _empty_day()
        assert "date" in d
        assert "entries" in d
        assert "summary" in d
        assert d["entries"] == []
        assert d["summary"]["tasks_completed"] == 0
        assert d["summary"]["conversations"] == 0


# ── Persistence ──────────────────────────────────────────────────────

class TestPersistence:

    def test_load_missing_returns_skeleton(self, tmp_journal):
        data = load_day(date(2026, 1, 1))
        assert data["entries"] == []
        assert data["date"] == "2026-01-01"

    def test_save_and_load_roundtrip(self, tmp_journal):
        day = date(2026, 3, 5)
        data = {"date": "2026-03-05", "entries": [{"type": "test", "content": "hello"}],
                "summary": {"tasks_completed": 1, "conversations": 0,
                            "observations": 0, "things_learned": 0}}
        save_day(data)
        loaded = load_day(day)
        assert len(loaded["entries"]) == 1
        assert loaded["entries"][0]["content"] == "hello"

    def test_save_creates_directory(self, tmp_path, monkeypatch):
        deep_dir = tmp_path / "deep" / "nested" / "journal"
        monkeypatch.setattr("src.core.journal._journal_dir", lambda: str(deep_dir))
        data = _empty_day()
        data["date"] = "2026-03-05"
        save_day(data)
        assert (deep_dir / "2026-03-05.json").exists()

    def test_load_corrupt_returns_skeleton(self, tmp_journal):
        day = date(2026, 3, 5)
        path = tmp_journal / "2026-03-05.json"
        path.write_text("not valid json{{{")
        data = load_day(day)
        assert data["entries"] == []

    def test_atomic_write_no_tmp_files(self, tmp_journal):
        data = _empty_day()
        data["date"] = "2026-03-05"
        save_day(data)
        tmp_files = list(tmp_journal.glob("*.tmp"))
        assert len(tmp_files) == 0


# ── Entry recording ──────────────────────────────────────────────────

class TestAddEntry:

    def test_basic_entry(self, tmp_journal):
        today = date.today()
        add_entry("task_completed", "Built the scheduler", day=today)
        data = load_day(today)
        assert len(data["entries"]) == 1
        assert data["entries"][0]["type"] == "task_completed"
        assert data["entries"][0]["content"] == "Built the scheduler"
        assert "time" in data["entries"][0]

    def test_entry_with_metadata(self, tmp_journal):
        today = date.today()
        add_entry("observation", "Code is clean", metadata={"file": "scheduler.py"}, day=today)
        data = load_day(today)
        assert data["entries"][0]["metadata"]["file"] == "scheduler.py"

    def test_summary_counter_task(self, tmp_journal):
        today = date.today()
        add_entry("task_completed", "task 1", day=today)
        add_entry("task_completed", "task 2", day=today)
        data = load_day(today)
        assert data["summary"]["tasks_completed"] == 2

    def test_summary_counter_conversation(self, tmp_journal):
        today = date.today()
        add_entry("conversation", "chatted about weather", day=today)
        data = load_day(today)
        assert data["summary"]["conversations"] == 1

    def test_summary_counter_observation(self, tmp_journal):
        today = date.today()
        add_entry("observation", "noticed a pattern", day=today)
        data = load_day(today)
        assert data["summary"]["observations"] == 1

    def test_summary_counter_thing_learned(self, tmp_journal):
        today = date.today()
        add_entry("thing_learned", "circuit breakers are cool", day=today)
        data = load_day(today)
        assert data["summary"]["things_learned"] == 1

    def test_non_counter_type(self, tmp_journal):
        """Types without a counter mapping shouldn't crash."""
        today = date.today()
        add_entry("dream_cycle", "cycle ran", day=today)
        data = load_day(today)
        assert len(data["entries"]) == 1
        # Counters unchanged
        assert data["summary"]["tasks_completed"] == 0

    def test_entry_cap(self, tmp_journal):
        """Entries beyond MAX_ENTRIES_PER_DAY should be skipped."""
        today = date.today()
        # Pre-fill with max entries
        data = _empty_day()
        data["date"] = today.strftime(_DATE_FMT)
        data["entries"] = [{"type": "filler", "content": f"entry {i}",
                            "time": "2026-03-05T00:00:00"}
                           for i in range(_MAX_ENTRIES_PER_DAY)]
        save_day(data)
        # This should be silently dropped
        add_entry("task_completed", "one too many", day=today)
        loaded = load_day(today)
        assert len(loaded["entries"]) == _MAX_ENTRIES_PER_DAY

    def test_multiple_entries_accumulate(self, tmp_journal):
        today = date.today()
        for i in range(5):
            add_entry("observation", f"note {i}", day=today)
        data = load_day(today)
        assert len(data["entries"]) == 5
        assert data["summary"]["observations"] == 5


# ── Query helpers ────────────────────────────────────────────────────

class TestGetRecentEntries:

    def test_recent_from_today(self, tmp_journal):
        today = date.today()
        add_entry("task_completed", "task A", day=today)
        add_entry("observation", "obs B", day=today)
        entries = get_recent_entries(days=1)
        assert len(entries) == 2

    def test_recent_multi_day(self, tmp_journal):
        today = date.today()
        yesterday = today - timedelta(days=1)
        add_entry("task_completed", "today task", day=today)
        add_entry("observation", "yesterday obs", day=yesterday)
        entries = get_recent_entries(days=2)
        assert len(entries) == 2

    def test_filter_by_type(self, tmp_journal):
        today = date.today()
        add_entry("task_completed", "task", day=today)
        add_entry("observation", "obs", day=today)
        tasks = get_recent_entries(days=1, entry_type="task_completed")
        assert len(tasks) == 1
        assert tasks[0]["type"] == "task_completed"

    def test_empty_days(self, tmp_journal):
        entries = get_recent_entries(days=5)
        assert entries == []

    def test_entries_tagged_with_date(self, tmp_journal):
        today = date.today()
        add_entry("observation", "test", day=today)
        entries = get_recent_entries(days=1)
        assert entries[0]["_date"] == today.strftime(_DATE_FMT)


class TestGetDaySummary:

    def test_empty_day(self, tmp_journal):
        result = get_day_summary(date(2026, 1, 1))
        assert "No journal entries" in result

    def test_summary_with_entries(self, tmp_journal):
        today = date.today()
        add_entry("task_completed", "built scheduler", day=today)
        add_entry("conversation", "talked about weather", day=today)
        result = get_day_summary(today)
        assert "1 task completed" in result
        assert "1 conversation" in result

    def test_summary_includes_recent_entries(self, tmp_journal):
        today = date.today()
        add_entry("observation", "code looks clean", day=today)
        result = get_day_summary(today)
        assert "code looks clean" in result


class TestGetOrientation:

    def test_empty_orientation(self, tmp_journal):
        result = get_orientation(days=3)
        assert "No recent journal entries" in result

    def test_orientation_with_data(self, tmp_journal):
        today = date.today()
        add_entry("task_completed", "finished scheduler", day=today)
        add_entry("observation", "codebase is well structured", day=today)
        result = get_orientation(days=1)
        assert "Today" in result
        assert "1 tasks" in result

    def test_orientation_multi_day(self, tmp_journal):
        today = date.today()
        yesterday = today - timedelta(days=1)
        add_entry("task_completed", "today work", day=today)
        add_entry("thing_learned", "learned something", day=yesterday)
        result = get_orientation(days=2)
        assert "Today" in result
        assert "Yesterday" in result


# ── Pruning ──────────────────────────────────────────────────────────

class TestPruning:

    def test_prune_removes_old_files(self, tmp_journal):
        old_date = date.today() - timedelta(days=60)
        path = tmp_journal / f"{old_date.strftime(_DATE_FMT)}.json"
        path.write_text("[]")
        removed = prune_old_journals(retention_days=30)
        assert removed == 1
        assert not path.exists()

    def test_prune_keeps_recent_files(self, tmp_journal):
        recent_date = date.today() - timedelta(days=5)
        path = tmp_journal / f"{recent_date.strftime(_DATE_FMT)}.json"
        path.write_text("[]")
        removed = prune_old_journals(retention_days=30)
        assert removed == 0
        assert path.exists()

    def test_prune_empty_directory(self, tmp_journal):
        removed = prune_old_journals()
        assert removed == 0

    def test_prune_ignores_non_json(self, tmp_journal):
        (tmp_journal / "notes.txt").write_text("not a journal")
        removed = prune_old_journals()
        assert removed == 0

    def test_prune_ignores_bad_filenames(self, tmp_journal):
        (tmp_journal / "not-a-date.json").write_text("[]")
        removed = prune_old_journals()
        assert removed == 0

    def test_prune_custom_retention(self, tmp_journal):
        old_date = date.today() - timedelta(days=10)
        path = tmp_journal / f"{old_date.strftime(_DATE_FMT)}.json"
        path.write_text("[]")
        # Default 30 days — should keep
        assert prune_old_journals(retention_days=30) == 0
        # 7 days — should remove
        assert prune_old_journals(retention_days=7) == 1


# ── Self-reflection (session 199) ──────────────────────────────────

class TestSelfReflection:

    def test_skips_when_too_few_entries(self, tmp_journal):
        from src.core.journal import generate_self_reflection
        add_entry("task_completed", "one task")
        result = generate_self_reflection(router=None, days=7)
        assert result is None  # Not enough entries

    def test_simple_reflection_without_model(self, tmp_journal):
        from src.core.journal import generate_self_reflection
        for i in range(8):
            add_entry("task_completed", f"Task {i}: did some work")
        result = generate_self_reflection(router=None, days=7)
        assert result is not None
        assert "Completed 8 tasks" in result
        # Should have stored a reflection entry
        entries = get_recent_entries(days=1, entry_type="reflection")
        assert len(entries) == 1

    def test_model_reflection_stores_entry(self, tmp_journal):
        from unittest.mock import MagicMock
        from src.core.journal import generate_self_reflection
        for i in range(6):
            add_entry("task_completed", f"Task {i}")

        mock_router = MagicMock()
        mock_router.generate.return_value = {"text": "I've been productive this week. Noticed I focus on error handling a lot."}

        with patch("src.core.journal._update_worldview_from_reflection"):
            result = generate_self_reflection(router=mock_router, days=7)

        assert result is not None
        assert "productive" in result
        entries = get_recent_entries(days=1, entry_type="reflection")
        assert len(entries) == 1
        assert entries[0]["content"] == result

    def test_model_reflection_handles_failure(self, tmp_journal):
        from unittest.mock import MagicMock
        from src.core.journal import generate_self_reflection
        for i in range(6):
            add_entry("task_completed", f"Task {i}")

        mock_router = MagicMock()
        mock_router.generate.side_effect = RuntimeError("API down")

        result = generate_self_reflection(router=mock_router, days=7)
        assert result is None

    def test_model_reflection_handles_empty_response(self, tmp_journal):
        from unittest.mock import MagicMock
        from src.core.journal import generate_self_reflection
        for i in range(6):
            add_entry("task_completed", f"Task {i}")

        mock_router = MagicMock()
        mock_router.generate.return_value = {"text": ""}

        result = generate_self_reflection(router=mock_router, days=7)
        assert result is None

    def test_simple_reflection_counts_types(self, tmp_journal):
        from src.core.journal import _simple_reflection
        entries = [
            {"type": "task_completed", "content": "work"},
            {"type": "task_completed", "content": "more work"},
            {"type": "conversation", "content": "chat"},
            {"type": "observation", "content": "noticed something"},
        ]
        result = _simple_reflection(entries)
        assert "2 tasks" in result
        assert "1 conversations" in result
        assert "1 observations" in result

    def test_worldview_update_from_reflection(self, tmp_journal):
        from unittest.mock import MagicMock
        from src.core.journal import _update_worldview_from_reflection

        mock_router = MagicMock()
        mock_router.generate.return_value = {"text": json.dumps({
            "opinions": [{"topic": "testing", "position": "More integration tests needed", "confidence": 0.6}],
            "interests": [{"topic": "observability", "curiosity_level": 0.7, "notes": "Keep seeing gaps"}],
        })}

        with patch("src.utils.parsing.extract_json", return_value={
            "opinions": [{"topic": "testing", "position": "More integration tests needed", "confidence": 0.6}],
            "interests": [{"topic": "observability", "curiosity_level": 0.7, "notes": "Keep seeing gaps"}],
        }):
            with patch("src.core.worldview.add_opinion") as mock_add_op, \
                 patch("src.core.worldview.add_interest") as mock_add_int:
                _update_worldview_from_reflection("I noticed testing gaps", router=mock_router)
                mock_add_op.assert_called_once()
                mock_add_int.assert_called_once()
