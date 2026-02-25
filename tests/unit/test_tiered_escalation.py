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
    """Verify the escalation context manager uses thread-local state."""

    def _make_router(self):
        """Create a real-ish ModelRouter with mocked API client."""
        import threading
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
        router._thread_local = threading.local()
        return router

    @patch("src.models.router.OpenRouterClient")
    def test_escalation_switches_model(self, mock_orclient_cls):
        """Inside the context, thread-local should be set to escalated client."""
        router = self._make_router()
        mock_new_client = MagicMock()
        mock_new_client.provider = "openrouter"
        mock_new_client._runtime_model = "anthropic/claude-sonnet-4.6"
        mock_orclient_cls.return_value = mock_new_client

        with router.escalate_for_task("claude-sonnet-4.6") as switch:
            # Thread-local should be set (shared state unchanged)
            assert switch.get("model") is not None
            assert router._thread_local.escalation_model is not None
            assert router._thread_local.escalation_client is not None
            # Shared state must NOT change
            assert router._force_api_override is False

    @patch("src.models.router.OpenRouterClient")
    def test_escalation_restores_after_block(self, mock_orclient_cls):
        """After the context exits, thread-local should be cleared."""
        router = self._make_router()
        mock_new_client = MagicMock()
        mock_new_client.provider = "openrouter"
        mock_orclient_cls.return_value = mock_new_client

        with router.escalate_for_task("claude-sonnet-4.6"):
            pass

        # Thread-local should be cleared
        assert router._thread_local.escalation_client is None
        assert router._thread_local.escalation_model is None
        # Shared state unchanged
        assert router._force_api_override is False
        assert router._api._runtime_model == "grok-4-1-fast-reasoning"

    @patch("src.models.router.OpenRouterClient")
    def test_escalation_restores_on_exception(self, mock_orclient_cls):
        """Even if the block raises, thread-local should be cleared."""
        router = self._make_router()
        mock_new_client = MagicMock()
        mock_new_client.provider = "openrouter"
        mock_orclient_cls.return_value = mock_new_client

        with pytest.raises(ValueError):
            with router.escalate_for_task("claude-sonnet-4.6"):
                raise ValueError("task failed")

        # Thread-local should be cleared
        assert router._thread_local.escalation_client is None
        assert router._thread_local.escalation_model is None

    def test_escalation_handles_unknown_alias(self):
        """If the alias doesn't resolve, the context still works (no-op)."""
        router = self._make_router()
        with router.escalate_for_task("nonexistent-model-xyz") as switch:
            assert switch.get("model") is None
        # Original state preserved
        assert router._force_api_override is False

    @patch("src.models.router.OpenRouterClient")
    def test_escalation_preserves_forced_override(self, mock_orclient_cls):
        """Escalation does NOT touch the user's force override setting."""
        router = self._make_router()
        router._force_api_override = True  # User said "switch to grok"
        mock_new_client = MagicMock()
        mock_new_client.provider = "openrouter"
        mock_orclient_cls.return_value = mock_new_client

        with router.escalate_for_task("claude-sonnet-4.6"):
            # Force override should still be True (user setting, not touched)
            assert router._force_api_override is True

        # Should still be True
        assert router._force_api_override is True


# ============================================================================
# 3. Model-aware cache — prevents cache poisoning on QA escalation
# ============================================================================

class TestModelAwareCache:
    """Cache keys include the active model so escalation gets fresh responses."""

    def _make_router(self):
        """Create a ModelRouter with a real QueryCache and mocked API client."""
        import threading
        from src.models.router import ModelRouter
        from src.models.cache import QueryCache

        mock_client = MagicMock()
        mock_client.provider = "xai"
        mock_client._runtime_model = "grok-4-1-fast-reasoning"
        mock_client.get_active_model.return_value = "grok-4-1-fast-reasoning"

        router = ModelRouter.__new__(ModelRouter)
        router._api = mock_client
        router._cache = QueryCache(ttl_seconds=300)
        router._stats_lock = __import__("threading").Lock()
        router._stats = {"api_used": 0, "total_cost": 0.0}
        router._force_api_override = True
        router._temp_lock = __import__("threading").Lock()
        router._temp_remaining = 0
        router._temp_previous = None
        router._fallback = MagicMock()
        router._fallback_clients = {"xai": mock_client}
        router._thread_local = threading.local()
        return router

    def test_same_prompt_different_model_is_cache_miss(self):
        """Switching models should NOT return cached responses from the old model."""
        router = self._make_router()
        prompt = "What is the next action for this task?"

        # Simulate Grok response cached
        grok_response = {"text": "grok answer", "success": True, "cost_usd": 0.01, "model": "grok"}
        router._api.generate.return_value = grok_response
        result1 = router.generate(prompt=prompt, max_tokens=100)
        assert result1["text"] == "grok answer"

        # Switch to Claude
        router._api.get_active_model.return_value = "anthropic/claude-sonnet-4.6"
        claude_response = {"text": "claude answer", "success": True, "cost_usd": 0.05, "model": "claude"}
        router._api.generate.return_value = claude_response
        result2 = router.generate(prompt=prompt, max_tokens=100)

        # Should NOT get cached grok response — should be a fresh call
        assert result2["text"] == "claude answer"
        assert result2.get("cached") is not True

    def test_same_model_same_prompt_is_cache_hit(self):
        """Same model + same prompt should still return cached response."""
        router = self._make_router()
        prompt = "What is the next action for this task?"

        grok_response = {"text": "grok answer", "success": True, "cost_usd": 0.01, "model": "grok"}
        router._api.generate.return_value = grok_response
        router.generate(prompt=prompt, max_tokens=100)

        # Same model, same prompt — should be cache hit
        result2 = router.generate(prompt=prompt, max_tokens=100)
        assert result2.get("cached") is True
        assert result2["cost_usd"] == 0.0

    def test_escalation_bypasses_grok_cache(self):
        """Full escalation scenario: Grok cache should not poison Claude retry."""
        router = self._make_router()
        prompt = "Step 1: decide next action for task"

        # Grok run caches the response
        grok_response = {"text": "grok step", "success": True, "cost_usd": 0.01, "model": "grok"}
        router._api.generate.return_value = grok_response
        router.generate(prompt=prompt, max_tokens=100)

        # Simulate escalation: model changes to Claude
        router._api.get_active_model.return_value = "anthropic/claude-sonnet-4.6"
        claude_response = {"text": "claude step", "success": True, "cost_usd": 0.05, "model": "claude"}
        router._api.generate.return_value = claude_response
        result = router.generate(prompt=prompt, max_tokens=100)

        # Must NOT be the cached Grok response
        assert result["text"] == "claude step"
        assert result.get("cached") is not True
        # API should have been called twice (Grok + Claude, not served from cache)
        assert router._api.generate.call_count == 2

    def test_cache_stats_reflect_model_aware_misses(self):
        """Switching models should produce a cache miss, not a hit."""
        router = self._make_router()
        prompt = "test prompt"

        grok_response = {"text": "ok", "success": True, "cost_usd": 0.01, "model": "grok"}
        router._api.generate.return_value = grok_response
        router.generate(prompt=prompt, max_tokens=100)

        # Cache should have 1 hit (if called again with same model)
        router.generate(prompt=prompt, max_tokens=100)
        stats = router._cache.get_stats()
        assert stats["hits"] >= 1

        # Switch model and try again — should be a miss
        router._api.get_active_model.return_value = "claude-sonnet-4.6"
        router._api.generate.return_value = {"text": "ok2", "success": True, "cost_usd": 0.02, "model": "claude"}
        router.generate(prompt=prompt, max_tokens=100)
        stats2 = router._cache.get_stats()
        # Should have at least 2 misses (initial grok + claude switch)
        assert stats2["misses"] >= 2
