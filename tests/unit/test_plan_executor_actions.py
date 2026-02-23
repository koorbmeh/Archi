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
            return {"success": True, "formatted": "Result 1\nResult 2"}
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
        if name.startswith("_do_") or name == "_execute_action":
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

    def test_aliases_research_to_web_search(self):
        m = _make_mixin()
        parsed = {"action": "research", "query": "test"}
        result = m._execute_action(parsed, 1)
        assert parsed["action"] == "web_search"
        assert result["success"] is True

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
        # The actual edit_file handler will try path resolution, which we mock
        with patch("src.core.plan_executor.actions._resolve_project_path") as mock_rp, \
             patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=False):
            test_file = tmp_path / "test.py"
            test_file.write_text("x = 1", encoding="utf-8")
            mock_rp.return_value = str(test_file)
            with patch("src.core.plan_executor.actions.pre_modify_checkpoint", return_value="tag1"), \
                 patch("src.core.plan_executor.actions.post_modify_commit"), \
                 patch("src.core.plan_executor.actions._backup_file", return_value=None), \
                 patch("src.core.plan_executor.actions._syntax_check", return_value=None):
                result = m._execute_action(
                    {"action": "edit_file", "path": "workspace/test.py", "find": "x = 1", "replace": "x = 2"}, 1,
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

    def test_fetch_error_returned(self):
        m = _make_mixin()
        with patch("src.core.plan_executor.actions._fetch_url_text", return_value="Error fetching: timeout"):
            result = m._do_fetch_webpage({"url": "https://example.com"}, 1)
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
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(target)), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=False):
            result = m._do_edit_file({"path": "test.py", "find": "z = 3", "replace": "z = 4"}, 1)
            assert result["success"] is False
            assert "not found" in result["error"]

    def test_multiple_matches_without_replace_all(self, tmp_path):
        m = _make_mixin()
        target = tmp_path / "test.py"
        target.write_text("x = 1\nx = 1\n", encoding="utf-8")
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(target)), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=False):
            result = m._do_edit_file({"path": "test.py", "find": "x = 1", "replace": "x = 2"}, 1)
            assert result["success"] is False
            assert "matches 2 times" in result["error"]

    def test_replace_all_works(self, tmp_path):
        m = _make_mixin()
        target = tmp_path / "test.txt"
        target.write_text("aaa\naaa\n", encoding="utf-8")
        with patch("src.core.plan_executor.actions._check_protected"), \
             patch("src.core.plan_executor.actions._resolve_project_path", return_value=str(target)), \
             patch("src.core.plan_executor.actions._requires_approval", return_value=False), \
             patch("src.core.plan_executor.actions.pre_modify_checkpoint", return_value="tag"), \
             patch("src.core.plan_executor.actions.post_modify_commit"), \
             patch("src.core.plan_executor.actions._backup_file", return_value=None), \
             patch("src.core.plan_executor.actions._syntax_check", return_value=None):
            result = m._do_edit_file(
                {"path": "test.txt", "find": "aaa", "replace": "bbb", "replace_all": True}, 1,
            )
            assert result["success"] is True
            assert result["replacements"] == 2
            assert target.read_text() == "bbb\nbbb\n"


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
