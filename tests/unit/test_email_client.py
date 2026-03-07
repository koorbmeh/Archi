"""Tests for src/utils/email_client.py."""

import email.mime.text
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.utils.email_client import (
    EmailClient,
    _check_rate_limit,
    _contains_secrets,
    _reset_for_testing,
    _MAX_SENDS_PER_DAY,
)


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Reset rate limit state before each test."""
    _reset_for_testing()
    yield
    _reset_for_testing()


# ── Secret detection ──────────────────────────────────────────────

class TestContainsSecrets:
    def test_detects_api_key(self):
        assert _contains_secrets("here is my api_key=abc123") is True

    def test_detects_app_password(self):
        assert _contains_secrets("APP_PASSWORD=xyz") is True

    def test_detects_discord_token(self):
        assert _contains_secrets("My discord_token is foo") is True

    def test_clean_text_passes(self):
        assert _contains_secrets("Hello, how are you today?") is False

    def test_empty_string(self):
        assert _contains_secrets("") is False


# ── Rate limiting ─────────────────────────────────────────────────

class TestRateLimit:
    def test_allows_first_send(self):
        assert _check_rate_limit() is None

    def test_blocks_after_max(self):
        for _ in range(_MAX_SENDS_PER_DAY):
            _check_rate_limit()
        result = _check_rate_limit()
        assert result is not None
        assert "limit" in result.lower()

    @patch("src.utils.email_client.time")
    def test_resets_on_new_day(self, mock_time):
        mock_time.strftime.return_value = "2026-03-06"
        for _ in range(_MAX_SENDS_PER_DAY):
            _check_rate_limit()
        # New day
        mock_time.strftime.return_value = "2026-03-07"
        assert _check_rate_limit() is None


# ── EmailClient.send ──────────────────────────────────────────────

class TestEmailClientSend:
    def _make_client(self):
        return EmailClient("test@outlook.com", "fake-password")

    def test_blocks_secrets_in_body(self):
        client = self._make_client()
        result = client.send("to@example.com", "Hi", "my api_key=secret123")
        assert result["success"] is False
        assert "secrets" in result["error"].lower()

    def test_blocks_secrets_in_subject(self):
        client = self._make_client()
        result = client.send("to@example.com", "my discord_token here", "body text")
        assert result["success"] is False
        assert "secrets" in result["error"].lower()

    @patch("src.utils.email_client.smtplib.SMTP")
    def test_send_success(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        client = self._make_client()
        result = client.send("to@example.com", "Test Subject", "Test body")
        assert result["success"] is True
        assert "sent" in result["message"].lower()
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("test@outlook.com", "fake-password")
        mock_server.send_message.assert_called_once()

    @patch("src.utils.email_client.smtplib.SMTP")
    def test_send_auth_failure(self, mock_smtp_cls):
        import smtplib
        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        client = self._make_client()
        result = client.send("to@example.com", "Test", "Body")
        assert result["success"] is False
        assert "authentication" in result["error"].lower()


# ── EmailClient.read_inbox ────────────────────────────────────────

class TestEmailClientReadInbox:
    def _make_client(self):
        return EmailClient("test@outlook.com", "fake-password")

    @patch("src.utils.email_client.imaplib.IMAP4_SSL")
    def test_read_empty_inbox(self, mock_imap_cls):
        mock_conn = MagicMock()
        mock_imap_cls.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_imap_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.login.return_value = ("OK", [])
        mock_conn.select.return_value = ("OK", [b"0"])
        mock_conn.search.return_value = ("OK", [b""])

        client = self._make_client()
        result = client.read_inbox()
        assert result["success"] is True
        assert result["count"] == 0
        assert result["messages"] == []

    @patch("src.utils.email_client.imaplib.IMAP4_SSL")
    def test_read_inbox_with_message(self, mock_imap_cls):
        # Build a simple email
        msg = email.mime.text.MIMEText("Hello from test", "plain", "utf-8")
        msg["From"] = "sender@example.com"
        msg["To"] = "test@outlook.com"
        msg["Subject"] = "Test Email"
        msg["Date"] = "Thu, 06 Mar 2026 12:00:00 +0000"
        raw_bytes = msg.as_bytes()

        mock_conn = MagicMock()
        mock_imap_cls.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_imap_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.login.return_value = ("OK", [])
        mock_conn.select.return_value = ("OK", [b"1"])
        mock_conn.search.return_value = ("OK", [b"1"])
        mock_conn.fetch.return_value = ("OK", [(b"1", raw_bytes)])

        client = self._make_client()
        result = client.read_inbox(max_count=5, unread_only=True)
        assert result["success"] is True
        assert result["count"] == 1
        assert result["messages"][0]["subject"] == "Test Email"
        assert result["messages"][0]["from"] == "sender@example.com"
        assert "Hello from test" in result["messages"][0]["preview"]


# ── EmailClient.search ────────────────────────────────────────────

class TestEmailClientSearch:
    @patch("src.utils.email_client.imaplib.IMAP4_SSL")
    def test_search_no_results(self, mock_imap_cls):
        mock_conn = MagicMock()
        mock_imap_cls.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_imap_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.login.return_value = ("OK", [])
        mock_conn.select.return_value = ("OK", [b"0"])
        mock_conn.search.return_value = ("OK", [b""])

        client = EmailClient("test@outlook.com", "fake-password")
        result = client.search('FROM "nobody@example.com"')
        assert result["success"] is True
        assert result["count"] == 0


# ── EmailClient.mark_read ─────────────────────────────────────────

class TestEmailClientMarkRead:
    @patch("src.utils.email_client.imaplib.IMAP4_SSL")
    def test_mark_read_success(self, mock_imap_cls):
        mock_conn = MagicMock()
        mock_imap_cls.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_imap_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.login.return_value = ("OK", [])
        mock_conn.select.return_value = ("OK", [b"1"])
        mock_conn.store.return_value = ("OK", [])

        client = EmailClient("test@outlook.com", "fake-password")
        result = client.mark_read("1")
        assert result["success"] is True
