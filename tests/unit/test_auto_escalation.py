"""
Unit tests for computer use auto-escalation in message_handler.py.

Tests:
  1. _needs_computer_use() — keyword detection for desktop/vision tasks
  2. _auto_escalate_if_needed() — model switching logic with mock router
  3. _revert_escalation() — cleanup after task completes
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest
from src.interfaces.message_handler import (
    _needs_computer_use,
    _auto_escalate_if_needed,
    _revert_escalation,
)


# ============================================================================
# 1. _needs_computer_use() — keyword detection
# ============================================================================

class TestNeedsComputerUse:
    """Verify computer use keyword detection."""

    # --- Should match ---

    @pytest.mark.parametrize("msg", [
        "click the start button",
        "right-click on the file",
        "double-click that icon",
        "take a screenshot",
        "take a screenshot of the desktop",
        "capture the screen please",
        "what's on screen right now?",
        "look at the screen and tell me what you see",
        "open the app settings",
        "open the browser to google",
        "type into the search box",
        "press the button",
        "scroll down to the bottom",
        "scroll up a bit",
        "Click the Start Button",
        "TAKE A SCREENSHOT",
        "what do you see on my desktop",
    ])
    def test_matches_computer_use(self, msg):
        assert _needs_computer_use(msg) is True

    # --- Should NOT match ---

    @pytest.mark.parametrize("msg", [
        "hello",
        "what's the weather?",
        "research thermal paste",
        "create a file called notes.txt",
        "search for Python tutorials",
        "how are you doing?",
        "what time is it?",
        "/goals",
        "/test",
        "fix the bug in router.py",
        "generate an image of a cat",
        "",
        "open a new goal",  # "open" alone matches, but "open the app/program/browser" is the pattern
    ])
    def test_does_not_match(self, msg):
        # "open a new goal" — "open the app" / "open the program" / "open the browser" are the keywords
        # plain "open" without "the app/program/browser" should not match
        # But wait — "desktop" is a standalone keyword. Let's check the actual keywords.
        # The keywords include "open the app", "open the program", "open the browser"
        # "open a new goal" doesn't contain any of those.
        assert _needs_computer_use(msg) is False

    def test_none_input(self):
        assert _needs_computer_use(None) is False

    def test_empty_string(self):
        assert _needs_computer_use("") is False


# ============================================================================
# 2. _auto_escalate_if_needed() — model switching logic
# ============================================================================

class TestAutoEscalateIfNeeded:
    """Verify auto-escalation calls switch_model_temp correctly."""

    def _make_router(self, current_model="x-ai/grok-4.1-fast"):
        """Create a mock router."""
        router = MagicMock()
        router.get_active_model_info.return_value = {"model": current_model}
        router.switch_model_temp.return_value = {
            "model": "google/gemini-3.1-pro-preview",
            "message": "Switched",
        }
        return router

    # --- Escalation should fire ---

    def test_escalates_for_click_action(self):
        router = self._make_router()
        result = _auto_escalate_if_needed(router, "click", "click the button", count=3)
        assert result is True
        router.switch_model_temp.assert_called_once_with("gemini-3.1-pro", count=3)

    def test_escalates_for_browser_navigate_action(self):
        router = self._make_router()
        result = _auto_escalate_if_needed(router, "browser_navigate", "open google", count=3)
        assert result is True
        router.switch_model_temp.assert_called_once_with("gemini-3.1-pro", count=3)

    def test_escalates_for_screenshot_keyword_in_chat(self):
        """Even if action is 'chat', keyword detection should fire."""
        router = self._make_router()
        result = _auto_escalate_if_needed(router, "chat", "take a screenshot", count=3)
        assert result is True
        router.switch_model_temp.assert_called_once()

    def test_escalates_for_desktop_keyword(self):
        router = self._make_router()
        result = _auto_escalate_if_needed(router, "multi_step", "look at the desktop", count=15)
        assert result is True
        router.switch_model_temp.assert_called_once_with("gemini-3.1-pro", count=15)

    def test_respects_count_parameter(self):
        router = self._make_router()
        _auto_escalate_if_needed(router, "click", "click it", count=7)
        router.switch_model_temp.assert_called_once_with("gemini-3.1-pro", count=7)

    # --- Escalation should NOT fire ---

    def test_no_escalation_for_chat_action(self):
        router = self._make_router()
        result = _auto_escalate_if_needed(router, "chat", "hello how are you?", count=3)
        assert result is False
        router.switch_model_temp.assert_not_called()

    def test_no_escalation_for_search_action(self):
        router = self._make_router()
        result = _auto_escalate_if_needed(router, "search", "weather today", count=3)
        assert result is False
        router.switch_model_temp.assert_not_called()

    def test_no_escalation_for_create_file(self):
        router = self._make_router()
        result = _auto_escalate_if_needed(router, "create_file", "create notes.txt", count=3)
        assert result is False
        router.switch_model_temp.assert_not_called()

    def test_no_escalation_when_already_on_claude(self):
        """If already using Claude, don't switch again."""
        router = self._make_router(current_model="google/gemini-3.1-pro-preview")
        result = _auto_escalate_if_needed(router, "click", "click the button", count=3)
        assert result is False
        router.switch_model_temp.assert_not_called()

    def test_no_escalation_when_on_gemini_variant(self):
        """If already on any Gemini model, don't switch again."""
        router = self._make_router(current_model="google/gemini-2.5-flash")
        result = _auto_escalate_if_needed(router, "click", "click it", count=3)
        assert result is False
        router.switch_model_temp.assert_not_called()

    def test_no_escalation_with_none_router(self):
        result = _auto_escalate_if_needed(None, "click", "click the button", count=3)
        assert result is False

    def test_handles_router_exception_gracefully(self):
        router = MagicMock()
        router.get_active_model_info.side_effect = RuntimeError("broken")
        result = _auto_escalate_if_needed(router, "click", "click it", count=3)
        assert result is False

    def test_handles_switch_exception_gracefully(self):
        router = self._make_router()
        router.switch_model_temp.side_effect = RuntimeError("switch failed")
        result = _auto_escalate_if_needed(router, "click", "click it", count=3)
        assert result is False


# ============================================================================
# 3. _revert_escalation() — cleanup
# ============================================================================

class TestRevertEscalation:
    """Verify escalation revert calls complete_temp_task."""

    def test_calls_complete_temp_task(self):
        router = MagicMock()
        router.complete_temp_task.return_value = "Reverted to grok"
        _revert_escalation(router)
        router.complete_temp_task.assert_called_once()

    def test_handles_none_router(self):
        # Should not raise
        _revert_escalation(None)

    def test_handles_exception_gracefully(self):
        router = MagicMock()
        router.complete_temp_task.side_effect = RuntimeError("oops")
        # Should not raise
        _revert_escalation(router)

    def test_no_revert_message(self):
        router = MagicMock()
        router.complete_temp_task.return_value = None
        _revert_escalation(router)
        router.complete_temp_task.assert_called_once()
