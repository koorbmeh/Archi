"""
Tool registry for Archi: execute actions by name.

MCP-aware (Phase 7): Tools can come from direct registration (legacy) or
MCP servers. On startup, connects to configured MCP servers and merges
their tools with directly-registered ones. Execute() routes through MCP
for MCP-backed tools, falls back to direct execution otherwise.

Image generation stays direct (privacy — NSFW prompts stay local).

Path validation is enforced by SafetyController before execute(); tools
assume they are only invoked when authorized.

Resilience: circuit breakers prevent cascading failures when tools repeatedly fail.
"""

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from src.core.resilience import CircuitBreaker, CircuitBreakerError
except ImportError:
    CircuitBreaker = None
    CircuitBreakerError = Exception


class Tool:
    """Base class for all tools."""

    def __init__(self, name: str, risk_level: str = "L1_LOW") -> None:
        self.name = name
        self.risk_level = risk_level

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the tool action. Return dict with at least 'success' and optionally 'error'."""
        raise NotImplementedError


def _validate_path_security(path: str) -> Optional[str]:
    """Validate that a file path is within the project root.

    Whitelist approach: resolves symlinks via realpath(), then checks the
    canonical path starts with the project root. Rejects everything else.
    Defense-in-depth: PlanExecutor also validates, but this catches
    direct tool_registry.execute() calls.

    Returns None if the path is safe, or an error message if it's not.
    """
    try:
        from src.utils.paths import base_path
        project_root = os.path.realpath(base_path())
        real = os.path.realpath(path)
        if not real.startswith(project_root + os.sep) and real != project_root:
            logger.warning("Path security: blocked '%s' (resolves to %s, outside project %s)", path, real, project_root)
            return f"Path outside project: {real}"
    except Exception as e:
        logger.warning("Path security: validation error for '%s': %s", path, e)
        return f"Path validation failed: {e}"
    return None


def _validate_write_path(path: str) -> Optional[str]:
    """Validate that a write path is within workspace/ (not the codebase).

    Archi should only create/modify files under workspace/ for its deliverables.
    Source code modifications (src/) go through the separate write_source/edit_file
    approval flow in PlanExecutor. This guard prevents create_file and append_file
    from accidentally writing into the codebase, config, or other sensitive areas.

    Returns None if the path is safe, or an error message if it's not.
    """
    # First check it's within the project at all
    basic_check = _validate_path_security(path)
    if basic_check:
        return basic_check
    try:
        from src.utils.paths import base_path
        project_root = os.path.realpath(base_path())
        workspace_dir = os.path.join(project_root, "workspace")
        data_dir = os.path.join(project_root, "data")
        real = os.path.realpath(path)
        # Allow writes to workspace/ (deliverables) and data/ (runtime state)
        if real.startswith(workspace_dir + os.sep) or real.startswith(data_dir + os.sep):
            return None
        logger.warning(
            "Write path restricted: '%s' is outside workspace/ and data/. "
            "Archi can only create files in workspace/.",
            path,
        )
        return (
            f"Cannot write to '{path}' — file creation is restricted to workspace/ "
            f"and data/ directories. Move your target path under workspace/."
        )
    except Exception as e:
        logger.warning("Write path validation error for '%s': %s", path, e)
        return f"Path validation failed: {e}"


class FileReadTool(Tool):
    """Read a file from disk (only called when path already authorized by SafetyController)."""

    def __init__(self) -> None:
        super().__init__("read_file", "L1_LOW")

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = params.get("path")
        if not path:
            return {"success": False, "error": "Missing parameter: path"}
        security_err = _validate_path_security(path)
        if security_err:
            return {"success": False, "error": security_err}
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
    """Write to a file (workspace/ and data/ only)."""

    def __init__(self) -> None:
        super().__init__("create_file", "L2_MEDIUM")

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = params.get("path")
        content = params.get("content", "Test content")
        if not path:
            return {"success": False, "error": "Missing parameter: path"}
        security_err = _validate_write_path(path)
        if security_err:
            return {"success": False, "error": security_err}
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
    """Base for desktop tools: wrap DesktopControl with params dict."""

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
    """Browser tools: wrap BrowserControl with params dict."""

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
            return method(str(selector), timeout=params.get("timeout", 0))
        if self._method == "fill":
            selector = params.get("selector")
            text = params.get("text")
            if not selector or text is None:
                return {"success": False, "error": "Missing parameter: selector or text"}
            return method(str(selector), str(text), timeout=params.get("timeout", 0))
        if self._method == "screenshot":
            filepath = params.get("filepath")
            full_page = params.get("full_page", False)
            return method(filepath=Path(filepath) if filepath else None, full_page=full_page)
        if self._method == "get_text":
            selector = params.get("selector")
            if not selector:
                return {"success": False, "error": "Missing parameter: selector"}
            return method(str(selector), timeout=params.get("timeout", 0))
        return {"success": False, "error": f"Unknown browser method: {self._method}"}


class DesktopClickElementTool(Tool):
    """Vision-based click via ComputerUse (semantic: 'click the start button')."""

    def __init__(self) -> None:
        super().__init__("desktop_click_element", "L3_HIGH")

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        target = params.get("target") or params.get("description")
        if not target:
            return {"success": False, "error": "Missing parameter: target or description"}
        app_name = params.get("app_name", "desktop")
        use_vision = params.get("use_vision", True)
        try:
            from src.tools.computer_use import ComputerUse

            computer = ComputerUse()
            return computer.click_element(
                target=str(target),
                app_name=str(app_name),
                use_vision=bool(use_vision),
            )
        except ImportError as e:
            logger.warning("ComputerUse not available: %s", e)
            return {"success": False, "error": f"Computer control not available: {e}"}


class WebSearchToolWrapper(Tool):
    """Web search via WebSearchTool (free DuckDuckGo)."""

    def __init__(self) -> None:
        super().__init__("web_search", "L1_LOW")

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query") or params.get("q")
        if not query:
            return {"success": False, "error": "Missing parameter: query"}
        max_results = int(params.get("max_results", 5))
        try:
            from src.tools.web_search_tool import WebSearchTool

            search = WebSearchTool()
            results = search.search(str(query), max_results=max_results)
            if not results:
                return {
                    "success": False,
                    "error": f"I couldn't find relevant results for '{query}'. The search service may be unavailable.",
                }
            formatted = search.format_results(results)
            return {
                "success": True,
                "results": results,
                "formatted": formatted,
            }
        except ImportError as e:
            logger.warning("WebSearchTool not available: %s", e)
            return {"success": False, "error": f"Web search not available: {e}"}


# ---------------------------------------------------------------------------
# Async helper for running MCP calls from sync context
# ---------------------------------------------------------------------------

# Dedicated event loop for MCP operations (MCP SDK is async-only).
# This runs in a background thread so sync callers (PlanExecutor, etc.)
# can call MCP tools without blocking the main event loop.
_mcp_loop: Optional[asyncio.AbstractEventLoop] = None
_mcp_thread: Optional[threading.Thread] = None
_mcp_lock = threading.Lock()


def _get_mcp_loop() -> asyncio.AbstractEventLoop:
    """Get or create the background event loop for MCP operations."""
    global _mcp_loop, _mcp_thread
    with _mcp_lock:
        if _mcp_loop is not None and _mcp_loop.is_running():
            return _mcp_loop
        _mcp_loop = asyncio.new_event_loop()
        _mcp_thread = threading.Thread(
            target=_mcp_loop.run_forever,
            name="mcp-event-loop",
            daemon=True,
        )
        _mcp_thread.start()
        return _mcp_loop


def _run_async(coro):
    """Run an async coroutine from sync code using the MCP event loop."""
    loop = _get_mcp_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=60)  # 60s timeout for any MCP operation


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Registry of available tools; execute by action type with circuit breakers.

    MCP-aware: after initialize_mcp(), MCP-provided tools are available
    alongside directly-registered tools. MCP tools are preferred when
    both a direct and MCP version exist (except for image generation
    which always stays direct for privacy).
    """

    # Tools that must NEVER route through MCP (privacy, local-only)
    _DIRECT_ONLY = frozenset({"generate_image"})

    def __init__(self) -> None:
        self.tools: Dict[str, Tool] = {}
        self._circuits: Dict[str, Any] = {}
        self._mcp_client = None       # MCPClientManager instance
        self._mcp_tools: set = set()  # Tool names available via MCP
        self._mcp_initialized = False
        if CircuitBreaker is not None:
            self._circuits["desktop"] = CircuitBreaker(
                failure_threshold=5, recovery_timeout=60
            )
            self._circuits["browser"] = CircuitBreaker(
                failure_threshold=3, recovery_timeout=30
            )
            self._circuits["file"] = CircuitBreaker(
                failure_threshold=10, recovery_timeout=10
            )
            self._circuits["search"] = CircuitBreaker(
                failure_threshold=5, recovery_timeout=60
            )
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register built-in tools (direct execution, used as fallback)."""
        self.register(FileReadTool())
        self.register(FileWriteTool())
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
        try:
            self.register(DesktopClickElementTool())
        except Exception as e:
            logger.debug("Desktop click element not registered: %s", e)
        try:
            self.register(WebSearchToolWrapper())
        except Exception as e:
            logger.debug("Web search not registered: %s", e)
        try:
            from src.tools.image_gen import ImageGenerator

            class _ImageGenTool(Tool):
                def __init__(self):
                    super().__init__("generate_image", "L2_MEDIUM")

                def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
                    prompt = params.get("prompt") or params.get("text", "")
                    if not prompt:
                        return {"success": False, "error": "Missing parameter: prompt"}
                    gen = ImageGenerator()
                    return gen.generate(prompt)

            if ImageGenerator.is_available():
                self.register(_ImageGenTool())
                logger.info("Image generation tool registered")
            else:
                logger.debug("Image generation: no model found (set IMAGE_MODEL_PATH or add SDXL .safetensors to models/)")
        except ImportError:
            logger.debug("Image generation not registered (diffusers not installed)")

    def register(self, tool: Tool) -> None:
        """Register a tool by its name."""
        self.tools[tool.name] = tool
        logger.debug("Registered tool: %s (risk: %s)", tool.name, tool.risk_level)

    # -- MCP integration ---------------------------------------------------

    def initialize_mcp(self) -> None:
        """Connect to configured MCP servers and discover their tools.

        Call once at startup. Non-blocking from the caller's perspective
        (uses background event loop). If MCP SDK is not installed or
        servers fail to start, direct tools are used as fallback.
        """
        if self._mcp_initialized:
            return
        try:
            from src.tools.mcp_client import MCPClientManager
            self._mcp_client = MCPClientManager()
            _run_async(self._mcp_client.initialize())
            mcp_tools = _run_async(self._mcp_client.list_all_tools())
            self._mcp_tools = set(mcp_tools.keys())
            self._mcp_initialized = True
            logger.info(
                "MCP initialized: %d tools from MCP servers (%s)",
                len(self._mcp_tools),
                ", ".join(sorted(self._mcp_tools)[:10]),
            )
        except ImportError:
            logger.info("MCP SDK not installed — using direct tools only")
            self._mcp_initialized = True
        except Exception as e:
            logger.warning("MCP initialization failed (direct tools as fallback): %s", e)
            self._mcp_initialized = True

    def shutdown_mcp(self) -> None:
        """Stop all MCP servers. Call at application shutdown."""
        if self._mcp_client:
            try:
                _run_async(self._mcp_client.shutdown())
            except Exception as e:
                logger.debug("MCP shutdown error: %s", e)
            self._mcp_client = None
            self._mcp_tools.clear()

    def get_all_tool_names(self) -> list:
        """Return all available tool names (direct + MCP)."""
        names = set(self.tools.keys())
        names.update(self._mcp_tools)
        return sorted(names)

    # -- Execution ---------------------------------------------------------

    def _get_circuit(self, action_type: str) -> Optional[Any]:
        """Get circuit breaker for action type, or None if resilience not available."""
        if not self._circuits:
            return None
        if action_type.startswith("desktop_"):
            return self._circuits.get("desktop")
        if action_type.startswith("browser_"):
            return self._circuits.get("browser")
        if action_type in ("create_file", "read_file"):
            return self._circuits.get("file")
        if action_type == "web_search":
            return self._circuits.get("search")
        return self._circuits.get("desktop")

    def _should_use_mcp(self, action_type: str) -> bool:
        """Decide whether to route a tool call through MCP."""
        if action_type in self._DIRECT_ONLY:
            return False
        if not self._mcp_client:
            return False
        return action_type in self._mcp_tools

    def execute(self, action_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool by action type.

        Routing priority:
        1. Direct-only tools (generate_image) → always direct
        2. MCP-backed tools → route through MCP client
        3. Direct tools → local execution with circuit breakers
        4. MCP-only tools (no direct equivalent) → MCP client
        5. Unknown → error

        Returns dict with 'success' and optionally 'error', plus tool-specific fields.
        """
        # Route through MCP if available and appropriate
        if self._should_use_mcp(action_type):
            result = self._execute_via_mcp(action_type, params)
            if result is not None:
                return result
            # MCP call failed — fall through to direct execution
            logger.debug("MCP call failed for %s, falling back to direct", action_type)

        # Direct execution
        tool = self.tools.get(action_type)
        if not tool:
            # Check if it's an MCP-only tool (no direct equivalent)
            if action_type in self._mcp_tools and self._mcp_client:
                result = self._execute_via_mcp(action_type, params)
                if result is not None:
                    return result
            return {"success": False, "error": f"Unknown tool: {action_type}"}

        circuit = self._get_circuit(action_type)

        def _do_execute() -> Dict[str, Any]:
            return tool.execute(params)

        try:
            if circuit is not None:
                return circuit.call(_do_execute)
            return _do_execute()
        except CircuitBreakerError as e:
            logger.warning("Circuit breaker OPEN for %s: %s", action_type, e)
            return {
                "success": False,
                "error": "Service temporarily unavailable (too many failures). Will retry automatically.",
            }
        except Exception as e:
            logger.exception("Tool %s failed: %s", action_type, e)
            return {"success": False, "error": str(e)}

    def _execute_via_mcp(self, action_type: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Execute a tool call through MCP. Returns None on transport failure."""
        try:
            result = _run_async(self._mcp_client.call_tool(action_type, params))
            return result
        except Exception as e:
            logger.warning("MCP execution failed for %s: %s", action_type, e)
            return None


# ── Singleton accessor ───────────────────────────────────────────────
# Avoids re-creating DesktopControl, BrowserControl, ImageGenerator,
# and MCP connections for every PlanExecutor task.
import threading as _threading

_shared_registry: Optional[ToolRegistry] = None
_shared_lock = _threading.Lock()


def get_shared_registry() -> ToolRegistry:
    """Return the shared ToolRegistry singleton, creating it on first call.

    Thread-safe.  MCP is initialized lazily on first access.
    """
    global _shared_registry
    if _shared_registry is not None:
        return _shared_registry
    with _shared_lock:
        if _shared_registry is None:
            _shared_registry = ToolRegistry()
            _shared_registry.initialize_mcp()
    return _shared_registry
