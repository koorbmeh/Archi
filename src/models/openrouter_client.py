"""
OpenRouter API client (OpenAI-compatible). Load OPENROUTER_API_KEY from environment.
Unified gateway to 300+ models (DeepSeek, Grok via BYOK, Mistral, auto-routing, etc.).
Replaces direct Grok API client with identical interface for drop-in migration.

Setup:
  1. Create account at https://openrouter.ai
  2. (Optional) Add your xAI key in Settings for BYOK (first 1M requests/month free)
  3. Generate API key at https://openrouter.ai/keys
  4. Set OPENROUTER_API_KEY in .env

Model selection:
  - Explicit per-request: model="deepseek/deepseek-chat-v3-0324"
  - Default from env: OPENROUTER_MODEL (e.g., x-ai/grok-4.1-fast)
  - Auto-routing: model="openrouter/auto" (picks best model for prompt)
"""

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# OpenRouter: OpenAI-compatible endpoint
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# Default to same model as previous Grok setup; override via OPENROUTER_MODEL
DEFAULT_MODEL = "x-ai/grok-4.1-fast"
# Fallback pricing (per 1M tokens) — used only when API doesn't return cost.
# Real cost depends on which model is selected; prefer API-reported cost.
DEFAULT_INPUT_COST_PER_1M = 0.20
DEFAULT_OUTPUT_COST_PER_1M = 1.00
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
TIMEOUT_SEC = 60.0

# Model-specific pricing (per 1M tokens) for cost estimation when API
# doesn't report cost.  Kept intentionally small — add models as needed.
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "x-ai/grok-4.1-fast": {"input": 0.20, "output": 1.00},
    "x-ai/grok-4-fast": {"input": 0.20, "output": 1.00},
    "x-ai/grok-4": {"input": 2.00, "output": 10.00},
    "deepseek/deepseek-chat-v3-0324": {"input": 0.14, "output": 0.28},
    "deepseek/deepseek-chat": {"input": 0.14, "output": 0.28},
    "mistralai/mistral-medium-3.1": {"input": 0.40, "output": 0.40},
    "openrouter/auto": {"input": 0.50, "output": 1.00},  # Conservative avg
}


class OpenRouterClient:
    """Client for OpenRouter API with retries, cost tracking, and timeouts.

    Drop-in replacement for GrokClient — identical public interface and
    return-value format so the rest of Archi (router, cost tracker, etc.)
    works without changes.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = TIMEOUT_SEC,
        default_model: Optional[str] = None,
    ) -> None:
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            # Check for legacy Grok key and warn
            legacy = os.environ.get("GROK_API_KEY")
            if legacy:
                logger.warning(
                    "GROK_API_KEY found but OPENROUTER_API_KEY not set. "
                    "Direct Grok access has been replaced by OpenRouter. "
                    "See .env.example for migration steps."
                )
            raise ValueError(
                "OPENROUTER_API_KEY not set in environment or passed to OpenRouterClient. "
                "Get a key at https://openrouter.ai/keys"
            )
        self._api_key = key
        self._base_url = (
            base_url
            or os.environ.get("OPENROUTER_API_BASE_URL")
            or DEFAULT_BASE_URL
        )
        self._timeout = timeout
        self._default_model = (
            default_model
            or os.environ.get("OPENROUTER_MODEL")
            or DEFAULT_MODEL
        )

        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=key,
                base_url=self._base_url,
                default_headers={
                    "HTTP-Referer": os.environ.get(
                        "OPENROUTER_APP_REFERER", "https://github.com/archi-agent"
                    ),
                    "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Archi"),
                },
            )
        except ImportError:
            raise ImportError(
                "openai package required for OpenRouterClient; pip install openai"
            )

        logger.info(
            "OpenRouter client initialized (base_url=%s, default_model=%s)",
            self._base_url,
            self._default_model,
        )

    # ------------------------------------------------------------------
    # Public API (matches GrokClient interface exactly)
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        model: Optional[str] = None,
        enable_web_search: bool = False,
    ) -> Dict[str, Any]:
        """
        Generate a completion.  Returns dict with text, tokens, cost_usd, success, error.

        Model can be overridden per-request (e.g., "deepseek/deepseek-chat-v3-0324")
        or defaults to OPENROUTER_MODEL env / x-ai/grok-4.1-fast.

        enable_web_search is accepted for interface compat but logged as a
        no-op — Archi uses its own free WebSearchTool (DuckDuckGo) instead.
        """
        if enable_web_search:
            logger.debug(
                "OpenRouter: enable_web_search ignored — use local WebSearchTool"
            )
        model = model or self._default_model
        return self._generate_chat_completions(prompt, model, max_tokens, temperature)

    def generate_with_vision(
        self,
        prompt: str,
        image_base64: str,
        image_media_type: str = "image/png",
        max_tokens: int = 200,
        temperature: float = 0.2,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate with image input (vision).  Uses OpenAI-compatible chat completions.
        Returns dict with text, cost_usd, success, error.

        Uses OPENROUTER_VISION_MODEL env if set, otherwise falls back to default model.
        """
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
            logger.error("OpenRouter vision API failed: %s", e)
            return _error_result(str(e), time.perf_counter() - start)

        msg = response.choices[0].message.content
        usage = getattr(response, "usage", None)
        input_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        actual_model = getattr(response, "model", model) or model
        cost = self._estimate_cost(actual_model, input_tok, output_tok)
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "OpenRouter vision [%s]: %d in + %d out = $%.6f in %d ms",
            actual_model, input_tok, output_tok, cost, duration_ms,
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

    def is_available(self) -> bool:
        """Return True if OPENROUTER_API_KEY is set (client can be constructed)."""
        return bool(os.environ.get("OPENROUTER_API_KEY"))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _generate_chat_completions(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> Dict[str, Any]:
        """Standard Chat Completions via OpenRouter."""
        start = time.perf_counter()
        response = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=self._timeout,
                )
                break
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    logger.error(
                        "OpenRouter API failed after %d retries: %s",
                        MAX_RETRIES, e,
                    )
                    return _error_result(str(e), time.perf_counter() - start)
                backoff = INITIAL_BACKOFF * (2 ** attempt)
                logger.warning(
                    "OpenRouter API attempt %d failed (%s), retry in %.1fs",
                    attempt + 1, e, backoff,
                )
                time.sleep(backoff)

        msg = response.choices[0].message.content
        usage = getattr(response, "usage", None)
        input_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        total_tok = (
            getattr(usage, "total_tokens", input_tok + output_tok)
            if usage
            else (input_tok + output_tok)
        )
        # OpenRouter may return the actual model used (important for auto-routing)
        actual_model = getattr(response, "model", model) or model
        cost = self._estimate_cost(actual_model, input_tok, output_tok)
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "OpenRouter API [%s]: %d in + %d out = $%.6f in %d ms",
            actual_model, input_tok, output_tok, cost, duration_ms,
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

    def _estimate_cost(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Estimate cost from model-specific pricing table.

        Falls back to default pricing if model isn't in the lookup table.
        """
        pricing = MODEL_PRICING.get(model)
        if pricing:
            input_per_tok = pricing["input"] / 1_000_000
            output_per_tok = pricing["output"] / 1_000_000
        else:
            input_per_tok = DEFAULT_INPUT_COST_PER_1M / 1_000_000
            output_per_tok = DEFAULT_OUTPUT_COST_PER_1M / 1_000_000
        return input_tokens * input_per_tok + output_tokens * output_per_tok


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
