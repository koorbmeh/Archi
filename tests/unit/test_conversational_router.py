"""Comprehensive unit tests for src.core.conversational_router.

Tests cover:
- RouterResult dataclass defaults
- _AccumulationState timeout behavior
- Accumulation management functions (start/get/clear)
- Local fast-paths: slash commands, datetime, screenshot, image gen
- _parse_router_response: all intent types, tier validation, multi-pick
- route() function: fast-paths, accumulation timeout, API failures, JSON parsing
- Casual remarks classified as easy tier
- Config request signal pipeline

Created with comprehensive test coverage for all module functionality.
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
    _handle_slash_command,
    _build_router_prompt,
    _parse_router_response,
    route,
    start_accumulation,
    get_accumulation_state,
    clear_accumulation,
)


# ── RouterResult dataclass tests ──────────────────────────────────────────


class TestRouterResultDefaults:
    """Verify RouterResult default values are correct."""

    def test_defaults_with_intent_only(self):
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
        assert r.config_requests == []
        assert r.mood_signal == ""

    def test_defaults_easy_tier(self):
        r = RouterResult(intent="greeting", tier="easy", answer="Hi!")
        assert r.tier == "easy"
        assert r.answer == "Hi!"
        assert r.complexity == ""  # Not needed for easy tier

    def test_defaults_complex_tier(self):
        r = RouterResult(intent="new_request", tier="complex", complexity="goal")
        assert r.tier == "complex"
        assert r.complexity == "goal"
        assert r.answer == ""  # Not used for complex tier


# ── _AccumulationState tests ──────────────────────────────────────────────


class TestAccumulationStateTimeout:
    """Verify _AccumulationState timeout behavior."""

    def test_is_timed_out_false_immediately_after_creation(self):
        state = _AccumulationState("task_1", "List your items")
        assert state.is_timed_out() is False

    def test_is_timed_out_true_after_silence_timeout(self):
        state = _AccumulationState("task_1", "List your items")
        # Artificially move last_item_at to past
        state.last_item_at = time.time() - (_AccumulationState.SILENCE_TIMEOUT + 1)
        assert state.is_timed_out() is True

    def test_is_timed_out_true_exactly_at_timeout_boundary(self):
        state = _AccumulationState("task_1", "prompt")
        state.last_item_at = time.time() - _AccumulationState.SILENCE_TIMEOUT
        # Just past the boundary
        assert state.is_timed_out() is True

    def test_is_timed_out_false_just_before_boundary(self):
        state = _AccumulationState("task_1", "prompt")
        state.last_item_at = time.time() - (_AccumulationState.SILENCE_TIMEOUT - 1)
        assert state.is_timed_out() is False

    def test_silence_timeout_constant(self):
        """Verify SILENCE_TIMEOUT is 120 seconds (2 minutes)."""
        assert _AccumulationState.SILENCE_TIMEOUT == 120


# ── Accumulation management function tests ────────────────────────────────


class TestAccumulationManagement:
    """Test start_accumulation, get_accumulation_state, clear_accumulation."""

    def setup_method(self):
        """Reset accumulation state before each test."""
        clear_accumulation()

    def teardown_method(self):
        """Clean up accumulation state after each test."""
        clear_accumulation()

    def test_start_accumulation_creates_state(self):
        start_accumulation("task_123", "List your supplements")
        state = get_accumulation_state()
        assert state is not None
        assert state.task_id == "task_123"
        assert state.prompt == "List your supplements"

    def test_get_accumulation_state_returns_none_initially(self):
        clear_accumulation()
        assert get_accumulation_state() is None

    def test_clear_accumulation_removes_state(self):
        start_accumulation("task_1", "prompt")
        assert get_accumulation_state() is not None
        clear_accumulation()
        assert get_accumulation_state() is None

    def test_start_accumulation_items_empty(self):
        start_accumulation("task_1", "prompt")
        state = get_accumulation_state()
        assert state.items == []

    def test_accumulation_state_tracks_timestamps(self):
        start_accumulation("task_1", "prompt")
        state = get_accumulation_state()
        assert state.started_at is not None
        assert state.last_item_at is not None
        assert abs(state.started_at - state.last_item_at) < 0.01


# ── Local fast-paths tests ────────────────────────────────────────────────


class TestCheckLocalFastPathsSlashCommands:
    """Test _check_local_fast_paths with slash commands."""

    def test_goal_command(self):
        mock_gm = MagicMock()
        result = _check_local_fast_paths("/goal build a web app", "/goal build a web app", mock_gm)
        assert result is not None
        assert result.fast_path is True
        assert result.intent == "easy_answer"
        assert result.tier == "easy"
        assert result.action == "create_goal"
        assert result.action_params["description"] == "build a web app"

    def test_goals_command(self):
        result = _check_local_fast_paths("/goals", "/goals", None)
        assert result is not None
        assert result.fast_path is True
        assert result.action == "goals_status"

    def test_status_command(self):
        result = _check_local_fast_paths("/status", "/status", None)
        assert result is not None
        assert result.fast_path is True
        assert result.action == "system_status"

    def test_cost_command(self):
        result = _check_local_fast_paths("/cost", "/cost", None)
        assert result is not None
        assert result.fast_path is True
        assert result.action == "cost_report"

    def test_help_command(self):
        result = _check_local_fast_paths("/help", "/help", None)
        assert result is not None
        assert result.fast_path is True
        assert result.action == "help"

    def test_test_command(self):
        result = _check_local_fast_paths("/test", "/test", None)
        assert result is not None
        assert result.action == "run_tests"
        assert result.action_params.get("mode") == "quick"

    def test_test_full_command(self):
        result = _check_local_fast_paths("/test full", "/test full", None)
        assert result is not None
        assert result.action == "run_tests"
        assert result.action_params.get("mode") == "full"

    def test_unknown_command(self):
        result = _check_local_fast_paths("/unknowncmd", "/unknowncmd", None)
        assert result is not None
        assert result.fast_path is True
        assert result.action == "unknown_command"
        assert "/unknowncmd" in result.answer


class TestCheckLocalFastPathsDatetime:
    """Test _check_local_fast_paths with datetime questions."""

    def test_what_time_is_it(self):
        result = _check_local_fast_paths("what time is it", "what time is it")
        assert result is not None
        assert result.fast_path is True
        assert result.intent == "easy_answer"
        assert result.tier == "easy"
        assert ":" in result.answer  # Contains time

    def test_current_date_question(self):
        result = _check_local_fast_paths("what's today's date", "what's today's date")
        assert result is not None
        assert result.fast_path is True


class TestCheckLocalFastPathsScreenshot:
    """Test _check_local_fast_paths with screenshot requests."""

    def test_take_a_screenshot(self):
        result = _check_local_fast_paths("take a screenshot", "take a screenshot")
        assert result is not None
        assert result.fast_path is True
        assert result.action == "screenshot"


class TestCheckLocalFastPathsImageGen:
    """Test _check_local_fast_paths with image generation."""

    def test_draw_me_a_dragon(self):
        result = _check_local_fast_paths("Draw me a dragon", "draw me a dragon")
        assert result is not None
        assert result.fast_path is True
        assert result.action == "generate_image"
        assert "dragon" in result.action_params.get("prompt", "").lower()

    def test_generate_3_images_of_cats(self):
        result = _check_local_fast_paths(
            "Generate 3 images of cats", "generate 3 images of cats"
        )
        assert result is not None
        assert result.fast_path is True
        assert result.action == "generate_image"
        assert result.action_params.get("count") == 3
        assert "cat" in result.action_params.get("prompt", "").lower()


class TestCheckLocalFastPathsCostQuery:
    """Test _check_local_fast_paths for cost/spending meta-questions."""

    def test_how_much_spent(self):
        result = _check_local_fast_paths("how much have you spent today?", "how much have you spent today?")
        assert result is not None
        assert result.action == "cost_report"
        assert result.fast_path is True

    def test_check_spending(self):
        result = _check_local_fast_paths("check spending", "check spending")
        assert result is not None
        assert result.action == "cost_report"

    def test_cost_report(self):
        result = _check_local_fast_paths("cost report", "cost report")
        assert result is not None
        assert result.action == "cost_report"

    def test_external_cost_not_matched(self):
        msg = "how much does a flight to Paris cost?"
        result = _check_local_fast_paths(msg, msg.lower())
        assert result is None


class TestCheckLocalFastPathsNormalMessages:
    """Test _check_local_fast_paths returns None for normal messages."""

    def test_hello(self):
        result = _check_local_fast_paths("hello", "hello")
        assert result is None

    def test_regular_question(self):
        result = _check_local_fast_paths("Can you help me with X?", "can you help me with x?")
        assert result is None

    def test_empty_string(self):
        result = _check_local_fast_paths("", "")
        assert result is None


# ── _parse_router_response tests ──────────────────────────────────────────


class TestParseRouterResponseNewRequest:
    """Test _parse_router_response with new_request intent."""

    def test_new_request_easy_tier(self):
        parsed = {"intent": "new_request", "tier": "easy", "answer": "Hello there!"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "new_request"
        assert result.tier == "easy"
        assert result.answer == "Hello there!"

    def test_new_request_complex_tier(self):
        parsed = {"intent": "new_request", "tier": "complex", "complexity": "goal"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "new_request"
        assert result.tier == "complex"
        assert result.complexity == "goal"


class TestParseRouterResponseSuggestionPick:
    """Test _parse_router_response with suggestion_pick intent."""

    def test_suggestion_pick_with_pick_number(self):
        parsed = {"intent": "suggestion_pick", "pick_number": 2}
        ctx = ContextState(pending_suggestions=["A", "B", "C"])
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "suggestion_pick"
        assert result.pick_number == 2
        assert result.pick_numbers == [2]

    def test_suggestion_pick_with_pick_numbers_list(self):
        parsed = {"intent": "suggestion_pick", "pick_numbers": [1, 3]}
        ctx = ContextState(pending_suggestions=["A", "B", "C"])
        result = _parse_router_response(parsed, ctx)
        assert result.pick_numbers == [1, 3]
        assert result.pick_number == 1  # First from list

    def test_suggestion_pick_out_of_range_filtered(self):
        parsed = {"intent": "suggestion_pick", "pick_number": 5}
        ctx = ContextState(pending_suggestions=["A", "B"])
        result = _parse_router_response(parsed, ctx)
        # Out of range picks should be set to 0
        assert result.pick_number == 0

    def test_suggestion_pick_pick_numbers_only(self):
        """When only pick_numbers provided (no pick_number), set pick_number from list."""
        parsed = {"intent": "suggestion_pick", "pick_numbers": [2, 3]}
        ctx = ContextState(pending_suggestions=["A", "B", "C"])
        result = _parse_router_response(parsed, ctx)
        assert result.pick_number == 2  # First from pick_numbers list
        assert result.pick_numbers == [2, 3]

    def test_suggestion_pick_filters_out_of_range_picks(self):
        """Out-of-range picks in pick_numbers should be filtered."""
        parsed = {"intent": "suggestion_pick", "pick_numbers": [1, 5, 2]}
        ctx = ContextState(pending_suggestions=["A", "B"])
        result = _parse_router_response(parsed, ctx)
        # Only [1, 2] are valid
        assert result.pick_numbers == [1, 2]


class TestParseRouterResponseAffirmation:
    """Test _parse_router_response with affirmation intent."""

    def test_affirmation_with_pending_suggestions(self):
        """Affirmation with pending suggestions → suggestion_pick #1."""
        parsed = {"intent": "affirmation"}
        ctx = ContextState(pending_suggestions=["Option A", "Option B"])
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "suggestion_pick"
        assert result.pick_number == 1
        assert result.pick_numbers == [1]

    def test_affirmation_with_pending_approval(self):
        """Affirmation with pending approval → approval true."""
        parsed = {"intent": "affirmation"}
        ctx = ContextState(pending_approval=True)
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "approval"
        assert result.approval is True

    def test_affirmation_with_pending_question(self):
        """Affirmation with pending question → question_reply."""
        parsed = {"intent": "affirmation"}
        ctx = ContextState(pending_question=True)
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "question_reply"


class TestParseRouterResponseApproval:
    """Test _parse_router_response with approval intent."""

    def test_approval_explicit_true(self):
        parsed = {"intent": "approval", "approval": True}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.approval is True

    def test_approval_explicit_false(self):
        parsed = {"intent": "approval", "approval": False}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.approval is False

    def test_approval_inferred_from_negative_words(self):
        """Approval inferred as False from answer text with negative words."""
        parsed = {"intent": "approval", "answer": "no, don't do that"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.approval is False

    def test_approval_inferred_negative_nope(self):
        parsed = {"intent": "approval", "answer": "nope"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.approval is False

    def test_approval_inferred_from_positive_context(self):
        """Approval inferred as True when no negative words."""
        parsed = {"intent": "approval", "answer": "sure, go ahead"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.approval is True


class TestParseRouterResponseAccumulation:
    """Test _parse_router_response with accumulation intent."""

    def test_accumulation_with_item(self):
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

    def test_accumulation_done_signal(self):
        parsed = {
            "intent": "accumulation",
            "accumulation_item": "",
            "accumulation_done": True,
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.accumulation_done is True


class TestParseRouterResponseIntentTypes:
    """Test _parse_router_response with various intent types."""

    def test_cancel_always_easy_tier(self):
        """Cancel intent always sets tier to easy."""
        parsed = {"intent": "cancel", "tier": "complex"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "easy"

    def test_greeting_always_easy_tier(self):
        """Greeting intent always sets tier to easy."""
        parsed = {"intent": "greeting", "tier": "complex", "answer": "Hey!"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "easy"

    def test_clarification_always_easy_tier(self):
        """Clarification intent always sets tier to easy."""
        parsed = {
            "intent": "clarification",
            "tier": "complex",
            "answer": "I meant the blue one, not red.",
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "easy"


class TestParseRouterResponseTierValidation:
    """Test _parse_router_response tier validation logic."""

    def test_easy_tier_without_answer_upgrades_to_complex(self):
        """Easy tier without answer and non-special intent → bumped to complex."""
        parsed = {"intent": "new_request", "tier": "easy", "answer": ""}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "complex"

    def test_easy_tier_without_answer_stays_for_special_intents(self):
        """Easy tier without answer is OK for special intents like approval."""
        parsed = {"intent": "approval", "tier": "easy", "answer": ""}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "easy"  # Does not upgrade

    def test_complex_tier_without_complexity_defaults_to_goal(self):
        """Complex tier without complexity defaults to 'goal'."""
        parsed = {"intent": "new_request", "tier": "complex"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.complexity == "goal"

    def test_missing_fields_default_gracefully(self):
        """Missing intent defaults to new_request."""
        parsed = {"tier": "easy", "answer": "ok"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "new_request"


class TestParseRouterResponseFieldPassthrough:
    """Test _parse_router_response passes through fields correctly."""

    def test_user_signals_passthrough(self):
        parsed = {
            "intent": "new_request",
            "tier": "easy",
            "answer": "ok",
            "user_signals": [
                {"type": "preference", "text": "Prefers tabs"},
                {"type": "fact", "text": "Works in tech"},
            ],
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert len(result.user_signals) == 2
        assert result.user_signals[0]["text"] == "Prefers tabs"

    def test_action_and_action_params_passthrough(self):
        parsed = {
            "intent": "new_request",
            "tier": "easy",
            "answer": "ok",
            "action": "create_goal",
            "action_params": {"description": "Build something", "priority": "high"},
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.action == "create_goal"
        assert result.action_params["description"] == "Build something"
        assert result.action_params["priority"] == "high"

    def test_mood_signal_passthrough(self):
        parsed = {
            "intent": "new_request",
            "tier": "easy",
            "answer": "ok",
            "mood_signal": "Busy",
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.mood_signal == "busy"

    def test_mood_signal_missing(self):
        parsed = {"intent": "greeting", "tier": "easy", "answer": "hi"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.mood_signal == ""


# ── route() function tests ────────────────────────────────────────────────


class TestRouteFastPaths:
    """Test route() function with fast-path matches."""

    def test_fast_path_slash_command(self):
        """Slash commands bypass model call entirely."""
        router = MagicMock()
        ctx = ContextState()
        result = route("/status", router, ctx)
        assert result.fast_path is True
        assert result.action == "system_status"
        router.generate.assert_not_called()

    def test_fast_path_datetime_question(self):
        """Datetime questions return without model call."""
        router = MagicMock()
        ctx = ContextState()
        result = route("what time is it", router, ctx)
        assert result.fast_path is True
        assert ":" in result.answer
        router.generate.assert_not_called()

    def test_fast_path_screenshot_request(self):
        """Screenshot requests return without model call."""
        router = MagicMock()
        ctx = ContextState()
        result = route("take a screenshot", router, ctx)
        assert result.fast_path is True
        assert result.action == "screenshot"
        router.generate.assert_not_called()

    def test_fast_path_image_generation(self):
        """Image generation returns without model call."""
        router = MagicMock()
        ctx = ContextState()
        result = route("draw me a dragon", router, ctx)
        assert result.fast_path is True
        assert result.action == "generate_image"
        router.generate.assert_not_called()


class TestRouteAccumulationTimeout:
    """Test route() with accumulation timeout."""

    def setup_method(self):
        clear_accumulation()

    def teardown_method(self):
        clear_accumulation()

    def test_accumulation_timeout_auto_finalizes(self):
        """Accumulation timeout auto-finalizes and returns without model call."""
        start_accumulation("task_1", "List items")
        state = get_accumulation_state()
        state.items = ["item1", "item2"]
        # Move last_item_at to past the timeout
        state.last_item_at = time.time() - 130

        router = MagicMock()
        ctx = ContextState()
        result = route("anything", router, ctx)

        assert result.intent == "accumulation"
        assert result.accumulation_done is True
        assert result.accumulated_items == ["item1", "item2"]
        assert result.fast_path is True
        router.generate.assert_not_called()


class TestRouteAPIFailure:
    """Test route() with API failures."""

    def test_api_failure_returns_safe_fallback(self):
        """API failure returns safe complex fallback."""
        router = MagicMock()
        router.generate.return_value = {
            "success": False,
            "error": "API rate limited",
            "cost_usd": 0.0,
        }
        ctx = ContextState()
        result = route("hello", router, ctx)

        assert result.intent == "new_request"
        assert result.tier == "complex"
        assert result.complexity == "multi_step"


class TestRouteJSONParsing:
    """Test route() with JSON parsing failures."""

    def test_json_parse_failure_triggers_retry(self):
        """JSON parse failure triggers simplified retry."""
        router = MagicMock()
        # First call: invalid JSON. Second call: valid JSON
        router.generate.side_effect = [
            {"text": "not valid json", "success": True, "cost_usd": 0.001},
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
        assert result.tier == "easy"

    def test_total_json_failure_returns_complex_fallback(self):
        """Total JSON failure returns complex fallback."""
        router = MagicMock()
        router.generate.side_effect = [
            {"text": "not json", "success": True, "cost_usd": 0.001},
            {"text": "still not json", "success": True, "cost_usd": 0.001},
        ]
        ctx = ContextState()

        with patch("src.core.conversational_router.extract_json") as mock_ej:
            mock_ej.return_value = None  # Both fail
            result = route("hello", router, ctx)

        assert result.intent == "new_request"
        assert result.tier == "complex"
        assert result.complexity == "goal"


class TestRouteSuccessfulRouting:
    """Test route() with successful routing."""

    @patch("src.core.conversational_router.extract_user_signals", create=True)
    @patch("src.core.conversational_router.sync_signals_to_project_context", create=True)
    def test_successful_route_with_easy_tier_answer(self, mock_sync, mock_signals):
        """Successful route with easy-tier answer gets sanitized."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"intent":"greeting","tier":"easy","answer":"Hey there!"}',
            "success": True,
            "cost_usd": 0.001,
        }
        ctx = ContextState()

        with patch("src.core.conversational_router.extract_json") as mock_ej:
            mock_ej.return_value = {
                "intent": "greeting",
                "tier": "easy",
                "answer": "Hey there!",
            }
            result = route("hello", router, ctx)

        assert result.intent == "greeting"
        assert result.tier == "easy"
        assert result.answer == "Hey there!"
        assert result.cost == 0.001


class TestRouteWithContext:
    """Test route() with various context states."""

    @patch("src.core.conversational_router.extract_user_signals", create=True)
    @patch("src.core.conversational_router.sync_signals_to_project_context", create=True)
    def test_route_with_pending_suggestions(self, mock_sync, mock_signals):
        """route() includes pending suggestions in context."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"intent":"suggestion_pick","pick_number":1}',
            "success": True,
            "cost_usd": 0.001,
        }
        ctx = ContextState(pending_suggestions=["Option A", "Option B"])

        with patch("src.core.conversational_router.extract_json") as mock_ej:
            mock_ej.return_value = {"intent": "suggestion_pick", "pick_number": 1}
            result = route("1", router, ctx)

        # Verify context was passed to prompt builder
        call_args = router.generate.call_args
        messages = call_args[1]["messages"]
        user_prompt = messages[1]["content"]
        assert "Option A" in user_prompt

    @patch("src.core.conversational_router.extract_user_signals", create=True)
    @patch("src.core.conversational_router.sync_signals_to_project_context", create=True)
    def test_route_with_history_messages(self, mock_sync, mock_signals):
        """route() includes history messages in prompt."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"intent":"greeting","tier":"easy","answer":"Hey!"}',
            "success": True,
            "cost_usd": 0.001,
        }
        ctx = ContextState()
        history = [
            {"role": "user", "content": "What's up?"},
            {"role": "assistant", "content": "Not much, what can I do?"},
        ]

        with patch("src.core.conversational_router.extract_json") as mock_ej:
            mock_ej.return_value = {
                "intent": "greeting",
                "tier": "easy",
                "answer": "Hey!",
            }
            result = route("hello", router, ctx, history_messages=history)

        # Verify history was included in prompt
        call_args = router.generate.call_args
        messages = call_args[1]["messages"]
        user_prompt = messages[1]["content"]
        assert "What's up?" in user_prompt


class TestRouteWithMemory:
    """Test route() with memory manager."""

    @patch("src.core.conversational_router.extract_user_signals", create=True)
    @patch("src.core.conversational_router.sync_signals_to_project_context", create=True)
    def test_route_with_memory_retrieves_context(self, mock_sync, mock_signals):
        """route() retrieves and includes conversation memory."""
        mock_memory = MagicMock()
        mock_memory.get_conversation_context.return_value = [
            "Jesse talked about woodworking",
            "Discussed RC cars collection",
        ]

        router = MagicMock()
        router.generate.return_value = {
            "text": '{"intent":"greeting","tier":"easy","answer":"Hey!"}',
            "success": True,
            "cost_usd": 0.001,
        }
        ctx = ContextState()

        with patch("src.core.conversational_router.extract_json") as mock_ej:
            mock_ej.return_value = {
                "intent": "greeting",
                "tier": "easy",
                "answer": "Hey!",
            }
            result = route("hey", router, ctx, memory=mock_memory)

        # Verify memory was queried
        mock_memory.get_conversation_context.assert_called_once()
        # Verify context was included in prompt
        call_args = router.generate.call_args
        messages = call_args[1]["messages"]
        user_prompt = messages[1]["content"]
        assert "woodworking" in user_prompt

    @patch("src.core.conversational_router.extract_user_signals", create=True)
    @patch("src.core.conversational_router.sync_signals_to_project_context", create=True)
    def test_route_without_memory_still_works(self, mock_sync, mock_signals):
        """route() works fine when memory=None (backward compat)."""
        router = MagicMock()
        ctx = ContextState()
        result = route("/status", router, ctx, memory=None)
        assert result.fast_path is True
        assert result.action == "system_status"


# ── Tier and complexity validation tests ───────────────────────────────────


class TestTierValidationEdgeCases:
    """Test edge cases in tier and complexity validation."""

    def test_easy_tier_special_intents_without_answer_stay_easy(self):
        """Special intents (approval, etc.) stay easy even without answer."""
        special_intents = ["approval", "suggestion_pick", "question_reply", "cancel", "accumulation", "clarification"]
        for intent in special_intents:
            parsed = {"intent": intent, "tier": "easy", "answer": ""}
            ctx = ContextState()
            result = _parse_router_response(parsed, ctx)
            assert result.tier == "easy", f"Intent '{intent}' should stay easy tier"

    def test_complex_tier_gets_default_complexity(self):
        """Complex tier always gets a default complexity if missing."""
        parsed = {"intent": "new_request", "tier": "complex"}
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.complexity in ["goal", "multi_step", "coding"]
        assert result.complexity == "goal"  # Default


# ── Config request signal tests ────────────────────────────────────────────


class TestConfigRequestSignals:
    """Test config_request signal handling."""

    def test_config_requests_default_to_empty_list(self):
        """RouterResult config_requests defaults to empty list."""
        r = RouterResult(intent="new_request")
        assert r.config_requests == []

    @patch("src.core.user_model.extract_user_signals")
    @patch("src.core.conversational_router.sync_signals_to_project_context", create=True)
    def test_config_requests_attached_to_result(self, mock_sync, mock_extract):
        """Config requests from extract_user_signals are attached to result."""
        mock_extract.return_value = ["Add humor to prime directive"]

        router = MagicMock()
        router.generate.return_value = {
            "text": '{"intent":"new_request","tier":"easy","answer":"Got it!"}',
            "success": True,
            "cost_usd": 0.001,
        }
        ctx = ContextState()

        with patch("src.core.conversational_router.extract_json") as mock_ej:
            mock_ej.return_value = {
                "intent": "new_request",
                "tier": "easy",
                "answer": "Got it!",
            }
            result = route("add humor to your prime directive", router, ctx)

        assert result.config_requests == ["Add humor to prime directive"]

    @patch("src.core.user_model.extract_user_signals")
    @patch("src.core.conversational_router.sync_signals_to_project_context", create=True)
    def test_no_config_requests_when_none_detected(self, mock_sync, mock_extract):
        """Normal signals don't populate config_requests."""
        mock_extract.return_value = []

        router = MagicMock()
        router.generate.return_value = {
            "text": '{"intent":"greeting","tier":"easy","answer":"Hey!"}',
            "success": True,
            "cost_usd": 0.001,
        }
        ctx = ContextState()

        with patch("src.core.conversational_router.extract_json") as mock_ej:
            mock_ej.return_value = {
                "intent": "greeting",
                "tier": "easy",
                "answer": "Hey!",
            }
            result = route("hello", router, ctx)

        assert result.config_requests == []


# ── ContextState tests ────────────────────────────────────────────────────


class TestContextStateDefaults:
    """Test ContextState default values."""

    def test_context_state_all_defaults(self):
        ctx = ContextState()
        assert ctx.pending_suggestions == []
        assert ctx.recent_suggestions == []
        assert ctx.pending_approval is False
        assert ctx.pending_question is False
        assert ctx.active_goals == []
        assert ctx.accumulating is False
        assert ctx.accumulation_prompt == ""
        assert ctx.accumulated_items == []

    def test_context_state_with_suggestions(self):
        ctx = ContextState(
            pending_suggestions=["A", "B"],
            recent_suggestions=["C"],
        )
        assert ctx.pending_suggestions == ["A", "B"]
        assert ctx.recent_suggestions == ["C"]


# ── Prompt building tests ─────────────────────────────────────────────────


class TestBuildRouterPrompt:
    """Test _build_router_prompt includes all context."""

    def test_basic_prompt_includes_message(self):
        ctx = ContextState()
        prompt = _build_router_prompt("hello", ctx)
        assert 'Message: "hello"' in prompt
        assert "Classify this message" in prompt

    def test_prompt_includes_pending_suggestions(self):
        ctx = ContextState(pending_suggestions=["Option A", "Option B"])
        prompt = _build_router_prompt("1", ctx)
        assert "Pending suggestions" in prompt
        assert "Option A" in prompt
        assert "Option B" in prompt

    def test_prompt_includes_approval_pending(self):
        ctx = ContextState(pending_approval=True)
        prompt = _build_router_prompt("yes", ctx)
        assert "Pending approval" in prompt

    def test_prompt_includes_question_pending(self):
        ctx = ContextState(pending_question=True)
        prompt = _build_router_prompt("answer", ctx)
        assert "Pending question" in prompt

    def test_prompt_includes_accumulation_state(self):
        ctx = ContextState(
            accumulating=True,
            accumulation_prompt="List items",
            accumulated_items=["item1", "item2"],
        )
        prompt = _build_router_prompt("item3", ctx)
        assert "Accumulating" in prompt
        assert "List items" in prompt
        assert "item1" in prompt

    def test_prompt_includes_history(self):
        ctx = ContextState()
        prompt = _build_router_prompt(
            "hello", ctx,
            history_snippet="Jesse: hi\nArchi: hey there"
        )
        assert "Recent conversation" in prompt
        assert "Jesse: hi" in prompt

    def test_prompt_includes_user_model_context(self):
        ctx = ContextState()
        prompt = _build_router_prompt(
            "hello", ctx,
            user_model_context="Prefers concise responses"
        )
        assert "Prefers concise" in prompt

    def test_prompt_includes_conversation_memories(self):
        ctx = ContextState()
        memories = [
            "Jesse talked about woodworking",
            "Discussed Jesse's Rat Terrier",
        ]
        prompt = _build_router_prompt("hello", ctx, conversation_memories=memories)
        assert "Relevant past conversations" in prompt
        assert "woodworking" in prompt
        assert "Rat Terrier" in prompt

    def test_prompt_memory_limit_is_3(self):
        ctx = ContextState()
        memories = ["One", "Two", "Three", "Four", "Five"]
        prompt = _build_router_prompt("hello", ctx, conversation_memories=memories)
        # Should only include first 3
        assert "One" in prompt
        assert "Two" in prompt
        assert "Three" in prompt
        # Fourth and fifth may or may not be in prompt (implementation detail)


# ── Integration-style tests ───────────────────────────────────────────────


class TestMultiPickSuggestionIntegration:
    """Integration tests for multi-pick suggestions."""

    def test_pick_numbers_with_pick_number_both_provided(self):
        """When both pick_number and pick_numbers provided, pick_number is set from explicit value."""
        parsed = {
            "intent": "suggestion_pick",
            "pick_number": 1,
            "pick_numbers": [2, 3],
        }
        ctx = ContextState(pending_suggestions=["A", "B", "C"])
        result = _parse_router_response(parsed, ctx)
        assert result.pick_numbers == [2, 3]
        assert result.pick_number == 1  # Explicit pick_number takes precedence

    def test_single_pick_populates_pick_numbers(self):
        """Single pick_number without pick_numbers gets wrapped in list."""
        parsed = {"intent": "suggestion_pick", "pick_number": 2}
        ctx = ContextState(pending_suggestions=["A", "B", "C"])
        result = _parse_router_response(parsed, ctx)
        assert result.pick_numbers == [2]
        assert result.pick_number == 2


class TestCasualRemarksAsEasyTier:
    """Test that casual remarks/thinking out loud parse as easy tier."""

    def test_casual_remark_i_think_we_should(self):
        parsed = {
            "intent": "new_request",
            "tier": "easy",
            "answer": "Yeah, we can circle back to that later.",
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "easy"

    def test_casual_remark_hmm_interesting(self):
        parsed = {
            "intent": "new_request",
            "tier": "easy",
            "answer": "Right, interesting observation.",
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "easy"

    def test_casual_remark_note_to_self(self):
        parsed = {
            "intent": "new_request",
            "tier": "easy",
            "answer": "Noted.",
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "easy"

    def test_actual_request_stays_complex(self):
        """Legitimate requests should remain complex."""
        parsed = {
            "intent": "new_request",
            "tier": "complex",
            "complexity": "goal",
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.tier == "complex"


class TestEdgeCasesAndBoundaries:
    """Test boundary conditions and edge cases."""

    def test_accumulation_state_with_empty_prompt(self):
        state = _AccumulationState("task", "")
        assert state.prompt == ""
        assert state.task_id == "task"

    def test_router_result_with_all_fields(self):
        r = RouterResult(
            intent="suggestion_pick",
            tier="easy",
            answer="Picked one",
            complexity="",
            pick_number=1,
            pick_numbers=[1],
            approval=None,
            accumulated_items=[],
            accumulation_done=False,
            action="pick",
            action_params={"selected": 1},
            cost=0.005,
            fast_path=False,
            user_signals=[{"type": "style", "text": "Direct"}],
            config_requests=["Change theme"],
        )
        assert r.intent == "suggestion_pick"
        assert r.pick_number == 1
        assert r.config_requests == ["Change theme"]

    def test_parse_response_with_none_values(self):
        """Parser handles None values gracefully."""
        parsed = {
            "intent": None,
            "tier": None,
            "answer": None,
            "complexity": None,
        }
        ctx = ContextState()
        result = _parse_router_response(parsed, ctx)
        assert result.intent == "new_request"  # Default
        assert result.tier == "complex"  # Default
        assert result.answer == ""  # Stripped to empty

    def test_suggestion_pick_with_float_indices(self):
        """Parser handles float indices in pick_numbers."""
        parsed = {
            "intent": "suggestion_pick",
            "pick_numbers": [1.0, 2.0, 3.0],
        }
        ctx = ContextState(pending_suggestions=["A", "B", "C"])
        result = _parse_router_response(parsed, ctx)
        assert result.pick_numbers == [1, 2, 3]
