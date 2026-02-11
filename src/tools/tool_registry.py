"""
Tool registry for Archi: execute actions by name.
Gate A: FileReadTool, FileWriteTool. Path validation is enforced by SafetyController
before execute(); tools assume they are only invoked when authorized.
Gate C: desktop_*, browser_navigate, browser_click, browser_fill, browser_screenshot, browser_get_text.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


class Tool:
    """Base class for all tools."""

    def __init__(self, name: str, risk_level: str = "L1_LOW") -> None:
        self.name = name
        self.risk_level = risk_level

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the tool action. Return dict with at least 'success' and optionally 'error'."""
        raise NotImplementedError


class FileReadTool(Tool):
    """Read a file from disk (only called when path already authorized by SafetyController)."""

    def __init__(self) -> None:
        super().__init__("read_file", "L1_LOW")

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = params.get("path")
        if not path:
            return {"success": False, "error": "Missing parameter: path"}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return {
                "success": True,
                "content": content[:500],
                "size": len(content),
            }
        except FileNotFoundError:
            return {"success": False, "error": "File not found"}
        except OSError as e:
            logger.warning("Read failed for %s: %s", path, e)
            return {"success": False, "error": str(e)}


class FileWriteTool(Tool):
    """Write to a file (workspace only; SafetyController validates path before call)."""

    def __init__(self) -> None:
        super().__init__("create_file", "L2_MEDIUM")

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = params.get("path")
        content = params.get("content", "Test content from Gate A")
        if not path:
            return {"success": False, "error": "Missing parameter: path"}
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return {
                "success": True,
                "path": path,
                "bytes_written": len(content),
            }
        except OSError as e:
            logger.warning("Write failed for %s: %s", path, e)
            return {"success": False, "error": str(e)}


class _DesktopTool(Tool):
    """Base for Gate C desktop tools: wrap DesktopControl with params dict."""

    def __init__(self, name: str, risk_level: str, desktop: Any, method: str) -> None:
        super().__init__(name, risk_level)
        self._desktop = desktop
        self._method = method

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        method = getattr(self._desktop, self._method)
        if self._method == "click":
            x = params.get("x")
            y = params.get("y")
            if x is None or y is None:
                return {"success": False, "error": "Missing parameters: x, y"}
            return method(int(x), int(y), button=params.get("button", "left"))
        if self._method == "type_text":
            text = params.get("text")
            if text is None:
                return {"success": False, "error": "Missing parameter: text"}
            return method(str(text))
        if self._method == "hotkey":
            keys = params.get("keys")
            if not keys:
                return {"success": False, "error": "Missing parameter: keys (list of key names)"}
            if isinstance(keys, list):
                return method(*keys)
            return {"success": False, "error": "keys must be a list"}
        if self._method == "screenshot":
            filepath = params.get("filepath")
            return method(filepath=Path(filepath) if filepath else None)
        if self._method == "open_application":
            app_name = params.get("app_name")
            if not app_name:
                return {"success": False, "error": "Missing parameter: app_name"}
            return method(str(app_name))
        return {"success": False, "error": f"Unknown desktop method: {self._method}"}


class _BrowserTool(Tool):
    """Gate C browser tools: wrap BrowserControl with params dict."""

    def __init__(self, name: str, risk_level: str, browser: Any, method: str) -> None:
        super().__init__(name, risk_level)
        self._browser = browser
        self._method = method

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        method = getattr(self._browser, self._method)
        if self._method == "navigate":
            url = params.get("url")
            if not url:
                return {"success": False, "error": "Missing parameter: url"}
            return method(str(url), wait_until=params.get("wait_until", "domcontentloaded"))
        if self._method == "click":
            selector = params.get("selector")
            if not selector:
                return {"success": False, "error": "Missing parameter: selector"}
            return method(str(selector), timeout=params.get("timeout", 5000))
        if self._method == "fill":
            selector = params.get("selector")
            text = params.get("text")
            if not selector or text is None:
                return {"success": False, "error": "Missing parameter: selector or text"}
            return method(str(selector), str(text), timeout=params.get("timeout", 5000))
        if self._method == "screenshot":
            filepath = params.get("filepath")
            full_page = params.get("full_page", False)
            return method(filepath=Path(filepath) if filepath else None, full_page=full_page)
        if self._method == "get_text":
            selector = params.get("selector")
            if not selector:
                return {"success": False, "error": "Missing parameter: selector"}
            return method(str(selector), timeout=params.get("timeout", 5000))
        return {"success": False, "error": f"Unknown browser method: {self._method}"}


class ToolRegistry:
    """Registry of available tools; execute by action type."""

    def __init__(self) -> None:
        self.tools: Dict[str, Tool] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register Gate A tools."""
        self.register(FileReadTool())
        self.register(FileWriteTool())
        # Gate C: desktop automation (optional if pyautogui not installed)
        try:
            from src.tools.desktop_control import DesktopControl

            desktop = DesktopControl()
            self.register(_DesktopTool("desktop_click", "L3_HIGH", desktop, "click"))
            self.register(_DesktopTool("desktop_type", "L2_MEDIUM", desktop, "type_text"))
            self.register(_DesktopTool("desktop_hotkey", "L3_HIGH", desktop, "hotkey"))
            self.register(_DesktopTool("desktop_screenshot", "L1_LOW", desktop, "screenshot"))
            self.register(_DesktopTool("desktop_open", "L2_MEDIUM", desktop, "open_application"))
        except ImportError as e:
            logger.debug("Desktop control not registered (missing deps): %s", e)
        # Gate C Phase 2: browser automation (optional if playwright not installed)
        try:
            from src.tools.browser_control import BrowserControl

            browser = BrowserControl()
            self.register(_BrowserTool("browser_navigate", "L2_MEDIUM", browser, "navigate"))
            self.register(_BrowserTool("browser_click", "L2_MEDIUM", browser, "click"))
            self.register(_BrowserTool("browser_fill", "L2_MEDIUM", browser, "fill"))
            self.register(_BrowserTool("browser_screenshot", "L1_LOW", browser, "screenshot"))
            self.register(_BrowserTool("browser_get_text", "L1_LOW", browser, "get_text"))
            logger.info("Browser tools registered")
        except ImportError as e:
            logger.debug("Browser control not registered (missing deps): %s", e)

    def register(self, tool: Tool) -> None:
        """Register a tool by its name."""
        self.tools[tool.name] = tool
        logger.debug("Registered tool: %s (risk: %s)", tool.name, tool.risk_level)

    def execute(self, action_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool by action type. Returns dict with 'success' and optionally
        'error', plus tool-specific fields.
        """
        tool = self.tools.get(action_type)
        if not tool:
            return {"success": False, "error": f"Unknown tool: {action_type}"}
        try:
            return tool.execute(params)
        except Exception as e:
            logger.exception("Tool %s failed: %s", action_type, e)
            return {"success": False, "error": str(e)}
