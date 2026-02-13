"""
Local text-to-image generation via diffusers SDXL pipeline.

Manages its own pipeline lifecycle:
  - Load: only when needed (on-demand)
  - Generate: single image per request
  - Unload: immediately after to free VRAM for the LLM

Coordinates with LocalModel to unload/reload the reasoning model
around each generation.  All GPU memory management is explicit.

Uncensored: safety_checker is disabled.  The user opted into a local,
unfiltered model — Archi does not apply content restrictions to
image generation.
"""

import gc
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Module-level flag: True while SDXL is using the GPU.
# The dream cycle checks this to avoid fighting over VRAM.
generating_in_progress: bool = False


class ImageGenerator:
    """
    SDXL text-to-image with coordinated single-GPU swap.

    Usage:
        from src.tools.image_gen import ImageGenerator
        gen = ImageGenerator(local_model=local_model_instance)
        result = gen.generate("a cyberpunk city at sunset")
        # result["image_path"] -> workspace/images/generated_20260212_143000.png
    """

    def __init__(self, local_model: Optional[Any] = None) -> None:
        self._local_model = local_model
        self._pipeline = None

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

        torch.cuda.is_available() can return False if another library
        (e.g. llama-cpp-python) killed the CUDA context when it unloaded.
        We fall back to checking CUDA_PATH + nvidia-smi before giving up.
        """
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            # CUDA context may be dead — try to reinitialise it
            if os.environ.get("CUDA_PATH") or os.environ.get("CUDA_HOME"):
                try:
                    torch.cuda.init()
                    if torch.cuda.is_available():
                        return "cuda"
                except Exception:
                    pass
        except Exception:
            pass
        logger.warning("CUDA not detected — SDXL will run on CPU (very slow)")
        return "cpu"

    def _load_pipeline(self, model_path: str) -> bool:
        """Load the SDXL pipeline into VRAM.  Returns True on success."""
        try:
            import torch
            from diffusers import StableDiffusionXLPipeline

            device = self._detect_device()
            dtype = torch.float16 if device == "cuda" else torch.float32

            logger.info("Loading SDXL from %s (device=%s)", model_path, device)
            t0 = time.monotonic()

            # Single .safetensors file (CivitAI etc.) vs HF directory/repo
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

            # Disable safety checker (uncensored)
            pipe.safety_checker = None
            pipe.requires_safety_checker = False

            pipe = pipe.to(device)

            # Disable tqdm progress bars — they crash on Windows services
            # where stderr is an invalid handle (WinError 1).
            # We use callback_on_step_end for logging instead.
            pipe.set_progress_bar_config(disable=True)

            self._pipeline = pipe
            self._device = device

            logger.info("SDXL pipeline loaded in %.1fs", time.monotonic() - t0)
            return True

        except ImportError:
            logger.error(
                "diffusers not installed. Run: pip install diffusers transformers accelerate safetensors"
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
    ) -> Dict[str, Any]:
        """Generate a single image from a text prompt.

        Handles the full lifecycle:
          1. Unload the LLM (free VRAM)
          2. Load SDXL pipeline
          3. Generate image
          4. Save to workspace/images/
          5. Unload pipeline
          6. Reload the LLM

        Returns dict with success, image_path, prompt, duration_ms, error.
        """
        global generating_in_progress
        t0 = time.monotonic()
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

        # Step 1 — free VRAM
        generating_in_progress = True
        if self._local_model is not None:
            logger.info("Requesting VRAM: unloading LLM")
            self._local_model.unload_for_external()

        try:
            # Step 2 — load SDXL
            if not self._load_pipeline(model_path):
                return {
                    "success": False,
                    "error": "Failed to load SDXL pipeline (check logs for details)",
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                }

            # Step 3 — generate
            logger.info("Generating image: %s", prompt[:100])
            gen_t0 = time.monotonic()

            # Log step progress instead of tqdm (tqdm crashes on Windows
            # services where stderr is an invalid handle: WinError 1)
            def _step_logger(pipe, step, timestep, callback_kwargs):
                if step % 5 == 0 or step == num_inference_steps - 1:
                    logger.info("  step %d/%d", step + 1, num_inference_steps)
                return callback_kwargs

            result = self._pipeline(
                prompt=prompt,
                negative_prompt=negative_prompt,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                width=width,
                height=height,
                callback_on_step_end=_step_logger,
            )
            image = result.images[0]
            gen_ms = int((time.monotonic() - gen_t0) * 1000)

            # Step 4 — save
            output_dir = self._get_output_dir()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            image_path = output_dir / f"generated_{ts}.png"
            image.save(str(image_path))
            logger.info("Image saved: %s (%d ms generation)", image_path, gen_ms)

            return {
                "success": True,
                "image_path": str(image_path),
                "prompt": prompt,
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
            # Step 5 — always clean up
            self._unload_pipeline()

            # Step 6 — always reload LLM
            if self._local_model is not None:
                logger.info("Restoring LLM after image generation")
                self._local_model.reload_after_external()

            generating_in_progress = False
