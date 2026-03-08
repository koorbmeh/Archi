"""Tests for Content Calendar (content strategy Phase 4).

Tests planning, queuing, scheduling, status tracking, and formatting.
"""

import json
import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from src.tools.content_calendar import (
    ContentSlot,
    ContentCalendar,
    format_week_plan,
    format_upcoming,
    _pick_pillar,
    _pick_topic_from_pillar,
    _generate_slot_id,
)


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def tmp_calendar(tmp_path):
    """Patch the calendar path to a temp file."""
    cal_path = str(tmp_path / "content_calendar.json")
    with patch("src.tools.content_calendar._CALENDAR_PATH", cal_path):
        yield cal_path


@pytest.fixture
def mock_brand():
    """Mock brand config with pillars."""
    pillars = [
        {"id": "ai_tech", "name": "AI & Tech", "angles": ["hype vs reality", "tool reviews"], "platforms": ["blog", "twitter"]},
        {"id": "finance", "name": "Finance", "angles": ["wealth building", "crypto"], "platforms": ["blog", "twitter"]},
        {"id": "health", "name": "Health", "angles": ["supplements", "sleep"], "platforms": ["instagram", "blog"]},
    ]
    brand = {"topic_pillars": pillars}
    with patch("src.tools.content_calendar._get_pillars", return_value=pillars):
        yield brand


# ── ContentSlot tests ────────────────────────────────────────────────

class TestContentSlot:

    def test_to_dict_round_trip(self):
        slot = ContentSlot(
            slot_id="cs_test_001",
            pillar="ai_tech",
            platform="twitter",
            content_format="tweet",
            topic="AI trends",
            status="planned",
            scheduled_at="2026-03-08T10:00:00",
            created_at="2026-03-07T12:00:00",
            updated_at="2026-03-07T12:00:00",
        )
        d = slot.to_dict()
        rebuilt = ContentSlot.from_dict(d)
        assert rebuilt.slot_id == "cs_test_001"
        assert rebuilt.pillar == "ai_tech"
        assert rebuilt.platform == "twitter"
        assert rebuilt.topic == "AI trends"

    def test_from_dict_defaults(self):
        slot = ContentSlot.from_dict({})
        assert slot.status == "planned"
        assert slot.slot_id == ""

    def test_is_due_generated_past(self):
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        slot = ContentSlot(status="generated", scheduled_at=past)
        assert slot.is_due is True

    def test_is_due_planned_not_ready(self):
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        slot = ContentSlot(status="planned", scheduled_at=past)
        assert slot.is_due is False

    def test_is_due_future(self):
        future = (datetime.now() + timedelta(hours=2)).isoformat()
        slot = ContentSlot(status="generated", scheduled_at=future)
        assert slot.is_due is False


# ── Helper tests ─────────────────────────────────────────────────────

class TestHelpers:

    def test_generate_slot_id_unique(self):
        ids = {_generate_slot_id() for _ in range(20)}
        assert len(ids) >= 15  # Some might collide on same-second, but mostly unique

    def test_pick_pillar_diversity(self):
        pillars = [
            {"id": "a", "name": "A"},
            {"id": "b", "name": "B"},
            {"id": "c", "name": "C"},
        ]
        result = _pick_pillar(["a", "b"], pillars)
        assert result["id"] == "c"

    def test_pick_pillar_wraps_when_all_recent(self):
        pillars = [{"id": "a", "name": "A"}]
        result = _pick_pillar(["a", "a"], pillars)
        assert result["id"] == "a"  # Only option

    def test_pick_topic_from_pillar(self):
        pillar = {"name": "AI & Tech", "angles": ["hype vs reality"]}
        topic = _pick_topic_from_pillar(pillar)
        assert "AI & Tech" in topic

    def test_pick_topic_no_angles(self):
        pillar = {"name": "General", "angles": []}
        topic = _pick_topic_from_pillar(pillar)
        assert "General" in topic


# ── Calendar core tests ──────────────────────────────────────────────

class TestContentCalendar:

    def test_plan_week(self, tmp_calendar, mock_brand):
        cal = ContentCalendar()
        slots = cal.plan_week()
        assert len(slots) > 20  # Should produce many slots across platforms
        # All planned status
        assert all(s.status == "planned" for s in slots)
        # Covers multiple platforms
        platforms = {s.platform for s in slots}
        assert len(platforms) >= 4
        # Persisted to disk
        assert os.path.isfile(tmp_calendar)

    def test_plan_week_pillar_diversity(self, tmp_calendar, mock_brand):
        cal = ContentCalendar()
        slots = cal.plan_week()
        pillars = [s.pillar for s in slots]
        unique = set(pillars)
        assert len(unique) >= 2  # At least 2 different pillars

    def test_queue_content(self, tmp_calendar):
        cal = ContentCalendar()
        slot = cal.queue_content(
            topic="Test topic about AI",
            platform="twitter",
            content_format="tweet",
        )
        assert slot is not None
        assert slot.topic == "Test topic about AI"
        assert slot.platform == "twitter"
        assert slot.status == "planned"
        assert slot.slot_id.startswith("cs_")

    def test_queue_content_auto_format(self, tmp_calendar):
        cal = ContentCalendar()
        slot = cal.queue_content(topic="Test", platform="blog")
        assert slot is not None
        assert slot.content_format == "blog"

    def test_get_upcoming(self, tmp_calendar, mock_brand):
        cal = ContentCalendar()
        cal.plan_week()
        upcoming = cal.get_upcoming(days=7)
        assert len(upcoming) > 0
        # Sorted by scheduled_at
        for i in range(len(upcoming) - 1):
            assert upcoming[i].scheduled_at <= upcoming[i + 1].scheduled_at

    def test_get_upcoming_empty(self, tmp_calendar):
        cal = ContentCalendar()
        upcoming = cal.get_upcoming()
        assert upcoming == []

    def test_mark_generated(self, tmp_calendar):
        cal = ContentCalendar()
        slot = cal.queue_content(topic="Test", platform="twitter")
        assert cal.mark_generated(slot.slot_id, "Generated tweet text") is True
        # Verify status updated
        upcoming = cal.get_upcoming(days=30)
        found = [s for s in upcoming if s.slot_id == slot.slot_id]
        assert len(found) == 1
        assert found[0].status == "generated"
        assert found[0].generated_content == "Generated tweet text"

    def test_mark_published(self, tmp_calendar):
        cal = ContentCalendar()
        slot = cal.queue_content(topic="Test", platform="twitter")
        cal.mark_generated(slot.slot_id, "content")
        assert cal.mark_published(slot.slot_id, "https://twitter.com/123") is True

    def test_mark_failed(self, tmp_calendar):
        cal = ContentCalendar()
        slot = cal.queue_content(topic="Test", platform="twitter")
        assert cal.mark_failed(slot.slot_id, "API error") is True

    def test_get_due_slots(self, tmp_calendar):
        cal = ContentCalendar()
        # Queue something in the past
        past = datetime.now() - timedelta(hours=1)
        slot = cal.queue_content(topic="Past post", platform="twitter",
                                 publish_at=past)
        cal.mark_generated(slot.slot_id, "content text")
        due = cal.get_due_slots()
        assert len(due) == 1
        assert due[0].slot_id == slot.slot_id

    def test_get_due_slots_ignores_planned(self, tmp_calendar):
        cal = ContentCalendar()
        past = datetime.now() - timedelta(hours=1)
        cal.queue_content(topic="Past post", platform="twitter",
                          publish_at=past)
        # Not generated yet → not due
        due = cal.get_due_slots()
        assert len(due) == 0

    def test_get_pending_generation(self, tmp_calendar):
        cal = ContentCalendar()
        # Queue something due soon
        soon = datetime.now() + timedelta(hours=6)
        cal.queue_content(topic="Soon post", platform="twitter",
                          publish_at=soon)
        # Queue something far in the future
        far = datetime.now() + timedelta(days=5)
        cal.queue_content(topic="Far post", platform="twitter",
                          publish_at=far)
        pending = cal.get_pending_generation()
        assert len(pending) == 1
        assert pending[0].topic == "Soon post"

    def test_queue_depth_days(self, tmp_calendar, mock_brand):
        cal = ContentCalendar()
        cal.plan_week()
        depth = cal.queue_depth_days()
        assert depth >= 5  # Should have content across most of the week

    def test_queue_depth_empty(self, tmp_calendar):
        cal = ContentCalendar()
        assert cal.queue_depth_days() == 0.0

    def test_needs_planning_empty(self, tmp_calendar):
        cal = ContentCalendar()
        assert cal.needs_planning() is True

    def test_needs_planning_full(self, tmp_calendar, mock_brand):
        cal = ContentCalendar()
        cal.plan_week()
        assert cal.needs_planning() is False

    def test_stats(self, tmp_calendar, mock_brand):
        cal = ContentCalendar()
        cal.plan_week()
        stats = cal.get_stats()
        assert stats["total_slots"] > 0
        assert "planned" in stats["by_status"]
        assert stats["queue_depth_days"] > 0

    def test_update_slot_missing(self, tmp_calendar):
        cal = ContentCalendar()
        assert cal._update_slot("nonexistent", status="failed") is False


# ── Formatting tests ─────────────────────────────────────────────────

class TestFormatting:

    def test_format_week_plan(self):
        slots = [
            ContentSlot(platform="twitter", scheduled_at="2026-03-08T10:00:00"),
            ContentSlot(platform="twitter", scheduled_at="2026-03-08T14:00:00"),
            ContentSlot(platform="blog", scheduled_at="2026-03-09T09:00:00"),
        ]
        result = format_week_plan(slots)
        assert "Content Calendar" in result
        assert "twitter" in result
        assert "blog" in result
        assert "3 posts" in result

    def test_format_week_plan_empty(self):
        result = format_week_plan([])
        assert "No content planned" in result

    def test_format_upcoming(self):
        slots = [
            ContentSlot(
                platform="twitter",
                topic="AI tools review",
                status="planned",
                scheduled_at="2026-03-08T10:00:00",
            ),
            ContentSlot(
                platform="blog",
                topic="Deep dive on transformers",
                status="generated",
                scheduled_at="2026-03-09T09:00:00",
            ),
        ]
        result = format_upcoming(slots)
        assert "Upcoming Content" in result
        assert "twitter" in result
        assert "AI tools" in result

    def test_format_upcoming_empty(self):
        result = format_upcoming([])
        assert "Nothing scheduled" in result

    def test_format_upcoming_limit(self):
        slots = [
            ContentSlot(platform="twitter", topic=f"Post {i}",
                        status="planned", scheduled_at=f"2026-03-{8+i:02d}T10:00:00")
            for i in range(15)
        ]
        result = format_upcoming(slots, limit=5)
        assert "...and 10 more" in result
