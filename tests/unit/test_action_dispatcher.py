"""
Unit tests for action_dispatcher.py handlers.

Covers: dispatch, _handle_chat, _handle_search, _handle_create_file,
_handle_create_goal, _handle_generate_image, _handle_screenshot,
_handle_click, _handle_browser_navigate, _handle_fetch_webpage,
_handle_send_file, _handle_read_file, _handle_list_files, _handle_unknown,
_is_chat_claiming_action_done, _workspace_path.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest

from src.interfaces.action_dispatcher import (
    dispatch,
    _handle_chat,
    _handle_search,
    _handle_create_file,
    _handle_list_files,
    _handle_read_file,
    _handle_create_goal,
    _handle_generate_image,
    _handle_click,
    _handle_screenshot,
    _handle_browser_navigate,
    _handle_fetch_webpage,
    _handle_unknown,
    _handle_send_file,
    _extract_file_path_from_context,
    _extract_file_path_from_history,
    _find_file_path_in_text,
    _is_chat_claiming_action_done,
    _workspace_path,
    _fetch_url_text,
    ACTION_HANDLERS,
)

# All tool-using handlers import get_shared_registry locally from
# src.tools.tool_registry, so we patch at that location.
_REGISTRY_PATCH = "src.tools.tool_registry.get_shared_registry"
_SAFETY_PATCH = "src.core.safety_controller.SafetyController"
_RESOLVE_PATCH = "src.core.plan_executor._resolve_project_path"


# ── dispatch ─────────────────────────────────────────────────────────

class TestDispatch:
    """dispatch() routes to the correct handler and falls back on unknowns."""

    def test_known_action_routes(self):
        resp, actions, cost = dispatch("chat", {"response": "hello"}, {})
        assert resp == "hello"

    def test_unknown_action_falls_back(self):
        mock_router = MagicMock()
        mock_router.generate.return_value = {"text": "fallback answer", "cost_usd": 0.01}
        ctx = {"router": mock_router, "system_prompt": "", "effective_message": "hi", "history_messages": []}
        resp, actions, cost = dispatch("nonexistent_action", {}, ctx)
        assert "fallback answer" in resp or "not sure" in resp.lower()

    def test_all_handlers_registered(self):
        expected = {
            "chat", "search", "create_file", "list_files", "read_file",
            "send_file", "create_goal", "create_skill", "generate_image",
            "screenshot", "click", "browser_navigate", "fetch_webpage",
            "create_schedule", "modify_schedule", "remove_schedule", "list_schedule",
            "send_email", "check_email", "search_email",
            "morning_digest", "check_calendar",
            "create_content", "publish_content", "list_content",
            "deep_research",
            "content_plan", "content_upcoming", "content_schedule",
            "content_adapt", "content_image",
            "add_supplement", "remove_supplement", "log_supplement",
            "supplement_status",
            "log_expense", "add_subscription", "cancel_subscription",
            "set_budget", "finance_status",
        }
        assert set(ACTION_HANDLERS.keys()) == expected


# ── _handle_chat ─────────────────────────────────────────────────────

class TestHandleChat:
    """_handle_chat returns the response from params."""

    def test_returns_response(self):
        resp, actions, cost = _handle_chat({"response": "Hello there"}, {})
        assert resp == "Hello there"
        assert actions == []
        assert cost == 0.0

    def test_empty_response_fallback(self):
        resp, _, _ = _handle_chat({"response": ""}, {})
        assert "not sure" in resp.lower()

    def test_none_response_fallback(self):
        resp, _, _ = _handle_chat({}, {})
        assert "not sure" in resp.lower()

    def test_rejects_false_claim(self):
        resp, _, _ = _handle_chat({"response": "I created the file for you."}, {})
        assert "didn't actually execute" in resp


# ── _is_chat_claiming_action_done ────────────────────────────────────

class TestIsChatClaimingActionDone:
    """Detect false claims of work."""

    def test_empty_string(self):
        assert _is_chat_claiming_action_done("") is False

    def test_none(self):
        assert _is_chat_claiming_action_done(None) is False

    def test_normal_chat(self):
        assert _is_chat_claiming_action_done("Sure, I can help with that.") is False

    def test_file_creation_claim(self):
        assert _is_chat_claiming_action_done("I created the file you asked for.") is True

    def test_navigation_claim(self):
        assert _is_chat_claiming_action_done("I've navigated to the page.") is True

    def test_image_generation_claim(self):
        assert _is_chat_claiming_action_done("I've generated the images you requested.") is True

    def test_case_insensitive(self):
        assert _is_chat_claiming_action_done("I CREATED THE FILE for you") is True


# ── _workspace_path ──────────────────────────────────────────────────

class TestWorkspacePath:
    """Path resolution and traversal guard."""

    def test_normal_path(self):
        result = _workspace_path("test.txt")
        assert "workspace" in result
        assert result.endswith("test.txt")

    def test_prepends_workspace(self):
        result = _workspace_path("subdir/file.py")
        assert "workspace" in result

    def test_already_prefixed(self):
        result = _workspace_path("workspace/test.txt")
        assert result.endswith("test.txt")
        assert "workspace/workspace" not in result

    def test_traversal_blocked(self):
        with pytest.raises(ValueError, match="escapes workspace"):
            _workspace_path("../../etc/passwd")

    def test_traversal_blocked_absolute(self):
        with pytest.raises(ValueError, match="escapes workspace"):
            _workspace_path("workspace/../../etc/passwd")

    def test_leading_slash_stripped(self):
        result = _workspace_path("/test.txt")
        assert "workspace" in result


# ── _handle_search ───────────────────────────────────────────────────

class TestHandleSearch:
    """Search handler: query enrichment, tool dispatch, error paths."""

    def test_empty_query(self):
        resp, actions, cost = _handle_search({}, {"router": MagicMock(), "effective_message": ""})
        assert "couldn't determine" in resp.lower()

    @patch(_REGISTRY_PATCH)
    def test_successful_search(self, mock_reg):
        mock_tools = MagicMock()
        mock_tools.execute.return_value = {"success": True, "formatted": "Result snippet here"}
        mock_reg.return_value = mock_tools

        mock_router = MagicMock()
        mock_router.generate.return_value = {"text": "Summarized answer", "cost_usd": 0.001}

        resp, actions, cost = _handle_search({"query": "test query"}, {"router": mock_router})
        assert "Summarized answer" in resp
        assert cost > 0

    @patch(_REGISTRY_PATCH)
    def test_search_no_results(self, mock_reg):
        mock_tools = MagicMock()
        mock_tools.execute.return_value = {"success": False, "error": "No results"}
        mock_reg.return_value = mock_tools

        resp, _, cost = _handle_search({"query": "obscure query"}, {"router": MagicMock()})
        assert "No results" in resp

    @patch(_REGISTRY_PATCH)
    def test_silver_price_enrichment(self, mock_reg):
        """Commodity queries get enriched with unit info."""
        mock_tools = MagicMock()
        mock_tools.execute.return_value = {"success": True, "formatted": "Silver $30"}
        mock_reg.return_value = mock_tools

        mock_router = MagicMock()
        mock_router.generate.return_value = {"text": "Silver is $30/oz", "cost_usd": 0.001}

        _handle_search({"query": "current price of silver"}, {"router": mock_router})
        call_args = mock_tools.execute.call_args[0]
        assert "ounce" in call_args[1]["query"].lower()

    def test_search_import_error(self):
        with patch(_REGISTRY_PATCH, side_effect=ImportError("no ddgs")):
            resp, _, _ = _handle_search({"query": "test"}, {"router": MagicMock()})
        assert "not available" in resp.lower()


# ── _handle_create_file ──────────────────────────────────────────────

class TestHandleCreateFile:
    """File creation handler: path validation, safety check, tool call."""

    def test_no_path(self):
        resp, _, _ = _handle_create_file({}, {})
        assert "no path" in resp.lower()

    def test_traversal_rejected(self):
        resp, _, _ = _handle_create_file({"path": "../../etc/passwd", "content": "bad"}, {})
        assert "escapes workspace" in resp.lower()

    @patch(_REGISTRY_PATCH)
    @patch("os.path.isfile", return_value=True)
    @patch(_SAFETY_PATCH)
    def test_successful_creation(self, mock_sc_cls, mock_isfile, mock_reg):
        mock_tools = MagicMock()
        mock_tools.execute.return_value = {"success": True}
        mock_reg.return_value = mock_tools
        mock_sc = MagicMock()
        mock_sc.authorize_action.return_value = {"allowed": True}
        mock_sc_cls.return_value = mock_sc

        resp, actions, _ = _handle_create_file({"path": "test.txt", "content": "hello"}, {})
        assert "created" in resp.lower()

    @patch(_REGISTRY_PATCH)
    @patch(_SAFETY_PATCH)
    def test_safety_blocked(self, mock_sc_cls, mock_reg):
        mock_sc = MagicMock()
        mock_sc.authorize_action.return_value = {"allowed": False, "reason": "protected"}
        mock_sc_cls.return_value = mock_sc

        resp, _, _ = _handle_create_file({"path": "test.txt", "content": "x"}, {})
        assert "Safety blocked" in resp


# ── _handle_create_goal ──────────────────────────────────────────────

class TestHandleCreateGoal:
    """Goal creation handler."""

    def test_no_goal_manager(self):
        resp, _, _ = _handle_create_goal({"description": "test"}, {})
        assert "not available" in resp.lower()

    def test_no_description(self):
        resp, _, _ = _handle_create_goal({}, {"goal_manager": MagicMock(), "effective_message": ""})
        assert "couldn't determine" in resp.lower()

    @patch("src.interfaces.discord_bot.kick_heartbeat")
    def test_successful_goal(self, mock_kick):
        mock_gm = MagicMock()
        mock_goal = MagicMock()
        mock_goal.goal_id = "g123"
        mock_gm.create_goal.return_value = mock_goal

        resp, _, _ = _handle_create_goal(
            {"description": "Write a poem"}, {"goal_manager": mock_gm, "source": "discord"}
        )
        assert "work on that" in resp.lower() or "on it" in resp.lower()
        mock_gm.create_goal.assert_called_once()

    def test_goal_creation_failure(self):
        mock_gm = MagicMock()
        mock_gm.create_goal.side_effect = RuntimeError("DB error")

        resp, _, _ = _handle_create_goal(
            {"description": "test"}, {"goal_manager": mock_gm, "source": "discord"}
        )
        assert "couldn't create" in resp.lower()


# ── _handle_create_skill ─────────────────────────────────────────────

class TestHandleCreateSkill:
    """Skill creation handler routes through SkillCreator."""

    def test_no_description_returns_error(self):
        from src.interfaces.action_dispatcher import _handle_create_skill
        resp, actions, cost = _handle_create_skill({}, {"router": MagicMock()})
        assert "need a description" in resp.lower()

    def test_no_router_returns_error(self):
        from src.interfaces.action_dispatcher import _handle_create_skill
        resp, actions, cost = _handle_create_skill({"description": "test"}, {})
        assert "model connection" in resp.lower()

    @patch("src.core.skill_creator.SkillCreator")
    def test_successful_creation(self, MockCreator):
        from src.interfaces.action_dispatcher import _handle_create_skill
        mock_creator = MockCreator.return_value
        mock_proposal = MagicMock()
        mock_proposal.name = "web_summarizer"
        mock_creator.create_skill_from_request.return_value = mock_proposal
        mock_creator.finalize_skill.return_value = True

        resp, actions, cost = _handle_create_skill(
            {"description": "summarize web pages"},
            {"router": MagicMock()},
        )
        assert "skill_web_summarizer" in resp
        assert len(actions) == 1
        mock_creator.create_skill_from_request.assert_called_once()
        mock_creator.finalize_skill.assert_called_once_with(mock_proposal)

    @patch("src.core.skill_creator.SkillCreator")
    def test_generation_failure(self, MockCreator):
        from src.interfaces.action_dispatcher import _handle_create_skill
        mock_creator = MockCreator.return_value
        mock_creator.create_skill_from_request.return_value = None

        resp, actions, cost = _handle_create_skill(
            {"description": "bad skill"},
            {"router": MagicMock()},
        )
        assert "couldn't generate" in resp.lower() or "failed validation" in resp.lower()


# ── _handle_generate_image ───────────────────────────────────────────

class TestHandleGenerateImage:
    """Image generation handler."""

    def test_no_prompt(self):
        resp, _, _ = _handle_generate_image({}, {"router": MagicMock(), "effective_message": ""})
        assert "need a description" in resp.lower()

    def test_single_image_success(self):
        mock_router = MagicMock()
        mock_router.generate_image.return_value = {
            "success": True, "image_path": "/workspace/images/test.png",
            "model_used": "sdxl", "duration_ms": 5000,
        }
        resp, actions, _ = _handle_generate_image(
            {"prompt": "a dragon"}, {"router": mock_router}
        )
        assert "generated" in resp.lower() or "Image" in resp
        assert len(actions) == 1

    def test_multi_image_success(self):
        mock_router = MagicMock()
        mock_router.generate_image.return_value = {
            "success": True, "image_path": "/workspace/images/test.png",
            "model_used": "sdxl", "duration_ms": 5000,
        }
        resp, actions, _ = _handle_generate_image(
            {"prompt": "cats", "count": 3}, {"router": mock_router}
        )
        assert "3 images" in resp
        assert len(actions) == 3
        mock_router.finish_image_batch.assert_called_once()

    def test_image_failure(self):
        mock_router = MagicMock()
        mock_router.generate_image.return_value = {"success": False, "error": "GPU OOM"}
        resp, actions, _ = _handle_generate_image(
            {"prompt": "a dragon"}, {"router": mock_router}
        )
        assert "failed" in resp.lower()
        assert "GPU OOM" in resp

    def test_count_capped_at_10(self):
        mock_router = MagicMock()
        mock_router.generate_image.return_value = {
            "success": True, "image_path": "/tmp/img.png",
            "model_used": "sdxl", "duration_ms": 1000,
        }
        _handle_generate_image({"prompt": "test", "count": 50}, {"router": mock_router})
        assert mock_router.generate_image.call_count == 10


# ── _handle_click ────────────────────────────────────────────────────

class TestHandleClick:
    """Click handler."""

    def test_no_target(self):
        resp, _, _ = _handle_click({}, {})
        assert "what should i click" in resp.lower()

    @patch(_REGISTRY_PATCH)
    def test_successful_click(self, mock_reg):
        mock_tools = MagicMock()
        mock_tools.execute.return_value = {"success": True, "method": "image_match"}
        mock_reg.return_value = mock_tools

        resp, actions, _ = _handle_click({"target": "OK button"}, {})
        assert "Clicked" in resp
        assert len(actions) == 1

    def test_click_not_available(self):
        with patch(_REGISTRY_PATCH, side_effect=ImportError):
            resp, _, _ = _handle_click({"target": "button"}, {})
        assert "not available" in resp.lower()


# ── _handle_screenshot ───────────────────────────────────────────────

class TestHandleScreenshot:
    """Screenshot handler."""

    @patch(_REGISTRY_PATCH)
    @patch("os.makedirs")
    def test_successful_screenshot(self, mock_mkdir, mock_reg):
        mock_tools = MagicMock()
        mock_tools.execute.return_value = {"success": True}
        mock_reg.return_value = mock_tools

        resp, actions, _ = _handle_screenshot({}, {})
        assert "screenshot" in resp.lower()
        assert len(actions) == 1

    def test_screenshot_not_available(self):
        with patch(_REGISTRY_PATCH, side_effect=ImportError):
            resp, _, _ = _handle_screenshot({}, {})
        assert "not available" in resp.lower()


# ── _handle_browser_navigate ─────────────────────────────────────────

class TestHandleBrowserNavigate:
    """Browser navigation handler."""

    def test_no_url(self):
        resp, _, _ = _handle_browser_navigate({}, {})
        assert "none was specified" in resp.lower()

    @patch(_REGISTRY_PATCH)
    def test_successful_navigation(self, mock_reg):
        mock_tools = MagicMock()
        mock_tools.execute.return_value = {"success": True}
        mock_reg.return_value = mock_tools

        resp, actions, _ = _handle_browser_navigate({"url": "https://example.com"}, {})
        assert "Opened" in resp
        assert len(actions) == 1

    @patch(_REGISTRY_PATCH)
    def test_url_normalization_shortcut(self, mock_reg):
        mock_tools = MagicMock()
        mock_tools.execute.return_value = {"success": True}
        mock_reg.return_value = mock_tools

        _handle_browser_navigate({"url": "google"}, {})
        call_args = mock_tools.execute.call_args[0]
        assert "google.com" in call_args[1]["url"]

    @patch(_REGISTRY_PATCH)
    def test_url_normalization_bare_domain(self, mock_reg):
        mock_tools = MagicMock()
        mock_tools.execute.return_value = {"success": True}
        mock_reg.return_value = mock_tools

        _handle_browser_navigate({"url": "example.com"}, {})
        call_args = mock_tools.execute.call_args[0]
        assert call_args[1]["url"] == "https://example.com"


# ── _handle_fetch_webpage ────────────────────────────────────────────

class TestHandleFetchWebpage:
    """Webpage fetch and summarize handler."""

    def test_no_url(self):
        resp, _, _ = _handle_fetch_webpage({}, {"router": MagicMock()})
        assert "no URL" in resp

    @patch("src.interfaces.action_dispatcher._fetch_url_text", return_value="Page content here")
    def test_successful_fetch(self, mock_fetch):
        mock_router = MagicMock()
        mock_router.generate.return_value = {"text": "Summary of page", "cost_usd": 0.001}

        resp, actions, cost = _handle_fetch_webpage(
            {"url": "https://example.com"}, {"router": mock_router}
        )
        assert "Summary of page" in resp
        assert cost > 0

    @patch("src.interfaces.action_dispatcher._fetch_url_text", return_value="")
    def test_empty_fetch(self, mock_fetch):
        resp, _, _ = _handle_fetch_webpage(
            {"url": "https://example.com"}, {"router": MagicMock()}
        )
        assert "couldn't extract" in resp.lower()

    @patch("src.interfaces.action_dispatcher._fetch_url_text", return_value="content")
    def test_url_normalization(self, mock_fetch):
        """URLs without scheme get https:// prepended."""
        mock_router = MagicMock()
        mock_router.generate.return_value = {"text": "ok", "cost_usd": 0}
        _handle_fetch_webpage({"url": "example.com"}, {"router": mock_router})
        mock_fetch.assert_called_with("https://example.com")


# ── _handle_send_file ────────────────────────────────────────────────

class TestExtractFilePathFromContext:
    """_extract_file_path_from_context extracts file paths from reply context."""

    def test_no_message(self):
        assert _extract_file_path_from_context("") is None
        assert _extract_file_path_from_context(None) is None

    def test_no_reply_context(self):
        assert _extract_file_path_from_context("Send me that file") is None

    def test_backtick_filename(self):
        msg = '[Replying to Archi\'s message: "Created `report.md` for you"]\n\nSend it'
        assert _extract_file_path_from_context(msg) == "report.md"

    def test_workspace_path(self):
        msg = '[Replying to Archi\'s message: "Files created: workspace/projects/data.json"]\n\nSend that'
        result = _extract_file_path_from_context(msg)
        assert result == "workspace/projects/data.json"

    def test_files_created_pattern(self):
        msg = '[Replying to Archi\'s message: "Task completed. Files created: pet_insurance_recommendations.md"]\n\nSend me that file'
        result = _extract_file_path_from_context(msg)
        assert "pet_insurance_recommendations.md" in result

    def test_plain_filename(self):
        msg = '[Replying to Archi\'s message: "I wrote summary.txt with the analysis"]\n\nSend it'
        result = _extract_file_path_from_context(msg)
        assert result == "summary.txt"

    def test_no_file_in_reply(self):
        msg = '[Replying to Archi\'s message: "Sure, I can help with that!"]\n\nSend me a file'
        assert _extract_file_path_from_context(msg) is None


class TestHandleSendFile:
    """Send file as Discord attachment."""

    def test_no_path(self):
        resp, _, _ = _handle_send_file({}, {})
        assert "need a path" in resp.lower()

    def test_extracts_path_from_reply_context(self):
        """When no path param, extract from reply context in effective_message."""
        ctx = {
            "effective_message": (
                '[Replying to Archi\'s message: "Files created: report.md"]\n\n'
                'Send me that file'
            ),
        }
        with patch(_RESOLVE_PATCH, return_value="/tmp/report.md") as mock_resolve, \
             patch("os.path.isfile", return_value=True), \
             patch("src.interfaces.action_dispatcher.send_notification", create=True) as mock_send:
            # Import send_notification at module level won't work, so we patch
            # the discord_bot import
            with patch("src.interfaces.discord_bot.send_notification", return_value=True):
                resp, actions, _ = _handle_send_file({}, ctx)
        mock_resolve.assert_called_once_with("report.md")

    @patch(_RESOLVE_PATCH, return_value="/tmp/test.txt")
    @patch("os.path.isfile", return_value=False)
    def test_file_not_found(self, mock_isfile, mock_resolve):
        resp, _, _ = _handle_send_file({"path": "missing.txt"}, {})
        assert "not found" in resp.lower()

    def test_extracts_path_from_history(self):
        """When no path param and no reply context, search conversation history."""
        ctx = {
            "effective_message": "Send me the file",
            "history_messages": [
                {"role": "user", "content": "Do number 1"},
                {"role": "assistant", "content": "Done — post_work_stretch_routine.html ready for you."},
                {"role": "user", "content": "Send me the file"},
            ],
        }
        with patch(_RESOLVE_PATCH, return_value="/tmp/post_work_stretch_routine.html"), \
             patch("os.path.isfile", return_value=True), \
             patch("src.interfaces.discord_bot.send_notification", return_value=True):
            resp, actions, _ = _handle_send_file({}, ctx)
        assert "sent" in resp.lower() or "attachment" in resp.lower()


# ── _find_file_path_in_text ──────────────────────────────────────────

class TestFindFilePathInText:
    """_find_file_path_in_text finds file paths in arbitrary text."""

    def test_workspace_path(self):
        assert _find_file_path_in_text("workspace/projects/data.json") == "workspace/projects/data.json"

    def test_backtick_filename(self):
        assert _find_file_path_in_text("Created `report.md` for you") == "report.md"

    def test_standalone_filename(self):
        assert _find_file_path_in_text("Done — post_work_stretch_routine.html ready") == "post_work_stretch_routine.html"

    def test_no_file(self):
        assert _find_file_path_in_text("Sure, I can help with that!") is None

    def test_empty(self):
        assert _find_file_path_in_text("") is None
        assert _find_file_path_in_text(None) is None

    def test_false_positive_skipped(self):
        assert _find_file_path_in_text("e.g this is an example") is None


# ── _extract_file_path_from_history ──────────────────────────────────

class TestExtractFilePathFromHistory:
    """_extract_file_path_from_history finds files in recent assistant messages."""

    def test_no_history(self):
        assert _extract_file_path_from_history([]) is None
        assert _extract_file_path_from_history(None) is None

    def test_finds_path_in_assistant_message(self):
        history = [
            {"role": "user", "content": "Do the stretch task"},
            {"role": "assistant", "content": "Done — post_work_stretch_routine.html ready for you."},
        ]
        result = _extract_file_path_from_history(history)
        assert result == "post_work_stretch_routine.html"

    def test_ignores_user_messages(self):
        history = [
            {"role": "user", "content": "I saved it as report.md"},
        ]
        assert _extract_file_path_from_history(history) is None

    def test_prefers_most_recent(self):
        history = [
            {"role": "assistant", "content": "Created old_file.txt"},
            {"role": "user", "content": "Do something else"},
            {"role": "assistant", "content": "Created new_file.html for you"},
        ]
        result = _extract_file_path_from_history(history)
        assert result == "new_file.html"

    def test_workspace_path_in_history(self):
        history = [
            {"role": "assistant", "content": "Files at workspace/projects/analysis.json"},
        ]
        result = _extract_file_path_from_history(history)
        assert result == "workspace/projects/analysis.json"


# ── _handle_read_file ────────────────────────────────────────────────

class TestHandleReadFile:
    """Read file handler."""

    def test_no_path(self):
        resp, _, _ = _handle_read_file({}, {})
        assert "need a path" in resp.lower()

    @patch(_RESOLVE_PATCH, return_value="/tmp/test.txt")
    @patch("os.path.isfile", return_value=False)
    def test_file_not_found(self, mock_isfile, mock_resolve):
        resp, _, _ = _handle_read_file({"path": "missing.txt"}, {})
        assert "not found" in resp.lower()

    @patch(_RESOLVE_PATCH, return_value="/tmp/test.txt")
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.open", mock_open(read_data="file contents here"))
    def test_successful_read(self, mock_isfile, mock_resolve):
        resp, actions, _ = _handle_read_file({"path": "test.txt"}, {})
        assert "file contents here" in resp
        assert len(actions) == 1


# ── _handle_list_files ───────────────────────────────────────────────

class TestHandleListFiles:
    """List files handler."""

    @patch(_RESOLVE_PATCH, return_value="/tmp/testdir")
    @patch("os.path.isdir", return_value=True)
    @patch("os.listdir", return_value=["a.txt", "b.py", "subdir"])
    def test_successful_list(self, mock_listdir, mock_isdir, mock_resolve):
        resp, actions, _ = _handle_list_files({"path": "testdir"}, {})
        assert "3 items" in resp
        assert len(actions) == 1

    @patch(_RESOLVE_PATCH, return_value="/tmp/nope")
    @patch("os.path.isdir", return_value=False)
    def test_not_a_directory(self, mock_isdir, mock_resolve):
        resp, _, _ = _handle_list_files({"path": "nope"}, {})
        assert "not a directory" in resp.lower()


# ── _handle_unknown ──────────────────────────────────────────────────

class TestHandleUnknown:
    """Fallback handler uses model for response."""

    def test_uses_model(self):
        mock_router = MagicMock()
        mock_router.generate.return_value = {"text": "I can help with that!", "cost_usd": 0.01}

        resp, actions, cost = _handle_unknown(
            {}, {"router": mock_router, "system_prompt": "", "history_messages": [], "effective_message": "hi"}
        )
        assert "I can help with that!" in resp
        assert cost == 0.01
        assert actions == []

    def test_empty_model_response(self):
        mock_router = MagicMock()
        mock_router.generate.return_value = {"text": "", "cost_usd": 0}

        resp, _, _ = _handle_unknown(
            {}, {"router": mock_router, "system_prompt": "", "history_messages": [], "effective_message": "hi"}
        )
        assert "not sure" in resp.lower()
