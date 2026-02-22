"""
Unit tests for direct provider support (Anthropic, DeepSeek, OpenAI, Mistral, xAI).

Tests:
  1. Provider registry — all providers configured correctly
  2. Alias resolution — direct aliases resolve to correct provider/model
  3. Pricing — direct-provider model IDs all have pricing entries
  4. Helper functions — get_api_key, get_base_url, get_headers, get_default_model
  5. Client creation — OpenRouterClient works with each provider when key is set
  6. Fallback chain — chain builds correctly based on available API keys
  7. Model switching — switch_model() with direct aliases works end-to-end
"""

import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest


# ============================================================================
# 1. Provider registry configuration
# ============================================================================

class TestProviderRegistry:
    """Verify all providers are registered with correct config."""

    EXPECTED_PROVIDERS = ["openrouter", "xai", "anthropic", "deepseek", "openai", "mistral"]

    def test_all_providers_registered(self):
        from src.models.providers import PROVIDERS
        for p in self.EXPECTED_PROVIDERS:
            assert p in PROVIDERS, f"Provider '{p}' missing from PROVIDERS"

    @pytest.mark.parametrize("provider", EXPECTED_PROVIDERS)
    def test_provider_has_base_url(self, provider):
        from src.models.providers import PROVIDERS
        assert "base_url" in PROVIDERS[provider]
        assert PROVIDERS[provider]["base_url"].startswith("https://")

    @pytest.mark.parametrize("provider", EXPECTED_PROVIDERS)
    def test_provider_has_api_key_env(self, provider):
        from src.models.providers import PROVIDERS
        env_var = PROVIDERS[provider]["api_key_env"]
        assert env_var.endswith("_API_KEY") or env_var.endswith("_KEY")

    @pytest.mark.parametrize("provider", EXPECTED_PROVIDERS)
    def test_provider_has_default_model(self, provider):
        from src.models.providers import PROVIDERS
        assert "default_model" in PROVIDERS[provider]
        assert len(PROVIDERS[provider]["default_model"]) > 0

    def test_xai_base_url(self):
        from src.models.providers import PROVIDERS
        assert PROVIDERS["xai"]["base_url"] == "https://api.x.ai/v1"

    def test_anthropic_base_url(self):
        from src.models.providers import PROVIDERS
        assert PROVIDERS["anthropic"]["base_url"] == "https://api.anthropic.com/v1"

    def test_deepseek_base_url(self):
        from src.models.providers import PROVIDERS
        assert PROVIDERS["deepseek"]["base_url"] == "https://api.deepseek.com"

    def test_openai_base_url(self):
        from src.models.providers import PROVIDERS
        assert PROVIDERS["openai"]["base_url"] == "https://api.openai.com/v1"

    def test_mistral_base_url(self):
        from src.models.providers import PROVIDERS
        assert PROVIDERS["mistral"]["base_url"] == "https://api.mistral.ai/v1"


# ============================================================================
# 2. Direct alias resolution
# ============================================================================

class TestDirectAliasResolution:
    """Verify -direct aliases resolve to the correct provider and model."""

    DIRECT_ALIASES = {
        "grok-direct": ("xai", "grok-4-1-fast-reasoning"),
        "grok-fast-direct": ("xai", "grok-4-1-fast-reasoning"),
        "claude-direct": ("anthropic", "claude-sonnet-4-6"),
        "claude-haiku-direct": ("anthropic", "claude-haiku-4.5"),
        "claude-opus-direct": ("anthropic", "claude-opus-4"),
        "deepseek-direct": ("deepseek", "deepseek-chat"),
        "gpt-direct": ("openai", "gpt-4o-mini"),
        "mistral-direct": ("mistral", "mistral-medium-latest"),
    }

    @pytest.mark.parametrize("alias,expected", list(DIRECT_ALIASES.items()))
    def test_direct_alias_resolves(self, alias, expected):
        from src.models.providers import resolve_alias
        provider, model = resolve_alias(alias)
        assert provider == expected[0], f"{alias} resolved to provider '{provider}', expected '{expected[0]}'"
        assert model == expected[1], f"{alias} resolved to model '{model}', expected '{expected[1]}'"

    def test_provider_model_path_syntax(self):
        """'provider/model' format resolves to the correct provider."""
        from src.models.providers import resolve_alias
        provider, model = resolve_alias("xai/grok-3")
        assert provider == "xai"
        assert model == "grok-3"

    def test_unknown_provider_path_defaults_to_openrouter(self):
        """'unknown/model' defaults to OpenRouter."""
        from src.models.providers import resolve_alias
        provider, model = resolve_alias("x-ai/grok-4.1-fast")
        assert provider == "openrouter"
        assert model == "x-ai/grok-4.1-fast"

    def test_unknown_bare_alias_raises(self):
        from src.models.providers import resolve_alias
        with pytest.raises(ValueError, match="Unknown model"):
            resolve_alias("nonexistent-model")

    def test_resolve_alias_case_insensitive(self):
        from src.models.providers import resolve_alias
        provider1, model1 = resolve_alias("Grok-Direct")
        provider2, model2 = resolve_alias("grok-direct")
        assert provider1 == provider2
        assert model1 == model2


# ============================================================================
# 3. Direct provider pricing
# ============================================================================

class TestDirectProviderPricing:
    """Verify pricing entries exist for direct-provider model IDs."""

    DIRECT_MODELS = [
        "grok-4-1-fast-reasoning", "grok-4-0709", "grok-3", "grok-3-mini",
        "deepseek-chat",
        "gpt-4o-mini",
        "claude-haiku-4.5", "claude-sonnet-4-6", "claude-opus-4",
        "mistral-medium-latest",
    ]

    @pytest.mark.parametrize("model", DIRECT_MODELS)
    def test_pricing_exists(self, model):
        from src.models.providers import MODEL_PRICING
        assert model in MODEL_PRICING, f"No pricing for direct model '{model}'"

    @pytest.mark.parametrize("model", DIRECT_MODELS)
    def test_pricing_has_input_and_output(self, model):
        from src.models.providers import MODEL_PRICING
        pricing = MODEL_PRICING[model]
        assert "input" in pricing and pricing["input"] >= 0
        assert "output" in pricing and pricing["output"] >= 0

    def test_default_pricing_is_conservative(self):
        from src.models.providers import DEFAULT_PRICING, get_pricing
        # Unknown model should get conservative defaults
        pricing = get_pricing("some-unknown-model-xyz")
        assert pricing == DEFAULT_PRICING
        assert pricing["input"] > 0
        assert pricing["output"] > 0


# ============================================================================
# 4. Helper functions
# ============================================================================

class TestProviderHelpers:
    """Test provider helper functions across all providers."""

    @pytest.mark.parametrize("provider", ["xai", "anthropic", "deepseek", "openai", "mistral"])
    def test_get_api_key_returns_none_when_unset(self, provider):
        from src.models.providers import PROVIDERS, get_api_key
        env_var = PROVIDERS[provider]["api_key_env"]
        with patch.dict(os.environ, {}, clear=True):
            # Remove the key if set
            os.environ.pop(env_var, None)
            assert get_api_key(provider) is None

    @pytest.mark.parametrize("provider", ["xai", "anthropic", "deepseek", "openai", "mistral"])
    def test_get_api_key_returns_value_when_set(self, provider):
        from src.models.providers import PROVIDERS, get_api_key
        env_var = PROVIDERS[provider]["api_key_env"]
        with patch.dict(os.environ, {env_var: "test-key-123"}):
            assert get_api_key(provider) == "test-key-123"

    @pytest.mark.parametrize("provider", ["xai", "anthropic", "deepseek", "openai", "mistral"])
    def test_get_base_url(self, provider):
        from src.models.providers import get_base_url
        url = get_base_url(provider)
        assert url is not None
        assert url.startswith("https://")

    def test_get_base_url_env_override(self):
        from src.models.providers import get_base_url
        with patch.dict(os.environ, {"XAI_BASE_URL": "https://custom.example.com"}):
            assert get_base_url("xai") == "https://custom.example.com"

    def test_get_headers_openrouter_has_referer(self):
        from src.models.providers import get_headers
        headers = get_headers("openrouter")
        assert "HTTP-Referer" in headers

    @pytest.mark.parametrize("provider", ["xai", "anthropic", "deepseek", "openai", "mistral"])
    def test_get_headers_non_openrouter_empty(self, provider):
        from src.models.providers import get_headers
        headers = get_headers(provider)
        # Non-OpenRouter providers shouldn't have special headers
        assert isinstance(headers, dict)

    @pytest.mark.parametrize("provider", ["xai", "anthropic", "deepseek", "openai", "mistral"])
    def test_get_default_model(self, provider):
        from src.models.providers import get_default_model
        model = get_default_model(provider)
        assert model is not None
        assert len(model) > 0

    def test_get_api_key_unknown_provider(self):
        from src.models.providers import get_api_key
        assert get_api_key("nonexistent") is None

    def test_get_base_url_unknown_provider(self):
        from src.models.providers import get_base_url
        assert get_base_url("nonexistent") is None

    def test_get_default_model_unknown_provider(self):
        from src.models.providers import get_default_model
        assert get_default_model("nonexistent") is None


# ============================================================================
# 5. Client creation per provider
# ============================================================================

class TestClientCreation:
    """Test OpenRouterClient instantiation with different providers."""

    @pytest.mark.parametrize("provider,env_var", [
        ("xai", "XAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("mistral", "MISTRAL_API_KEY"),
    ])
    @patch("openai.OpenAI")
    def test_client_creates_with_key(self, mock_openai, provider, env_var):
        """Client creates successfully when API key is available."""
        with patch.dict(os.environ, {env_var: "test-key"}):
            from src.models.openrouter_client import OpenRouterClient
            client = OpenRouterClient(provider=provider)
            assert client.provider == provider
            assert client.get_active_model() is not None

    @pytest.mark.parametrize("provider,env_var", [
        ("xai", "XAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("mistral", "MISTRAL_API_KEY"),
    ])
    def test_client_raises_without_key(self, provider, env_var):
        """Client raises ValueError when API key is missing."""
        env = {k: v for k, v in os.environ.items() if k != env_var}
        with patch.dict(os.environ, env, clear=True):
            from src.models.openrouter_client import OpenRouterClient
            with pytest.raises(ValueError, match="not set"):
                OpenRouterClient(provider=provider)

    def test_client_raises_for_unknown_provider(self):
        from src.models.openrouter_client import OpenRouterClient
        with pytest.raises(ValueError, match="Unknown provider"):
            OpenRouterClient(provider="nonexistent-provider")

    @patch("openai.OpenAI")
    def test_client_explicit_api_key(self, mock_openai):
        """Passing api_key directly bypasses env lookup."""
        from src.models.openrouter_client import OpenRouterClient
        client = OpenRouterClient(provider="anthropic", api_key="explicit-key")
        assert client.provider == "anthropic"

    @patch("openai.OpenAI")
    def test_client_custom_base_url(self, mock_openai):
        from src.models.openrouter_client import OpenRouterClient
        client = OpenRouterClient(
            provider="xai", api_key="key", base_url="https://custom.api.com"
        )
        assert client._base_url == "https://custom.api.com"

    @patch("openai.OpenAI")
    def test_client_custom_default_model(self, mock_openai):
        from src.models.openrouter_client import OpenRouterClient
        client = OpenRouterClient(
            provider="deepseek", api_key="key", default_model="deepseek-v3"
        )
        assert client.get_active_model() == "deepseek-v3"

    @patch("openai.OpenAI")
    def test_client_switch_model(self, mock_openai):
        from src.models.openrouter_client import OpenRouterClient
        client = OpenRouterClient(provider="anthropic", api_key="key")
        client.switch_model("claude-opus-4")
        assert client.get_active_model() == "claude-opus-4"

    @patch("openai.OpenAI")
    def test_client_reset_model(self, mock_openai):
        from src.models.openrouter_client import OpenRouterClient
        client = OpenRouterClient(provider="anthropic", api_key="key")
        default = client.get_active_model()
        client.switch_model("claude-opus-4")
        client.reset_model()
        assert client.get_active_model() == default

    @patch("openai.OpenAI")
    def test_client_close_sets_flag(self, mock_openai):
        from src.models.openrouter_client import OpenRouterClient
        client = OpenRouterClient(provider="xai", api_key="key")
        assert client._closed is False
        client.close()
        assert client._closed is True

    @patch("openai.OpenAI")
    def test_client_is_available(self, mock_openai):
        from src.models.openrouter_client import OpenRouterClient
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "key"}):
            client = OpenRouterClient(provider="anthropic", api_key="key")
            assert client.is_available() is True


# ============================================================================
# 6. Fallback chain with direct providers
# ============================================================================

class TestFallbackChainProviders:
    """Test that the fallback chain correctly incorporates direct providers."""

    def test_chain_only_includes_keyed_providers(self):
        from src.models.fallback import ProviderFallbackChain
        with patch.dict(os.environ, {
            "XAI_API_KEY": "xai-key",
            "ANTHROPIC_API_KEY": "anth-key",
        }, clear=True):
            chain = ProviderFallbackChain(primary_provider="xai")
            providers = chain.get_chain()
            assert "xai" in providers
            assert "anthropic" in providers
            assert "deepseek" not in providers  # no key
            assert "openai" not in providers

    def test_chain_primary_is_first(self):
        from src.models.fallback import ProviderFallbackChain
        with patch.dict(os.environ, {
            "XAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k", "DEEPSEEK_API_KEY": "k",
        }, clear=True):
            chain = ProviderFallbackChain(primary_provider="anthropic")
            providers = chain.get_chain()
            assert providers[0] == "anthropic"

    def test_chain_empty_when_no_keys(self):
        from src.models.fallback import ProviderFallbackChain
        with patch.dict(os.environ, {}, clear=True):
            # Remove all provider keys
            for key in ["XAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY",
                        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "MISTRAL_API_KEY"]:
                os.environ.pop(key, None)
            chain = ProviderFallbackChain(primary_provider="xai")
            assert chain.all_providers_down() is True

    def test_provider_health_reports_all_in_chain(self):
        from src.models.fallback import ProviderFallbackChain
        with patch.dict(os.environ, {
            "XAI_API_KEY": "k", "DEEPSEEK_API_KEY": "k",
        }, clear=True):
            chain = ProviderFallbackChain(primary_provider="xai")
            health = chain.get_provider_health()
            assert "xai" in health
            assert "deepseek" in health
            assert health["xai"]["is_primary"] is True
            assert health["deepseek"]["is_primary"] is False

    def test_fallback_cascades_on_failure(self):
        """When primary fails, fallback tries next provider."""
        from src.models.fallback import ProviderFallbackChain
        with patch.dict(os.environ, {
            "XAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k",
        }, clear=True):
            chain = ProviderFallbackChain(primary_provider="xai")

            call_log = []
            def mock_call(provider):
                call_log.append(provider)
                if provider == "xai":
                    raise ConnectionError("xai down")
                return {"success": True, "text": "ok", "cost_usd": 0.001}

            response, used = chain.call_with_fallback(mock_call)
            assert response["success"] is True
            assert used == "anthropic"
            assert "xai" in call_log
            assert "anthropic" in call_log

    def test_fallback_marks_degraded(self):
        from src.models.fallback import ProviderFallbackChain
        with patch.dict(os.environ, {
            "XAI_API_KEY": "k", "DEEPSEEK_API_KEY": "k",
        }, clear=True):
            chain = ProviderFallbackChain(primary_provider="xai")
            assert chain.is_degraded is False

            def mock_call(provider):
                if provider == "xai":
                    raise ConnectionError("down")
                return {"success": True, "text": "ok"}

            chain.call_with_fallback(mock_call)
            assert chain.is_degraded is True

    def test_fallback_total_outage(self):
        from src.models.fallback import ProviderFallbackChain
        with patch.dict(os.environ, {"XAI_API_KEY": "k"}, clear=True):
            chain = ProviderFallbackChain(primary_provider="xai")

            def mock_call(provider):
                raise ConnectionError(f"{provider} down")

            # Need to fail enough times to trip the circuit breaker
            for _ in range(4):
                response, used = chain.call_with_fallback(mock_call)
            assert response["success"] is False
            assert used == "none"

    def test_reset_provider_works(self):
        from src.models.fallback import ProviderFallbackChain
        with patch.dict(os.environ, {"XAI_API_KEY": "k"}, clear=True):
            chain = ProviderFallbackChain(primary_provider="xai")
            assert chain.reset_provider("xai") is True
            assert chain.reset_provider("nonexistent") is False


# ============================================================================
# 7. Router model switching with direct providers
# ============================================================================

class TestRouterDirectSwitching:
    """Test ModelRouter.switch_model() with direct provider aliases."""

    def _make_router(self):
        from src.models.router import ModelRouter
        mock_client = MagicMock()
        mock_client.provider = "xai"
        mock_client._runtime_model = "grok-4-1-fast-reasoning"
        mock_client.get_active_model.return_value = "grok-4-1-fast-reasoning"
        mock_client.switch_model = MagicMock()
        router = ModelRouter.__new__(ModelRouter)
        router._api = mock_client
        router._cache = MagicMock()
        router._stats_lock = threading.Lock()
        router._stats = {"api_used": 0, "total_cost": 0.0}
        router._force_api_override = False
        router._temp_lock = threading.Lock()
        router._temp_remaining = 0
        router._temp_previous = None
        router._fallback = MagicMock()
        router._fallback_clients = {"xai": mock_client}
        return router

    @patch("src.models.router.OpenRouterClient")
    def test_switch_to_claude_direct(self, mock_cls):
        router = self._make_router()
        mock_new = MagicMock()
        mock_new.provider = "anthropic"
        mock_cls.return_value = mock_new

        result = router.switch_model("claude-direct")
        assert result["provider"] == "anthropic"
        assert result["model"] == "claude-sonnet-4-6"
        assert router._force_api_override is True

    @patch("src.models.router.OpenRouterClient")
    def test_switch_to_deepseek_direct(self, mock_cls):
        router = self._make_router()
        mock_new = MagicMock()
        mock_new.provider = "deepseek"
        mock_cls.return_value = mock_new

        result = router.switch_model("deepseek-direct")
        assert result["provider"] == "deepseek"
        assert result["model"] == "deepseek-chat"

    @patch("src.models.router.OpenRouterClient")
    def test_switch_to_gpt_direct(self, mock_cls):
        router = self._make_router()
        mock_new = MagicMock()
        mock_new.provider = "openai"
        mock_cls.return_value = mock_new

        result = router.switch_model("gpt-direct")
        assert result["provider"] == "openai"
        assert result["model"] == "gpt-4o-mini"

    @patch("src.models.router.OpenRouterClient")
    def test_switch_to_mistral_direct(self, mock_cls):
        router = self._make_router()
        mock_new = MagicMock()
        mock_new.provider = "mistral"
        mock_cls.return_value = mock_new

        result = router.switch_model("mistral-direct")
        assert result["provider"] == "mistral"
        assert result["model"] == "mistral-medium-latest"

    def test_switch_to_auto_resets(self):
        router = self._make_router()
        router._force_api_override = True
        with patch("src.models.router.OpenRouterClient") as mock_cls:
            mock_or = MagicMock()
            mock_or.provider = "openrouter"
            mock_cls.return_value = mock_or
            result = router.switch_model("auto")
        assert result["provider"] == "openrouter"
        assert router._force_api_override is False

    @patch("src.models.router.OpenRouterClient")
    def test_switch_fails_without_key(self, mock_cls):
        router = self._make_router()
        mock_cls.side_effect = ValueError("ANTHROPIC_API_KEY not set")

        result = router.switch_model("claude-direct")
        assert result["model"] is None
        assert "not set" in result["message"]

    @patch("src.models.router.OpenRouterClient")
    def test_temp_switch_direct_provider(self, mock_cls):
        router = self._make_router()
        mock_new = MagicMock()
        mock_new.provider = "anthropic"
        mock_cls.return_value = mock_new

        result = router.switch_model_temp("claude-direct", count=3)
        assert result["provider"] == "anthropic"
        assert result["temp_remaining"] == 3

    @patch("src.models.router.OpenRouterClient")
    def test_provider_path_syntax_switching(self, mock_cls):
        """'deepseek/deepseek-v3' syntax should work."""
        router = self._make_router()
        mock_new = MagicMock()
        mock_new.provider = "deepseek"
        mock_cls.return_value = mock_new

        result = router.switch_model("deepseek/deepseek-v3")
        assert result["provider"] == "deepseek"
        assert result["model"] == "deepseek-v3"

    def test_get_active_model_info(self):
        router = self._make_router()
        info = router.get_active_model_info()
        assert "model" in info
        assert "provider" in info
        assert "mode" in info


# ============================================================================
# 8. Cost estimation across providers
# ============================================================================

class TestCostEstimation:
    """Test cost estimation uses correct pricing per provider model."""

    @patch("openai.OpenAI")
    def test_xai_cost_estimation(self, mock_openai):
        from src.models.openrouter_client import OpenRouterClient
        client = OpenRouterClient(provider="xai", api_key="k")
        cost = client._estimate_cost("grok-4-1-fast-reasoning", 1000, 500)
        # $0.20/M input + $0.50/M output
        expected = (1000 * 0.20 / 1_000_000) + (500 * 0.50 / 1_000_000)
        assert abs(cost - expected) < 1e-8

    @patch("openai.OpenAI")
    def test_anthropic_cost_estimation(self, mock_openai):
        from src.models.openrouter_client import OpenRouterClient
        client = OpenRouterClient(provider="anthropic", api_key="k")
        cost = client._estimate_cost("claude-sonnet-4-6", 1000, 500)
        # $3.00/M input + $15.00/M output
        expected = (1000 * 3.00 / 1_000_000) + (500 * 15.00 / 1_000_000)
        assert abs(cost - expected) < 1e-8

    @patch("openai.OpenAI")
    def test_deepseek_cost_estimation(self, mock_openai):
        from src.models.openrouter_client import OpenRouterClient
        client = OpenRouterClient(provider="deepseek", api_key="k")
        cost = client._estimate_cost("deepseek-chat", 1000, 500)
        expected = (1000 * 0.14 / 1_000_000) + (500 * 0.28 / 1_000_000)
        assert abs(cost - expected) < 1e-8

    def test_unknown_model_uses_default_pricing(self):
        from src.models.openrouter_client import OpenRouterClient
        cost = OpenRouterClient._estimate_cost("some-future-model", 1000, 500)
        from src.models.providers import DEFAULT_PRICING
        expected = (1000 * DEFAULT_PRICING["input"] / 1_000_000) + (500 * DEFAULT_PRICING["output"] / 1_000_000)
        assert abs(cost - expected) < 1e-8
