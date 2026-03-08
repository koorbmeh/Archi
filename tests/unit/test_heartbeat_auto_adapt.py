"""Tests for heartbeat _auto_adapt_generated_content integration.

Session 242: Wiring adapt_content into the content calendar heartbeat phase.
"""

import threading
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


def _make_heartbeat():
    """Build a minimal Heartbeat instance for testing."""
    with patch("src.core.heartbeat.Heartbeat.__init__", return_value=None):
        from src.core.heartbeat import Heartbeat
        hb = Heartbeat.__new__(Heartbeat)
        hb.stop_flag = threading.Event()
        hb._router = None
        hb._last_user_message_time = None
        return hb


def _make_mock_calendar(slots_data):
    """Build a mock ContentCalendar with given slot data."""
    cal = MagicMock()

    # Simulate _load_slots_raw not existing so fallback is used
    del cal._load_slots_raw

    # queue_content returns a mock slot with a slot_id
    def mock_queue(topic, platform, content_format="", publish_at=None, pillar=""):
        mock_slot = MagicMock()
        mock_slot.slot_id = f"cs_mock_{platform}"
        return mock_slot
    cal.queue_content.side_effect = mock_queue
    cal.mark_generated.return_value = True
    cal._update_slot.return_value = True

    return cal


class TestAutoAdaptGeneratedContent:
    """Test the _auto_adapt_generated_content method on Heartbeat."""

    def test_adapts_blog_content(self):
        """When a blog slot is generated, it should be adapted."""
        hb = _make_heartbeat()

        slots_data = {
            "slots": [{
                "slot_id": "cs_blog_001",
                "content_format": "blog",
                "status": "generated",
                "generated_content": "A" * 200,  # Long enough
                "topic": "AI trends 2026",
                "pillar": "ai_tech",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        cal = _make_mock_calendar(slots_data)

        mock_adapt_results = {
            "tweet": {
                "format": "tweet",
                "content": "AI is changing everything in 2026!",
                "topic": "AI trends 2026",
            },
            "instagram_post": {
                "format": "instagram_post",
                "content": "The future of AI looks incredible...",
                "topic": "AI trends 2026",
            },
            "facebook_post": {
                "format": "facebook_post",
                "content": "Here's what's happening in AI this year...",
                "topic": "AI trends 2026",
            },
        }

        mock_router = MagicMock()
        hb._get_router = MagicMock(return_value=mock_router)

        with patch("src.tools.content_calendar._load_calendar", return_value=slots_data), \
             patch("src.tools.content_creator.adapt_content", return_value=mock_adapt_results):
            hb._auto_adapt_generated_content(cal)

        # Should have queued 3 adapted versions
        assert cal.queue_content.call_count == 3
        assert cal.mark_generated.call_count == 3
        # Source slot should be marked as adapted
        cal._update_slot.assert_called_once_with("cs_blog_001", publish_result="adapted")

    def test_skips_already_adapted(self):
        """Slots already marked as adapted should be skipped."""
        hb = _make_heartbeat()

        slots_data = {
            "slots": [{
                "slot_id": "cs_blog_002",
                "content_format": "blog",
                "status": "generated",
                "generated_content": "B" * 200,
                "topic": "Old topic",
                "pillar": "",
                "publish_result": "adapted",  # Already adapted
            }],
            "last_plan_date": "",
        }

        cal = _make_mock_calendar(slots_data)
        hb._get_router = MagicMock()

        with patch("src.tools.content_calendar._load_calendar", return_value=slots_data):
            hb._auto_adapt_generated_content(cal)

        cal.queue_content.assert_not_called()

    def test_skips_non_blog_formats(self):
        """Only blog and video_script should trigger adaptation."""
        hb = _make_heartbeat()

        slots_data = {
            "slots": [{
                "slot_id": "cs_tweet_001",
                "content_format": "tweet",
                "status": "generated",
                "generated_content": "Short tweet content here",
                "topic": "Quick update",
                "pillar": "",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        cal = _make_mock_calendar(slots_data)
        hb._get_router = MagicMock()

        with patch("src.tools.content_calendar._load_calendar", return_value=slots_data):
            hb._auto_adapt_generated_content(cal)

        cal.queue_content.assert_not_called()

    def test_skips_short_content(self):
        """Content shorter than 100 chars should not be adapted."""
        hb = _make_heartbeat()

        slots_data = {
            "slots": [{
                "slot_id": "cs_blog_003",
                "content_format": "blog",
                "status": "generated",
                "generated_content": "Too short",
                "topic": "Brief",
                "pillar": "",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        cal = _make_mock_calendar(slots_data)
        hb._get_router = MagicMock()

        with patch("src.tools.content_calendar._load_calendar", return_value=slots_data):
            hb._auto_adapt_generated_content(cal)

        cal.queue_content.assert_not_called()

    def test_limit_one_adaptation_per_cycle(self):
        """Only one source slot should be adapted per cycle."""
        hb = _make_heartbeat()

        slots_data = {
            "slots": [
                {
                    "slot_id": "cs_blog_004",
                    "content_format": "blog",
                    "status": "generated",
                    "generated_content": "C" * 200,
                    "topic": "First blog",
                    "pillar": "",
                    "publish_result": "",
                },
                {
                    "slot_id": "cs_blog_005",
                    "content_format": "blog",
                    "status": "generated",
                    "generated_content": "D" * 200,
                    "topic": "Second blog",
                    "pillar": "",
                    "publish_result": "",
                },
            ],
            "last_plan_date": "",
        }

        cal = _make_mock_calendar(slots_data)

        mock_adapt_results = {
            "tweet": {"format": "tweet", "content": "Adapted tweet", "topic": "First blog"},
        }

        mock_router = MagicMock()
        hb._get_router = MagicMock(return_value=mock_router)

        with patch("src.tools.content_calendar._load_calendar", return_value=slots_data), \
             patch("src.tools.content_creator.adapt_content", return_value=mock_adapt_results):
            hb._auto_adapt_generated_content(cal)

        # Only the first blog should be adapted (limit 1 per cycle)
        cal._update_slot.assert_called_once_with("cs_blog_004", publish_result="adapted")

    def test_respects_stop_flag(self):
        """Should stop when stop_flag is set."""
        hb = _make_heartbeat()
        hb.stop_flag.set()  # Signal shutdown

        slots_data = {
            "slots": [{
                "slot_id": "cs_blog_006",
                "content_format": "blog",
                "status": "generated",
                "generated_content": "E" * 200,
                "topic": "Shouldn't adapt",
                "pillar": "",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        cal = _make_mock_calendar(slots_data)
        hb._get_router = MagicMock()

        with patch("src.tools.content_calendar._load_calendar", return_value=slots_data):
            hb._auto_adapt_generated_content(cal)

        cal.queue_content.assert_not_called()

    def test_handles_adapt_failure_gracefully(self):
        """If adapt_content raises, it should be caught."""
        hb = _make_heartbeat()

        slots_data = {
            "slots": [{
                "slot_id": "cs_blog_007",
                "content_format": "blog",
                "status": "generated",
                "generated_content": "F" * 200,
                "topic": "Error post",
                "pillar": "",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        cal = _make_mock_calendar(slots_data)
        mock_router = MagicMock()
        hb._get_router = MagicMock(return_value=mock_router)

        with patch("src.tools.content_calendar._load_calendar", return_value=slots_data), \
             patch("src.tools.content_creator.adapt_content", side_effect=RuntimeError("API down")):
            # Should not raise
            hb._auto_adapt_generated_content(cal)

        cal.queue_content.assert_not_called()

    def test_adapts_video_script(self):
        """video_script format should also trigger adaptation."""
        hb = _make_heartbeat()

        slots_data = {
            "slots": [{
                "slot_id": "cs_vid_001",
                "content_format": "video_script",
                "status": "generated",
                "generated_content": "G" * 200,
                "topic": "AI tutorial video",
                "pillar": "ai_tech",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        cal = _make_mock_calendar(slots_data)

        mock_adapt_results = {
            "tweet": {"format": "tweet", "content": "Check out my new video!", "topic": "AI tutorial"},
            "instagram_post": None,  # Failed
            "facebook_post": {"format": "facebook_post", "content": "New video!", "topic": "AI tutorial"},
        }

        mock_router = MagicMock()
        hb._get_router = MagicMock(return_value=mock_router)

        with patch("src.tools.content_calendar._load_calendar", return_value=slots_data), \
             patch("src.tools.content_creator.adapt_content", return_value=mock_adapt_results):
            hb._auto_adapt_generated_content(cal)

        # 2 successful (tweet + facebook), instagram failed (None)
        assert cal.queue_content.call_count == 2
        assert cal.mark_generated.call_count == 2

    def test_skips_planned_status(self):
        """Only 'generated' status should trigger adaptation."""
        hb = _make_heartbeat()

        slots_data = {
            "slots": [{
                "slot_id": "cs_blog_008",
                "content_format": "blog",
                "status": "planned",  # Not yet generated
                "generated_content": "",
                "topic": "Pending post",
                "pillar": "",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        cal = _make_mock_calendar(slots_data)
        hb._get_router = MagicMock()

        with patch("src.tools.content_calendar._load_calendar", return_value=slots_data):
            hb._auto_adapt_generated_content(cal)

        cal.queue_content.assert_not_called()
