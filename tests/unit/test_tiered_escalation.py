"""
Unit tests for tiered model escalation (Claude Sonnet 4.6 on failure).

Tests:
  1. providers.py — Claude Sonnet 4.6 aliases and pricing
  2. router.escalate_for_task() — context manager snapshot/restore
  3. Integration: escalation wiring in autonomous_executor QA retry path
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest


# ============================================================================
# 1. Provider aliases and pricing for Claude Sonnet 4.6
# ============================================================================

class TestSonnet46ProviderConfig:
    """Verify Claude Sonnet 4.6 is registered correctly."""

    def test_claude_alias_points_to_sonnet_46(self):
        from src.models.providers import MODEL_ALIASES
        provider, model = MODEL_ALIASES["claude"]
        assert provider == "openrouter"
        assert "4.6" in model

    def test_claude_sonnet_46_alias_exists(self):
        from src.models.providers import MODEL_ALIASES
        assert "claude-sonnet-4.6" in MODEL_ALIASES
        assert "claude-4.6" in MODEL_ALIASES

    def test_all_sonnet_46_aliases_resolve_same(self):
        from src.models.providers import MODEL_ALIASES
        target = MODEL_ALIASES["claude-sonnet-4.6"]
        assert MODEL_ALIASES["claude"] == target
        assert MODEL_ALIASES["claude-sonnet"] == target
        assert MODEL_ALIASES["claude-4.6"] == target

    def test_resolve_alias_works(self):
        from src.models.providers import resolve_alias
        provider, model = resolve_alias("claude-sonnet-4.6")
        assert provider == "openrouter"
        assert "claude-sonnet-4.6" in model

    def test_pricing_exists_for_openrouter_id(self):
        from src.models.providers import MODEL_PRICING
        pricing = MODEL_PRICING.get("anthropic/claude-sonnet-4.6")
        assert pricing is not None
        assert pricing["input"] == 3.00
        assert pricing["output"] == 15.00

    def test_pricing_exists_for_direct_id(self):
        from src.models.providers import MODEL_PRICING
        pricing = MODEL_PRICING.get("claude-sonnet-4-6")
        assert pricing is not None
        assert pricing["input"] == 3.00
        assert pricing["output"] == 15.00

    def test_claude_direct_alias_points_to_46(self):
        from src.models.providers import MODEL_ALIASES
        provider, model = MODEL_ALIASES["claude-direct"]
        assert provider == "anthropic"
        assert "4-6" in model


# ============================================================================
# 2. router.escalate_for_task() — snapshot/restore context manager
# ============================================================================

class TestEscalateForTask:
    """Verify the escalation context manager snapshots and restores state."""

    def _make_router(self):
        """Create a real-ish ModelRouter with mocked API client."""
        from src.models.router import ModelRouter
        mock_client = MagicMock()
        mock_client.provider = "xai"
        mock_client._runtime_model = "grok-4-1-fast-reasoning"
        mock_client.get_active_model.return_value = "grok-4-1-fast-reasoning"
        mock_client.switch_model = MagicMock()
        router = ModelRouter.__new__(ModelRouter)
        router._api = mock_client
        router._cache = MagicMock()
        router._stats_lock = __import__("threading").Lock()
        router._stats = {"api_used": 0, "total_cost": 0.0}
        router._force_api_override = False
        router._temp_remaining = 0
        router._temp_previous = None
        router._fallback = MagicMock()
        router._fallback_clients = {"xai": mock_client}
        return router

    @patch("src.models.router.OpenRouterClient")
    def test_escalation_switches_model(self, mock_orclient_cls):
        """Inside the context, the model should be switched to Claude."""
        router = self._make_router()
        mock_new_client = MagicMock()
        mock_new_client.provider = "openrouter"
        mock_new_client._runtime_model = "anthropic/claude-sonnet-4.6"
        mock_orclient_cls.return_value = mock_new_client

        with router.escalate_for_task("claude-sonnet-4.6") as switch:
            # Inside the block, model should be switched
            assert router._force_api_override is True
            assert router._api == mock_new_client

    @patch("src.models.router.OpenRouterClient")
    def test_escalation_restores_after_block(self, mock_orclient_cls):
        """After the context exits, the original model should be restored."""
        router = self._make_router()
        orig_client = router._api
        mock_new_client = MagicMock()
        mock_new_client.provider = "openrouter"
        mock_orclient_cls.return_value = mock_new_client

        with router.escalate_for_task("claude-sonnet-4.6"):
            pass

        # After exit, override should be restored to False
        assert router._force_api_override is False
        # Runtime model should be restored
        assert router._api._runtime_model == "grok-4-1-fast-reasoning"

    @patch("src.models.router.OpenRouterClient")
    def test_escalation_restores_on_exception(self, mock_orclient_cls):
        """Even if the block raises, state should be restored."""
        router = self._make_router()
        mock_new_client = MagicMock()
        mock_new_client.provider = "openrouter"
        mock_orclient_cls.return_value = mock_new_client

        with pytest.raises(ValueError):
            with router.escalate_for_task("claude-sonnet-4.6"):
                raise ValueError("task failed")

        # State should still be restored
        assert router._force_api_override is False

    def test_escalation_handles_unknown_alias(self):
        """If the alias doesn't resolve, the context still works (no-op)."""
        router = self._make_router()
        with router.escalate_for_task("nonexistent-model-xyz") as switch:
            assert switch.get("model") is None
        # Original state preserved
        assert router._force_api_override is False

    @patch("src.models.router.OpenRouterClient")
    def test_escalation_preserves_forced_override(self, mock_orclient_cls):
        """If the user had a permanent model override, it should be restored."""
        router = self._make_router()
        router._force_api_override = True  # User said "switch to grok"
        mock_new_client = MagicMock()
        mock_new_client.provider = "openrouter"
        mock_orclient_cls.return_value = mock_new_client

        with router.escalate_for_task("claude-sonnet-4.6"):
            pass

        # Should restore the forced override
        assert router._force_api_override is True
