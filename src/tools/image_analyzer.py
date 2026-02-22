"""
Image Analyzer — Vision API service for UI element location.

Extracted from ComputerUse (session 75) to separate concerns:
- ImageAnalyzer: builds vision prompts, calls API, parses coordinates
- ComputerUse: orchestrates click routing (cache → known → vision → fallback)

Uses OpenRouter vision API (Claude Haiku 4.5 for computer use).
"""

import base64
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


def expand_target_description(target: str) -> str:
    """Expand common targets with clearer descriptions for vision."""
    lower = target.lower().strip()
    if "start" in lower and ("windows" in lower or "button" in lower):
        return (
            "Windows Start button - the icon with the four-square Windows logo on "
            "the taskbar. On Windows 11 it is often to the RIGHT of a small weather "
            "icon. It is NOT the weather icon (sun/temperature). Click the Windows "
            "logo (four squares), not the weather widget."
        )
    return target


def _build_start_button_prompt(
    screen_w: int, screen_h: int,
) -> str:
    """Build a specialized vision prompt for the Windows Start button."""
    x_min, x_max = int(screen_w * 0.25), int(screen_w * 0.45)
    y_min, y_max = screen_h - 80, screen_h - 10
    return (
        f"You are analyzing a Windows 11 desktop screenshot ({screen_w}×{screen_h} pixels).\n\n"
        "CRITICAL: Find the Windows Start button - a BLUE SQUARE with 4 smaller squares inside (Windows logo).\n"
        "It is in the TASKBAR at the BOTTOM of the screen. Ignore Windows logos in window title bars (top).\n"
        "It is NOT the weather widget (sun/cloud on far left). NOT the search icon.\n"
        f"Coordinates: X between {x_min}-{x_max} (center), Y between {y_min}-{y_max} (bottom taskbar).\n\n"
        "Find the blue 4-square logo IN THE TASKBAR (bottom). Return its center coordinates.\n"
        f"Return ONLY JSON: {{\"x\": <int>, \"y\": <int>}}. Y must be {y_min}-{y_max}. No other text."
    )


def _build_generic_prompt(target: str, screen_w: int, screen_h: int) -> str:
    """Build a generic vision prompt for finding a UI element."""
    expanded = expand_target_description(target)
    return (
        f"Look at this screenshot. Find the UI element: {expanded}. "
        f"The original screen is {screen_w}×{screen_h} pixels. "
        f"Return coordinates in full screen space. "
        f'Return ONLY JSON: {{"x": <int>, "y": <int>}}. If not found: {{"error": "not found"}}. No other text.'
    )


def parse_coordinates(
    text: str, screen_w: int, screen_h: int,
) -> Tuple[bool, int, int]:
    """Parse x,y coordinates from a vision API JSON response.

    Returns (success, x, y). On failure x and y are 0.
    """
    x_match = re.search(r'"x"\s*:\s*([+-]?\d+(?:\.\d+)?)', text)
    y_match = re.search(r'"y"\s*:\s*([+-]?\d+(?:\.\d+)?)', text)
    if x_match and y_match:
        x = max(0, min(screen_w - 1, int(float(x_match.group(1)))))
        y = max(0, min(screen_h - 1, int(float(y_match.group(1)))))
        return True, x, y
    return False, 0, 0


def find_element(
    screenshot_path: Path,
    target: str,
    screen_w: int,
    screen_h: int,
) -> Dict[str, Any]:
    """Use OpenRouter vision API to find a UI element in a screenshot.

    Args:
        screenshot_path: Path to the screenshot image.
        target: Description of the element to find.
        screen_w: Original screen width in pixels.
        screen_h: Original screen height in pixels.

    Returns:
        Dict with success, coordinates (x, y), cost_usd on success,
        or success=False and error message on failure.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        return {"success": False, "error": "OPENROUTER_API_KEY not set"}
    try:
        from src.models.openrouter_client import OpenRouterClient
    except Exception as e:
        return {"success": False, "error": str(e)}

    target_lower = target.lower()
    is_start = "start" in target_lower and "windows" in target_lower

    prompt = (
        _build_start_button_prompt(screen_w, screen_h)
        if is_start
        else _build_generic_prompt(target, screen_w, screen_h)
    )

    try:
        with open(screenshot_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
    except Exception as e:
        return {"success": False, "error": str(e)}

    try:
        api_client = OpenRouterClient(provider="openrouter")
    except ValueError as e:
        return {"success": False, "error": str(e)}

    response = api_client.generate_with_vision(
        prompt=prompt,
        image_base64=img_b64,
        max_tokens=100,
        temperature=0.2,
    )
    if not response.get("success"):
        return {"success": False, "error": response.get("error", "Vision API failed")}

    text = (response.get("text") or "").strip()
    logger.info("API vision response: %s", text[:300] if text else "(empty)")

    ok, x, y = parse_coordinates(text, screen_w, screen_h)
    if ok:
        return {
            "success": True,
            "coordinates": (x, y),
            "cost_usd": response.get("cost_usd", 0.001),
        }
    return {"success": False, "error": "Could not parse vision API coordinates"}
