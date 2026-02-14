"""
Local LLM wrapper for Archi using Forge (model-agnostic inference).

Single-GPU swap architecture:
  - Only ONE model loaded on GPU at a time (avoids VRAM pressure / CPU spill)
  - Reasoning model (Qwen3-8B preferred, DeepSeek-R1 fallback): primary,
    loaded at startup, handles all text tasks (chat, intent, planning, JSON)
  - Vision model (Qwen3-VL-8B): loaded on demand for image analysis,
    then swapped back to reasoning after use
  - If no reasoning model available, vision model handles everything (no swapping)

Qwen3 thinking mode:
  - Qwen3 produces <think> blocks by default; /no_think appended to prompts
    to skip thinking for faster direct responses (~50% speed improvement)
  - _strip_thinking() handles any residual empty <think></think> tags

Supports free web search via WebSearchTool when enable_web_search=True.
"""

import gc
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Optional
from src.utils.paths import base_path as _base_path

logger = logging.getLogger(__name__)

# Keywords that suggest the query needs current/live data (use web search)
_SEARCH_KEYWORDS = (
    "current", "today", "now", "latest", "recent", "weather", "news",
    "stock price", "spot price", "price of", "market price", "commodity",
    "score", "what happened", "what's happening", "headline",
    "bitcoin", "crypto", "forex", "exchange rate",
)

# Default model filenames
_DEFAULT_VL_MODEL = "Qwen3VL-8B-Instruct-Q4_K_M.gguf"
_DEFAULT_REASONING_MODELS = [
    # Qwen3 is preferred: excellent JSON output, instruction following, no
    # wasteful <think> reasoning overhead, and tool-calling trained.
    "Qwen3-8B-Q4_K_M.gguf",
    "Qwen3-8B.Q4_K_M.gguf",
    # DeepSeek-R1 works but is slower (3x token overhead for <think> blocks)
    # and gets confused with long context. Use only as fallback.
    "DeepSeek-R1-Distill-Llama-8B-Q4_K_M.gguf",
    "DeepSeek-R1-Distill-Llama-8B.Q4_K_M.gguf",
    "Phi-4-mini-reasoning-Q4_K_M.gguf",
]


def _find_vision_model_path() -> Optional[str]:
    """Find vision model path. Checks env vars, then models/ directory."""
    # Explicit env vars
    for var in ("LOCAL_VISION_MODEL_PATH", "LOCAL_MODEL_PATH"):
        path = os.environ.get(var)
        if path and os.path.isfile(path):
            return path

    base = _base_path()
    models_dir = os.path.join(base, "models")

    # Known filename
    vl_path = os.path.join(models_dir, _DEFAULT_VL_MODEL)
    if os.path.isfile(vl_path):
        return vl_path

    # Scan for any Qwen3VL
    if os.path.isdir(models_dir):
        for name in os.listdir(models_dir):
            if "qwen3" in name.lower() and "vl" in name.lower() and name.endswith(".gguf"):
                return os.path.join(models_dir, name)

    return None


def _find_reasoning_model_path() -> Optional[str]:
    """Find reasoning model path. Checks env var, then models/ directory."""
    explicit = os.environ.get("REASONING_MODEL_PATH")
    if explicit and os.path.isfile(explicit):
        return explicit

    base = _base_path()
    models_dir = os.path.join(base, "models")

    for fname in _DEFAULT_REASONING_MODELS:
        path = os.path.join(models_dir, fname)
        if os.path.isfile(path):
            return path

    if os.path.isdir(models_dir):
        for name in os.listdir(models_dir):
            low = name.lower()
            if name.endswith(".gguf") and (
                ("deepseek" in low and "r1" in low)
                or ("phi-4" in low and "reason" in low)
            ):
                return os.path.join(models_dir, name)

    return None


def _ensure_forge_on_path() -> None:
    """Ensure project root (with backends/, utils/) is on sys.path."""
    import sys
    base = _base_path()
    if base not in sys.path:
        sys.path.insert(0, base)


class LocalModel:
    """
    Single-GPU model manager with hot-swap between reasoning and vision models.

    Only one model is loaded at a time.  The reasoning model is the primary
    workhorse for all text tasks.  The vision model is loaded on-demand for
    image analysis and then the reasoning model is restored.

    If only one model is available, no swapping occurs.
    """

    # DeepSeek-R1 wraps output in <think>...</think> — these thinking tokens
    # count against max_tokens so we need extra headroom.  Non-reasoning models
    # like Qwen3 don't need this overhead.
    _REASONING_TOKEN_MULTIPLIER = 3
    _REASONING_MIN_TOKENS = 512  # Floor for small max_tokens requests

    def __init__(self, model_path: Optional[str] = None) -> None:
        _ensure_forge_on_path()

        # ── Discover model paths ──────────────────────────────────
        self._vision_path: Optional[str] = _find_vision_model_path()
        self._reasoning_path: Optional[str] = _find_reasoning_model_path()

        # Allow explicit override for backward compat
        if model_path and os.path.isfile(model_path):
            self._vision_path = model_path

        # Need at least one model
        if not self._vision_path and not self._reasoning_path:
            raise ValueError(
                "No model found.  Place a GGUF model in models/ or set "
                "REASONING_MODEL_PATH / LOCAL_VISION_MODEL_PATH in .env.  See README.md."
            )

        # Don't double-load if both point to the same file
        if (self._vision_path and self._reasoning_path
                and os.path.abspath(self._vision_path) == os.path.abspath(self._reasoning_path)):
            self._reasoning_path = None  # vision model handles everything

        # Check ARCHI_DUAL_MODEL toggle
        if os.environ.get("ARCHI_DUAL_MODEL", "auto").lower() == "off":
            self._reasoning_path = None

        # ── Context sizes ─────────────────────────────────────────
        self._vision_ctx = int(os.getenv("ARCHI_CONTEXT_SIZE", "8192"))
        self._reasoning_ctx = int(os.getenv("ARCHI_REASONING_CONTEXT_SIZE", "4096"))

        # ── Runtime state ─────────────────────────────────────────
        self._backend: Optional[Any] = None
        self._active_model: Optional[str] = None   # "vision" | "reasoning"
        self._gpu_lock = threading.Lock()
        self._search_tool: Optional[Any] = None

        # ── Load primary model ────────────────────────────────────
        # Prefer reasoning model as primary (handles all text tasks);
        # fall back to vision model if no reasoning model available.
        if self._reasoning_path:
            self._swap_to("reasoning")
            logger.info(
                "Primary model: reasoning (%s).  Vision available on demand.",
                os.path.basename(self._reasoning_path),
            )
        elif self._vision_path:
            self._swap_to("vision")
            logger.info(
                "Primary model: vision (%s).  No dedicated reasoning model.",
                os.path.basename(self._vision_path),
            )
        else:
            raise ValueError("No loadable model found")

        logger.info(
            "Swap architecture: reasoning=%s, vision=%s",
            os.path.basename(self._reasoning_path) if self._reasoning_path else "none",
            os.path.basename(self._vision_path) if self._vision_path else "none",
        )

    # ── Model swapping ────────────────────────────────────────────

    def _swap_to(self, model_type: str) -> None:
        """Unload current model and load the requested one.

        Args:
            model_type: "vision" or "reasoning"

        Must be called while holding self._gpu_lock (or during __init__).
        """
        if self._active_model == model_type:
            return  # already loaded

        path = self._reasoning_path if model_type == "reasoning" else self._vision_path
        ctx = self._reasoning_ctx if model_type == "reasoning" else self._vision_ctx

        if not path or not os.path.isfile(path):
            logger.warning("Cannot swap to %s — model path not available", model_type)
            return

        from backends import select_backend
        from src.utils.model_detector import detect_model

        # Unload current model
        if self._backend is not None:
            prev = self._active_model
            logger.info("Swapping model: %s → %s", prev, model_type)
            try:
                self._backend.unload()
            except Exception as e:
                logger.warning("Unload failed (continuing): %s", e)
            self._backend = None
            self._active_model = None
            gc.collect()
        else:
            logger.info("Loading model: %s", model_type)

        # Load new model
        t0 = time.monotonic()
        model_info = detect_model(path)
        backend = select_backend(model_info)
        # skip_mmproj: don't search for vision projector when loading reasoning model
        # (both models share models/ dir, and the mmproj belongs to the vision model)
        backend.load(
            path,
            n_gpu_layers=-1,
            n_ctx=ctx,
            verbose=False,
            skip_mmproj=(model_type == "reasoning"),
        )
        dt = time.monotonic() - t0

        self._backend = backend
        self._active_model = model_type
        logger.info(
            "Model loaded: %s (%s, ctx=%d) in %.1fs",
            os.path.basename(path), model_type, ctx, dt,
        )

    def _ensure_model(self, model_type: str) -> None:
        """Swap to the requested model if not already loaded.

        Must be called while holding self._gpu_lock.
        """
        if self._active_model != model_type:
            self._swap_to(model_type)

    # ── Properties ────────────────────────────────────────────────

    @property
    def has_reasoning_model(self) -> bool:
        """True if a dedicated reasoning model is available (may not be loaded right now)."""
        return self._reasoning_path is not None

    @property
    def has_vision(self) -> bool:
        """True if a vision model is available for chat_with_image."""
        return self._vision_path is not None

    # ── Web search ────────────────────────────────────────────────

    def _get_search_tool(self) -> Any:
        if self._search_tool is None:
            try:
                from src.tools.web_search_tool import WebSearchTool
                self._search_tool = WebSearchTool()
            except ImportError:
                self._search_tool = False  # type: ignore[assignment]
        return self._search_tool if self._search_tool else None

    @staticmethod
    def _extract_user_query(prompt: str) -> str:
        """Extract just the user's message from a full prompt (system + history + user).

        Looks for the last 'User:' line and returns everything after it,
        stripping instruction suffixes like 'Respond naturally as Archi...'.
        Falls back to the last line if no 'User:' marker is found.
        """
        lines = prompt.split("\n")
        for i in range(len(lines) - 1, -1, -1):
            stripped = lines[i].strip()
            if stripped.startswith("User:"):
                user_text = stripped.split("User:", 1)[1].strip()
                for j in range(i + 1, len(lines)):
                    line = lines[j].strip()
                    if not line or line.startswith("Respond ") or line.startswith("Archi:"):
                        break
                    user_text += " " + line
                return user_text.strip()

        for line in reversed(lines):
            if line.strip():
                return line.strip()
        return prompt[:200]

    def _needs_search(self, prompt: str) -> bool:
        """True if the user's actual question likely needs current/live data."""
        user_query = self._extract_user_query(prompt).lower()
        return any(kw in user_query for kw in _SEARCH_KEYWORDS)

    # ── Think-tag stripping ───────────────────────────────────────

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Remove <think>...</think> reasoning blocks from model output.

        DeepSeek-R1 wraps chain-of-thought in <think> tags.  Qwen3 may emit
        empty <think></think> tags even with /no_think enabled (this is
        expected per llama.cpp — the client handles it).

        If the entire response was thinking (model ran out of tokens before
        producing an answer outside the block), try to extract the last line
        from inside the block—some models put the final answer there.
        """
        if not text or "<think>" not in text:
            return text or ""
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        if "<think>" in cleaned:
            cleaned = cleaned.split("<think>")[0].strip()
        cleaned = cleaned.replace("</think>", "").strip()
        if cleaned:
            return cleaned
        # Entire response was thinking; try last line of block as fallback
        match = re.search(r"<think>(.*?)(?:</think>|$)", text, flags=re.DOTALL)
        if match:
            inner = match.group(1).strip()
            if inner:
                last_line = inner.split("\n")[-1].strip()
                if last_line and len(last_line) < 200:
                    return last_line
        return ""

    # ── Result conversion ─────────────────────────────────────────

    def _forge_result_to_dict(
        self,
        result: Any,
        model_name: str = "local",
        used_web_search: bool = False,
        search_results_count: int = 0,
    ) -> Dict[str, Any]:
        """Convert Forge GenerationResult to Archi's expected dict shape.

        Automatically strips <think> tags from reasoning model output.
        """
        raw_text = (result.text or "").strip()
        clean_text = self._strip_thinking(raw_text)
        tokens = result.tokens_generated or len(raw_text.split())
        return {
            "text": clean_text,
            "_raw_text": raw_text,
            "tokens": tokens,
            "duration_ms": int(result.time_seconds * 1000),
            "cost_usd": 0.0,
            "model": model_name,
            "success": True,
            "used_web_search": used_web_search,
            "search_results_count": search_results_count,
        }

    # ── Generation ────────────────────────────────────────────────

    def generate_with_tools(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        enable_web_search: bool = False,
        stop: Optional[list] = None,
        use_reasoning: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate response, optionally using free web search first.

        Args:
            use_reasoning: If True and reasoning model available, use it with
                token boost for <think> overhead.  If False, use reasoning model
                with standard settings (faster for simple tasks).
        """
        if enable_web_search and self._needs_search(prompt):
            tool = self._get_search_tool()
            if tool is not None:
                search_query = self._extract_user_query(prompt)
                # Safeguard: if query is very long, we likely got the full prompt by mistake
                if len(search_query) > 200:
                    search_query = search_query[:200].rsplit(" ", 1)[0] or search_query[:100]
                    logger.warning("Search query truncated (was >200 chars); may have been full prompt")
                logger.info("Local model using free web search (query: %s)", search_query[:80])
                results = tool.search(search_query, max_results=3)
                if results:
                    search_context = tool.format_results(results)
                    enhanced_prompt = (
                        "You are a helpful AI assistant with access to current web search results.\n\n"
                        "Search Results:\n"
                        f"{search_context}\n\n"
                        f"User Question: {prompt}\n\n"
                        "Instructions: Answer the question using ONLY the information from the search results above. "
                        "If the search results don't contain the answer, say so. Be concise and cite sources.\n\n"
                        "Answer:"
                    )
                    out = self.generate(enhanced_prompt, max_tokens=max_tokens,
                                        temperature=temperature, stop=stop,
                                        use_reasoning=use_reasoning)
                    out["used_web_search"] = True
                    out["search_results_count"] = len(results)
                    return out
        out = self.generate(prompt, max_tokens=max_tokens, temperature=temperature,
                            stop=stop, use_reasoning=use_reasoning)
        out["used_web_search"] = False
        return out

    def generate(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        stop: Optional[list] = None,
        use_reasoning: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate text from prompt using whichever model is appropriate.

        If use_reasoning=True and a reasoning model is available:
          - Ensures reasoning model is loaded (swaps if needed)
          - Boosts max_tokens (3x) for <think> overhead
          - Removes "\\n\\n" stop sequence (thinking blocks contain double-newlines)
          - Strips <think> tags from output

        If use_reasoning=False:
          - Uses reasoning model (it's already loaded) but with normal token
            limits and standard stop sequences.  Good for simple tasks like
            greetings where you don't need elaborate chain-of-thought.
          - Reasoning models still output <think> first; we need a minimum token
            floor so the model can produce both thinking and answer.
        """
        from backends.base import GenerationConfig

        is_reasoning = (use_reasoning and self._reasoning_path is not None)

        with self._gpu_lock:
            # Ensure reasoning model is loaded for text tasks
            preferred = "reasoning" if self._reasoning_path else "vision"
            self._ensure_model(preferred)

            if is_reasoning:
                # Only boost tokens for models that produce <think> blocks
                # (DeepSeek-R1). Qwen3 with /no_think doesn't need the 3x
                # overhead — it just wastes generation time.
                model_base = os.path.basename(self._reasoning_path or "").lower()
                needs_think_boost = "deepseek" in model_base or ("r1" in model_base and "qwen" not in model_base)
                if needs_think_boost:
                    effective_max = max(
                        max_tokens * self._REASONING_TOKEN_MULTIPLIER,
                        self._REASONING_MIN_TOKENS,
                    )
                else:
                    effective_max = max(max_tokens, self._REASONING_MIN_TOKENS)
                config = GenerationConfig(
                    max_tokens=effective_max,
                    temperature=temperature,
                    stop=stop if stop is not None else [],
                )
            else:
                # Safety floor: Qwen3 with /no_think responds directly but
                # DeepSeek-R1 still emits <think> blocks.  Without a floor,
                # short limits (e.g. 50) could yield empty -> Grok escalation.
                effective_max = max(max_tokens, 128)
                # IMPORTANT: Don't use "\n\n" stop for Qwen3 — even with
                # /no_think, residual empty <think></think> tags contain
                # newlines that trigger the stop before the answer.
                model_base_nr = os.path.basename(self._reasoning_path or "").lower()
                is_qwen3 = "qwen3" in model_base_nr and "vl" not in model_base_nr
                default_stop = [] if is_qwen3 else ["\n\n"]
                config = GenerationConfig(
                    max_tokens=effective_max,
                    temperature=temperature,
                    stop=stop if stop is not None else default_stop,
                )

            try:
                if hasattr(self._backend, "chat"):
                    # Qwen3 produces <think> blocks by default which waste
                    # ~50% of generation time on thinking tokens.  Append
                    # /no_think to skip thinking for faster, direct responses.
                    # _strip_thinking() handles any residual empty tags.
                    content = prompt
                    model_base = os.path.basename(self._reasoning_path or "").lower()
                    if "qwen3" in model_base and "vl" not in model_base:
                        content = prompt.rstrip() + "\n/no_think"
                    messages = [{"role": "user", "content": content}]
                    result = self._backend.chat(messages, config=config)
                else:
                    result = self._backend.generate(prompt, config=config)
                return self._forge_result_to_dict(
                    result, model_name=f"local-{self._active_model}")
            except Exception as e:
                logger.exception("Local model generation failed: %s", e)
                return {
                    "text": "",
                    "error": str(e),
                    "duration_ms": 0,
                    "cost_usd": 0.0,
                    "model": "local",
                    "success": False,
                }

    def chat_with_image(
        self,
        text_prompt: str,
        image_path: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Analyze an image with the vision model (screen reading, image analysis).

        Swaps to the vision model if not already loaded, uses it, then leaves it
        loaded (the next text request will swap back to reasoning automatically).
        """
        if not self._vision_path:
            return {
                "text": "",
                "error": "Vision not available.  Set LOCAL_VISION_MODEL_PATH in .env.",
                "duration_ms": 0,
                "cost_usd": 0.0,
                "model": "local",
                "success": False,
            }

        from backends.base import GenerationConfig

        config = GenerationConfig(max_tokens=max_tokens, temperature=temperature)

        with self._gpu_lock:
            self._ensure_model("vision")

            if not getattr(self._backend, "is_vision", False):
                return {
                    "text": "",
                    "error": "Vision model loaded but vision not supported (missing mmproj?).",
                    "duration_ms": 0,
                    "cost_usd": 0.0,
                    "model": "local-vision",
                    "success": False,
                }

            try:
                result = self._backend.chat_with_image(text_prompt, image_path, config=config)
                return self._forge_result_to_dict(result, model_name="local-vision")
            except Exception as e:
                logger.exception("Vision inference failed: %s", e)
                return {
                    "text": "",
                    "error": str(e),
                    "duration_ms": 0,
                    "cost_usd": 0.0,
                    "model": "local-vision",
                    "success": False,
                }

    # ── External pipeline coordination ───────────────────────────
    #  Used by ImageGenerator (diffusers SDXL) or any other external
    #  GPU workload that needs the VRAM currently held by the LLM.

    def unload_for_external(self) -> None:
        """Unload the current LLM to free VRAM for an external pipeline.

        Acquires the GPU lock, unloads the backend, runs GC, and clears
        CUDA cache.  The caller is then free to load their own model.
        Call reload_after_external() when done.
        """
        with self._gpu_lock:
            if self._backend is not None:
                prev = self._active_model
                logger.info("Unloading %s model for external pipeline", prev)
                try:
                    self._backend.unload()
                except Exception as e:
                    logger.warning("Unload for external failed: %s", e)
                self._backend = None
                self._active_model = None
                gc.collect()
                logger.info("VRAM freed for external use")
            else:
                logger.debug("unload_for_external: no model was loaded")

    def reload_after_external(self, preferred: str = "reasoning") -> None:
        """Reload the LLM after an external pipeline has finished.

        Args:
            preferred: which model to restore ("reasoning" or "vision").
        """
        with self._gpu_lock:
            if preferred == "reasoning" and self._reasoning_path:
                self._swap_to("reasoning")
            elif preferred == "vision" and self._vision_path:
                self._swap_to("vision")
            elif self._reasoning_path:
                self._swap_to("reasoning")
            elif self._vision_path:
                self._swap_to("vision")
            else:
                logger.warning("No model available to reload after external pipeline")

    def get_model_info(self) -> Dict[str, Any]:
        """Return info about available models for dashboard/status display."""
        return {
            "vision_model": os.path.basename(self._vision_path) if self._vision_path else None,
            "has_vision": self._vision_path is not None,
            "reasoning_model": os.path.basename(self._reasoning_path) if self._reasoning_path else None,
            "dual_model": self._reasoning_path is not None and self._vision_path is not None,
            "active_model": self._active_model,
            "architecture": "swap" if (self._reasoning_path and self._vision_path) else "single",
        }
