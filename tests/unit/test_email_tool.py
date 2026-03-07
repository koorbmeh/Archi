"""Tests for src/tools/email_tool.py."""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.tools import email_tool
from src.tools.email_tool import (
    send_email,
    check_inbox,
    search_inbox,
    is_configured,
    _reset_for_testing,
)


@pytest.fixture(autouse=True)
def reset_tool():
    """Reset singleton before each test."""
    _reset_for_testing()
    yield
    _reset_for_testing()


# ── is_configured ─────────────────────────────────────────────────

class TestIsConfigured:
    @patch.dict(os.environ, {"ARCHI_EMAIL_ADDRESS": "a@b.com", "ARCHI_EMAIL_APP_PASSWORD": "pass"})
    def test_configured_when_both_set(self):
        assert is_configured() is True

    @patch.dict(os.environ, {"ARCHI_EMAIL_ADDRESS": "", "ARCHI_EMAIL_APP_PASSWORD": ""}, clear=False)
    def test_not_configured_when_empty(self):
        assert is_configured() is False

    @patch.dict(os.environ, {}, clear=True)
    def test_not_configured_when_missing(self):
        # Need to patch out other env vars that may be needed
        assert is_configured() is False


# ── send_email ────────────────────────────────────────────────────

class TestSendEmail:
    @patch.dict(os.environ, {"ARCHI_EMAIL_ADDRESS": "", "ARCHI_EMAIL_APP_PASSWORD": ""})
    def test_not_configured_error(self):
        result = send_email("to@example.com", "Subject", "Body")
        assert result["success"] is False
        assert "not configured" in result["error"].lower()

    @patch("src.tools.email_tool._get_client")
    def test_send_delegates_to_client(self, mock_get):
        mock_client = MagicMock()
        mock_client.send.return_value = {"success": True, "message": "Sent"}
        mock_get.return_value = mock_client

        result = send_email("to@example.com", "Hi", "Hello there")
        assert result["success"] is True
        mock_client.send.assert_called_once_with("to@example.com", "Hi", "Hello there")


# ── check_inbox ───────────────────────────────────────────────────

class TestCheckInbox:
    @patch("src.tools.email_tool._get_client")
    def test_check_delegates_to_client(self, mock_get):
        mock_client = MagicMock()
        mock_client.read_inbox.return_value = {
            "success": True,
            "count": 2,
            "messages": [
                {"from": "a@b.com", "subject": "Hello", "date": "today", "preview": "Hi"},
                {"from": "c@d.com", "subject": "World", "date": "today", "preview": "Yo"},
            ],
        }
        mock_get.return_value = mock_client

        result = check_inbox(max_count=5, unread_only=True)
        assert result["success"] is True
        assert result["count"] == 2
        mock_client.read_inbox.assert_called_once_with(max_count=5, unread_only=True)


# ── search_inbox ──────────────────────────────────────────────────

class TestSearchInbox:
    @patch("src.tools.email_tool._get_client")
    def test_search_delegates_to_client(self, mock_get):
        mock_client = MagicMock()
        mock_client.search.return_value = {"success": True, "count": 0, "messages": []}
        mock_get.return_value = mock_client

        result = search_inbox('FROM "test@example.com"', max_count=3)
        assert result["success"] is True
        assert result["count"] == 0
        mock_client.search.assert_called_once_with('FROM "test@example.com"', max_count=3)


# ── action_dispatcher handlers ────────────────────────────────────

class TestEmailDispatchHandlers:
    def test_send_email_handler_missing_params(self):
        from src.interfaces.action_dispatcher import dispatch
        response, actions, cost = dispatch("send_email", {}, {"router": MagicMock()})
        assert "need" in response.lower()

    @patch("src.tools.email_tool.send_email")
    def test_send_email_handler_success(self, mock_send):
        mock_send.return_value = {"success": True, "message": "Sent"}
        from src.interfaces.action_dispatcher import dispatch
        response, actions, cost = dispatch(
            "send_email",
            {"to": "bob@example.com", "subject": "Hi", "body": "Hello"},
            {"router": MagicMock()},
        )
        assert "sent" in response.lower()
        mock_send.assert_called_once()

    @patch("src.tools.email_tool.check_inbox")
    def test_check_email_handler_empty(self, mock_check):
        mock_check.return_value = {"success": True, "count": 0, "messages": []}
        from src.interfaces.action_dispatcher import dispatch
        response, actions, cost = dispatch("check_email", {}, {"router": MagicMock()})
        assert "no" in response.lower()

    @patch("src.tools.email_tool.check_inbox")
    def test_check_email_handler_with_messages(self, mock_check):
        mock_check.return_value = {
            "success": True,
            "count": 1,
            "messages": [
                {"from": "a@b.com", "subject": "Test", "date": "today", "preview": "Hello"},
            ],
        }
        from src.interfaces.action_dispatcher import dispatch
        response, actions, cost = dispatch("check_email", {}, {"router": MagicMock()})
        assert "1 email" in response.lower() or "found 1" in response.lower()

    def test_search_email_handler_missing_query(self):
        from src.interfaces.action_dispatcher import dispatch
        response, actions, cost = dispatch("search_email", {}, {"router": MagicMock()})
        assert "need" in response.lower()


# ── Dream-mode email approval queue ─────────────────────────────

class TestDreamModeEmailApproval:
    """Tests for dream-mode email approval gating in _handle_send_email."""

    @patch("src.tools.email_tool.send_email")
    def test_chat_mode_sends_immediately(self, mock_send):
        """Chat mode (source=discord) should send without approval."""
        mock_send.return_value = {"success": True, "message": "Sent"}
        from src.interfaces.action_dispatcher import dispatch
        response, actions, cost = dispatch(
            "send_email",
            {"to": "bob@example.com", "subject": "Hi", "body": "Hello"},
            {"router": MagicMock(), "source": "discord"},
        )
        assert "sent" in response.lower()
        mock_send.assert_called_once()

    @patch("src.interfaces.discord_bot.request_email_approval")
    @patch("src.tools.email_tool.send_email")
    def test_dream_mode_approved_sends(self, mock_send, mock_approve):
        """Dream mode with approval should proceed to send."""
        mock_approve.return_value = True
        mock_send.return_value = {"success": True, "message": "Sent"}
        from src.interfaces.action_dispatcher import _handle_send_email
        response, actions, cost = _handle_send_email(
            {"to": "bob@example.com", "subject": "Hi", "body": "Hello"},
            {"router": MagicMock(), "source": "dream_cycle_queue"},
        )
        assert "sent" in response.lower()
        mock_send.assert_called_once()

    @patch("src.interfaces.discord_bot.request_email_approval")
    def test_dream_mode_denied_blocks_send(self, mock_approve):
        """Dream mode with denied approval should NOT send."""
        mock_approve.return_value = False
        from src.interfaces.action_dispatcher import _handle_send_email
        response, actions, cost = _handle_send_email(
            {"to": "bob@example.com", "subject": "Hi", "body": "Hello"},
            {"router": MagicMock(), "source": "dream_cycle_queue"},
        )
        assert "not sent" in response.lower() or "denied" in response.lower()
        # email_tool.send_email should NOT have been called
        assert any("denied" in str(a.get("result", {})).lower() for a in actions)

    @patch("src.tools.email_tool.send_email")
    def test_unknown_source_sends_immediately(self, mock_send):
        """Unknown source (e.g., test_runner) should send without approval."""
        mock_send.return_value = {"success": True, "message": "Sent"}
        from src.interfaces.action_dispatcher import _handle_send_email
        response, actions, cost = _handle_send_email(
            {"to": "bob@example.com", "subject": "Hi", "body": "Hello"},
            {"router": MagicMock(), "source": "unknown"},
        )
        assert "sent" in response.lower()
        mock_send.assert_called_once()
