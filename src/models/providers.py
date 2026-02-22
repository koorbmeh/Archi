"""
Provider registry for multi-provider LLM routing.

A provider is just config: base_url, api_key_env, default_model, headers, pricing.
The OpenRouterClient uses this to talk to any OpenAI-compatible endpoint.
"""

import os
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Provider definitions
# ---------------------------------------------------------------------------

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "x-ai/grok-4.1-fast",
        "headers": {
            "HTTP-Referer": os.environ.get(
                "OPENROUTER_APP_REFERER", "https://github.com/archi-agent"
            ),
            "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Archi"),
        },
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "default_model": "grok-4-1-fast-reasoning",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-sonnet-4-6",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "default_model": "mistral-medium-latest",
    },
}

# ---------------------------------------------------------------------------
# Model aliases — friendly names → (provider, full_model_id)
#
# Convention: Grok bare names route via xAI direct (session 90).
#             Other bare names route via OpenRouter.
#             "-direct" suffix routes to the provider's own API.
# ---------------------------------------------------------------------------

MODEL_ALIASES: Dict[str, Tuple[str, str]] = {
    # Grok: default to xAI direct (session 90 fix — was routing through OpenRouter)
    "grok": ("xai", "grok-4-1-fast-reasoning"),
    "grok-fast": ("xai", "grok-4-1-fast-reasoning"),
    "grok-4": ("xai", "grok-4-0709"),
    # OpenRouter-routed (non-Grok models)
    "deepseek": ("openrouter", "deepseek/deepseek-chat-v3-0324"),
    "minimax": ("openrouter", "minimax/minimax-m2.5"),
    "kimi": ("openrouter", "moonshotai/kimi-k2.5"),
    "gpt": ("openrouter", "openai/gpt-4o-mini"),
    "gpt-4o-mini": ("openrouter", "openai/gpt-4o-mini"),
    "claude": ("openrouter", "anthropic/claude-sonnet-4.6"),
    "claude-sonnet": ("openrouter", "anthropic/claude-sonnet-4.6"),
    "claude-sonnet-4.6": ("openrouter", "anthropic/claude-sonnet-4.6"),
    "claude-4.6": ("openrouter", "anthropic/claude-sonnet-4.6"),
    "claude-haiku": ("openrouter", "anthropic/claude-haiku-4.5"),
    "claude-opus": ("openrouter", "anthropic/claude-opus-4"),
    "mistral": ("openrouter", "mistralai/mistral-medium-3.1"),
    "auto": ("openrouter", "openrouter/auto"),
    # Explicit OpenRouter routing (for when user specifically wants OpenRouter)
    "grok-openrouter": ("openrouter", "x-ai/grok-4.1-fast"),
    # Direct provider routing (explicit -direct suffix, kept for backward compat)
    "grok-direct": ("xai", "grok-4-1-fast-reasoning"),
    "grok-fast-direct": ("xai", "grok-4-1-fast-reasoning"),
    "grok-4-direct": ("xai", "grok-4-0709"),
    "grok-3-direct": ("xai", "grok-3"),
    "grok-mini-direct": ("xai", "grok-3-mini"),
    "claude-direct": ("anthropic", "claude-sonnet-4-6"),
    "claude-haiku-direct": ("anthropic", "claude-haiku-4.5"),
    "claude-opus-direct": ("anthropic", "claude-opus-4"),
    "deepseek-direct": ("deepseek", "deepseek-chat"),
    "gpt-direct": ("openai", "gpt-4o-mini"),
    "mistral-direct": ("mistral", "mistral-medium-latest"),
}

# ---------------------------------------------------------------------------
# Model pricing (per 1M tokens) for cost estimation.
# Covers both OpenRouter model IDs and direct-provider model IDs.
# ---------------------------------------------------------------------------

MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # OpenRouter model IDs
    "x-ai/grok-4.1-fast": {"input": 0.20, "output": 0.50},
    "x-ai/grok-4-fast": {"input": 0.20, "output": 0.50},
    "x-ai/grok-4": {"input": 2.00, "output": 10.00},
    "deepseek/deepseek-chat-v3-0324": {"input": 0.14, "output": 0.28},
    "deepseek/deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek/deepseek-v3-0324": {"input": 0.28, "output": 0.42},
    "minimax/minimax-m2.5": {"input": 0.30, "output": 1.20},
    "moonshotai/kimi-k2.5": {"input": 0.50, "output": 2.80},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "anthropic/claude-haiku-4.5": {"input": 1.00, "output": 5.00},
    "anthropic/claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "anthropic/claude-sonnet-4.6": {"input": 3.00, "output": 15.00},
    "anthropic/claude-opus-4": {"input": 5.00, "output": 25.00},
    "mistralai/mistral-medium-3.1": {"input": 0.40, "output": 0.40},
    "openrouter/auto": {"input": 0.50, "output": 1.00},
    # Direct-provider model IDs (xAI uses different naming than OpenRouter)
    "grok-4-1-fast-non-reasoning": {"input": 0.20, "output": 0.50},
    "grok-4-1-fast-reasoning": {"input": 0.20, "output": 0.50},
    "grok-4-0709": {"input": 2.00, "output": 10.00},
    "grok-3": {"input": 3.00, "output": 15.00},
    "grok-3-mini": {"input": 0.30, "output": 0.50},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-haiku-4.5": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4": {"input": 5.00, "output": 25.00},
    "mistral-medium-latest": {"input": 0.40, "output": 0.40},
}

# Fallback pricing when model not found (conservative estimate).
DEFAULT_PRICING: Dict[str, float] = {"input": 0.50, "output": 2.00}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_alias(name: str) -> Tuple[str, str]:
    """Resolve a friendly name to (provider, model_id).

    Accepts:
        - Registered alias: "grok" → ("xai", "grok-4-1-fast-reasoning")
        - Provider/model path: "xai/grok-2" → ("xai", "grok-2")
        - Full OpenRouter path: "x-ai/grok-4.1-fast" → ("openrouter", "x-ai/grok-4.1-fast")
        - Raw model for openrouter: "some-new/model" → ("openrouter", "some-new/model")

    Raises ValueError if alias not found and format is ambiguous.
    """
    lower = name.strip().lower()

    # Check registered aliases first
    if lower in MODEL_ALIASES:
        return MODEL_ALIASES[lower]

    # "provider/model" format — check if first segment is a known provider
    if "/" in name:
        parts = name.split("/", 1)
        if parts[0].lower() in PROVIDERS:
            return (parts[0].lower(), parts[1])
        # Assume it's a full OpenRouter model path (e.g. "x-ai/grok-4.1-fast")
        return ("openrouter", name.strip())

    raise ValueError(
        f"Unknown model '{name}'. "
        f"Available aliases: {', '.join(sorted(MODEL_ALIASES.keys()))}"
    )


def get_api_key(provider: str) -> Optional[str]:
    """Load API key for a provider from environment."""
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return None
    return os.environ.get(cfg["api_key_env"])


def get_base_url(provider: str) -> Optional[str]:
    """Get base URL for a provider (env override or default)."""
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return None
    override = os.environ.get(f"{provider.upper()}_BASE_URL")
    return override or cfg.get("base_url")


def get_headers(provider: str) -> Dict[str, str]:
    """Get default headers for a provider."""
    cfg = PROVIDERS.get(provider, {})
    return dict(cfg.get("headers", {}))


def get_default_model(provider: str) -> Optional[str]:
    """Get the default model for a provider."""
    cfg = PROVIDERS.get(provider)
    return cfg.get("default_model") if cfg else None


def get_pricing(model: str) -> Dict[str, float]:
    """Look up pricing for a model. Falls back to conservative defaults."""
    return MODEL_PRICING.get(model, DEFAULT_PRICING)
