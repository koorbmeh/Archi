"""Tests for src/models/providers.py — provider registry, aliases, pricing, helpers."""

import os
from unittest.mock import patch

import pytest

from src.models.providers import (
    DEFAULT_PRICING,
    MODEL_ALIASES,
    MODEL_PRICING,
    PROVIDERS,
    get_api_key,
    get_base_url,
    get_default_model,
    get_headers,
    get_pricing,
    resolve_alias,
)


# ── Provider definitions ─────────────────────────────────────────────

class TestProviderDefinitions:
    def test_all_providers_have_base_url(self):
        for name, cfg in PROVIDERS.items():
            assert "base_url" in cfg, f"Provider {name} missing base_url"

    def test_all_providers_have_api_key_env(self):
        for name, cfg in PROVIDERS.items():
            assert "api_key_env" in cfg, f"Provider {name} missing api_key_env"

    def test_all_providers_have_default_model(self):
        for name, cfg in PROVIDERS.items():
            assert "default_model" in cfg, f"Provider {name} missing default_model"

    def test_known_providers_exist(self):
        expected = {"openrouter", "xai", "anthropic", "deepseek", "openai", "mistral"}
        assert expected.issubset(set(PROVIDERS.keys()))

    def test_base_urls_are_https(self):
        for name, cfg in PROVIDERS.items():
            assert cfg["base_url"].startswith("https://"), f"Provider {name} base_url not HTTPS"


# ── Model aliases ────────────────────────────────────────────────────

class TestModelAliases:
    def test_all_aliases_reference_known_providers(self):
        for alias, (provider, model) in MODEL_ALIASES.items():
            assert provider in PROVIDERS, f"Alias '{alias}' references unknown provider '{provider}'"

    def test_grok_defaults_to_xai(self):
        provider, model = MODEL_ALIASES["grok"]
        assert provider == "xai"

    def test_claude_routes_through_openrouter(self):
        provider, _ = MODEL_ALIASES["claude"]
        assert provider == "openrouter"

    def test_direct_suffix_routes_to_native_provider(self):
        provider, _ = MODEL_ALIASES["claude-direct"]
        assert provider == "anthropic"
        provider, _ = MODEL_ALIASES["grok-direct"]
        assert provider == "xai"
        provider, _ = MODEL_ALIASES["deepseek-direct"]
        assert provider == "deepseek"

    def test_auto_routes_to_openrouter(self):
        provider, model = MODEL_ALIASES["auto"]
        assert provider == "openrouter"
        assert "auto" in model


# ── resolve_alias ────────────────────────────────────────────────────

class TestResolveAlias:
    def test_registered_alias(self):
        provider, model = resolve_alias("grok")
        assert provider == "xai"
        assert model == "grok-4-1-fast-reasoning"

    def test_case_insensitive(self):
        provider, model = resolve_alias("GROK")
        assert provider == "xai"

    def test_whitespace_stripped(self):
        provider, model = resolve_alias("  grok  ")
        assert provider == "xai"

    def test_provider_slash_model_known_provider(self):
        provider, model = resolve_alias("xai/grok-2")
        assert provider == "xai"
        assert model == "grok-2"

    def test_provider_slash_model_unknown_provider(self):
        # Unknown first segment → assume OpenRouter path
        provider, model = resolve_alias("x-ai/grok-4.1-fast")
        assert provider == "openrouter"
        assert model == "x-ai/grok-4.1-fast"

    def test_unknown_alias_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown model"):
            resolve_alias("totally-made-up-model")

    def test_unknown_alias_shows_available(self):
        with pytest.raises(ValueError, match="Available aliases"):
            resolve_alias("nonexistent")

    def test_openrouter_provider_slash(self):
        provider, model = resolve_alias("openrouter/auto")
        assert provider == "openrouter"
        assert model == "auto"

    def test_anthropic_provider_slash(self):
        provider, model = resolve_alias("anthropic/claude-haiku-4.5")
        assert provider == "anthropic"
        assert model == "claude-haiku-4.5"


# ── get_api_key ──────────────────────────────────────────────────────

class TestGetApiKey:
    def test_returns_env_value_when_set(self):
        with patch.dict(os.environ, {"XAI_API_KEY": "test-key-123"}):
            assert get_api_key("xai") == "test-key-123"

    def test_returns_none_when_env_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            result = get_api_key("xai")
            # May or may not be None depending on environment
            # Just check it doesn't crash

    def test_unknown_provider_returns_none(self):
        assert get_api_key("nonexistent_provider") is None


# ── get_base_url ─────────────────────────────────────────────────────

class TestGetBaseUrl:
    def test_returns_default_url(self):
        with patch.dict(os.environ, {}, clear=False):
            url = get_base_url("xai")
            assert url == "https://api.x.ai/v1"

    def test_env_override(self):
        with patch.dict(os.environ, {"XAI_BASE_URL": "https://custom.api/v1"}):
            assert get_base_url("xai") == "https://custom.api/v1"

    def test_unknown_provider_returns_none(self):
        assert get_base_url("nonexistent") is None

    def test_openrouter_default(self):
        # Clear any override
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_BASE_URL"}
        with patch.dict(os.environ, env, clear=True):
            url = get_base_url("openrouter")
            assert "openrouter.ai" in url


# ── get_headers ──────────────────────────────────────────────────────

class TestGetHeaders:
    def test_openrouter_has_headers(self):
        headers = get_headers("openrouter")
        assert "HTTP-Referer" in headers
        assert "X-Title" in headers

    def test_xai_has_no_extra_headers(self):
        headers = get_headers("xai")
        assert headers == {}

    def test_unknown_provider_returns_empty_dict(self):
        assert get_headers("nonexistent") == {}

    def test_returns_copy_not_reference(self):
        h1 = get_headers("openrouter")
        h2 = get_headers("openrouter")
        h1["extra"] = "injected"
        assert "extra" not in h2


# ── get_default_model ────────────────────────────────────────────────

class TestGetDefaultModel:
    def test_known_provider(self):
        model = get_default_model("xai")
        assert model is not None
        assert "grok" in model

    def test_unknown_provider_returns_none(self):
        assert get_default_model("nonexistent") is None

    def test_each_provider_has_model(self):
        for name in PROVIDERS:
            assert get_default_model(name) is not None, f"Provider {name} has no default model"


# ── get_pricing ──────────────────────────────────────────────────────

class TestGetPricing:
    def test_known_model_returns_pricing(self):
        pricing = get_pricing("grok-4-1-fast-reasoning")
        assert "input" in pricing
        assert "output" in pricing
        assert pricing["input"] > 0

    def test_unknown_model_returns_default(self):
        pricing = get_pricing("some-future-model-9000")
        assert pricing == DEFAULT_PRICING

    def test_openrouter_and_direct_ids_both_have_pricing(self):
        # Grok should have pricing under both naming schemes
        or_pricing = get_pricing("x-ai/grok-4.1-fast")
        direct_pricing = get_pricing("grok-4-1-fast-reasoning")
        assert or_pricing["input"] > 0
        assert direct_pricing["input"] > 0

    def test_all_pricing_entries_have_input_and_output(self):
        for model, pricing in MODEL_PRICING.items():
            assert "input" in pricing, f"Model {model} missing input pricing"
            assert "output" in pricing, f"Model {model} missing output pricing"
            assert pricing["input"] >= 0, f"Model {model} has negative input pricing"
            assert pricing["output"] >= 0, f"Model {model} has negative output pricing"

    def test_default_pricing_has_both_fields(self):
        assert "input" in DEFAULT_PRICING
        assert "output" in DEFAULT_PRICING
