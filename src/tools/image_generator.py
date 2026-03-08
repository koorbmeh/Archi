"""Visual content pipeline — generates images for content posts.

Bridges the existing SDXL local model (image_gen.py) into the content
creation pipeline.  Generates images sized for each platform with
optional text overlays via Pillow.

Phase 2 of Content Strategy (session 242).

Public API:
    generate_content_image(topic, platform, ...) -> dict
    add_text_overlay(image_path, text, ...) -> str
    PLATFORM_DIMENSIONS  — {platform: (width, height)}
"""

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Platform image dimensions ────────────────────────────────────────

PLATFORM_DIMENSIONS: Dict[str, tuple] = {
    "instagram_post":  (1080, 1080),   # 1:1 square
    "instagram_story": (1080, 1920),   # 9:16 vertical
    "twitter":         (1200, 675),    # 16:9 landscape
    "tweet":           (1200, 675),    # alias
    "facebook_post":   (1200, 630),    # ~1.91:1 landscape
    "blog":            (1200, 630),    # Wide hero image
    "youtube":         (1280, 720),    # 16:9 thumbnail
    "reddit":          (1200, 628),    # ~1.91:1
    "default":         (1024, 1024),   # SDXL native square
}


def _get_output_dir() -> Path:
    """Get image output directory, creating if needed."""
    try:
        from src.utils.paths import base_path
        d = Path(base_path()) / "workspace" / "images" / "content"
    except Exception:
        d = Path("workspace") / "images" / "content"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Prompt engineering for content images ────────────────────────────

def _build_image_prompt(
    topic: str,
    platform: str = "default",
    style: str = "modern digital art",
    pillar: str = "",
) -> str:
    """Build an SDXL prompt optimized for content imagery.

    Combines topic, platform context, and style direction into
    a prompt that SDXL handles well (short, descriptive, keyword-rich).
    """
    # Platform-specific style hints
    platform_hints = {
        "instagram_post":  "eye-catching, vibrant, social media aesthetic",
        "instagram_story": "vertical composition, bold, mobile-optimized",
        "twitter":         "clean, professional, attention-grabbing",
        "tweet":           "clean, professional, attention-grabbing",
        "facebook_post":   "engaging, shareable, warm colors",
        "blog":            "professional header image, wide composition",
        "youtube":         "dramatic, thumbnail-ready, high contrast",
        "reddit":          "interesting, detailed, reddit-worthy",
    }

    # Pillar-specific visual direction
    pillar_styles = {
        "ai_tech":           "futuristic, digital, tech aesthetic, circuits, neon",
        "finance":           "charts, graphs, gold, currency, professional",
        "health_fitness":    "energetic, nature, wellness, bright, clean",
        "self_improvement":  "inspirational, sunrise, growth, mindset",
        "music":             "audio waves, instruments, concert, dynamic",
    }

    parts = [topic.strip().rstrip(".")]
    parts.append(style)

    hint = platform_hints.get(platform, "")
    if hint:
        parts.append(hint)

    pillar_style = pillar_styles.get(pillar, "")
    if pillar_style:
        parts.append(pillar_style)

    # SDXL works best with comma-separated keywords, ~20-40 words
    prompt = ", ".join(parts)

    # Truncate to reasonable length for SDXL
    if len(prompt) > 300:
        prompt = prompt[:297] + "..."

    return prompt


# ── Text overlay ─────────────────────────────────────────────────────

def add_text_overlay(
    image_path: str,
    text: str,
    position: str = "bottom",
    font_size: int = 48,
    text_color: str = "white",
    bg_color: str = "black",
    bg_opacity: int = 160,
) -> str:
    """Add a text overlay banner to an image.

    Creates a semi-transparent background strip with text.
    Saves to a new file (appends _overlay to the stem).

    Args:
        image_path: Path to the source image.
        text: Text to overlay.
        position: "top", "center", or "bottom".
        font_size: Font size in pixels.
        text_color: CSS color name or hex.
        bg_color: Background strip color.
        bg_opacity: Background transparency (0-255).

    Returns:
        Path to the overlaid image, or original path on error.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow not available for text overlay")
        return image_path

    try:
        img = Image.open(image_path).convert("RGBA")
        w, h = img.size

        # Create overlay layer
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Try to load a good font, fall back to default
        font = None
        for font_path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
        ]:
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, font_size)
                    break
                except Exception:
                    continue
        if font is None:
            font = ImageFont.load_default()

        # Measure text
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        # Word-wrap if text is wider than image
        if text_w > w - 40:
            lines = _wrap_text(text, font, w - 40, draw)
        else:
            lines = [text]

        total_text_h = sum(
            draw.textbbox((0, 0), line, font=font)[3]
            - draw.textbbox((0, 0), line, font=font)[1]
            for line in lines
        ) + (len(lines) - 1) * 8  # 8px line spacing

        # Background strip
        padding = 20
        strip_h = total_text_h + padding * 2

        if position == "top":
            strip_y = 0
        elif position == "center":
            strip_y = (h - strip_h) // 2
        else:  # bottom
            strip_y = h - strip_h

        # Parse bg_color
        bg_rgba = _parse_color(bg_color, bg_opacity)
        draw.rectangle([(0, strip_y), (w, strip_y + strip_h)], fill=bg_rgba)

        # Draw text lines
        y_offset = strip_y + padding
        for line in lines:
            line_bbox = draw.textbbox((0, 0), line, font=font)
            line_w = line_bbox[2] - line_bbox[0]
            line_h = line_bbox[3] - line_bbox[1]
            x = (w - line_w) // 2  # center horizontally
            draw.text((x, y_offset), line, fill=text_color, font=font)
            y_offset += line_h + 8

        # Composite
        result = Image.alpha_composite(img, overlay)
        result = result.convert("RGB")

        # Save
        src = Path(image_path)
        out_path = src.parent / f"{src.stem}_overlay{src.suffix}"
        result.save(str(out_path))
        logger.info("Text overlay added: %s", out_path)
        return str(out_path)

    except Exception as e:
        logger.error("Text overlay failed: %s", e)
        return image_path


def _wrap_text(text: str, font, max_width: int, draw) -> list:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]

    if current_line:
        lines.append(" ".join(current_line))

    return lines or [text]


def _parse_color(color: str, opacity: int = 255) -> tuple:
    """Parse a color name or hex to RGBA tuple."""
    color_map = {
        "black": (0, 0, 0),
        "white": (255, 255, 255),
        "red": (255, 0, 0),
        "blue": (0, 0, 255),
        "green": (0, 128, 0),
        "navy": (0, 0, 128),
        "dark": (30, 30, 30),
    }
    rgb = color_map.get(color.lower(), (0, 0, 0))
    return (*rgb, min(255, max(0, opacity)))


# ── Resize / crop for platform ───────────────────────────────────────

def resize_for_platform(
    image_path: str,
    platform: str = "default",
) -> str:
    """Resize and crop an image to match platform dimensions.

    Uses center-crop to maintain composition. Saves to new file.

    Returns path to resized image, or original on error.
    """
    dims = PLATFORM_DIMENSIONS.get(platform, PLATFORM_DIMENSIONS["default"])
    target_w, target_h = dims

    try:
        from PIL import Image

        img = Image.open(image_path)
        w, h = img.size

        # Already correct size?
        if w == target_w and h == target_h:
            return image_path

        # Scale to cover target dimensions, then center-crop
        scale = max(target_w / w, target_h / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Center crop
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        img = img.crop((left, top, left + target_w, top + target_h))

        # Save
        src = Path(image_path)
        out_path = src.parent / f"{src.stem}_{platform}{src.suffix}"
        img.save(str(out_path))
        logger.debug("Resized for %s: %s → %dx%d", platform, out_path,
                      target_w, target_h)
        return str(out_path)

    except ImportError:
        logger.warning("Pillow not available for resize")
        return image_path
    except Exception as e:
        logger.error("Resize failed for %s: %s", platform, e)
        return image_path


# ── Main public API ──────────────────────────────────────────────────

def generate_content_image(
    topic: str,
    platform: str = "default",
    style: str = "modern digital art",
    pillar: str = "",
    overlay_text: str = "",
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a platform-optimized image for a content post.

    Full pipeline:
    1. Build an SDXL-optimized prompt from topic + platform + pillar
    2. Generate at SDXL native resolution (1024x1024)
    3. Resize/crop for the target platform
    4. Optionally add text overlay

    Args:
        topic: Content topic (used to build the image prompt).
        platform: Target platform (instagram_post, twitter, blog, etc.).
        style: Base style direction for the image.
        pillar: Brand pillar for visual theming.
        overlay_text: Optional text to overlay on the image.
        model: Optional SDXL model alias.

    Returns:
        Dict with: success, image_path, prompt, platform, duration_ms, error.
    """
    t0 = time.monotonic()

    # Step 1: Build prompt
    prompt = _build_image_prompt(topic, platform, style, pillar)
    logger.info("Content image prompt: %s", prompt[:120])

    # Step 2: Generate via SDXL
    try:
        from src.tools.image_gen import ImageGenerator as SDXLGenerator
    except ImportError:
        return {
            "success": False,
            "error": "SDXL image_gen module not available",
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }

    gen = SDXLGenerator()
    if not gen.is_available():
        return {
            "success": False,
            "error": (
                "No SDXL model found. Place an SDXL .safetensors file "
                "in the models/ directory, or set IMAGE_MODEL_PATH in .env."
            ),
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }

    result = gen.generate(
        prompt=prompt,
        width=1024,
        height=1024,
        model=model,
    )

    if not result.get("success"):
        return {
            "success": False,
            "error": result.get("error", "SDXL generation failed"),
            "prompt": prompt,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }

    image_path = result["image_path"]

    # Step 3: Resize for platform
    if platform != "default":
        image_path = resize_for_platform(image_path, platform)

    # Step 4: Optional text overlay
    if overlay_text:
        image_path = add_text_overlay(image_path, overlay_text)

    # Copy to content output dir for organization
    final_path = _copy_to_content_dir(image_path, topic, platform)

    total_ms = int((time.monotonic() - t0) * 1000)
    logger.info("Content image generated in %dms: %s → %s",
                total_ms, platform, final_path)

    return {
        "success": True,
        "image_path": final_path,
        "prompt": prompt,
        "platform": platform,
        "model_used": result.get("model_used", ""),
        "duration_ms": total_ms,
    }


def _copy_to_content_dir(image_path: str, topic: str, platform: str) -> str:
    """Copy generated image to the organized content directory."""
    import shutil
    out_dir = _get_output_dir()
    ts = time.strftime("%Y%m%d_%H%M%S")
    # Build a short slug from topic
    slug = "".join(c if c.isalnum() else "_" for c in topic[:30]).strip("_").lower()
    ext = Path(image_path).suffix or ".png"
    dest = out_dir / f"{ts}_{platform}_{slug}{ext}"
    try:
        shutil.copy2(image_path, str(dest))
        return str(dest)
    except Exception as e:
        logger.warning("Failed to copy to content dir: %s", e)
        return image_path


def is_available() -> bool:
    """Check if visual content pipeline is usable (SDXL + Pillow)."""
    try:
        from src.tools.image_gen import ImageGenerator
        return ImageGenerator.is_available()
    except Exception:
        return False
