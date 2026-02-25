"""Tests for the source approval mechanism in discord_bot.py.

Verifies that _resolve_approval() and _has_pending_approval() work correctly
for the file-modification approval flow, including reaction-based approval
via rich embeds (session 128).

Note: The original _check_pending_approval() function was removed in the
router refactor. Approval detection now goes through the conversational
router classifier. These tests cover the remaining approval state helpers.
"""
import threading
from unittest.mock import MagicMock, patch

import pytest

try:
    from src.interfaces.discord_bot import (
        _approval_lock,
        _has_pending_approval,
        _resolve_approval,
    )
    import src.interfaces.discord_bot as _bot_module
except ImportError as _exc:
    pytest.skip(f"discord_bot import chain unavailable: {_exc}", allow_module_level=True)
    _approval_lock = _has_pending_approval = _resolve_approval = _bot_module = None


@pytest.fixture(autouse=True)
def _clean_approval_state():
    """Ensure no stale approval state bleeds between tests."""
    with _approval_lock:
        _bot_module._pending_approval = None
        _bot_module._approval_result = False
        _bot_module._approval_message_id = None
    yield
    with _approval_lock:
        _bot_module._pending_approval = None
        _bot_module._approval_result = False
        _bot_module._approval_message_id = None


class TestHasPendingApproval:
    """Tests for _has_pending_approval()."""

    def test_no_pending(self):
        """No pending approval → False."""
        assert _has_pending_approval() is False

    def test_with_pending(self):
        """Active pending approval → True."""
        with _approval_lock:
            _bot_module._pending_approval = threading.Event()
        assert _has_pending_approval() is True

    def test_already_answered(self):
        """Set (answered) event → False."""
        ev = threading.Event()
        ev.set()
        with _approval_lock:
            _bot_module._pending_approval = ev
        assert _has_pending_approval() is False


class TestResolveApproval:
    """Tests for _resolve_approval()."""

    def test_approve_sets_result_true(self):
        """Approving sets _approval_result and signals the event."""
        ev = threading.Event()
        with _approval_lock:
            _bot_module._pending_approval = ev
            _bot_module._approval_result = False
        _resolve_approval(True)
        assert ev.is_set()
        assert _bot_module._approval_result is True

    def test_deny_sets_result_false(self):
        """Denying sets _approval_result=False and signals the event."""
        ev = threading.Event()
        with _approval_lock:
            _bot_module._pending_approval = ev
            _bot_module._approval_result = False
        _resolve_approval(False)
        assert ev.is_set()
        assert _bot_module._approval_result is False

    def test_resolve_noop_when_none(self):
        """Resolving with no pending approval is a no-op."""
        _resolve_approval(True)  # should not raise

    def test_resolve_noop_when_already_set(self):
        """Resolving an already-set event is a no-op."""
        ev = threading.Event()
        ev.set()
        with _approval_lock:
            _bot_module._pending_approval = ev
            _bot_module._approval_result = False
        _resolve_approval(True)
        # result should remain False since it was already answered
        assert _bot_module._approval_result is False


class TestApprovalMessageId:
    """Tests for reaction-based approval via _approval_message_id (session 128)."""

    def test_approval_message_id_starts_none(self):
        """_approval_message_id starts as None."""
        assert _bot_module._approval_message_id is None

    def test_approval_message_id_set_and_cleared(self):
        """Setting and clearing _approval_message_id works."""
        with _approval_lock:
            _bot_module._approval_message_id = 12345
        assert _bot_module._approval_message_id == 12345
        with _approval_lock:
            _bot_module._approval_message_id = None
        assert _bot_module._approval_message_id is None

    def test_resolve_via_matching_message_id(self):
        """Resolving approval works when message IDs match."""
        ev = threading.Event()
        with _approval_lock:
            _bot_module._pending_approval = ev
            _bot_module._approval_result = False
            _bot_module._approval_message_id = 99999

        # Simulate what on_raw_reaction_add does for ✅:
        # check under lock, release, then resolve
        is_match = False
        with _approval_lock:
            is_match = (
                _bot_module._approval_message_id is not None
                and 99999 == _bot_module._approval_message_id
            )
        if is_match:
            _resolve_approval(True)

        assert ev.is_set()
        assert _bot_module._approval_result is True

    def test_resolve_ignores_wrong_message_id(self):
        """Reactions on unrelated messages don't resolve the approval."""
        ev = threading.Event()
        with _approval_lock:
            _bot_module._pending_approval = ev
            _bot_module._approval_result = False
            _bot_module._approval_message_id = 99999

        # Simulate reaction on a different message
        is_match = False
        with _approval_lock:
            is_match = (
                _bot_module._approval_message_id is not None
                and 11111 == _bot_module._approval_message_id
            )
        if is_match:
            _resolve_approval(True)

        assert not ev.is_set()
        assert _bot_module._approval_result is False

    def test_deny_via_reaction(self):
        """❌ reaction resolves approval as denied."""
        ev = threading.Event()
        with _approval_lock:
            _bot_module._pending_approval = ev
            _bot_module._approval_result = False
            _bot_module._approval_message_id = 55555

        is_match = False
        with _approval_lock:
            is_match = (
                _bot_module._approval_message_id is not None
                and 55555 == _bot_module._approval_message_id
            )
        if is_match:
            _resolve_approval(False)

        assert ev.is_set()
        assert _bot_module._approval_result is False


class TestSendApprovalEmbed:
    """Tests for _send_approval_embed helper (session 128)."""

    @pytest.fixture(autouse=True)
    def _mock_discord(self):
        """Ensure `import discord` inside _send_approval_embed succeeds."""
        import sys
        fake_discord = MagicMock()
        already = "discord" in sys.modules
        if not already:
            sys.modules["discord"] = fake_discord
        yield
        if not already:
            sys.modules.pop("discord", None)

    def test_returns_none_when_discord_not_ready(self):
        """Returns None if _owner_dm_channel or _bot_loop is not set."""
        with patch.object(_bot_module, "_owner_dm_channel", None), \
             patch.object(_bot_module, "_bot_loop", None):
            result = _bot_module._send_approval_embed(
                "write_source", "src/tools/foo.py", "test task", 300,
            )
            assert result is None

    def test_returns_none_on_exception(self):
        """Returns None when asyncio scheduling raises."""
        with patch.object(_bot_module, "_owner_dm_channel", MagicMock()), \
             patch.object(_bot_module, "_bot_loop", MagicMock()), \
             patch("asyncio.run_coroutine_threadsafe", side_effect=RuntimeError("test")):
            result = _bot_module._send_approval_embed(
                "write_source", "src/tools/foo.py", "test task", 300,
            )
            assert result is None


# -- Shared approval flow helpers (session 131) --

class TestSetupApprovalGate:
    """Tests for _setup_approval_gate helper."""

    def test_sets_up_fresh_gate(self):
        """Creates a fresh Event and resets state."""
        assert _bot_module._setup_approval_gate() is True
        assert _bot_module._pending_approval is not None
        assert not _bot_module._pending_approval.is_set()
        assert _bot_module._approval_result is False
        assert _bot_module._approval_message_id is None

    def test_rejects_when_pending_and_check_enabled(self):
        """Returns False if another approval is pending and check_pending=True."""
        with _approval_lock:
            _bot_module._pending_approval = threading.Event()
        assert _bot_module._setup_approval_gate(check_pending=True) is False

    def test_allows_when_pending_and_check_disabled(self):
        """Overwrites pending approval when check_pending=False."""
        old_ev = threading.Event()
        with _approval_lock:
            _bot_module._pending_approval = old_ev
        assert _bot_module._setup_approval_gate(check_pending=False) is True
        assert _bot_module._pending_approval is not old_ev

    def test_allows_when_previous_is_set(self):
        """Allows setup when the previous approval was already answered."""
        ev = threading.Event()
        ev.set()
        with _approval_lock:
            _bot_module._pending_approval = ev
        assert _bot_module._setup_approval_gate(check_pending=True) is True


class TestSendEmbedOrFallback:
    """Tests for _send_embed_or_fallback helper."""

    def test_stores_msg_id_when_provided(self):
        """Sets _approval_message_id when msg_id is not None."""
        _bot_module._setup_approval_gate(check_pending=False)
        result = _bot_module._send_embed_or_fallback(12345, "fallback text")
        assert result is True
        assert _bot_module._approval_message_id == 12345

    def test_sends_fallback_when_no_msg_id(self):
        """Sends fallback text when msg_id is None."""
        _bot_module._setup_approval_gate(check_pending=False)
        with patch.object(_bot_module, "send_notification", return_value=True) as mock_send:
            result = _bot_module._send_embed_or_fallback(None, "fallback text")
        assert result is True
        mock_send.assert_called_once_with("fallback text")

    def test_returns_false_on_total_failure(self):
        """Returns False when both embed and fallback fail."""
        _bot_module._setup_approval_gate(check_pending=False)
        with patch.object(_bot_module, "send_notification", return_value=False):
            result = _bot_module._send_embed_or_fallback(None, "fallback text")
        assert result is False
        assert _bot_module._pending_approval is None


class TestCollectApprovalResult:
    """Tests for _collect_approval_result helper."""

    def test_returns_approved_when_responded(self):
        """Returns (True, True) when user approves before timeout."""
        _bot_module._setup_approval_gate(check_pending=False)
        # Simulate user approving
        with _approval_lock:
            _bot_module._approval_result = True
            _bot_module._pending_approval.set()
        responded, approved = _bot_module._collect_approval_result(timeout=1.0)
        assert responded is True
        assert approved is True
        assert _bot_module._pending_approval is None  # cleaned up

    def test_returns_not_responded_on_timeout(self):
        """Returns (False, False) on timeout."""
        _bot_module._setup_approval_gate(check_pending=False)
        responded, approved = _bot_module._collect_approval_result(timeout=0.01)
        assert responded is False
        assert approved is False
        assert _bot_module._pending_approval is None  # cleaned up
