"""Tests for src/tools/mcp_client.py — MCP client manager, config loading, helpers."""

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest

from src.tools.mcp_client import (
    MCPClientManager,
    MCPToolInfo,
    ServerConfig,
    ServerConnection,
    _load_server_configs,
    _parse_tool_result,
    _resolve_env,
)


# ── MCPToolInfo ──────────────────────────────────────────────────────


class TestMCPToolInfo:
    def test_defaults(self):
        info = MCPToolInfo(name="t", description="d", server_name="s")
        assert info.name == "t"
        assert info.description == "d"
        assert info.server_name == "s"
        assert info.input_schema == {}

    def test_custom_schema(self):
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        info = MCPToolInfo(name="t", description="d", server_name="s", input_schema=schema)
        assert info.input_schema == schema


# ── ServerConfig ─────────────────────────────────────────────────────


class TestServerConfig:
    def test_defaults(self):
        cfg = ServerConfig(
            name="test", command="python", args=["-m", "server"],
            env={}, idle_timeout=300, enabled=True,
        )
        assert cfg.exclude_tools == []
        assert cfg.enabled is True
        assert cfg.idle_timeout == 300

    def test_with_exclude_tools(self):
        cfg = ServerConfig(
            name="test", command="node", args=["server.js"],
            env={"KEY": "val"}, idle_timeout=600, enabled=False,
            exclude_tools=["dangerous_tool"],
        )
        assert cfg.enabled is False
        assert "dangerous_tool" in cfg.exclude_tools


# ── ServerConnection ─────────────────────────────────────────────────


class TestServerConnection:
    def test_defaults(self):
        cfg = ServerConfig(
            name="s", command="cmd", args=[], env={}, idle_timeout=300, enabled=True,
        )
        conn = ServerConnection(config=cfg)
        assert conn.session is None
        assert conn.tools == {}
        assert conn.last_used == 0.0
        assert conn.starting is False
        assert conn._lock is None


# ── MCPClientManager init ────────────────────────────────────────────


class TestMCPClientManagerInit:
    def test_initial_state(self):
        mgr = MCPClientManager()
        assert mgr._servers == {}
        assert mgr._connections == {}
        assert mgr._tool_map == {}
        assert mgr._idle_task is None
        assert mgr._shutdown is False

    def test_get_lifecycle_lock_creates_once(self):
        mgr = MCPClientManager()
        lock1 = mgr._get_lifecycle_lock()
        lock2 = mgr._get_lifecycle_lock()
        assert lock1 is lock2
        assert isinstance(lock1, asyncio.Lock)


# ── MCPClientManager.initialize ──────────────────────────────────────


class TestInitialize:
    @pytest.mark.asyncio
    async def test_loads_configs_and_starts_idle_monitor(self):
        mgr = MCPClientManager()
        fake_configs = {
            "srv1": ServerConfig(
                name="srv1", command="cmd", args=[], env={},
                idle_timeout=300, enabled=True,
            ),
        }
        with patch("src.tools.mcp_client._load_server_configs", return_value=fake_configs):
            await mgr.initialize()

        assert mgr._servers == fake_configs
        assert mgr._idle_task is not None
        # Clean up
        mgr._shutdown = True
        mgr._idle_task.cancel()
        try:
            await mgr._idle_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_initialize_empty_config(self):
        mgr = MCPClientManager()
        with patch("src.tools.mcp_client._load_server_configs", return_value={}):
            await mgr.initialize()

        assert mgr._servers == {}
        mgr._shutdown = True
        mgr._idle_task.cancel()
        try:
            await mgr._idle_task
        except asyncio.CancelledError:
            pass


# ── MCPClientManager.get_server_for_tool ─────────────────────────────


class TestGetServerForTool:
    def test_known_tool(self):
        mgr = MCPClientManager()
        mgr._tool_map = {"web_search": "local-tools"}
        assert mgr.get_server_for_tool("web_search") == "local-tools"

    def test_unknown_tool(self):
        mgr = MCPClientManager()
        assert mgr.get_server_for_tool("nonexistent") is None


# ── MCPClientManager.call_tool ───────────────────────────────────────


class TestCallTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        mgr = MCPClientManager()
        mgr._tool_map = {}
        result = await mgr.call_tool("no_such_tool", {"arg": "val"})
        assert result["success"] is False
        assert "No MCP server provides" in result["error"]

    @pytest.mark.asyncio
    async def test_successful_call(self):
        mgr = MCPClientManager()
        mgr._tool_map = {"my_tool": "srv1"}
        mgr._lifecycle_lock = asyncio.Lock()

        mock_session = AsyncMock()
        # Build a fake CallToolResult
        mock_block = MagicMock()
        mock_block.text = '{"success": true, "data": "hello"}'
        mock_result = MagicMock()
        mock_result.isError = False
        mock_result.content = [mock_block]
        mock_session.call_tool.return_value = mock_result

        cfg = ServerConfig(
            name="srv1", command="cmd", args=[], env={},
            idle_timeout=300, enabled=True,
        )
        conn = ServerConnection(config=cfg)
        conn.session = mock_session
        mgr._connections["srv1"] = conn

        result = await mgr.call_tool("my_tool", {"q": "test"})
        assert result["success"] is True
        assert result["data"] == "hello"
        mock_session.call_tool.assert_called_once_with("my_tool", {"q": "test"})

    @pytest.mark.asyncio
    async def test_call_tool_exception(self):
        mgr = MCPClientManager()
        mgr._tool_map = {"my_tool": "srv1"}
        mgr._lifecycle_lock = asyncio.Lock()

        mock_session = AsyncMock()
        mock_session.call_tool.side_effect = RuntimeError("connection lost")

        cfg = ServerConfig(
            name="srv1", command="cmd", args=[], env={},
            idle_timeout=300, enabled=True,
        )
        conn = ServerConnection(config=cfg)
        conn.session = mock_session
        mgr._connections["srv1"] = conn

        result = await mgr.call_tool("my_tool", {})
        assert result["success"] is False
        assert "MCP call failed" in result["error"]


# ── MCPClientManager.list_all_tools ──────────────────────────────────


class TestListAllTools:
    @pytest.mark.asyncio
    async def test_lists_tools_from_enabled_servers(self):
        mgr = MCPClientManager()
        cfg_enabled = ServerConfig(
            name="s1", command="cmd", args=[], env={},
            idle_timeout=300, enabled=True,
        )
        cfg_disabled = ServerConfig(
            name="s2", command="cmd", args=[], env={},
            idle_timeout=300, enabled=False,
        )
        mgr._servers = {"s1": cfg_enabled, "s2": cfg_disabled}

        tool1 = MCPToolInfo(name="t1", description="desc1", server_name="s1")
        conn = ServerConnection(config=cfg_enabled)
        conn.session = MagicMock()
        conn.tools = {"t1": tool1}

        async def fake_ensure(name):
            return conn

        mgr._ensure_connection = fake_ensure

        result = await mgr.list_all_tools()
        assert "t1" in result
        assert mgr._tool_map["t1"] == "s1"

    @pytest.mark.asyncio
    async def test_handles_connection_failure(self):
        mgr = MCPClientManager()
        cfg = ServerConfig(
            name="s1", command="cmd", args=[], env={},
            idle_timeout=300, enabled=True,
        )
        mgr._servers = {"s1": cfg}

        async def fail_ensure(name):
            raise RuntimeError("can't start")

        mgr._ensure_connection = fail_ensure

        result = await mgr.list_all_tools()
        assert result == {}


# ── MCPClientManager._ensure_connection ──────────────────────────────


class TestEnsureConnection:
    @pytest.mark.asyncio
    async def test_returns_existing_connection(self):
        mgr = MCPClientManager()
        cfg = ServerConfig(
            name="s1", command="cmd", args=[], env={},
            idle_timeout=300, enabled=True,
        )
        conn = ServerConnection(config=cfg)
        conn.session = MagicMock()  # Already connected
        mgr._connections["s1"] = conn
        mgr._servers["s1"] = cfg

        result = await mgr._ensure_connection("s1")
        assert result is conn
        assert result.last_used > 0

    @pytest.mark.asyncio
    async def test_unknown_server_raises(self):
        mgr = MCPClientManager()
        mgr._servers = {}
        with pytest.raises(ValueError, match="Unknown MCP server"):
            await mgr._ensure_connection("nonexistent")

    @pytest.mark.asyncio
    async def test_starts_server_when_not_connected(self):
        mgr = MCPClientManager()
        cfg = ServerConfig(
            name="s1", command="cmd", args=[], env={},
            idle_timeout=300, enabled=True,
        )
        mgr._servers = {"s1": cfg}

        async def fake_start(name, conn):
            conn.session = MagicMock()

        mgr._start_server = fake_start

        result = await mgr._ensure_connection("s1")
        assert result.session is not None


# ── MCPClientManager.shutdown ────────────────────────────────────────


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_stops_all_servers(self):
        mgr = MCPClientManager()
        mgr._idle_task = asyncio.create_task(asyncio.sleep(100))

        cfg = ServerConfig(
            name="s1", command="cmd", args=[], env={},
            idle_timeout=300, enabled=True,
        )
        conn = ServerConnection(config=cfg)
        conn.session = MagicMock()
        mgr._connections["s1"] = conn
        mgr._tool_map["tool1"] = "s1"

        async def fake_cleanup(c):
            c.session = None
            c.tools.clear()

        mgr._cleanup_connection = fake_cleanup

        await mgr.shutdown()
        assert mgr._shutdown is True
        assert len(mgr._connections) == 0
        assert "tool1" not in mgr._tool_map

    @pytest.mark.asyncio
    async def test_shutdown_no_idle_task(self):
        mgr = MCPClientManager()
        mgr._idle_task = None
        await mgr.shutdown()
        assert mgr._shutdown is True


# ── MCPClientManager._stop_server ────────────────────────────────────


class TestStopServer:
    @pytest.mark.asyncio
    async def test_removes_connection_and_tools(self):
        mgr = MCPClientManager()
        cfg = ServerConfig(
            name="s1", command="cmd", args=[], env={},
            idle_timeout=300, enabled=True,
        )
        conn = ServerConnection(config=cfg)
        conn.session = MagicMock()
        mgr._connections["s1"] = conn
        mgr._tool_map = {"t1": "s1", "t2": "s2"}

        async def fake_cleanup(c):
            c.session = None

        mgr._cleanup_connection = fake_cleanup

        await mgr._stop_server("s1")
        assert "s1" not in mgr._connections
        assert "t1" not in mgr._tool_map
        assert "t2" in mgr._tool_map  # Other server's tools untouched

    @pytest.mark.asyncio
    async def test_stop_nonexistent_server(self):
        mgr = MCPClientManager()
        await mgr._stop_server("ghost")  # Should not raise


# ── MCPClientManager._cleanup_connection ─────────────────────────────


class TestCleanupConnection:
    @pytest.mark.asyncio
    async def test_cleans_up_all_resources(self):
        cfg = ServerConfig(
            name="s1", command="cmd", args=[], env={},
            idle_timeout=300, enabled=True,
        )
        conn = ServerConnection(config=cfg)
        conn.session_cm = AsyncMock()
        conn.context_manager = AsyncMock()
        conn.session = MagicMock()
        conn.read_stream = MagicMock()
        conn.write_stream = MagicMock()
        conn.tools = {"t1": MagicMock()}

        mgr = MCPClientManager()
        await mgr._cleanup_connection(conn)

        assert conn.session is None
        assert conn.read_stream is None
        assert conn.write_stream is None
        assert conn.context_manager is None
        assert conn.session_cm is None
        assert conn.tools == {}

    @pytest.mark.asyncio
    async def test_handles_cleanup_errors(self):
        cfg = ServerConfig(
            name="s1", command="cmd", args=[], env={},
            idle_timeout=300, enabled=True,
        )
        conn = ServerConnection(config=cfg)
        conn.session_cm = AsyncMock()
        conn.session_cm.__aexit__.side_effect = RuntimeError("cleanup fail")
        conn.context_manager = AsyncMock()
        conn.context_manager.__aexit__.side_effect = RuntimeError("transport fail")

        mgr = MCPClientManager()
        await mgr._cleanup_connection(conn)  # Should not raise
        assert conn.session is None


# ── MCPClientManager._rebuild_tool_map ───────────────────────────────


class TestRebuildToolMap:
    def test_rebuilds_from_tools(self):
        mgr = MCPClientManager()
        tools = {
            "t1": MCPToolInfo(name="t1", description="", server_name="s1"),
            "t2": MCPToolInfo(name="t2", description="", server_name="s2"),
        }
        mgr._rebuild_tool_map(tools)
        assert mgr._tool_map == {"t1": "s1", "t2": "s2"}

    def test_empty_tools(self):
        mgr = MCPClientManager()
        mgr._tool_map = {"old": "server"}
        mgr._rebuild_tool_map({})
        assert mgr._tool_map == {}


# ── _load_server_configs ─────────────────────────────────────────────


class TestLoadServerConfigs:
    def test_loads_valid_yaml(self, tmp_path):
        yaml_content = """
servers:
  local:
    command: python
    args: ["-m", "server"]
    env:
      API_KEY: "${MY_KEY}"
    idle_timeout: 600
    enabled: true
    exclude_tools: ["dangerous"]
  disabled:
    command: node
    args: ["server.js"]
    env: {}
    idle_timeout: 300
    enabled: false
"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "mcp_servers.yaml"
        config_file.write_text(yaml_content)

        with patch("src.tools.mcp_client._base_path", return_value=str(tmp_path)):
            configs = _load_server_configs()

        assert "local" in configs
        assert configs["local"].command == "python"
        assert configs["local"].args == ["-m", "server"]
        assert configs["local"].idle_timeout == 600
        assert configs["local"].enabled is True
        assert configs["local"].exclude_tools == ["dangerous"]
        assert "disabled" in configs
        assert configs["disabled"].enabled is False

    def test_missing_config_file(self, tmp_path):
        with patch("src.tools.mcp_client._base_path", return_value=str(tmp_path)):
            configs = _load_server_configs()
        assert configs == {}

    def test_empty_config(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "mcp_servers.yaml").write_text("")

        with patch("src.tools.mcp_client._base_path", return_value=str(tmp_path)):
            configs = _load_server_configs()
        assert configs == {}

    def test_skips_non_dict_entries(self, tmp_path):
        yaml_content = """
servers:
  valid:
    command: python
    args: []
    env: {}
    idle_timeout: 300
    enabled: true
  invalid: "not a dict"
"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "mcp_servers.yaml").write_text(yaml_content)

        with patch("src.tools.mcp_client._base_path", return_value=str(tmp_path)):
            configs = _load_server_configs()
        assert "valid" in configs
        assert "invalid" not in configs

    def test_default_values(self, tmp_path):
        yaml_content = """
servers:
  minimal:
    {}
"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "mcp_servers.yaml").write_text(yaml_content)

        with patch("src.tools.mcp_client._base_path", return_value=str(tmp_path)):
            configs = _load_server_configs()
        assert configs["minimal"].command == ""
        assert configs["minimal"].args == []
        assert configs["minimal"].idle_timeout == 300
        assert configs["minimal"].enabled is True


# ── _resolve_env ─────────────────────────────────────────────────────


class TestResolveEnv:
    def test_resolves_var_refs(self):
        with patch.dict(os.environ, {"MY_SECRET": "abc123"}):
            result = _resolve_env({"API_KEY": "${MY_SECRET}"})
        assert result == {"API_KEY": "abc123"}

    def test_missing_var_excluded(self):
        with patch.dict(os.environ, {}, clear=True):
            result = _resolve_env({"API_KEY": "${NONEXISTENT_VAR}"})
        assert "API_KEY" not in result

    def test_literal_values_passed_through(self):
        result = _resolve_env({"HOST": "localhost", "PORT": "8080"})
        assert result == {"HOST": "localhost", "PORT": "8080"}

    def test_non_string_values_converted(self):
        result = _resolve_env({"PORT": 8080})
        assert result == {"PORT": "8080"}

    def test_empty_env(self):
        result = _resolve_env({})
        assert result == {}

    def test_partial_var_syntax_not_resolved(self):
        result = _resolve_env({"KEY": "${INCOMPLETE"})
        assert result == {"KEY": "${INCOMPLETE"}


# ── _parse_tool_result ───────────────────────────────────────────────


class TestParseToolResult:
    def test_success_json_result(self):
        block = MagicMock()
        block.text = '{"success": true, "files": ["a.py"]}'
        result = MagicMock()
        result.isError = False
        result.content = [block]

        parsed = _parse_tool_result(result)
        assert parsed["success"] is True
        assert parsed["files"] == ["a.py"]

    def test_error_result(self):
        block = MagicMock()
        block.text = "Something went wrong"
        result = MagicMock()
        result.isError = True
        result.content = [block]

        parsed = _parse_tool_result(result)
        assert parsed["success"] is False
        assert "Something went wrong" in parsed["error"]

    def test_error_no_text(self):
        result = MagicMock()
        result.isError = True
        result.content = []

        parsed = _parse_tool_result(result)
        assert parsed["success"] is False
        assert "MCP tool returned error" in parsed["error"]

    def test_non_json_text(self):
        block = MagicMock()
        block.text = "Just some plain text output"
        result = MagicMock()
        result.isError = False
        result.content = [block]

        parsed = _parse_tool_result(result)
        assert parsed["success"] is True
        assert parsed["content"] == "Just some plain text output"

    def test_multiple_text_blocks(self):
        block1 = MagicMock()
        block1.text = "Line 1"
        block2 = MagicMock()
        block2.text = "Line 2"
        result = MagicMock()
        result.isError = False
        result.content = [block1, block2]

        parsed = _parse_tool_result(result)
        assert parsed["success"] is True
        assert "Line 1" in parsed["content"]
        assert "Line 2" in parsed["content"]

    def test_blocks_without_text_attr(self):
        block = MagicMock(spec=[])  # No text attribute
        result = MagicMock()
        result.isError = False
        result.content = [block]

        parsed = _parse_tool_result(result)
        assert parsed["success"] is True

    def test_exception_in_parsing(self):
        result = MagicMock()
        result.isError = False
        # Make content iteration raise
        type(result).content = property(lambda self: (_ for _ in ()).throw(ValueError("bad")))

        parsed = _parse_tool_result(result)
        assert parsed["success"] is False
        assert "Result parse error" in parsed["error"]

    def test_empty_content(self):
        result = MagicMock()
        result.isError = False
        result.content = []

        parsed = _parse_tool_result(result)
        assert parsed["success"] is True
        assert parsed["content"] == ""
