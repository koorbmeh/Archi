"""
Universal LLM client (OpenAI-compatible). Works with any provider in providers.py.

Default provider is OpenRouter. Add API keys for other providers to use them
directly (e.g. XAI_API_KEY for xAI, ANTHROPIC_API_KEY for Anthropic).

Setup (default — OpenRouter):
  1. Create account at https://openrouter.ai
  2. Generate API key at https://openrouter.ai/keys
  3. Set OPENROUTER_API_KEY in .env
"""

import logging
import os
import time
from typing import Any, Dict, Optional

from src.models.providers import (
    PROVIDERS,
    get_api_key,
    get_base_url,
    get_default_model,
    get_headers,
    get_pricing,
)

# Explicit logger name: file is openrouter_client.py but now serves multiple
# providers (xAI, Google, Anthropic, OpenRouter).  Session 161.
logger = logging.getLogger("src.models.llm_client")

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
TIMEOUT_SEC = 60.0


class OpenRouterClient:
    """Client for OpenAI-compatible LLM APIs with retries, cost tracking, and timeouts.

    Despite the name (kept for backward compatibility), this client works with
    any provider defined in providers.py — OpenRouter, xAI, Anthropic, DeepSeek, etc.
    """

    def __init__(
        self,
        provider: str = "openrouter",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = TIMEOUT_SEC,
        default_model: Optional[str] = None,
    ) -> None:
        if provider not in PROVIDERS:
            raise ValueError(
                f"Unknown provider '{provider}'. "
                f"Available: {', '.join(sorted(PROVIDERS.keys()))}"
            )

        self._provider = provider
        key = api_key or get_api_key(provider)
        if not key:
            env_var = PROVIDERS[provider]["api_key_env"]
            raise ValueError(
                f"{env_var} not set in environment. "
                f"Set it in .env to use {provider} directly."
            )
        self._api_key = key
        self._base_url = base_url or get_base_url(provider) or ""
        self._timeout = timeout

        # Model: explicit > env override (OpenRouter only) > provider default
        if default_model:
            self._default_model = default_model
        elif provider == "openrouter":
            self._default_model = (
                os.environ.get("OPENROUTER_MODEL") or get_default_model(provider) or ""
            )
        else:
            self._default_model = get_default_model(provider) or ""

        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=key,
                base_url=self._base_url,
                default_headers=get_headers(provider),
            )
        except ImportError:
            raise ImportError("openai package required; pip install openai")

        # Runtime model override — set via switch_model(), persists until
        # changed again or process restarts (reverts to _default_model).
        self._runtime_model: Optional[str] = None
        self._closed = False

        logger.debug(
            "LLM client initialized (provider=%s, base_url=%s, default_model=%s)",
            provider, self._base_url, self._default_model,
        )

    @property
    def provider(self) -> str:
        """Return the provider name for this client instance."""
        return self._provider

    # ------------------------------------------------------------------
    # Model switching
    # ------------------------------------------------------------------

    def switch_model(self, model_id: str) -> str:
        """Switch the active model by full model ID.

        Provider-aware alias resolution happens in router.py / providers.py,
        not here. This method just sets the runtime model string.
        """
        self._runtime_model = model_id.strip()
        logger.info("Model switched to %s (provider: %s)", self._runtime_model, self._provider)
        return self._runtime_model

    def get_active_model(self) -> str:
        """Return the currently active model (runtime override or default)."""
        return self._runtime_model or self._default_model

    def reset_model(self) -> str:
        """Reset to the default model (from env / config)."""
        self._runtime_model = None
        logger.info("Model reset to default: %s", self._default_model)
        return self._default_model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str = "",
        max_tokens: int = 500,
        temperature: float = 0.7,
        model: Optional[str] = None,
        enable_web_search: bool = False,
        system_prompt: Optional[str] = None,
        messages: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Generate a completion. Returns dict with text, tokens, cost_usd, success, error.

        Two calling conventions:
        1. prompt + system_prompt (legacy) — builds [system, user] messages internally.
        2. messages (new) — caller supplies a full messages array.
        """
        if enable_web_search:
            logger.debug("enable_web_search ignored — use local WebSearchTool")
        model = model or self._runtime_model or self._default_model
        return self._generate_chat_completions(
            prompt, model, max_tokens, temperature,
            system_prompt=system_prompt, messages=messages,
        )

    def generate_with_vision(
        self,
        prompt: str,
        image_base64: str,
        image_media_type: str = "image/png",
        max_tokens: int = 200,
        temperature: float = 0.2,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate with image input (vision). Returns dict with text, cost_usd, success."""
        model = (
            model
            or os.environ.get("OPENROUTER_VISION_MODEL")
            or self._default_model
        )
        start = time.perf_counter()
        url = f"data:{image_media_type};base64,{image_base64}"
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": url}},
        ]
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=self._timeout,
            )
        except Exception as e:
            logger.error("%s vision API failed: %s", self._provider, e)
            return _error_result(str(e), time.perf_counter() - start)

        msg = response.choices[0].message.content
        usage = getattr(response, "usage", None)
        input_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        actual_model = getattr(response, "model", model) or model
        cost = self._estimate_cost(actual_model, input_tok, output_tok)
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "%s vision [%s]: %d in + %d out = $%.6f in %d ms",
            self._provider, actual_model, input_tok, output_tok, cost, duration_ms,
        )
        return {
            "text": msg,
            "tokens": input_tok + output_tok,
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "duration_ms": duration_ms,
            "cost_usd": cost,
            "model": actual_model,
            "success": True,
        }

    def close(self) -> None:
        """Close the underlying httpx transport.

        This immediately fails all in-flight API requests, unblocking any
        threads waiting on responses.  Called during shutdown so
        ThreadPoolExecutor workers release and Python can exit cleanly.
        """
        self._closed = True
        try:
            self._client.close()
            logger.debug("LLM client closed (provider=%s)", self._provider)
        except Exception as e:
            logger.debug("LLM client close error: %s", e)

    def is_available(self) -> bool:
        """Return True if this provider's API key is set."""
        env_var = PROVIDERS.get(self._provider, {}).get("api_key_env", "")
        return bool(os.environ.get(env_var))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _generate_chat_completions(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
        system_prompt: Optional[str] = None,
        messages: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Standard Chat Completions call with retry logic."""
        start = time.perf_counter()
        if messages is not None:
            messages = list(messages)  # shallow copy
        else:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

        response = None
        for attempt in range(MAX_RETRIES):
            if self._closed:
                return _error_result("client closed (shutdown)", time.perf_counter() - start)
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=self._timeout,
                )
                break
            except Exception as e:
                if self._closed:
                    return _error_result("client closed (shutdown)", time.perf_counter() - start)
                if attempt == MAX_RETRIES - 1:
                    logger.error(
                        "%s API failed after %d retries: %s",
                        self._provider, MAX_RETRIES, e,
                    )
                    return _error_result(str(e), time.perf_counter() - start)
                backoff = INITIAL_BACKOFF * (2 ** attempt)
                logger.warning(
                    "%s API attempt %d failed (%s), retry in %.1fs",
                    self._provider, attempt + 1, e, backoff,
                )
                time.sleep(backoff)

        msg = response.choices[0].message.content
        usage = getattr(response, "usage", None)
        input_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        total_tok = (
            getattr(usage, "total_tokens", input_tok + output_tok)
            if usage else (input_tok + output_tok)
        )
        actual_model = getattr(response, "model", model) or model
        cost = self._estimate_cost(actual_model, input_tok, output_tok)
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "%s API [%s]: %d in + %d out = $%.6f in %d ms",
            self._provider, actual_model, input_tok, output_tok, cost, duration_ms,
        )
        return {
            "text": msg,
            "tokens": total_tok,
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "duration_ms": duration_ms,
            "cost_usd": cost,
            "model": actual_model,
            "success": True,
        }

    @staticmethod
    def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost from provider pricing table."""
        pricing = get_pricing(model)
        input_per_tok = pricing["input"] / 1_000_000
        output_per_tok = pricing["output"] / 1_000_000
        return input_tokens * input_per_tok + output_tokens * output_per_tok


# Alias for forward compatibility (callers can use either name).
LLMClient = OpenRouterClient


def _error_result(error: str, duration: float) -> Dict[str, Any]:
    return {
        "text": "",
        "error": error,
        "tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "duration_ms": int(duration * 1000),
        "cost_usd": 0.0,
        "model": "",
        "success": False,
    }
