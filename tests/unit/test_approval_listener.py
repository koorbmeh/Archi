"""Tests for the source code approval listener in discord_bot.py.

Verifies that _check_pending_approval() correctly handles:
- Exact match responses ("yes", "no")
- Natural language responses ("No, I don't think you need to do that")
- First-word detection ("nope, skip that")
- Phrase-based detection ("go ahead and do it")
- Non-approval messages (normal conversation)
- Edge cases (long messages, ambiguous content)
"""
import sys
import os
import threading

import pytest

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# We need to import _check_pending_approval and the approval state globals.
# The function checks _pending_approval under _approval_lock, so we need
# to set up a pending approval state for most tests.
from src.interfaces.discord_bot import (
    _check_pending_approval,
    _approval_lock,
)
# Access module globals to set up pending approval state
import src.interfaces.discord_bot as _bot_module


@pytest.fixture(autouse=True)
def _setup_pending_approval():
    """Set up a pending approval so _check_pending_approval doesn't
    short-circuit to None on the lock check."""
    with _approval_lock:
        _bot_module._pending_approval = threading.Event()
        _bot_module._approval_result = False
    yield
    with _approval_lock:
        _bot_module._pending_approval = None
        _bot_module._approval_result = False


# ── Exact match: approval ────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "yes", "y", "approve", "approved", "ok", "go ahead", "go",
    "yeah", "yep", "sure", "do it", "go for it",
    "Yes", "YES", "  yes  ",  # case/whitespace
])
def test_exact_approve(msg):
    assert _check_pending_approval(msg) is True


# ── Exact match: denial ──────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "no", "n", "deny", "denied", "stop", "cancel", "nope",
    "nah", "don't", "dont",
    "No", "NO", "  no  ",  # case/whitespace
])
def test_exact_deny(msg):
    assert _check_pending_approval(msg) is False


# ── Natural language: denial ─────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "No, I don't think you need to do that",
    "No, I don't think you need to do that.",
    "nope, skip that",
    "nah that's not needed",
    "no thanks",
    "don't do that please",
    "cancel, I changed my mind",
    "stop, that's wrong",
])
def test_natural_language_deny(msg):
    assert _check_pending_approval(msg) is False


# ── Natural language: approval ───────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "yes, go ahead",
    "yeah that looks good",
    "yep, do it",
    "sure, that sounds right",
    "ok go for it",
    "go ahead and make the change",
    "approve that one",
    "sounds good to me",
])
def test_natural_language_approve(msg):
    assert _check_pending_approval(msg) is True


# ── Non-approval messages (should return None) ───────────────────────

@pytest.mark.parametrize("msg", [
    "What are you working on?",
    "Tell me about the weather",
    "I'd like you to research something",
    "How's the dream cycle going?",
    "Make it a goal to improve error handling",
])
def test_non_approval_returns_none(msg):
    assert _check_pending_approval(msg) is None


# ── Long messages should not trigger phrase check ────────────────────

def test_long_message_no_false_positive():
    """A long message that happens to contain 'no' should not trigger denial."""
    long_msg = (
        "I was reading about the project and noticed that there are "
        "no issues with the current implementation of the vector store. "
        "The embeddings look correct and the retrieval is working well."
    )
    # Message is >80 chars and doesn't start with a denial word
    assert _check_pending_approval(long_msg) is None


# ── No pending approval → always None ───────────────────────────────

def test_no_pending_returns_none():
    """When no approval is pending, everything returns None."""
    with _approval_lock:
        _bot_module._pending_approval = None
    assert _check_pending_approval("yes") is None
    assert _check_pending_approval("no") is None


def test_already_set_returns_none():
    """When the approval event is already set (answered), returns None."""
    with _approval_lock:
        _bot_module._pending_approval.set()
    assert _check_pending_approval("yes") is None
    assert _check_pending_approval("no") is None
