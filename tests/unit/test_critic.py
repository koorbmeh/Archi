"""Unit tests for the Critic module — adversarial per-goal quality evaluation.

Tests critique_goal() with mocked router, severity parsing, remediation
task extraction, user model context injection, and edge cases.

Created session 74.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.core.critic import (
    critique_goal,
    _get_user_model_context,
    _no_concerns,
)


# ── _no_concerns() tests ────────────────────────────────────────────


class TestNoConcerns:
    """Tests for _no_concerns()."""

    def test_structure(self):
        r = _no_concerns()
        assert r["concerns"] == []
        assert r["remediation_tasks"] == []
        assert r["severity"] == "none"
        assert r["cost"] == 0


# ── _get_user_model_context() tests ──────────────────────────────────


class TestGetUserModelContext:
    """Tests for _get_user_model_context() — User Model injection."""

    def test_returns_empty_when_no_user_model(self):
        with patch("src.core.user_model.get_user_model", side_effect=ImportError):
            result = _get_user_model_context()
            assert result == ""

    def test_returns_empty_when_model_is_none(self):
        with patch("src.core.user_model.get_user_model", return_value=None):
            result = _get_user_model_context()
            assert result == ""

    def test_includes_preferences(self):
        mock_um = MagicMock()
        mock_um.preferences = [{"text": "Prefers concise code"}]
        mock_um.corrections = []
        mock_um.patterns = []
        with patch("src.core.user_model.get_user_model", return_value=mock_um):
            result = _get_user_model_context()
            assert "concise code" in result
            assert "PREFERENCES" in result

    def test_includes_corrections_and_patterns(self):
        mock_um = MagicMock()
        mock_um.preferences = []
        mock_um.corrections = [{"text": "Don't use global state"}]
        mock_um.patterns = [{"text": "Always logs errors"}]
        with patch("src.core.user_model.get_user_model", return_value=mock_um):
            result = _get_user_model_context()
            assert "global state" in result
            assert "logs errors" in result

    def test_truncates_long_context(self):
        mock_um = MagicMock()
        mock_um.preferences = [{"text": "x" * 200} for _ in range(5)]
        mock_um.corrections = [{"text": "y" * 200} for _ in range(3)]
        mock_um.patterns = [{"text": "z" * 200} for _ in range(3)]
        with patch("src.core.user_model.get_user_model", return_value=mock_um):
            result = _get_user_model_context()
            # The context body is truncated to ~500 chars, but wrapping text adds more
            assert len(result) < 700


# ── critique_goal() tests ────────────────────────────────────────────


class TestCritiqueGoal:
    """Tests for the main critique_goal() function."""

    def test_no_router_returns_clean(self):
        result = critique_goal("Goal", [], [], router=None)
        assert result == _no_concerns()

    def test_no_successful_tasks_returns_clean(self):
        tasks = [{"success": False, "task": "T1", "summary": "Failed"}]
        result = critique_goal("Goal", tasks, [], router=MagicMock())
        assert result["severity"] == "none"

    def test_severity_none(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "none", "concerns": [], "remediation_tasks": [], "summary": "Looks good."}',
            "cost_usd": 0.001,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done: Built it."}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["severity"] == "none"
        assert result["concerns"] == []
        assert result["remediation_tasks"] == []
        assert result["cost"] == 0.001

    def test_severity_minor(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "minor", "concerns": ["Could use better error handling"], "remediation_tasks": [], "summary": "Mostly fine."}',
            "cost_usd": 0.001,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Built module"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["severity"] == "minor"
        assert len(result["concerns"]) == 1
        assert result["remediation_tasks"] == []  # Minor = no remediation

    def test_severity_significant_with_remediation(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "significant", "concerns": ["Missing error handling", "No input validation"], "remediation_tasks": ["Add try/except to main handler", "Add input validation for user data"], "summary": "Has real problems."}',
            "cost_usd": 0.002,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Built API"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["severity"] == "significant"
        assert len(result["concerns"]) == 2
        assert len(result["remediation_tasks"]) == 2
        assert "try/except" in result["remediation_tasks"][0]

    def test_remediation_capped_at_two(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "significant", "concerns": ["A"], "remediation_tasks": ["Fix 1", "Fix 2", "Fix 3", "Fix 4"], "summary": "Bad."}',
            "cost_usd": 0,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert len(result["remediation_tasks"]) == 2

    def test_remediation_only_for_significant(self):
        """Minor severity should not include remediation even if model returns some."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "minor", "concerns": ["Small issue"], "remediation_tasks": ["Fix it"], "summary": "ok"}',
            "cost_usd": 0,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["remediation_tasks"] == []

    def test_invalid_severity_defaults_to_none(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "catastrophic", "concerns": ["Very bad"], "remediation_tasks": [], "summary": "Bad"}',
            "cost_usd": 0,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["severity"] == "none"

    def test_unparseable_response(self):
        router = MagicMock()
        router.generate.return_value = {"text": "This is not JSON at all", "cost_usd": 0.001}
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["severity"] == "none"
        assert result["cost"] == 0.001

    def test_router_exception_handled(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("API down")
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["severity"] == "none"
        assert result["cost"] == 0

    def test_reads_file_contents(self, tmp_path):
        """Critic should read file contents as evidence."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "none", "concerns": [], "remediation_tasks": [], "summary": "ok"}',
            "cost_usd": 0,
        }
        test_file = tmp_path / "output.py"
        test_file.write_text("def main():\n    print('hello')\n")
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        critique_goal("Goal", tasks, [str(test_file)], router=router)
        prompt = router.generate.call_args.kwargs["prompt"]
        assert "output.py" in prompt
        assert "def main" in prompt

    def test_user_model_context_injected(self):
        """Critic should include User Model context in its prompt."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "none", "concerns": [], "remediation_tasks": [], "summary": "ok"}',
            "cost_usd": 0,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        mock_um = MagicMock()
        mock_um.preferences = [{"text": "Concise documentation"}]
        mock_um.corrections = []
        mock_um.patterns = []
        with patch("src.core.user_model.get_user_model", return_value=mock_um):
            critique_goal("Goal", tasks, [], router=router)
            prompt = router.generate.call_args.kwargs["prompt"]
            assert "Concise documentation" in prompt

    def test_non_list_concerns_handled(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "minor", "concerns": "just a string", "remediation_tasks": [], "summary": "ok"}',
            "cost_usd": 0,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["concerns"] == []

    def test_empty_concerns_filtered(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "minor", "concerns": ["Real issue", "", null], "remediation_tasks": [], "summary": "ok"}',
            "cost_usd": 0,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert len(result["concerns"]) == 1
        assert result["concerns"][0] == "Real issue"
