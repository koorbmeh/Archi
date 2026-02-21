"""
Unit tests for IdeaHistory — persistent idea ledger.
Session 63.
"""

import json
import pytest
from pathlib import Path

from src.core.idea_history import (
    IdeaHistory,
    STATUS_AUTO_FILTERED,
    STATUS_PRESENTED,
    STATUS_ACCEPTED,
    STATUS_USER_REJECTED,
    STATUS_IGNORED,
    _text_similar,
)


@pytest.fixture
def tmp_history(tmp_path):
    """Create an IdeaHistory backed by a temp directory."""
    return IdeaHistory(data_dir=tmp_path)


# ── Text similarity ─────────────────────────────────────────────────

class TestTextSimilar:
    def test_identical(self):
        assert _text_similar("build a step counter", "build a step counter")

    def test_similar(self):
        assert _text_similar(
            "Python script for tracking daily step counts",
            "Python script for tracking and visualizing daily step counts",
        )

    def test_different(self):
        assert not _text_similar(
            "build a step counter CLI",
            "implement OAuth authentication flow",
        )

    def test_empty(self):
        assert not _text_similar("", "something")
        assert not _text_similar("something", "")


# ── Recording ────────────────────────────────────────────────────────

class TestRecording:
    def test_record_auto_filtered(self, tmp_history):
        tmp_history.record_auto_filtered("step counter", "not relevant", "Health")
        assert tmp_history.total_ideas == 1
        assert tmp_history.stats[STATUS_AUTO_FILTERED] == 1

    def test_record_presented(self, tmp_history):
        batch_id = tmp_history.record_presented(["idea A", "idea B", "idea C"])
        assert batch_id is not None
        assert tmp_history.total_ideas == 3
        assert tmp_history.stats[STATUS_PRESENTED] == 3

    def test_record_accepted(self, tmp_history):
        tmp_history.record_presented(["build a CLI tool"])
        tmp_history.record_accepted("build a CLI tool")
        assert tmp_history.stats[STATUS_ACCEPTED] == 1
        # Presented count should be 0 now (upgraded to accepted)
        assert tmp_history.stats.get(STATUS_PRESENTED, 0) == 0

    def test_record_accepted_no_prior_presented(self, tmp_history):
        """Accepting an idea that wasn't in history creates a fresh entry."""
        tmp_history.record_accepted("spontaneous idea")
        assert tmp_history.total_ideas == 1
        assert tmp_history.stats[STATUS_ACCEPTED] == 1

    def test_record_user_rejected(self, tmp_history):
        tmp_history.record_presented(["sleep tracker"])
        tmp_history.record_user_rejected("sleep tracker", "not interested")
        assert tmp_history.stats[STATUS_USER_REJECTED] == 1

    def test_mark_batch_ignored(self, tmp_history):
        batch_id = tmp_history.record_presented(["idea X", "idea Y"])
        count = tmp_history.mark_batch_ignored(batch_id)
        assert count == 2
        assert tmp_history.stats[STATUS_IGNORED] == 2

    def test_mark_batch_ignores_only_presented(self, tmp_history):
        """Already-accepted ideas in a batch shouldn't be marked ignored."""
        batch_id = tmp_history.record_presented(["idea A", "idea B"])
        tmp_history.record_accepted("idea A")
        count = tmp_history.mark_batch_ignored(batch_id)
        assert count == 1  # Only idea B
        assert tmp_history.stats[STATUS_ACCEPTED] == 1
        assert tmp_history.stats[STATUS_IGNORED] == 1


# ── Persistence ──────────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_reload(self, tmp_path):
        h1 = IdeaHistory(data_dir=tmp_path)
        h1.record_auto_filtered("test idea", "not relevant")
        h1.record_presented(["idea 2"])

        h2 = IdeaHistory(data_dir=tmp_path)
        assert h2.total_ideas == 2

    def test_file_format(self, tmp_path):
        h = IdeaHistory(data_dir=tmp_path)
        h.record_auto_filtered("test", "reason")
        data = json.loads((tmp_path / "idea_history.json").read_text())
        assert data["version"] == 1
        assert len(data["ideas"]) == 1
        assert data["ideas"][0]["status"] == STATUS_AUTO_FILTERED


# ── Querying ─────────────────────────────────────────────────────────

class TestQuerying:
    def test_is_stale_returns_match(self, tmp_history):
        tmp_history.record_auto_filtered("build a step counter tracking script", "not relevant")
        match = tmp_history.is_stale("build a step counter tracking tool")
        assert match is not None
        assert match["status"] == STATUS_AUTO_FILTERED

    def test_is_stale_ignores_accepted(self, tmp_history):
        tmp_history.record_presented(["build a step counter"])
        tmp_history.record_accepted("build a step counter")
        assert tmp_history.is_stale("build a step counter") is None

    def test_is_stale_returns_none_for_fresh(self, tmp_history):
        tmp_history.record_auto_filtered("step counter", "not relevant")
        assert tmp_history.is_stale("implement OAuth flow") is None

    def test_times_rejected(self, tmp_history):
        tmp_history.record_auto_filtered("build a daily step counter tracker", "not relevant")
        tmp_history.record_auto_filtered("build a daily step counter tool", "not purpose-driven")
        tmp_history.record_presented(["build a daily step counter script"])
        tmp_history.mark_batch_ignored(
            tmp_history._ideas[-1].get("batch_id", "")
        )
        # Similar descriptions — should count as 3 rejections
        assert tmp_history.times_rejected("build a daily step counter app") == 3

    def test_get_rejection_context_empty(self, tmp_history):
        assert tmp_history.get_rejection_context() == ""

    def test_get_rejection_context_with_rejections(self, tmp_history):
        tmp_history.record_auto_filtered("interactive sleep tracker logging tool", "not relevant")
        tmp_history.record_auto_filtered("interactive sleep tracker logging CLI", "not purpose-driven")
        ctx = tmp_history.get_rejection_context()
        assert "Previously rejected ideas" in ctx
        assert "sleep tracker" in ctx.lower()
        assert "rejected 2x" in ctx

    def test_get_accepted_context(self, tmp_history):
        tmp_history.record_presented(["build auth module"])
        tmp_history.record_accepted("build auth module")
        ctx = tmp_history.get_accepted_context()
        assert "Previously accepted" in ctx
        assert "auth module" in ctx.lower()

    def test_get_accepted_context_empty(self, tmp_history):
        assert tmp_history.get_accepted_context() == ""
