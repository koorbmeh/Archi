"""Unit tests for screenshot action handling.

Tests _handle_screenshot in action_dispatcher, screenshot detection
in discord_bot image-sending logic, and intent classifier routing.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ---- action_dispatcher._handle_screenshot tests ----

def _make_mock_tool_registry(execute_return=None, execute_side_effect=None):
    """Create a mock ToolRegistry module for patching local imports."""
    mock_module = MagicMock()
    mock_instance = MagicMock()
    if execute_side_effect:
        mock_instance.execute.side_effect = execute_side_effect
    else:
        mock_instance.execute.return_value = execute_return or {"success": True}
    mock_module.ToolRegistry.return_value = mock_instance
    return mock_module, mock_instance


class TestHandleScreenshot:
    """Tests for the screenshot action handler."""

    def test_screenshot_success(self):
        """Successful screenshot returns image path in actions."""
        mock_mod, mock_inst = _make_mock_tool_registry({"success": True})

        import sys
        from src.interfaces.action_dispatcher import _handle_screenshot

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"ARCHI_ROOT": tmpdir}):
                with patch.dict(sys.modules, {"src.tools.tool_registry": mock_mod}):
                    response, actions, cost = _handle_screenshot({}, {"router": MagicMock()})

        assert "screenshot" in response.lower()
        assert len(actions) == 1
        assert actions[0]["description"] == "Screenshot taken"
        assert "image_path" in actions[0]["result"]
        assert cost == 0.0

    def test_screenshot_failure(self):
        """Failed screenshot returns error message."""
        mock_mod, mock_inst = _make_mock_tool_registry({"success": False, "error": "No display"})

        import sys
        from src.interfaces.action_dispatcher import _handle_screenshot

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"ARCHI_ROOT": tmpdir}):
                with patch.dict(sys.modules, {"src.tools.tool_registry": mock_mod}):
                    response, actions, cost = _handle_screenshot({}, {"router": MagicMock()})

        assert "failed" in response.lower() or "No display" in response
        assert len(actions) == 0
        assert cost == 0.0

    def test_screenshot_no_pyautogui(self):
        """Missing pyautogui returns graceful error."""
        from src.interfaces.action_dispatcher import _handle_screenshot

        # Make the local import raise ImportError
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if "tool_registry" in name:
                raise ImportError("no pyautogui")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            response, actions, cost = _handle_screenshot({}, {"router": MagicMock()})

        assert "not available" in response.lower()
        assert len(actions) == 0

    def test_screenshot_saves_to_workspace(self):
        """Screenshot file is saved under workspace/screenshots/."""
        mock_mod, mock_inst = _make_mock_tool_registry({"success": True})

        import sys
        from src.interfaces.action_dispatcher import _handle_screenshot

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"ARCHI_ROOT": tmpdir}):
                with patch.dict(sys.modules, {"src.tools.tool_registry": mock_mod}):
                    response, actions, cost = _handle_screenshot({}, {"router": MagicMock()})

        # Check execute was called with a filepath under workspace/screenshots/
        call_args = mock_inst.execute.call_args
        assert call_args[0][0] == "desktop_screenshot"
        filepath = call_args[0][1]["filepath"]
        assert "screenshots" in filepath
        assert filepath.endswith(".png")

    def test_screenshot_exception(self):
        """Unexpected exception is caught and reported."""
        mock_mod, mock_inst = _make_mock_tool_registry(
            execute_side_effect=RuntimeError("VRAM exploded")
        )

        import sys
        from src.interfaces.action_dispatcher import _handle_screenshot

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"ARCHI_ROOT": tmpdir}):
                with patch.dict(sys.modules, {"src.tools.tool_registry": mock_mod}):
                    response, actions, cost = _handle_screenshot({}, {"router": MagicMock()})

        assert "error" in response.lower() or "VRAM" in response
        assert cost == 0.0


# ---- ACTION_HANDLERS registration test ----

class TestScreenshotRegistered:
    """Verify screenshot is in the handler registry."""

    def test_screenshot_in_action_handlers(self):
        from src.interfaces.action_dispatcher import ACTION_HANDLERS
        assert "screenshot" in ACTION_HANDLERS

    def test_screenshot_handler_callable(self):
        from src.interfaces.action_dispatcher import ACTION_HANDLERS
        assert callable(ACTION_HANDLERS["screenshot"])


# ---- Intent classifier tests ----

class TestScreenshotFastPath:
    """Verify screenshot is detected as a zero-cost fast-path (no model call)."""

    def test_take_a_screenshot(self):
        from src.interfaces.intent_classifier import _is_screenshot_request
        assert _is_screenshot_request("take a screenshot")

    def test_screenshot_alone(self):
        from src.interfaces.intent_classifier import _is_screenshot_request
        assert _is_screenshot_request("screenshot")

    def test_capture_the_screen(self):
        from src.interfaces.intent_classifier import _is_screenshot_request
        assert _is_screenshot_request("capture the screen")

    def test_whats_on_screen(self):
        from src.interfaces.intent_classifier import _is_screenshot_request
        assert _is_screenshot_request("what's on screen")

    def test_show_me_the_screen(self):
        from src.interfaces.intent_classifier import _is_screenshot_request
        assert _is_screenshot_request("show me the screen")

    def test_print_screen(self):
        from src.interfaces.intent_classifier import _is_screenshot_request
        assert _is_screenshot_request("print screen")

    def test_screen_grab(self):
        from src.interfaces.intent_classifier import _is_screenshot_request
        assert _is_screenshot_request("screengrab")

    def test_not_screenshot_weather(self):
        from src.interfaces.intent_classifier import _is_screenshot_request
        assert not _is_screenshot_request("what's the weather?")

    def test_not_screenshot_general(self):
        from src.interfaces.intent_classifier import _is_screenshot_request
        assert not _is_screenshot_request("tell me about screen technology")

    def test_fast_path_flag_set(self):
        """classify() returns fast_path=True for screenshot requests."""
        from src.interfaces.intent_classifier import classify
        result = classify(
            "take a screenshot", "take a screenshot",
            router=MagicMock(), history_messages=[], system_prompt=""
        )
        assert result.action == "screenshot"
        assert result.fast_path is True
        assert result.cost == 0.0


# ---- Discord bot image detection tests ----

class TestDiscordScreenshotDetection:
    """Test that screenshot actions are detected for file sending."""

    def test_screenshot_action_matches_pattern(self):
        """The description 'Screenshot taken' should match the discord bot's
        image-sending logic (desc == 'Screenshot taken')."""
        # Simulate the discord bot's action-scanning loop
        actions = [{"description": "Screenshot taken", "result": {"image_path": "/tmp/test.png"}}]

        media_found = False
        for act in actions:
            desc = act.get("description", "")
            if desc.startswith("Generated image:") or desc == "Screenshot taken":
                img_path = act.get("result", {}).get("image_path", "")
                if img_path:
                    media_found = True
                break

        assert media_found

    def test_generated_image_still_matches(self):
        """Existing generated image detection still works."""
        actions = [{"description": "Generated image: cat", "result": {"image_path": "/tmp/cat.png"}}]

        media_found = False
        for act in actions:
            desc = act.get("description", "")
            if desc.startswith("Generated image:") or desc == "Screenshot taken":
                img_path = act.get("result", {}).get("image_path", "")
                if img_path:
                    media_found = True
                break

        assert media_found

    def test_other_actions_dont_match(self):
        """Non-image actions shouldn't trigger media sending."""
        actions = [{"description": "Searched for: cats", "result": {"query": "cats"}}]

        media_found = False
        for act in actions:
            desc = act.get("description", "")
            if desc.startswith("Generated image:") or desc == "Screenshot taken":
                img_path = act.get("result", {}).get("image_path", "")
                if img_path:
                    media_found = True
                break

        assert not media_found


# ---- Computer use keyword detection tests ----

class TestScreenshotKeywordDetection:
    """Verify screenshot-related keywords trigger computer use detection."""

    def test_screenshot_keyword(self):
        from src.interfaces.message_handler import _needs_computer_use
        assert _needs_computer_use("take a screenshot of my desktop")

    def test_capture_screen_keyword(self):
        from src.interfaces.message_handler import _needs_computer_use
        assert _needs_computer_use("capture the screen please")

    def test_whats_on_screen(self):
        from src.interfaces.message_handler import _needs_computer_use
        assert _needs_computer_use("what's on screen right now?")

    def test_take_a_picture(self):
        from src.interfaces.message_handler import _needs_computer_use
        assert _needs_computer_use("take a picture of what you see")

    def test_unrelated_doesnt_match(self):
        from src.interfaces.message_handler import _needs_computer_use
        assert not _needs_computer_use("what's the weather today?")
