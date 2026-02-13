"""
Model router: choose local model or OpenRouter API by query complexity and confidence.
Try local first for simple/medium, escalate to OpenRouter when needed.
"""

import logging
import time
from typing import Any, Dict, Optional

from src.models.cache import QueryCache
from src.models.openrouter_client import OpenRouterClient
from src.models.local_model import LocalModel

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.7
# Lower threshold for short conversational queries (identity, greetings, etc)
CONFIDENCE_THRESHOLD_CONVERSATIONAL = 0.5


class ModelRouter:
    """Routes prompts to local model or OpenRouter API based on complexity and confidence."""

    def __init__(
        self,
        local_model: Optional[LocalModel] = None,
        grok_client: Optional[OpenRouterClient] = None,
        cache: Optional[QueryCache] = None,
    ) -> None:
        """Initialize router with local model, OpenRouter client, and optional query cache."""
        logger.info("Initializing model router...")
        self._local = local_model
        # _grok attribute name kept for minimal diff; this is now OpenRouter
        self._grok = grok_client
        self._cache = cache if cache is not None else QueryCache()
        if self._local is None:
            try:
                self._local = LocalModel()
            except (ValueError, ImportError, RuntimeError) as e:
                logger.warning("Local model not available: %s (router will use API only)", e)
        if self._grok is None:
            try:
                self._grok = OpenRouterClient()
            except (ValueError, ImportError) as e:
                raise RuntimeError(
                    "OpenRouter client required for router. Set OPENROUTER_API_KEY in .env or environment."
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
        use_reasoning: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate response using local model or Grok based on complexity and confidence.

        Args:
            prefer_local: If True, try local model first even for complex prompts (chat use).
            skip_web_search: If True, don't run web search (caller already has results).
            use_reasoning: If False, force vision model even if reasoning model is available.
                Useful for simple tasks (greetings) that don't need chain-of-thought.
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
            logger.info("Forcing OpenRouter API (requested)")
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
                    use_reasoning=use_reasoning,
                )
            except Exception as e:
                logger.warning("ROUTER: local model failed, falling back to API: %s", e)
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
            # Use USER message length, not full prompt - "Hello" in 800-word history = conversational
            user_query = self._extract_user_query(prompt)
            user_word_count = len(user_query.split())
            threshold = (
                CONFIDENCE_THRESHOLD_CONVERSATIONAL
                if user_word_count <= 15 and not needs_search
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
                "ROUTER: local confidence %.2f < threshold %.2f -> escalating to OpenRouter",
                confidence,
                threshold,
            )

            # Budget-aware: when budget warning threshold exceeded, don't escalate simple queries
            if complexity == "simple" and not needs_search:
                try:
                    from src.monitoring.cost_tracker import get_cost_tracker
                    from src.utils.config import get_monitoring
                    _budget_warn_frac = get_monitoring()["budget_warning_pct"] / 100.0

                    tracker = get_cost_tracker()
                    budget_check = tracker.check_budget(estimated_cost=0)
                    daily_spent = budget_check.get("daily_spent", 0)
                    daily_limit = budget_check.get("daily_limit", 1.0)
                    if daily_limit > 0 and (daily_spent / daily_limit) >= _budget_warn_frac:
                        logger.info(
                            "ROUTER: budget >%d%% used (%.0f%%), keeping local for simple query",
                            int(_budget_warn_frac * 100),
                            100 * daily_spent / daily_limit,
                        )
                        self._stats["local_used"] += 1
                        self._cache.set(prompt, local_response)
                        return local_response
                except Exception as e:
                    logger.debug("Budget check failed during escalation: %s", e)

        logger.info("ROUTER: using OpenRouter API")
        response = self._use_grok(prompt, max_tokens, temperature, needs_search)
        self._cache.set(prompt, response)
        return response

    @staticmethod
    def _extract_user_query(prompt: str) -> str:
        """Extract just the user's message from a full prompt (system + history + user)."""
        lines = prompt.split("\n")
        for i in range(len(lines) - 1, -1, -1):
            stripped = lines[i].strip()
            if stripped.startswith("User:"):
                user_text = stripped.split("User:", 1)[1].strip()
                # Grab continuation lines until instruction or blank
                for j in range(i + 1, len(lines)):
                    line = lines[j].strip()
                    if not line or line.startswith("Respond ") or line.startswith("Archi:") or line.startswith("CRITICAL"):
                        break
                    user_text += " " + line
                return user_text.strip()
        # Fallback: last non-empty line
        for line in reversed(lines):
            if line.strip():
                return line.strip()
        return prompt[:200]

    def _needs_web_search(self, prompt: str) -> bool:
        """True if the user's actual question likely needs current/live data.

        Only checks the user's message, NOT the system prompt or history,
        to avoid false positives from system prompt keywords like 'current'.
        """
        user_query = self._extract_user_query(prompt).lower()
        keywords = (
            "current", "today", "now", "latest", "recent", "weather", "news",
            "stock price", "spot price", "price of", "market price", "commodity",
            "score", "what happened", "what's happening", "headline",
            "bitcoin", "crypto", "forex", "exchange rate",
        )
        return any(kw in user_query for kw in keywords)

    def _use_grok(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        enable_web_search: bool = False,
    ) -> Dict[str, Any]:
        """Call OpenRouter API and update stats."""
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
            logger.warning("Budget check failed, allowing API call: %s", e)

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
                "Using OpenRouter API (cost: $%.6f, total: $%.6f)",
                response.get("cost_usd", 0),
                self._stats["total_cost"],
            )
            # Record in CostTracker for persistent budget enforcement
            try:
                from src.monitoring.cost_tracker import get_cost_tracker

                tracker = get_cost_tracker()
                tracker.record_usage(
                    provider="openrouter",
                    model=response.get("model", "unknown"),
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

    def chat_with_image(
        self,
        text_prompt: str,
        image_path: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Analyze an image using the local vision model (Qwen3-VL).
        Falls back to OpenRouter API if local vision is unavailable.

        Args:
            text_prompt: What to ask about the image
            image_path: Path to the image file on disk

        Returns:
            dict with text, cost_usd, model, success
        """
        # Try local vision first
        if self._local and self._local.has_vision:
            logger.info("ROUTER: using local vision model for image analysis")
            result = self._local.chat_with_image(
                text_prompt, image_path, max_tokens=max_tokens, temperature=temperature
            )
            if result.get("success") and result.get("text", "").strip():
                self._stats["local_used"] += 1
                return result
            logger.warning("ROUTER: local vision failed: %s", result.get("error"))

        # Fallback: text-only with OpenRouter API
        logger.info("ROUTER: no local vision available, using text-only API fallback")
        return self._use_grok(
            f"{text_prompt}\n\n[Note: An image was provided but the vision model is not available. "
            f"Please respond based on the text prompt only.]",
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def generate_image(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """Generate an image locally using SDXL via diffusers.

        Coordinates with LocalModel to swap VRAM: unloads the LLM,
        runs SDXL, then reloads the LLM.

        Args:
            prompt: text description of the image
            **kwargs: forwarded to ImageGenerator.generate()

        Returns:
            dict with success, image_path, prompt, duration_ms, model, error
        """
        if not self._local:
            return {
                "success": False,
                "error": "Local model not available for image generation",
                "model": "none",
            }

        try:
            from src.tools.image_gen import ImageGenerator

            gen = ImageGenerator(local_model=self._local)
            result = gen.generate(prompt, **kwargs)
            result["model"] = "sdxl-local"
            result["cost_usd"] = 0.0  # local generation, no API cost
            return result
        except ImportError:
            return {
                "success": False,
                "error": "diffusers not installed. Run: pip install diffusers transformers accelerate safetensors",
                "model": "sdxl-local",
                "cost_usd": 0.0,
            }
        except Exception as e:
            logger.exception("Image generation via router failed: %s", e)
            return {
                "success": False,
                "error": str(e),
                "model": "sdxl-local",
                "cost_usd": 0.0,
            }

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
