"""Unit tests for QA Evaluator — deterministic checks and verdict logic.

Tests _deterministic_checks() and evaluate_task() to ensure:
  - Missing files are caught (CRITICAL)
  - Empty files are caught (CRITICAL)
  - Python syntax errors are caught (CRITICAL)
  - Truncation markers are noted
  - Done summary quality is checked
  - Verdict logic (accept/reject/fail) works correctly
  - Schema retry exhaustion is handled

Created session 72.
"""

import os
import pytest

from src.core.qa_evaluator import (
    _deterministic_checks,
    evaluate_task,
    evaluate_goal,
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


class TestDeterministicChecks:
    """Tests for _deterministic_checks() — free quality checks."""

    def test_no_files_no_issues(self, workspace):
        """No files created and no done step → empty issues."""
        result = {"files_created": [], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert issues == []

    def test_missing_file_is_critical(self, workspace):
        """A file reported created but doesn't exist is a CRITICAL issue."""
        result = {
            "files_created": [str(workspace / "nonexistent.py")],
            "steps_taken": [],
        }
        issues = _deterministic_checks(result)
        assert len(issues) == 1
        assert "CRITICAL" in issues[0]
        assert "doesn't exist" in issues[0]

    def test_empty_file_is_critical(self, workspace):
        """An empty file (0 bytes) is a CRITICAL issue."""
        fpath = _make_file(workspace, "empty.py", "")
        result = {"files_created": [fpath], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert any("CRITICAL" in i and "empty" in i.lower() for i in issues)

    def test_small_file_is_note(self, workspace):
        """A very small file (< 50 bytes) gets a NOTE."""
        fpath = _make_file(workspace, "tiny.py", "x = 1\n")
        result = {"files_created": [fpath], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert any("NOTE" in i and "small" in i.lower() for i in issues)

    def test_normal_file_no_issues(self, workspace):
        """A normal-sized, valid file has no issues."""
        content = "# Report\n\n" + "This is a substantial report with real content.\n" * 10
        fpath = _make_file(workspace, "report.md", content)
        result = {
            "files_created": [fpath],
            "steps_taken": [{"action": "done", "summary": "Created a detailed report about the topic."}],
        }
        issues = _deterministic_checks(result)
        # Should have no CRITICAL issues
        assert not any("CRITICAL" in i for i in issues)

    def test_python_syntax_error_is_critical(self, workspace):
        """A Python file with syntax errors is a CRITICAL issue."""
        fpath = _make_file(workspace, "bad.py", "def broken(\n    pass\n")
        result = {"files_created": [fpath], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert any("CRITICAL" in i and "syntax" in i.lower() for i in issues)

    def test_valid_python_no_syntax_issue(self, workspace):
        """A valid Python file passes syntax check."""
        fpath = _make_file(workspace, "good.py", "def hello():\n    return 'world'\n")
        result = {"files_created": [fpath], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert not any("syntax" in i.lower() for i in issues)

    def test_truncation_markers_noted(self, workspace):
        """Files ending with common truncation markers get a NOTE."""
        for ending in ["...", "# TODO", "# ...", "pass  #"]:
            content = f"def func():\n    {ending}"
            fpath = _make_file(workspace, f"trunc_{ending[:3]}.py", content)
            result = {"files_created": [fpath], "steps_taken": []}
            issues = _deterministic_checks(result)
            assert any("NOTE" in i and "truncated" in i.lower() for i in issues), \
                f"Truncation marker '{ending}' not detected"

    def test_done_summary_missing(self, workspace):
        """Missing done summary gets a NOTE."""
        result = {
            "files_created": [],
            "steps_taken": [{"action": "done", "summary": ""}],
        }
        issues = _deterministic_checks(result)
        assert any("summary" in i.lower() for i in issues)

    def test_done_summary_too_short(self, workspace):
        """Very short done summary gets a NOTE."""
        result = {
            "files_created": [],
            "steps_taken": [{"action": "done", "summary": "Done."}],
        }
        issues = _deterministic_checks(result)
        assert any("summary" in i.lower() and "short" in i.lower() for i in issues)

    def test_force_stopped_task_is_critical(self, workspace):
        """Task that was force-stopped is a CRITICAL issue."""
        result = {
            "files_created": [],
            "steps_taken": [{"action": "done", "summary": "Task force-stopped due to loop detection."}],
        }
        issues = _deterministic_checks(result)
        assert any("CRITICAL" in i and "not complete" in i.lower() for i in issues)

    def test_cancelled_task_is_critical(self, workspace):
        """Cancelled task is a CRITICAL issue."""
        result = {
            "files_created": [],
            "steps_taken": [{"action": "done", "summary": "Task cancelled by user."}],
        }
        issues = _deterministic_checks(result)
        assert any("CRITICAL" in i for i in issues)

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
        # Should have at least 2 CRITICAL issues (syntax error + missing file)
        criticals = [i for i in issues if "CRITICAL" in i]
        assert len(criticals) >= 2

    def test_non_python_file_skips_syntax_check(self, workspace):
        """Non-.py files don't get Python syntax checks."""
        fpath = _make_file(workspace, "data.json", '{"valid": true}')
        result = {"files_created": [fpath], "steps_taken": []}
        issues = _deterministic_checks(result)
        assert not any("syntax" in i.lower() for i in issues)


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
        assert any("schema" in i.lower() for i in qa["issues"])

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
        # No router → semantic eval returns accept by default
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
        assert any("CRITICAL" in i for i in qa["issues"])

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
        # NOTE issues exist but should not cause reject on their own
        # (the model eval would need to reject, but with no router it accepts)
        assert qa["verdict"] == "accept"

    def test_max_qa_retries_constant(self):
        """MAX_QA_RETRIES is set to 1 (one retry allowed)."""
        assert MAX_QA_RETRIES == 1


class TestEvaluateGoal:
    """Tests for evaluate_goal() — goal-level conformance check."""

    def test_no_router_accepts(self):
        """Goal eval with no router defaults to accept (no model call possible)."""
        tasks = [
            {"description": "Task 1", "result": {"success": True}},
        ]
        qa = evaluate_goal("Build a tool", tasks, [], "", router=None)
        assert qa["verdict"] == "accept"
        assert qa["cost"] == 0

    def test_no_router_always_accepts(self):
        """Without a router, evaluate_goal always returns accept — the
        'no successful tasks' check only runs when a router is available."""
        tasks = [
            {"description": "Task 1", "result": {"success": False}},
        ]
        qa = evaluate_goal("Build a tool", tasks, [], "", router=None)
        assert qa["verdict"] == "accept"

    def test_no_successful_tasks_fails_with_router(self):
        """Goal with no successful tasks returns fail when router IS provided."""
        from unittest.mock import MagicMock
        mock_router = MagicMock()
        tasks = [
            {"description": "Task 1", "result": {"success": False}},
            {"description": "Task 2", "result": {"success": False}},
        ]
        qa = evaluate_goal("Build a tool", tasks, [], "", router=mock_router)
        assert qa["verdict"] == "fail"
        assert any("no tasks" in i.lower() for i in qa["issues"])
        assert qa["cost"] == 0

    def test_empty_tasks_fails_with_router(self):
        """Goal with empty task list fails when router is provided."""
        from unittest.mock import MagicMock
        mock_router = MagicMock()
        qa = evaluate_goal("Build a tool", [], [], "", router=mock_router)
        assert qa["verdict"] == "fail"
