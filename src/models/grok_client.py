"""
Grok API client (x.ai, OpenAI-compatible). Load GROK_API_KEY from environment.
Gate B Phase 3: frontier model for escalation when local confidence is low.
Web search: enable_web_search=True uses Responses API + web_search (requires Accept/User-Agent headers).
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# x.ai Grok: OpenAI-compatible endpoint (see https://docs.x.ai/docs/models)
DEFAULT_BASE_URL = "https://api.x.ai/v1"
# x.ai default; set GROK_MODEL in .env to override
DEFAULT_MODEL = "grok-4-1-fast-reasoning"
# Pricing (check https://docs.x.ai for current rates)
INPUT_COST_PER_1M = 0.20
OUTPUT_COST_PER_1M = 1.00
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
TIMEOUT_SEC = 60.0


class GrokClient:
    """Client for x.ai Grok API with retries, cost tracking, and timeouts."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = TIMEOUT_SEC,
    ) -> None:
        key = api_key or os.environ.get("GROK_API_KEY")
        if not key:
            raise ValueError("GROK_API_KEY not set in environment or passed to GrokClient")
        self._api_key = key
        self._base_url = base_url or os.environ.get("GROK_API_BASE_URL") or DEFAULT_BASE_URL
        self._timeout = timeout
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=key, base_url=self._base_url)
        except ImportError:
            raise ImportError("openai package required for GrokClient; pip install openai")
        self._input_cost_per_token = INPUT_COST_PER_1M / 1_000_000
        self._output_cost_per_token = OUTPUT_COST_PER_1M / 1_000_000
        logger.info("Grok client initialized (base_url=%s)", self._base_url)

    def generate(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        model: Optional[str] = None,
        enable_web_search: bool = False,
    ) -> Dict[str, Any]:
        """
        Generate a completion. Returns dict with text, tokens, cost_usd, success, error.
        Model can be overridden via GROK_MODEL env (e.g. grok-4-fast-reasoning).
        When enable_web_search=True, uses Responses API + web_search (POST /v1/responses with proper headers).
        """
        model = model or os.environ.get("GROK_MODEL") or DEFAULT_MODEL
        if enable_web_search:
            return self._generate_responses_api_web_search(prompt, model, max_tokens, temperature)
        return self._generate_chat_completions(prompt, model, max_tokens, temperature)

    def _generate_responses_api_web_search(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> Dict[str, Any]:
        """Responses API with web_search tool. Uses Accept/User-Agent to avoid 403/1010 WAF."""
        start = time.perf_counter()
        url = f"{self._base_url.rstrip('/')}/responses"
        payload = {
            "model": model,
            "input": [{"role": "user", "content": prompt}],
            "tools": [{"type": "web_search"}],
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": "Archi/1.0 (python-urllib; Grok-Responses-API)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=int(self._timeout)) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else ""
            try:
                j = json.loads(err_body)
                msg = j.get("error", {}).get("message", err_body[:200])
            except Exception:
                msg = err_body[:200]
            return _error_result(f"HTTP {e.code}: {msg}", time.perf_counter() - start)
        except Exception as e:
            return _error_result(str(e), time.perf_counter() - start)

        output = body.get("output", [])
        text_parts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content", [])
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "output_text":
                        text_parts.append(c.get("text", ""))
            elif isinstance(content, str):
                text_parts.append(content)
        text = " ".join(text_parts).strip()
        usage = body.get("usage", {})
        input_tok = usage.get("input_tokens", 0)
        output_tok = usage.get("output_tokens", 0)
        total_tok = usage.get("total_tokens", input_tok + output_tok)
        cost = input_tok * self._input_cost_per_token + output_tok * self._output_cost_per_token
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "Grok API (web_search): %d in + %d out = $%.6f in %d ms",
            input_tok, output_tok, cost, duration_ms,
        )
        return {
            "text": text,
            "tokens": total_tok,
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "duration_ms": duration_ms,
            "cost_usd": cost,
            "model": model,
            "success": True,
        }

    def _generate_chat_completions(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> Dict[str, Any]:
        """Standard Chat Completions (no tools)."""
        start = time.perf_counter()
        last_error: Optional[Exception] = None
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
                last_error = e
                if attempt == MAX_RETRIES - 1:
                    logger.error("Grok API failed after %d retries: %s", MAX_RETRIES, e)
                    return _error_result(str(e), time.perf_counter() - start)
                backoff = INITIAL_BACKOFF * (2 ** attempt)
                logger.warning("Grok API attempt %d failed (%s), retry in %.1fs", attempt + 1, e, backoff)
                time.sleep(backoff)

        msg = response.choices[0].message.content
        usage = getattr(response, "usage", None)
        input_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        total_tok = getattr(usage, "total_tokens", input_tok + output_tok) if usage else (input_tok + output_tok)
        cost = input_tok * self._input_cost_per_token + output_tok * self._output_cost_per_token
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "Grok API: %d in + %d out = $%.6f in %d ms",
            input_tok, output_tok, cost, duration_ms,
        )
        return {
            "text": msg,
            "tokens": total_tok,
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "duration_ms": duration_ms,
            "cost_usd": cost,
            "model": model,
            "success": True,
        }

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
        Generate with image input (vision). Uses OpenAI-compatible chat completions.
        Returns dict with text, cost_usd, success, error.
        """
        model = model or os.environ.get("GROK_MODEL") or DEFAULT_MODEL
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
            logger.error("Grok vision API failed: %s", e)
            return _error_result(str(e), time.perf_counter() - start)

        msg = response.choices[0].message.content
        usage = getattr(response, "usage", None)
        input_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        cost = input_tok * self._input_cost_per_token + output_tok * self._output_cost_per_token
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "Grok vision: %d in + %d out = $%.6f in %d ms",
            input_tok, output_tok, cost, duration_ms,
        )
        return {
            "text": msg,
            "tokens": input_tok + output_tok,
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "duration_ms": duration_ms,
            "cost_usd": cost,
            "model": model,
            "success": True,
        }

    def is_available(self) -> bool:
        """Return True if GROK_API_KEY is set (client can be constructed)."""
        return bool(os.environ.get("GROK_API_KEY"))


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
