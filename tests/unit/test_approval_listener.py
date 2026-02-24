"""Tests for the source approval mechanism in discord_bot.py.

Verifies that _resolve_approval() and _has_pending_approval() work correctly
for the file-modification approval flow.

Note: The original _check_pending_approval() function was removed in the
router refactor. Approval detection now goes through the conversational
router classifier. These tests cover the remaining approval state helpers.
"""
import threading

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
    yield
    with _approval_lock:
        _bot_module._pending_approval = None
        _bot_module._approval_result = False


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
