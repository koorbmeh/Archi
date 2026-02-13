"""
Local video generation via WAN 2.1 diffusers pipelines.

Supports two modes:
  - Text-to-video (T2V): Wan2.1-T2V-1.3B  (~29 GB download, ~8 GB VRAM w/ CPU offload)
  - Image-to-video (I2V): Wan2.1-I2V-14B-480P  (~50 GB download, heavy CPU offload)

Note on download sizes: the "1.3B" / "14B" refer only to the video transformer.
Both models share a ~20 GB UMT5-XXL text encoder, which is the bulk of the download.

Manages its own pipeline lifecycle:
  - Load: only when needed (on-demand)
  - Generate: single video per request (49 frames @ 16 FPS = ~3 s)
  - Unload: immediately after to free VRAM for the LLM

Coordinates with LocalModel to unload/reload the reasoning model
around each generation.  All GPU memory management is explicit.

Output: 480p (832x480) MP4 saved to workspace/videos/.

Uncensored: no safety checker is applied.  The user opted into a local,
unfiltered model -- Archi does not apply content restrictions to
video generation.
"""

import gc
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Module-level flag: True while a WAN pipeline is using the GPU.
# The dream cycle checks this to avoid fighting over VRAM.
generating_in_progress: bool = False

# Default HuggingFace model IDs
_DEFAULT_T2V_MODEL = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
_DEFAULT_I2V_MODEL = "Wan-AI/Wan2.1-I2V-14B-480P-Diffusers"


class VideoGenerator:
    """
    WAN 2.1 text-to-video / image-to-video with coordinated single-GPU swap.

    Usage:
        from src.tools.video_gen import VideoGenerator
        gen = VideoGenerator(local_model=local_model_instance)

        # Text-to-video
        result = gen.generate("a dog running in a park")

        # Image-to-video
        result = gen.generate("camera slowly pans", image_path="/path/to/img.png")

        # result["video_path"] -> workspace/videos/generated_20260212_150000.mp4
    """

    def __init__(self, local_model: Optional[Any] = None) -> None:
        self._local_model = local_model
        self._pipeline = None
        self._device: str = "cpu"

    # -- Model discovery ------------------------------------------------

    @staticmethod
    def _resolve_model_id(mode: str = "t2v") -> str:
        """Return a HuggingFace repo ID or local path for the requested mode.

        Search order:
          1. VIDEO_T2V_MODEL_PATH / VIDEO_I2V_MODEL_PATH env var
          2. Default HuggingFace model ID (auto-downloads + caches)
        """
        if mode == "i2v":
            env_path = os.environ.get("VIDEO_I2V_MODEL_PATH", "").strip()
            default = _DEFAULT_I2V_MODEL
        else:
            env_path = os.environ.get("VIDEO_T2V_MODEL_PATH", "").strip()
            default = _DEFAULT_T2V_MODEL

        if env_path:
            # Could be a local dir or HF repo ID
            if os.path.isdir(env_path):
                return env_path
            if "/" in env_path:
                return env_path  # Treat as HF repo ID

        return default

    @staticmethod
    def is_available() -> bool:
        """True if diffusers WAN pipelines can be imported."""
        try:
            from diffusers import WanPipeline  # noqa: F401
            return True
        except ImportError:
            return False

    # -- Output directory -----------------------------------------------

    @staticmethod
    def _get_output_dir() -> Path:
        try:
            from src.utils.paths import base_path
            d = Path(base_path()) / "workspace" / "videos"
        except Exception:
            d = Path("workspace") / "videos"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- Pipeline lifecycle ---------------------------------------------

    @staticmethod
    def _detect_device() -> str:
        """Detect the best device for video generation.

        torch.cuda.is_available() can return False if another library
        (e.g. llama-cpp-python) killed the CUDA context when it unloaded.
        We fall back to checking CUDA_PATH + torch.cuda.init() before giving up.
        """
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            # CUDA context may be dead -- try to reinitialise it
            if os.environ.get("CUDA_PATH") or os.environ.get("CUDA_HOME"):
                try:
                    torch.cuda.init()
                    if torch.cuda.is_available():
                        return "cuda"
                except Exception:
                    pass
        except Exception:
            pass
        logger.warning("CUDA not detected -- WAN video gen will run on CPU (very slow)")
        return "cpu"

    def _load_pipeline(self, mode: str, model_id: str, height: int = 480) -> bool:
        """Load the WAN pipeline.  Returns True on success.

        Args:
            mode: "t2v" for text-to-video, "i2v" for image-to-video
            model_id: HuggingFace repo ID or local directory path
            height: Output video height (determines flow_shift: 3.0 for 480P, 5.0 for 720P)
        """
        try:
            import torch
            from diffusers import AutoencoderKLWan
            from diffusers.schedulers import UniPCMultistepScheduler

            device = self._detect_device()
            # WAN was trained in bfloat16 — using float16 causes precision
            # issues that degrade output quality.
            dtype = torch.bfloat16 if device == "cuda" else torch.float32

            logger.info("Loading WAN %s from %s (device=%s, dtype=%s)",
                        mode.upper(), model_id, device, dtype)
            t0 = time.monotonic()

            # VAE must be loaded in float32 (WAN requirement)
            vae = AutoencoderKLWan.from_pretrained(
                model_id,
                subfolder="vae",
                torch_dtype=torch.float32,
            )

            if mode == "i2v":
                from diffusers import WanImageToVideoPipeline
                pipe = WanImageToVideoPipeline.from_pretrained(
                    model_id,
                    vae=vae,
                    torch_dtype=dtype,
                )
            else:
                from diffusers import WanPipeline
                pipe = WanPipeline.from_pretrained(
                    model_id,
                    vae=vae,
                    torch_dtype=dtype,
                )

            # CRITICAL: Set the correct scheduler with flow_shift.
            # Without this, the denoising process uses wrong noise levels
            # and produces blank/mush output instead of coherent video.
            # flow_shift = 3.0 for 480P, 5.0 for 720P
            flow_shift = 5.0 if height >= 720 else 3.0
            pipe.scheduler = UniPCMultistepScheduler.from_config(
                pipe.scheduler.config,
                flow_shift=flow_shift,
            )
            logger.info("%s scheduler: UniPC with flow_shift=%.1f", mode.upper(), flow_shift)

            # CPU offload is REQUIRED for 12 GB GPU.  The text encoder
            # alone needs ~8 GB; loading everything to GPU at once would
            # overflow 12 GB and cause severe swapping / hangs.
            # CPU offload moves each component to GPU only for its forward
            # pass, keeping peak VRAM at ~8 GB (the text encoder).
            pipe.enable_model_cpu_offload()
            logger.info("%s pipeline: CPU offloading enabled", mode.upper())

            # Disable tqdm progress bars -- they crash on Windows services
            # where stderr is an invalid handle (WinError 1).
            pipe.set_progress_bar_config(disable=True)

            self._pipeline = pipe
            self._device = device

            logger.info("WAN %s pipeline loaded in %.1fs", mode.upper(), time.monotonic() - t0)
            return True

        except ImportError as e:
            logger.error(
                "WAN dependencies not installed (%s). "
                "Run: scripts\\install.py videogen", e
            )
            return False
        except Exception as e:
            logger.exception("Failed to load WAN %s pipeline: %s", mode.upper(), e)
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
        logger.info("WAN pipeline unloaded")

    # -- Public API -----------------------------------------------------

    def generate(
        self,
        prompt: str,
        image_path: Optional[str] = None,
        negative_prompt: str = "low quality, blurry, distorted, static, frozen, jittery",
        num_frames: int = 49,
        num_inference_steps: int = 25,
        guidance_scale: float = 5.0,
        width: int = 832,
        height: int = 480,
        fps: int = 16,
    ) -> Dict[str, Any]:
        """Generate a video from text (T2V) or from text + image (I2V).

        Handles the full lifecycle:
          1. Unload the LLM (free VRAM)
          2. Load appropriate WAN pipeline (T2V or I2V)
          3. Generate video frames
          4. Export to MP4 in workspace/videos/
          5. Unload pipeline
          6. Reload the LLM

        Args:
            prompt: Text description for the video.
            image_path: Path to a starting image (enables I2V mode).
            negative_prompt: Things to avoid.
            num_frames: Number of frames (49 = ~3 s at 16 FPS; use 81 for ~5 s).
            num_inference_steps: Denoising steps (25 is fast; 40-50 for max quality).
            guidance_scale: Prompt adherence (5.0 is WAN default).
            width: Video width in pixels (832 for 480p).
            height: Video height in pixels (480 for 480p).
            fps: Frames per second for the output MP4.

        Returns:
            dict with success, video_path, prompt, duration_ms, mode, error.
        """
        global generating_in_progress
        t0 = time.monotonic()

        # Decide mode
        mode = "i2v" if image_path else "t2v"
        model_id = self._resolve_model_id(mode)

        # For I2V, validate image exists
        if mode == "i2v" and not os.path.isfile(image_path):
            return {
                "success": False,
                "error": f"Image file not found: {image_path}",
                "duration_ms": 0,
                "mode": mode,
            }

        # Step 1 -- free VRAM
        generating_in_progress = True
        if self._local_model is not None:
            logger.info("Requesting VRAM: unloading LLM for video generation")
            self._local_model.unload_for_external()

        try:
            # Step 2 -- load pipeline
            if not self._load_pipeline(mode, model_id, height=height):
                return {
                    "success": False,
                    "error": f"Failed to load WAN {mode.upper()} pipeline (check logs)",
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "mode": mode,
                }

            # Step 3 -- generate
            logger.info("Generating %s video: %s", mode.upper(), prompt[:100])
            gen_t0 = time.monotonic()

            # Step callback for logging (no tqdm — tqdm crashes on Windows services)
            _step_times = [gen_t0]

            def _step_logger(pipe, step, timestep, callback_kwargs):
                now = time.monotonic()
                elapsed = now - _step_times[-1]
                _step_times.append(now)
                if step % 5 == 0 or step == num_inference_steps - 1:
                    total = now - gen_t0
                    logger.info(
                        "  step %d/%d  (%.1fs this step, %.1fs total)",
                        step + 1, num_inference_steps, elapsed, total,
                    )
                return callback_kwargs

            # Build pipeline kwargs
            pipe_kwargs = dict(
                prompt=prompt,
                negative_prompt=negative_prompt,
                num_frames=num_frames,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                width=width,
                height=height,
                callback_on_step_end=_step_logger,
            )

            # I2V: add the starting image
            if mode == "i2v":
                from PIL import Image as PILImage
                image = PILImage.open(image_path).convert("RGB")
                # Resize to match target resolution if needed
                if image.size != (width, height):
                    image = image.resize((width, height), PILImage.LANCZOS)
                pipe_kwargs["image"] = image

            output = self._pipeline(**pipe_kwargs)
            gen_ms = int((time.monotonic() - gen_t0) * 1000)

            # Step 4 -- export to MP4
            from diffusers.utils import export_to_video

            output_dir = self._get_output_dir()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            video_path = output_dir / f"generated_{ts}.mp4"

            export_to_video(output.frames[0], str(video_path), fps=fps)
            logger.info("Video saved: %s (%d ms generation)", video_path, gen_ms)

            return {
                "success": True,
                "video_path": str(video_path),
                "prompt": prompt,
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "mode": mode,
            }

        except Exception as e:
            logger.exception("Video generation failed: %s", e)
            return {
                "success": False,
                "error": str(e),
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "mode": mode,
            }

        finally:
            # Step 5 -- always clean up
            self._unload_pipeline()

            # Step 6 -- always reload LLM
            if self._local_model is not None:
                logger.info("Restoring LLM after video generation")
                self._local_model.reload_after_external()

            generating_in_progress = False
