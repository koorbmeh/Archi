"""Unit tests for the Critic module — adversarial per-goal quality evaluation.

Tests critique_goal() with mocked router, severity parsing, remediation
task extraction, user model context injection, structured concern format,
and edge cases.

Created session 74.
Enhanced session 124 (structured error taxonomy for concerns).
"""

import pytest
from unittest.mock import MagicMock, patch

from src.core.critic import (
    critique_goal,
    format_concerns,
    _no_concerns,
    CRITIC_ERROR_TYPES,
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


# ── format_concerns() tests ─────────────────────────────────────────


class TestFormatConcerns:
    """Tests for format_concerns() — structured to string conversion."""

    def test_structured_concern(self):
        concerns = [{"type": "edge_case", "detail": "No null check"}]
        result = format_concerns(concerns)
        assert len(result) == 1
        assert "edge_case" in result[0]
        assert "No null check" in result[0]

    def test_plain_string_passthrough(self):
        """Backward compat: plain strings pass through."""
        result = format_concerns(["Just a string concern"])
        assert result == ["Just a string concern"]

    def test_empty_list(self):
        assert format_concerns([]) == []

    def test_mixed_types(self):
        concerns = [
            {"type": "style_mismatch", "detail": "Too verbose"},
            "Legacy string concern",
        ]
        result = format_concerns(concerns)
        assert len(result) == 2
        assert "style_mismatch" in result[0]
        assert result[1] == "Legacy string concern"


# ── UserModel.get_context_for_critic() tests ────────────────────────
# (Consolidated from _get_user_model_context — session 158)


class TestGetContextForCritic:
    """Tests for UserModel.get_context_for_critic() — used by Critic."""

    def test_returns_empty_when_no_data(self):
        from src.core.user_model import UserModel
        um = UserModel.__new__(UserModel)
        um.preferences = []
        um.corrections = []
        um.patterns = []
        assert um.get_context_for_critic() == ""

    def test_includes_preferences(self):
        from src.core.user_model import UserModel
        um = UserModel.__new__(UserModel)
        um.preferences = [{"text": "Prefers concise code"}]
        um.corrections = []
        um.patterns = []
        result = um.get_context_for_critic()
        assert "concise code" in result
        assert "PREFERENCES" in result

    def test_includes_corrections_and_patterns(self):
        from src.core.user_model import UserModel
        um = UserModel.__new__(UserModel)
        um.preferences = []
        um.corrections = [{"text": "Don't use global state"}]
        um.patterns = [{"text": "Always logs errors"}]
        result = um.get_context_for_critic()
        assert "global state" in result
        assert "logs errors" in result

    def test_truncates_long_context(self):
        from src.core.user_model import UserModel
        um = UserModel.__new__(UserModel)
        um.preferences = [{"text": "x" * 200} for _ in range(5)]
        um.corrections = [{"text": "y" * 200} for _ in range(3)]
        um.patterns = [{"text": "z" * 200} for _ in range(3)]
        result = um.get_context_for_critic()
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

    def test_severity_none_with_structured_concerns(self):
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

    def test_severity_minor_with_typed_concerns(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "minor", "concerns": [{"type": "edge_case", "detail": "Could use better error handling"}], "remediation_tasks": [], "summary": "Mostly fine."}',
            "cost_usd": 0.001,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Built module"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["severity"] == "minor"
        assert len(result["concerns"]) == 1
        assert result["concerns"][0]["type"] == "edge_case"
        assert result["concerns"][0]["detail"] == "Could use better error handling"
        assert result["remediation_tasks"] == []

    def test_severity_significant_with_remediation(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "significant", "concerns": [{"type": "missing_validation", "detail": "No input validation"}, {"type": "non_functional", "detail": "Missing error handling"}], "remediation_tasks": ["Add try/except to main handler", "Add input validation for user data"], "summary": "Has real problems."}',
            "cost_usd": 0.002,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Built API"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["severity"] == "significant"
        assert len(result["concerns"]) == 2
        assert result["concerns"][0]["type"] == "missing_validation"
        assert len(result["remediation_tasks"]) == 2
        assert "try/except" in result["remediation_tasks"][0]

    def test_remediation_capped_at_two(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "significant", "concerns": [{"type": "quality_concern", "detail": "A"}], "remediation_tasks": ["Fix 1", "Fix 2", "Fix 3", "Fix 4"], "summary": "Bad."}',
            "cost_usd": 0,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert len(result["remediation_tasks"]) == 2

    def test_remediation_only_for_significant(self):
        """Minor severity should not include remediation even if model returns some."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "minor", "concerns": [{"type": "quality_concern", "detail": "Small issue"}], "remediation_tasks": ["Fix it"], "summary": "ok"}',
            "cost_usd": 0,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["remediation_tasks"] == []

    def test_invalid_severity_defaults_to_none(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "catastrophic", "concerns": [{"type": "quality_concern", "detail": "Very bad"}], "remediation_tasks": [], "summary": "Bad"}',
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
        mock_um.get_context_for_critic.return_value = (
            "\nJESSE'S KNOWN PREFERENCES (from User Model):\n"
            "- Prefers: Concise documentation\n"
        )
        with patch("src.core.user_model.get_user_model", return_value=mock_um):
            critique_goal("Goal", tasks, [], router=router)
            prompt = router.generate.call_args.kwargs["prompt"]
            assert "Concise documentation" in prompt

    def test_plain_string_concerns_normalized(self):
        """Model returning plain string concerns gets them normalized to dicts."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "minor", "concerns": ["Legacy string concern"], "remediation_tasks": [], "summary": "ok"}',
            "cost_usd": 0,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert len(result["concerns"]) == 1
        assert isinstance(result["concerns"][0], dict)
        assert result["concerns"][0]["type"] == "quality_concern"
        assert result["concerns"][0]["detail"] == "Legacy string concern"

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
            "text": '{"severity": "minor", "concerns": [{"type": "edge_case", "detail": "Real issue"}, "", null], "remediation_tasks": [], "summary": "ok"}',
            "cost_usd": 0,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert len(result["concerns"]) == 1
        assert result["concerns"][0]["detail"] == "Real issue"

    def test_unknown_error_type_defaults(self):
        """Unknown error types from model get normalized to quality_concern."""
        router = MagicMock()
        router.generate.return_value = {
            "text": '{"severity": "minor", "concerns": [{"type": "invented_type", "detail": "Something"}], "remediation_tasks": [], "summary": "ok"}',
            "cost_usd": 0,
        }
        tasks = [{"success": True, "task": "T1", "summary": "Done"}]
        result = critique_goal("Goal", tasks, [], router=router)
        assert result["concerns"][0]["type"] == "quality_concern"


# ── CRITIC_ERROR_TYPES coverage ──────────────────────────────────


class TestCriticErrorTypes:
    """Tests for the critic error taxonomy."""

    def test_all_types_have_descriptions(self):
        for etype, desc in CRITIC_ERROR_TYPES.items():
            assert isinstance(desc, str) and len(desc) > 5, f"Bad description for {etype}"

    def test_expected_types_present(self):
        expected = [
            "style_mismatch", "edge_case", "quality_concern",
            "missing_validation", "non_functional", "not_useful",
        ]
        for t in expected:
            assert t in CRITIC_ERROR_TYPES, f"Missing critic type: {t}"
