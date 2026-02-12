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
# Lower threshold for short conversational queries (identity, greetings, etc)
CONFIDENCE_THRESHOLD_CONVERSATIONAL = 0.5


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
        prefer_local: bool = False,
        skip_web_search: bool = False,
    ) -> Dict[str, Any]:
        """
        Generate response using local model or Grok based on complexity and confidence.

        Args:
            prefer_local: If True, try local model first even for complex prompts (chat use).
            skip_web_search: If True, don't run web search (caller already has results).
        Returns dict with text, cost_usd, model, success; and confidence if local was used.
        """
        # DEBUG: Log routing inputs
        logger.info(
            "ROUTER: prompt_len=%d words, prefer_local=%s, force_grok=%s",
            len(prompt.split()),
            prefer_local,
            force_grok,
        )
        logger.debug("ROUTER: prompt preview: %s...", (prompt[:120] + "..." if len(prompt) > 120 else prompt))

        cached = self._cache.get(prompt)
        if cached is not None:
            logger.info("ROUTER: cache HIT - returning cached")
            out = dict(cached)
            out["cost_usd"] = 0.0
            out["cached"] = True
            return out

        complexity = self._classify_complexity(prompt)
        logger.info("ROUTER: complexity=%s", complexity)

        if force_grok:
            logger.info("Forcing Grok API (requested)")
            response = self._use_grok(prompt, max_tokens, temperature, self._needs_web_search(prompt))
            self._cache.set(prompt, response)
            return response

        needs_search = False if skip_web_search else self._needs_web_search(prompt)
        # prefer_local=True (chat): always try local first, including for search (use free web search)
        # prefer_local=False (agent loop): use complexity + needs_search (may escalate to Grok)
        try_local = (
            self._local
            and (prefer_local or (complexity in ("simple", "medium") and not needs_search))
        )
        logger.info(
            "ROUTER: needs_search=%s, try_local=%s (local_ok=%s)",
            needs_search,
            try_local,
            self._local is not None,
        )
        if try_local:
            logger.info("ROUTER: trying LOCAL model...")
            try:
                local_response = self._local.generate_with_tools(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    enable_web_search=needs_search,
                )
            except Exception as e:
                logger.warning("ROUTER: local model failed, falling back to Grok: %s", e)
                local_response = {"success": False, "text": "", "confidence": 0.0}
            confidence = self._estimate_confidence(local_response, prompt)
            local_response["confidence"] = confidence

            # prefer_local: always use local response (no escalation to Grok)
            if prefer_local and local_response.get("success") and local_response.get("text", "").strip():
                logger.info(
                    "ROUTER: LOCAL SUCCESS (prefer_local, cost=$0.00)",
                )
                self._stats["local_used"] += 1
                self._cache.set(prompt, local_response)
                return local_response

            # Otherwise use confidence threshold
            word_count = len(prompt.split())
            threshold = (
                CONFIDENCE_THRESHOLD_CONVERSATIONAL
                if word_count <= 15 and not needs_search
                else CONFIDENCE_THRESHOLD
            )
            if confidence >= threshold:
                used_search = local_response.get("used_web_search", False)
                logger.info(
                    "ROUTER: LOCAL SUCCESS (confidence=%.2f, threshold=%.2f, cost=$0.00)",
                    confidence,
                    threshold,
                )
                self._stats["local_used"] += 1
                self._cache.set(prompt, local_response)
                return local_response

            logger.info(
                "ROUTER: local confidence %.2f < threshold %.2f -> escalating to Grok",
                confidence,
                threshold,
            )

        logger.info("ROUTER: using Grok API")
        response = self._use_grok(prompt, max_tokens, temperature, needs_search)
        self._cache.set(prompt, response)
        return response

    def _needs_web_search(self, prompt: str) -> bool:
        """True if the query likely needs current/live data (use web search)."""
        prompt_lower = prompt.lower()
        keywords = (
            "current", "today", "now", "latest", "recent", "weather", "news",
            "stock price", "spot price", "price of", "market price", "commodity",
            "score", "what happened", "what's happening", "headline",
            "bitcoin", "crypto", "forex", "exchange rate",
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
        # Check budget before paid API call (budget_hard_stop from rules.yaml)
        try:
            from src.monitoring.cost_tracker import get_cost_tracker

            tracker = get_cost_tracker()
            budget_check = tracker.check_budget(estimated_cost=0.01)
            if not budget_check.get("allowed", True):
                reason = budget_check.get("reason", "budget_exceeded")
                daily_spent = budget_check.get("daily_spent", 0)
                daily_limit = budget_check.get("daily_limit", 0)
                msg = (
                    f"Budget hard stop: ${daily_spent:.2f} >= ${daily_limit:.2f} "
                    f"({reason}). Paid API calls blocked."
                )
                logger.warning(msg)
                return {
                    "text": "",
                    "error": msg,
                    "tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "duration_ms": 0,
                    "cost_usd": 0.0,
                    "model": "blocked",
                    "success": False,
                }
        except Exception as e:
            logger.warning("Budget check failed, allowing Grok call: %s", e)

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
            # Record in CostTracker for persistent budget enforcement
            try:
                from src.monitoring.cost_tracker import get_cost_tracker

                tracker = get_cost_tracker()
                tracker.record_usage(
                    provider="grok",
                    model=response.get("model", "grok-beta"),
                    input_tokens=response.get("input_tokens", 0),
                    output_tokens=response.get("output_tokens", 0),
                    cost_usd=response.get("cost_usd", 0.0),
                )
            except Exception as e:
                logger.debug("CostTracker record failed: %s", e)
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
            "what is", "what's", "who is", "who are", "when was", "where is",
            "how many", "calculate", "define", "hello", "hi ", "hey ",
            "your name", "who are you", "what can you do",
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
