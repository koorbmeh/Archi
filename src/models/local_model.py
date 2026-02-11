"""
Local LLM wrapper for Archi using Forge (model-agnostic inference).
Uses Qwen3VL-8B by default for vision + reasoning. Gate B and Gate C use this.
Supports free web search via WebSearchTool when enable_web_search=True.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Keywords that suggest the query needs current/live data (use web search)
_SEARCH_KEYWORDS = (
    "current", "today", "now", "latest", "recent", "weather", "news",
    "stock price", "score", "what happened", "what's happening", "headline",
)

# Qwen3VL-8B is the primary model (vision + reasoning for Gate C)
_DEFAULT_VL_MODEL = "Qwen3VL-8B-Instruct-Q4_K_M.gguf"


def _base_path() -> str:
    base = os.environ.get("ARCHI_ROOT")
    if base:
        return os.path.normpath(base)
    cur = Path(__file__).resolve().parent
    for _ in range(6):
        if (cur / "config").is_dir():
            return str(cur)
        cur = cur.parent
    return os.getcwd()


def _default_model_path() -> Optional[str]:
    """Find Qwen3VL model. Prefer LOCAL_MODEL_PATH, then models/ under ARCHI_ROOT."""
    explicit = os.environ.get("LOCAL_MODEL_PATH")
    if explicit and os.path.isfile(explicit):
        return explicit

    base = _base_path()
    models_dir = os.path.join(base, "models")

    # Primary: Qwen3VL-8B (vision + reasoning)
    vl_path = os.path.join(models_dir, _DEFAULT_VL_MODEL)
    if os.path.isfile(vl_path):
        return vl_path

    # Fallback: any Qwen3VL in models/
    if os.path.isdir(models_dir):
        for name in os.listdir(models_dir):
            if "qwen3" in name.lower() and "vl" in name.lower() and name.endswith(".gguf"):
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
    Wraps Forge backend for local inference. Loads Qwen3VL-8B by default.
    Supports text generation, chat, and vision (chat_with_image).
    """

    def __init__(self, model_path: Optional[str] = None) -> None:
        _ensure_forge_on_path()
        from backends import select_backend
        from backends.base import GenerationConfig
        from utils.model_detector import detect_model

        path = model_path or _default_model_path()
        if not path or not os.path.isfile(path):
            raise ValueError(
                "LOCAL_MODEL_PATH must point to a GGUF file (e.g. Qwen3VL-8B-Instruct-Q4_K_M.gguf). "
                "Place the model and mmproj in models/ or set .env. See RUN.md."
            )

        logger.info("Loading local model via Forge: %s", path)
        try:
            model_info = detect_model(path)
            self._backend = select_backend(model_info)
            self._backend.load(
                path,
                n_gpu_layers=-1,
                n_ctx=4096,
                verbose=False,
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to load model: {e}. "
                "For Qwen3-VL vision, install JamePeng llama-cpp-python 0.3.24 fork. See RUN.md."
            ) from e

        self._path = path
        self._search_tool: Optional[Any] = None
        self._has_vision = getattr(self._backend, "is_vision", False)
        logger.info("Local model loaded successfully (vision=%s)", self._has_vision)

    def _get_search_tool(self) -> Any:
        if self._search_tool is None:
            try:
                from src.tools.web_search_tool import WebSearchTool
                self._search_tool = WebSearchTool()
            except ImportError:
                self._search_tool = False  # type: ignore[assignment]
        return self._search_tool if self._search_tool else None

    def _needs_search(self, prompt: str) -> bool:
        """True if the prompt likely needs current/live data from the web."""
        return any(kw in prompt.lower() for kw in _SEARCH_KEYWORDS)

    def _forge_result_to_dict(
        self,
        result: Any,
        used_web_search: bool = False,
        search_results_count: int = 0,
    ) -> Dict[str, Any]:
        """Convert Forge GenerationResult to Archi's expected dict shape."""
        tokens = result.tokens_generated or len((result.text or "").split())
        return {
            "text": (result.text or "").strip(),
            "tokens": tokens,
            "duration_ms": int(result.time_seconds * 1000),
            "cost_usd": 0.0,
            "model": "local",
            "success": True,
            "used_web_search": used_web_search,
            "search_results_count": search_results_count,
        }

    def generate_with_tools(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        enable_web_search: bool = False,
        stop: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Generate response, optionally using free web search first.
        If enable_web_search and prompt needs current data, search then generate from results.
        Returns same dict shape as generate(), plus used_web_search and search_results_count when applicable.
        """
        if enable_web_search and self._needs_search(prompt):
            tool = self._get_search_tool()
            if tool is not None:
                logger.info("Local model using free web search")
                results = tool.search(prompt, max_results=3)
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
                    out = self.generate(enhanced_prompt, max_tokens=max_tokens, temperature=temperature, stop=stop)
                    out["used_web_search"] = True
                    out["search_results_count"] = len(results)
                    return out
        out = self.generate(prompt, max_tokens=max_tokens, temperature=temperature, stop=stop)
        out["used_web_search"] = False
        return out

    def generate(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        stop: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Generate text from prompt. Returns dict with text, tokens, duration_ms,
        cost_usd (0), model, success; or error and success=False on failure.
        """
        from backends.base import GenerationConfig

        config = GenerationConfig(
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop or ["\n\n"],
        )
        try:
            result = self._backend.generate(prompt, config=config)
            return self._forge_result_to_dict(result)
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
        Analyze an image with the vision model. For Gate C (screen reading, UI analysis).
        Returns same dict shape as generate(); raises if vision not available.
        """
        from backends.base import GenerationConfig

        if not self._has_vision:
            return {
                "text": "",
                "error": "Vision not available. Load Qwen3VL with mmproj file.",
                "duration_ms": 0,
                "cost_usd": 0.0,
                "model": "local",
                "success": False,
            }

        config = GenerationConfig(max_tokens=max_tokens, temperature=temperature)
        try:
            result = self._backend.chat_with_image(text_prompt, image_path, config=config)
            return self._forge_result_to_dict(result)
        except Exception as e:
            logger.exception("Vision inference failed: %s", e)
            return {
                "text": "",
                "error": str(e),
                "duration_ms": 0,
                "cost_usd": 0.0,
                "model": "local",
                "success": False,
            }

    @property
    def has_vision(self) -> bool:
        """True if the loaded model supports vision (chat_with_image)."""
        return self._has_vision
