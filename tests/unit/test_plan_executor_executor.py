"""
Unit tests for plan_executor/executor.py.

Covers the core execution engine: step estimation, init, lazy tool loading,
the execute() loop (cancellation, cost cap, rewrite-loop detection, JSON/schema
retry, crash recovery, repeated-error abort, progress callbacks, learning system
recording), step description, step compression, prompt building (context
compression, budget warnings, blocked-domain/search-overlap warnings, error
hints), self-verification, and the get_interrupted_tasks classmethod.
Session 153.
"""

import os
import time
from unittest.mock import MagicMock, call, patch

import pytest

from src.core.plan_executor import executor as executor_mod
from src.core.plan_executor.executor import (
    MAX_STEPS_CHAT,
    MAX_STEPS_CODING,
    MAX_STEPS_PER_TASK,
    PLAN_MAX_TOKENS,
    TASK_COST_CAP,
    PlanExecutor,
    _estimate_total_steps,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_router(text='{"action": "done", "summary": "finished"}', cost=0.001):
    """Create a mock router that returns a fixed response."""
    router = MagicMock()
    router.generate.return_value = {"text": text, "cost_usd": cost}
    router.escalate_for_task.return_value.__enter__ = MagicMock(return_value={"model": "gemini"})
    router.escalate_for_task.return_value.__exit__ = MagicMock(return_value=False)
    return router


def _make_executor(router=None, **kwargs):
    """Create a PlanExecutor with a mock router."""
    return PlanExecutor(router=router or _make_router(), **kwargs)


def _step(action, success=True, **extra):
    """Build a step dict."""
    s = {"step": extra.pop("step_num", 1), "action": action, "success": success}
    s.update(extra)
    return s


# ---------------------------------------------------------------------------
# _estimate_total_steps
# ---------------------------------------------------------------------------

class TestEstimateTotalSteps:
    """_estimate_total_steps returns heuristic estimates based on action mix."""

    def test_fewer_than_two_steps_returns_max(self):
        assert _estimate_total_steps([], 50) == 50
        assert _estimate_total_steps([_step("think")], 50) == 50

    def test_writing_steps_estimate_n_plus_2(self):
        steps = [_step("think", step_num=1), _step("create_file", step_num=2)]
        result = _estimate_total_steps(steps, 50)
        assert result == 4  # n(2) + 2

    def test_research_steps_estimate_remaining_plus_overhead(self):
        steps = [_step("think", step_num=1), _step("web_search", step_num=2)]
        result = _estimate_total_steps(steps, 50)
        # n=2, researching=1, remaining_research=max(0,3-1)=2, estimate=2+2+3=7
        assert result == 7

    def test_thinking_only_estimate_n_plus_5(self):
        steps = [_step("think", step_num=1), _step("think", step_num=2)]
        result = _estimate_total_steps(steps, 50)
        assert result == 7  # n(2) + 5

    def test_unknown_actions_return_max(self):
        steps = [_step("unknown", step_num=1), _step("unknown", step_num=2)]
        assert _estimate_total_steps(steps, 50) == 50

    def test_clamped_to_max_steps(self):
        # Many write steps: estimate would be n+2 but clamped to max
        steps = [_step("create_file", step_num=i) for i in range(1, 20)]
        result = _estimate_total_steps(steps, 20)
        assert result <= 20

    def test_minimum_is_n_plus_1(self):
        steps = [_step("create_file", step_num=i) for i in range(1, 10)]
        result = _estimate_total_steps(steps, 50)
        assert result >= len(steps) + 1


# ---------------------------------------------------------------------------
# PlanExecutor.__init__
# ---------------------------------------------------------------------------

class TestInit:
    """PlanExecutor constructor stores all parameters."""

    def test_stores_router(self):
        r = _make_router()
        pe = PlanExecutor(router=r)
        assert pe._router is r

    def test_defaults(self):
        pe = PlanExecutor(router=_make_router())
        assert pe._tools is None
        assert pe._learning_system is None
        assert pe._hints == []
        assert pe._approval_callback is None
        assert pe._task_id is None

    def test_custom_params(self):
        tools = MagicMock()
        ls = MagicMock()
        hints = ["hint1", "hint2"]
        cb = lambda a, b, c: True
        pe = PlanExecutor(
            router=_make_router(), tools=tools,
            learning_system=ls, hints=hints, approval_callback=cb,
        )
        assert pe._tools is tools
        assert pe._learning_system is ls
        assert pe._hints == hints
        assert pe._approval_callback is cb


# ---------------------------------------------------------------------------
# PlanExecutor.tools property
# ---------------------------------------------------------------------------

class TestToolsProperty:
    """tools property lazy-initializes from get_shared_registry."""

    def test_returns_provided_tools(self):
        tools = MagicMock()
        pe = PlanExecutor(router=_make_router(), tools=tools)
        assert pe.tools is tools

    def test_lazy_loads_shared_registry(self):
        pe = PlanExecutor(router=_make_router())
        mock_registry = MagicMock()
        with patch("src.tools.tool_registry.get_shared_registry", return_value=mock_registry):
            result = pe.tools
        assert result is mock_registry


# ---------------------------------------------------------------------------
# PlanExecutor._describe_step (static)
# ---------------------------------------------------------------------------

class TestDescribeStep:
    """_describe_step returns human-readable one-liners per action type."""

    def test_web_search_success(self):
        msg = PlanExecutor._describe_step(
            "web_search", {"query": "vitamin D"}, {"success": True},
        )
        assert "Searching" in msg
        assert "vitamin D" in msg

    def test_web_search_failure(self):
        msg = PlanExecutor._describe_step(
            "web_search", {"query": "q"}, {"success": False},
        )
        assert "failed" in msg.lower()

    def test_fetch_webpage_extracts_domain(self):
        msg = PlanExecutor._describe_step(
            "fetch_webpage", {"url": "https://example.com/page"}, {"success": True},
        )
        assert "example.com" in msg

    def test_fetch_webpage_no_scheme(self):
        msg = PlanExecutor._describe_step(
            "fetch_webpage", {"url": "no-scheme-url"}, {"success": True},
        )
        assert "Reading" in msg

    def test_create_file(self):
        msg = PlanExecutor._describe_step(
            "create_file", {"path": "/foo/bar/report.md"}, {"success": True},
        )
        assert "report.md" in msg

    def test_append_file(self):
        msg = PlanExecutor._describe_step(
            "append_file", {"path": "/foo/data.txt"}, {"success": True},
        )
        assert "Updating" in msg

    def test_read_file(self):
        msg = PlanExecutor._describe_step(
            "read_file", {"path": "/foo/code.py"}, {"success": True},
        )
        assert "Reading" in msg

    def test_write_source_and_edit_file(self):
        for action in ("write_source", "edit_file"):
            msg = PlanExecutor._describe_step(
                action, {"path": "/foo/mod.py"}, {"success": True},
            )
            assert "Editing" in msg

    def test_run_python(self):
        msg = PlanExecutor._describe_step(
            "run_python", {}, {"success": True},
        )
        assert "Running code" in msg

    def test_run_command(self):
        msg = PlanExecutor._describe_step(
            "run_command", {"command": "pytest tests/"}, {"success": True},
        )
        assert "pytest" in msg

    def test_list_files(self):
        msg = PlanExecutor._describe_step(
            "list_files", {}, {"success": True},
        )
        assert "Checking files" in msg

    def test_unknown_action(self):
        msg = PlanExecutor._describe_step(
            "custom_action", {}, {"success": True},
        )
        assert "custom_action" in msg

    def test_empty_params(self):
        msg = PlanExecutor._describe_step(
            "create_file", {}, {"success": True},
        )
        assert "Writing" in msg


# ---------------------------------------------------------------------------
# PlanExecutor._compress_step (static)
# ---------------------------------------------------------------------------

class TestCompressStep:
    """_compress_step produces compressed one-liners for context compression."""

    def test_web_search(self):
        s = {"step": 1, "action": "web_search", "success": True, "params": {"query": "test"}}
        line = PlanExecutor._compress_step(s)
        assert "web_search" in line and "test" in line and "ok" in line

    def test_fetch_webpage(self):
        s = {"step": 2, "action": "fetch_webpage", "success": False, "params": {"url": "http://x.com"}}
        line = PlanExecutor._compress_step(s)
        assert "fetch" in line and "FAIL" in line

    def test_create_file(self):
        s = {"step": 3, "action": "create_file", "success": True, "params": {"path": "foo.md"}}
        line = PlanExecutor._compress_step(s)
        assert "create_file" in line and "foo.md" in line

    def test_run_python(self):
        s = {"step": 4, "action": "run_python", "success": True, "params": {}}
        line = PlanExecutor._compress_step(s)
        assert "run_python" in line

    def test_run_command(self):
        s = {"step": 5, "action": "run_command", "success": True, "params": {"command": "ls"}}
        line = PlanExecutor._compress_step(s)
        assert "run_command" in line and "ls" in line

    def test_think(self):
        s = {"step": 6, "action": "think", "note": "reasoning here"}
        line = PlanExecutor._compress_step(s)
        assert "think" in line and "reasoning" in line

    def test_done(self):
        s = {"step": 7, "action": "done", "summary": "task completed"}
        line = PlanExecutor._compress_step(s)
        assert "done" in line and "task completed" in line

    def test_unknown(self):
        s = {"step": 8, "action": "custom", "success": True, "params": {}}
        line = PlanExecutor._compress_step(s)
        assert "custom" in line


# ---------------------------------------------------------------------------
# PlanExecutor._build_step_prompt
# ---------------------------------------------------------------------------

class TestBuildStepPrompt:
    """_build_step_prompt constructs the next-step prompt with history + budget."""

    def test_includes_task_description(self):
        pe = _make_executor()
        pe._hints = []
        pe._conversation_history = ""
        pe._step_history = []
        prompt = pe._build_step_prompt("Do the thing", "Goal X", [], step_num=0, max_steps=50)
        assert "Do the thing" in prompt
        assert "Goal X" in prompt

    def test_budget_block_shows_step_info(self):
        pe = _make_executor()
        pe._hints = []
        pe._conversation_history = ""
        pe._step_history = []
        prompt = pe._build_step_prompt("t", "", [], step_num=5, max_steps=50)
        assert "Step 6 of 50" in prompt
        assert "45 remaining" in prompt

    def test_low_budget_warning_at_3_remaining(self):
        pe = _make_executor()
        pe._hints = []
        pe._conversation_history = ""
        pe._step_history = []
        prompt = pe._build_step_prompt("t", "", [], step_num=47, max_steps=50)
        assert "LOW BUDGET" in prompt

    def test_halfway_warning(self):
        pe = _make_executor()
        pe._hints = []
        pe._conversation_history = ""
        pe._step_history = []
        prompt = pe._build_step_prompt("t", "", [], step_num=30, max_steps=50)
        assert "halfway" in prompt

    def test_no_warning_early_steps(self):
        pe = _make_executor()
        pe._hints = []
        pe._conversation_history = ""
        pe._step_history = []
        prompt = pe._build_step_prompt("t", "", [], step_num=5, max_steps=50)
        assert "LOW BUDGET" not in prompt
        assert "halfway" not in prompt

    def test_context_compression_after_8_steps(self):
        pe = _make_executor()
        pe._hints = []
        pe._conversation_history = ""
        steps = []
        for i in range(1, 11):
            steps.append({
                "step": i, "action": "web_search", "success": True,
                "params": {"query": f"query_{i}"}, "snippet": f"result_{i}",
            })
        pe._step_history = steps
        prompt = pe._build_step_prompt("t", "", steps, step_num=10, max_steps=50)
        assert "summarized" in prompt

    def test_hints_included(self):
        pe = _make_executor(hints=["use pandas", "check types"])
        pe._conversation_history = ""
        pe._step_history = []
        prompt = pe._build_step_prompt("t", "", [], step_num=0, max_steps=50)
        assert "use pandas" in prompt
        assert "check types" in prompt

    def test_conversation_history_included(self):
        pe = _make_executor()
        pe._hints = []
        pe._conversation_history = "User said: do X"
        pe._step_history = []
        prompt = pe._build_step_prompt("t", "", [], step_num=0, max_steps=50)
        assert "User said: do X" in prompt

    def test_blocked_domains_warning(self):
        pe = _make_executor()
        pe._hints = []
        pe._conversation_history = ""
        steps = [
            {"step": 1, "action": "fetch_webpage", "success": False,
             "params": {"url": "https://blocked.com/page"}, "error": "403"},
        ]
        pe._step_history = steps
        prompt = pe._build_step_prompt("t", "", steps, step_num=1, max_steps=50)
        assert "BLOCKED DOMAINS" in prompt
        assert "blocked.com" in prompt

    def test_repeated_search_warning(self):
        pe = _make_executor()
        pe._hints = []
        pe._conversation_history = ""
        steps = [
            {"step": i, "action": "web_search", "success": True,
             "params": {"query": "vitamin D dosage optimal"}, "snippet": "r"}
            for i in range(1, 6)
        ]
        pe._step_history = steps
        prompt = pe._build_step_prompt("t", "", steps, step_num=5, max_steps=50)
        assert "STOP searching" in prompt

    def test_error_hint_injected(self):
        pe = _make_executor()
        pe._hints = []
        pe._conversation_history = ""
        steps = [
            {"step": 1, "action": "edit_file", "success": False,
             "error": "not found", "error_hint": "Try read_file first"},
        ]
        pe._step_history = steps
        prompt = pe._build_step_prompt("t", "", steps, step_num=1, max_steps=50)
        assert "FIX HINT" in prompt
        assert "Try read_file first" in prompt


# ---------------------------------------------------------------------------
# PlanExecutor.execute — happy path
# ---------------------------------------------------------------------------

class TestExecuteHappyPath:
    """execute() runs the loop and returns results on done."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_simple_done(self, mock_cancel, mock_load, mock_clear, mock_save):
        """Only a 'done' step with no action steps → success is False (no successful steps)."""
        router = _make_router('{"action": "done", "summary": "All done"}')
        pe = _make_executor(router=router)
        result = pe.execute("Test task", task_id="t1")
        assert result["success"] is False  # no executed action steps
        assert result["total_steps"] == 1
        assert result["executed_steps"] == 0
        mock_clear.assert_called_once_with("t1")

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_think_then_done(self, mock_cancel, mock_load, mock_clear, mock_save):
        """think + done only → success is False (think/done are not 'executed' actions)."""
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "think", "note": "planning"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "finished"}', "cost_usd": 0.001},
        ]
        pe = _make_executor(router=router)
        result = pe.execute("Task", task_id="t2")
        assert result["success"] is False  # no executed action steps
        assert result["total_steps"] == 2
        assert result["total_cost"] == pytest.approx(0.002)

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_action_then_done_success(self, mock_cancel, mock_load, mock_clear, mock_save):
        """A successful action step + done → success is True."""
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "web_search", "query": "test"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "finished"}', "cost_usd": 0.001},
        ]
        pe = _make_executor(router=router)
        pe._execute_action = MagicMock(return_value={"success": True, "snippet": "results"})
        with patch("src.core.plan_executor.executor._classify_error"):
            result = pe.execute("Task", task_id="t2b")
        assert result["success"] is True
        assert result["total_steps"] == 2
        assert result["successful_steps"] == 1


# ---------------------------------------------------------------------------
# PlanExecutor.execute — cancellation
# ---------------------------------------------------------------------------

class TestExecuteCancellation:
    """execute() stops cleanly when cancellation is signalled."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation",
           return_value="User cancelled")
    def test_cancelled_at_start(self, mock_cancel, mock_load, mock_clear, mock_save):
        pe = _make_executor()
        result = pe.execute("Task", task_id="t3")
        assert result["total_steps"] == 1
        assert result["steps_taken"][0].get("cancelled") is True
        assert "cancelled" in result["steps_taken"][0]["summary"].lower()

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation")
    def test_cancelled_mid_execution(self, mock_cancel, mock_load, mock_clear, mock_save):
        # First step: no cancellation; second step: cancelled
        mock_cancel.side_effect = [None, "Stop now"]
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "think", "note": "first"}', "cost_usd": 0.001},
            # This generate should not be called because cancellation happens first
        ]
        pe = _make_executor(router=router)
        result = pe.execute("Task", task_id="t4")
        # Should have think step + done-cancelled step
        assert result["total_steps"] == 2
        assert result["steps_taken"][-1].get("cancelled") is True


# ---------------------------------------------------------------------------
# PlanExecutor.execute — cost cap
# ---------------------------------------------------------------------------

class TestExecuteCostCap:
    """execute() stops when per-task cost cap is reached."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_cost_cap_stops_execution(self, mock_cancel, mock_load, mock_clear, mock_save):
        router = _make_router()
        # Each call costs $0.20, so after 3 calls we hit $0.60 > $0.50
        router.generate.side_effect = [
            {"text": '{"action": "think", "note": "step1"}', "cost_usd": 0.20},
            {"text": '{"action": "think", "note": "step2"}', "cost_usd": 0.20},
            {"text": '{"action": "think", "note": "step3"}', "cost_usd": 0.20},
            # Should not reach this
            {"text": '{"action": "done", "summary": "done"}', "cost_usd": 0.001},
        ]
        pe = _make_executor(router=router)
        result = pe.execute("Task", task_id="t5")
        assert result["steps_taken"][-1].get("cost_capped") is True
        assert result["total_cost"] >= TASK_COST_CAP


# ---------------------------------------------------------------------------
# PlanExecutor.execute — rewrite loop detection
# ---------------------------------------------------------------------------

class TestExecuteRewriteLoop:
    """execute() detects and aborts rewrite loops on the same file."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_aborts_after_4_writes_to_same_file(self, mock_cancel, mock_load, mock_clear, mock_save):
        """Force-stop triggers at >=4 writes to the same file (lowered from 7 in session 161)."""
        router = _make_router()
        responses = []
        for i in range(6):
            responses.append(
                {"text": '{"action": "create_file", "path": "workspace/f.md", "content": "v' + str(i) + '"}',
                 "cost_usd": 0.001}
            )
        responses.append({"text": '{"action": "done", "summary": "done"}', "cost_usd": 0.001})
        router.generate.side_effect = responses

        pe = _make_executor(router=router)
        # Mock _execute_action to return success
        pe._execute_action = MagicMock(return_value={
            "success": True, "path": "workspace/f.md",
        })
        result = pe.execute("Task", task_id="t6")
        assert result["steps_taken"][-1].get("loop_aborted") is True
        # Should abort at step 5 (after 4 successful writes are counted on step 5's check)
        # Step 1-4: create_file (succeed), Step 5 check: 4 writes detected -> abort
        assert len([s for s in result["steps_taken"] if s["action"] == "create_file"]) == 4


# ---------------------------------------------------------------------------
# PlanExecutor.execute — crash recovery
# ---------------------------------------------------------------------------

class TestExecuteCrashRecovery:
    """execute() resumes from saved state."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_resumes_from_saved_state(self, mock_cancel, mock_clear, mock_save):
        saved = {
            "steps_taken": [
                {"step": 1, "action": "think", "note": "first", "success": True},
                {"step": 2, "action": "think", "note": "second", "success": True},
            ],
            "total_cost": 0.01,
            "files_created": [],
        }
        with patch("src.core.plan_executor.executor.load_state", return_value=saved):
            router = _make_router('{"action": "done", "summary": "resumed and done"}')
            pe = _make_executor(router=router)
            result = pe.execute("Task", task_id="t7", max_steps=50)
        # Should have the 2 saved steps + 1 done step
        assert result["total_steps"] == 3
        assert result["total_cost"] >= 0.01


# ---------------------------------------------------------------------------
# PlanExecutor.execute — repeated error abort
# ---------------------------------------------------------------------------

class TestExecuteEditFailureRewriteHint:
    """After 2+ edit/append failures on the same file, prompt hints to use create_file."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_rewrite_hint_after_2_edit_failures(self, mock_cancel, mock_load, mock_clear, mock_save):
        """After 2 edit_file failures, the step prompt includes a rewrite hint."""
        router = _make_router()
        # Steps: edit_file fails (x2), then create_file succeeds, then done
        responses = [
            {"text": '{"action": "edit_file", "path": "workspace/app.py", "find": "old", "replace": "new"}', "cost_usd": 0.001},
            {"text": '{"action": "edit_file", "path": "workspace/app.py", "find": "old2", "replace": "new2"}', "cost_usd": 0.001},
            {"text": '{"action": "create_file", "path": "workspace/app.py", "content": "fixed"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "done"}', "cost_usd": 0.001},
        ]
        router.generate.side_effect = responses

        pe = _make_executor(router=router)

        def _mock_action(parsed, step_num):
            action = parsed.get("action", "")
            if action == "edit_file":
                return {"success": False, "error": "SyntaxError: unexpected indent"}
            return {"success": True, "path": parsed.get("path", "")}

        pe._execute_action = _mock_action
        with patch("src.core.plan_executor.executor._classify_error",
                    return_value=("mechanical", "syntax error")):
            result = pe.execute("Task", task_id="t_edit_hint", max_steps=10)

        # The 3rd call to generate() should have the rewrite hint in the prompt
        # (after 2 edit failures, before the create_file step)
        assert router.generate.call_count >= 3
        third_call_kwargs = router.generate.call_args_list[2]
        prompt_text = third_call_kwargs.kwargs.get("prompt") or third_call_kwargs[1].get("prompt", "")
        assert "STOP trying to patch this file" in prompt_text
        assert "create_file" in prompt_text


class TestExecuteRepeatedErrorAbort:
    """execute() aborts after 3 identical errors."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_aborts_after_3_same_errors(self, mock_cancel, mock_load, mock_clear, mock_save):
        router = _make_router()
        responses = [
            {"text": '{"action": "fetch_webpage", "url": "https://bad.com/x"}', "cost_usd": 0.001}
            for _ in range(5)
        ]
        router.generate.side_effect = responses

        pe = _make_executor(router=router)
        pe._execute_action = MagicMock(return_value={
            "success": False, "error": "ConnectionError: timeout",
        })
        with patch("src.core.plan_executor.executor._classify_error",
                    return_value=("permanent", "")):
            result = pe.execute("Task", task_id="t8")
        assert result["steps_taken"][-1].get("repeated_error_abort") is True


# ---------------------------------------------------------------------------
# PlanExecutor.execute — JSON/schema retry
# ---------------------------------------------------------------------------

class TestExecuteSchemaRetry:
    """execute() retries on invalid JSON and schema violations."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_retries_invalid_json_then_succeeds(self, mock_cancel, mock_load, mock_clear, mock_save):
        router = _make_router()
        router.generate.side_effect = [
            {"text": "not json at all", "cost_usd": 0.001},  # initial: bad
            {"text": '{"action": "done", "summary": "ok"}', "cost_usd": 0.001},  # retry 1: good
        ]
        pe = _make_executor(router=router)
        with patch("src.core.output_schemas.validate_action", return_value=None):
            result = pe.execute("Task", task_id="t9")
        # done with no action steps → success is False (no successful steps), but
        # schema_retries_exhausted should be False since JSON was recovered.
        assert result["schema_retries_exhausted"] is False

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_schema_retries_exhausted_marks_failure(self, mock_cancel, mock_load, mock_clear, mock_save):
        router = _make_router()
        # All attempts return bad JSON
        router.generate.return_value = {"text": "garbage", "cost_usd": 0.001}
        # Escalation also fails
        esc_ctx = MagicMock()
        esc_ctx.__enter__ = MagicMock(return_value={"model": "gemini"})
        esc_ctx.__exit__ = MagicMock(return_value=False)
        router.escalate_for_task.return_value = esc_ctx

        pe = _make_executor(router=router)
        result = pe.execute("Task", task_id="t10")
        assert result["schema_retries_exhausted"] is True
        assert result["success"] is False


# ---------------------------------------------------------------------------
# PlanExecutor.execute — progress callback
# ---------------------------------------------------------------------------

class TestExecuteProgressCallback:
    """execute() calls progress_callback after action steps."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_progress_callback_called(self, mock_cancel, mock_load, mock_clear, mock_save):
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "web_search", "query": "test"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "ok"}', "cost_usd": 0.001},
        ]
        cb = MagicMock()
        pe = _make_executor(router=router)
        pe._execute_action = MagicMock(return_value={
            "success": True, "snippet": "results",
        })
        with patch("src.core.plan_executor.executor._classify_error"):
            result = pe.execute("Task", task_id="t11", progress_callback=cb)
        assert cb.called

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_progress_callback_exception_swallowed(self, mock_cancel, mock_load, mock_clear, mock_save):
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "web_search", "query": "test"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "ok"}', "cost_usd": 0.001},
        ]
        cb = MagicMock(side_effect=RuntimeError("callback crash"))
        pe = _make_executor(router=router)
        pe._execute_action = MagicMock(return_value={"success": True, "snippet": "r"})
        with patch("src.core.plan_executor.executor._classify_error"):
            result = pe.execute("Task", task_id="t12", progress_callback=cb)
        # Should not crash
        assert result["total_steps"] >= 1


# ---------------------------------------------------------------------------
# PlanExecutor.execute — learning system
# ---------------------------------------------------------------------------

class TestExecuteLearningSystem:
    """execute() records action outcomes to learning system."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_records_action_outcome(self, mock_cancel, mock_load, mock_clear, mock_save):
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "web_search", "query": "test"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "ok"}', "cost_usd": 0.001},
        ]
        ls = MagicMock()
        pe = _make_executor(router=router, learning_system=ls)
        pe._execute_action = MagicMock(return_value={"success": True, "snippet": "r"})
        with patch("src.core.plan_executor.executor._classify_error"):
            pe.execute("Task", task_id="t13")
        ls.record_action_outcome.assert_called_once_with("web_search", True)

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_learning_system_exception_swallowed(self, mock_cancel, mock_load, mock_clear, mock_save):
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "web_search", "query": "test"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "ok"}', "cost_usd": 0.001},
        ]
        ls = MagicMock()
        ls.record_action_outcome.side_effect = RuntimeError("boom")
        pe = _make_executor(router=router, learning_system=ls)
        pe._execute_action = MagicMock(return_value={"success": True, "snippet": "r"})
        with patch("src.core.plan_executor.executor._classify_error"):
            result = pe.execute("Task", task_id="t14")
        assert result["total_steps"] >= 1  # did not crash


# ---------------------------------------------------------------------------
# PlanExecutor.execute — file tracking
# ---------------------------------------------------------------------------

class TestExecuteFileTracking:
    """execute() tracks files created by write actions."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_tracks_created_files(self, mock_cancel, mock_load, mock_clear, mock_save):
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "create_file", "path": "workspace/f.md", "content": "hi"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "ok"}', "cost_usd": 0.001},
        ]
        pe = _make_executor(router=router)
        pe._execute_action = MagicMock(return_value={
            "success": True, "path": "workspace/f.md",
        })
        # Skip verification
        pe._verify_work = MagicMock(return_value={"passed": True, "cost": 0.0})
        with patch("src.core.plan_executor.executor._classify_error"):
            result = pe.execute("Task", task_id="t15")
        assert "workspace/f.md" in result["files_created"]

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_no_duplicate_file_tracking(self, mock_cancel, mock_load, mock_clear, mock_save):
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "create_file", "path": "workspace/f.md", "content": "v1"}', "cost_usd": 0.001},
            {"text": '{"action": "append_file", "path": "workspace/f.md", "content": "v2"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "ok"}', "cost_usd": 0.001},
        ]
        pe = _make_executor(router=router)
        pe._execute_action = MagicMock(return_value={
            "success": True, "path": "workspace/f.md",
        })
        pe._verify_work = MagicMock(return_value={"passed": True, "cost": 0.0})
        with patch("src.core.plan_executor.executor._classify_error"):
            result = pe.execute("Task", task_id="t16")
        assert result["files_created"].count("workspace/f.md") == 1


# ---------------------------------------------------------------------------
# PlanExecutor.execute — transient error retry
# ---------------------------------------------------------------------------

class TestExecuteTransientRetry:
    """execute() retries transient errors once after 2s delay."""

    @patch("src.core.plan_executor.executor.time.sleep")
    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_transient_error_retried(self, mock_cancel, mock_load, mock_clear, mock_save, mock_sleep):
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "web_search", "query": "test"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "ok"}', "cost_usd": 0.001},
        ]
        call_count = 0
        def execute_action_side_effect(parsed, step_num):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"success": False, "error": "timeout"}
            return {"success": True, "snippet": "results"}

        pe = _make_executor(router=router)
        pe._execute_action = MagicMock(side_effect=execute_action_side_effect)
        with patch("src.core.plan_executor.executor._classify_error",
                    return_value=("transient", "")):
            result = pe.execute("Task", task_id="t17")
        mock_sleep.assert_called_with(2)
        assert pe._execute_action.call_count == 2

    @patch("src.core.plan_executor.executor.time.sleep")
    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_mechanical_error_gets_hint(self, mock_cancel, mock_load, mock_clear, mock_save, mock_sleep):
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "edit_file", "path": "src/x.py", "find": "old", "replace": "new"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "ok"}', "cost_usd": 0.001},
        ]
        pe = _make_executor(router=router)
        pe._execute_action = MagicMock(return_value={
            "success": False, "error": "find string not found",
        })
        with patch("src.core.plan_executor.executor._classify_error",
                    return_value=("mechanical", "Use read_file first")):
            result = pe.execute("Task", task_id="t18")
        # The step should have error_hint set
        action_steps = [s for s in result["steps_taken"] if s["action"] != "done"]
        assert action_steps[0].get("error_hint") == "Use read_file first"


# ---------------------------------------------------------------------------
# PlanExecutor._verify_work
# ---------------------------------------------------------------------------

class TestVerifyWork:
    """_verify_work reads created files and asks the model to rate quality."""

    def test_no_files_returns_passed(self):
        pe = _make_executor()
        result = pe._verify_work("task", "goal", [], [])
        assert result["passed"] is True
        assert result["cost"] == 0.0

    def test_unreadable_files_returns_passed(self):
        pe = _make_executor()
        result = pe._verify_work("task", "goal", [], ["/nonexistent/file.txt"])
        assert result["passed"] is True

    def test_passed_true(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("Some content here")
        router = _make_router('{"passed": true}', cost=0.002)
        pe = _make_executor(router=router)
        result = pe._verify_work("task", "goal", [], [str(f)])
        assert result["passed"] is True
        assert result["cost"] == 0.002

    def test_passed_false(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("Placeholder")
        router = _make_router('{"passed": false, "reason": "empty shell content"}', cost=0.002)
        pe = _make_executor(router=router)
        result = pe._verify_work("task", "goal", [], [str(f)])
        assert result["passed"] is False

    def test_unparseable_response_returns_passed(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("Content")
        router = _make_router("not json", cost=0.001)
        pe = _make_executor(router=router)
        result = pe._verify_work("task", "goal", [], [str(f)])
        assert result["passed"] is True

    def test_exception_returns_passed(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("Content")
        router = _make_router()
        router.generate.side_effect = RuntimeError("API down")
        pe = _make_executor(router=router)
        result = pe._verify_work("task", "goal", [], [str(f)])
        assert result["passed"] is True

    def test_max_3_files_read(self, tmp_path):
        files = []
        for i in range(5):
            f = tmp_path / f"file{i}.md"
            f.write_text(f"content {i}")
            files.append(str(f))
        router = _make_router('{"quality": 7, "issues": "", "strengths": "ok"}')
        pe = _make_executor(router=router)
        result = pe._verify_work("task", "goal", [], files)
        # Should only read first 3 — verify prompt doesn't include all 5
        prompt_arg = router.generate.call_args[1]["prompt"]
        assert "file0" in prompt_arg
        assert "file2" in prompt_arg
        # file3 and file4 should not be in the prompt
        assert "file3" not in prompt_arg


# ---------------------------------------------------------------------------
# PlanExecutor._check_task_requirements
# ---------------------------------------------------------------------------

class TestCheckTaskRequirements:
    """_check_task_requirements runs a QA pre-check on task output."""

    def test_no_files_returns_met(self):
        pe = _make_executor()
        result = pe._check_task_requirements("task", "goal", [])
        assert result["met"] is True
        assert result["cost"] == 0.0

    def test_met_true(self, tmp_path):
        f = tmp_path / "output.md"
        f.write_text("# Research Report\nDetailed findings about X...")
        router = _make_router('{"met": true}', cost=0.001)
        pe = _make_executor(router=router)
        result = pe._check_task_requirements("task", "goal", [str(f)])
        assert result["met"] is True
        assert result["cost"] == 0.001

    def test_met_false_with_feedback(self, tmp_path):
        f = tmp_path / "output.md"
        f.write_text("TODO: fill in later")
        router = _make_router(
            '{"met": false, "gaps": "Output is placeholder, no real content"}',
            cost=0.001,
        )
        pe = _make_executor(router=router)
        result = pe._check_task_requirements("task", "goal", [str(f)])
        assert result["met"] is False
        assert "placeholder" in result["feedback"].lower()

    def test_unparseable_response_returns_met(self, tmp_path):
        f = tmp_path / "output.md"
        f.write_text("Content here")
        router = _make_router("not json at all", cost=0.001)
        pe = _make_executor(router=router)
        result = pe._check_task_requirements("task", "goal", [str(f)])
        assert result["met"] is True

    def test_exception_returns_met(self, tmp_path):
        f = tmp_path / "output.md"
        f.write_text("Content here")
        router = _make_router()
        router.generate.side_effect = RuntimeError("API down")
        pe = _make_executor(router=router)
        result = pe._check_task_requirements("task", "goal", [str(f)])
        assert result["met"] is True

    def test_nonexistent_files_returns_met(self):
        pe = _make_executor()
        result = pe._check_task_requirements("task", "goal", ["/no/such/file.md"])
        # read_file_contents returns "(no files)" for nonexistent files
        assert result["met"] is True


# ---------------------------------------------------------------------------
# _extract_inline_output + _verify_inline_output
# ---------------------------------------------------------------------------

class TestExtractInlineOutput:
    """_extract_inline_output extracts text from step results when no files created."""

    def test_returns_empty_for_no_steps(self):
        from src.core.plan_executor.executor import _extract_inline_output
        assert _extract_inline_output([]) == ""

    def test_returns_empty_for_short_output(self):
        from src.core.plan_executor.executor import _extract_inline_output
        steps = [{"action": "done", "summary": "ok"}]
        assert _extract_inline_output(steps) == ""

    def test_extracts_done_summary(self):
        from src.core.plan_executor.executor import _extract_inline_output
        steps = [
            {"action": "web_search", "success": True, "result": "Found 5 results about puppy training schedules and tips for new owners."},
            {"action": "done", "summary": "Created a comprehensive 8-week puppy training schedule covering basic commands, socialization, and house training milestones."},
        ]
        result = _extract_inline_output(steps)
        assert "puppy training schedule" in result
        assert "8-week" in result

    def test_extracts_successful_step_results(self):
        from src.core.plan_executor.executor import _extract_inline_output
        steps = [
            {"action": "web_search", "success": True, "result": "Found detailed hiking trails information with ratings and distances for the local area."},
            {"action": "done", "summary": "Compiled a list of 5 beginner-friendly hiking trails."},
        ]
        result = _extract_inline_output(steps)
        assert "hiking trails" in result

    def test_skips_think_steps(self):
        from src.core.plan_executor.executor import _extract_inline_output
        steps = [
            {"action": "think", "success": True, "note": "I should search for trails"},
            {"action": "done", "summary": "Compiled a detailed hiking guide with trail information and difficulty ratings for beginners."},
        ]
        result = _extract_inline_output(steps)
        assert "I should search" not in result
        assert "hiking guide" in result


class TestVerifyInlineOutput:
    """_verify_inline_output verifies text output when no files were created."""

    def test_passed_true(self):
        pe = _make_executor()
        pe._router.generate.return_value = {"text": '{"passed": true}', "cost_usd": 0.001}
        result = pe._verify_inline_output("make a schedule", "puppy training", "Week 1: Focus on...")
        assert result["passed"] is True
        assert result["cost"] == 0.001

    def test_passed_false(self):
        pe = _make_executor()
        pe._router.generate.return_value = {"text": '{"passed": false, "reason": "empty"}', "cost_usd": 0.001}
        result = pe._verify_inline_output("make a schedule", "goal", "placeholder text")
        assert result["passed"] is False

    def test_exception_returns_passed(self):
        pe = _make_executor()
        pe._router.generate.side_effect = RuntimeError("API down")
        result = pe._verify_inline_output("task", "goal", "some text")
        assert result["passed"] is True

    def test_unparseable_response_returns_passed(self):
        pe = _make_executor()
        pe._router.generate.return_value = {"text": "not json", "cost_usd": 0.0}
        result = pe._verify_inline_output("task", "goal", "some text")
        assert result["passed"] is True


# ---------------------------------------------------------------------------
# PlanExecutor.execute — requirements correction pass
# ---------------------------------------------------------------------------

class TestExecuteRequirementsCorrectionPass:
    """execute() runs a correction pass when requirements check finds gaps."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_correction_pass_runs_on_requirements_gap(
        self, mock_cancel, mock_load, mock_clear, mock_save, tmp_path,
    ):
        """When verify passes but requirements check fails, correction pass executes."""
        f = tmp_path / "output.md"
        f.write_text("placeholder content")

        # Sequence: step 1 (create_file) → done → verify(pass) → req_check(fail)
        #   → correction step (edit) → correction done → re-verify(pass)
        responses = [
            # Step 1: create_file
            {"text": f'{{"action": "create_file", "path": "{f}", "content": "placeholder"}}', "cost_usd": 0.001},
            # Step 2: done
            {"text": '{"action": "done", "summary": "Created output file"}', "cost_usd": 0.001},
            # Verify: passed
            {"text": '{"passed": true}', "cost_usd": 0.001},
            # Requirements check: not met
            {"text": '{"met": false, "gaps": "Output is placeholder text"}', "cost_usd": 0.001},
            # Correction step: edit_file
            {"text": '{"action": "done", "summary": "Fixed content"}', "cost_usd": 0.001},
            # Re-verify: passed
            {"text": '{"passed": true}', "cost_usd": 0.001},
        ]
        router = MagicMock()
        router.generate.side_effect = [{"text": r["text"], "cost_usd": r["cost_usd"]} for r in responses]
        router.escalate_for_task.return_value.__enter__ = MagicMock(return_value={"model": "gemini"})
        router.escalate_for_task.return_value.__exit__ = MagicMock(return_value=False)

        pe = PlanExecutor(router=router)
        pe._execute_action = MagicMock(return_value={"success": True, "path": str(f)})

        result = pe.execute("Write a report", "Create report", max_steps=20)
        # Should have correction_pass steps
        correction_steps = [s for s in result["steps_taken"] if s.get("correction_pass")]
        assert len(correction_steps) >= 1

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_no_correction_when_requirements_met(
        self, mock_cancel, mock_load, mock_clear, mock_save, tmp_path,
    ):
        """When requirements check passes, no correction pass runs."""
        f = tmp_path / "output.md"
        f.write_text("Real content about the topic")

        responses = [
            {"text": f'{{"action": "create_file", "path": "{f}", "content": "real content"}}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "Done"}', "cost_usd": 0.001},
            {"text": '{"passed": true}', "cost_usd": 0.001},  # verify
            {"text": '{"met": true}', "cost_usd": 0.001},  # req check
        ]
        router = MagicMock()
        router.generate.side_effect = [{"text": r["text"], "cost_usd": r["cost_usd"]} for r in responses]
        router.escalate_for_task.return_value.__enter__ = MagicMock(return_value={"model": "gemini"})
        router.escalate_for_task.return_value.__exit__ = MagicMock(return_value=False)

        pe = PlanExecutor(router=router)
        pe._execute_action = MagicMock(return_value={"success": True, "path": str(f)})

        result = pe.execute("Write a report", "Create report", max_steps=20)
        correction_steps = [s for s in result["steps_taken"] if s.get("correction_pass")]
        assert len(correction_steps) == 0

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_no_correction_when_step_budget_exhausted(
        self, mock_cancel, mock_load, mock_clear, mock_save, tmp_path,
    ):
        """When step budget is too low for correction, skip requirements check."""
        f = tmp_path / "out.md"
        f.write_text("content")

        responses = [
            # Step 1: create_file
            {"text": f'{{"action": "create_file", "path": "{f}", "content": "content"}}', "cost_usd": 0.001},
            # Step 2: another action to eat budget
            {"text": '{"action": "think", "note": "planning"}', "cost_usd": 0.001},
            # Step 3: done (max_steps=4 so remaining=1 after 3 steps)
            {"text": '{"action": "done", "summary": "Done"}', "cost_usd": 0.001},
            # Verify: passed
            {"text": '{"passed": true}', "cost_usd": 0.001},
            # No requirements check expected — remaining < 3
        ]
        router = MagicMock()
        router.generate.side_effect = [{"text": r["text"], "cost_usd": r["cost_usd"]} for r in responses]
        router.escalate_for_task.return_value.__enter__ = MagicMock(return_value={"model": "gemini"})
        router.escalate_for_task.return_value.__exit__ = MagicMock(return_value=False)

        pe = PlanExecutor(router=router)
        pe._execute_action = MagicMock(return_value={"success": True, "path": str(f)})

        # max_steps=4, uses 3 steps, remaining=1 which is < 3 → no req check
        result = pe.execute("task", "goal", max_steps=4)
        assert result["success"] is True
        # Only 4 generate calls (3 steps + verify), no requirements check
        assert router.generate.call_count == 4


# ---------------------------------------------------------------------------
# PlanExecutor.get_interrupted_tasks
# ---------------------------------------------------------------------------

class TestGetInterruptedTasks:
    """get_interrupted_tasks delegates to recovery module."""

    def test_delegates_to_recovery(self):
        with patch("src.core.plan_executor.recovery.get_interrupted_tasks",
                    return_value=[{"task_id": "t1"}]) as mock_fn:
            result = PlanExecutor.get_interrupted_tasks()
        mock_fn.assert_called_once()
        assert result == [{"task_id": "t1"}]

    def test_classmethod_returns_list(self):
        with patch("src.core.plan_executor.recovery.get_interrupted_tasks",
                    return_value=[{"task_id": "t99"}]):
            result = PlanExecutor.get_interrupted_tasks()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# PlanExecutor.execute — success/failure determination
# ---------------------------------------------------------------------------

class TestExecuteSuccessDetermination:
    """execute() determines success based on steps, verification, and schema."""

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_no_successful_steps_is_failure(self, mock_cancel, mock_load, mock_clear, mock_save):
        """Only 'done' step with no executed steps — success is False (no successful steps)."""
        router = _make_router('{"action": "done", "summary": "nothing to do"}')
        pe = _make_executor(router=router)
        result = pe.execute("Task", task_id="t19")
        # 0 successful steps → success is False
        assert result["successful_steps"] == 0
        assert result["success"] is False

    @patch("src.core.plan_executor.executor.save_state")
    @patch("src.core.plan_executor.executor.clear_state")
    @patch("src.core.plan_executor.executor.load_state", return_value=None)
    @patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None)
    def test_verification_failure_with_files_is_failure(self, mock_cancel, mock_load, mock_clear, mock_save):
        router = _make_router()
        router.generate.side_effect = [
            {"text": '{"action": "create_file", "path": "workspace/f.md", "content": "x"}', "cost_usd": 0.001},
            {"text": '{"action": "done", "summary": "ok"}', "cost_usd": 0.001},
        ]
        pe = _make_executor(router=router)
        pe._execute_action = MagicMock(return_value={
            "success": True, "path": "workspace/f.md",
        })
        pe._verify_work = MagicMock(return_value={"passed": False, "cost": 0.001})
        with patch("src.core.plan_executor.executor._classify_error"):
            result = pe.execute("Task", task_id="t20")
        assert result["verified"] is False
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Constants sanity check
# ---------------------------------------------------------------------------

class TestConstants:
    """Module constants have expected values."""

    def test_max_steps(self):
        assert MAX_STEPS_PER_TASK == 50
        assert MAX_STEPS_CODING == 25
        assert MAX_STEPS_CHAT == 12

    def test_cost_cap(self):
        assert TASK_COST_CAP == 0.50

    def test_plan_max_tokens(self):
        assert PLAN_MAX_TOKENS == 4096


# ---------------------------------------------------------------------------
# _build_step_prompt — skill injection
# ---------------------------------------------------------------------------

class TestBuildStepPromptSkillInjection:
    """Skills from SkillRegistry appear in the step prompt."""

    def test_skills_injected_when_registry_has_skills(self):
        """When skills exist, _build_step_prompt includes CUSTOM SKILLS block."""
        mock_info = {
            "name": "summarize_web_pages",
            "description": "Summarize the content of a web page given a URL",
        }
        mock_registry = MagicMock()
        mock_registry.get_available_skills.return_value = ["summarize_web_pages"]
        mock_registry.get_skill_info.return_value = mock_info

        exe = _make_executor()
        with patch("src.core.skill_system.get_shared_skill_registry", return_value=mock_registry):
            prompt = exe._build_step_prompt("Summarize a web page", "", [])
        assert "CUSTOM SKILLS" in prompt
        assert "skill_summarize_web_pages" in prompt
        assert "Summarize the content" in prompt

    def test_no_skills_block_when_registry_empty(self):
        """When no skills exist, the prompt has no CUSTOM SKILLS block."""
        mock_registry = MagicMock()
        mock_registry.get_available_skills.return_value = []

        exe = _make_executor()
        with patch("src.core.skill_system.get_shared_skill_registry", return_value=mock_registry):
            prompt = exe._build_step_prompt("Do something", "", [])
        assert "CUSTOM SKILLS" not in prompt

    def test_skills_block_absent_on_import_error(self):
        """If skill_system import fails, prompt still builds without skills."""
        exe = _make_executor()
        with patch("src.core.skill_system.get_shared_skill_registry", side_effect=RuntimeError("broken")):
            prompt = exe._build_step_prompt("Do something", "", [])
        assert "CUSTOM SKILLS" not in prompt
        assert "EFFICIENCY RULES" in prompt  # rest of prompt intact
