"""Tests for src/tools/tool_registry.py — Tool base, path validation, ToolRegistry, singleton."""

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.tools.tool_registry import (
    FileReadTool,
    FileWriteTool,
    Tool,
    ToolRegistry,
    WebSearchToolWrapper,
    _validate_path_security,
    _validate_write_path,
    get_shared_registry,
    _reset_for_testing,
)


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure singleton is clean before and after each test."""
    _reset_for_testing()
    yield
    _reset_for_testing()


@pytest.fixture
def registry():
    """Fresh ToolRegistry with default tools registered."""
    return ToolRegistry()


@pytest.fixture
def project_root(tmp_path):
    """Create a temporary project root with workspace/ and data/ dirs."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    return tmp_path


def _patch_base_path(project_root):
    """Patch base_path at the import location inside tool_registry functions."""
    return patch("src.utils.paths.base_path", return_value=str(project_root))


# ── Tool base class ──────────────────────────────────────────────────

class TestToolBase:
    def test_tool_stores_name_and_risk(self):
        t = Tool("my_tool", "L2_MEDIUM")
        assert t.name == "my_tool"
        assert t.risk_level == "L2_MEDIUM"

    def test_tool_default_risk(self):
        t = Tool("basic")
        assert t.risk_level == "L1_LOW"

    def test_execute_raises_not_implemented(self):
        t = Tool("abstract")
        with pytest.raises(NotImplementedError):
            t.execute({})


# ── Path security validation ─────────────────────────────────────────

class TestValidatePathSecurity:
    def test_path_inside_project_returns_none(self, project_root):
        path = str(project_root / "workspace" / "test.txt")
        with _patch_base_path(project_root):
            result = _validate_path_security(path)
        assert result is None

    def test_path_outside_project_returns_error(self, project_root):
        outside = str(Path("/tmp/evil.txt"))
        with _patch_base_path(project_root):
            result = _validate_path_security(outside)
        assert result is not None
        assert "outside project" in result.lower() or "Path" in result

    def test_project_root_itself_is_allowed(self, project_root):
        with _patch_base_path(project_root):
            result = _validate_path_security(str(project_root))
        assert result is None

    def test_import_error_returns_error_message(self):
        with patch("src.utils.paths.base_path", side_effect=Exception("no paths")):
            result = _validate_path_security("/any/path")
        assert result is not None
        assert "failed" in result.lower()


class TestValidateWritePath:
    def test_workspace_path_allowed(self, project_root):
        path = str(project_root / "workspace" / "output.txt")
        with _patch_base_path(project_root):
            result = _validate_write_path(path)
        assert result is None

    def test_data_path_allowed(self, project_root):
        path = str(project_root / "data" / "state.json")
        with _patch_base_path(project_root):
            result = _validate_write_path(path)
        assert result is None

    def test_src_path_blocked(self, project_root):
        src = project_root / "src"
        src.mkdir()
        path = str(src / "evil.py")
        with _patch_base_path(project_root):
            result = _validate_write_path(path)
        assert result is not None
        assert "restricted" in result.lower() or "workspace" in result.lower()

    def test_outside_project_blocked(self, project_root):
        with _patch_base_path(project_root):
            result = _validate_write_path("/tmp/hack.txt")
        assert result is not None


# ── FileReadTool ─────────────────────────────────────────────────────

class TestFileReadTool:
    def test_missing_path_returns_error(self):
        tool = FileReadTool()
        result = tool.execute({})
        assert result["success"] is False
        assert "path" in result["error"].lower()

    def test_reads_existing_file(self, project_root):
        f = project_root / "workspace" / "hello.txt"
        f.write_text("hello world")
        with _patch_base_path(project_root):
            result = FileReadTool().execute({"path": str(f)})
        assert result["success"] is True
        assert result["content"] == "hello world"
        assert result["size"] == 11

    def test_file_not_found(self, project_root):
        with _patch_base_path(project_root):
            result = FileReadTool().execute({"path": str(project_root / "nope.txt")})
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_truncates_to_500_chars(self, project_root):
        f = project_root / "workspace" / "big.txt"
        f.write_text("x" * 1000)
        with _patch_base_path(project_root):
            result = FileReadTool().execute({"path": str(f)})
        assert result["success"] is True
        assert len(result["content"]) == 500
        assert result["size"] == 1000

    def test_path_security_blocks_outside(self, project_root):
        with _patch_base_path(project_root):
            result = FileReadTool().execute({"path": "/etc/passwd"})
        assert result["success"] is False


# ── FileWriteTool ────────────────────────────────────────────────────

class TestFileWriteTool:
    def test_missing_path_returns_error(self):
        tool = FileWriteTool()
        result = tool.execute({})
        assert result["success"] is False

    def test_writes_to_workspace(self, project_root):
        path = str(project_root / "workspace" / "new.txt")
        with _patch_base_path(project_root):
            result = FileWriteTool().execute({"path": path, "content": "written"})
        assert result["success"] is True
        assert result["bytes_written"] == 7
        assert Path(path).read_text() == "written"

    def test_creates_parent_dirs(self, project_root):
        path = str(project_root / "workspace" / "deep" / "nested" / "file.txt")
        with _patch_base_path(project_root):
            result = FileWriteTool().execute({"path": path, "content": "deep"})
        assert result["success"] is True

    def test_blocked_write_to_src(self, project_root):
        src = project_root / "src"
        src.mkdir()
        with _patch_base_path(project_root):
            result = FileWriteTool().execute({"path": str(src / "bad.py"), "content": "evil"})
        assert result["success"] is False

    def test_default_content(self, project_root):
        path = str(project_root / "workspace" / "default.txt")
        with _patch_base_path(project_root):
            result = FileWriteTool().execute({"path": path})
        assert result["success"] is True
        assert Path(path).read_text() == "Test content"


# ── WebSearchToolWrapper ─────────────────────────────────────────────

class TestWebSearchToolWrapper:
    def test_missing_query_returns_error(self):
        tool = WebSearchToolWrapper()
        result = tool.execute({})
        assert result["success"] is False
        assert "query" in result["error"].lower()

    def test_accepts_q_param(self):
        tool = WebSearchToolWrapper()
        with patch("src.tools.web_search_tool.WebSearchTool") as MockSearch:
            mock_inst = MockSearch.return_value
            mock_inst.search.return_value = [{"title": "T", "snippet": "S", "url": "U"}]
            mock_inst.format_results.return_value = "formatted"
            result = tool.execute({"q": "test query"})
        assert result["success"] is True
        mock_inst.search.assert_called_once_with("test query", max_results=5)

    def test_no_results_returns_failure(self):
        tool = WebSearchToolWrapper()
        with patch("src.tools.web_search_tool.WebSearchTool") as MockSearch:
            mock_inst = MockSearch.return_value
            mock_inst.search.return_value = []
            result = tool.execute({"query": "obscure query"})
        assert result["success"] is False

    def test_custom_max_results(self):
        tool = WebSearchToolWrapper()
        with patch("src.tools.web_search_tool.WebSearchTool") as MockSearch:
            mock_inst = MockSearch.return_value
            mock_inst.search.return_value = [{"title": "T", "snippet": "S", "url": "U"}]
            mock_inst.format_results.return_value = "f"
            tool.execute({"query": "test", "max_results": "3"})
        mock_inst.search.assert_called_once_with("test", max_results=3)


# ── ToolRegistry ─────────────────────────────────────────────────────

class TestToolRegistryInit:
    def test_default_tools_registered(self, registry):
        names = list(registry.tools.keys())
        assert "read_file" in names
        assert "create_file" in names
        assert "web_search" in names

    def test_desktop_tools_registered(self, registry):
        assert "desktop_click" in registry.tools
        assert "desktop_type" in registry.tools
        assert "desktop_screenshot" in registry.tools

    def test_browser_tools_registered(self, registry):
        assert "browser_navigate" in registry.tools
        assert "browser_click" in registry.tools
        assert "browser_fill" in registry.tools


class TestToolRegistryRegister:
    def test_register_custom_tool(self, registry):
        custom = Tool("my_custom_tool", "L1_LOW")
        registry.register(custom)
        assert "my_custom_tool" in registry.tools

    def test_register_overwrites_existing(self, registry):
        t1 = Tool("dupe", "L1_LOW")
        t2 = Tool("dupe", "L3_HIGH")
        registry.register(t1)
        registry.register(t2)
        assert registry.tools["dupe"].risk_level == "L3_HIGH"


class TestToolRegistryExecute:
    def test_execute_known_tool(self, registry, project_root):
        f = project_root / "workspace" / "read_me.txt"
        f.write_text("content here")
        with _patch_base_path(project_root):
            result = registry.execute("read_file", {"path": str(f)})
        assert result["success"] is True

    def test_execute_unknown_tool_returns_error(self, registry):
        result = registry.execute("nonexistent_tool", {})
        assert result["success"] is False
        assert "Unknown tool" in result["error"]

    def test_circuit_breaker_mapping_desktop(self, registry):
        cb = registry._get_circuit("desktop_click")
        assert cb is not None

    def test_circuit_breaker_mapping_browser(self, registry):
        cb = registry._get_circuit("browser_navigate")
        assert cb is not None

    def test_circuit_breaker_mapping_file(self, registry):
        cb = registry._get_circuit("read_file")
        assert cb is not None

    def test_circuit_breaker_mapping_search(self, registry):
        cb = registry._get_circuit("web_search")
        assert cb is not None

    def test_tool_exception_caught(self, registry):
        """If a tool raises, execute catches and returns error dict."""
        bad_tool = MagicMock(spec=Tool)
        bad_tool.name = "bad"
        bad_tool.risk_level = "L1_LOW"
        bad_tool.execute.side_effect = RuntimeError("kaboom")
        registry.register(bad_tool)
        result = registry.execute("bad", {})
        assert result["success"] is False
        assert "kaboom" in result["error"]


class TestToolRegistryGetAllNames:
    def test_includes_direct_tools(self, registry):
        names = registry.get_all_tool_names()
        assert "read_file" in names
        assert "create_file" in names

    def test_returns_sorted(self, registry):
        names = registry.get_all_tool_names()
        assert names == sorted(names)


class TestToolRegistryMCPRouting:
    def test_direct_only_never_uses_mcp(self, registry):
        registry._mcp_client = MagicMock()
        registry._mcp_tools = {"generate_image"}
        assert registry._should_use_mcp("generate_image") is False

    def test_no_mcp_client_returns_false(self, registry):
        assert registry._should_use_mcp("web_search") is False

    def test_mcp_tool_routes_through_mcp(self, registry):
        registry._mcp_client = MagicMock()
        registry._mcp_tools = {"web_search"}
        assert registry._should_use_mcp("web_search") is True

    def test_non_mcp_tool_returns_false(self, registry):
        registry._mcp_client = MagicMock()
        registry._mcp_tools = {"other_tool"}
        assert registry._should_use_mcp("web_search") is False


# ── Singleton ────────────────────────────────────────────────────────

class TestSingleton:
    def test_get_shared_registry_returns_instance(self):
        with patch("src.tools.tool_registry.ToolRegistry") as MockReg:
            mock_inst = MockReg.return_value
            mock_inst.initialize_mcp = MagicMock()
            reg = get_shared_registry()
            assert reg is mock_inst

    def test_reset_clears_singleton(self):
        with patch("src.tools.tool_registry.ToolRegistry") as MockReg:
            mock_inst = MockReg.return_value
            mock_inst.initialize_mcp = MagicMock()
            r1 = get_shared_registry()
            _reset_for_testing()
            r2 = get_shared_registry()
            assert MockReg.call_count == 2

    def test_singleton_returns_same_instance(self):
        with patch("src.tools.tool_registry.ToolRegistry") as MockReg:
            mock_inst = MockReg.return_value
            mock_inst.initialize_mcp = MagicMock()
            r1 = get_shared_registry()
            r2 = get_shared_registry()
            assert r1 is r2
            assert MockReg.call_count == 1
