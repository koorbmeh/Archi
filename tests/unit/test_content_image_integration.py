"""Tests for content image integration — heartbeat wiring + action handler.

Session 243: Wire image_generator into content calendar flow and register
as a Discord command (content_image action).
"""

import threading
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


# ── Heartbeat fixture ────────────────────────────────────────────────

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
    """Build a mock ContentCalendar with _update_slot support."""
    cal = MagicMock()
    cal._update_slot.return_value = True
    return cal


# ── Heartbeat image generation tests ─────────────────────────────────


class TestGenerateContentImages:
    """Test _generate_content_images in the heartbeat."""

    @patch("src.tools.content_calendar._load_calendar")
    @patch("src.tools.image_generator.is_available", return_value=True)
    @patch("src.tools.image_generator.generate_content_image")
    def test_generates_image_for_visual_slot(self, mock_gen, mock_avail, mock_load):
        """Should generate an image for a generated instagram_post slot."""
        hb = _make_heartbeat()
        cal = _make_mock_calendar(None)

        mock_load.return_value = {
            "slots": [{
                "slot_id": "cs_ig_001",
                "content_format": "instagram_post",
                "status": "generated",
                "generated_content": "Great post about AI!",
                "topic": "AI news today",
                "pillar": "ai_tech",
                "image_path": "",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        mock_gen.return_value = {
            "success": True,
            "image_path": "/workspace/images/content/test.png",
        }

        hb._generate_content_images(cal)

        mock_gen.assert_called_once_with(
            topic="AI news today",
            platform="instagram_post",
            pillar="ai_tech",
        )
        cal._update_slot.assert_called_once_with(
            "cs_ig_001", image_path="/workspace/images/content/test.png",
        )

    @patch("src.tools.content_calendar._load_calendar")
    @patch("src.tools.image_generator.is_available", return_value=True)
    @patch("src.tools.image_generator.generate_content_image")
    def test_skips_slot_with_existing_image(self, mock_gen, mock_avail, mock_load):
        """Should NOT regenerate if slot already has an image_path."""
        hb = _make_heartbeat()
        cal = _make_mock_calendar(None)

        mock_load.return_value = {
            "slots": [{
                "slot_id": "cs_ig_002",
                "content_format": "instagram_post",
                "status": "generated",
                "generated_content": "Post content",
                "topic": "AI",
                "pillar": "ai_tech",
                "image_path": "/already/exists.png",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        hb._generate_content_images(cal)

        mock_gen.assert_not_called()
        cal._update_slot.assert_not_called()

    @patch("src.tools.content_calendar._load_calendar")
    @patch("src.tools.image_generator.is_available", return_value=False)
    def test_skips_when_sdxl_unavailable(self, mock_avail, mock_load):
        """Should silently skip when SDXL is not available."""
        hb = _make_heartbeat()
        cal = _make_mock_calendar(None)

        hb._generate_content_images(cal)

        mock_load.assert_not_called()  # Should return before loading calendar

    @patch("src.tools.content_calendar._load_calendar")
    @patch("src.tools.image_generator.is_available", return_value=True)
    @patch("src.tools.image_generator.generate_content_image")
    def test_skips_non_visual_formats(self, mock_gen, mock_avail, mock_load):
        """Should NOT generate images for video_script or reddit formats."""
        hb = _make_heartbeat()
        cal = _make_mock_calendar(None)

        mock_load.return_value = {
            "slots": [{
                "slot_id": "cs_vid_001",
                "content_format": "video_script",
                "status": "generated",
                "generated_content": "Script content",
                "topic": "Python tips",
                "pillar": "ai_tech",
                "image_path": "",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        hb._generate_content_images(cal)

        mock_gen.assert_not_called()

    @patch("src.tools.content_calendar._load_calendar")
    @patch("src.tools.image_generator.is_available", return_value=True)
    @patch("src.tools.image_generator.generate_content_image")
    def test_limits_to_one_image_per_cycle(self, mock_gen, mock_avail, mock_load):
        """Should only generate 1 image per cycle to avoid long cycles."""
        hb = _make_heartbeat()
        cal = _make_mock_calendar(None)

        mock_load.return_value = {
            "slots": [
                {
                    "slot_id": "cs_ig_a",
                    "content_format": "instagram_post",
                    "status": "generated",
                    "generated_content": "Post A",
                    "topic": "Topic A",
                    "pillar": "ai_tech",
                    "image_path": "",
                    "publish_result": "",
                },
                {
                    "slot_id": "cs_ig_b",
                    "content_format": "blog",
                    "status": "generated",
                    "generated_content": "Post B",
                    "topic": "Topic B",
                    "pillar": "finance",
                    "image_path": "",
                    "publish_result": "",
                },
            ],
            "last_plan_date": "",
        }

        mock_gen.return_value = {
            "success": True,
            "image_path": "/workspace/images/content/a.png",
        }

        hb._generate_content_images(cal)

        assert mock_gen.call_count == 1  # Only first slot processed

    @patch("src.tools.content_calendar._load_calendar")
    @patch("src.tools.image_generator.is_available", return_value=True)
    @patch("src.tools.image_generator.generate_content_image")
    def test_maps_tweet_to_twitter_platform(self, mock_gen, mock_avail, mock_load):
        """tweet content format should map to 'twitter' image platform."""
        hb = _make_heartbeat()
        cal = _make_mock_calendar(None)

        mock_load.return_value = {
            "slots": [{
                "slot_id": "cs_tw_001",
                "content_format": "tweet",
                "status": "generated",
                "generated_content": "A tweet about AI",
                "topic": "AI update",
                "pillar": "",
                "image_path": "",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        mock_gen.return_value = {"success": True, "image_path": "/img.png"}

        hb._generate_content_images(cal)

        mock_gen.assert_called_once_with(
            topic="AI update", platform="twitter", pillar="",
        )

    @patch("src.tools.content_calendar._load_calendar")
    @patch("src.tools.image_generator.is_available", return_value=True)
    @patch("src.tools.image_generator.generate_content_image")
    def test_handles_generation_failure_gracefully(self, mock_gen, mock_avail, mock_load):
        """Should not crash or update slot when image gen fails."""
        hb = _make_heartbeat()
        cal = _make_mock_calendar(None)

        mock_load.return_value = {
            "slots": [{
                "slot_id": "cs_fail_001",
                "content_format": "blog",
                "status": "generated",
                "generated_content": "Blog content",
                "topic": "Failing topic",
                "pillar": "finance",
                "image_path": "",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        mock_gen.return_value = {"success": False, "error": "GPU out of memory"}

        hb._generate_content_images(cal)

        mock_gen.assert_called_once()
        cal._update_slot.assert_not_called()  # No update on failure

    @patch("src.tools.content_calendar._load_calendar")
    @patch("src.tools.image_generator.is_available", return_value=True)
    @patch("src.tools.image_generator.generate_content_image")
    def test_skips_planned_slots(self, mock_gen, mock_avail, mock_load):
        """Should only process 'generated' status, not 'planned'."""
        hb = _make_heartbeat()
        cal = _make_mock_calendar(None)

        mock_load.return_value = {
            "slots": [{
                "slot_id": "cs_planned",
                "content_format": "instagram_post",
                "status": "planned",
                "generated_content": "",
                "topic": "Planned topic",
                "pillar": "ai_tech",
                "image_path": "",
                "publish_result": "",
            }],
            "last_plan_date": "",
        }

        hb._generate_content_images(cal)

        mock_gen.assert_not_called()


# ── Action dispatcher handler tests ──────────────────────────────────


class TestContentImageHandler:
    """Test _handle_content_image action handler."""

    @patch("src.tools.image_generator.is_available", return_value=False)
    def test_unavailable_returns_message(self, mock_avail):
        """Should return informative error when SDXL unavailable."""
        from src.interfaces.action_dispatcher import _handle_content_image
        resp, actions, cost = _handle_content_image(
            {"topic": "AI"}, {"effective_message": ""},
        )
        assert "not available" in resp.lower() or "no SDXL" in resp

    @patch("src.tools.image_generator.is_available", return_value=True)
    @patch("src.tools.image_generator.generate_content_image")
    def test_generates_image_successfully(self, mock_gen, mock_avail):
        """Should call generate_content_image and return path."""
        from src.interfaces.action_dispatcher import _handle_content_image
        mock_gen.return_value = {
            "success": True,
            "image_path": "/workspace/images/content/test.png",
            "duration_ms": 5000,
        }
        resp, actions, cost = _handle_content_image(
            {"topic": "AI news", "platform": "instagram_post"},
            {"effective_message": "generate image about AI"},
        )
        assert "Image generated" in resp
        assert "/workspace/images/content/test.png" in resp
        mock_gen.assert_called_once_with(
            topic="AI news", platform="instagram_post", pillar="", overlay_text="",
        )

    @patch("src.tools.image_generator.is_available", return_value=True)
    @patch("src.tools.image_generator.generate_content_image")
    def test_handles_failure(self, mock_gen, mock_avail):
        """Should return error message on generation failure."""
        from src.interfaces.action_dispatcher import _handle_content_image
        mock_gen.return_value = {
            "success": False,
            "error": "No GPU available",
        }
        resp, actions, cost = _handle_content_image(
            {"topic": "test"}, {"effective_message": ""},
        )
        assert "failed" in resp.lower() or "No GPU" in resp

    @patch("src.tools.image_generator.is_available", return_value=True)
    def test_no_topic_prompts_user(self, mock_avail):
        """Should ask for a topic when none provided."""
        from src.interfaces.action_dispatcher import _handle_content_image
        resp, actions, cost = _handle_content_image(
            {}, {"effective_message": ""},
        )
        assert "what" in resp.lower()

    @patch("src.tools.image_generator.is_available", return_value=True)
    @patch("src.tools.image_generator.generate_content_image")
    def test_passes_overlay_text(self, mock_gen, mock_avail):
        """Should pass overlay_text param to generator."""
        from src.interfaces.action_dispatcher import _handle_content_image
        mock_gen.return_value = {
            "success": True,
            "image_path": "/img.png",
            "duration_ms": 1000,
        }
        _handle_content_image(
            {"topic": "AI", "overlay_text": "Breaking News"},
            {"effective_message": ""},
        )
        mock_gen.assert_called_once_with(
            topic="AI", platform="default", pillar="", overlay_text="Breaking News",
        )


# ── ContentSlot image_path field tests ───────────────────────────────


class TestContentSlotImagePath:
    """Test that ContentSlot correctly handles the image_path field."""

    def test_image_path_in_to_dict(self):
        from src.tools.content_calendar import ContentSlot
        slot = ContentSlot(
            slot_id="cs_test",
            image_path="/path/to/image.png",
        )
        d = slot.to_dict()
        assert d["image_path"] == "/path/to/image.png"

    def test_image_path_from_dict(self):
        from src.tools.content_calendar import ContentSlot
        d = {"slot_id": "cs_test", "image_path": "/path/to/image.png"}
        slot = ContentSlot.from_dict(d)
        assert slot.image_path == "/path/to/image.png"

    def test_image_path_defaults_empty(self):
        from src.tools.content_calendar import ContentSlot
        slot = ContentSlot.from_dict({"slot_id": "cs_test"})
        assert slot.image_path == ""

    def test_roundtrip_preserves_image_path(self):
        from src.tools.content_calendar import ContentSlot
        slot = ContentSlot(
            slot_id="cs_rt",
            image_path="/images/content/test.png",
            topic="Test",
            platform="instagram",
        )
        d = slot.to_dict()
        restored = ContentSlot.from_dict(d)
        assert restored.image_path == slot.image_path
        assert restored.slot_id == slot.slot_id
