"""
Computer Use Orchestrator.

Intelligently routes UI tasks through the best available method:
1. UI Memory (cached locations) - fastest, $0
2. Known positions (hardcoded for common elements) - $0
3. Vision API (via ImageAnalyzer) - when cache misses

Smart routing minimizes cost and maximizes reliability.
Vision API logic extracted to image_analyzer.py (session 75).
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from src.tools.image_analyzer import find_element as _find_element_with_api
from src.utils.paths import base_path as _base_path

logger = logging.getLogger(__name__)


class ComputerUse:
    """
    Orchestrates computer control using multiple methods:
    1. UI Memory (cached locations)
    2. Known positions (common elements)
    3. Vision API via ImageAnalyzer - fallback when cache misses

    Smart routing minimizes cost and maximizes reliability.
    """

    def __init__(self) -> None:
        base = _base_path()
        self._base = base
        self._desktop = None
        self._browser = None
        self._ui_memory = None
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

        # Step 3: Use vision API if allowed
        if use_vision:
            return self._click_via_vision(target, app_name, desktop, screen_w, screen_h)

        return {
            "success": False,
            "error": "Element not found in cache and vision disabled",
        }

    def _click_via_vision(
        self,
        target: str,
        app_name: str,
        desktop: Any,
        screen_w: int,
        screen_h: int,
    ) -> Dict[str, Any]:
        """Take screenshot, find element via vision API, click it, cache result."""
        logger.info("Cache MISS for %s, using vision API", target)
        ui_memory = self._get_ui_memory()

        # Use unique filenames to avoid collision under concurrency
        _fd, _tmp = tempfile.mkstemp(suffix=".png", dir=str(self._data_dir))
        os.close(_fd)
        screenshot_path = Path(_tmp)
        screenshot_result = desktop.screenshot(filepath=screenshot_path)

        if not screenshot_result.get("success"):
            screenshot_path.unlink(missing_ok=True)
            return {"success": False, "error": "Failed to take screenshot"}

        # Resize for token efficiency
        orig_w, orig_h = 0, 0
        _fd2, _tmp2 = tempfile.mkstemp(suffix="_orig.png", dir=str(self._data_dir))
        os.close(_fd2)
        original_screenshot_path = Path(_tmp2)
        try:
            from PIL import Image
            with Image.open(screenshot_path) as img:
                orig_w, orig_h = img.size
                img.save(original_screenshot_path, "PNG")
                img.thumbnail((768, 768))
                img.save(screenshot_path, "PNG")
        except Exception as e:
            logger.warning("Screenshot resize failed: %s", e)

        screen_w = orig_w or screen_w
        screen_h = orig_h or screen_h

        api_img_path = original_screenshot_path if original_screenshot_path.exists() else screenshot_path
        api_result = _find_element_with_api(
            api_img_path, target, screen_w, screen_h
        )

        if not (api_result.get("success") and "coordinates" in api_result):
            screenshot_path.unlink(missing_ok=True)
            original_screenshot_path.unlink(missing_ok=True)
            return {"success": False, "error": api_result.get("error", "Vision could not locate element")}

        gx, gy = api_result["coordinates"]
        gx = max(0, min(screen_w - 1, gx))
        gy = max(0, min(screen_h - 1, gy))

        # Validate API result for Start button (often returns weather widget at x~120)
        if "start" in target.lower() and "windows" in target.lower():
            if gx < int(screen_w * 0.22):
                logger.warning(
                    "API returned x=%s (likely weather widget), using known fallback", gx,
                )
                screenshot_path.unlink(missing_ok=True)
                original_screenshot_path.unlink(missing_ok=True)
                return self._start_button_fallback(screen_w, screen_h, desktop)

        x, y = gx, gy
        logger.info("Screen coords: (%s, %s)", x, y)

        # Store in cache for next time
        screenshot_hash = ui_memory.hash_screenshot(screenshot_path)
        ui_memory.store_element(
            app_name=app_name,
            element_name=target,
            element_type="coordinate",
            location={"x": x, "y": y},
            screenshot_hash=screenshot_hash,
            confidence=0.8,
        )

        result = desktop.click(x, y)
        # Clean up temp screenshot files
        screenshot_path.unlink(missing_ok=True)
        original_screenshot_path.unlink(missing_ok=True)
        return {
            **result,
            "method": "api_vision",
            "cost_usd": api_result.get("cost_usd", 0.0),
        }

    def _start_button_fallback(
        self, screen_w: int, screen_h: int, desktop: Any,
    ) -> Dict[str, Any]:
        """Fall back to known Start button position when vision fails."""
        env_x = os.environ.get("START_BUTTON_X")
        try:
            val = float(env_x) if env_x else 0.33
            x = int(screen_w * val) if val <= 1 else int(val)
            y = screen_h - 45
            logger.info("Using known Start position: (%s, %s) (API found wrong element)", x, y)
            result = desktop.click(x, y)
            return {**result, "method": "known_position_fallback", "cost_usd": 0.0}
        except (ValueError, TypeError):
            return {"success": False, "error": "Start button fallback failed"}

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
