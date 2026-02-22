"""Unit tests for ConversationalRouter — fast-paths, deferred requests, routing.

Tests the local fast-path helpers, deferred request detection, accumulation
state, prompt building, response parsing, and the full route() function with
mocked model calls.

Created session 80.
"""

import json
import time
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from src.core.conversational_router import (
    RouterResult,
    ContextState,
    _AccumulationState,
    _check_local_fast_paths,
    _is_datetime_question,
    _is_screenshot_request,
    _extract_image_prompt,
    _handle_slash_command,
    _is_deferred_request,
    _build_router_prompt,
    _parse_router_response,
    route,
    start_accumulation,
    get_accumulation_state,
    clear_accumulation,
)


# ── RouterResult defaults ─────────────────────────────────────────────


class TestRouterResult:

    def test_defaults(self):
        r = RouterResult(intent="new_request")
        assert r.intent == "new_request"
        assert r.tier == "complex"
        assert r.answer == ""
        assert r.complexity == ""
        assert r.pick_number == 0
        assert r.pick_numbers == []
        assert r.approval is None
        assert r.accumulated_items == []
        assert r.accumulation_done is False
        assert r.action == ""
        assert r.action_params == {}
        assert r.cost == 0.0
        assert r.fast_path is False
        assert r.user_signals == []


# ── Datetime question detection ──────────────────────────────────────


class TestDatetimeQuestion:

    @pytest.mark.parametrize("msg", [
        "what day is it",
        "today's date",
        "current date",
        "what's the date",
        "what time is it now",
        "what is today",
    ])
    def test_matches(self, msg):
        assert _is_datetime_question(msg) is True

    @pytest.mark.parametrize("msg", [
        "hello",
        "set a timer",
        "what is python",
        "",
    ])
    def test_non_matches(self, msg):
        assert _is_datetime_question(msg) is False


# ── Screenshot request detection ─────────────────────────────────────


class TestScreenshotRequest:

    @pytest.mark.parametrize("msg", [
        "take a screenshot",
        "screenshot",
        "capture the screen",
        "what's on my screen",
        "screen grab",
        "printscreen",
    ])
    def test_matches(self, msg):
        assert _is_screenshot_request(msg) is True

    @pytest.mark.parametrize("msg", [
        "hello",
        "take a photo",
        "screen resolution",
        "",
    ])
    def test_non_matches(self, msg):
        assert _is_screenshot_request(msg) is False


# ── Image generation prompt extraction ────────────────────────────────


class TestExtractImagePrompt:

    def test_simple_generate(self):
        result = _extract_image_prompt(
            "generate an image of a cat", "Generate an image of a cat",
        )
        assert result is not None
        prompt, count, model = result
        assert prompt == "a cat"
        assert count == 1
        assert model is None

    def test_draw_command(self):
        result = _extract_image_prompt("draw a sunset", "Draw a sunset")
        assert result is not None
        assert result[0] == "a sunset"

    def test_count_pattern(self):
        result = _extract_image_prompt(
            "generate 3 images of a dog", "Generate 3 images of a dog",
        )
        assert result is not None
        assert result[0] == "a dog"
        assert result[1] == 3

    def test_count_capped_at_10(self):
        result = _extract_image_prompt(
            "generate 99 images of a dog", "Generate 99 images of a dog",
        )
        assert result is not None
        assert result[1] == 10

    def test_too_short_prompt(self):
        result = _extract_image_prompt(
            "generate an image of hi", "Generate an image of hi",
        )
        # "hi" is only 2 chars, below minimum of 3
        assert result is None

    def test_no_match(self):
        result = _extract_image_prompt("hello world", "Hello world")
        assert result is None

    def test_create_a_picture(self):
        result = _extract_image_prompt(
            "create a picture of mountains", "Create a picture of mountains",
        )
        assert result is not None
        assert result[0] == "mountains"


# ── Slash command handling ───────────────────────────────────────────


class TestSlashCommands:

    def test_goal_command(self):
        gm = MagicMock()
        result = _handle_slash_command("/goal build a tool", "/goal build a tool", gm)
        assert result is not None
        assert result.action == "create_goal"
        assert result.action_params["description"] == "build a tool"

    def test_goals_command(self):
        result = _handle_slash_command("/goals", "/goals", None)
        assert result is not None
        assert result.action == "goals_status"

    def test_status_command(self):
        result = _handle_slash_command("/status", "/status", None)
        assert result.action == "system_status"

    def test_cost_command(self):
        result = _handle_slash_command("/cost", "/cost", None)
        assert result.action == "cost_report"

    def test_help_command(self):
        result = _handle_slash_command("/help", "/help", None)
        assert result.action == "help"

    def test_help_shortcut(self):
        result = _handle_slash_command("/h", "/h", None)
        assert result.action == "help"

    def test_test_command_quick(self):
        result = _handle_slash_command("/test", "/test", None)
        assert result.action == "run_tests"
        assert result.action_params["mode"] == "quick"

    def test_test_command_full(self):
        result = _handle_slash_command("/test full", "/test full", None)
        assert result.action == "run_tests"
        assert result.action_params["mode"] == "full"

    def test_unknown_command(self):
        result = _handle_slash_command("/foobar", "/foobar", None)
        assert result is not None
        assert result.action == "unknown_command"
        assert "/foobar" in result.answer

    def test_non_slash_returns_none(self):
        result = _handle_slash_command("hello", "hello", None)
        assert result is None


# ── Local fast-paths integration ─────────────────────────────────────


class TestCheckLocalFastPaths:

    def test_slash_command(self):
        result = _check_local_fast_paths("/status", "/status", None)
        assert result is not None
        assert result.fast_path is True

    def test_datetime_question(self):
        result = _check_local_fast_paths("what time is it", "what time is it")
        assert result is not None
        assert result.fast_path is True
        assert ":" in result.answer  # Contains time

    def test_screenshot_request(self):
        result = _check_local_fast_paths("take a screenshot", "take a screenshot")
        assert result is not None
        assert result.action == "screenshot"

    def test_image_gen(self):
        # _check_local_fast_paths(message, msg_lower, ...) — msg_lower must be lowercase
        result = _check_local_fast_paths(
            "Generate an image of a cat", "generate an image of a cat",
        )
        assert result is not None
        assert result.action == "generate_image"
        assert result.action_params["prompt"] == "a cat"

    def test_normal_message_no_match(self):
        result = _check_local_fast_paths("hello there", "hello there")
        assert result is None


# ── Deferred request detection ────────────────────────────────────────


class TestDeferredRequest:

    def test_remind_me_to(self):
        result = _is_deferred_request("remind me to check the weather tomorrow")
        assert result is not None
        assert "check the weather" in result

    def test_when_you_have_time(self):
        result = _is_deferred_request("when you have time look into Python web frameworks")
        assert result is not None
        assert "look into" in result

    def test_no_rush(self):
        result = _is_deferred_request("no rush but research the best database for this project")
        assert result is not None

    def test_too_short(self):
        result = _is_deferred_request("remind me x")
        assert result is None

    def test_too_long(self):
        result = _is_deferred_request("x" * 501)
        assert result is None

    def test_empty(self):
        result = _is_deferred_request("")
        assert result is None

    def test_normal_message(self):
        result = _is_deferred_request("build me a web scraper")
        assert result is None

    def test_dont_forget(self):
        result = _is_deferred_request("don't forget to update the config file")
        assert result is not None
        assert "update" in result


# ── Accumulation state ────────────────────────────────────────────────


class TestAccumulationState:

    def setup_method(self):
        clear_accumulation()

    def teardown_method(self):
        clear_accumulation()

    def test_start_and_get(self):
        start_accumulation("task_1", "List your supplements")
        state = get_accumulation_state()
        assert state is not None
        assert state.task_id == "task_1"
        assert state.prompt == "List your supplements"
        assert state.items == []

    def test_clear(self):
        start_accumulation("task_1", "prompt")
        clear_accumulation()
        assert get_accumulation_state() is None

    def test_timeout_detection(self):
        state = _AccumulationState("task_1", "prompt")
        assert state.is_timed_out() is False
        state.last_item_at = time.time() - 130
        assert state.is_timed_out() is True

    def test_silence_timeout_value(self):
        assert _AccumulationState.SILENCE_TIMEOUT == 120


# ── ContextState ──────────────────────────────────────────────────────


class TestContextState:

    def test_defaults(self):
        ctx = ContextState()
        assert ctx.pending_suggestions == []
        assert ctx.recent_suggestions == []
        assert ctx.pending_approval is False
        assert ctx.pending_question is False
        assert ctx.active_goals == []
        assert ctx.accumulating is False
        assert ctx.accumulation_prompt == ""
        assert ctx.accumulated_items == []


# ── Prompt building ──────────────────────────────────────────────────


class TestBuildRouterPrompt:

    def test_basic_prompt(self):
        ctx = ContextState()
        prompt = _build_router_prompt("hello", ctx)
        assert 'Message: "hello"' in prompt
        assert "Classify this message" in prompt

    def test_with_suggestions(self):
        ctx = ContextState(pending_suggestions=["Option A", "Option B"])
        prompt = _build_router_prompt("1", ctx)
        assert "Pending suggestions" in prompt
        assert "Option A" in prompt
        assert "Option B" in prompt

    def test_with_recent_suggestions(self):
        ctx = ContextState(recent_suggestions=["Old idea 1"])
        prompt = _build_router_prompt("that old idea", ctx)
        assert "Recently suggested" in prompt
        assert "Old idea 1" in prompt

    def test_with_approval_pending(self):
        ctx = ContextState(pending_approval=True)
        prompt = _build_router_prompt("yes", ctx)
        assert "Pending approval" in prompt

    def test_with_pending_question(self):
        ctx = ContextState(pending_question=True)
        prompt = _build_router_prompt("42", ctx)
        assert "Pending question" in prompt

    def test_with_accumulation(self):
        ctx = ContextState(
            accumulating=True,
            accumulation_prompt="List items",
            accumulated_items=["item1"],
        )
        prompt = _build_router_prompt("item2", ctx)
        assert "Accumulating" in prompt
        assert "List items" in prompt

    def test_with_history(self):
        ctx = ContextState()
        prompt = _build_router_prompt("hello", ctx, history_snippet="Jesse: hi\nArchi: hey")
        assert "Recent conversation" in prompt
        assert "Jesse: hi" in prompt

    def test_with_user_model(self):
        ctx = ContextState()
        prompt = _build_router_prompt("hello", ctx, user_model_context="Prefers concise responses")
        assert "Prefers concise" in prompt


# ── Response parsing ─────────────────────────────────────────────────


class TestParseRouterResponse:

    def test_new_request_easy(self):
        parsed = {"intent": "new_request", "tier": "easy", "answer": "Hello!"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "new_request"
        assert result.tier == "easy"
        assert result.answer == "Hello!"

    def test_new_request_complex(self):
        parsed = {"intent": "new_request", "tier": "complex", "complexity": "goal"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "complex"
        assert result.complexity == "goal"

    def test_complex_default_complexity(self):
        """Complex tier with no complexity defaults to 'goal'."""
        parsed = {"intent": "new_request", "tier": "complex"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.complexity == "goal"

    def test_suggestion_pick(self):
        parsed = {"intent": "suggestion_pick", "pick_number": 2}
        ctx = ContextState(pending_suggestions=["A", "B", "C"])
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "suggestion_pick"
        assert result.pick_number == 2
        assert result.pick_numbers == [2]

    def test_suggestion_pick_multi(self):
        parsed = {"intent": "suggestion_pick", "pick_numbers": [1, 3]}
        ctx = ContextState(pending_suggestions=["A", "B", "C"])
        result = _parse_router_response(parsed, ctx)
        assert result.pick_numbers == [1, 3]
        assert result.pick_number == 1  # First from list

    def test_suggestion_pick_validates_range(self):
        parsed = {"intent": "suggestion_pick", "pick_number": 5}
        ctx = ContextState(pending_suggestions=["A", "B"])
        result = _parse_router_response(parsed, ctx)
        assert result.pick_number == 0  # Out of range

    def test_affirmation_with_suggestions(self):
        parsed = {"intent": "affirmation"}
        ctx = ContextState(pending_suggestions=["A"])
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "suggestion_pick"
        assert result.pick_number == 1

    def test_affirmation_with_approval(self):
        parsed = {"intent": "affirmation"}
        ctx = ContextState(pending_approval=True)
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "approval"
        assert result.approval is True

    def test_affirmation_with_question(self):
        parsed = {"intent": "affirmation"}
        ctx = ContextState(pending_question=True)
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "question_reply"

    def test_approval_true(self):
        parsed = {"intent": "approval", "approval": True}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.approval is True

    def test_approval_false(self):
        parsed = {"intent": "approval", "approval": False}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.approval is False

    def test_approval_inferred_from_answer(self):
        parsed = {"intent": "approval", "answer": "no, don't do that"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.approval is False

    def test_approval_inferred_positive(self):
        parsed = {"intent": "approval", "answer": "sure, go ahead"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.approval is True

    def test_cancel_is_easy(self):
        parsed = {"intent": "cancel", "tier": "complex"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "easy"

    def test_greeting_is_easy(self):
        parsed = {"intent": "greeting", "tier": "complex", "answer": "Hey!"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "easy"

    def test_accumulation_item(self):
        parsed = {
            "intent": "accumulation",
            "accumulation_item": "Vitamin D",
            "accumulation_done": False,
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "accumulation"
        assert result.accumulated_items == ["Vitamin D"]
        assert result.accumulation_done is False

    def test_accumulation_done(self):
        parsed = {
            "intent": "accumulation",
            "accumulation_item": "",
            "accumulation_done": True,
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.accumulation_done is True

    def test_easy_tier_without_answer_upgrades(self):
        """Easy tier with no answer and non-special intent → upgrades to complex."""
        parsed = {"intent": "new_request", "tier": "easy", "answer": ""}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "complex"

    def test_user_signals_passthrough(self):
        parsed = {
            "intent": "new_request", "tier": "easy", "answer": "ok",
            "user_signals": [{"type": "preference", "text": "Likes tabs"}],
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert len(result.user_signals) == 1
        assert result.user_signals[0]["text"] == "Likes tabs"

    def test_action_passthrough(self):
        parsed = {
            "intent": "new_request", "tier": "easy", "answer": "ok",
            "action": "create_goal", "action_params": {"description": "test"},
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.action == "create_goal"
        assert result.action_params == {"description": "test"}


# ── Full route() with mocked model ───────────────────────────────────


class TestRoute:

    def _mock_router(self, response_json):
        router = MagicMock()
        router.generate.return_value = {
            "text": json.dumps(response_json),
            "success": True,
            "cost_usd": 0.001,
        }
        return router

    def test_fast_path_slash(self):
        """Slash commands bypass model call entirely."""
        router = MagicMock()
        ctx = ContextState()
        result = route("/status", router, ctx)
        assert result.fast_path is True
        assert result.action == "system_status"
        router.generate.assert_not_called()

    def test_fast_path_datetime(self):
        router = MagicMock()
        ctx = ContextState()
        result = route("what time is it", router, ctx)
        assert result.fast_path is True
        assert ":" in result.answer

    def test_fast_path_deferred(self):
        router = MagicMock()
        ctx = ContextState()
        result = route("remind me to check logs tomorrow", router, ctx)
        assert result.fast_path is True
        assert result.action == "deferred_request"
        router.generate.assert_not_called()

    @patch("src.core.conversational_router.extract_user_signals", create=True)
    @patch("src.core.conversational_router.sync_signals_to_project_context", create=True)
    def test_model_route_easy(self, mock_sync, mock_signals):
        router = self._mock_router({
            "intent": "greeting", "tier": "easy", "answer": "Hey Jesse!",
        })
        ctx = ContextState()
        with patch("src.core.conversational_router.extract_json") as mock_ej:
            mock_ej.return_value = {
                "intent": "greeting", "tier": "easy", "answer": "Hey Jesse!",
            }
            result = route("hello", router, ctx)
        assert result.intent == "greeting"
        assert result.tier == "easy"
        assert result.cost > 0

    def test_model_failure_fallback(self):
        router = MagicMock()
        router.generate.return_value = {
            "success": False, "error": "API down", "cost_usd": 0,
        }
        ctx = ContextState()
        result = route("hello", router, ctx)
        assert result.intent == "new_request"
        assert result.tier == "complex"

    def test_json_parse_failure_retry(self):
        """When first parse fails, route retries with simplified prompt."""
        router = MagicMock()
        # First call: unparseable. Second call: valid.
        router.generate.side_effect = [
            {"text": "not json", "success": True, "cost_usd": 0.001},
            {"text": '{"intent":"greeting","tier":"easy","answer":"hi"}',
             "success": True, "cost_usd": 0.001},
        ]
        ctx = ContextState()
        with patch("src.core.conversational_router.extract_json") as mock_ej:
            mock_ej.side_effect = [
                None,  # First parse fails
                {"intent": "greeting", "tier": "easy", "answer": "hi"},
            ]
            result = route("hello", router, ctx)
        assert router.generate.call_count == 2
        assert result.intent == "greeting"

    def test_accumulation_timeout_fast_path(self):
        """Timed-out accumulation returns items without model call."""
        clear_accumulation()
        start_accumulation("task_1", "List items")
        state = get_accumulation_state()
        state.items = ["item1", "item2"]
        state.last_item_at = time.time() - 130  # Past timeout

        router = MagicMock()
        ctx = ContextState()
        result = route("anything", router, ctx)
        assert result.intent == "accumulation"
        assert result.accumulation_done is True
        assert result.accumulated_items == ["item1", "item2"]
        assert result.fast_path is True
        router.generate.assert_not_called()
        clear_accumulation()


# ── Casual remarks should be classified as easy (not complex) ─────────


class TestCasualRemarksNotActionable:
    """Verify that the Router prompt instructs the model to treat casual
    remarks, musings, and thinking-out-loud as easy-tier, not complex.

    These tests verify that when the model correctly follows the prompt
    instructions, the parsed result is easy tier.  They use pre-built
    model responses (the prompt changes are what guide the real model;
    these tests validate the parsing + dispatch side).
    """

    @pytest.mark.parametrize("message,answer", [
        ("I think we'll have to check on that", "Yeah, we can circle back to that."),
        ("hmm that's interesting", "Right?"),
        ("maybe later", "Sure, whenever you're ready."),
        ("I wonder if that's related to the other issue", "Could be — worth keeping in mind."),
        ("note to self: look into X", "Noted."),
        ("we might need to revisit that", "Agreed, we can revisit later."),
        ("probably should clean that up at some point", "Yeah, no rush."),
        ("could be worth looking into", "Definitely worth a look when we get to it."),
        ("huh, good to know", "Yeah, handy to know."),
        ("I'm going to try restarting it", "Sounds good, let me know how it goes."),
    ])
    def test_casual_remark_parses_as_easy(self, message, answer):
        """Casual remarks should be parsed as easy tier when model classifies correctly."""
        parsed = {
            "intent": "new_request",
            "tier": "easy",
            "answer": answer,
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "easy"
        assert result.answer == answer

    @pytest.mark.parametrize("message,answer", [
        ("I think we'll have to check on that", "Yeah, noted."),
        ("hmm that's interesting", "Right?"),
        ("maybe later", "Sure thing."),
        ("note to self: look into X", "Noted."),
        ("probably should clean that up at some point", "Yeah, no rush."),
    ])
    @patch("src.core.conversational_router.extract_user_signals", create=True)
    @patch("src.core.conversational_router.sync_signals_to_project_context", create=True)
    def test_casual_remark_full_route(self, mock_sync, mock_signals, message, answer):
        """End-to-end: casual remarks route through model and return easy tier."""
        router = MagicMock()
        router.generate.return_value = {
            "text": json.dumps({
                "intent": "new_request",
                "tier": "easy",
                "answer": answer,
            }),
            "success": True,
            "cost_usd": 0.001,
        }
        ctx = ContextState()
        with patch("src.core.conversational_router.extract_json") as mock_ej:
            mock_ej.return_value = {
                "intent": "new_request",
                "tier": "easy",
                "answer": answer,
            }
            result = route(message, router, ctx)
        assert result.tier == "easy"
        assert result.answer == answer

    @pytest.mark.parametrize("message", [
        "Look into why it failed",
        "Can you figure out why it failed?",
        "Check on that for me",
        "Research the best database options",
        "See if you can fix the bug in router.py",
    ])
    def test_actual_requests_stay_complex(self, message):
        """Legitimate requests should remain complex tier."""
        parsed = {
            "intent": "new_request",
            "tier": "complex",
            "complexity": "goal",
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "complex"
        assert result.complexity == "goal"

    def test_prompt_contains_thinking_out_loud_section(self):
        """The router system prompt should contain guidance on casual remarks."""
        from src.core.conversational_router import _ROUTER_SYSTEM
        assert "THINKING OUT LOUD" in _ROUTER_SYSTEM
        assert "NOT ACTIONABLE" in _ROUTER_SYSTEM
        assert "I think we'll have to check on that" in _ROUTER_SYSTEM
        assert "note to self" in _ROUTER_SYSTEM
        assert "RULE OF THUMB" in _ROUTER_SYSTEM
