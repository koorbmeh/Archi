"""Tests for heartbeat content calendar integration (Phase 0.99).

Session 241: Tests the _run_content_calendar_phase() method that auto-plans,
auto-generates, and auto-publishes content from the calendar.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

from src.core.heartbeat import Heartbeat


# ── Fixture ─────────────────────────────────────────────────────────


@pytest.fixture
def hb():
    """Heartbeat with mocked heavy deps."""
    with patch("src.core.heartbeat.MemoryManager"), \
         patch("src.core.heartbeat.reporting") as mock_reporting, \
         patch.object(Heartbeat, "_load_identity", return_value={}), \
         patch.object(Heartbeat, "_load_project_context", return_value={}), \
         patch.object(Heartbeat, "_load_prime_directive", return_value=""):
        mock_reporting.load_overnight_results.return_value = []
        cycle = Heartbeat(interval_seconds=60)
        cycle._memory_init_thread.join(timeout=2)
        # Mock the router so it doesn't try to init a real ModelRouter
        cycle._router = MagicMock()
        yield cycle
        cycle.stop_flag.set()


def _make_slot(**overrides):
    """Create a mock ContentSlot with defaults."""
    from src.tools.content_calendar import ContentSlot
    defaults = dict(
        slot_id="cs_test_001",
        pillar="ai_tech",
        platform="twitter",
        content_format="tweet",
        topic="Test topic about AI",
        status="planned",
        scheduled_at=(datetime.now() + timedelta(hours=2)).isoformat(),
        generated_content="",
        publish_result="",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    defaults.update(overrides)
    return ContentSlot(**defaults)


# ── Auto-plan tests ─────────────────────────────────────────────────


class TestContentCalendarAutoplan:

    @patch("src.tools.content_calendar.ContentCalendar")
    def test_auto_plans_when_queue_thin(self, MockCal, hb):
        """Should call plan_week() when needs_planning() returns True."""
        cal = MockCal.return_value
        cal.needs_planning.return_value = True
        cal.plan_week.return_value = [_make_slot()]
        cal.get_pending_generation.return_value = []
        cal.get_due_slots.return_value = []

        hb._run_content_calendar_phase()

        cal.needs_planning.assert_called_once()
        cal.plan_week.assert_called_once()

    @patch("src.tools.content_calendar.ContentCalendar")
    def test_no_plan_when_queue_sufficient(self, MockCal, hb):
        """Should NOT call plan_week() when queue is deep enough."""
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = []
        cal.get_due_slots.return_value = []

        hb._run_content_calendar_phase()

        cal.plan_week.assert_not_called()


# ── Auto-generate tests ─────────────────────────────────────────────


class TestContentCalendarAutoGenerate:

    @patch("src.tools.content_creator.generate_content")
    @patch("src.tools.content_calendar.ContentCalendar")
    def test_generates_pending_content(self, MockCal, mock_gen, hb):
        """Should generate content for pending slots."""
        slot = _make_slot(status="planned")
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = [slot]
        cal.get_due_slots.return_value = []
        mock_gen.return_value = {"content": "Generated tweet about AI"}

        hb._run_content_calendar_phase()

        mock_gen.assert_called_once_with(
            hb._get_router(),
            topic=slot.topic,
            content_format="tweet",
        )
        cal.mark_generated.assert_called_once_with(
            "cs_test_001", "Generated tweet about AI",
        )

    @patch("src.tools.content_creator.generate_content")
    @patch("src.tools.content_calendar.ContentCalendar")
    def test_marks_failed_on_empty_generation(self, MockCal, mock_gen, hb):
        """Should mark slot failed if generation returns empty."""
        slot = _make_slot(status="planned")
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = [slot]
        cal.get_due_slots.return_value = []
        mock_gen.return_value = None

        hb._run_content_calendar_phase()

        cal.mark_failed.assert_called_once()
        cal.mark_generated.assert_not_called()

    @patch("src.tools.content_creator.generate_content")
    @patch("src.tools.content_calendar.ContentCalendar")
    def test_marks_failed_on_generation_exception(self, MockCal, mock_gen, hb):
        """Should mark slot failed if generation throws."""
        slot = _make_slot(status="planned")
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = [slot]
        cal.get_due_slots.return_value = []
        mock_gen.side_effect = RuntimeError("API timeout")

        hb._run_content_calendar_phase()

        cal.mark_failed.assert_called_once()

    @patch("src.tools.content_creator.generate_content")
    @patch("src.tools.content_calendar.ContentCalendar")
    def test_respects_generation_limit(self, MockCal, mock_gen, hb):
        """Should only generate up to 3 items per cycle."""
        slots = [_make_slot(slot_id=f"cs_{i}") for i in range(5)]
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = slots
        cal.get_due_slots.return_value = []
        mock_gen.return_value = {"content": "Generated content"}

        hb._run_content_calendar_phase()

        # get_pending_generation is called with limit=3
        cal.get_pending_generation.assert_called_once_with(limit=3)

    @patch("src.tools.content_creator.generate_content")
    @patch("src.tools.content_calendar.ContentCalendar")
    def test_stops_generation_on_stop_flag(self, MockCal, mock_gen, hb):
        """Should stop generating when stop_flag is set."""
        slots = [_make_slot(slot_id=f"cs_{i}") for i in range(3)]
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = slots
        cal.get_due_slots.return_value = []

        # Set stop flag after first call
        call_count = 0
        def gen_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                hb.stop_flag.set()
            return {"content": "content"}
        mock_gen.side_effect = gen_side_effect

        hb._run_content_calendar_phase()

        # Should have generated only 1 (stop after first)
        assert mock_gen.call_count == 1
        hb.stop_flag.clear()  # reset for fixture cleanup


# ── Auto-publish tests ───────────────────────────────────────────────


class TestContentCalendarAutoPublish:

    @patch("src.interfaces.discord_bot.send_notification")
    @patch("src.interfaces.discord_bot.is_outbound_ready", return_value=True)
    @patch("src.tools.content_creator.publish_tweet")
    @patch("src.tools.content_calendar.ContentCalendar")
    def test_publishes_due_tweet(self, MockCal, mock_pub, _outbound, _notify, hb):
        """Should publish a generated tweet that's past its scheduled time."""
        slot = _make_slot(
            status="generated",
            content_format="tweet",
            generated_content="AI is changing the world!",
            scheduled_at=(datetime.now() - timedelta(minutes=5)).isoformat(),
        )
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = []
        cal.get_due_slots.return_value = [slot]
        mock_pub.return_value = {"url": "https://x.com/123"}
        # Not recently active
        hb.last_activity = datetime.now() - timedelta(minutes=10)

        hb._run_content_calendar_phase()

        mock_pub.assert_called_once_with("AI is changing the world!")
        cal.mark_published.assert_called_once()

    @patch("src.tools.content_calendar.ContentCalendar")
    def test_skips_publish_when_user_active(self, MockCal, hb):
        """Should NOT publish if user is mid-conversation."""
        slot = _make_slot(
            status="generated",
            content_format="tweet",
            generated_content="test",
            scheduled_at=(datetime.now() - timedelta(minutes=5)).isoformat(),
        )
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = []
        cal.get_due_slots.return_value = [slot]
        # Recently active
        hb.last_activity = datetime.now()

        hb._run_content_calendar_phase()

        cal.mark_published.assert_not_called()

    @patch("src.tools.content_creator.publish_to_github_blog")
    @patch("src.tools.content_calendar.ContentCalendar")
    def test_publishes_blog_post(self, MockCal, mock_pub, hb):
        """Should publish a blog post using the right publisher."""
        slot = _make_slot(
            status="generated",
            content_format="blog",
            generated_content="# My Blog Post\nContent here.",
            scheduled_at=(datetime.now() - timedelta(minutes=5)).isoformat(),
        )
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = []
        cal.get_due_slots.return_value = [slot]
        mock_pub.return_value = {"url": "https://blog.example.com/post"}
        hb.last_activity = datetime.now() - timedelta(minutes=10)

        hb._run_content_calendar_phase()

        mock_pub.assert_called_once_with("# My Blog Post\nContent here.")
        cal.mark_published.assert_called_once()

    @patch("src.tools.content_creator.publish_tweet")
    @patch("src.tools.content_calendar.ContentCalendar")
    def test_marks_failed_on_publish_error(self, MockCal, mock_pub, hb):
        """Should mark slot failed if publishing throws."""
        slot = _make_slot(
            status="generated",
            content_format="tweet",
            generated_content="test",
            scheduled_at=(datetime.now() - timedelta(minutes=5)).isoformat(),
        )
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = []
        cal.get_due_slots.return_value = [slot]
        mock_pub.side_effect = RuntimeError("Twitter API error")
        hb.last_activity = datetime.now() - timedelta(minutes=10)

        hb._run_content_calendar_phase()

        cal.mark_failed.assert_called_once()
        cal.mark_published.assert_not_called()

    @patch("src.tools.content_creator.publish_tweet")
    @patch("src.tools.content_calendar.ContentCalendar")
    def test_publish_limit_of_two_per_cycle(self, MockCal, mock_pub, hb):
        """Should publish at most 2 items per cycle."""
        slots = [
            _make_slot(
                slot_id=f"cs_{i}",
                status="generated",
                content_format="tweet",
                generated_content=f"content {i}",
                scheduled_at=(datetime.now() - timedelta(minutes=5)).isoformat(),
            )
            for i in range(5)
        ]
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = []
        cal.get_due_slots.return_value = slots
        mock_pub.return_value = {"url": "https://x.com/123"}
        hb.last_activity = datetime.now() - timedelta(minutes=10)

        hb._run_content_calendar_phase()

        assert mock_pub.call_count == 2
        assert cal.mark_published.call_count == 2


# ── Edge cases ───────────────────────────────────────────────────────


class TestContentCalendarEdgeCases:

    @patch("src.tools.content_calendar.ContentCalendar")
    def test_handles_import_error_gracefully(self, MockCal, hb):
        """Should not crash if content_calendar import fails."""
        MockCal.side_effect = ImportError("module not found")
        # Should not raise
        hb._run_content_calendar_phase()

    @patch("src.tools.content_calendar.ContentCalendar")
    def test_empty_queue_no_action(self, MockCal, hb):
        """Should do nothing with an empty calendar."""
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = []
        cal.get_due_slots.return_value = []

        hb._run_content_calendar_phase()

        cal.plan_week.assert_not_called()
        cal.mark_generated.assert_not_called()
        cal.mark_published.assert_not_called()

    @patch("src.tools.content_calendar.ContentCalendar")
    def test_unknown_format_skipped_in_publish(self, MockCal, hb):
        """Should skip slots with unknown content formats during publish."""
        slot = _make_slot(
            status="generated",
            content_format="tiktok_video",  # unknown format
            generated_content="test",
            scheduled_at=(datetime.now() - timedelta(minutes=5)).isoformat(),
        )
        cal = MockCal.return_value
        cal.needs_planning.return_value = False
        cal.get_pending_generation.return_value = []
        cal.get_due_slots.return_value = [slot]
        hb.last_activity = datetime.now() - timedelta(minutes=10)

        hb._run_content_calendar_phase()

        cal.mark_published.assert_not_called()
        cal.mark_failed.assert_not_called()
