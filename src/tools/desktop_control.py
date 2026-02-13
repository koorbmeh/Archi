"""
Desktop automation: mouse/keyboard control via pyautogui.
Provides click, type, hotkey, screenshot, open app, etc.
"""

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import pyautogui
except ImportError:
    pyautogui = None  # type: ignore

# Safety settings for pyautogui (set when module is used)
if pyautogui is not None:
    pyautogui.PAUSE = 0.5  # Half second pause between actions
    pyautogui.FAILSAFE = True  # Move mouse to corner to abort


class DesktopControl:
    """
    Desktop automation tool using pyautogui.

    Provides mouse/keyboard control for Windows applications.
    """

    def __init__(self) -> None:
        if pyautogui is None:
            raise ImportError("pyautogui is required for desktop control. pip install pyautogui")
        self.screen_size = pyautogui.size()
        logger.info("Desktop control initialized (screen: %s)", self.screen_size)

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.1,
    ) -> Dict[str, Any]:
        """
        Click at specific coordinates.

        Args:
            x: X coordinate
            y: Y coordinate
            button: "left", "right", or "middle"
            clicks: Number of clicks (1 for single, 2 for double)
            interval: Time between clicks

        Returns:
            Dict with success status
        """
        try:
            logger.info("Clicking at (%s, %s) with %s button", x, y, button)
            pyautogui.click(x, y, clicks=clicks, interval=interval, button=button)
            return {
                "success": True,
                "action": "click",
                "location": (x, y),
                "button": button,
            }
        except Exception as e:
            logger.error("Click failed: %s", e)
            return {"success": False, "error": str(e)}

    def type_text(self, text: str, interval: float = 0.05) -> Dict[str, Any]:
        """
        Type text as if from keyboard.

        Args:
            text: Text to type
            interval: Delay between keystrokes (seconds)

        Returns:
            Dict with success status
        """
        try:
            logger.info("Typing text: %s...", text[:50])
            pyautogui.write(text, interval=interval)
            return {
                "success": True,
                "action": "type",
                "text_length": len(text),
            }
        except Exception as e:
            logger.error("Type failed: %s", e)
            return {"success": False, "error": str(e)}

    def press_key(self, key: str, presses: int = 1) -> Dict[str, Any]:
        """
        Press a key or key combination.

        Args:
            key: Key name (e.g., 'enter', 'tab', 'ctrl')
            presses: Number of times to press

        Returns:
            Dict with success status
        """
        try:
            logger.info("Pressing key: %s (%sx)", key, presses)
            pyautogui.press(key, presses=presses)
            return {
                "success": True,
                "action": "press",
                "key": key,
            }
        except Exception as e:
            logger.error("Key press failed: %s", e)
            return {"success": False, "error": str(e)}

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        """
        Press a hotkey combination.

        Args:
            *keys: Keys to press together (e.g., 'ctrl', 'c')

        Returns:
            Dict with success status
        """
        try:
            logger.info("Pressing hotkey: %s", "+".join(keys))
            pyautogui.hotkey(*keys)
            return {
                "success": True,
                "action": "hotkey",
                "keys": list(keys),
            }
        except Exception as e:
            logger.error("Hotkey failed: %s", e)
            return {"success": False, "error": str(e)}

    def move_mouse(self, x: int, y: int, duration: float = 0.5) -> Dict[str, Any]:
        """
        Move mouse to coordinates smoothly.

        Args:
            x: Target X coordinate
            y: Target Y coordinate
            duration: Time to move (seconds)

        Returns:
            Dict with success status
        """
        try:
            logger.debug("Moving mouse to (%s, %s)", x, y)
            pyautogui.moveTo(x, y, duration=duration)
            return {
                "success": True,
                "action": "move",
                "location": (x, y),
            }
        except Exception as e:
            logger.error("Mouse move failed: %s", e)
            return {"success": False, "error": str(e)}

    def screenshot(
        self,
        region: Optional[Tuple[int, int, int, int]] = None,
        filepath: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Take a screenshot.

        Args:
            region: (x, y, width, height) to capture, or None for full screen
            filepath: Where to save, or None to return image object

        Returns:
            Dict with success, filepath, and/or image data
        """
        try:
            logger.info("Taking screenshot (region: %s)", region)
            if region:
                img = pyautogui.screenshot(region=region)
            else:
                img = pyautogui.screenshot()
            if filepath:
                path = Path(filepath)
                path.parent.mkdir(parents=True, exist_ok=True)
                img.save(path)
                return {
                    "success": True,
                    "action": "screenshot",
                    "filepath": str(path),
                    "size": img.size,
                }
            return {
                "success": True,
                "action": "screenshot",
                "image": img,
                "size": img.size,
            }
        except Exception as e:
            logger.error("Screenshot failed: %s", e)
            return {"success": False, "error": str(e)}

    def get_mouse_position(self) -> Tuple[int, int]:
        """Get current mouse position."""
        return pyautogui.position()

    def open_application(self, app_name: str) -> Dict[str, Any]:
        """
        Open an application using Windows start command.

        Args:
            app_name: Application name or path

        Returns:
            Dict with success status
        """
        import shlex
        import subprocess

        try:
            logger.info("Opening application: %s", app_name)
            # Allowlist of safe system apps that can be launched directly
            safe_apps = {"notepad", "calc", "mspaint", "explorer", "cmd", "powershell"}
            if app_name.lower() in safe_apps:
                subprocess.Popen([app_name])
            else:
                # Use os.startfile on Windows (no shell injection risk) or
                # subprocess with shell=False via cmd /c start for non-shell paths
                import os
                if hasattr(os, "startfile"):
                    os.startfile(app_name)
                else:
                    subprocess.Popen(["xdg-open", app_name])
            time.sleep(1)
            return {
                "success": True,
                "action": "open_app",
                "app": app_name,
            }
        except Exception as e:
            logger.error("Failed to open %s: %s", app_name, e)
            return {"success": False, "error": str(e)}

    def scroll(self, clicks: int) -> Dict[str, Any]:
        """
        Scroll mouse wheel.

        Args:
            clicks: Number of clicks (positive=up, negative=down)

        Returns:
            Dict with success status
        """
        try:
            logger.debug("Scrolling %s clicks", clicks)
            pyautogui.scroll(clicks)
            return {
                "success": True,
                "action": "scroll",
                "clicks": clicks,
            }
        except Exception as e:
            logger.error("Scroll failed: %s", e)
            return {"success": False, "error": str(e)}
