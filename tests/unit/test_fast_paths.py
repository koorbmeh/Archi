"""
Dedicated tests for src/utils/fast_paths.py.

Covers the three shared fast-path modules: datetime detection, screenshot
detection, and image generation extraction. These are tested indirectly
through test_conversational_router.py and test_screenshot.py, but this file
gives clearer coverage attribution.
"""

import sys
from pathlib import Path
from unittest.mock import patch

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest
from src.utils.fast_paths import (
    DATETIME_PATTERNS,
    SCREENSHOT_PATTERNS,
    IMAGE_GEN_STARTERS,
    is_datetime_question,
    is_screenshot_request,
    extract_image_prompt,
)


# ============================================================================
# 1. is_datetime_question
# ============================================================================

class TestIsDatetimeQuestion:

    @pytest.mark.parametrize("msg", [
        "what day is it",
        "what's the date",
        "what is the date",
        "what time is it",
        "current time",
        "current date",
        "today's date",
        "day of the week",
        "what date is it today",
        "what is today",
    ])
    def test_datetime_matches(self, msg):
        assert is_datetime_question(msg) is True

    @pytest.mark.parametrize("msg", [
        "what is the weather",
        "tell me a joke",
        "hello",
        "research the best monitors",
        "",
    ])
    def test_datetime_non_matches(self, msg):
        assert is_datetime_question(msg) is False

    def test_all_patterns_actually_match(self):
        """Every entry in DATETIME_PATTERNS should be recognized."""
        for pattern in DATETIME_PATTERNS:
            assert is_datetime_question(pattern) is True, f"Pattern failed: {pattern}"


# ============================================================================
# 2. is_screenshot_request
# ============================================================================

class TestIsScreenshotRequest:

    @pytest.mark.parametrize("msg", [
        "take a screenshot",
        "take screenshot",
        "screenshot",
        "capture the screen",
        "capture screen",
        "screen capture",
        "what's on screen",
        "what's on my screen",
        "show me the screen",
        "grab the screen",
        "screen grab",
        "screengrab",
        "print screen",
        "printscreen",
    ])
    def test_screenshot_matches(self, msg):
        assert is_screenshot_request(msg) is True

    @pytest.mark.parametrize("msg", [
        "",
        "hello",
        "take a picture of this code",
        "capture the moment",
    ])
    def test_screenshot_non_matches(self, msg):
        assert is_screenshot_request(msg) is False

    def test_all_patterns_actually_match(self):
        """Every entry in SCREENSHOT_PATTERNS should be recognized."""
        for pattern in SCREENSHOT_PATTERNS:
            assert is_screenshot_request(pattern) is True, f"Pattern failed: {pattern}"


# ============================================================================
# 3. extract_image_prompt
# ============================================================================

class TestExtractImagePrompt:

    # ---- Basic starter phrases ----

    @pytest.mark.parametrize("msg,expected_prompt", [
        ("generate an image of a sunset", "a sunset"),
        ("generate image of a cat", "a cat"),
        ("generate a picture of mountains", "mountains"),
        ("create an image of a robot", "a robot"),
        ("create a picture of a forest", "a forest"),
        ("draw a dragon", "a dragon"),
        ("draw me a unicorn", "a unicorn"),
        ("paint a landscape", "a landscape"),
        ("paint me a portrait", "a portrait"),
        ("make an image of a car", "a car"),
        ("make a picture of a house", "a house"),
        ("send me a picture of a dog", "a dog"),
        ("send me an image of space", "space"),
    ])
    def test_basic_starters(self, msg, expected_prompt):
        result = extract_image_prompt(msg.lower(), msg)
        assert result is not None
        prompt, count, model = result
        assert prompt == expected_prompt
        assert count == 1
        assert model is None

    def test_all_starters_work(self):
        """Every entry in IMAGE_GEN_STARTERS should extract a prompt."""
        for starter in IMAGE_GEN_STARTERS:
            msg = starter + "a test subject"
            result = extract_image_prompt(msg.lower(), msg)
            assert result is not None, f"Starter failed: {starter!r}"
            assert "a test subject" in result[0]

    # ---- Count pattern ----

    def test_count_pattern(self):
        msg = "generate 3 images of a sunset"
        result = extract_image_prompt(msg.lower(), msg)
        assert result is not None
        prompt, count, model = result
        assert prompt == "a sunset"
        assert count == 3

    def test_count_capped_at_10(self):
        msg = "generate 50 images of cats"
        result = extract_image_prompt(msg.lower(), msg)
        assert result is not None
        assert result[1] == 10

    def test_count_pattern_variations(self):
        for phrase in ("create 2 pictures of a dog", "draw 4 paintings of trees",
                       "make 1 image of a house", "send 5 photos of cats"):
            result = extract_image_prompt(phrase.lower(), phrase)
            assert result is not None, f"Count pattern failed: {phrase!r}"

    # ---- Trailing punctuation stripped ----

    def test_trailing_punctuation_stripped(self):
        msg = "draw a cat!"
        result = extract_image_prompt(msg.lower(), msg)
        assert result is not None
        assert result[0] == "a cat"

        msg2 = "generate an image of a sunset?"
        result2 = extract_image_prompt(msg2.lower(), msg2)
        assert result2 is not None
        assert result2[0] == "a sunset"

    # ---- Too-short prompts rejected ----

    def test_short_prompt_rejected(self):
        msg = "draw ab"
        result = extract_image_prompt(msg.lower(), msg)
        assert result is None

    def test_empty_prompt_rejected(self):
        msg = "draw "
        result = extract_image_prompt(msg.lower(), msg)
        assert result is None

    # ---- Non-matches ----

    @pytest.mark.parametrize("msg", [
        "hello",
        "what time is it",
        "research image generation techniques",
        "the image of a cat is cute",
        "can you generate a report",
        "",
    ])
    def test_non_image_messages(self, msg):
        result = extract_image_prompt(msg.lower(), msg)
        assert result is None

    # ---- Model prefix ----

    def test_model_prefix(self):
        msg = "using testmodel, draw a cat"
        result = extract_image_prompt(msg.lower(), msg)
        assert result is not None
        prompt, count, model = result
        assert prompt == "a cat"
        assert model == "testmodel"

    # ---- Model suffix ----

    def test_model_suffix_with_resolver(self):
        """Model suffix extraction requires image_gen.resolve_image_model."""
        import types
        msg = "generate an image of a cat with sdxl"
        mock_mod = types.ModuleType("src.tools.image_gen")
        mock_mod.resolve_image_model = lambda x: "sdxl"
        with patch.dict("sys.modules", {"src.tools.image_gen": mock_mod}):
            result = extract_image_prompt(msg.lower(), msg)
        assert result is not None
        assert result[0] == "a cat"
        assert result[2] == "sdxl"

    def test_model_suffix_import_error_handled(self):
        """If image_gen can't be imported, suffix model detection is skipped gracefully."""
        msg = "generate an image of a cat with unknownmodel"
        # Ensure no crash even if import fails
        with patch.dict("sys.modules", {"src.tools.image_gen": None}):
            result = extract_image_prompt(msg.lower(), msg)
        # Should still return a result (just without model extraction)
        assert result is not None
        assert result[2] is None  # no model extracted

    # ---- "draw me" / "paint me" strips leading "me" (session 115) ----

    @pytest.mark.parametrize("msg,expected_prompt", [
        ("draw me a dragon", "a dragon"),
        ("paint me a sunset", "a sunset"),
        ("draw me something cool", "something cool"),
        ("paint me like one of your french girls", "like one of your french girls"),
    ])
    def test_draw_paint_me_strips_me(self, msg, expected_prompt):
        """Session 115 regression: 'draw me X' should strip 'me' from prompt."""
        result = extract_image_prompt(msg.lower(), msg)
        assert result is not None
        assert result[0] == expected_prompt


# ============================================================================
# 4. Screenshot detection precision (session 115)
# ============================================================================

class TestScreenshotPrecision:
    """Session 115 regression: bare 'screenshot' should not match questions about screenshots."""

    @pytest.mark.parametrize("msg", [
        "tell me about screenshots",
        "how do screenshots work",
        "what is a screenshot",
        "explain how screenshot tools work",
    ])
    def test_questions_about_screenshots_rejected(self, msg):
        assert is_screenshot_request(msg) is False

    @pytest.mark.parametrize("msg", [
        "screenshot",
        "screenshot!",
        "screenshot please",
        "take a screenshot",
        "take screenshot",
        "capture the screen",
        "screen grab",
    ])
    def test_real_screenshot_requests_still_match(self, msg):
        assert is_screenshot_request(msg) is True


# ============================================================================
# 5. "for me" image gen starters (session 117)
# ============================================================================

class TestForMeImageStarters:
    """Verify 'generate a picture for me of X' patterns match."""

    @pytest.mark.parametrize("msg,expected_prompt", [
        ("generate a picture for me of a cat", "a cat"),
        ("generate an image for me of a sunset", "a sunset"),
        ("create a picture for me of mountains", "mountains"),
        ("create an image for me of a robot", "a robot"),
    ])
    def test_for_me_starters_match(self, msg, expected_prompt):
        result = extract_image_prompt(msg.lower(), msg)
        assert result is not None
        assert result[0] == expected_prompt
        assert result[1] == 1
        assert result[2] is None
