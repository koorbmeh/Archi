"""
Model router: multi-provider LLM routing (OpenRouter default, direct providers optional).

All reasoning queries route through the configured provider's API.  SDXL image
generation runs locally via diffusers (no LLM involvement).
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

from src.models.cache import QueryCache
from src.models.openrouter_client import OpenRouterClient
from src.models.providers import MODEL_ALIASES, resolve_alias

logger = logging.getLogger(__name__)


class ModelRouter:
    """Routes prompts to LLM APIs (OpenRouter, xAI, Anthropic, DeepSeek, etc.)."""

    def __init__(
        self,
        api_client: Optional[OpenRouterClient] = None,
        cache: Optional[QueryCache] = None,
    ) -> None:
        """Initialize router with LLM client and optional query cache."""
        logger.info("Initializing model router...")
        self._api = api_client
        self._cache = cache if cache is not None else QueryCache()
        if self._api is None:
            try:
                self._api = OpenRouterClient(provider="xai")
            except (ValueError, ImportError):
                # Fall back to OpenRouter if xAI key not set
                try:
                    self._api = OpenRouterClient()
                except (ValueError, ImportError) as e:
                    raise RuntimeError(
                        "LLM client required for router. Set XAI_API_KEY or OPENROUTER_API_KEY in .env."
                    ) from e
        self._stats_lock = threading.Lock()  # Protects _stats dict
        self._stats: Dict[str, Any] = {
            "api_used": 0,
            "total_cost": 0.0,
        }
        # When True, force all generate() calls to a specific API model (user said
        # "switch to <model>").  Reset by switching to "auto".
        self._force_api_override: bool = False
        # Temporary switch state: auto-revert after N messages or when task completes.
        # _temp_remaining = number of generate() calls left before reverting.
        # _temp_previous = snapshot of (force_api, api_runtime_model, provider) to restore.
        self._temp_remaining: int = 0
        self._temp_previous: Optional[tuple] = None
        logger.info("Model router initialized")

    # ------------------------------------------------------------------
    # Runtime model switching (Discord "switch to X" command)
    # ------------------------------------------------------------------

    def switch_model(self, alias_or_full: str) -> Dict[str, Any]:
        """Switch the active model by alias, provider/model path, or full model ID.

        Returns dict with model, provider, display, message.

        Examples:
            switch_model("grok")         -> x-ai/grok-4.1-fast via OpenRouter
            switch_model("grok-direct")  -> grok-2 via xAI direct
            switch_model("xai/grok-2")   -> grok-2 via xAI direct
            switch_model("auto")         -> openrouter/auto (resets overrides)
        """
        lower = alias_or_full.strip().lower()

        # Special case: "auto" resets all overrides back to OpenRouter default
        if lower == "auto":
            self._force_api_override = False
            try:
                self._api = OpenRouterClient(provider="openrouter")
            except (ValueError, ImportError):
                if self._api:
                    self._api.reset_model()
            return {
                "model": "openrouter/auto",
                "provider": "openrouter",
                "display": "Auto (smart routing)",
                "message": "Switched to auto mode. Queries will be routed by complexity.",
            }

        # Resolve alias → (provider, model_id)
        try:
            provider, model_id = resolve_alias(alias_or_full)
        except ValueError as e:
            return {
                "model": None,
                "provider": None,
                "display": None,
                "message": str(e),
            }

        # Switch provider if needed (creates new client)
        current_provider = self._api.provider if self._api else None
        if provider != current_provider:
            try:
                self._api = OpenRouterClient(provider=provider)
            except ValueError as e:
                return {
                    "model": None,
                    "provider": None,
                    "display": None,
                    "message": str(e),
                }

        self._api.switch_model(model_id)
        self._force_api_override = True

        # Build a friendly display name
        alias_display = lower if lower in MODEL_ALIASES else model_id
        provider_label = f" on {provider}" if provider != "openrouter" else ""
        return {
            "model": model_id,
            "provider": provider,
            "display": alias_display,
            "message": f"Switched to **{alias_display}** (`{model_id}`{provider_label}). All queries will use this model.",
        }

    def get_active_model_info(self) -> Dict[str, str]:
        """Return info about the currently active model for status display."""
        provider = self._api.provider if self._api else "unknown"
        if self._force_api_override and self._api:
            active = self._api.get_active_model()
            provider_label = f" on {provider}" if provider != "openrouter" else ""
            info = {"model": active, "provider": provider,
                    "display": f"{active}{provider_label}", "mode": "forced_api"}
        else:
            default = self._api.get_active_model() if self._api else "unknown"
            info = {"model": default, "provider": provider, "display": default, "mode": "auto"}
        if self._temp_remaining > 0:
            info["temp_remaining"] = str(self._temp_remaining)
            info["mode"] += f" (temp: {self._temp_remaining} left)"
        return info

    def switch_model_temp(self, alias_or_full: str, count: int = 1) -> Dict[str, Any]:
        """Switch model temporarily for N generate() calls, then auto-revert.

        Use cases:
            "use claude for this task"          -> count=1 (one PlanExecutor run)
            "switch to grok for 5 messages"     -> count=5
            "use claude direct for this task"   -> count=1, direct provider

        After `count` calls to generate(), the model and provider revert to
        whatever was active before this call.
        """
        # Snapshot current state so we can restore it (including provider)
        prev_api_model = self._api._runtime_model if self._api else None
        prev_provider = self._api.provider if self._api else "openrouter"
        self._temp_previous = (
            self._force_api_override,
            prev_api_model,
            prev_provider,
        )
        self._temp_remaining = max(1, count)

        # Delegate to normal switch_model
        result = self.switch_model(alias_or_full)
        if result.get("model") is None:
            # Switch failed — don't set temp state
            self._temp_previous = None
            self._temp_remaining = 0
            return result

        result["message"] = (
            f"{result['message']}\n"
            f"_This is temporary — will revert after {self._temp_remaining} "
            f"{'message' if self._temp_remaining == 1 else 'messages'}._"
        )
        result["temp_remaining"] = self._temp_remaining
        return result

    def _tick_temp_switch(self) -> Optional[str]:
        """Decrement temp counter after a generate() call.

        Returns a revert message if the temp switch just expired, else None.
        Called internally at the end of generate().
        """
        if self._temp_remaining <= 0 or self._temp_previous is None:
            return None

        self._temp_remaining -= 1
        if self._temp_remaining > 0:
            return None

        # Revert to previous state (including provider)
        prev_api, prev_model, prev_provider = self._temp_previous
        self._force_api_override = prev_api
        # Restore provider if it changed
        if self._api and self._api.provider != prev_provider:
            try:
                self._api = OpenRouterClient(provider=prev_provider)
            except (ValueError, ImportError):
                pass  # Keep current client if restore fails
        if self._api and prev_model is not None:
            self._api._runtime_model = prev_model
        elif self._api:
            self._api._runtime_model = None
        self._temp_previous = None

        reverted_to = self.get_active_model_info()
        msg = f"Temporary model switch expired. Reverted to **{reverted_to['display']}**."
        logger.info("Temp model switch expired — reverted to %s", reverted_to["display"])
        return msg

    def complete_temp_task(self) -> Optional[str]:
        """Force-expire the temp switch (e.g. when a PlanExecutor task completes).

        Returns a revert message if there was a temp switch, else None.
        """
        if self._temp_remaining <= 0 or self._temp_previous is None:
            return None
        self._temp_remaining = 1  # Will expire on next tick
        return self._tick_temp_switch()

    def generate(
        self,
        prompt: str = "",
        max_tokens: int = 500,
        temperature: float = 0.7,
        force_api: bool = False,
        skip_web_search: bool = False,
        use_reasoning: bool = True,
        classify_hint: str = "",
        system_prompt: Optional[str] = None,
        messages: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Generate a response via the active provider's API.

        Args:
            force_api: Legacy, always True now. Kept for call-site compat.
            skip_web_search: Don't run web search (caller already has results).
            use_reasoning: Unused (kept for call-site compat).
            classify_hint: "plan_step" = classify by task description, not full prompt.
            system_prompt: Sent as separate system role message (enables caching).
            messages: Fully-formed messages array; takes precedence over prompt.

        Returns:
            dict with text, cost_usd, model, success.
            May include 'temp_revert_msg' if a temporary model switch just expired.
        """
        _prompt_for_log = prompt or (str(messages[-1].get("content", ""))[:200] if messages else "")
        logger.info(
            "ROUTER: prompt_len=%d words, multi_turn=%s",
            len(_prompt_for_log.split()), bool(messages),
        )

        # Skip cache for multi-turn messages (contextual, not cacheable)
        if not messages:
            cached = self._cache.get(prompt)
            if cached is not None:
                logger.info("ROUTER: cache HIT")
                out = dict(cached)
                out["cost_usd"] = 0.0
                out["cached"] = True
                return self._with_temp_tick(out)

        complexity = self._classify_complexity(prompt or _prompt_for_log, classify_hint=classify_hint)
        logger.info("ROUTER: complexity=%s", complexity)

        _search_prompt = prompt or _prompt_for_log
        needs_search = False if skip_web_search else self._needs_web_search(_search_prompt)

        _provider = self._api.provider if self._api else "unknown"
        if self._force_api_override:
            logger.info("ROUTER: forced API (user override, provider=%s, model=%s)",
                        _provider, self._api.get_active_model() if self._api else "?")
        else:
            logger.info("ROUTER: using %s API", _provider)

        response = self._use_api(prompt, max_tokens, temperature, needs_search,
                                  system_prompt=system_prompt, messages=messages)
        if prompt:
            self._cache.set(prompt, response)
        return self._with_temp_tick(response)

    def _with_temp_tick(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Tick down temporary model switch counter and attach revert message."""
        revert_msg = self._tick_temp_switch()
        if revert_msg:
            result["temp_revert_msg"] = revert_msg
        return result

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

    def _use_api(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        enable_web_search: bool = False,
        system_prompt: Optional[str] = None,
        messages: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Call the active provider's API and update stats."""
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

        response = self._api.generate(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_web_search=enable_web_search,
            system_prompt=system_prompt,
            messages=messages,
        )
        if response.get("success"):
            _provider = self._api.provider if self._api else "unknown"
            with self._stats_lock:
                self._stats["api_used"] += 1
                self._stats["total_cost"] += response.get("cost_usd", 0.0)
                _total_cost = self._stats["total_cost"]
            logger.info(
                "Using %s API (cost: $%.6f, total: $%.6f)",
                _provider, response.get("cost_usd", 0),
                _total_cost,
            )
            # Record in CostTracker for persistent budget enforcement
            try:
                from src.monitoring.cost_tracker import get_cost_tracker

                tracker = get_cost_tracker()
                tracker.record_usage(
                    provider=_provider,
                    model=response.get("model", "unknown"),
                    input_tokens=response.get("input_tokens", 0),
                    output_tokens=response.get("output_tokens", 0),
                    cost_usd=response.get("cost_usd", 0.0),
                )
            except Exception as e:
                logger.debug("CostTracker record failed: %s", e)
        return response

    def _classify_complexity(self, prompt: str, classify_hint: str = "") -> str:
        """
        Classify query complexity: simple, medium, or complex.
        Simple = short, factual; complex = long or analytical.

        When classify_hint="plan_step", the prompt is a PlanExecutor step prompt
        which is ALWAYS long (600-1200+ words) because it includes task history
        and available-actions boilerplate.  In that case, classify based on the
        *task description* and *last step result* rather than the full prompt
        length — the actual decision ("pick the next action") is typically medium
        complexity even though the context window is large.
        """
        prompt_lower = prompt.lower().strip()
        words = prompt.split()
        n = len(words)

        # PlanExecutor step prompts: extract just the task description for
        # length-based classification.
        if classify_hint == "plan_step":
            task_section = ""
            for marker in ("TASK:", "Task:", "task:"):
                if marker in prompt:
                    after = prompt.split(marker, 1)[1]
                    for end_marker in ("\n\n", "GOAL:", "Goal:", "STEPS SO FAR:",
                                       "Steps so far:", "AVAILABLE ACTIONS:"):
                        if end_marker in after:
                            after = after.split(end_marker, 1)[0]
                    task_section = after.strip()
                    break
            if task_section:
                words = task_section.split()
                n = len(words)
                logger.info(
                    "ROUTER: plan_step hint -> classifying by task description "
                    "(%d words) instead of full prompt (%d words)",
                    n, len(prompt.split()),
                )
            else:
                n = min(n, 45)
                logger.info(
                    "ROUTER: plan_step hint -> capping effective word count to %d", n,
                )

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

    def chat_with_image(
        self,
        text_prompt: str,
        image_path: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Analyze an image using the API vision model.

        Args:
            text_prompt: What to ask about the image
            image_path: Path to the image file on disk

        Returns:
            dict with text, cost_usd, model, success
        """
        if self._api:
            try:
                import base64
                with open(image_path, "rb") as img_f:
                    image_b64 = base64.b64encode(img_f.read()).decode("utf-8")
                # Detect media type from extension
                ext = image_path.rsplit(".", 1)[-1].lower() if "." in image_path else "png"
                media_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                             "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}
                media_type = media_map.get(ext, "image/png")
                logger.info("ROUTER: using API vision (model: %s)",
                            self._api.get_active_model())
                result = self._api.generate_with_vision(
                    prompt=text_prompt,
                    image_base64=image_b64,
                    image_media_type=media_type,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                with self._stats_lock:
                    self._stats["api_used"] += 1
                    self._stats["total_cost"] += result.get("cost_usd", 0)
                return result
            except Exception as ve:
                logger.warning("ROUTER: API vision failed: %s", ve)

        # Last resort: text-only with OpenRouter API
        logger.info("ROUTER: no vision available, using text-only API fallback")
        return self._use_api(
            f"{text_prompt}\n\n[Note: An image was provided but the vision model is not available. "
            f"Please respond based on the text prompt only.]",
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def generate_image(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """Generate an image locally using SDXL via diffusers.

        Args:
            prompt: text description of the image
            **kwargs: forwarded to ImageGenerator.generate()

        Returns:
            dict with success, image_path, prompt, duration_ms, model, error
        """
        try:
            from src.tools.image_gen import ImageGenerator

            gen = ImageGenerator()
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
        with self._stats_lock:
            total = self._stats["api_used"]
            total_cost = self._stats["total_cost"]
        cache_stats = self._cache.get_stats()
        return {
            "api_used": total,
            "total_queries": total,
            "total_cost_usd": total_cost,
            "avg_cost_per_query": (total_cost / total) if total > 0 else 0.0,
            "cache_hits": cache_stats["hits"],
            "cache_misses": cache_stats["misses"],
            "cache_hit_rate": cache_stats["hit_rate_percent"],
            "cached_entries": cache_stats["cached_entries"],
        }
