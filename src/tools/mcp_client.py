"""
MCP Client — Connects Archi to MCP servers for tool execution.

Manages server lifecycle: start on first use, stop after idle timeout.
Each server runs as a subprocess via stdio transport.

Usage:
    client = MCPClientManager()
    await client.initialize()               # Load config, no servers started yet
    result = await client.call_tool("web_search", {"query": "test"})
    tools = await client.list_all_tools()    # {tool_name: MCPToolInfo}
    await client.shutdown()                  # Stop all servers
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from src.utils.paths import base_path as _base_path

logger = logging.getLogger(__name__)


@dataclass
class MCPToolInfo:
    """Metadata for an MCP-provided tool."""
    name: str
    description: str
    server_name: str
    input_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ServerConfig:
    """Parsed config for one MCP server."""
    name: str
    command: str
    args: List[str]
    env: Dict[str, str]
    idle_timeout: int
    enabled: bool
    exclude_tools: List[str] = field(default_factory=list)


@dataclass
class ServerConnection:
    """Live connection state for a running MCP server."""
    config: ServerConfig
    session: Any = None           # mcp.client.session.ClientSession
    read_stream: Any = None
    write_stream: Any = None
    context_manager: Any = None   # The stdio_client context manager
    session_cm: Any = None        # The ClientSession context manager
    tools: Dict[str, MCPToolInfo] = field(default_factory=dict)
    last_used: float = 0.0
    starting: bool = False
    _lock: Optional[asyncio.Lock] = field(default=None)


class MCPClientManager:
    """Manages connections to multiple MCP servers."""

    def __init__(self) -> None:
        self._servers: Dict[str, ServerConfig] = {}
        self._connections: Dict[str, ServerConnection] = {}
        self._tool_map: Dict[str, str] = {}  # tool_name → server_name
        self._idle_task: Optional[asyncio.Task] = None
        self._shutdown = False
        # Serialises server start/stop so the idle monitor can't tear down
        # a connection while call_tool() is establishing or using it.
        self._lifecycle_lock: Optional[asyncio.Lock] = None

    def _get_lifecycle_lock(self) -> asyncio.Lock:
        """Lazy-init the lifecycle lock inside the running event loop."""
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        return self._lifecycle_lock

    # -- Public API --------------------------------------------------------

    async def initialize(self) -> None:
        """Load server config. Does NOT start any servers yet (on-demand)."""
        self._servers = _load_server_configs()
        logger.info(
            "MCP client: loaded %d server configs (%s)",
            len(self._servers),
            ", ".join(s for s, c in self._servers.items() if c.enabled),
        )
        # Start idle monitor
        self._idle_task = asyncio.create_task(self._idle_monitor())

    async def list_all_tools(self) -> Dict[str, MCPToolInfo]:
        """Discover tools from all enabled servers. Starts servers as needed."""
        all_tools: Dict[str, MCPToolInfo] = {}
        for name, config in self._servers.items():
            if not config.enabled:
                continue
            try:
                conn = await self._ensure_connection(name)
                all_tools.update(conn.tools)
            except Exception as e:
                logger.warning("MCP: failed to discover tools from '%s': %s", name, e)
        self._rebuild_tool_map(all_tools)
        return all_tools

    async def call_tool(
        self, tool_name: str, arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Call a tool by name. Starts the owning server if not running.

        Returns dict with 'success' and tool-specific fields.
        Falls back gracefully if the server can't be reached.
        """
        server_name = self._tool_map.get(tool_name)
        if not server_name:
            return {"success": False, "error": f"No MCP server provides tool '{tool_name}'"}

        try:
            async with self._get_lifecycle_lock():
                conn = await self._ensure_connection(server_name)
                conn.last_used = time.monotonic()

            result = await conn.session.call_tool(tool_name, arguments)

            # Parse MCP CallToolResult into our standard dict format
            return _parse_tool_result(result)

        except Exception as e:
            logger.error("MCP tool call failed (%s on %s): %s", tool_name, server_name, e)
            return {"success": False, "error": f"MCP call failed: {e}"}

    def get_server_for_tool(self, tool_name: str) -> Optional[str]:
        """Return which server provides a given tool, or None."""
        return self._tool_map.get(tool_name)

    async def shutdown(self) -> None:
        """Stop all running MCP servers and clean up."""
        self._shutdown = True
        if self._idle_task:
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass

        for name in list(self._connections):
            await self._stop_server(name)
        logger.info("MCP client: shutdown complete")

    # -- Server lifecycle --------------------------------------------------

    async def _ensure_connection(self, server_name: str) -> ServerConnection:
        """Start a server if not running, return the live connection."""
        if server_name in self._connections:
            conn = self._connections[server_name]
            if conn.session is not None:
                conn.last_used = time.monotonic()
                return conn

        config = self._servers.get(server_name)
        if not config:
            raise ValueError(f"Unknown MCP server: {server_name}")

        conn = self._connections.get(server_name) or ServerConnection(config=config)
        self._connections[server_name] = conn

        if conn._lock is None:
            conn._lock = asyncio.Lock()
        async with conn._lock:
            # Double-check after acquiring lock
            if conn.session is not None:
                return conn
            await self._start_server(server_name, conn)

        return conn

    async def _start_server(self, name: str, conn: ServerConnection) -> None:
        """Start an MCP server subprocess and establish session."""
        config = conn.config
        logger.info("MCP: starting server '%s' (%s %s)", name, config.command, config.args)

        try:
            import sys as _sys
            from mcp.client.stdio import StdioServerParameters, stdio_client
            from mcp.client.session import ClientSession

            # Resolve env vars (support ${VAR} syntax from config)
            resolved_env = _resolve_env(config.env)

            # Resolve "python" to sys.executable for reliability on Windows
            # where bare "python" may resolve to the wrong interpreter or
            # to the Windows Store stub.
            command = config.command
            if command in ("python", "python3"):
                command = _sys.executable

            # Build env — always include PYTHONUTF8 for Windows encoding
            merged_env = {**os.environ, **(resolved_env or {})}
            merged_env.setdefault("PYTHONUTF8", "1")

            # Build params — pass cwd so module-relative commands work
            params_kwargs = {
                "command": command,
                "args": config.args,
                "env": merged_env,
            }
            # StdioServerParameters gained 'cwd' in mcp >= 1.1;
            # pass it when available so local servers can find their modules.
            try:
                import inspect
                if "cwd" in inspect.signature(StdioServerParameters).parameters:
                    params_kwargs["cwd"] = str(_base_path())
            except Exception:
                pass

            server_params = StdioServerParameters(**params_kwargs)

            # Enter the stdio_client context manager
            conn.context_manager = stdio_client(server_params)
            read_stream, write_stream = await conn.context_manager.__aenter__()
            conn.read_stream = read_stream
            conn.write_stream = write_stream

            # Enter the ClientSession context manager
            conn.session_cm = ClientSession(read_stream, write_stream)
            session = await conn.session_cm.__aenter__()
            conn.session = session

            # Initialize the session
            await session.initialize()

            # Discover tools
            tools_response = await session.list_tools()
            for tool in tools_response.tools:
                if tool.name in config.exclude_tools:
                    continue
                info = MCPToolInfo(
                    name=tool.name,
                    description=tool.description or "",
                    server_name=name,
                    input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
                )
                conn.tools[tool.name] = info
                self._tool_map[tool.name] = name

            conn.last_used = time.monotonic()
            conn.starting = False

            logger.info(
                "MCP: server '%s' started — %d tools available: %s",
                name, len(conn.tools),
                ", ".join(sorted(conn.tools.keys())[:10]),
            )

        except ImportError as e:
            logger.error(
                "MCP SDK not installed — cannot start server '%s'. "
                "Install with: pip install mcp",
                name,
            )
            raise RuntimeError(f"MCP SDK not available: {e}") from e
        except Exception as e:
            logger.error("MCP: failed to start server '%s': %s", name, e)
            # Clean up partial state
            await self._cleanup_connection(conn)
            raise

    async def _stop_server(self, name: str) -> None:
        """Stop a running MCP server."""
        conn = self._connections.pop(name, None)
        if not conn:
            return
        await self._cleanup_connection(conn)
        # Remove tool mappings
        for tool_name in list(self._tool_map):
            if self._tool_map[tool_name] == name:
                del self._tool_map[tool_name]
        logger.info("MCP: stopped server '%s'", name)

    async def _cleanup_connection(self, conn: ServerConnection) -> None:
        """Clean up a server connection's resources.

        The MCP SDK's stdio_client uses anyio task groups internally.
        On shutdown, CancelledError can propagate — catch it so
        cleanup completes gracefully.
        """
        try:
            if conn.session_cm:
                await conn.session_cm.__aexit__(None, None, None)
        except (Exception, asyncio.CancelledError) as e:
            logger.debug("MCP cleanup session error: %s", e)

        try:
            if conn.context_manager:
                await conn.context_manager.__aexit__(None, None, None)
        except (Exception, asyncio.CancelledError) as e:
            logger.debug("MCP cleanup transport error: %s", e)

        conn.session = None
        conn.read_stream = None
        conn.write_stream = None
        conn.context_manager = None
        conn.session_cm = None
        conn.tools.clear()

    # -- Idle monitoring ---------------------------------------------------

    async def _idle_monitor(self) -> None:
        """Periodically check for idle servers and stop them.

        Acquires _lifecycle_lock before stopping so a concurrent call_tool()
        cannot race with the teardown.
        """
        while not self._shutdown:
            await asyncio.sleep(30)
            now = time.monotonic()
            for name, conn in list(self._connections.items()):
                timeout = conn.config.idle_timeout
                if timeout <= 0:
                    continue  # Never timeout
                if conn.session is None:
                    continue
                idle_secs = now - conn.last_used
                if idle_secs > timeout:
                    logger.info(
                        "MCP: stopping idle server '%s' (idle %.0fs > %ds)",
                        name, idle_secs, timeout,
                    )
                    async with self._get_lifecycle_lock():
                        # Re-check after acquiring lock — call_tool may have
                        # refreshed last_used while we were waiting.
                        if (time.monotonic() - conn.last_used) > timeout:
                            await self._stop_server(name)

    # -- Internal helpers --------------------------------------------------

    def _rebuild_tool_map(self, all_tools: Dict[str, MCPToolInfo]) -> None:
        """Rebuild the tool_name → server_name mapping."""
        self._tool_map = {name: info.server_name for name, info in all_tools.items()}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_server_configs() -> Dict[str, ServerConfig]:
    """Load MCP server configs from config/mcp_servers.yaml."""
    config_path = os.path.join(_base_path(), "config", "mcp_servers.yaml")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Could not load MCP server config: %s", e)
        return {}

    servers = {}
    for name, cfg in raw.get("servers", {}).items():
        if not isinstance(cfg, dict):
            continue
        servers[name] = ServerConfig(
            name=name,
            command=cfg.get("command", ""),
            args=cfg.get("args", []),
            env=cfg.get("env", {}),
            idle_timeout=int(cfg.get("idle_timeout", 300)),
            enabled=cfg.get("enabled", True),
            exclude_tools=cfg.get("exclude_tools", []),
        )
    return servers


def _resolve_env(env: Dict[str, str]) -> Dict[str, str]:
    """Resolve ${VAR} references in env values from os.environ."""
    resolved = {}
    for key, val in env.items():
        if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
            var_name = val[2:-1]
            actual = os.environ.get(var_name, "")
            if actual:
                resolved[key] = actual
            else:
                logger.debug("MCP env: %s references %s but it's not set", key, var_name)
        else:
            resolved[key] = str(val)
    return resolved


def _parse_tool_result(result: Any) -> Dict[str, Any]:
    """Convert MCP CallToolResult to Archi's standard tool result dict.

    MCP returns CallToolResult with .content (list of content blocks)
    and .isError flag. We flatten this into our {success, ...} format.
    """
    try:
        is_error = getattr(result, "isError", False)

        # Extract text content from result blocks
        text_parts = []
        for block in getattr(result, "content", []):
            if hasattr(block, "text"):
                text_parts.append(block.text)

        combined = "\n".join(text_parts)

        if is_error:
            return {"success": False, "error": combined or "MCP tool returned error"}

        # Try to parse as JSON (our local server returns JSON-encoded results)
        try:
            import json
            parsed = json.loads(combined)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

        # Return raw text as content
        return {"success": True, "content": combined}

    except Exception as e:
        logger.error("Failed to parse MCP tool result: %s", e)
        return {"success": False, "error": f"Result parse error: {e}"}
