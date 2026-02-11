"""
Model router: choose local model or Grok API by query complexity and confidence.
Gate B Phase 3 – try local first for simple/medium, escalate to Grok when needed.
"""

import logging
import time
from typing import Any, Dict, Optional

from src.models.cache import QueryCache
from src.models.grok_client import GrokClient
from src.models.local_model import LocalModel

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.7


class ModelRouter:
    """Routes prompts to local model or Grok API based on complexity and confidence."""

    def __init__(
        self,
        local_model: Optional[LocalModel] = None,
        grok_client: Optional[GrokClient] = None,
        cache: Optional[QueryCache] = None,
    ) -> None:
        """Initialize router with local model, Grok client, and optional query cache."""
        logger.info("Initializing model router...")
        self._local = local_model
        self._grok = grok_client
        self._cache = cache if cache is not None else QueryCache()
        if self._local is None:
            try:
                self._local = LocalModel()
            except (ValueError, ImportError, RuntimeError) as e:
                logger.warning("Local model not available: %s (router will use Grok only)", e)
        if self._grok is None:
            try:
                self._grok = GrokClient()
            except (ValueError, ImportError) as e:
                raise RuntimeError(
                    "Grok client required for router. Set GROK_API_KEY in .env or environment."
                ) from e
        self._stats: Dict[str, Any] = {
            "local_used": 0,
            "grok_used": 0,
            "total_cost": 0.0,
        }
        logger.info("Model router initialized")

    @property
    def local_available(self) -> bool:
        """True if the local model is loaded and can be used for generation."""
        return self._local is not None

    def generate(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        force_grok: bool = False,
    ) -> Dict[str, Any]:
        """
        Generate response using local model or Grok based on complexity and confidence.

        Returns dict with text, cost_usd, model, success; and confidence if local was used.
        """
        cached = self._cache.get(prompt)
        if cached is not None:
            # Return a copy so this call is reported as $0 cost (no API/local use)
            out = dict(cached)
            out["cost_usd"] = 0.0
            out["cached"] = True
            return out

        complexity = self._classify_complexity(prompt)
        logger.debug("Query complexity: %s", complexity)

        if force_grok:
            logger.info("Forcing Grok API (requested)")
            response = self._use_grok(prompt, max_tokens, temperature, self._needs_web_search(prompt))
            self._cache.set(prompt, response)
            return response

        needs_search = self._needs_web_search(prompt)
        if self._local and complexity in ("simple", "medium"):
            logger.debug("Trying local model first (web_search=%s)...", needs_search)
            local_response = self._local.generate_with_tools(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                enable_web_search=needs_search,
            )
            confidence = self._estimate_confidence(local_response, prompt)
            local_response["confidence"] = confidence

            if confidence >= CONFIDENCE_THRESHOLD:
                used_search = local_response.get("used_web_search", False)
                logger.info(
                    "Using local model (confidence: %.2f, web_search: %s, cost: $0.00)",
                    confidence,
                    used_search,
                )
                self._stats["local_used"] += 1
                self._cache.set(prompt, local_response)
                return local_response

            logger.info(
                "Local confidence too low (%.2f), escalating to Grok",
                confidence,
            )

        response = self._use_grok(prompt, max_tokens, temperature, needs_search)
        self._cache.set(prompt, response)
        return response

    def _needs_web_search(self, prompt: str) -> bool:
        """True if the query likely needs current/live data (use Grok web search)."""
        prompt_lower = prompt.lower()
        keywords = (
            "current", "today", "now", "latest", "recent", "weather", "news",
            "stock price", "score", "what happened", "what's happening", "headline",
        )
        return any(kw in prompt_lower for kw in keywords)

    def _use_grok(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        enable_web_search: bool = False,
    ) -> Dict[str, Any]:
        """Call Grok API and update stats. Optionally enable web search (Responses API)."""
        if enable_web_search:
            logger.info("Query needs current data, using Grok with web search")
        response = self._grok.generate(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_web_search=enable_web_search,
        )
        if response.get("success"):
            self._stats["grok_used"] += 1
            self._stats["total_cost"] += response.get("cost_usd", 0.0)
            logger.info(
                "Using Grok API (cost: $%.6f, total: $%.6f)",
                response.get("cost_usd", 0),
                self._stats["total_cost"],
            )
        return response

    def _classify_complexity(self, prompt: str) -> str:
        """
        Classify query complexity: simple, medium, or complex.
        Simple = short, factual; complex = long or analytical.
        """
        prompt_lower = prompt.lower().strip()
        words = prompt.split()
        n = len(words)

        if n < 10:
            return "simple"
        if n > 50:
            return "complex"

        complex_keywords = [
            "analyze", "compare", "evaluate", "explain why",
            "in detail", "step by step", "comprehensive", "detailed analysis",
        ]
        if any(kw in prompt_lower for kw in complex_keywords):
            return "complex"

        simple_keywords = [
            "what is", "who is", "when was", "where is",
            "how many", "calculate", "define",
        ]
        if any(kw in prompt_lower for kw in simple_keywords):
            return "simple"

        return "medium"

    def _estimate_confidence(self, response: Dict[str, Any], prompt: str) -> float:
        """
        Estimate confidence in local model response (0..1).
        Uses length, uncertainty phrases, and duration.
        """
        if not response.get("success"):
            return 0.0

        text = (response.get("text") or "").strip()
        # Truly empty = low confidence (single-char answers like "4" are valid)
        if not text:
            return 0.3
        word_count = len(text.split())
        # Short direct answers (e.g. "4", "42", "Paris") when prompt asks for brevity
        uncertainty = [
            "i'm not sure", "i don't know", "maybe", "possibly",
            "it's unclear", "uncertain", "perhaps",
        ]
        if len(text) < 20 and word_count <= 3 and not any(p in text.lower() for p in uncertainty):
            return 0.85  # trust short direct answers

        confidence = 0.7
        if word_count < 20:
            confidence += 0.1
        elif word_count > 100:
            confidence -= 0.1
        if any(phrase in text.lower() for phrase in uncertainty):
            confidence -= 0.2
        duration_ms = response.get("duration_ms", 0)
        if duration_ms > 10_000:
            confidence -= 0.1
        return max(0.0, min(1.0, confidence))

    def get_stats(self) -> Dict[str, Any]:
        """Return routing and cache statistics."""
        total = self._stats["local_used"] + self._stats["grok_used"]
        cache_stats = self._cache.get_stats()
        return {
            "local_used": self._stats["local_used"],
            "grok_used": self._stats["grok_used"],
            "total_queries": total,
            "local_percentage": (self._stats["local_used"] / total * 100) if total > 0 else 0.0,
            "total_cost_usd": self._stats["total_cost"],
            "avg_cost_per_query": (self._stats["total_cost"] / total) if total > 0 else 0.0,
            "cache_hits": cache_stats["hits"],
            "cache_misses": cache_stats["misses"],
            "cache_hit_rate": cache_stats["hit_rate_percent"],
            "cached_entries": cache_stats["cached_entries"],
        }
