"""Unit tests for the Integrator module — post-completion cross-task synthesis.

Tests helper functions (deterministic) and integrate_goal() with mocked router.

Created session 74.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

from src.core.integrator import (
    integrate_goal,
    _build_task_evidence,
    _read_file_contents,
    _single_task_summary,
    _fallback_summary,
    _empty_result,
)


# ── Helper function tests ────────────────────────────────────────────


class TestEmptyResult:
    """Tests for _empty_result()."""

    def test_returns_correct_structure(self):
        r = _empty_result()
        assert r["summary"] == ""
        assert r["issues_found"] == []
        assert r["glue_created"] == []
        assert r["cost"] == 0


class TestFallbackSummary:
    """Tests for _fallback_summary() — deterministic summary on model failure."""

    def test_with_files(self):
        tasks = [
            {"result": {"success": True}},
            {"result": {"success": True}},
            {"result": {"success": False}},
        ]
        files = ["/workspace/a.py", "/workspace/b.md"]
        summary = _fallback_summary(tasks, files)
        assert "2 tasks" in summary
        assert "a.py" in summary
        assert "b.md" in summary

    def test_without_files(self):
        tasks = [{"result": {"success": True}}]
        summary = _fallback_summary(tasks, [])
        assert "1 tasks" in summary
        assert "Files" not in summary

    def test_no_successful_tasks(self):
        tasks = [{"result": {"success": False}}]
        summary = _fallback_summary(tasks, [])
        assert "0 tasks" in summary


class TestSingleTaskSummary:
    """Tests for _single_task_summary() — no-model-call summary for 1-task goals."""

    def test_extracts_done_text(self):
        task = {"result": {"summary": "Step 1: searched. Done: Created a guide with 5 sections."}}
        summary = _single_task_summary(task, [])
        assert "guide" in summary.lower()

    def test_falls_back_to_description(self):
        task = {"description": "Write a report on X", "result": {"summary": ""}}
        summary = _single_task_summary(task, ["/workspace/report.md"])
        assert "report" in summary.lower()
        assert "report.md" in summary

    def test_short_done_text_uses_description(self):
        task = {"description": "Build the thing", "result": {"summary": "Done: ok"}}
        files = ["/workspace/thing.py"]
        summary = _single_task_summary(task, files)
        # "ok" is < 20 chars so it should use description path
        assert "Build the thing" in summary


class TestBuildTaskEvidence:
    """Tests for _build_task_evidence()."""

    def test_basic_evidence(self):
        tasks = [
            {
                "description": "Research topic X",
                "result": {"success": True, "summary": "Done: Found 5 sources about X"},
            },
            {
                "description": "Write report",
                "result": {"success": False, "summary": "Failed to write", "files_created": []},
            },
        ]
        evidence = _build_task_evidence(tasks)
        assert "Task 1 [DONE]" in evidence
        assert "Task 2 [FAILED]" in evidence
        assert "Research topic X" in evidence

    def test_includes_spec_fields(self):
        tasks = [{
            "description": "Build module",
            "expected_output": "A working Python module",
            "interfaces": ["api.get_data()"],
            "result": {"success": True, "summary": "", "files_created": ["/x/mod.py"]},
        }]
        evidence = _build_task_evidence(tasks)
        assert "Expected:" in evidence
        assert "Interfaces:" in evidence
        assert "mod.py" in evidence


class TestReadFileContents:
    """Tests for _read_file_contents()."""

    def test_reads_existing_files(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("print('hello')")
        f2 = tmp_path / "b.md"
        f2.write_text("# Guide")

        result = _read_file_contents([str(f1), str(f2)])
        assert "a.py" in result
        assert "print" in result
        assert "b.md" in result

    def test_skips_missing_files(self, tmp_path):
        f1 = tmp_path / "exists.py"
        f1.write_text("x = 1")
        result = _read_file_contents([str(f1), str(tmp_path / "nope.py")])
        assert "exists.py" in result

    def test_empty_list(self):
        result = _read_file_contents([])
        assert "no files" in result.lower()

    def test_caps_at_six_files(self, tmp_path):
        files = []
        for i in range(10):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content {i}")
            files.append(str(f))
        result = _read_file_contents(files)
        # Should only read first 6
        assert "file_0.txt" in result
        assert "file_5.txt" in result
        assert "file_6.txt" not in result


# ── integrate_goal() tests ───────────────────────────────────────────


class TestIntegrateGoal:
    """Tests for the main integrate_goal() function."""

    def test_no_router_returns_empty(self):
        result = integrate_goal("Test goal", [], [], router=None)
        assert result == _empty_result()

    def test_no_successful_tasks_returns_empty(self):
        tasks = [{"result": {"success": False}}]
        result = integrate_goal("Goal", tasks, [], router=MagicMock())
        assert result == _empty_result()

    def test_single_task_skips_model_call(self):
        """Single-task goal should produce summary without calling router."""
        router = MagicMock()
        tasks = [{
            "description": "Write guide",
            "result": {"success": True, "summary": "Done: Created comprehensive guide."},
        }]
        result = integrate_goal("Write a guide", tasks, [], router=router)
        router.generate.assert_not_called()
        assert result["cost"] == 0
        assert "guide" in result["summary"].lower()

    def test_multi_task_calls_router(self):
        """Multi-task goal should call router.generate()."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"summary": "Built a web scraper with 2 modules.", "issues": [], "missing_glue": []}',
            "cost_usd": 0.001,
        }
        tasks = [
            {"description": "Task 1", "result": {"success": True, "summary": "Done: Part 1"}},
            {"description": "Task 2", "result": {"success": True, "summary": "Done: Part 2"}},
        ]
        result = integrate_goal("Build scraper", tasks, [], router=router)
        router.generate.assert_called_once()
        assert "scraper" in result["summary"].lower()
        assert result["cost"] == 0.001
        assert result["issues_found"] == []

    def test_model_returns_issues(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"summary": "Built it.", "issues": ["Missing import in module A"], "missing_glue": ["__init__.py"]}',
            "cost_usd": 0.002,
        }
        tasks = [
            {"description": "T1", "result": {"success": True, "summary": "Done: A"}},
            {"description": "T2", "result": {"success": True, "summary": "Done: B"}},
        ]
        result = integrate_goal("Goal", tasks, [], router=router)
        assert len(result["issues_found"]) == 1
        assert "Missing import" in result["issues_found"][0]
        assert len(result["missing_glue"]) == 1

    def test_unparseable_response_uses_fallback(self):
        router = MagicMock()
        router.generate.return_value = {"text": "I don't know what JSON is", "cost_usd": 0.001}
        tasks = [
            {"description": "T1", "result": {"success": True, "summary": "Done: A"}},
            {"description": "T2", "result": {"success": True, "summary": "Done: B"}},
        ]
        result = integrate_goal("Goal", tasks, ["/workspace/a.py"], router=router)
        assert result["cost"] == 0.001
        assert "a.py" in result["summary"]  # Fallback includes file names

    def test_router_exception_uses_fallback(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("API down")
        tasks = [
            {"description": "T1", "result": {"success": True, "summary": ""}},
            {"description": "T2", "result": {"success": True, "summary": ""}},
        ]
        result = integrate_goal("Goal", tasks, [], router=router)
        assert result["cost"] == 0
        assert "2 tasks" in result["summary"]

    def test_discovery_brief_included_in_prompt(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"summary": "Done.", "issues": [], "missing_glue": []}',
            "cost_usd": 0,
        }
        tasks = [
            {"description": "T1", "result": {"success": True, "summary": "D1"}},
            {"description": "T2", "result": {"success": True, "summary": "D2"}},
        ]
        integrate_goal("Goal", tasks, [], router=router, discovery_brief="Project has existing API")
        prompt = router.generate.call_args.kwargs["prompt"]
        assert "existing API" in prompt

    def test_invalid_issues_type_handled(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"summary": "Done.", "issues": "not a list", "missing_glue": 42}',
            "cost_usd": 0,
        }
        tasks = [
            {"description": "T1", "result": {"success": True, "summary": ""}},
            {"description": "T2", "result": {"success": True, "summary": ""}},
        ]
        result = integrate_goal("Goal", tasks, [], router=router)
        assert result["issues_found"] == []
        assert result["missing_glue"] == []
