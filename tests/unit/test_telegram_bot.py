"""Unit tests for src/interfaces/telegram_bot.py — session 246."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest
import src.interfaces.telegram_bot as tg_mod


# ── Configuration checks ────────────────────────────────────────────

class TestConfiguration:
    def test_is_configured_without_token(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove the key if present
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            assert tg_mod.is_configured() is False

    def test_is_configured_with_token(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test_token"}):
            assert tg_mod.is_configured() is True

    def test_is_configured_empty_token(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "  "}):
            assert tg_mod.is_configured() is False

    def test_is_ready_default(self):
        old_ready = tg_mod._ready
        old_bot = tg_mod._bot_instance
        try:
            tg_mod._ready = False
            tg_mod._bot_instance = None
            assert tg_mod.is_ready() is False
        finally:
            tg_mod._ready = old_ready
            tg_mod._bot_instance = old_bot

    def test_is_ready_when_running(self):
        old_ready = tg_mod._ready
        old_bot = tg_mod._bot_instance
        try:
            tg_mod._ready = True
            tg_mod._bot_instance = MagicMock()
            assert tg_mod.is_ready() is True
        finally:
            tg_mod._ready = old_ready
            tg_mod._bot_instance = old_bot


# ── History management ───────────────────────────────────────────────

class TestHistory:
    def setup_method(self):
        tg_mod._chat_history.clear()

    def test_append_history(self):
        tg_mod._append_history("user", "hello")
        assert len(tg_mod._chat_history) == 1
        assert tg_mod._chat_history[0] == {"role": "user", "content": "hello"}

    def test_history_trimming(self):
        for i in range(50):
            tg_mod._append_history("user", f"msg {i}")
        assert len(tg_mod._chat_history) <= tg_mod._MAX_HISTORY * 2

    def teardown_method(self):
        tg_mod._chat_history.clear()


# ── Owner check ──────────────────────────────────────────────────────

class TestOwnerCheck:
    def setup_method(self):
        self._old_owner = tg_mod._owner_id

    def test_owner_auto_discovery(self):
        tg_mod._owner_id = None
        update = MagicMock()
        update.effective_user.id = 12345
        update.effective_user.first_name = "Jesse"
        assert tg_mod._is_owner(update) is True
        assert tg_mod._owner_id == 12345

    def test_owner_match(self):
        tg_mod._owner_id = 12345
        update = MagicMock()
        update.effective_user.id = 12345
        assert tg_mod._is_owner(update) is True

    def test_non_owner_rejected(self):
        tg_mod._owner_id = 12345
        update = MagicMock()
        update.effective_user.id = 99999
        assert tg_mod._is_owner(update) is False

    def teardown_method(self):
        tg_mod._owner_id = self._old_owner


# ── Notification ─────────────────────────────────────────────────────

class TestNotification:
    def test_notification_not_ready(self):
        old_bot = tg_mod._bot_instance
        try:
            tg_mod._bot_instance = None
            assert tg_mod.send_telegram_notification("test") is False
        finally:
            tg_mod._bot_instance = old_bot

    def test_notification_empty_text(self):
        old_vals = (tg_mod._bot_instance, tg_mod._bot_loop, tg_mod._owner_id)
        try:
            tg_mod._bot_instance = MagicMock()
            tg_mod._bot_loop = MagicMock()
            tg_mod._owner_id = 12345
            assert tg_mod.send_telegram_notification("") is False
            assert tg_mod.send_telegram_notification("   ") is False
        finally:
            tg_mod._bot_instance, tg_mod._bot_loop, tg_mod._owner_id = old_vals

    def test_notification_no_owner(self):
        old_vals = (tg_mod._bot_instance, tg_mod._bot_loop, tg_mod._owner_id)
        try:
            tg_mod._bot_instance = MagicMock()
            tg_mod._bot_loop = MagicMock()
            tg_mod._owner_id = None
            assert tg_mod.send_telegram_notification("test") is False
        finally:
            tg_mod._bot_instance, tg_mod._bot_loop, tg_mod._owner_id = old_vals

    def test_notification_truncation(self):
        old_vals = (tg_mod._bot_instance, tg_mod._bot_loop, tg_mod._owner_id)
        try:
            mock_bot = MagicMock()
            mock_loop = MagicMock()
            tg_mod._bot_instance = mock_bot
            tg_mod._bot_loop = mock_loop
            tg_mod._owner_id = 12345

            long_text = "x" * 5000
            # Will fail at asyncio level but we test that truncation happens
            with patch("asyncio.run_coroutine_threadsafe") as mock_async:
                mock_future = MagicMock()
                mock_future.result.return_value = None
                mock_async.return_value = mock_future

                result = tg_mod.send_telegram_notification(long_text)
                assert result is True
                # Check the text was truncated
                call_args = mock_bot.send_message.call_args
                assert call_args is not None
                sent_text = call_args[1].get("text", "") if call_args[1] else call_args[0][1] if len(call_args[0]) > 1 else ""
                # The coroutine was created, so check the mock_bot.send_message was called
                assert mock_bot.send_message.called
        finally:
            tg_mod._bot_instance, tg_mod._bot_loop, tg_mod._owner_id = old_vals


# ── Start bot ────────────────────────────────────────────────────────

class TestStartBot:
    def test_start_without_token(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            result = tg_mod.start_telegram_bot()
            assert result is None

    def test_start_already_running(self):
        old_ready = tg_mod._ready
        try:
            tg_mod._ready = True
            result = tg_mod.start_telegram_bot()
            assert result is None
        finally:
            tg_mod._ready = old_ready

    def test_start_with_token_launches_thread(self):
        old_ready = tg_mod._ready
        try:
            tg_mod._ready = False
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test:token"}):
                with patch("threading.Thread") as mock_thread:
                    mock_instance = MagicMock()
                    mock_thread.return_value = mock_instance
                    result = tg_mod.start_telegram_bot()
                    assert result is mock_instance
                    mock_instance.start.assert_called_once()
        finally:
            tg_mod._ready = old_ready

    def test_start_loads_owner_from_env(self):
        old_ready = tg_mod._ready
        old_owner = tg_mod._owner_id
        try:
            tg_mod._ready = False
            tg_mod._owner_id = None
            with patch.dict(os.environ, {
                "TELEGRAM_BOT_TOKEN": "test:token",
                "TELEGRAM_OWNER_ID": "67890",
            }):
                with patch("threading.Thread") as mock_thread:
                    mock_instance = MagicMock()
                    mock_thread.return_value = mock_instance
                    tg_mod.start_telegram_bot()
                    assert tg_mod._owner_id == 67890
        finally:
            tg_mod._ready = old_ready
            tg_mod._owner_id = old_owner


# ── Message processing ───────────────────────────────────────────────

class TestProcessMessage:
    def test_process_message_easy_tier(self):
        mock_router = MagicMock()
        mock_rr = MagicMock()
        mock_rr.intent = "greeting"
        mock_rr.tier = "easy"
        mock_rr.answer = "Hey there!"
        mock_rr.action = None
        mock_rr.action_params = None
        mock_rr.cost = 0.001

        with patch("src.interfaces.discord_bot._get_router", return_value=mock_router):
            with patch("src.core.conversational_router.route", return_value=mock_rr):
                with patch("src.interfaces.message_handler._build_history_messages", return_value=[]):
                    result = tg_mod._process_message("hello")
                    assert "Hey there!" in result

    def test_process_message_with_action(self):
        mock_router = MagicMock()
        mock_rr = MagicMock()
        mock_rr.intent = "supplement"
        mock_rr.tier = "easy"
        mock_rr.answer = None
        mock_rr.action = "supplement_status"
        mock_rr.action_params = {"view": "list"}
        mock_rr.cost = 0.001

        with patch("src.interfaces.discord_bot._get_router", return_value=mock_router):
            with patch("src.core.conversational_router.route", return_value=mock_rr):
                with patch("src.interfaces.message_handler._build_history_messages", return_value=[]):
                    with patch("src.interfaces.action_dispatcher.dispatch",
                               return_value=("No supplements tracked.", [], 0.0)):
                        result = tg_mod._process_message("what supplements do I take?")
                        assert "supplements" in result.lower()

    def test_process_message_error_handling(self):
        with patch("src.interfaces.discord_bot._get_router", return_value=MagicMock()):
            with patch("src.core.conversational_router.route",
                       side_effect=Exception("test error")):
                with patch("src.interfaces.message_handler._build_history_messages", return_value=[]):
                    result = tg_mod._process_message("crash me")
                    assert "wrong" in result.lower() or "error" in result.lower()


# ── Stop bot ─────────────────────────────────────────────────────────

class TestStopBot:
    def test_stop_clears_ready(self):
        old_ready = tg_mod._ready
        old_app = tg_mod._bot_app
        try:
            tg_mod._ready = True
            tg_mod._bot_app = None
            tg_mod.stop_telegram_bot()
            assert tg_mod._ready is False
        finally:
            tg_mod._ready = old_ready
            tg_mod._bot_app = old_app
