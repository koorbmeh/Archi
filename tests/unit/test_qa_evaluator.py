"""Unit tests for QA Evaluator — deterministic checks, semantic eval, goal QA,
verdict logic, error taxonomy, and formatting.

Created session 72. Enhanced session 124 (structured error taxonomy).
Expanded session 148 (_semantic_evaluation, evaluate_goal edge cases).
"""

import os
import pytest
from unittest.mock import MagicMock

from src.core.qa_evaluator import (
    _deterministic_checks,
    _semantic_evaluation,
    _normalize_model_issues,
    _call_qa_model,
    _build_task_evidence,
    evaluate_task,
    evaluate_goal,
    make_issue,
    format_issues,
    format_issues_for_retry,
    ERROR_TYPES,
    MAX_QA_RETRIES,
)


@pytest.fixture
def workspace(tmp_path):
    """Create a temp workspace with test files."""
    return tmp_path


def _make_file(workspace, name, content=""):
    """Helper to create a file in the workspace."""
    path = workspace / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


# ── make_issue() tests ─────────────────────────────────────────────


class TestMakeIssue:
    """Tests for the make_issue() helper."""

    def test_basic_issue(self):
        i = make_issue("syntax_error", "Bad code", severity="critical")
        assert i["type"] == "syntax_error"
        assert i["severity"] == "critical"
        assert i["detail"] == "Bad code"
        assert "step" not in i
        assert "file" not in i

    def test_with_step_and_file(self):
        i = make_issue("truncated", "File cut short", severity="note", step=3, file="out.py")
        assert i["step"] == 3
        assert i["file"] == "out.py"

    def test_default_severity(self):
        i = make_issue("invalid_output", "Wrong")
        assert i["severity"] == "warning"


# ── format_issues() tests ──────────────────────────────────────────


class TestFormatIssues:
    """Tests for format_issues() — structured to string conversion."""

    def test_basic_format(self):
        issues = [make_issue("syntax_error", "Bad syntax", severity="critical", file="a.py")]
        result = format_issues(issues)
        assert len(result) == 1
        assert "CRITICAL" in result[0]
        assert "syntax_error" in result[0]
        assert "a.py" in result[0]
        assert "Bad syntax" in result[0]

    def test_with_step_reference(self):
        issues = [make_issue("invalid_output", "Wrong output", severity="warning", step=5)]
        result = format_issues(issues)
        assert "step 5" in result[0]

    def test_empty_list(self):
        assert format_issues([]) == []

    def test_handles_plain_strings(self):
        """Backward compat: plain strings pass through."""
        result = format_issues(["some old-format issue"])
        assert result == ["some old-format issue"]


# ── format_issues_for_retry() tests ────────────────────────────────


class TestFormatIssuesForRetry:
    """Tests for format_issues_for_retry() — targeted retry feedback."""

    def test_groups_by_type(self):
        issues = [
            make_issue("syntax_error", "Error in a.py", file="a.py"),
            make_issue("syntax_error", "Error in b.py", file="b.py"),
        ]
        result = format_issues_for_retry(issues)
        assert "QA feedback:" in result
        assert "2x" in result  # Grouped as 2 of same type

    def test_single_issue_with_location(self):
        issues = [make_issue("invalid_output", "Not what was asked", step=3, file="out.md")]
        result = format_issues_for_retry(issues)
        assert "in out.md" in result
        assert "at step 3" in result

    def test_empty_returns_empty(self):
        assert format_issues_for_retry([]) == ""


# ── _deterministic_checks() tests ─────────────────────────────────


class TestDeterministicChecks:
    """Tests for _deterministic_checks() — free quality checks with structured output."""

    def test_no_files_no_issues(self, workspace):
        """No files created and no done step → empty issues."""
        result = {"files_created": [], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert issues == []

    def test_missing_file_is_critical(self, workspace):
        """A file reported created but doesn't exist is a critical issue."""
        result = {
            "files_created": [str(workspace / "nonexistent.py")],
            "steps_taken": [],
        }
        issues = _deterministic_checks(result)
        assert len(issues) == 1
        assert issues[0]["severity"] == "critical"
        assert issues[0]["type"] == "missing_file"
        assert "doesn't exist" in issues[0]["detail"]

    def test_empty_file_is_critical(self, workspace):
        """An empty file (0 bytes) is a critical issue."""
        fpath = _make_file(workspace, "empty.py", "")
        result = {"files_created": [fpath], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert any(
            i["severity"] == "critical" and i["type"] == "empty_file"
            for i in issues
        )

    def test_small_file_is_note(self, workspace):
        """A very small file (< 50 bytes) gets a note."""
        fpath = _make_file(workspace, "tiny.py", "x = 1\n")
        result = {"files_created": [fpath], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert any(
            i["severity"] == "note" and i["type"] == "small_file"
            for i in issues
        )

    def test_normal_file_no_issues(self, workspace):
        """A normal-sized, valid file has no issues."""
        content = "# Report\n\n" + "This is a substantial report with real content.\n" * 10
        fpath = _make_file(workspace, "report.md", content)
        result = {
            "files_created": [fpath],
            "steps_taken": [{"action": "done", "summary": "Created a detailed report about the topic."}],
        }
        issues = _deterministic_checks(result)
        assert not any(i["severity"] == "critical" for i in issues)

    def test_python_syntax_error_is_critical(self, workspace):
        """A Python file with syntax errors is a critical issue."""
        fpath = _make_file(workspace, "bad.py", "def broken(\n    pass\n")
        result = {"files_created": [fpath], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert any(
            i["severity"] == "critical" and i["type"] == "syntax_error"
            for i in issues
        )

    def test_valid_python_no_syntax_issue(self, workspace):
        """A valid Python file passes syntax check."""
        fpath = _make_file(workspace, "good.py", "def hello():\n    return 'world'\n")
        result = {"files_created": [fpath], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert not any(i["type"] == "syntax_error" for i in issues)

    def test_truncation_markers_noted(self, workspace):
        """Files ending with common truncation markers get a note."""
        for ending in ["...", "# TODO", "# ...", "pass  #"]:
            content = f"def func():\n    {ending}"
            fpath = _make_file(workspace, f"trunc_{ending[:3]}.py", content)
            result = {"files_created": [fpath], "steps_taken": []}
            issues = _deterministic_checks(result)
            assert any(
                i["severity"] == "note" and i["type"] == "truncated"
                for i in issues
            ), f"Truncation marker '{ending}' not detected"

    def test_done_summary_missing(self, workspace):
        """Missing done summary gets a note."""
        result = {
            "files_created": [],
            "steps_taken": [{"action": "done", "summary": ""}],
        }
        issues = _deterministic_checks(result)
        assert any(i["type"] == "weak_summary" for i in issues)

    def test_done_summary_too_short(self, workspace):
        """Very short done summary gets a note."""
        result = {
            "files_created": [],
            "steps_taken": [{"action": "done", "summary": "Done."}],
        }
        issues = _deterministic_checks(result)
        assert any(i["type"] == "weak_summary" for i in issues)

    def test_force_stopped_task_is_critical(self, workspace):
        """Task that was force-stopped is a critical issue."""
        result = {
            "files_created": [],
            "steps_taken": [{"action": "done", "summary": "Task force-stopped due to loop detection."}],
        }
        issues = _deterministic_checks(result)
        assert any(
            i["severity"] == "critical" and i["type"] == "incomplete_task"
            for i in issues
        )

    def test_cancelled_task_is_critical(self, workspace):
        """Cancelled task is a critical issue."""
        result = {
            "files_created": [],
            "steps_taken": [{"action": "done", "summary": "Task cancelled by user."}],
        }
        issues = _deterministic_checks(result)
        assert any(i["severity"] == "critical" for i in issues)

    def test_multiple_files_mixed_issues(self, workspace):
        """Multiple files — some good, some bad — each checked independently."""
        good_file = _make_file(workspace, "good.md", "# Good Report\n" + "Content " * 20)
        bad_file = _make_file(workspace, "bad.py", "def broken(\n")
        missing_file = str(workspace / "ghost.txt")
        result = {
            "files_created": [good_file, bad_file, missing_file],
            "steps_taken": [],
        }
        issues = _deterministic_checks(result)
        criticals = [i for i in issues if i["severity"] == "critical"]
        assert len(criticals) >= 2

    def test_non_python_file_skips_syntax_check(self, workspace):
        """Non-.py files don't get Python syntax checks."""
        fpath = _make_file(workspace, "data.json", '{"valid": true}')
        result = {"files_created": [fpath], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert not any(i["type"] == "syntax_error" for i in issues)

    def test_issues_have_file_references(self, workspace):
        """Structured issues include the file field for file-related checks."""
        fpath = _make_file(workspace, "empty.py", "")
        result = {"files_created": [fpath], "steps_taken": []}
        issues = _deterministic_checks(result)
        file_issues = [i for i in issues if i.get("file")]
        assert len(file_issues) >= 1
        assert file_issues[0]["file"] == "empty.py"


# ── evaluate_task() tests ──────────────────────────────────────────


class TestEvaluateTask:
    """Tests for evaluate_task() — combined deterministic + semantic eval."""

    def test_failed_task_returns_fail_verdict(self, workspace):
        """A task that reported failure gets fail verdict without model call."""
        result = {
            "success": False,
            "files_created": [],
            "steps_taken": [],
        }
        qa = evaluate_task("Write a report", "Research project", result, router=None)
        assert qa["verdict"] == "fail"
        assert qa["cost"] == 0
        assert any(i["type"] == "no_output" for i in qa["issues"])

    def test_schema_retry_exhaustion(self, workspace):
        """Schema retry exhaustion gets specific fail verdict."""
        result = {
            "success": False,
            "schema_retries_exhausted": True,
            "files_created": [],
            "steps_taken": [],
        }
        qa = evaluate_task("Write code", "Build tool", result, router=None)
        assert qa["verdict"] == "fail"
        assert any(i["type"] == "schema_exhausted" for i in qa["issues"])

    def test_successful_task_no_router_accepts(self, workspace):
        """Successful task with no router (no model eval) accepts if deterministic OK."""
        content = "# Report\n" + "Content goes here.\n" * 5
        fpath = _make_file(workspace, "report.md", content)
        result = {
            "success": True,
            "files_created": [fpath],
            "steps_taken": [{"action": "done", "summary": "Created a comprehensive report on the topic."}],
        }
        qa = evaluate_task("Write a report", "Research", result, router=None)
        assert qa["verdict"] == "accept"
        assert qa["cost"] == 0

    def test_critical_deterministic_issue_rejects(self, workspace):
        """Critical deterministic issues cause reject even without model eval."""
        missing = str(workspace / "missing.py")
        result = {
            "success": True,
            "files_created": [missing],
            "steps_taken": [{"action": "done", "summary": "Created the requested Python script."}],
        }
        qa = evaluate_task("Write a script", "Build tool", result, router=None)
        assert qa["verdict"] == "reject"
        assert any(i["severity"] == "critical" for i in qa["issues"])

    def test_note_issues_dont_reject_alone(self, workspace):
        """NOTE-level issues don't cause rejection by themselves."""
        content = "x = 1\n"  # Small but valid Python
        fpath = _make_file(workspace, "small.py", content)
        result = {
            "success": True,
            "files_created": [fpath],
            "steps_taken": [{"action": "done", "summary": "Created a minimal configuration script."}],
        }
        qa = evaluate_task("Write config", "Setup project", result, router=None)
        assert qa["verdict"] == "accept"

    def test_max_qa_retries_constant(self):
        """MAX_QA_RETRIES is set to 1 (one retry allowed)."""
        assert MAX_QA_RETRIES == 1

    def test_feedback_uses_structured_format(self, workspace):
        """Feedback string should be generated from structured issues."""
        missing = str(workspace / "ghost.py")
        result = {
            "success": True,
            "files_created": [missing],
            "steps_taken": [{"action": "done", "summary": "Created the requested Python script."}],
        }
        qa = evaluate_task("Write a script", "Build tool", result, router=None)
        # Feedback should mention error type
        assert "missing_file" in qa["feedback"].lower() or "QA feedback" in qa["feedback"]

    def test_issues_are_structured_dicts(self, workspace):
        """Issues should be structured dicts, not plain strings."""
        result = {
            "success": False,
            "files_created": [],
            "steps_taken": [],
        }
        qa = evaluate_task("Write code", "Build tool", result, router=None)
        for issue in qa["issues"]:
            assert isinstance(issue, dict)
            assert "type" in issue
            assert "severity" in issue
            assert "detail" in issue

    def test_semantic_eval_with_router(self, workspace):
        """Semantic evaluation with a mocked router returns structured issues."""
        from unittest.mock import MagicMock
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "reject", "issues": [{"type": "invalid_output", "detail": "Just a summary, not deliverables", "step": 4}], "reasoning": "Too shallow"}',
            "cost_usd": 0.002,
        }
        content = "# Summary\nThis is a report.\n" + "x " * 20
        fpath = _make_file(workspace, "out.md", content)
        result = {
            "success": True,
            "files_created": [fpath],
            "steps_taken": [
                {"action": "web_search", "params": {"query": "test"}},
                {"action": "done", "summary": "Created a summary report about the topic."},
            ],
        }
        qa = evaluate_task("Write full analysis", "Research", result, router=router)
        assert qa["verdict"] == "reject"
        sem_issues = [i for i in qa["issues"] if i["type"] == "invalid_output"]
        assert len(sem_issues) == 1
        assert sem_issues[0].get("step") == 4

    def test_semantic_eval_plain_string_issues(self, workspace):
        """Model returning plain strings instead of dicts gets wrapped."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "reject", "issues": ["Not what was asked", "Missing data"], "reasoning": "Bad"}',
            "cost_usd": 0.001,
        }
        content = "# Output\n" + "content " * 20
        fpath = _make_file(workspace, "out.md", content)
        result = {
            "success": True,
            "files_created": [fpath],
            "steps_taken": [{"action": "done", "summary": "Created a report about the topic for user."}],
        }
        qa = evaluate_task("Do X", "Goal Y", result, router=router)
        # Plain strings get wrapped as invalid_output type
        for issue in qa["issues"]:
            assert isinstance(issue, dict)
            assert "type" in issue

    def test_det_notes_dont_override_model_accept(self, workspace):
        """NOTE-severity deterministic issues don't override model accept."""
        content = "x = 1\n"  # Small file (note) but valid
        fpath = _make_file(workspace, "small.py", content)
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "accept", "issues": [], "reasoning": "Fine"}',
            "cost_usd": 0.001,
        }
        result = {
            "success": True,
            "files_created": [fpath],
            "steps_taken": [{"action": "done", "summary": "Created the minimal config with correct values."}],
            "total_steps": 1, "successful_steps": 1,
        }
        qa = evaluate_task("Create config", "Setup", result, router=router)
        assert qa["verdict"] == "accept"


# ── _semantic_evaluation() tests ──────────────────────────────────


class TestSemanticEvaluation:
    """Tests for _semantic_evaluation() — model-based quality check."""

    def test_no_router_accepts(self):
        result = _semantic_evaluation("task", "goal", {}, None)
        assert result["verdict"] == "accept"
        assert result["cost"] == 0

    def test_model_exception_accepts(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("API down")
        result = _semantic_evaluation("task", "goal", {
            "steps_taken": [], "files_created": [],
        }, router)
        assert result["verdict"] == "accept"

    def test_unparseable_response_accepts(self):
        router = MagicMock()
        router.generate.return_value = {"text": "not json", "cost_usd": 0.001}
        result = _semantic_evaluation("task", "goal", {
            "steps_taken": [], "files_created": [],
        }, router)
        assert result["verdict"] == "accept"

    def test_accept_with_reasoning(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "accept", "issues": [], "reasoning": "Looks good"}',
            "cost_usd": 0.002,
        }
        result = _semantic_evaluation("task", "goal", {
            "steps_taken": [{"action": "done", "summary": "Done"}],
            "files_created": [], "total_steps": 1, "successful_steps": 1,
        }, router)
        assert result["verdict"] == "accept"
        assert result["cost"] == 0.002

    def test_reject_with_typed_issues(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "reject", "issues": [{"type": "placeholder_content", "detail": "Just stubs", "step": 2}], "reasoning": ""}',
            "cost_usd": 0.001,
        }
        result = _semantic_evaluation("task", "goal", {
            "steps_taken": [], "files_created": [],
            "total_steps": 3, "successful_steps": 2,
        }, router)
        assert result["verdict"] == "reject"
        assert result["issues"][0]["type"] == "placeholder_content"
        assert result["issues"][0]["step"] == 2

    def test_string_issues_wrapped(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "reject", "issues": ["plain string"], "reasoning": ""}',
            "cost_usd": 0.001,
        }
        result = _semantic_evaluation("task", "goal", {
            "steps_taken": [], "files_created": [],
            "total_steps": 0, "successful_steps": 0,
        }, router)
        assert result["issues"][0]["type"] == "invalid_output"

    def test_unknown_type_normalized(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "reject", "issues": [{"type": "fake_type", "detail": "x"}], "reasoning": ""}',
            "cost_usd": 0.001,
        }
        result = _semantic_evaluation("task", "goal", {
            "steps_taken": [], "files_created": [],
            "total_steps": 0, "successful_steps": 0,
        }, router)
        assert result["issues"][0]["type"] == "invalid_output"

    def test_invalid_verdict_normalized(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "maybe", "issues": [], "reasoning": ""}',
            "cost_usd": 0.001,
        }
        result = _semantic_evaluation("task", "goal", {
            "steps_taken": [], "files_created": [],
            "total_steps": 0, "successful_steps": 0,
        }, router)
        assert result["verdict"] == "accept"

    def test_null_issues_ignored(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "reject", "issues": [null, "", {"type": "invalid_output", "detail": "real"}], "reasoning": ""}',
            "cost_usd": 0.001,
        }
        result = _semantic_evaluation("task", "goal", {
            "steps_taken": [], "files_created": [],
            "total_steps": 0, "successful_steps": 0,
        }, router)
        assert len(result["issues"]) == 1
        assert result["issues"][0]["detail"] == "real"

    def test_issues_not_a_list(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "accept", "issues": "not a list", "reasoning": ""}',
            "cost_usd": 0.001,
        }
        result = _semantic_evaluation("task", "goal", {
            "steps_taken": [], "files_created": [],
            "total_steps": 0, "successful_steps": 0,
        }, router)
        assert result["issues"] == []

    def test_step_log_includes_various_actions(self, workspace):
        """Various step types are formatted correctly in the prompt."""
        f = workspace / "out.txt"
        f.write_text("content " * 50)
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "accept", "issues": [], "reasoning": "ok"}',
            "cost_usd": 0.001,
        }
        _semantic_evaluation("task", "goal", {
            "steps_taken": [
                {"action": "web_search", "params": {"query": "test"}},
                {"action": "create_file", "params": {"path": "/tmp/f.py"}},
                {"action": "read_file", "params": {"path": "/tmp/in.txt"}},
                {"action": "edit_file", "params": {"path": "/tmp/f.py"}},
                {"action": "think"},  # Skipped in step log
                {"action": "done", "summary": "All done"},
            ],
            "files_created": [str(f)],
            "total_steps": 6, "successful_steps": 6,
        }, router)
        router.generate.assert_called_once()
        prompt = router.generate.call_args[1].get("prompt", "") or router.generate.call_args[0][0] if router.generate.call_args[0] else ""
        # The prompt should include step references


# ── evaluate_goal() tests ──────────────────────────────────────────


class TestEvaluateGoal:
    """Tests for evaluate_goal() — goal-level conformance check."""

    def test_no_router_accepts(self):
        """Goal eval with no router defaults to accept."""
        tasks = [
            {"description": "Task 1", "result": {"success": True}},
        ]
        qa = evaluate_goal("Build a tool", tasks, [], "", router=None)
        assert qa["verdict"] == "accept"
        assert qa["cost"] == 0

    def test_no_router_always_accepts(self):
        """Without a router, evaluate_goal always returns accept."""
        tasks = [
            {"description": "Task 1", "result": {"success": False}},
        ]
        qa = evaluate_goal("Build a tool", tasks, [], "", router=None)
        assert qa["verdict"] == "accept"

    def test_no_successful_tasks_fails_with_router(self):
        """Goal with no successful tasks returns fail when router IS provided."""
        mock_router = MagicMock()
        tasks = [
            {"description": "Task 1", "result": {"success": False}},
            {"description": "Task 2", "result": {"success": False}},
        ]
        qa = evaluate_goal("Build a tool", tasks, [], "", router=mock_router)
        assert qa["verdict"] == "fail"
        assert any(i["type"] == "no_successful_tasks" for i in qa["issues"])
        assert qa["cost"] == 0

    def test_empty_tasks_fails_with_router(self):
        """Goal with empty task list fails when router is provided."""
        mock_router = MagicMock()
        qa = evaluate_goal("Build a tool", [], [], "", router=mock_router)
        assert qa["verdict"] == "fail"

    def test_goal_issues_are_structured(self):
        """Goal-level QA returns structured issues."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "reject", "issues": [{"type": "conformance_gap", "detail": "Missing API module"}], "reasoning": "Incomplete"}',
            "cost_usd": 0.002,
        }
        tasks = [{"description": "T1", "result": {"success": True}}]
        qa = evaluate_goal("Build API", tasks, [], "", router=router)
        assert qa["verdict"] == "reject"
        assert qa["issues"][0]["type"] == "conformance_gap"
        assert isinstance(qa["issues"][0], dict)

    def test_model_exception_accepts(self):
        """Model failure during goal eval gracefully accepts."""
        router = MagicMock()
        router.generate.side_effect = RuntimeError("API down")
        tasks = [{"description": "T1", "result": {"success": True, "files_created": []}}]
        qa = evaluate_goal("goal", tasks, [], "", router=router)
        assert qa["verdict"] == "accept"

    def test_unparseable_response_accepts(self):
        router = MagicMock()
        router.generate.return_value = {"text": "gibberish", "cost_usd": 0.001}
        tasks = [{"description": "T1", "result": {"success": True, "files_created": []}}]
        qa = evaluate_goal("goal", tasks, [], "", router=router)
        assert qa["verdict"] == "accept"

    def test_string_issues_normalized(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "reject", "issues": ["string issue"], "reasoning": ""}',
            "cost_usd": 0.001,
        }
        tasks = [{"description": "T1", "result": {"success": True, "files_created": []}}]
        qa = evaluate_goal("goal", tasks, [], "", router=router)
        assert qa["issues"][0]["type"] == "conformance_gap"

    def test_unknown_type_normalized(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "reject", "issues": [{"type": "fake_type", "detail": "x"}], "reasoning": ""}',
            "cost_usd": 0.001,
        }
        tasks = [{"description": "T1", "result": {"success": True, "files_created": []}}]
        qa = evaluate_goal("goal", tasks, [], "", router=router)
        assert qa["issues"][0]["type"] == "conformance_gap"

    def test_invalid_verdict_normalized(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "unknown", "issues": [], "reasoning": ""}',
            "cost_usd": 0.001,
        }
        tasks = [{"description": "T1", "result": {"success": True, "files_created": []}}]
        qa = evaluate_goal("goal", tasks, [], "", router=router)
        assert qa["verdict"] == "accept"

    def test_file_evidence_read(self, workspace):
        f = _make_file(workspace, "output.txt", "Real content for eval")
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "accept", "issues": [], "reasoning": "good"}',
            "cost_usd": 0.001,
        }
        tasks = [{"description": "T1", "result": {"success": True, "files_created": [f]}}]
        qa = evaluate_goal("goal", tasks, [f], "", router=router)
        assert qa["verdict"] == "accept"

    def test_missing_file_in_evidence(self, workspace):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "accept", "issues": [], "reasoning": "ok"}',
            "cost_usd": 0.001,
        }
        tasks = [{"description": "T1", "result": {"success": True, "files_created": []}}]
        qa = evaluate_goal("goal", tasks, ["/nonexistent/file.txt"], "", router=router)
        assert qa["verdict"] == "accept"

    def test_mixed_success_and_failure_tasks(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "accept", "issues": [], "reasoning": "ok"}',
            "cost_usd": 0.001,
        }
        tasks = [
            {"description": "T1", "result": {"success": True, "files_created": []}},
            {"description": "T2", "result": {"success": False}},
        ]
        qa = evaluate_goal("goal", tasks, [], "", router=router)
        assert qa["verdict"] == "accept"

    def test_feedback_from_rejected_issues(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"verdict": "reject", "issues": [{"type": "missing_component", "detail": "No tests"}], "reasoning": ""}',
            "cost_usd": 0.001,
        }
        tasks = [{"description": "T1", "result": {"success": True, "files_created": []}}]
        qa = evaluate_goal("goal", tasks, [], "", router=router)
        assert qa["feedback"] != ""
        assert "QA feedback" in qa["feedback"]


# ── ERROR_TYPES coverage ──────────────────────────────────────────


class TestErrorTypes:
    """Tests for the error taxonomy constants."""

    def test_all_deterministic_types_documented(self):
        """All error types used in deterministic checks exist in the taxonomy."""
        det_types = [
            "missing_file", "empty_file", "unreadable_file", "syntax_error",
            "truncated", "small_file", "incomplete_task", "weak_summary",
        ]
        for t in det_types:
            assert t in ERROR_TYPES, f"Missing deterministic type: {t}"

    def test_all_semantic_types_documented(self):
        """All error types used in semantic evaluation exist in the taxonomy."""
        sem_types = [
            "invalid_output", "placeholder_content", "missing_precondition",
            "stale_data", "wrong_approach",
        ]
        for t in sem_types:
            assert t in ERROR_TYPES, f"Missing semantic type: {t}"

    def test_all_goal_types_documented(self):
        """All error types used in goal-level QA exist in the taxonomy."""
        goal_types = [
            "conformance_gap", "dangling_reference", "missing_component",
            "no_successful_tasks",
        ]
        for t in goal_types:
            assert t in ERROR_TYPES, f"Missing goal-level type: {t}"
