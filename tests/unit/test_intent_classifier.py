"""
Unit tests for intent_classifier.py — classify(), _handle_slash_command(),
IntentResult, and _is_farewell().

The three routing classifiers (_is_greeting_or_social, needs_multi_step,
is_coding_request) are tested separately in test_routing_classifiers.py.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest
from src.interfaces.intent_classifier import (
    IntentResult,
    classify,
    _handle_slash_command,
    _is_farewell,
)


# ============================================================================
# IntentResult
# ============================================================================

class TestIntentResult:
    """Basic IntentResult construction and defaults."""

    def test_defaults(self):
        r = IntentResult(action="chat_fallback", params={})
        assert r.action == "chat_fallback"
        assert r.params == {}
        assert r.prefix == ""
        assert r.cost == 0.0
        assert r.fast_path is False

    def test_custom_values(self):
        r = IntentResult(action="greeting", params={"k": "v"},
                         prefix="Hey!", cost=0.01, fast_path=True)
        assert r.action == "greeting"
        assert r.params == {"k": "v"}
        assert r.prefix == "Hey!"
        assert r.cost == 0.01
        assert r.fast_path is True

    def test_slots(self):
        """IntentResult uses __slots__ — no arbitrary attrs."""
        r = IntentResult(action="test", params={})
        with pytest.raises(AttributeError):
            r.nonexistent = True


# ============================================================================
# _handle_slash_command
# ============================================================================

class TestHandleSlashCommand:
    """Tests for slash command routing."""

    def test_goal_with_manager(self):
        gm = MagicMock()
        r = _handle_slash_command("/goal buy groceries", "/goal buy groceries", gm)
        assert r is not None
        assert r.action == "create_goal"
        assert r.params["description"] == "buy groceries"
        assert r.fast_path is True

    def test_goal_without_manager(self):
        # Without goal_manager, /goal falls through to unknown_command
        r = _handle_slash_command("/goal buy groceries", "/goal buy groceries", None)
        assert r.action == "unknown_command"

    def test_goals_status(self):
        r = _handle_slash_command("/goals", "/goals", None)
        assert r.action == "goals_status"

    def test_status(self):
        r = _handle_slash_command("/status", "/status", None)
        assert r.action == "system_status"

    def test_cost(self):
        r = _handle_slash_command("/cost", "/cost", None)
        assert r.action == "cost_report"

    def test_help(self):
        r = _handle_slash_command("/help", "/help", None)
        assert r.action == "help"

    def test_help_alias(self):
        r = _handle_slash_command("/h", "/h", None)
        assert r.action == "help"

    def test_test_quick(self):
        r = _handle_slash_command("/test", "/test", None)
        assert r.action == "run_tests"
        assert r.params["mode"] == "quick"

    def test_test_full(self):
        r = _handle_slash_command("/test full", "/test full", None)
        assert r.action == "run_tests"
        assert r.params["mode"] == "full"

    def test_unknown_command(self):
        r = _handle_slash_command("/foobar", "/foobar", None)
        assert r.action == "unknown_command"
        assert "/foobar" in r.params["response"]

    def test_non_slash_returns_none(self):
        r = _handle_slash_command("hello", "hello", None)
        assert r is None


# ============================================================================
# _is_farewell
# ============================================================================

class TestIsFarewell:
    """Tests for farewell detection."""

    def test_empty(self):
        assert _is_farewell("") is False
        assert _is_farewell(None) is False

    def test_standard_farewells(self):
        assert _is_farewell("good night") is True
        assert _is_farewell("goodbye") is True
        assert _is_farewell("see you later") is True
        assert _is_farewell("ttyl") is True
        assert _is_farewell("peace out") is True

    def test_bye_word_boundary(self):
        assert _is_farewell("bye") is True
        assert _is_farewell("ok bye") is True
        assert _is_farewell("bye!") is True

    def test_bye_no_false_positive(self):
        assert _is_farewell("bypass") is False
        assert _is_farewell("bystander") is False

    def test_not_farewell(self):
        assert _is_farewell("hello") is False
        assert _is_farewell("what time is it") is False

    def test_whitespace_stripped(self):
        assert _is_farewell("  goodbye  ") is True


# ============================================================================
# classify() — main entry point
# ============================================================================

class TestClassifyDatetime:
    """Datetime fast-path."""

    def test_what_time(self):
        r = classify("what time is it", "what time is it", None, [], "", None)
        assert r.action == "datetime"
        assert r.fast_path is True
        assert r.cost == 0.0

    def test_what_day(self):
        r = classify("what day is it", "what day is it", None, [], "", None)
        assert r.action == "datetime"
        assert r.fast_path is True


class TestClassifySlashCommands:
    """Slash command fast-paths via classify()."""

    def test_goals(self):
        r = classify("/goals", "/goals", None, [], "", None)
        assert r.action == "goals_status"
        assert r.fast_path is True

    def test_status(self):
        r = classify("/status", "/status", None, [], "", None)
        assert r.action == "system_status"

    def test_goal_with_manager(self):
        gm = MagicMock()
        r = classify("/goal do stuff", "/goal do stuff", None, [], "", gm)
        assert r.action == "create_goal"
        assert r.params["description"] == "do stuff"

    def test_unknown_command(self):
        r = classify("/nope", "/nope", None, [], "", None)
        assert r.action == "unknown_command"


class TestClassifyGreeting:
    """Greeting fast-path."""

    def test_simple_hello(self):
        r = classify("hello", "hello", None, [], "", None)
        assert r.action == "greeting"
        assert r.fast_path is True

    def test_hey_archi(self):
        r = classify("hey archi", "hey archi", None, [], "", None)
        assert r.action == "greeting"

    def test_goodbye(self):
        r = classify("goodbye", "goodbye", None, [], "", None)
        assert r.action == "greeting"
        assert r.fast_path is True


class TestClassifyScreenshot:
    """Screenshot fast-path."""

    def test_take_a_screenshot(self):
        r = classify("take a screenshot", "take a screenshot", None, [], "", None)
        assert r.action == "screenshot"
        assert r.fast_path is True


class TestClassifyImageGen:
    """Image generation fast-path."""

    def test_draw_a_cat(self):
        r = classify("draw a cat", "draw a cat", None, [], "", None)
        assert r.action == "generate_image"
        assert r.fast_path is True
        assert "cat" in r.params["prompt"]

    def test_generate_an_image_of(self):
        r = classify("generate an image of a sunset",
                      "generate an image of a sunset", None, [], "", None)
        assert r.action == "generate_image"
        assert "sunset" in r.params["prompt"]


class TestClassifyChatFallback:
    """Messages that don't hit any fast-path → chat_fallback."""

    def test_complex_question(self):
        r = classify("What is the meaning of life?",
                      "What is the meaning of life?", None, [], "", None)
        assert r.action == "chat_fallback"
        assert r.fast_path is False

    def test_task_request(self):
        r = classify("Can you check my API usage for this week?",
                      "Can you check my API usage for this week?", None, [], "", None)
        assert r.action == "chat_fallback"


class TestClassifyPriority:
    """Verify fast-path priority ordering."""

    def test_datetime_beats_greeting(self):
        # "what time is it" could be mistaken for social, but datetime fires first
        r = classify("what time is it", "what time is it", None, [], "", None)
        assert r.action == "datetime"

    def test_slash_command_beats_greeting(self):
        r = classify("/help", "/help", None, [], "", None)
        assert r.action == "help"

    def test_greeting_beats_fallback(self):
        r = classify("hi", "hi", None, [], "", None)
        assert r.action == "greeting"
