"""
Unit tests for _handle_config_commands() in discord_bot.py.

Tests the extracted config command handler covering model switching,
retry, status queries, image model switching, dream cycle management,
and project commands.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest
from src.interfaces.discord_bot import _handle_config_commands


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_message(author_id=123):
    """Build a mock Discord message."""
    msg = AsyncMock()
    msg.author = MagicMock()
    msg.author.id = author_id
    msg.reply = AsyncMock()
    msg.channel = AsyncMock()
    msg.channel.send = AsyncMock()
    return msg


class TestConfigCommandsModelSwitch:
    """Model switching via 'switch to X' / 'use X'."""

    @patch("src.interfaces.discord_bot._parse_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._parse_image_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._parse_dream_cycle_interval", return_value=None)
    @patch("src.interfaces.discord_bot._parse_project_command", return_value=None)
    def test_not_a_config_command(self, *mocks):
        msg = _make_message()
        handled, new_content = _run(_handle_config_commands(msg, "hello world"))
        assert handled is False
        assert new_content is None

    @patch("src.interfaces.discord_bot._log_convo")
    @patch("src.interfaces.discord_bot._get_router")
    @patch("src.interfaces.discord_bot._parse_model_switch",
           return_value=("grok", False, 0))
    def test_model_switch_permanent(self, mock_parse, mock_router, mock_log):
        router = MagicMock()
        router.switch_model.return_value = {"message": "Switched to grok", "display": "grok"}
        mock_router.return_value = router
        msg = _make_message()
        handled, new_content = _run(_handle_config_commands(msg, "switch to grok"))
        assert handled is True
        assert new_content is None
        msg.reply.assert_awaited_once()
        mock_log.assert_called_once()

    @patch("src.interfaces.discord_bot._log_convo")
    @patch("src.interfaces.discord_bot._last_user_message", {123: "what is AI?"})
    @patch("src.interfaces.discord_bot._get_router")
    @patch("src.interfaces.discord_bot._parse_model_switch",
           return_value=("grok", True, 0))
    def test_model_switch_with_retry(self, mock_parse, mock_router, mock_log):
        router = MagicMock()
        router.switch_model.return_value = {"message": "Switched", "display": "grok"}
        mock_router.return_value = router
        msg = _make_message()
        handled, new_content = _run(_handle_config_commands(msg, "switch to grok and retry"))
        assert handled is False
        assert new_content == "what is AI?"

    @patch("src.interfaces.discord_bot._log_convo")
    @patch("src.interfaces.discord_bot._get_router", return_value=None)
    @patch("src.interfaces.discord_bot._parse_model_switch",
           return_value=("grok", False, 0))
    def test_model_switch_no_router(self, mock_parse, mock_router, mock_log):
        msg = _make_message()
        handled, new_content = _run(_handle_config_commands(msg, "switch to grok"))
        assert handled is True
        msg.reply.assert_awaited_once()


class TestConfigCommandsRetry:
    """'try again' / 'retry' without model switch."""

    @patch("src.interfaces.discord_bot._parse_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._last_user_message", {123: "previous question"})
    def test_retry_with_previous_message(self, mock_parse):
        msg = _make_message()
        handled, new_content = _run(_handle_config_commands(msg, "try again"))
        assert handled is False
        assert new_content == "previous question"

    @patch("src.interfaces.discord_bot._log_convo")
    @patch("src.interfaces.discord_bot._parse_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._last_user_message", {})
    def test_retry_no_previous_message(self, mock_parse, mock_log):
        msg = _make_message()
        handled, new_content = _run(_handle_config_commands(msg, "retry"))
        assert handled is True
        assert new_content is None
        msg.reply.assert_awaited_once()


class TestConfigCommandsStatusQuery:
    """Status/model query commands."""

    @patch("src.interfaces.discord_bot._log_convo")
    @patch("src.interfaces.discord_bot._parse_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._get_router")
    def test_status_query_with_router(self, mock_router, mock_parse, mock_log):
        router = MagicMock()
        router.get_active_model_info.return_value = {
            "display": "grok-4.1-fast", "mode": "auto", "provider": "xai",
        }
        router.get_provider_health.return_value = {}
        mock_router.return_value = router
        with patch("src.tools.image_gen.get_default_image_model_name", return_value="auto"), \
             patch("src.tools.image_gen.get_image_model_aliases", return_value={}):
            msg = _make_message()
            handled, new_content = _run(_handle_config_commands(msg, "status"))
            assert handled is True
            msg.reply.assert_awaited_once()

    @patch("src.interfaces.discord_bot._log_convo")
    @patch("src.interfaces.discord_bot._parse_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._get_router", return_value=None)
    def test_status_query_no_router(self, mock_router, mock_parse, mock_log):
        msg = _make_message()
        handled, new_content = _run(_handle_config_commands(msg, "what model"))
        assert handled is True


class TestConfigCommandsDreamCycle:
    """Dream cycle interval set/query."""

    @patch("src.interfaces.discord_bot._log_convo")
    @patch("src.interfaces.discord_bot._parse_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._parse_image_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._parse_dream_cycle_interval", return_value=600)
    @patch("src.interfaces.discord_bot._heartbeat")
    def test_set_heartbeat(self, mock_dc, mock_parse_dc, *mocks):
        mock_dc.set_idle_threshold.return_value = "Set to 10 minutes"
        msg = _make_message()
        handled, new_content = _run(
            _handle_config_commands(msg, "set dream cycle to 10 minutes"))
        assert handled is True
        msg.reply.assert_awaited_once()

    @patch("src.interfaces.discord_bot._log_convo")
    @patch("src.interfaces.discord_bot._parse_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._parse_image_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._parse_dream_cycle_interval", return_value=None)
    @patch("src.interfaces.discord_bot._parse_project_command", return_value=None)
    @patch("src.interfaces.discord_bot._heartbeat")
    def test_heartbeat_status_query(self, mock_dc, *mocks):
        mock_dc.get_idle_threshold.return_value = 900
        msg = _make_message()
        handled, new_content = _run(
            _handle_config_commands(msg, "dream cycle?"))
        assert handled is True
        msg.reply.assert_awaited_once()


class TestConfigCommandsProjectCommand:
    """Project management commands."""

    @patch("src.interfaces.discord_bot._log_convo")
    @patch("src.interfaces.discord_bot._parse_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._parse_image_model_switch", return_value=None)
    @patch("src.interfaces.discord_bot._parse_dream_cycle_interval", return_value=None)
    @patch("src.interfaces.discord_bot._parse_project_command",
           return_value=("list", None))
    @patch("src.interfaces.discord_bot._handle_project_command",
           return_value="No active projects.")
    def test_project_list(self, *mocks):
        msg = _make_message()
        handled, new_content = _run(
            _handle_config_commands(msg, "list projects"))
        assert handled is True
        msg.reply.assert_awaited_once()
