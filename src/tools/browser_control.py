"""
Browser automation using Playwright.
Web navigation, form filling, clicking, data extraction via CSS selectors.
Complements local vision (Qwen3-VL) for when selectors are unknown.
"""

import atexit
import logging
import time
import weakref
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        sync_playwright,
    )
except ImportError:
    sync_playwright = None  # type: ignore
    Browser = None  # type: ignore
    BrowserContext = None  # type: ignore
    Page = None  # type: ignore

# Track all live BrowserControl instances so we can clean them up at exit.
# Uses weakrefs to avoid preventing garbage collection.
_live_instances: weakref.WeakSet = weakref.WeakSet()


def _cleanup_all_browsers() -> None:
    """atexit handler: stop every Playwright browser to prevent EPIPE errors."""
    for bc in list(_live_instances):
        try:
            if bc.browser is not None or bc.playwright is not None:
                bc.stop()
        except Exception:
            pass  # Best-effort; we're shutting down


atexit.register(_cleanup_all_browsers)


class BrowserControl:
    """
    Browser automation using Playwright.

    Provides web navigation, form filling, clicking, and data extraction.
    Uses CSS selectors (stable, no vision needed when selector is known).
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self.playwright: Any = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        # Load timeout defaults from rules.yaml (single source of truth)
        try:
            from src.utils.config import get_browser_config
            _cfg = get_browser_config()
            self.default_timeout: int = _cfg["default_timeout_ms"]
            self.nav_timeout: int = _cfg["navigation_timeout_ms"]
        except Exception:
            self.default_timeout = 5000
            self.nav_timeout = 30000
        _live_instances.add(self)
        logger.info("Browser control initialized (headless=%s)", headless)

    def start(self) -> Dict[str, Any]:
        """Start browser session."""
        try:
            if sync_playwright is None:
                return {"success": False, "error": "Playwright not installed. pip install playwright && playwright install chromium"}
            if self.page:
                return {"success": True, "message": "Already running"}
            logger.info("Starting browser...")
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=self.headless)
            self.context = self.browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            self.page = self.context.new_page()
            logger.info("Browser started successfully")
            return {"success": True, "message": "Browser started"}
        except Exception as e:
            logger.error("Failed to start browser: %s", e)
            return {"success": False, "error": str(e)}

    def stop(self) -> Dict[str, Any]:
        """Stop browser session."""
        try:
            if self.page:
                self.page.close()
                self.page = None
            if self.context:
                self.context.close()
                self.context = None
            if self.browser:
                self.browser.close()
                self.browser = None
            if self.playwright:
                self.playwright.stop()
                self.playwright = None
            logger.info("Browser stopped")
            return {"success": True, "message": "Browser stopped"}
        except Exception as e:
            logger.error("Failed to stop browser: %s", e)
            return {"success": False, "error": str(e)}

    def navigate(self, url: str, wait_until: str = "domcontentloaded") -> Dict[str, Any]:
        """Navigate to URL. wait_until: 'load' | 'domcontentloaded' | 'networkidle'."""
        try:
            if not self.page:
                self.start()
            if not self.page:
                return {"success": False, "error": "Browser not started"}
            logger.info("Navigating to %s", url)
            self.page.goto(url, wait_until=wait_until, timeout=self.nav_timeout)
            return {
                "success": True,
                "url": self.page.url,
                "title": self.page.title(),
            }
        except Exception as e:
            logger.error("Navigation failed: %s", e)
            return {"success": False, "error": str(e)}

    def click(self, selector: str, timeout: int = 0) -> Dict[str, Any]:
        """Click element by CSS selector."""
        try:
            if not self.page:
                return {"success": False, "error": "Browser not started"}
            logger.info("Clicking selector: %s", selector)
            self.page.click(selector, timeout=timeout or self.default_timeout)
            return {"success": True, "action": "click", "selector": selector}
        except Exception as e:
            logger.error("Click failed on %s: %s", selector, e)
            return {"success": False, "error": str(e)}

    def fill(self, selector: str, text: str, timeout: int = 0) -> Dict[str, Any]:
        """Fill input field with text."""
        try:
            if not self.page:
                return {"success": False, "error": "Browser not started"}
            logger.info("Filling %s with text", selector)
            self.page.fill(selector, text, timeout=timeout or self.default_timeout)
            return {
                "success": True,
                "action": "fill",
                "selector": selector,
                "text_length": len(text),
            }
        except Exception as e:
            logger.error("Fill failed on %s: %s", selector, e)
            return {"success": False, "error": str(e)}

    def type_text(self, selector: str, text: str, delay: int = 50) -> Dict[str, Any]:
        """Type text character by character (realistic input)."""
        try:
            if not self.page:
                return {"success": False, "error": "Browser not started"}
            logger.info("Typing into %s", selector)
            self.page.locator(selector).press_sequentially(text, delay=delay)
            return {"success": True, "action": "type", "selector": selector}
        except Exception as e:
            logger.error("Type failed on %s: %s", selector, e)
            return {"success": False, "error": str(e)}

    def press_key(self, key: str) -> Dict[str, Any]:
        """Press a keyboard key (e.g. Enter, Tab, Escape)."""
        try:
            if not self.page:
                return {"success": False, "error": "Browser not started"}
            logger.info("Pressing key: %s", key)
            self.page.keyboard.press(key)
            return {"success": True, "action": "press_key", "key": key}
        except Exception as e:
            logger.error("Key press failed: %s", e)
            return {"success": False, "error": str(e)}

    def get_text(self, selector: str, timeout: int = 0) -> Dict[str, Any]:
        """Get text content of element."""
        try:
            if not self.page:
                return {"success": False, "error": "Browser not started"}
            logger.info("Getting text from %s", selector)
            element = self.page.wait_for_selector(selector, timeout=timeout or self.default_timeout)
            text = element.text_content() if element else None
            return {"success": True, "selector": selector, "text": text}
        except Exception as e:
            logger.error("Get text failed on %s: %s", selector, e)
            return {"success": False, "error": str(e)}

    def screenshot(
        self,
        filepath: Optional[Path] = None,
        full_page: bool = False,
    ) -> Dict[str, Any]:
        """Take screenshot of current page."""
        try:
            if not self.page:
                return {"success": False, "error": "Browser not started"}
            logger.info("Taking screenshot (full_page=%s)", full_page)
            if filepath:
                path = Path(filepath)
                path.parent.mkdir(parents=True, exist_ok=True)
                self.page.screenshot(path=str(path), full_page=full_page)
                return {"success": True, "filepath": str(path)}
            screenshot_bytes = self.page.screenshot(full_page=full_page)
            return {
                "success": True,
                "bytes": screenshot_bytes,
                "size": len(screenshot_bytes),
            }
        except Exception as e:
            logger.error("Screenshot failed: %s", e)
            return {"success": False, "error": str(e)}

    def wait_for(self, selector: str, timeout: int = 0) -> Dict[str, Any]:
        """Wait for element to appear."""
        try:
            if not self.page:
                return {"success": False, "error": "Browser not started"}
            logger.info("Waiting for %s", selector)
            self.page.wait_for_selector(selector, timeout=timeout or self.default_timeout)
            return {"success": True, "selector": selector}
        except Exception as e:
            logger.error("Wait failed for %s: %s", selector, e)
            return {"success": False, "error": str(e)}

    def evaluate(self, script: str) -> Dict[str, Any]:
        """Execute JavaScript in page context."""
        try:
            if not self.page:
                return {"success": False, "error": "Browser not started"}
            logger.info("Executing JavaScript")
            result = self.page.evaluate(script)
            return {"success": True, "result": result}
        except Exception as e:
            logger.error("JavaScript execution failed: %s", e)
            return {"success": False, "error": str(e)}

    def get_current_url(self) -> str:
        """Get current page URL."""
        return self.page.url if self.page else ""

    def get_title(self) -> str:
        """Get current page title."""
        return self.page.title() if self.page else ""
