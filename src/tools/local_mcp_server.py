"""
Local MCP Server — Wraps Archi's built-in tools as an MCP server.

This is the bridge: existing tools continue to work through the MCP protocol.
The server exposes the same capabilities that tool_registry.py provides, but
through the MCP interface so PlanExecutor can reach them via the MCP client.

Image generation is excluded (privacy — NSFW prompts stay local and are
routed directly, not through MCP).

Run standalone:  python -m src.tools.local_mcp_server
"""

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Ensure project root is on sys.path when run as a module
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _create_server():
    """Create and configure the FastMCP server with all tool registrations."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("archi-local-tools")

    # Cached tool instances to avoid re-importing and re-constructing per call.
    _desktop_cache = None
    _browser_cache = None

    def _get_desktop():
        nonlocal _desktop_cache
        if _desktop_cache is None:
            from src.tools.desktop_control import DesktopControl
            _desktop_cache = DesktopControl()
        return _desktop_cache

    def _get_browser():
        nonlocal _browser_cache
        if _browser_cache is None:
            from src.tools.browser_control import BrowserControl
            _browser_cache = BrowserControl()
        return _browser_cache

    def _safe_json_call(func, unavailable_msg: str):
        """Run *func*, returning JSON. ImportError → unavailable; Exception → error."""
        try:
            return json.dumps(func())
        except ImportError:
            return json.dumps({"success": False, "error": unavailable_msg})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    # -- File operations ---------------------------------------------------

    @mcp.tool()
    def read_file(path: str) -> str:
        """Read a file from disk. Returns JSON with success, content, size."""
        from src.tools.tool_registry import FileReadTool
        tool = FileReadTool()
        result = tool.execute({"path": path})
        return json.dumps(result)

    @mcp.tool()
    def create_file(path: str, content: str = "") -> str:
        """Write a file to disk. Returns JSON with success, path, bytes_written."""
        from src.tools.tool_registry import FileWriteTool
        tool = FileWriteTool()
        result = tool.execute({"path": path, "content": content})
        return json.dumps(result)

    @mcp.tool()
    def list_files(path: str = ".") -> str:
        """List contents of a directory. Returns JSON with entries."""
        def _list():
            from src.utils.paths import base_path
            full_path = os.path.normpath(os.path.join(base_path(), path))
            if not os.path.isdir(full_path):
                return {"success": False, "error": f"Not a directory: {path}"}
            entries = sorted(os.listdir(full_path))
            items = []
            for e in entries[:100]:
                ep = os.path.join(full_path, e)
                items.append({"name": e, "is_dir": os.path.isdir(ep)})
            return {"success": True, "entries": items, "total": len(entries)}
        return _safe_json_call(_list, "File listing not available")

    # -- Web search --------------------------------------------------------

    @mcp.tool()
    def web_search(query: str, max_results: int = 5) -> str:
        """Search DuckDuckGo. Returns JSON with success, results, formatted."""
        def _search():
            from src.tools.web_search_tool import WebSearchTool
            search = WebSearchTool()
            results = search.search(query, max_results=max_results)
            if not results:
                return {"success": False, "error": f"No results for '{query}'"}
            formatted = search.format_results(results)
            return {"success": True, "results": results, "formatted": formatted}
        return _safe_json_call(_search, "Web search not available")

    # -- Desktop automation (optional) ------------------------------------

    @mcp.tool()
    def desktop_click(x: int, y: int, button: str = "left") -> str:
        """Click at screen coordinates. Returns JSON result."""
        return _safe_json_call(
            lambda: _get_desktop().click(x, y, button=button),
            "Desktop control not available")

    @mcp.tool()
    def desktop_type(text: str) -> str:
        """Type text via keyboard. Returns JSON result."""
        return _safe_json_call(
            lambda: _get_desktop().type_text(text),
            "Desktop control not available")

    @mcp.tool()
    def desktop_screenshot(filepath: str = "") -> str:
        """Take a screenshot. Returns JSON result with image path."""
        from pathlib import Path
        return _safe_json_call(
            lambda: _get_desktop().screenshot(filepath=Path(filepath) if filepath else None),
            "Desktop control not available")

    @mcp.tool()
    def desktop_hotkey(keys: str) -> str:
        """Press a keyboard shortcut. keys is comma-separated (e.g. 'ctrl,c'). Returns JSON."""
        key_list = [k.strip() for k in keys.split(",")]
        return _safe_json_call(
            lambda: _get_desktop().hotkey(*key_list),
            "Desktop control not available")

    @mcp.tool()
    def desktop_open(app_name: str) -> str:
        """Open an application by name. Returns JSON result."""
        return _safe_json_call(
            lambda: _get_desktop().open_application(app_name),
            "Desktop control not available")

    # -- Browser automation (optional) ------------------------------------

    @mcp.tool()
    def browser_navigate(url: str, wait_until: str = "domcontentloaded") -> str:
        """Navigate to a URL in the browser. Returns JSON result."""
        return _safe_json_call(
            lambda: _get_browser().navigate(url, wait_until=wait_until),
            "Browser control not available")

    @mcp.tool()
    def browser_click(selector: str, timeout: int = 0) -> str:
        """Click a browser element by CSS selector. Returns JSON result."""
        return _safe_json_call(
            lambda: _get_browser().click(selector, timeout=timeout),
            "Browser control not available")

    @mcp.tool()
    def browser_fill(selector: str, text: str, timeout: int = 0) -> str:
        """Fill a browser form field. Returns JSON result."""
        return _safe_json_call(
            lambda: _get_browser().fill(selector, text, timeout=timeout),
            "Browser control not available")

    @mcp.tool()
    def browser_screenshot(filepath: str = "", full_page: bool = False) -> str:
        """Take a browser screenshot. Returns JSON result."""
        from pathlib import Path
        return _safe_json_call(
            lambda: _get_browser().screenshot(
                filepath=Path(filepath) if filepath else None,
                full_page=full_page),
            "Browser control not available")

    @mcp.tool()
    def browser_get_text(selector: str, timeout: int = 0) -> str:
        """Get text content of a browser element. Returns JSON result."""
        return _safe_json_call(
            lambda: _get_browser().get_text(selector, timeout=timeout),
            "Browser control not available")

    # -- Vision-based desktop click ----------------------------------------

    @mcp.tool()
    def desktop_click_element(target: str, app_name: str = "desktop", use_vision: bool = True) -> str:
        """Click a UI element by description (vision-based). Returns JSON result."""
        def _click():
            from src.tools.computer_use import ComputerUse
            computer = ComputerUse()
            return computer.click_element(
                target=target, app_name=app_name, use_vision=use_vision)
        return _safe_json_call(_click, "Computer control not available")

    return mcp


# -- Entry point -----------------------------------------------------------

if __name__ == "__main__":
    import traceback
    try:
        server = _create_server()
        server.run(transport="stdio")
    except Exception:
        # Write to stderr so the MCP client can log what happened.
        # stdout must stay clean — it's the stdio transport channel.
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
