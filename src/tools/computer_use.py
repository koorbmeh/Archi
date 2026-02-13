"""
Computer Use Orchestrator.

Intelligently routes UI tasks through the best available method:
1. UI Memory (cached locations) - fastest, $0
2. Browser selectors (for web) - when known
3. Local Vision (Qwen3-VL) - visual grounding when cache misses
4. OpenRouter Vision (API fallback) - when local vision fails

Smart routing minimizes cost and maximizes reliability.
"""

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional
from src.utils.paths import base_path as _base_path

logger = logging.getLogger(__name__)





class ComputerUse:
    """
    Orchestrates computer control using multiple methods:
    1. UI Memory (cached locations)
    2. Browser selectors (for web)
    3. Local Vision (Qwen3-VL) - free
    4. OpenRouter Vision (API) - fallback when local fails

    Smart routing minimizes cost and maximizes reliability.
    """

    def __init__(self) -> None:
        base = _base_path()
        self._base = base
        self._desktop = None
        self._browser = None
        self._ui_memory = None
        self._local_model = None
        self._data_dir = Path(base) / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Computer Use orchestrator initialized")

    def _get_desktop(self):
        if self._desktop is None:
            from .desktop_control import DesktopControl
            self._desktop = DesktopControl()
        return self._desktop

    def _get_browser(self):
        if self._browser is None:
            from .browser_control import BrowserControl
            self._browser = BrowserControl(headless=True)
        return self._browser

    def _get_ui_memory(self):
        if self._ui_memory is None:
            from .ui_memory import UIMemory
            db_path = self._data_dir / "ui_memory.db"
            self._ui_memory = UIMemory(db_path=db_path)
        return self._ui_memory

    def _get_local_model(self):
        if self._local_model is None:
            from src.models.local_model import LocalModel
            self._local_model = LocalModel()
        return self._local_model

    def _expand_target_description(self, target: str) -> str:
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

    def _find_element_with_vision(
        self, screenshot_path: Path, target: str, img_width: int, img_height: int
    ) -> Dict[str, Any]:
        """
        Use local vision model to find element coordinates.
        Prompts for JSON {x, y} in the given image pixel space.
        """
        try:
            model = self._get_local_model()
            if not model.has_vision:
                return {"success": False, "error": "Vision model not available"}
        except Exception as e:
            return {"success": False, "error": str(e)}

        target_lower = target.lower()
        is_start = "start" in target_lower and "windows" in target_lower

        if is_start:
            # Same detailed prompt that worked for Grok (center taskbar, NOT weather widget)
            x_min_img = int(img_width * 0.25)
            x_max_img = int(img_width * 0.45)
            y_min_img = img_height - 80  # Taskbar at bottom
            y_max_img = img_height - 5
            prompt = (
                f"You are analyzing a Windows 11 desktop screenshot ({img_width}×{img_height} pixels).\n\n"
                "CRITICAL: Find the Windows Start button - a BLUE SQUARE with 4 smaller squares inside (Windows logo).\n"
                "It is in the TASKBAR at the BOTTOM of the screen. Ignore Windows logos in window title bars (top).\n"
                "It is NOT the weather widget (sun/cloud on far left). NOT the search icon.\n"
                f"On Windows 11 centered taskbar: X between {x_min_img}-{x_max_img}, Y between {y_min_img}-{y_max_img} (bottom).\n\n"
                "Find the blue 4-square logo IN THE TASKBAR (bottom). Return its center coordinates.\n"
                f"Return ONLY JSON: {{\"x\": <int>, \"y\": <int>}}. Y must be {y_min_img}-{y_max_img} (bottom). No other text."
            )
        else:
            expanded = self._expand_target_description(target)
            prompt = (
                f"Look at this screenshot. Find: {expanded}. "
                f"This image is {img_width}×{img_height} pixels. "
                f"Return ONLY JSON: {{\"x\": <int>, \"y\": <int>}} (center of element). No other text."
            )

        result = model.chat_with_image(
            prompt,
            str(screenshot_path),
            max_tokens=150,
            temperature=0.1,
        )

        if not result.get("success"):
            return {"success": False, "error": result.get("error", "vision failed")}

        text = (result.get("text") or "").strip()
        logger.info("Vision raw response: %s", text[:400] if text else "(empty)")
        # Extract coordinates from response (model might return JSON or mixed text)
        x_match = re.search(r'"x"\s*:\s*(\d+)', text)
        y_match = re.search(r'"y"\s*:\s*(\d+)', text)
        if x_match and y_match:
            x, y = int(x_match.group(1)), int(y_match.group(1))
            logger.info("Vision detected coordinates (image space): (%s, %s)", x, y)
            return {"success": True, "coordinates": (x, y)}
        # Try parsing full JSON object
        json_match = re.search(r'\{[^{}]*\}', text)
        if json_match:
            try:
                data = json.loads(json_match.group())
                if "error" in data:
                    return {"success": False, "error": str(data["error"])}
                if "x" in data and "y" in data:
                    return {
                        "success": True,
                        "coordinates": (int(data["x"]), int(data["y"])),
                    }
            except (json.JSONDecodeError, ValueError, TypeError, KeyError):
                pass
        return {
            "success": False,
            "error": "Could not parse coordinates from vision response",
            "answer": text[:200],
        }

    def _find_element_with_grok(
        self,
        screenshot_path: Path,
        target: str,
        screen_w: int,
        screen_h: int,
    ) -> Dict[str, Any]:
        """
        Use OpenRouter vision API to find element (paid fallback when local vision fails).
        """
        if not os.environ.get("OPENROUTER_API_KEY"):
            return {"success": False, "error": "OPENROUTER_API_KEY not set"}
        try:
            from src.models.openrouter_client import OpenRouterClient
        except Exception as e:
            return {"success": False, "error": str(e)}

        target_lower = target.lower()
        is_start = "start" in target_lower and "windows" in target_lower

        if is_start:
            # Windows Start button: be VERY specific - NOT weather widget, NOT title bar logo
            x_min, x_max = int(screen_w * 0.25), int(screen_w * 0.45)
            y_min, y_max = screen_h - 80, screen_h - 10
            prompt = (
                f"You are analyzing a Windows 11 desktop screenshot ({screen_w}×{screen_h} pixels).\n\n"
                "CRITICAL: Find the Windows Start button - a BLUE SQUARE with 4 smaller squares inside (Windows logo).\n"
                "It is in the TASKBAR at the BOTTOM of the screen. Ignore Windows logos in window title bars (top).\n"
                "It is NOT the weather widget (sun/cloud on far left). NOT the search icon.\n"
                f"Coordinates: X between {x_min}-{x_max} (center), Y between {y_min}-{y_max} (bottom taskbar).\n\n"
                "Find the blue 4-square logo IN THE TASKBAR (bottom). Return its center coordinates.\n"
                f"Return ONLY JSON: {{\"x\": <int>, \"y\": <int>}}. Y must be {y_min}-{y_max}. No other text."
            )
        else:
            expanded = self._expand_target_description(target)
            prompt = (
                f"Look at this screenshot. Find the UI element: {expanded}. "
                f"The original screen is {screen_w}×{screen_h} pixels. "
                f"Return coordinates in full screen space. "
                f"Return ONLY JSON: {{\"x\": <int>, \"y\": <int>}}. If not found: {{\"error\": \"not found\"}}. No other text."
            )
        try:
            with open(screenshot_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
        except Exception as e:
            return {"success": False, "error": str(e)}

        try:
            api_client = OpenRouterClient()
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
        x_match = re.search(r'"x"\s*:\s*(\d+)', text)
        y_match = re.search(r'"y"\s*:\s*(\d+)', text)
        if x_match and y_match:
            x = max(0, min(screen_w - 1, int(x_match.group(1))))
            y = max(0, min(screen_h - 1, int(y_match.group(1))))
            return {
                "success": True,
                "coordinates": (x, y),
                "cost_usd": response.get("cost_usd", 0.001),
            }
        return {"success": False, "error": "Could not parse vision API coordinates"}

    def click_element(
        self,
        target: str,
        app_name: str = "desktop",
        use_vision: bool = True,
    ) -> Dict[str, Any]:
        """
        Click a UI element intelligently.

        Args:
            target: What to click ("login button", "Windows Start button", etc.)
            app_name: Application context
            use_vision: Allow vision if needed

        Returns:
            Dict with success status, method used, and cost_usd
        """
        logger.info("Attempting to click: %s in %s", target, app_name)

        ui_memory = self._get_ui_memory()

        # Step 1: Check UI Memory cache
        cached = ui_memory.get_element(app_name, target)
        if cached:
            logger.info("Cache HIT for %s", target)

            if cached["type"] == "coordinate":
                loc = cached["location"]
                x, y = loc.get("x"), loc.get("y")
                if x is not None and y is not None:
                    desktop = self._get_desktop()
                    result = desktop.click(int(x), int(y))
                    if result.get("success"):
                        ui_memory.record_success(app_name, target)
                        return {
                            **result,
                            "method": "cached_coordinate",
                            "cost_usd": 0.0,
                        }
                    ui_memory.record_failure(app_name, target)

            elif cached["type"] == "selector":
                loc = cached["location"]
                selector = loc.get("selector") if isinstance(loc, dict) else None
                if selector:
                    browser = self._get_browser()
                    if not browser.page:
                        browser.start()
                    result = browser.click(selector)
                    if result.get("success"):
                        ui_memory.record_success(app_name, target)
                        return {
                            **result,
                            "method": "cached_selector",
                            "cost_usd": 0.0,
                        }
                    ui_memory.record_failure(app_name, target)

        # Step 2: Known positions (bypass vision for common elements)
        desktop = self._get_desktop()
        screen_w, screen_h = desktop.screen_size[0], desktop.screen_size[1]
        target_lower = target.lower()
        if "start" in target_lower and "windows" in target_lower:
            env_x = os.environ.get("START_BUTTON_X")
            try:
                val = float(env_x) if env_x else 0.33
                x = int(screen_w * val) if val <= 1 else int(val)
                y = screen_h - 45
                ui_memory.store_element(
                    app_name=app_name,
                    element_name=target,
                    element_type="coordinate",
                    location={"x": x, "y": y},
                    confidence=1.0,
                )
                result = desktop.click(x, y)
                logger.info("Known position: Start at (%s, %s), stored in cache", x, y)
                return {
                    **result,
                    "method": "known_position",
                    "cost_usd": 0.0,
                }
            except (ValueError, TypeError):
                pass

        # Step 3: Use vision if allowed
        if use_vision:
            logger.info("Cache MISS for %s, using vision", target)

            screenshot_path = self._data_dir / "temp_screenshot.png"
            screenshot_result = desktop.screenshot(filepath=screenshot_path)

            if not screenshot_result.get("success"):
                return {"success": False, "error": "Failed to take screenshot"}

            # Resize to avoid token overflow (768 for local vision)
            orig_w, orig_h = 0, 0
            resized_w, resized_h = 0, 0
            original_screenshot_path = self._data_dir / "temp_screenshot_original.png"
            try:
                from PIL import Image
                img = Image.open(screenshot_path)
                orig_w, orig_h = img.size
                img.save(original_screenshot_path, "PNG")  # Keep full-res for Grok
                img.thumbnail((768, 768))
                resized_w, resized_h = img.size
                img.save(screenshot_path, "PNG")
            except Exception as e:
                logger.warning("Screenshot resize failed: %s", e)

            vision_result = self._find_element_with_vision(
                screenshot_path, target, resized_w, resized_h
            )

            screen_w = orig_w or desktop.screen_size[0]
            screen_h = orig_h or desktop.screen_size[1]
            use_grok = False

            # Escalate to Grok when local vision fails
            if not vision_result.get("success") or "coordinates" not in vision_result:
                logger.warning("Local vision failed, escalating to Grok vision")
                use_grok = True
            elif vision_result.get("success") and "coordinates" in vision_result:
                # Scale and validate
                x, y = vision_result["coordinates"]
                if orig_w > 0 and orig_h > 0 and resized_w > 0 and resized_h > 0:
                    if x < resized_w and y < resized_h:
                        x = int(x * orig_w / resized_w)
                        y = int(y * orig_h / resized_h)
                    x = max(0, min(screen_w - 1, x))
                    y = max(0, min(screen_h - 1, y))

                # Start button validation: at 150% scale, expect center area (25-45% from left)
                target_lower = target.lower()
                if "start" in target_lower and "windows" in target_lower:
                    expected_x_min = int(screen_w * 0.25)
                    expected_x_max = int(screen_w * 0.45)
                    expected_y_min = screen_h - 80
                    expected_y_max = screen_h - 10
                    in_range = (
                        expected_x_min <= x <= expected_x_max
                        and expected_y_min <= y <= expected_y_max
                    )
                    logger.info(
                        "Start button validation: (%s, %s) in X[%s-%s] Y[%s-%s] = %s",
                        x, y, expected_x_min, expected_x_max, expected_y_min, expected_y_max, in_range,
                    )
                    if not in_range:
                        logger.warning(
                            "Local vision coords out of range, escalating to Grok"
                        )
                        use_grok = True

            if use_grok:
                grok_img_path = original_screenshot_path if original_screenshot_path.exists() else screenshot_path
                grok_result = self._find_element_with_grok(
                    grok_img_path, target, screen_w, screen_h
                )
                if grok_result.get("success") and "coordinates" in grok_result:
                    gx, gy = grok_result["coordinates"]
                    gx = max(0, min(screen_w - 1, gx))
                    gy = max(0, min(screen_h - 1, gy))
                    # Validate Grok result for Start button (often returns weather widget at x~120)
                    if "start" in target.lower() and "windows" in target.lower():
                        if gx < int(screen_w * 0.22):
                            logger.warning(
                                "Grok returned x=%s (likely weather widget), using known fallback",
                                gx,
                            )
                            grok_result = {"success": False}
                    if grok_result.get("success"):
                        vision_result = grok_result
                        x, y = gx, gy
                if not (grok_result.get("success") and "coordinates" in grok_result):
                    # Fallback: Start button uses known position (150% scale: ~0.33)
                    if "start" in target.lower() and "windows" in target.lower():
                        env_x = os.environ.get("START_BUTTON_X")
                        try:
                            val = float(env_x) if env_x else 0.33
                            x = int(screen_w * val) if val <= 1 else int(val)
                            y = screen_h - 45
                            vision_result = {
                                "success": True,
                                "coordinates": (x, y),
                                "cost_usd": 0.0,
                            }
                            logger.info(
                                "Using known Start position: (%s, %s) (Grok found wrong element)",
                                x, y,
                            )
                        except (ValueError, TypeError):
                            return {"success": False, "error": grok_result.get("error", "Grok failed")}
                    else:
                        return {"success": False, "error": grok_result.get("error", "Grok vision could not locate element")}
            else:
                x, y = vision_result["coordinates"]
                if orig_w > 0 and orig_h > 0 and resized_w > 0 and resized_h > 0:
                    if x <= resized_w and y <= resized_h:
                        x = int(x * orig_w / resized_w)
                        y = int(y * orig_h / resized_h)
                x = max(0, min(screen_w - 1, x))
                y = max(0, min(screen_h - 1, y))

            if vision_result.get("success") and "coordinates" in vision_result:
                logger.info("Screen coords: (%s, %s)", x, y)

                # Store in cache for next time (screen coordinates)
                screenshot_hash = ui_memory.hash_screenshot(screenshot_path)
                ui_memory.store_element(
                    app_name=app_name,
                    element_name=target,
                    element_type="coordinate",
                    location={"x": x, "y": y},
                    screenshot_hash=screenshot_hash,
                    confidence=0.8,
                )

                # Click
                result = desktop.click(x, y)
                cost = vision_result.get("cost_usd", 0.0)
                method = "grok_vision" if cost > 0 else "vision"
                return {
                    **result,
                    "method": method,
                    "cost_usd": cost,
                }
            return {
                "success": False,
                "error": vision_result.get("error", "Vision could not locate element"),
                "vision_response": vision_result.get("answer", ""),
            }

        return {
            "success": False,
            "error": "Element not found in cache and vision disabled",
        }

    def type_in_element(
        self,
        target: str,
        text: str,
        app_name: str = "desktop",
    ) -> Dict[str, Any]:
        """Type text into an element (click first to focus, then type)."""
        click_result = self.click_element(target, app_name)
        if not click_result.get("success"):
            return click_result

        desktop = self._get_desktop()
        type_result = desktop.type_text(text)
        return {
            **type_result,
            "click_method": click_result.get("method"),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get usage statistics."""
        return {
            "vision_loaded": self._local_model is not None,
            "browser_running": (
                self._browser is not None and self._browser.page is not None
            ),
        }

    @property
    def desktop(self):
        """Access desktop control (for tests / convenience)."""
        return self._get_desktop()

    @property
    def browser(self):
        """Access browser control (for tests / convenience)."""
        return self._get_browser()
