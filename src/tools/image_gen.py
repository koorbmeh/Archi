"""
Local text-to-image generation via SDXL (diffusers).

All .safetensors models in models/ are loaded as SDXL pipelines.

Manages its own pipeline lifecycle:
  - Load: only when needed (on-demand)
  - Generate: one or more images per session
  - Unload: after generation to free VRAM (unless batch mode)

Batch mode: when generating multiple images, the pipeline stays loaded
for the entire batch to avoid ~4s load/unload overhead per image.

All GPU memory management is explicit.

Uncensored: safety_checker is disabled.  The user opted into a local,
unfiltered model — Archi does not apply content restrictions to
image generation.
"""

import gc
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Module-level flag: True while SDXL is using the GPU.
# The dream cycle checks this to avoid fighting over VRAM.
generating_in_progress: bool = False

# Network access guard.  Image generation runs with safety_checker disabled
# (uncensored, local-only).  If network serving is ever enabled (MCP, HTTP,
# etc.), this flag MUST stay False to prevent unchecked images from being
# served externally.  Only local callers (Discord bot, PlanExecutor) should
# trigger generation.
_ALLOW_NETWORK_SERVING: bool = False

# ── Model registry ────────────────────────────────────────
# Short aliases → model filenames.  Built dynamically from models/ dir,
# but users can also set a default via Discord ("use illustrious for images").

_model_registry: Dict[str, str] = {}   # alias → full path
_default_model_alias: Optional[str] = None  # current default (None = auto)


def _build_model_registry() -> None:
    """Scan models/ and build alias → path mapping."""
    global _model_registry
    try:
        from src.utils.paths import base_path
        models_dir = Path(base_path()) / "models"
    except Exception:
        models_dir = Path("models")

    _model_registry.clear()
    if not models_dir.is_dir():
        return

    for f in sorted(models_dir.iterdir()):
        if f.suffix != ".safetensors":
            continue
        # Build aliases from the filename:
        # "illustriousRealismBy_v10VAE.safetensors" → "illustrious", "illustriousrealism"
        stem = f.stem.lower()
        # Full stem as an alias (minus version suffixes)
        _model_registry[stem] = str(f)
        # First word (split on camelCase boundaries, underscores, digits)
        parts = re.split(r'[_\-]|(?<=[a-z])(?=[A-Z])', f.stem)
        if parts:
            short = parts[0].lower()
            if short not in _model_registry:  # don't overwrite if collision
                _model_registry[short] = str(f)
            # Also two-word alias for disambiguation
            if len(parts) >= 2:
                two_word = (parts[0] + parts[1]).lower()
                if two_word not in _model_registry:
                    _model_registry[two_word] = str(f)

    if _model_registry:
        aliases = ", ".join(sorted(set(
            k for k, v in _model_registry.items()
            if len(k) <= 20  # only show short aliases
        )))
        logger.info("Image models available: %s", aliases)


def get_image_model_aliases() -> Dict[str, str]:
    """Return {alias: path} for all discovered models."""
    if not _model_registry:
        _build_model_registry()
    return dict(_model_registry)


def resolve_image_model(name: Optional[str] = None) -> Optional[str]:
    """Resolve a model alias or partial name to a full path.

    Args:
        name: alias, partial filename, or None for default.

    Returns:
        Full path to the .safetensors file, or None.
    """
    if not _model_registry:
        _build_model_registry()

    if name is None:
        # Use configured default, or fall back to auto-discovery
        if _default_model_alias and _default_model_alias in _model_registry:
            return _model_registry[_default_model_alias]
        return None  # let _resolve_model_path() do auto-discovery

    name_lower = name.lower().strip()

    # Exact alias match
    if name_lower in _model_registry:
        return _model_registry[name_lower]

    # Partial match (user said "uber" and we have "uberrealisticpornmerge...")
    for alias, path in _model_registry.items():
        if name_lower in alias or alias.startswith(name_lower):
            return path

    # Try matching against filenames directly
    for alias, path in _model_registry.items():
        if name_lower in Path(path).stem.lower():
            return path

    return None


def set_default_image_model(alias: str) -> Optional[str]:
    """Set the default image model by alias. Returns the resolved path or None."""
    global _default_model_alias
    if not _model_registry:
        _build_model_registry()

    path = resolve_image_model(alias)
    if path:
        # Store the alias that maps to this path
        for k, v in _model_registry.items():
            if v == path:
                _default_model_alias = k
                break
        logger.info("Default image model set to: %s (%s)",
                     _default_model_alias, Path(path).stem)
        return path
    return None


def get_default_image_model_name() -> Optional[str]:
    """Return the current default model alias, or None for auto."""
    return _default_model_alias


class ImageGenerator:
    """
    SDXL text-to-image with single-GPU memory management.

    Usage:
        from src.tools.image_gen import ImageGenerator
        gen = ImageGenerator()
        result = gen.generate("a cyberpunk city at sunset")
        # result["image_path"] -> workspace/images/generated_20260212_143000.png
    """

    def __init__(self) -> None:
        self._pipeline = None

    @staticmethod
    def check_dependencies() -> Dict[str, str]:
        """Diagnose SDXL dependency availability.  Returns {package: status}."""
        results: Dict[str, str] = {}
        for pkg in ("torch", "diffusers", "transformers", "accelerate", "safetensors"):
            try:
                mod = __import__(pkg)
                ver = getattr(mod, "__version__", "unknown")
                results[pkg] = f"ok ({ver})"
            except ImportError as e:
                results[pkg] = f"MISSING — {e}"
            except Exception as e:
                results[pkg] = f"ERROR — {e}"
        # Check pipeline class
        try:
            import importlib
            mod = importlib.import_module("diffusers")
            getattr(mod, "StableDiffusionXLPipeline")
            results["StableDiffusionXLPipeline"] = "ok"
        except (ImportError, AttributeError) as e:
            results["StableDiffusionXLPipeline"] = f"MISSING — {e}"
        except Exception as e:
            results["StableDiffusionXLPipeline"] = f"ERROR — {e}"
        return results

    # ── Model discovery ────────────────────────────────────────

    @staticmethod
    def _resolve_model_path() -> Optional[str]:
        """Find an SDXL model to use.

        Search order:
          1. IMAGE_MODEL_PATH env var  (file or HF repo ID)
          2. models/ directory — first .safetensors matching known image-model names
          3. models/ directory — any lone .safetensors that isn't a known LLM file
          4. None  (caller should show a helpful error)
        """
        # 1. Explicit env var
        env_path = os.environ.get("IMAGE_MODEL_PATH", "").strip()
        if env_path:
            # Could be a local path or a HuggingFace model ID
            if os.path.exists(env_path):
                return env_path
            # Treat as HF ID (e.g. "stabilityai/stable-diffusion-xl-base-1.0")
            if "/" in env_path:
                return env_path

        # 2. Scan models/ directory
        try:
            from src.utils.paths import base_path
            models_dir = Path(base_path()) / "models"
        except Exception:
            models_dir = Path("models")

        if models_dir.is_dir():
            # Known image model keywords (covers most CivitAI / HF naming)
            keywords = (
                "sdxl", "stable-diffusion", "sd_xl", "pony", "juggernaut",
                "illustrious", "realism", "realistic", "anime", "dreamshaper",
                "deliberate", "proteus", "flux", "playground", "animagine",
            )
            for f in sorted(models_dir.iterdir()):
                if f.suffix == ".safetensors" and any(k in f.name.lower() for k in keywords):
                    logger.info("Auto-discovered image model: %s", f.name)
                    return str(f)

            # 3. Fallback: any .safetensors that isn't a .gguf-adjacent LLM file
            #    (LLMs use .gguf; .safetensors in models/ is almost certainly an image model)
            safetensors = [
                f for f in models_dir.iterdir()
                if f.suffix == ".safetensors" and "mmproj" not in f.name.lower()
            ]
            if len(safetensors) == 1:
                logger.info("Auto-discovered image model (only .safetensors): %s", safetensors[0].name)
                return str(safetensors[0])
            elif len(safetensors) > 1:
                # Multiple unknown safetensors — pick the largest (most likely the base model)
                largest = max(safetensors, key=lambda f: f.stat().st_size)
                logger.info(
                    "Multiple .safetensors found; using largest as image model: %s",
                    largest.name,
                )
                return str(largest)

        # 4. Nothing found
        return None

    @staticmethod
    def is_available() -> bool:
        """True if an image model is configured / discoverable."""
        return ImageGenerator._resolve_model_path() is not None

    # ── Output directory ───────────────────────────────────────

    @staticmethod
    def _get_output_dir() -> Path:
        try:
            from src.utils.paths import base_path
            d = Path(base_path()) / "workspace" / "images"
        except Exception:
            d = Path("workspace") / "images"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Pipeline lifecycle ─────────────────────────────────────

    @staticmethod
    def _detect_device() -> str:
        """Detect the best device for SDXL.

        torch.cuda.is_available() can return False if:
          - torch was installed without CUDA (CPU-only build)
          - the CUDA context was killed
        We log a diagnostic so the cause is obvious.
        """
        try:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                logger.info("CUDA available: %s (torch %s)", gpu_name, torch.__version__)
                return "cuda"

            # Diagnose *why* CUDA isn't available
            has_cuda_build = hasattr(torch.version, "cuda") and torch.version.cuda is not None
            if not has_cuda_build:
                logger.error(
                    "torch %s is a CPU-ONLY build (no CUDA). "
                    "Reinstall with CUDA support:\n"
                    "  pip install torch --index-url https://download.pytorch.org/whl/cu124\n"
                    "  (adjust cu124 to match your CUDA toolkit version)",
                    torch.__version__,
                )
            else:
                # Has CUDA build but runtime says unavailable
                logger.warning(
                    "torch %s was built with CUDA %s, but CUDA runtime "
                    "is not available. Check GPU drivers / CUDA toolkit.",
                    torch.__version__, torch.version.cuda,
                )
                # Try to reinitialise CUDA context
                if os.environ.get("CUDA_PATH") or os.environ.get("CUDA_HOME"):
                    try:
                        torch.cuda.init()
                        if torch.cuda.is_available():
                            return "cuda"
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("torch import/CUDA detection failed: %s", e)

        logger.warning("CUDA not detected — SDXL will run on CPU (very slow)")
        return "cpu"

    def _load_pipeline(self, model_path: str) -> bool:
        """Load the SDXL pipeline into VRAM.  Returns True on success."""
        try:
            import torch
            from diffusers import StableDiffusionXLPipeline

            device = self._detect_device()
            dtype = torch.float16 if device == "cuda" else torch.float32

            logger.info("Loading SDXL model from %s (device=%s)", model_path, device)
            t0 = time.monotonic()

            if model_path.endswith(".safetensors"):
                pipe = StableDiffusionXLPipeline.from_single_file(
                    model_path,
                    torch_dtype=dtype,
                    use_safetensors=True,
                )
            else:
                pipe = StableDiffusionXLPipeline.from_pretrained(
                    model_path,
                    torch_dtype=dtype,
                    use_safetensors=True,
                )

            # Disable safety checker if present (uncensored)
            if hasattr(pipe, "safety_checker"):
                pipe.safety_checker = None
            if hasattr(pipe, "requires_safety_checker"):
                pipe.requires_safety_checker = False

            pipe = pipe.to(device)

            # Disable tqdm progress bars — they crash on Windows services
            # where stderr is an invalid handle (WinError 1).
            pipe.set_progress_bar_config(disable=True)

            self._pipeline = pipe
            self._device = device

            logger.info("SDXL pipeline loaded in %.1fs", time.monotonic() - t0)
            return True

        except ImportError as e:
            logger.error(
                "Import failed while loading pipeline: %s\n"
                "  Ensure these packages are installed IN THE VENV:\n"
                "  pip install diffusers transformers accelerate safetensors torch",
                e,
            )
            return False
        except Exception as e:
            logger.exception("Failed to load SDXL pipeline: %s", e)
            return False

    def _unload_pipeline(self) -> None:
        """Free pipeline and VRAM."""
        if self._pipeline is not None:
            del self._pipeline
            self._pipeline = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        logger.info("SDXL pipeline unloaded")

    # ── Public API ─────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        negative_prompt: str = "low quality, blurry, distorted, deformed",
        num_inference_steps: int = 25,
        guidance_scale: float = 7.5,
        width: int = 1024,
        height: int = 1024,
        model: Optional[str] = None,
        keep_loaded: bool = False,
    ) -> Dict[str, Any]:
        """Generate a single image from a text prompt.

        Handles the full lifecycle:
          1. Load SDXL pipeline (reuses if already loaded with same model)
          2. Generate image
          3. Save to workspace/images/
          4. Unload pipeline (unless keep_loaded=True for batch mode)

        Args:
            model: Optional model alias (e.g. "illustrious").
                   None uses the configured default or auto-discovery.
            keep_loaded: If True, skip unloading after generation.
                   Used for batch generation to avoid ~4s reload per image.
                   Caller MUST call unload() when done with the batch.

        Returns dict with success, image_path, prompt, duration_ms, model_used, error.
        """
        global generating_in_progress
        t0 = time.monotonic()

        # Resolve model: explicit request → configured default → auto-discovery
        model_path = resolve_image_model(model)
        if not model_path:
            model_path = self._resolve_model_path()

        if not model_path:
            return {
                "success": False,
                "error": (
                    "No image model found. Either set IMAGE_MODEL_PATH in .env "
                    "or place an SDXL .safetensors file in the models/ directory."
                ),
                "duration_ms": 0,
            }

        generating_in_progress = True
        try:
            # Reuse pipeline if already loaded with the same model
            if self._pipeline is not None and getattr(self, '_loaded_model', None) == model_path:
                logger.info("Reusing loaded pipeline for: %s", Path(model_path).stem)
            else:
                # Unload old pipeline if switching models
                if self._pipeline is not None:
                    self._unload_pipeline()
                if not self._load_pipeline(model_path):
                    # Log a full dependency check so the real problem is obvious
                    diag = self.check_dependencies()
                    for pkg, status in diag.items():
                        logger.info("  SDXL dep check: %-30s %s", pkg, status)
                    return {
                        "success": False,
                        "error": "Failed to load SDXL pipeline (check logs for details)",
                        "duration_ms": int((time.monotonic() - t0) * 1000),
                    }
                self._loaded_model = model_path

            # Generate
            logger.info("Generating image: %s", prompt[:100])
            gen_t0 = time.monotonic()

            actual_steps = num_inference_steps

            # Log step progress instead of tqdm (tqdm crashes on Windows
            # services where stderr is an invalid handle: WinError 1)
            def _step_logger(pipe, step, timestep, callback_kwargs):
                if step % 5 == 0 or step == actual_steps - 1:
                    logger.info("  step %d/%d", step + 1, actual_steps)
                return callback_kwargs

            pipe_kwargs = dict(
                prompt=prompt,
                negative_prompt=negative_prompt,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                width=width,
                height=height,
                callback_on_step_end=_step_logger,
            )

            result = self._pipeline(**pipe_kwargs)
            image = result.images[0]
            gen_ms = int((time.monotonic() - gen_t0) * 1000)

            # Save
            output_dir = self._get_output_dir()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            image_path = output_dir / f"generated_{ts}.png"
            image.save(str(image_path))
            logger.info("Image saved: %s (%d ms generation)", image_path, gen_ms)

            return {
                "success": True,
                "image_path": str(image_path),
                "prompt": prompt,
                "model_used": Path(model_path).stem,
                "duration_ms": int((time.monotonic() - t0) * 1000),
            }

        except Exception as e:
            logger.exception("Image generation failed: %s", e)
            return {
                "success": False,
                "error": str(e),
                "duration_ms": int((time.monotonic() - t0) * 1000),
            }

        finally:
            if not keep_loaded:
                self._unload_pipeline()
                generating_in_progress = False

    def unload(self) -> None:
        """Explicitly unload the pipeline and free VRAM.

        Call this after a batch of keep_loaded=True generations.
        """
        global generating_in_progress
        self._unload_pipeline()
        generating_in_progress = False
