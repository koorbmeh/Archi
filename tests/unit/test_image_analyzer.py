"""Unit tests for the Image Analyzer module — vision API service for UI element location.

Tests expand_target_description, prompt builders, parse_coordinates,
and find_element with mocked OpenRouter client.

Created session 149.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

from src.tools.image_analyzer import (
    expand_target_description,
    _build_start_button_prompt,
    _build_generic_prompt,
    parse_coordinates,
    find_element,
)


# ── expand_target_description() tests ──────────────────────────────


class TestExpandTargetDescription:
    """Tests for target description expansion."""

    def test_windows_start_button_expanded(self):
        result = expand_target_description("Windows Start button")
        assert "Windows logo" in result
        assert "taskbar" in result.lower()

    def test_start_button_variant(self):
        result = expand_target_description("start button")
        assert "Windows logo" in result

    def test_windows_start_case_insensitive(self):
        result = expand_target_description("WINDOWS START")
        assert "Windows logo" in result

    def test_generic_target_unchanged(self):
        result = expand_target_description("login button")
        assert result == "login button"

    def test_empty_target(self):
        result = expand_target_description("")
        assert result == ""

    def test_whitespace_stripped(self):
        result = expand_target_description("  login button  ")
        assert result == "  login button  "  # only lower().strip() used internally


# ── _build_start_button_prompt() tests ─────────────────────────────


class TestBuildStartButtonPrompt:
    """Tests for the specialized Start button vision prompt."""

    def test_contains_screen_dimensions(self):
        prompt = _build_start_button_prompt(1920, 1080)
        assert "1920" in prompt
        assert "1080" in prompt

    def test_contains_coordinate_bounds(self):
        prompt = _build_start_button_prompt(1920, 1080)
        # x_min = int(1920 * 0.25) = 480, x_max = int(1920 * 0.45) = 864
        assert "480" in prompt
        assert "864" in prompt
        # y_min = 1080 - 80 = 1000, y_max = 1080 - 10 = 1070
        assert "1000" in prompt
        assert "1070" in prompt

    def test_contains_critical_instructions(self):
        prompt = _build_start_button_prompt(1920, 1080)
        assert "CRITICAL" in prompt
        assert "weather" in prompt.lower()
        assert "JSON" in prompt

    def test_small_screen(self):
        prompt = _build_start_button_prompt(800, 600)
        assert "800" in prompt
        assert "600" in prompt


# ── _build_generic_prompt() tests ──────────────────────────────────


class TestBuildGenericPrompt:
    """Tests for generic UI element vision prompt."""

    def test_includes_target(self):
        prompt = _build_generic_prompt("login button", 1920, 1080)
        assert "login button" in prompt

    def test_includes_screen_size(self):
        prompt = _build_generic_prompt("OK", 1920, 1080)
        assert "1920" in prompt
        assert "1080" in prompt

    def test_returns_json_format_instruction(self):
        prompt = _build_generic_prompt("element", 100, 100)
        assert "JSON" in prompt
        assert '"x"' in prompt
        assert '"y"' in prompt

    def test_start_button_gets_expanded(self):
        """Generic prompt for start button should use expanded description."""
        prompt = _build_generic_prompt("Windows Start button", 1920, 1080)
        assert "Windows logo" in prompt


# ── parse_coordinates() tests ──────────────────────────────────────


class TestParseCoordinates:
    """Tests for coordinate parsing from vision API response."""

    def test_basic_json(self):
        ok, x, y = parse_coordinates('{"x": 500, "y": 300}', 1920, 1080)
        assert ok is True
        assert x == 500
        assert y == 300

    def test_float_coordinates(self):
        ok, x, y = parse_coordinates('{"x": 500.7, "y": 300.3}', 1920, 1080)
        assert ok is True
        assert x == 500
        assert y == 300

    def test_clamped_to_screen_bounds(self):
        ok, x, y = parse_coordinates('{"x": 5000, "y": -50}', 1920, 1080)
        assert ok is True
        assert x == 1919  # screen_w - 1
        assert y == 0  # clamped to 0

    def test_negative_x_clamped(self):
        ok, x, y = parse_coordinates('{"x": -100, "y": 500}', 1920, 1080)
        assert ok is True
        assert x == 0

    def test_missing_x(self):
        ok, x, y = parse_coordinates('{"y": 300}', 1920, 1080)
        assert ok is False
        assert x == 0
        assert y == 0

    def test_missing_y(self):
        ok, x, y = parse_coordinates('{"x": 500}', 1920, 1080)
        assert ok is False

    def test_no_json(self):
        ok, x, y = parse_coordinates("I can't find the element", 1920, 1080)
        assert ok is False

    def test_empty_string(self):
        ok, x, y = parse_coordinates("", 1920, 1080)
        assert ok is False

    def test_json_with_extra_text(self):
        """Regex should find coords even with surrounding text."""
        text = 'The element is at {"x": 450, "y": 600}. Click there.'
        ok, x, y = parse_coordinates(text, 1920, 1080)
        assert ok is True
        assert x == 450
        assert y == 600

    def test_zero_coordinates(self):
        ok, x, y = parse_coordinates('{"x": 0, "y": 0}', 1920, 1080)
        assert ok is True
        assert x == 0
        assert y == 0

    def test_whitespace_in_json(self):
        ok, x, y = parse_coordinates('{ "x" :  100 , "y" :  200 }', 1920, 1080)
        assert ok is True
        assert x == 100
        assert y == 200


# ── find_element() tests ───────────────────────────────────────────


class TestFindElement:
    """Tests for find_element() — full vision API flow with mocked client."""

    def test_no_api_key_returns_error(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"fake png data")
        with patch.dict(os.environ, {}, clear=True):
            # Ensure OPENROUTER_API_KEY is not set
            os.environ.pop("OPENROUTER_API_KEY", None)
            result = find_element(img, "button", 1920, 1080)
        assert result["success"] is False
        assert "API_KEY" in result["error"]

    def _mock_openrouter(self, mock_client=None, side_effect=None):
        """Create a mock module for src.models.openrouter_client."""
        import sys
        mock_mod = MagicMock()
        if side_effect:
            mock_mod.OpenRouterClient.side_effect = side_effect
        elif mock_client:
            mock_mod.OpenRouterClient.return_value = mock_client
        return patch.dict(sys.modules, {"src.models.openrouter_client": mock_mod})

    def test_import_error_returns_error(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"fake png data")
        import sys
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"src.models.openrouter_client": None}):
                result = find_element(img, "button", 1920, 1080)
        assert result["success"] is False

    def test_unreadable_screenshot_returns_error(self, tmp_path):
        img = tmp_path / "nonexistent.png"
        mock_client = MagicMock()
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with self._mock_openrouter(mock_client):
                result = find_element(img, "button", 1920, 1080)
        assert result["success"] is False

    def test_api_failure_returns_error(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"fake png data")
        mock_client = MagicMock()
        mock_client.generate_with_vision.return_value = {
            "success": False,
            "error": "Rate limited",
        }
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with self._mock_openrouter(mock_client):
                result = find_element(img, "button", 1920, 1080)
        assert result["success"] is False
        assert "Rate limited" in result["error"]

    def test_successful_element_found(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"fake png data")
        mock_client = MagicMock()
        mock_client.generate_with_vision.return_value = {
            "success": True,
            "text": '{"x": 960, "y": 540}',
            "cost_usd": 0.001,
        }
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with self._mock_openrouter(mock_client):
                result = find_element(img, "login button", 1920, 1080)
        assert result["success"] is True
        assert result["coordinates"] == (960, 540)
        assert result["cost_usd"] == 0.001

    def test_unparseable_vision_response(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"fake png data")
        mock_client = MagicMock()
        mock_client.generate_with_vision.return_value = {
            "success": True,
            "text": "I cannot find that element in the screenshot.",
            "cost_usd": 0.001,
        }
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with self._mock_openrouter(mock_client):
                result = find_element(img, "button", 1920, 1080)
        assert result["success"] is False
        assert "parse" in result["error"].lower()

    def test_start_button_uses_specialized_prompt(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"fake png data")
        mock_client = MagicMock()
        mock_client.generate_with_vision.return_value = {
            "success": True,
            "text": '{"x": 960, "y": 1060}',
            "cost_usd": 0.001,
        }
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with self._mock_openrouter(mock_client):
                result = find_element(img, "Windows Start button", 1920, 1080)
        assert result["success"] is True
        # Verify the specialized prompt was used (keyword args)
        call_args = mock_client.generate_with_vision.call_args
        prompt = call_args.kwargs.get("prompt", "")
        assert "CRITICAL" in prompt

    def test_client_valueerror_returns_error(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"fake png data")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with self._mock_openrouter(side_effect=ValueError("Invalid provider")):
                result = find_element(img, "button", 1920, 1080)
        assert result["success"] is False
        assert "Invalid provider" in result["error"]

    def test_empty_text_response(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"fake png data")
        mock_client = MagicMock()
        mock_client.generate_with_vision.return_value = {
            "success": True,
            "text": "",
            "cost_usd": 0.001,
        }
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with self._mock_openrouter(mock_client):
                result = find_element(img, "button", 1920, 1080)
        assert result["success"] is False

    def test_coordinates_clamped_to_screen(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"fake png data")
        mock_client = MagicMock()
        mock_client.generate_with_vision.return_value = {
            "success": True,
            "text": '{"x": 9999, "y": -10}',
            "cost_usd": 0.001,
        }
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with self._mock_openrouter(mock_client):
                result = find_element(img, "button", 1920, 1080)
        assert result["success"] is True
        assert result["coordinates"] == (1919, 0)
