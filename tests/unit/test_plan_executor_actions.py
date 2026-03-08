"""Unit tests for PlanExecutor action handlers.

Tests _execute_action() dispatch, individual action handler logic,
safety gates (source write denial, read-before-edit enforcement),
workspace path resolution, and action aliasing.

Created session 74.
"""

import os
import pytest
from unittest.mock import MagicMock, patch


# ── Helpers ──────────────────────────────────────────────────────────


class FakeTools:
    """Minimal tools mock for PlanExecutor action handlers."""

    def __init__(self):
        self.calls = []

    def execute(self, action, params):
        self.calls.append((action, params))
        if action == "web_search":
            return {
                "success": True,
                "formatted": "Result 1\nResult 2",
                "results": [
                    {"title": "Result 1", "snippet": "First result snippet", "url": "https://example.com/1"},
                    {"title": "Result 2", "snippet": "Second result snippet", "url": "https://example.com/2"},
                ],
            }
        if action == "create_file":
            return {"success": True}
        return {"error": f"Unknown tool: {action}"}


class FakeActionMixin:
    """Lightweight host for ActionMixin that doesn't need a full PlanExecutor."""

    def __init__(self, tools=None, approval_callback=None):
        self._tools = tools or FakeTools()
        self._approval_callback = approval_callback
        self._task_description = "test task"
        self._source_write_denied = False
        self._step_history = []

    @property
    def tools(self):
        return self._tools


def _make_mixin(**kwargs):
    """Construct a testable ActionMixin instance."""
    from src.core.plan_executor.actions import ActionMixin

    obj = FakeActionMixin(**kwargs)
    # Graft the mixin methods onto the instance
    for name in dir(ActionMixin):
        if name.startswith("_do_") or name.startswith("_cache_") or name.startswith("_get_snippet") or name == "_execute_action":
            method = getattr(ActionMixin, name)
            bound = method.__get__(obj, type(obj))
            setattr(obj, name, bound)
    return obj


# ── Action routing tests ─────────────────────────────────────────────


class TestExecuteActionDispatch:
    """Tests for _execute_action routing logic."""

    def test_routes_web_search(self):
        m = _make_mixin()
        result = m._execute_action({"action": "web_search", "query": "test"}, 1)
        assert result["success"] is True
        assert "snippet" in result

    def test_research_routes_to_deep_research(self):
        m = _make_mixin()
        # Patch _do_deep_research on the instance (after grafting)
        m._do_deep_research = MagicMock(return_value={"success": True, "snippet": "done"})
        parsed = {"action": "research", "question": "test"}
        result = m._execute_action(parsed, 1)
        assert m._do_deep_research.called

    def test_aliases_analyze_to_web_search(self):
        m = _make_mixin()
        parsed = {"action": "analyze", "query": "test"}
        m._execute_action(parsed, 1)
        assert parsed["action"] == "web_search"

    def test_aliases_search_to_web_search(self):
        m = _make_mixin()
        parsed = {"action": "search", "query": "test"}
        m._execute_action(parsed, 1)
        assert parsed["action"] == "web_search"

    def test_unknown_action_falls_to_tool_registry(self):
        m = _make_mixin()
        result = m._execute_action({"action": "unknown_tool"}, 1)
        assert m.tools.calls[-1][0] == "unknown_tool"

    def test_empty_action_falls_to_tool_registry(self):
        m = _make_mixin()
        result = m._execute_action({}, 1)
        assert m.tools.calls[-1][0] == ""


# ── Source write denial tests ────────────────────────────────────────


class TestSourceWriteDenial:
    """Tests for the sticky source-write-denied flag."""

    def test_write_source_blocked_after_denial(self):
        m = _make_mixin()
        m._source_write_denied = True
        result = m._execute_action({"action": "write_source", "path": "src/foo.py", "content": "x"}, 1)
        assert result["success"] is False
        assert "denied" in result["error"].lower()

    def test_edit_file_blocked_after_denial(self):
        m = _make_mixin()
        m._source_write_denied = True
        result = m._execute_action({"action": "edit_file", "path": "src/foo.py", "find": "a", "replace": "b"}, 1)
        assert result["success"] is False
        assert "denied" in result["error"].lower()


# ── Read-before-edit enforcement ─────────────────────────────────────


class TestReadBeforeEdit:
    """Tests for the requirement to read a file before editing it."""

    def test_edit_rejected_without_prior_read(self):
        m = _make_mixin()
        m._source_write_denied = False
        result = m._execute_action(
            {"action": "edit_file", "path": "workspace/test.py", "find": "x", "replace": "y"}, 1,
        )
        assert result["success"] is False
        assert "read_file" in result["error"]

    def test_edit_allowed_after_read(self, tmp_path):
        m = _make_mixin()
        m._source_write_denied = False
        # Simulate a recent read_file in step history
        m._step_history = [
            {"action": "read_file", "params": {"path": "workspace/test.py"}},
        ]
        _long_find = "x = 1  # this is a long enough find string"
        _long_replace = "x = 2  # this is a long enough find string"
        # The actual edit_file handler will try path resolution, which we mock
        with patch("src.core.plan_executor.actions._resolve_project_path") as mock_rp, \
             patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=False):
            test_file = tmp_path / "test.py"
            test_file.write_text(_long_find, encoding="utf-8")
            mock_rp.return_value = str(test_file)
            with patch("src.core.plan_executor.actions.pre_modify_checkpoint", return_value="tag1"), \
                 patch("src.core.plan_executor.actions.post_modify_commit"), \
                 patch("src.core.plan_executor.actions._backup_file", return_value=None), \
                 patch("src.core.plan_executor.actions._syntax_check", return_value=None):
                result = m._execute_action(
                    {"action": "edit_file", "path": "workspace/test.py", "find": _long_find, "replace": _long_replace}, 1,
                )
                assert result["success"] is True


# ── Web search tests ─────────────────────────────────────────────────


class TestDoWebSearch:
    """Tests for _do_web_search."""

    def test_empty_query_returns_error(self):
        m = _make_mixin()
        result = m._do_web_search({"query": ""}, 1)
        assert result["success"] is False
        assert "empty" in result["error"].lower()

    def test_whitespace_query_returns_error(self):
        m = _make_mixin()
        result = m._do_web_search({"query": "   "}, 1)
        assert result["success"] is False

    def test_successful_search(self):
        m = _make_mixin()
        result = m._do_web_search({"query": "python tutorial"}, 1)
        assert result["success"] is True
        assert "snippet" in result

    def test_search_failure_returns_error(self):
        tools = FakeTools()
        tools.execute = lambda a, p: {"success": False, "error": "Rate limited"}
        m = _make_mixin(tools=tools)
        result = m._do_web_search({"query": "test"}, 1)
        assert result["success"] is False
        assert "rate limited" in result["error"].lower()

    def test_search_exception_handled(self):
        tools = FakeTools()
        tools.execute = MagicMock(side_effect=RuntimeError("boom"))
        m = _make_mixin(tools=tools)
        result = m._do_web_search({"query": "test"}, 1)
        assert result["success"] is False
        assert "boom" in result["error"]

    def test_broadening_on_zero_results(self):
        """When search returns 0 results, should retry with simplified query."""
        call_count = [0]
        def fake_execute(action, params):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: no results
                return {"success": False, "error": "No results", "results": []}
            # Second call (broadened): results found
            return {
                "success": True,
                "formatted": "Broadened result",
                "results": [{"title": "Trail", "snippet": "A nice trail", "url": "https://trails.com/1"}],
            }
        tools = FakeTools()
        tools.execute = fake_execute
        m = _make_mixin(tools=tools)
        result = m._do_web_search({"query": '"best hiking trails near Portland Oregon"'}, 1)
        assert result["success"] is True
        assert call_count[0] == 2  # Retried once
        assert "broadened" in result["snippet"].lower()

    def test_no_broadening_when_query_already_simple(self):
        """Short queries that can't be simplified should not retry."""
        tools = FakeTools()
        tools.execute = MagicMock(return_value={"success": False, "error": "No results", "results": []})
        m = _make_mixin(tools=tools)
        result = m._do_web_search({"query": "python"}, 1)
        assert result["success"] is False
        # Should only call once (no broadening possible)
        assert tools.execute.call_count == 1

    def test_search_caches_snippets(self):
        """Successful search should cache snippets for fetch_webpage fallback."""
        m = _make_mixin()
        m._do_web_search({"query": "hiking trails"}, 1)
        assert hasattr(m, "_snippet_cache")
        assert "https://example.com/1" in m._snippet_cache
        assert m._snippet_cache["https://example.com/1"]["snippet"] == "First result snippet"


# ── Query simplification tests ───────────────────────────────────────


class TestSimplifyQuery:
    """Tests for _simplify_query helper."""

    def test_strips_quotes(self):
        from src.core.plan_executor.actions import _simplify_query
        result = _simplify_query('"best hiking trails"')
        assert '"' not in (result or "")

    def test_drops_filler_words(self):
        from src.core.plan_executor.actions import _simplify_query
        result = _simplify_query("find the best affordable hiking trails near Portland")
        assert result is not None
        lower = result.lower()
        assert "best" not in lower
        assert "find" not in lower
        assert "the" not in lower
        assert "hiking" in lower

    def test_returns_none_for_already_simple(self):
        from src.core.plan_executor.actions import _simplify_query
        result = _simplify_query("python")
        assert result is None

    def test_caps_at_five_words(self):
        from src.core.plan_executor.actions import _simplify_query
        result = _simplify_query("alpha bravo charlie delta echo foxtrot golf hotel")
        assert result is not None
        assert len(result.split()) <= 5


# ── Fetch webpage tests ──────────────────────────────────────────────


class TestDoFetchWebpage:
    """Tests for _do_fetch_webpage."""

    def test_empty_url_returns_error(self):
        m = _make_mixin()
        result = m._do_fetch_webpage({"url": ""}, 1)
        assert result["success"] is False
        assert "no url" in result["error"].lower()

    def test_auto_prepends_https(self):
        m = _make_mixin()
        with patch("src.core.plan_executor.actions._fetch_url_text", return_value="Page content here") as mock_fetch:
            result = m._do_fetch_webpage({"url": "example.com"}, 1)
            mock_fetch.assert_called_once_with("https://example.com", max_chars=5000)
            assert result["success"] is True

    def test_fetch_error_returned_no_cache(self):
        """Fetch failure with no cached snippet should return error."""
        m = _make_mixin()
        with patch("src.core.plan_executor.actions._fetch_url_text", return_value="Error fetching: timeout"):
            result = m._do_fetch_webpage({"url": "https://example.com"}, 1)
            assert result["success"] is False

    def test_fetch_error_falls_back_to_cached_snippet(self):
        """Fetch failure should fall back to cached search snippet if available."""
        m = _make_mixin()
        # Pre-populate snippet cache (as if web_search ran first)
        m._snippet_cache = {
            "https://alltrails.com/trail/foo": {
                "title": "Foo Trail",
                "snippet": "A beautiful 3-mile loop through old-growth forest.",
            },
        }
        with patch("src.core.plan_executor.actions._fetch_url_text", return_value="Error fetching: 403 Forbidden"):
            result = m._do_fetch_webpage({"url": "https://alltrails.com/trail/foo"}, 1)
            assert result["success"] is True
            assert "search result snippet" in result.get("note", "").lower()
            assert "Foo Trail" in result["snippet"]
            assert "old-growth forest" in result["snippet"]

    def test_fetch_exception_falls_back_to_cached_snippet(self):
        """Fetch exception should also fall back to cached snippet."""
        m = _make_mixin()
        m._snippet_cache = {
            "https://blocked-site.com/page": {
                "title": "Blocked Page",
                "snippet": "Useful information from search results.",
            },
        }
        with patch("src.core.plan_executor.actions._fetch_url_text", side_effect=ConnectionError("refused")):
            result = m._do_fetch_webpage({"url": "https://blocked-site.com/page"}, 1)
            assert result["success"] is True
            assert "Useful information" in result["snippet"]

    def test_snippet_fallback_prefix_match(self):
        """Snippet cache should match URLs by prefix (handles trailing slashes, params)."""
        m = _make_mixin()
        m._snippet_cache = {
            "https://example.com/trails": {
                "title": "Trails Page",
                "snippet": "List of hiking trails in the area.",
            },
        }
        with patch("src.core.plan_executor.actions._fetch_url_text", return_value="Error fetching: 403"):
            result = m._do_fetch_webpage({"url": "https://example.com/trails/"}, 1)
            assert result["success"] is True
            assert "hiking trails" in result["snippet"].lower()

    def test_no_fallback_when_snippet_empty(self):
        """Empty snippet in cache should not trigger fallback."""
        m = _make_mixin()
        m._snippet_cache = {
            "https://example.com/empty": {"title": "Empty", "snippet": ""},
        }
        with patch("src.core.plan_executor.actions._fetch_url_text", return_value="Error fetching: 403"):
            result = m._do_fetch_webpage({"url": "https://example.com/empty"}, 1)
            assert result["success"] is False


# ── Create file tests ────────────────────────────────────────────────


class TestDoCreateFile:
    """Tests for _do_create_file."""

    def test_no_path_returns_error(self):
        m = _make_mixin()
        result = m._do_create_file({"path": "", "content": "hello"}, 1)
        assert result["success"] is False
        assert "no file path" in result["error"].lower()

    def test_invalid_path_returns_error(self):
        m = _make_mixin()
        with patch("src.core.plan_executor.actions._resolve_workspace_path", side_effect=ValueError("Outside workspace")):
            result = m._do_create_file({"path": "/etc/passwd", "content": "x"}, 1)
            assert result["success"] is False
            assert "outside workspace" in result["error"].lower()


    def test_json_truncation_detected(self):
        """Truncated JSON in create_file should return an error with retry guidance."""
        tools = FakeTools()
        tools.execute = MagicMock(return_value={"success": True})
        m = _make_mixin(tools=tools)
        truncated_json = '{"providers": [{"name": "Acme"}, {"name": "Beta"'  # missing ]}
        with patch("src.core.plan_executor.actions._resolve_workspace_path", return_value="/tmp/test.json"):
            result = m._do_create_file({"path": "test.json", "content": truncated_json}, 1)
            assert result["success"] is False
            assert "truncated" in result["error"].lower()
            assert "run_python" in result["error"]

    def test_json_truncation_not_written_to_disk(self):
        """Session 194: truncated JSON must NOT be written to disk (pre-write validation)."""
        tools = FakeTools()
        tools.execute = MagicMock(return_value={"success": True})
        m = _make_mixin(tools=tools)
        truncated_json = '{"items": [{"id": 1}, {"id": 2'
        with patch("src.core.plan_executor.actions._resolve_workspace_path", return_value="/tmp/bad.json"):
            result = m._do_create_file({"path": "bad.json", "content": truncated_json}, 1)
            assert result["success"] is False
            # Key assertion: tools.execute("create_file", ...) was never called
            tools.execute.assert_not_called()

    def test_valid_json_passes_validation(self):
        """Valid JSON in create_file should succeed normally."""
        tools = FakeTools()
        tools.execute = MagicMock(return_value={"success": True})
        m = _make_mixin(tools=tools)
        valid_json = '{"name": "Acme", "value": 42}'
        with patch("src.core.plan_executor.actions._resolve_workspace_path", return_value="/tmp/test.json"):
            result = m._do_create_file({"path": "test.json", "content": valid_json}, 1)
            assert result["success"] is True

    def test_non_json_file_skips_validation(self):
        """Non-JSON files should not be validated for JSON syntax."""
        tools = FakeTools()
        tools.execute = MagicMock(return_value={"success": True})
        m = _make_mixin(tools=tools)
        with patch("src.core.plan_executor.actions._resolve_workspace_path", return_value="/tmp/test.txt"):
            result = m._do_create_file({"path": "test.txt", "content": "not json {"}, 1)
            assert result["success"] is True

    def test_html_truncation_detected(self):
        """Session 194: truncated HTML (missing </html>) returns error, not written."""
        tools = FakeTools()
        tools.execute = MagicMock(return_value={"success": True})
        m = _make_mixin(tools=tools)
        truncated_html = "<html><head><title>Test</title></head><body><p>Content"
        with patch("src.core.plan_executor.actions._resolve_workspace_path", return_value="/tmp/page.html"):
            result = m._do_create_file({"path": "page.html", "content": truncated_html}, 1)
            assert result["success"] is False
            assert "truncated" in result["error"].lower()
            assert "run_python" in result["error"]
            tools.execute.assert_not_called()

    def test_html_complete_passes_validation(self):
        """Complete HTML with closing tag should pass validation."""
        tools = FakeTools()
        tools.execute = MagicMock(return_value={"success": True})
        m = _make_mixin(tools=tools)
        complete_html = "<html><head></head><body><p>Hello</p></body></html>"
        with patch("src.core.plan_executor.actions._resolve_workspace_path", return_value="/tmp/ok.html"):
            result = m._do_create_file({"path": "ok.html", "content": complete_html}, 1)
            assert result["success"] is True

    def test_htm_extension_also_validated(self):
        """Session 194: .htm files also get HTML truncation check."""
        tools = FakeTools()
        tools.execute = MagicMock(return_value={"success": True})
        m = _make_mixin(tools=tools)
        truncated = "<HTML><body>stuff"
        with patch("src.core.plan_executor.actions._resolve_workspace_path", return_value="/tmp/page.htm"):
            result = m._do_create_file({"path": "page.htm", "content": truncated}, 1)
            assert result["success"] is False
            tools.execute.assert_not_called()


# ── Append file tests ────────────────────────────────────────────────


class TestDoAppendFile:
    """Tests for _do_append_file including duplicate content guard."""

    def test_no_path_returns_error(self):
        m = _make_mixin()
        result = m._do_append_file({"path": "", "content": "hello"}, 1)
        assert result["success"] is False

    def test_append_creates_new_file(self, tmp_path):
        m = _make_mixin()
        target = tmp_path / "output" / "new.txt"
        with patch("src.core.plan_executor.actions._resolve_workspace_path", return_value=str(target)):
            result = m._do_append_file({"path": "output/new.txt", "content": "hello world"}, 1)
            assert result["success"] is True
            assert target.read_text() == "hello world"

    def test_duplicate_content_guard(self, tmp_path):
        """If append content already exists in file, skip the append."""
        m = _make_mixin()
        target = tmp_path / "existing.txt"
        existing_content = "This is the existing content that was already written before."
        target.write_text(existing_content * 3, encoding="utf-8")  # Make it > 100 chars
        with patch("src.core.plan_executor.actions._resolve_workspace_path", return_value=str(target)):
            result = m._do_append_file(
                {"path": "existing.txt", "content": existing_content}, 1,
            )
            assert result["success"] is True
            assert "already present" in result.get("note", "").lower()

    def test_no_duplicate_guard_for_new_content(self, tmp_path):
        """New content should append normally."""
        m = _make_mixin()
        target = tmp_path / "existing.txt"
        target.write_text("A" * 200, encoding="utf-8")
        with patch("src.core.plan_executor.actions._resolve_workspace_path", return_value=str(target)):
            result = m._do_append_file(
                {"path": "existing.txt", "content": "Completely different content here"}, 1,
            )
            assert result["success"] is True
            assert "note" not in result  # No duplicate guard triggered


# ── Read file tests ──────────────────────────────────────────────────


class TestDoReadFile:
    """Tests for _do_read_file."""

    def test_no_path_returns_error(self):
        m = _make_mixin()
        result = m._do_read_file({"path": ""}, 1)
        assert result["success"] is False

    def test_missing_file_returns_error(self, tmp_path):
        m = _make_mixin()
        with patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(tmp_path / "nope.txt")):
            result = m._do_read_file({"path": "nope.txt"}, 1)
            assert result["success"] is False
            assert "not found" in result["error"].lower()

    def test_successful_read(self, tmp_path):
        m = _make_mixin()
        target = tmp_path / "hello.txt"
        target.write_text("Hello World", encoding="utf-8")
        with patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(target)):
            result = m._do_read_file({"path": "hello.txt"}, 1)
            assert result["success"] is True
            assert "Hello World" in result["snippet"]


# ── List files tests ─────────────────────────────────────────────────


class TestDoListFiles:
    """Tests for _do_list_files."""

    def test_not_a_directory(self, tmp_path):
        m = _make_mixin()
        f = tmp_path / "file.txt"
        f.write_text("x")
        with patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(f)):
            result = m._do_list_files({"path": "file.txt"}, 1)
            assert result["success"] is False
            assert "not a directory" in result["error"].lower()

    def test_lists_directory_contents(self, tmp_path):
        m = _make_mixin()
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "subdir").mkdir()
        with patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(tmp_path)):
            result = m._do_list_files({"path": "."}, 1)
            assert result["success"] is True
            assert "a.py" in result["snippet"]
            assert "subdir/" in result["snippet"]

    def test_empty_directory(self, tmp_path):
        m = _make_mixin()
        empty = tmp_path / "empty"
        empty.mkdir()
        with patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(empty)):
            result = m._do_list_files({"path": "empty"}, 1)
            assert result["success"] is True
            assert "empty" in result["snippet"].lower()


# ── Run command tests ────────────────────────────────────────────────


class TestDoRunCommand:
    """Tests for _do_run_command safety and execution."""

    def test_empty_command_returns_error(self):
        m = _make_mixin()
        result = m._do_run_command({"command": ""}, 1)
        assert result["success"] is False

    def test_blocked_command_not_on_allowlist(self):
        m = _make_mixin()
        with patch("src.core.plan_executor.actions._get_safety") as mock_safety:
            mock_safety.side_effect = lambda key: (
                {"pip", "pytest", "git", "python"} if key == "allowed_commands"
                else [] if key == "blocked_commands"
                else set()
            )
            result = m._do_run_command({"command": "curl http://evil.com"}, 1)
            assert result["success"] is False
            assert "not on the allowed" in result["error"]

    def test_allowed_command_blocked_by_blocklist(self):
        m = _make_mixin()
        with patch("src.core.plan_executor.actions._get_safety") as mock_safety:
            mock_safety.side_effect = lambda key: (
                {"git"} if key == "allowed_commands"
                else ["push --force"] if key == "blocked_commands"
                else set()
            )
            result = m._do_run_command({"command": "git push --force origin main"}, 1)
            assert result["success"] is False
            assert "blocked" in result["error"].lower()

    def test_allowed_command_passes_safety(self):
        m = _make_mixin()
        with patch("src.core.plan_executor.actions._get_safety") as mock_safety, \
             patch("subprocess.run") as mock_run, \
             patch("src.utils.paths.base_path", return_value="/tmp"):
            mock_safety.side_effect = lambda key: (
                {"python"} if key == "allowed_commands"
                else [] if key == "blocked_commands"
                else set()
            )
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = m._do_run_command({"command": "python --version"}, 1)
            assert result["success"] is True

    def test_exe_suffix_stripped(self):
        """python.exe should be recognized as python."""
        m = _make_mixin()
        with patch("src.core.plan_executor.actions._get_safety") as mock_safety, \
             patch("subprocess.run") as mock_run, \
             patch("src.utils.paths.base_path", return_value="/tmp"):
            mock_safety.side_effect = lambda key: (
                {"python"} if key == "allowed_commands"
                else [] if key == "blocked_commands"
                else set()
            )
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = m._do_run_command({"command": "python.exe --version"}, 1)
            assert result["success"] is True


# ── Ask user deferral detection ──────────────────────────────────────


class TestDoAskUser:
    """Tests for _do_ask_user deferral signal detection."""

    def test_empty_question_returns_error(self):
        m = _make_mixin()
        result = m._do_ask_user({"question": ""}, 1)
        assert result["success"] is False

    def test_deferral_detected_tomorrow(self):
        m = _make_mixin()
        with patch("src.interfaces.discord_bot.ask_user", return_value="I'll get to it tomorrow"):
            result = m._do_ask_user({"question": "What's your preference?"}, 1)
            assert result.get("deferred") is True
            assert "tomorrow" in result["error"].lower()

    def test_deferral_detected_few_hours(self):
        m = _make_mixin()
        with patch("src.interfaces.discord_bot.ask_user", return_value="That'll take a few hours"):
            result = m._do_ask_user({"question": "What supplements?"}, 1)
            assert result.get("deferred") is True
            assert "2 hour" in result["error"].lower()

    def test_normal_reply_not_deferred(self):
        m = _make_mixin()
        with patch("src.interfaces.discord_bot.ask_user", return_value="I prefer the second option"):
            result = m._do_ask_user({"question": "Which option?"}, 1)
            assert result["success"] is True
            assert result.get("deferred") is not True
            assert "second option" in result["response"]

    def test_no_reply_returns_timeout(self):
        m = _make_mixin()
        with patch("src.interfaces.discord_bot.ask_user", return_value=None):
            result = m._do_ask_user({"question": "Hello?"}, 1)
            assert result["success"] is False
            assert result["response"] is None


# ── Run python tests ─────────────────────────────────────────────────


class TestDoRunPython:
    """Tests for _do_run_python."""

    def test_empty_code_returns_error(self):
        m = _make_mixin()
        result = m._do_run_python({"code": ""}, 1)
        assert result["success"] is False
        assert "no code" in result["error"].lower()

    def test_successful_python_execution(self):
        m = _make_mixin()
        with patch("subprocess.run") as mock_run, \
             patch("src.utils.paths.base_path", return_value="/tmp"):
            mock_run.return_value = MagicMock(returncode=0, stdout="42", stderr="")
            result = m._do_run_python({"code": "print(42)"}, 1)
            assert result["success"] is True
            assert "42" in result["output"]

    def test_python_timeout_handled(self):
        import subprocess
        m = _make_mixin()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("python", 30)), \
             patch("src.utils.paths.base_path", return_value="/tmp"):
            result = m._do_run_python({"code": "while True: pass"}, 1)
            assert result["success"] is False
            assert "timed out" in result["error"].lower()

    def test_python_sets_utf8_env(self):
        m = _make_mixin()
        with patch("subprocess.run") as mock_run, \
             patch("src.utils.paths.base_path", return_value="/tmp"):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            m._do_run_python({"code": "pass"}, 1)
            call_kwargs = mock_run.call_args
            env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env", {})
            assert env.get("PYTHONUTF8") == "1"


# ── Edit file tests ──────────────────────────────────────────────────


class TestDoEditFile:
    """Tests for _do_edit_file logic (find/replace, multi-match, syntax check)."""

    def test_no_path_returns_error(self):
        m = _make_mixin()
        m._step_history = [{"action": "read_file", "params": {"path": ""}}]
        result = m._execute_action({"action": "edit_file", "path": "", "find": "a", "replace": "b"}, 1)
        assert result["success"] is False

    def test_no_find_string_returns_error(self):
        m = _make_mixin()
        m._step_history = [{"action": "read_file", "params": {"path": "test.py"}}]
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value="/tmp/test.py"), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=False):
            result = m._do_edit_file({"path": "test.py", "find": "", "replace": "b"}, 1)
            assert result["success"] is False
            assert "no 'find'" in result["error"].lower()

    def test_find_string_not_in_file(self, tmp_path):
        m = _make_mixin()
        target = tmp_path / "test.py"
        target.write_text("x = 1\ny = 2\n", encoding="utf-8")
        _find = "z = 3  # this line does not exist in file"
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(target)), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=False):
            result = m._do_edit_file({"path": "test.py", "find": _find, "replace": "z = 4"}, 1)
            assert result["success"] is False
            assert "not found" in result["error"]

    def test_short_find_string_rejected(self):
        """Find strings under 30 chars (without newline) are rejected to prevent ambiguous matches."""
        m = _make_mixin()
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value="/tmp/test.py"), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=False):
            result = m._do_edit_file({"path": "test.py", "find": "x = 1", "replace": "x = 2"}, 1)
            assert result["success"] is False
            assert "too short" in result["error"]

    def test_multiline_short_find_string_allowed(self, tmp_path):
        """Find strings with newlines are allowed even if under 30 chars (multi-line is unambiguous)."""
        m = _make_mixin()
        target = tmp_path / "test.txt"
        target.write_text("a = 1\nb = 2\n", encoding="utf-8")
        _find = "a = 1\nb = 2"
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(target)), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=False), \
             patch("src.core.plan_executor.actions.pre_modify_checkpoint", return_value="tag"), \
             patch("src.core.plan_executor.actions.post_modify_commit"), \
             patch("src.core.plan_executor.actions._backup_file", return_value=None), \
             patch("src.core.plan_executor.actions._syntax_check", return_value=None):
            result = m._do_edit_file({"path": "test.txt", "find": _find, "replace": "a = 10\nb = 20"}, 1)
            assert result["success"] is True

    def test_multiple_matches_without_replace_all(self, tmp_path):
        m = _make_mixin()
        target = tmp_path / "test.py"
        _repeated_line = "some_variable = compute_value()  # init"
        target.write_text(f"{_repeated_line}\n{_repeated_line}\n", encoding="utf-8")
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(target)), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=False):
            result = m._do_edit_file({"path": "test.py", "find": _repeated_line, "replace": "x = 2"}, 1)
            assert result["success"] is False
            assert "matches 2 times" in result["error"]

    def test_replace_all_works(self, tmp_path):
        m = _make_mixin()
        target = tmp_path / "test.txt"
        _repeated_line = "some_repeated_content_line_here"
        target.write_text(f"{_repeated_line}\n{_repeated_line}\n", encoding="utf-8")
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(target)), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=False), \
             patch("src.core.plan_executor.actions.pre_modify_checkpoint", return_value="tag"), \
             patch("src.core.plan_executor.actions.post_modify_commit"), \
             patch("src.core.plan_executor.actions._backup_file", return_value=None), \
             patch("src.core.plan_executor.actions._syntax_check", return_value=None):
            result = m._do_edit_file(
                {"path": "test.txt", "find": _repeated_line, "replace": "replaced_content_here_instead", "replace_all": True}, 1,
            )
            assert result["success"] is True
            assert result["replacements"] == 2
            assert target.read_text() == "replaced_content_here_instead\nreplaced_content_here_instead\n"


# ── Write source approval gate tests ────────────────────────────────


class TestWriteSourceApproval:
    """Tests for the write_source approval gate."""

    def test_no_approval_channel_blocks_and_sets_denied(self):
        m = _make_mixin(approval_callback=None)
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value="/tmp/src/foo.py"), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=True), \
             patch("src.core.plan_executor.actions._check_pre_approved", return_value=False):
            result = m._do_write_source({"path": "src/foo.py", "content": "x = 1"}, 1)
            assert result["success"] is False
            assert "no approval channel" in result["error"].lower()
            assert m._source_write_denied is True

    def test_user_denial_blocks_and_sets_denied(self):
        m = _make_mixin(approval_callback=lambda *a: False)
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value="/tmp/src/foo.py"), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=True), \
             patch("src.core.plan_executor.actions._check_pre_approved", return_value=False):
            result = m._do_write_source({"path": "src/foo.py", "content": "x = 1"}, 1)
            assert result["success"] is False
            assert "denied" in result["error"].lower()
            assert m._source_write_denied is True

    def test_pre_approved_skips_callback(self, tmp_path):
        callback = MagicMock()
        m = _make_mixin(approval_callback=callback)
        target = tmp_path / "foo.py"
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(target)), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=True), \
             patch("src.core.plan_executor.actions._check_pre_approved", return_value=True), \
             patch("src.core.plan_executor.actions.pre_modify_checkpoint", return_value="tag"), \
             patch("src.core.plan_executor.actions.post_modify_commit"), \
             patch("src.core.plan_executor.actions._backup_file", return_value=None), \
             patch("src.core.plan_executor.actions._syntax_check", return_value=None):
            result = m._do_write_source({"path": "src/foo.py", "content": "x = 1"}, 1)
            callback.assert_not_called()
            assert result["success"] is True


# ── run_python cwd fix tests ─────────────────────────────────────────


class TestRunPythonCwd:
    """Verify run_python uses project root as cwd (not workspace/)."""

    def test_cwd_is_project_root_not_workspace(self):
        """run_python cwd should be the project root, not workspace/."""
        m = _make_mixin()
        with patch("subprocess.run") as mock_run, \
             patch("src.utils.paths.base_path", return_value="/project/root"), \
             patch("os.makedirs"):
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            m._do_run_python({"code": "print('test')"}, 1)
            call_kwargs = mock_run.call_args
            cwd = call_kwargs.kwargs.get("cwd") or call_kwargs[1].get("cwd")
            assert cwd == "/project/root", f"Expected project root, got {cwd}"


# ── Repeated-error early abort tests ─────────────────────────────────


class TestRepeatedErrorAbort:
    """Verify PlanExecutor aborts early on repeated identical errors."""

    def _make_executor(self):
        """Create a minimal PlanExecutor with mocked router."""
        from src.core.plan_executor.executor import PlanExecutor
        mock_router = MagicMock()
        executor = PlanExecutor(router=mock_router, tools=FakeTools())
        return executor, mock_router

    def test_aborts_after_three_identical_errors(self):
        """Three identical errors on the same file should trigger abort."""
        executor, mock_router = self._make_executor()

        # Build responses: 3 run_python actions that all fail with the same error
        step_responses = []
        for _ in range(3):
            step_responses.append({
                "text": '{"action": "run_python", "code": "import workspace.data"}',
                "cost_usd": 0.01,
                "success": True,
            })
        # Should never reach this — abort should happen after 3rd failure
        step_responses.append({
            "text": '{"action": "done", "summary": "finished"}',
            "cost_usd": 0.0,
            "success": True,
        })
        mock_router.generate.side_effect = step_responses

        # Mock run_python to always fail with the same error
        import subprocess as sp
        with patch("subprocess.run") as mock_run, \
             patch("src.utils.paths.base_path", return_value="/tmp"), \
             patch("src.core.plan_executor.executor.load_state", return_value=None), \
             patch("src.core.plan_executor.executor.save_state"), \
             patch("src.core.plan_executor.executor.clear_state"), \
             patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None):
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="FileNotFoundError: workspace/data/file.json",
            )
            result = executor.execute(
                task_description="test task",
                goal_context="test goal",
                max_steps=10,
                task_id="test_repeated_error",
            )

        # Should have aborted with a repeated_error_abort step
        last_step = result["steps_taken"][-1]
        assert last_step.get("repeated_error_abort") is True
        assert "repeated" in last_step["summary"].lower()
        # Should have fewer than 10 steps (aborted early)
        assert result["total_steps"] <= 5
