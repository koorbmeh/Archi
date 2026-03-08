"""Tests for src/tools/image_generator.py — visual content pipeline.

Session 242: Content Strategy Phase 2.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Prompt building ──────────────────────────────────────────────────

class TestBuildImagePrompt:
    def test_basic_topic(self):
        from src.tools.image_generator import _build_image_prompt
        prompt = _build_image_prompt("AI trends in 2026")
        assert "AI trends in 2026" in prompt
        assert "modern digital art" in prompt

    def test_platform_hint_included(self):
        from src.tools.image_generator import _build_image_prompt
        prompt = _build_image_prompt("tech news", platform="instagram_post")
        assert "vibrant" in prompt or "eye-catching" in prompt

    def test_pillar_style_included(self):
        from src.tools.image_generator import _build_image_prompt
        prompt = _build_image_prompt("new AI model", pillar="ai_tech")
        assert "futuristic" in prompt or "neon" in prompt

    def test_custom_style(self):
        from src.tools.image_generator import _build_image_prompt
        prompt = _build_image_prompt("sunset", style="oil painting")
        assert "oil painting" in prompt

    def test_truncation_long_topic(self):
        from src.tools.image_generator import _build_image_prompt
        long_topic = "A" * 500
        prompt = _build_image_prompt(long_topic)
        assert len(prompt) <= 303  # 300 + "..."

    def test_default_platform(self):
        from src.tools.image_generator import _build_image_prompt
        prompt = _build_image_prompt("test topic", platform="default")
        assert "test topic" in prompt

    def test_twitter_alias(self):
        from src.tools.image_generator import _build_image_prompt
        p1 = _build_image_prompt("test", platform="twitter")
        p2 = _build_image_prompt("test", platform="tweet")
        # Both should include the same platform hint
        assert "professional" in p1
        assert "professional" in p2


# ── Platform dimensions ──────────────────────────────────────────────

class TestPlatformDimensions:
    def test_all_platforms_have_dimensions(self):
        from src.tools.image_generator import PLATFORM_DIMENSIONS
        expected = [
            "instagram_post", "instagram_story", "twitter", "tweet",
            "facebook_post", "blog", "youtube", "reddit", "default",
        ]
        for p in expected:
            assert p in PLATFORM_DIMENSIONS, f"Missing dimension for {p}"
            w, h = PLATFORM_DIMENSIONS[p]
            assert w > 0 and h > 0

    def test_instagram_is_square(self):
        from src.tools.image_generator import PLATFORM_DIMENSIONS
        w, h = PLATFORM_DIMENSIONS["instagram_post"]
        assert w == h == 1080


# ── Text overlay ─────────────────────────────────────────────────────

class TestTextOverlay:
    def _create_test_image(self, tmp_dir, w=200, h=200):
        """Create a small test image."""
        from PIL import Image
        img = Image.new("RGB", (w, h), color="blue")
        path = os.path.join(tmp_dir, "test_img.png")
        img.save(path)
        return path

    def test_overlay_creates_new_file(self):
        from src.tools.image_generator import add_text_overlay
        with tempfile.TemporaryDirectory() as tmp:
            path = self._create_test_image(tmp)
            result = add_text_overlay(path, "Hello World")
            assert result != path
            assert "_overlay" in result
            assert os.path.exists(result)

    def test_overlay_bottom_position(self):
        from src.tools.image_generator import add_text_overlay
        with tempfile.TemporaryDirectory() as tmp:
            path = self._create_test_image(tmp)
            result = add_text_overlay(path, "Test", position="bottom")
            assert os.path.exists(result)

    def test_overlay_top_position(self):
        from src.tools.image_generator import add_text_overlay
        with tempfile.TemporaryDirectory() as tmp:
            path = self._create_test_image(tmp)
            result = add_text_overlay(path, "Test", position="top")
            assert os.path.exists(result)

    def test_overlay_center_position(self):
        from src.tools.image_generator import add_text_overlay
        with tempfile.TemporaryDirectory() as tmp:
            path = self._create_test_image(tmp)
            result = add_text_overlay(path, "Test", position="center")
            assert os.path.exists(result)

    def test_overlay_long_text_wraps(self):
        from src.tools.image_generator import add_text_overlay
        with tempfile.TemporaryDirectory() as tmp:
            path = self._create_test_image(tmp, w=100, h=100)
            long_text = "This is a very long text that should wrap across multiple lines"
            result = add_text_overlay(path, long_text)
            assert os.path.exists(result)


# ── Resize for platform ──────────────────────────────────────────────

class TestResizeForPlatform:
    def _create_test_image(self, tmp_dir, w=1024, h=1024):
        from PIL import Image
        img = Image.new("RGB", (w, h), color="green")
        path = os.path.join(tmp_dir, "test_resize.png")
        img.save(path)
        return path

    def test_resize_twitter(self):
        from src.tools.image_generator import resize_for_platform
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            path = self._create_test_image(tmp)
            result = resize_for_platform(path, "twitter")
            img = Image.open(result)
            assert img.size == (1200, 675)

    def test_resize_instagram(self):
        from src.tools.image_generator import resize_for_platform
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            path = self._create_test_image(tmp)
            result = resize_for_platform(path, "instagram_post")
            img = Image.open(result)
            assert img.size == (1080, 1080)

    def test_resize_already_correct_size(self):
        from src.tools.image_generator import resize_for_platform
        with tempfile.TemporaryDirectory() as tmp:
            path = self._create_test_image(tmp, w=1024, h=1024)
            result = resize_for_platform(path, "default")
            assert result == path  # No resize needed

    def test_resize_creates_new_file(self):
        from src.tools.image_generator import resize_for_platform
        with tempfile.TemporaryDirectory() as tmp:
            path = self._create_test_image(tmp)
            result = resize_for_platform(path, "blog")
            assert result != path
            assert "_blog" in result


# ── Color parsing ────────────────────────────────────────────────────

class TestParseColor:
    def test_named_colors(self):
        from src.tools.image_generator import _parse_color
        assert _parse_color("black", 255) == (0, 0, 0, 255)
        assert _parse_color("white", 200) == (255, 255, 255, 200)
        assert _parse_color("red", 128) == (255, 0, 0, 128)

    def test_unknown_color_defaults_black(self):
        from src.tools.image_generator import _parse_color
        assert _parse_color("fuchsia", 100) == (0, 0, 0, 100)

    def test_opacity_clamped(self):
        from src.tools.image_generator import _parse_color
        r = _parse_color("white", 999)
        assert r[3] == 255
        r = _parse_color("white", -50)
        assert r[3] == 0


# ── Text wrapping ────────────────────────────────────────────────────

class TestWrapText:
    def test_short_text_single_line(self):
        from src.tools.image_generator import _wrap_text
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (500, 100))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        lines = _wrap_text("short text", font, 500, draw)
        assert len(lines) == 1
        assert lines[0] == "short text"

    def test_long_text_wraps(self):
        from src.tools.image_generator import _wrap_text
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (100, 100))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        text = "This is a much longer text that definitely needs wrapping"
        lines = _wrap_text(text, font, 100, draw)
        assert len(lines) > 1

    def test_empty_text(self):
        from src.tools.image_generator import _wrap_text
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (100, 100))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        lines = _wrap_text("", font, 100, draw)
        assert len(lines) >= 1


# ── generate_content_image ───────────────────────────────────────────

class TestGenerateContentImage:
    def test_returns_error_when_no_model(self):
        from src.tools.image_generator import generate_content_image
        with patch("src.tools.image_gen.ImageGenerator") as mock_cls:
            mock_cls.is_available.return_value = False
            mock_cls.return_value = mock_cls
            result = generate_content_image("test topic")
            assert result["success"] is False
            assert "No SDXL model" in result["error"]

    def test_returns_error_when_generation_fails(self):
        from src.tools.image_generator import generate_content_image
        with patch("src.tools.image_gen.ImageGenerator") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.is_available.return_value = True
            mock_instance.generate.return_value = {
                "success": False,
                "error": "GPU out of memory",
            }
            mock_cls.return_value = mock_instance
            mock_cls.is_available = mock_instance.is_available
            result = generate_content_image("test topic")
            assert result["success"] is False
            assert "GPU out of memory" in result["error"]

    def test_full_pipeline_success(self):
        from src.tools.image_generator import generate_content_image
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image
            fake_img = Image.new("RGB", (1024, 1024), color="red")
            fake_path = os.path.join(tmp, "generated_fake.png")
            fake_img.save(fake_path)

            with patch("src.tools.image_gen.ImageGenerator") as mock_cls, \
                 patch("src.tools.image_generator._get_output_dir") as mock_dir:
                mock_dir.return_value = Path(tmp)
                mock_instance = MagicMock()
                mock_instance.is_available.return_value = True
                mock_instance.generate.return_value = {
                    "success": True,
                    "image_path": fake_path,
                    "model_used": "test_model",
                }
                mock_cls.return_value = mock_instance
                mock_cls.is_available = mock_instance.is_available
                result = generate_content_image(
                    "AI news update",
                    platform="twitter",
                    pillar="ai_tech",
                )
                assert result["success"] is True
                assert result["platform"] == "twitter"
                assert os.path.exists(result["image_path"])

    def test_with_overlay_text(self):
        from src.tools.image_generator import generate_content_image
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image
            fake_img = Image.new("RGB", (1024, 1024), color="blue")
            fake_path = os.path.join(tmp, "generated_overlay.png")
            fake_img.save(fake_path)

            with patch("src.tools.image_gen.ImageGenerator") as mock_cls, \
                 patch("src.tools.image_generator._get_output_dir") as mock_dir:
                mock_dir.return_value = Path(tmp)
                mock_instance = MagicMock()
                mock_instance.is_available.return_value = True
                mock_instance.generate.return_value = {
                    "success": True,
                    "image_path": fake_path,
                    "model_used": "test_model",
                }
                mock_cls.return_value = mock_instance
                mock_cls.is_available = mock_instance.is_available
                result = generate_content_image(
                    "Breaking News",
                    platform="instagram_post",
                    overlay_text="Breaking: AI Revolution",
                )
                assert result["success"] is True


# ── is_available ─────────────────────────────────────────────────────

class TestIsAvailable:
    def test_available_when_sdxl_found(self):
        from src.tools.image_generator import is_available
        with patch("src.tools.image_gen.ImageGenerator") as mock_cls:
            mock_cls.is_available.return_value = True
            assert is_available() is True

    def test_unavailable_when_no_model(self):
        from src.tools.image_generator import is_available
        with patch("src.tools.image_gen.ImageGenerator") as mock_cls:
            mock_cls.is_available.return_value = False
            assert is_available() is False
