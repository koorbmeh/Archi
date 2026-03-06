"""
Unit tests for message_handler.py — _resolve_effective_message(),
_build_history_messages(), _map_router_result(), _dispatch_fast_path(),
_build_contextual_greeting(), and _needs_computer_use().
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest
from src.interfaces.message_handler import (
    _resolve_effective_message,
    _build_history_messages,
    _map_router_result,
    _dispatch_fast_path,
    _build_contextual_greeting,
    _needs_computer_use,
)
from src.interfaces.intent_classifier import IntentResult


# ============================================================================
# _resolve_effective_message
# ============================================================================

class TestResolveEffectiveMessage:
    """Correction detection and API escalation."""

    def test_no_correction_returns_original(self):
        msg, retry = _resolve_effective_message("hello", [])
        assert msg == "hello"
        assert retry is False

    def test_no_history_returns_original(self):
        msg, retry = _resolve_effective_message("try again", None)
        assert msg == "try again"
        assert retry is False

    def test_correction_with_history(self):
        history = [
            {"role": "user", "content": "What is the capital of France?"},
            {"role": "assistant", "content": "London"},
        ]
        msg, retry = _resolve_effective_message("that's wrong", history)
        assert msg == "What is the capital of France?"
        assert retry is True

    def test_correction_thats_not_right(self):
        history = [
            {"role": "user", "content": "Who wrote Hamlet?"},
            {"role": "assistant", "content": "Dickens"},
        ]
        msg, retry = _resolve_effective_message("that's not right", history)
        assert msg == "Who wrote Hamlet?"
        assert retry is True

    def test_correction_try_again(self):
        history = [
            {"role": "user", "content": "Explain quantum computing"},
            {"role": "assistant", "content": "It's about computers"},
        ]
        msg, retry = _resolve_effective_message("try again", history)
        assert msg == "Explain quantum computing"
        assert retry is True

    def test_long_message_not_treated_as_correction(self):
        """Messages over 80 chars shouldn't match correction patterns."""
        history = [{"role": "user", "content": "previous question"}]
        long_msg = "try again " + "x" * 80
        msg, retry = _resolve_effective_message(long_msg, history)
        assert msg == long_msg
        assert retry is False

    def test_correction_skips_short_history(self):
        """Previous messages <= 5 chars are skipped."""
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        msg, retry = _resolve_effective_message("try again", history)
        assert msg == "try again"
        assert retry is False

    def test_api_escalation(self):
        history = [
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "5"},
        ]
        msg, retry = _resolve_effective_message("ask grok", history)
        assert msg == "What is 2+2?"
        assert retry is True

    def test_api_escalation_skips_grok_in_history(self):
        """If previous message already mentioned grok, skip it."""
        history = [
            {"role": "user", "content": "ask grok about weather"},
            {"role": "assistant", "content": "it's sunny"},
        ]
        msg, retry = _resolve_effective_message("use grok", history)
        # Should still set retry but may not find a non-grok message
        assert retry is True


# ============================================================================
# _build_history_messages
# ============================================================================

class TestBuildHistoryMessages:
    """Session-aware history sizing and truncation."""

    def test_none_history(self):
        assert _build_history_messages(None) == []

    def test_empty_history(self):
        assert _build_history_messages([]) == []

    def test_basic_history(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        with patch("src.interfaces.chat_history.seconds_since_last_message",
                    side_effect=Exception("no chat history")):
            result = _build_history_messages(history)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_empty_content_filtered(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "world"},
        ]
        with patch("src.interfaces.chat_history.seconds_since_last_message",
                    side_effect=Exception("no chat history")):
            result = _build_history_messages(history)
        assert len(result) == 2

    def test_truncates_long_content(self):
        """Content over max_chars should be truncated with '...'."""
        long_content = "x" * 2000
        history = [{"role": "user", "content": long_content}]
        with patch("src.interfaces.chat_history.seconds_since_last_message",
                    return_value=600):  # 10 min = default tier
            result = _build_history_messages(history)
        assert result[0]["content"].endswith("...")
        assert len(result[0]["content"]) <= 1004  # 1000 + "..."

    def test_mid_conversation_sizing(self):
        """<5 min gap should use wider context (8 exchanges, 1200 chars)."""
        history = [{"role": "user", "content": "x" * 1500}]
        with patch("src.interfaces.chat_history.seconds_since_last_message",
                    return_value=60):  # 1 min
            result = _build_history_messages(history)
        # Mid-conversation allows 1200 chars
        content_len = len(result[0]["content"].rstrip("."))
        assert content_len >= 1190  # 1200 minus "..."

    def test_cold_start_sizing(self):
        """30+ min gap should use narrower context (4 exchanges, 800 chars)."""
        history = [{"role": "user", "content": "x" * 1000}]
        with patch("src.interfaces.chat_history.seconds_since_last_message",
                    return_value=3600):  # 1 hour
            result = _build_history_messages(history)
        # Cold start allows 800 chars
        assert result[0]["content"].endswith("...")
        assert len(result[0]["content"]) <= 804

    def test_limits_exchange_count(self):
        """Should limit to max_exchanges * 2 messages."""
        history = [{"role": "user", "content": f"msg {i}"} for i in range(30)]
        with patch("src.interfaces.chat_history.seconds_since_last_message",
                    return_value=600):  # default = 6 exchanges = 12 messages
            result = _build_history_messages(history)
        assert len(result) <= 12

    def test_strips_thinking_from_assistant(self):
        """Assistant messages should have think tags stripped."""
        history = [
            {"role": "assistant", "content": "<think>internal reasoning</think>visible response"},
        ]
        with patch("src.interfaces.chat_history.seconds_since_last_message",
                    side_effect=Exception("skip")):
            result = _build_history_messages(history)
        assert "<think>" not in result[0]["content"]
        assert "visible response" in result[0]["content"]


# ============================================================================
# _map_router_result
# ============================================================================

class TestMapRouterResult:
    """Map RouterResult to IntentResult for dispatch."""

    def _make_rr(self, **kwargs):
        """Create a mock RouterResult with given attributes."""
        rr = MagicMock()
        rr.tier = kwargs.get("tier", "easy")
        rr.answer = kwargs.get("answer", "")
        rr.action = kwargs.get("action", None)
        rr.action_params = kwargs.get("action_params", None)
        rr.cost = kwargs.get("cost", 0.001)
        rr.fast_path = kwargs.get("fast_path", False)
        rr.complexity = kwargs.get("complexity", None)
        return rr

    def test_easy_tier_with_answer(self):
        rr = self._make_rr(tier="easy", answer="Hello there!")
        intent = _map_router_result(rr, "hi")
        assert intent.action == "chat"
        assert intent.params["response"] == "Hello there!"

    def test_easy_tier_with_action(self):
        rr = self._make_rr(tier="easy", action="generate_image",
                           action_params={"prompt": "a cat"})
        intent = _map_router_result(rr, "draw a cat")
        assert intent.action == "generate_image"
        assert intent.params["prompt"] == "a cat"

    def test_complex_goal(self):
        rr = self._make_rr(tier="complex", complexity="goal")
        intent = _map_router_result(rr, "research quantum computing")
        assert intent.action == "create_goal"
        assert intent.params["description"] == "research quantum computing"

    def test_complex_coding(self):
        rr = self._make_rr(tier="complex", complexity="coding")
        intent = _map_router_result(rr, "write a fibonacci function")
        assert intent.action == "chat"
        assert intent.params["response"] == ""

    def test_complex_multi_step(self):
        rr = self._make_rr(tier="complex", complexity="multi_step")
        intent = _map_router_result(rr, "check disk and memory")
        assert intent.action == "multi_step"
        assert intent.params["description"] == "check disk and memory"

    def test_complex_default_complexity(self):
        """Complex tier with no complexity defaults to 'goal'."""
        rr = self._make_rr(tier="complex", complexity=None)
        intent = _map_router_result(rr, "do something complex")
        assert intent.action == "create_goal"

    def test_fallback_to_chat(self):
        rr = self._make_rr(tier="unknown", answer="fallback answer")
        intent = _map_router_result(rr, "whatever")
        assert intent.action == "chat"
        assert intent.params["response"] == "fallback answer"

    def test_fast_path_preserved(self):
        rr = self._make_rr(tier="easy", answer="time answer", fast_path=True)
        intent = _map_router_result(rr, "what time is it")
        assert intent.fast_path is True

    def test_cost_preserved(self):
        rr = self._make_rr(tier="easy", answer="hi", cost=0.005)
        intent = _map_router_result(rr, "hey")
        assert intent.cost == 0.005

    def test_action_overrides_answer(self):
        """When action is set, it takes precedence over answer."""
        rr = self._make_rr(tier="easy", answer="some text",
                           action="datetime", action_params={"response": "10:30 AM"})
        intent = _map_router_result(rr, "time?")
        assert intent.action == "datetime"
        assert intent.params["response"] == "10:30 AM"


# ============================================================================
# _dispatch_fast_path
# ============================================================================

class TestDispatchFastPath:
    """Fast-path intent routing that skips full dispatch."""

    def _make_intent(self, action, params=None, prefix=""):
        return IntentResult(
            action=action,
            params=params or {},
            prefix=prefix,
            cost=0.0,
            fast_path=True,
        )

    @patch("src.interfaces.message_handler.log_conversation")
    def test_datetime_returns_response(self, mock_log):
        intent = self._make_intent("datetime", {"response": "It's 3:00 PM"})
        result = _dispatch_fast_path(
            intent, "what time?", "test", 0.0, None, None, None)
        assert result == "It's 3:00 PM"
        mock_log.assert_called_once()

    @patch("src.interfaces.message_handler._build_contextual_greeting")
    @patch("src.interfaces.message_handler.log_conversation")
    def test_greeting_calls_builder(self, mock_log, mock_greet):
        mock_greet.return_value = "Good morning!"
        intent = self._make_intent("greeting")
        result = _dispatch_fast_path(
            intent, "hello", "test", 0.0, None, None, None)
        assert result == "Good morning!"
        mock_greet.assert_called_once_with("hello")

    @patch("src.interfaces.message_handler._build_contextual_greeting")
    @patch("src.interfaces.message_handler.mark_finding_delivered")
    @patch("src.interfaces.message_handler.log_conversation")
    def test_greeting_appends_finding(self, mock_log, mock_delivered, mock_greet):
        mock_greet.return_value = "Hi!"
        finding = {"id": "f1", "summary": "Found something interesting about X"}
        intent = self._make_intent("greeting")
        result = _dispatch_fast_path(
            intent, "hello", "test", 0.0, finding, None, None)
        assert "Found something interesting" in result
        mock_delivered.assert_called_once_with("f1")

    @patch("src.interfaces.message_handler._handle_deferred_request")
    @patch("src.interfaces.message_handler.log_conversation")
    def test_deferred_request(self, mock_log, mock_defer):
        mock_defer.return_value = "Got it — starting on that"
        intent = self._make_intent("deferred_request",
                                   {"description": "look into X later"})
        result = _dispatch_fast_path(
            intent, "look into X later", "test", 0.0, None, None, MagicMock())
        assert result == "Got it — starting on that"

    @patch("src.interfaces.message_handler._handle_slash_result")
    @patch("src.interfaces.message_handler.log_conversation")
    def test_goals_status(self, mock_log, mock_slash):
        mock_slash.return_value = "Goals (2 total):"
        intent = self._make_intent("goals_status")
        result = _dispatch_fast_path(
            intent, "/goals", "test", 0.0, None, None, None)
        assert result == "Goals (2 total):"

    @patch("src.interfaces.message_handler._handle_slash_result")
    @patch("src.interfaces.message_handler.log_conversation")
    def test_help_command(self, mock_log, mock_slash):
        mock_slash.return_value = "Available Commands:"
        intent = self._make_intent("help")
        result = _dispatch_fast_path(
            intent, "/help", "test", 0.0, None, None, None)
        assert result == "Available Commands:"

    @patch("src.interfaces.message_handler._run_production_tests")
    @patch("src.interfaces.message_handler.log_conversation")
    def test_run_tests(self, mock_log, mock_tests):
        mock_tests.return_value = "All tests passed"
        intent = self._make_intent("run_tests", {"mode": "quick"})
        result = _dispatch_fast_path(
            intent, "/test", "test", 0.0, None, MagicMock(), MagicMock())
        assert result == "All tests passed"

    def test_non_fast_path_returns_none(self):
        intent = self._make_intent("chat", {"response": "hello"})
        result = _dispatch_fast_path(
            intent, "hello", "test", 0.0, None, None, None)
        assert result is None

    def test_multi_step_returns_none(self):
        intent = self._make_intent("multi_step")
        result = _dispatch_fast_path(
            intent, "do stuff", "test", 0.0, None, None, None)
        assert result is None


# ============================================================================
# _build_contextual_greeting
# ============================================================================

class TestBuildContextualGreeting:
    """Time-based and context-aware greetings."""

    @patch("src.interfaces.intent_classifier._is_farewell", return_value=True)
    @patch("src.interfaces.message_handler.datetime")
    def test_farewell_night(self, mock_dt, mock_farewell):
        mock_dt.now.return_value = datetime(2026, 2, 24, 22, 0)
        result = _build_contextual_greeting("goodnight")
        assert "night" in result.lower() or "sleep" in result.lower()

    @patch("src.interfaces.intent_classifier._is_farewell", return_value=True)
    @patch("src.interfaces.message_handler.datetime")
    def test_farewell_daytime(self, mock_dt, mock_farewell):
        mock_dt.now.return_value = datetime(2026, 2, 24, 14, 0)
        result = _build_contextual_greeting("bye")
        assert "later" in result.lower() or "back" in result.lower()

    @patch("src.interfaces.intent_classifier._is_farewell", return_value=True)
    @patch("src.interfaces.message_handler.datetime")
    def test_farewell_sleep(self, mock_dt, mock_farewell):
        mock_dt.now.return_value = datetime(2026, 2, 24, 23, 0)
        result = _build_contextual_greeting("going to sleep")
        assert "sleep" in result.lower()

    @patch("src.interfaces.intent_classifier._is_farewell", return_value=False)
    @patch("src.interfaces.message_handler.datetime")
    def test_morning_greeting(self, mock_dt, mock_farewell):
        mock_dt.now.return_value = datetime(2026, 2, 24, 8, 0)
        with patch("builtins.open", side_effect=Exception("no results")):
            result = _build_contextual_greeting("hey")
        assert "morning" in result.lower()

    @patch("src.interfaces.intent_classifier._is_farewell", return_value=False)
    @patch("src.interfaces.message_handler.datetime")
    def test_afternoon_greeting(self, mock_dt, mock_farewell):
        mock_dt.now.return_value = datetime(2026, 2, 24, 14, 0)
        with patch("builtins.open", side_effect=Exception("no results")):
            result = _build_contextual_greeting("hello")
        assert "afternoon" in result.lower()

    @patch("src.interfaces.intent_classifier._is_farewell", return_value=False)
    @patch("src.interfaces.message_handler.datetime")
    def test_evening_greeting(self, mock_dt, mock_farewell):
        mock_dt.now.return_value = datetime(2026, 2, 24, 20, 0)
        with patch("builtins.open", side_effect=Exception("no results")):
            result = _build_contextual_greeting("hi there")
        assert "evening" in result.lower()

    @patch("src.interfaces.intent_classifier._is_farewell", return_value=False)
    @patch("src.interfaces.message_handler.datetime")
    def test_checkin_greeting(self, mock_dt, mock_farewell):
        mock_dt.now.return_value = datetime(2026, 2, 24, 10, 0)
        with patch("builtins.open", side_effect=Exception("no results")):
            result = _build_contextual_greeting("are you there?")
        assert "here" in result.lower()


# ============================================================================
# _needs_computer_use
# ============================================================================

class TestNeedsComputerUse:
    """Computer use keyword detection."""

    def test_click(self):
        assert _needs_computer_use("click on the button") is True

    def test_screenshot(self):
        assert _needs_computer_use("take a screenshot") is True

    def test_desktop(self):
        assert _needs_computer_use("what's on the desktop") is True

    def test_open_app(self):
        assert _needs_computer_use("open the app for me") is True

    def test_scroll(self):
        assert _needs_computer_use("scroll down the page") is True

    def test_normal_message(self):
        assert _needs_computer_use("what is the weather today") is False

    def test_none_message(self):
        assert _needs_computer_use(None) is False

    def test_empty_message(self):
        assert _needs_computer_use("") is False

    def test_case_insensitive(self):
        assert _needs_computer_use("TAKE A SCREENSHOT") is True


# ============================================================================
# _store_conversation_memory
# ============================================================================

class TestStoreConversationMemory:
    """Notable conversation storage in long-term memory."""

    def test_no_memory_noop(self):
        from src.interfaces.message_handler import _store_conversation_memory
        # Should not raise
        _store_conversation_memory("hello", "test", None)

    def test_no_user_signals_noop(self):
        from src.interfaces.message_handler import _store_conversation_memory
        rr = MagicMock()
        rr.user_signals = None
        _store_conversation_memory("hello", "test", rr)

    @patch("src.interfaces.message_handler._memory")
    def test_stores_with_signals(self, mock_memory):
        from src.interfaces.message_handler import _store_conversation_memory
        import src.interfaces.message_handler as mh
        orig = mh._memory
        try:
            mh._memory = MagicMock()
            rr = MagicMock()
            rr.user_signals = [{"type": "preference"}]
            _store_conversation_memory("I like blue", "discord", rr)
            mh._memory.store_long_term.assert_called_once()
        finally:
            mh._memory = orig


# ============================================================================
# _handle_deferred_request
# ============================================================================

class TestHandleDeferredRequest:
    """Deferred request goal creation."""

    def test_empty_description(self):
        from src.interfaces.message_handler import _handle_deferred_request
        intent = IntentResult(action="deferred_request", params={"description": ""})
        result = _handle_deferred_request(intent, MagicMock(), "test")
        assert "rephrase" in result.lower()

    def test_no_goal_manager(self):
        from src.interfaces.message_handler import _handle_deferred_request
        intent = IntentResult(action="deferred_request",
                              params={"description": "look into vitamins"})
        result = _handle_deferred_request(intent, None, "test")
        assert "isn't available" in result

    def test_creates_goal(self):
        from src.interfaces.message_handler import _handle_deferred_request
        gm = MagicMock()
        gm.create_goal.return_value = MagicMock(goal_id="goal_1")
        intent = IntentResult(action="deferred_request",
                              params={"description": "look into vitamins"})
        # kick_heartbeat is imported inside the function via discord_bot
        with patch.dict("sys.modules", {"src.interfaces.discord_bot": MagicMock()}):
            result = _handle_deferred_request(intent, gm, "test")
        assert "starting on that" in result.lower()
        gm.create_goal.assert_called_once()


# ============================================================================
# _handle_slash_result
# ============================================================================

class TestHandleSlashResult:
    """Slash command result handlers."""

    def test_help_action(self):
        from src.interfaces.message_handler import _handle_slash_result
        intent = IntentResult(action="help", params={})
        result = _handle_slash_result(intent, None)
        assert "/goal" in result
        assert "/status" in result
        assert "/cost" in result

    def test_unknown_command(self):
        from src.interfaces.message_handler import _handle_slash_result
        intent = IntentResult(action="unknown_command",
                              params={"response": "Unknown command: /foo"})
        result = _handle_slash_result(intent, None)
        assert "Unknown command" in result


# ============================================================================
# _build_chat_context
# ============================================================================

class TestBuildChatContext:
    """Chat context string builder for PlanExecutor."""

    def test_no_history_returns_empty(self):
        from src.interfaces.message_handler import _build_chat_context
        assert _build_chat_context(None, []) == ""

    def test_empty_history_returns_empty(self):
        from src.interfaces.message_handler import _build_chat_context
        assert _build_chat_context([], []) == ""

    def test_builds_context_from_messages(self):
        from src.interfaces.message_handler import _build_chat_context
        history_messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = _build_chat_context([{"role": "user"}], history_messages)
        assert "Conversation context:" in result
        assert "User: Hello" in result
        assert "Archi: Hi there" in result

    def test_limits_to_12_messages(self):
        from src.interfaces.message_handler import _build_chat_context
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        result = _build_chat_context([{}], messages)
        # Should only include the last 12
        assert "msg 8" in result
        assert "msg 19" in result
        assert "msg 7" not in result

    def test_truncates_long_content(self):
        from src.interfaces.message_handler import _build_chat_context
        long_msg = "x" * 2000
        messages = [{"role": "user", "content": long_msg}]
        result = _build_chat_context([{}], messages)
        # Content truncated to 1200 chars
        assert len(result) < 1500

    def test_skips_empty_content(self):
        from src.interfaces.message_handler import _build_chat_context
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "World"},
        ]
        result = _build_chat_context([{}], messages)
        assert "Hello" in result
        assert "World" in result
        # Empty assistant message skipped — only 2 lines
        lines = [l for l in result.split("\n") if l.startswith(("User:", "Archi:"))]
        assert len(lines) == 2


# ============================================================================
# _auto_escalate_chat_to_goal
# ============================================================================

class TestAutoEscalateChatToGoal:
    """Auto-escalation from chat PlanExecutor to background goal."""

    def test_no_escalation_when_done_step_exists(self):
        from src.interfaces.message_handler import _auto_escalate_chat_to_goal
        result = {"total_steps": 12, "steps_taken": [{"action": "done"}],
                  "files_created": []}
        assert _auto_escalate_chat_to_goal("test", result, result["steps_taken"],
                                           12, 0.1, MagicMock()) is None

    def test_no_escalation_when_files_created(self):
        from src.interfaces.message_handler import _auto_escalate_chat_to_goal
        result = {"total_steps": 12, "steps_taken": [],
                  "files_created": ["file.txt"]}
        assert _auto_escalate_chat_to_goal("test", result, result["steps_taken"],
                                           12, 0.1, MagicMock()) is None

    def test_no_escalation_when_steps_remaining(self):
        from src.interfaces.message_handler import _auto_escalate_chat_to_goal
        result = {"total_steps": 5, "steps_taken": [], "files_created": []}
        assert _auto_escalate_chat_to_goal("test", result, result["steps_taken"],
                                           12, 0.1, MagicMock()) is None

    def test_no_escalation_without_goal_manager(self):
        from src.interfaces.message_handler import _auto_escalate_chat_to_goal
        result = {"total_steps": 12, "steps_taken": [], "files_created": []}
        assert _auto_escalate_chat_to_goal("test", result, result["steps_taken"],
                                           12, 0.1, None) is None

    @patch("src.interfaces.message_handler.kick_heartbeat", create=True)
    def test_escalates_when_all_steps_used(self, mock_kick):
        from src.interfaces.message_handler import _auto_escalate_chat_to_goal
        gm = MagicMock()
        goal = MagicMock()
        goal.goal_id = "g1"
        gm.create_goal.return_value = goal
        steps = [{"action": "web_search", "success": True, "snippet": "found data"}]
        result = {"total_steps": 12, "steps_taken": steps, "files_created": []}
        out = _auto_escalate_chat_to_goal("research X", result, steps, 12, 0.05, gm)
        assert out is not None
        text, actions, cost = out
        assert "background" in text
        gm.create_goal.assert_called_once()

    def test_escalation_includes_partial_findings(self):
        from src.interfaces.message_handler import _auto_escalate_chat_to_goal
        gm = MagicMock()
        goal = MagicMock()
        goal.goal_id = "g2"
        gm.create_goal.return_value = goal
        steps = [
            {"action": "web_search", "success": True, "snippet": "AI paper results"},
            {"action": "think", "params": {"reasoning": "Need to dig deeper"}},
        ]
        result = {"total_steps": 12, "steps_taken": steps, "files_created": []}
        with patch("src.interfaces.message_handler.kick_heartbeat", create=True):
            out = _auto_escalate_chat_to_goal("research AI", result, steps, 12, 0.05, gm)
        assert out is not None
        text, _, _ = out
        assert "AI paper results" in text or "reviewed" in gm.create_goal.call_args[1]["user_intent"]

    def test_escalation_handles_goal_creation_failure(self):
        from src.interfaces.message_handler import _auto_escalate_chat_to_goal
        gm = MagicMock()
        gm.create_goal.side_effect = RuntimeError("DB error")
        result = {"total_steps": 12, "steps_taken": [], "files_created": []}
        out = _auto_escalate_chat_to_goal("test", result, [], 12, 0.05, gm)
        assert out is None  # Falls through on failure


# ============================================================================
# _format_pe_response
# ============================================================================

class TestFormatPeResponse:
    """PlanExecutor result formatting."""

    def test_uses_done_summary(self):
        from src.interfaces.message_handler import _format_pe_response
        result = {
            "steps_taken": [{"action": "done", "summary": "Task completed successfully"}],
            "files_created": [], "total_cost": 0.05, "total_steps": 3,
        }
        out, actions, cost = _format_pe_response(result, coding=False)
        assert out == "Task completed successfully"
        assert cost == 0.05

    def test_success_without_done_step(self):
        from src.interfaces.message_handler import _format_pe_response
        result = {
            "steps_taken": [{"action": "web_search"}],
            "files_created": [], "total_cost": 0.02, "total_steps": 5,
            "success": True,
        }
        out, actions, cost = _format_pe_response(result, coding=False)
        assert "5 steps" in out

    def test_failure_response(self):
        from src.interfaces.message_handler import _format_pe_response
        result = {
            "steps_taken": [], "files_created": [],
            "total_cost": 0.01, "total_steps": 3, "success": False,
        }
        out, actions, cost = _format_pe_response(result, coding=False)
        assert "couldn't complete" in out

    def test_includes_files_created(self):
        from src.interfaces.message_handler import _format_pe_response
        result = {
            "steps_taken": [{"action": "done", "summary": "Done"}],
            "files_created": ["/workspace/report.txt", "/workspace/data.csv"],
            "total_cost": 0.03, "total_steps": 4,
        }
        out, actions, cost = _format_pe_response(result, coding=False)
        assert "report.txt" in out
        assert "created" in out

    def test_coding_mode_says_modified(self):
        from src.interfaces.message_handler import _format_pe_response
        result = {
            "steps_taken": [{"action": "done", "summary": "Fixed bug"}],
            "files_created": ["/src/main.py"],
            "total_cost": 0.04, "total_steps": 6,
        }
        out, actions, cost = _format_pe_response(result, coding=True)
        assert "modified" in out

    def test_action_list_structure(self):
        from src.interfaces.message_handler import _format_pe_response
        result = {
            "steps_taken": [], "files_created": [],
            "total_cost": 0.0, "total_steps": 2, "success": True,
        }
        out, actions, cost = _format_pe_response(result, coding=False)
        assert len(actions) == 1
        assert "PlanExecutor" in actions[0]["description"]


# ============================================================================
# In-flight request dedup (session 170)
# ============================================================================

class TestInflightDedup:
    """_check_inflight_dedup prevents duplicate PlanExecutor invocations."""

    def test_first_request_not_duplicate(self):
        from src.interfaces.message_handler import (
            _check_inflight_dedup, _clear_inflight, _inflight_requests,
        )
        _inflight_requests.clear()
        assert not _check_inflight_dedup("Research AI news")
        _clear_inflight("Research AI news")

    def test_second_identical_request_is_duplicate(self):
        from src.interfaces.message_handler import (
            _check_inflight_dedup, _clear_inflight, _inflight_requests,
        )
        _inflight_requests.clear()
        assert not _check_inflight_dedup("Research AI news")
        assert _check_inflight_dedup("Research AI news")
        _clear_inflight("Research AI news")

    def test_case_insensitive_dedup(self):
        from src.interfaces.message_handler import (
            _check_inflight_dedup, _clear_inflight, _inflight_requests,
        )
        _inflight_requests.clear()
        assert not _check_inflight_dedup("Research AI News")
        assert _check_inflight_dedup("research ai news")
        _clear_inflight("Research AI News")

    def test_clear_allows_resubmission(self):
        from src.interfaces.message_handler import (
            _check_inflight_dedup, _clear_inflight, _inflight_requests,
        )
        _inflight_requests.clear()
        assert not _check_inflight_dedup("Research AI news")
        _clear_inflight("Research AI news")
        assert not _check_inflight_dedup("Research AI news")
        _clear_inflight("Research AI news")


# ============================================================================
# _record_chat_task_reflection (session 208)
# ============================================================================

class TestRecordChatTaskReflection:
    """Tests for worldview/taste/behavioral reflection on chat-mode PE results."""

    def test_calls_reflect_on_task_on_success(self):
        from src.interfaces.message_handler import _record_chat_task_reflection
        result = {
            "success": True,
            "steps_taken": [{"action": "done", "summary": "Found info"}],
            "total_cost": 0.05,
        }
        router = MagicMock()
        router.get_active_model_info.return_value = {"model": "grok-fast"}
        with patch("src.core.worldview.reflect_on_task") as mock_reflect:
            _record_chat_task_reflection(result, "Research AI", "discord", router)
            mock_reflect.assert_called_once()
            args = mock_reflect.call_args
            assert args.kwargs["success"] is True
            assert "Research AI" in args.kwargs["task_description"]

    def test_calls_develop_taste(self):
        from src.interfaces.message_handler import _record_chat_task_reflection
        result = {
            "success": True,
            "steps_taken": [{"action": "done", "summary": "Done"}],
            "total_cost": 0.03,
            "verified": True,
        }
        router = MagicMock()
        router.get_active_model_info.return_value = {"model": "grok-fast"}
        with patch("src.core.worldview.develop_taste") as mock_taste:
            _record_chat_task_reflection(result, "Write code", "discord", router)
            mock_taste.assert_called_once()
            assert mock_taste.call_args.kwargs["success"] is True

    def test_calls_behavioral_rules(self):
        from src.interfaces.message_handler import _record_chat_task_reflection
        result = {"success": False, "steps_taken": [], "total_cost": 0.1}
        router = MagicMock()
        with patch("src.core.behavioral_rules.process_task_outcome") as mock_br:
            _record_chat_task_reflection(result, "Broken task", "discord", router)
            mock_br.assert_called_once()
            assert mock_br.call_args.kwargs["success"] is False

    def test_exception_in_reflect_does_not_propagate(self):
        from src.interfaces.message_handler import _record_chat_task_reflection
        result = {"success": True, "steps_taken": [], "total_cost": 0.0}
        router = MagicMock()
        with patch("src.core.worldview.reflect_on_task", side_effect=RuntimeError("boom")):
            # Should not raise
            _record_chat_task_reflection(result, "Test", "discord", router)

    def test_no_done_step_uses_fallback_outcome(self):
        from src.interfaces.message_handler import _record_chat_task_reflection
        result = {"success": True, "steps_taken": [{"action": "web_search"}], "total_cost": 0.02}
        router = MagicMock()
        router.get_active_model_info.return_value = {"model": "grok-fast"}
        with patch("src.core.worldview.reflect_on_task") as mock_reflect:
            _record_chat_task_reflection(result, "Search", "discord", router)
            assert mock_reflect.call_args.kwargs["outcome"] == "completed"
