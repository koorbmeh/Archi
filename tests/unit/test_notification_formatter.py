"""Unit tests for src/core/notification_formatter.py.

Covers: _call_formatter, all format_* functions, all _fallback_* functions.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.notification_formatter import (
    _call_formatter,
    _fallback_finding,
    _fallback_goal_completion,
    _fallback_hourly_summary,
    _fallback_morning_report,
    _fallback_suggestions,
    format_conversation_starter,
    format_decomposition_failure,
    format_finding,
    format_goal_completion,
    format_hourly_summary,
    format_idle_prompt,
    format_initiative_announcement,
    format_interrupted_tasks,
    format_morning_report,
    format_suggestions,
)


# ---- TestCallFormatter ----

class TestCallFormatter:
    def test_returns_fallback_when_no_router(self):
        result = _call_formatter("prompt", None, fallback="fallback text")
        assert result["message"] == "fallback text"
        assert result["cost"] == 0.0

    def test_returns_model_response(self):
        router = MagicMock()
        router.generate.return_value = {"text": "Generated message here", "cost_usd": 0.001}
        result = _call_formatter("prompt", router, fallback="fallback")
        assert result["message"] == "Generated message here"
        assert result["cost"] == 0.001

    def test_strips_wrapping_quotes(self):
        router = MagicMock()
        router.generate.return_value = {"text": '"Quoted message"', "cost_usd": 0}
        result = _call_formatter("prompt", router, fallback="fb")
        assert result["message"] == "Quoted message"

    def test_strips_single_quotes(self):
        router = MagicMock()
        router.generate.return_value = {"text": "'Single quoted'", "cost_usd": 0}
        result = _call_formatter("prompt", router, fallback="fb")
        assert result["message"] == "Single quoted"

    def test_rejects_too_short_response(self):
        router = MagicMock()
        router.generate.return_value = {"text": "Short", "cost_usd": 0.001}
        result = _call_formatter("prompt", router, fallback="fallback")
        assert result["message"] == "fallback"

    def test_rejects_json_response(self):
        router = MagicMock()
        router.generate.return_value = {"text": '{"key": "value"}', "cost_usd": 0}
        result = _call_formatter("prompt", router, fallback="fallback")
        assert result["message"] == "fallback"

    def test_rejects_empty_response(self):
        router = MagicMock()
        router.generate.return_value = {"text": "", "cost_usd": 0}
        result = _call_formatter("prompt", router, fallback="fallback")
        assert result["message"] == "fallback"

    def test_handles_model_exception(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("API down")
        result = _call_formatter("prompt", router, fallback="safe fallback")
        assert result["message"] == "safe fallback"
        assert result["cost"] == 0.0

    def test_handles_none_text(self):
        router = MagicMock()
        router.generate.return_value = {"text": None, "cost_usd": 0}
        result = _call_formatter("prompt", router, fallback="fallback")
        assert result["message"] == "fallback"


# ---- TestFallbackGoalCompletion ----

class TestFallbackGoalCompletion:
    def test_all_tasks_succeeded(self):
        data = {"goal": "Build tracker", "tasks_completed": 3, "tasks_failed": 0}
        result = _fallback_goal_completion(data)
        assert "Done with Build tracker" in result

    def test_all_tasks_failed(self):
        data = {"goal": "Build thing", "tasks_completed": 0, "tasks_failed": 2}
        result = _fallback_goal_completion(data)
        assert "Couldn't make progress" in result
        assert "2 tasks" in result

    def test_mixed_success_failure(self):
        data = {"goal": "Build stuff", "tasks_completed": 2, "tasks_failed": 1}
        result = _fallback_goal_completion(data)
        assert "2 tasks finished" in result
        assert "1 had issues" in result

    def test_hit_budget(self):
        data = {"goal": "Big goal", "tasks_completed": 1, "tasks_failed": 0, "hit_budget": True}
        result = _fallback_goal_completion(data)
        assert "Pausing" in result
        assert "budget" in result

    def test_includes_summaries(self):
        data = {"goal": "Goal", "tasks_completed": 1, "tasks_failed": 0,
                "summaries": ["Created tracker.json", "Added validation"]}
        result = _fallback_goal_completion(data)
        assert "Created tracker.json" in result

    def test_includes_files_when_no_summaries(self):
        data = {"goal": "Goal", "tasks_completed": 1, "tasks_failed": 0,
                "files": ["tracker.json", "utils.py"]}
        result = _fallback_goal_completion(data)
        assert "tracker.json" in result

    def test_truncates_long_labels(self):
        data = {"goal": "x" * 100, "tasks_completed": 1, "tasks_failed": 0}
        result = _fallback_goal_completion(data)
        assert "…" in result


# ---- TestFallbackMorningReport ----

class TestFallbackMorningReport:
    def test_successes_only(self):
        data = {"successes": ["task1", "task2"], "failures": [], "total_cost": 0.05}
        result = _fallback_morning_report(data)
        assert "2 things done" in result
        assert "$0.0500" in result

    def test_failures_only(self):
        data = {"successes": [], "failures": ["bad task"], "total_cost": 0.01}
        result = _fallback_morning_report(data)
        assert "Rough night" in result

    def test_mixed(self):
        data = {"successes": ["good"], "failures": ["bad"], "total_cost": 0.02}
        result = _fallback_morning_report(data)
        assert "1 tasks done" in result
        assert "1 ran into issues" in result

    def test_quiet_night(self):
        data = {"successes": [], "failures": [], "total_cost": 0}
        result = _fallback_morning_report(data)
        assert "quiet night" in result

    def test_includes_user_goals(self):
        data = {"successes": ["ok"], "failures": [], "total_cost": 0,
                "user_goals": ["Goal A: 50% done"]}
        result = _fallback_morning_report(data)
        assert "Goal A: 50% done" in result

    def test_includes_finding(self):
        data = {"successes": ["ok"], "failures": [], "total_cost": 0,
                "finding": "Found interesting pattern"}
        result = _fallback_morning_report(data)
        assert "Found interesting pattern" in result


# ---- TestFallbackHourlySummary ----

class TestFallbackHourlySummary:
    def test_tasks_done_only(self):
        data = {"tasks_done": 3, "tasks_failed": 0}
        result = _fallback_hourly_summary(data)
        assert "finished 3 tasks" in result

    def test_mixed(self):
        data = {"tasks_done": 2, "tasks_failed": 1}
        result = _fallback_hourly_summary(data)
        assert "2 tasks" in result
        assert "1 had issues" in result

    def test_failures_only(self):
        data = {"tasks_done": 0, "tasks_failed": 2}
        result = _fallback_hourly_summary(data)
        assert "2 tasks ran into problems" in result

    def test_includes_user_goals(self):
        data = {"tasks_done": 1, "tasks_failed": 0, "user_goals": ["Progress on X"]}
        result = _fallback_hourly_summary(data)
        assert "Progress on X" in result

    def test_includes_files(self):
        data = {"tasks_done": 1, "tasks_failed": 0, "files": ["data.json"]}
        result = _fallback_hourly_summary(data)
        assert "data.json" in result


# ---- TestFallbackSuggestions ----

class TestFallbackSuggestions:
    def test_single_suggestion(self):
        items = [{"desc": "Build a tracker for health data", "cat": "build"}]
        result = _fallback_suggestions(items)
        assert "build a tracker" in result  # Lowercased first char
        assert "go ahead" in result

    def test_multiple_suggestions(self):
        items = [
            {"desc": "Build tracker", "cat": "build"},
            {"desc": "Fix error log", "cat": "fix"},
        ]
        result = _fallback_suggestions(items)
        assert "1. Build tracker" in result
        assert "2. Fix error log" in result
        assert "reply with a number" in result

    def test_includes_reasoning(self):
        items = [
            {"desc": "Build thing", "cat": "build", "why": "user asked for it"},
            {"desc": "Fix other", "cat": "fix", "why": "repeated errors"},
        ]
        result = _fallback_suggestions(items)
        assert "user asked for it" in result
        assert "repeated errors" in result

    def test_single_without_why(self):
        items = [{"desc": "Do something useful", "cat": "build"}]
        result = _fallback_suggestions(items)
        assert "go ahead" in result


# ---- TestFallbackFinding ----

class TestFallbackFinding:
    def test_basic(self):
        data = {"finding": "Interesting pattern in logs", "files": []}
        result = _fallback_finding(data)
        assert "Interesting pattern in logs" in result

    def test_with_files(self):
        data = {"finding": "Found thing", "files": ["report.md"]}
        result = _fallback_finding(data)
        assert "report.md" in result


# ---- TestFormatGoalCompletion ----

class TestFormatGoalCompletion:
    def test_calls_router_and_returns_message(self):
        router = MagicMock()
        router.generate.return_value = {"text": "I finished building the tracker for you!", "cost_usd": 0.0002}
        result = format_goal_completion(
            goal_description="Build health tracker",
            tasks_completed=2, tasks_failed=0, total_cost=0.05,
            task_summaries=["Created schema"], files_created=["tracker.json"],
            is_user_requested=True, hit_budget=False, is_significant=True,
            router=router,
        )
        assert result["message"] == "I finished building the tracker for you!"
        assert result["cost"] == 0.0002

    def test_falls_back_on_failure(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("fail")
        result = format_goal_completion(
            goal_description="Build thing",
            tasks_completed=1, tasks_failed=0, total_cost=0.01,
            task_summaries=[], files_created=[],
            is_user_requested=False, hit_budget=False, is_significant=False,
            router=router,
        )
        assert "Done with Build thing" in result["message"]


# ---- TestFormatMorningReport ----

class TestFormatMorningReport:
    def test_calls_router(self):
        router = MagicMock()
        router.generate.return_value = {"text": "Good morning! Got some work done overnight.", "cost_usd": 0.0002}
        result = format_morning_report(
            successes=[{"summary": "Done: built tracker", "task": "build tracker"}],
            failures=[], total_cost=0.03, user_goal_lines=[], finding_summary=None,
            router=router,
        )
        assert "morning" in result["message"].lower()

    def test_extracts_done_portion(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("fail")
        result = format_morning_report(
            successes=[{"summary": "Done: built tracker; cost: $0.01", "task": "build"}],
            failures=[], total_cost=0.01, user_goal_lines=[], finding_summary=None,
            router=router,
        )
        # Fallback should include success items
        assert "built tracker" in result["message"]


# ---- TestFormatHourlySummary ----

class TestFormatHourlySummary:
    def test_calls_router(self):
        router = MagicMock()
        router.generate.return_value = {"text": "Quick update — finished some tasks.", "cost_usd": 0.0001}
        result = format_hourly_summary(
            successes=[{"summary": "Done: fixed bug", "task": "fix", "files_created": []}],
            failures=[], files_created=[], user_goal_lines=[], finding_summary=None,
            router=router,
        )
        assert "Quick update" in result["message"]


# ---- TestFormatSuggestions ----

class TestFormatSuggestions:
    def test_single_suggestion(self):
        router = MagicMock()
        router.generate.return_value = {"text": "Hey — I could build a health tracker. Want me to go ahead?", "cost_usd": 0.0001}
        result = format_suggestions(
            suggestions=[{"description": "Build health tracker", "category": "build"}],
            router=router,
        )
        assert "tracker" in result["message"].lower()

    def test_multiple_suggestions(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": "Got some ideas:\n1. Build tracker\n2. Fix logs\nJust reply with a number.",
            "cost_usd": 0.0002,
        }
        result = format_suggestions(
            suggestions=[
                {"description": "Build tracker", "category": "build"},
                {"description": "Fix error logs", "category": "fix"},
            ],
            router=router,
        )
        assert len(result["message"]) > 10


# ---- TestFormatFinding ----

class TestFormatFinding:
    def test_calls_router(self):
        router = MagicMock()
        router.generate.return_value = {"text": "Hey, found something interesting while researching.", "cost_usd": 0.0001}
        result = format_finding(
            goal_description="Research patterns",
            finding_summary="Error rate decreased 40% after last fix",
            files_created=["analysis.md"],
            router=router,
        )
        assert "interesting" in result["message"].lower()


# ---- TestFormatInitiativeAnnouncement ----

class TestFormatInitiativeAnnouncement:
    def test_with_reasoning(self):
        router = MagicMock()
        router.generate.return_value = {"text": "Starting work on the health dashboard — noticed you've been tracking supplements.", "cost_usd": 0.0001}
        result = format_initiative_announcement(
            title="Health dashboard", why="User tracks supplements",
            router=router, reasoning="Based on conversation history",
            source="project_gap",
        )
        assert "dashboard" in result["message"].lower()

    def test_fallback_on_failure(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("fail")
        result = format_initiative_announcement(
            title="Build thing", why="User needs it", router=router,
        )
        assert "Build thing" in result["message"]


# ---- TestFormatConversationStarter ----

class TestFormatConversationStarter:
    def test_returns_empty_when_no_data(self):
        result = format_conversation_starter([], [], MagicMock())
        assert result["message"] == ""
        assert result["cost"] == 0.0

    def test_returns_model_response(self):
        router = MagicMock()
        router.generate.return_value = {"text": "Hey, did you end up trying that new supplement?", "cost_usd": 0.0001}
        result = format_conversation_starter(
            user_facts=["Takes magnesium", "Interested in health"],
            conversation_memories=["Discussed supplements last week"],
            router=router,
        )
        assert "supplement" in result["message"].lower()

    def test_skip_becomes_empty(self):
        router = MagicMock()
        router.generate.return_value = {"text": "SKIP", "cost_usd": 0.0001}
        result = format_conversation_starter(
            user_facts=["Some fact"], conversation_memories=[], router=router,
        )
        assert result["message"] == ""


# ---- TestFormatIdlePrompt ----

class TestFormatIdlePrompt:
    def test_calls_router(self):
        router = MagicMock()
        router.generate.return_value = {"text": "All caught up — anything on your mind?", "cost_usd": 0.0001}
        result = format_idle_prompt(router=router)
        assert len(result["message"]) > 10

    def test_fallback(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("fail")
        result = format_idle_prompt(router=router)
        assert "caught up" in result["message"]


# ---- TestFormatInterruptedTasks ----

class TestFormatInterruptedTasks:
    def test_single_task(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("fail")
        result = format_interrupted_tasks(
            tasks=[{"description": "Build health tracker"}], router=router,
        )
        assert "Picking up" in result["message"]
        assert "health tracker" in result["message"]

    def test_multiple_tasks(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("fail")
        result = format_interrupted_tasks(
            tasks=[{"description": "Task A"}, {"description": "Task B"}],
            router=router,
        )
        assert "Resuming 2 tasks" in result["message"]


# ---- TestFormatDecompositionFailure ----

class TestFormatDecompositionFailure:
    def test_fallback(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("fail")
        result = format_decomposition_failure(
            goal_description="Build complex system", router=router,
        )
        assert "Couldn't break down" in result["message"]

    def test_calls_router(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": "Ran into trouble breaking that goal into steps — might need to simplify it.",
            "cost_usd": 0.0001,
        }
        result = format_decomposition_failure(
            goal_description="Build complex system", router=router,
        )
        assert "trouble" in result["message"].lower()
