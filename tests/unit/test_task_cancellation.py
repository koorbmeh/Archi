"""Unit tests for task cancellation — stop/cancel multi-step tasks.

Tests the cancellation signal (plan_executor), cancel keyword detection
(discord_bot), and the cancellation check in the step loop.
"""

import threading
import pytest

from src.core.plan_executor import (
    signal_task_cancellation,
    check_and_clear_cancellation,
)
from src.interfaces.discord_bot import _is_cancel_request


class TestCancelSignal:
    """Tests for the cancellation signal mechanism."""

    def setup_method(self):
        """Clear any stale cancellation before each test."""
        check_and_clear_cancellation()

    def test_signal_and_check(self):
        """Signalling cancellation and checking returns the message."""
        signal_task_cancellation("stop")
        result = check_and_clear_cancellation()
        assert result == "stop"

    def test_check_clears_flag(self):
        """Checking clears the flag — second check returns None."""
        signal_task_cancellation("cancel")
        assert check_and_clear_cancellation() == "cancel"
        assert check_and_clear_cancellation() is None

    def test_no_signal_returns_none(self):
        """No signal means check returns None."""
        assert check_and_clear_cancellation() is None

    def test_empty_message(self):
        """Empty message still signals cancellation."""
        signal_task_cancellation("")
        result = check_and_clear_cancellation()
        assert result == ""

    def test_thread_safety(self):
        """Signal from one thread, check from another."""
        results = [None]

        def checker():
            import time
            time.sleep(0.05)
            results[0] = check_and_clear_cancellation()

        t = threading.Thread(target=checker)
        t.start()
        signal_task_cancellation("threaded cancel")
        t.join(timeout=2)

        assert results[0] == "threaded cancel"

    def test_latest_message_wins(self):
        """Multiple signals — latest message is returned."""
        signal_task_cancellation("first")
        signal_task_cancellation("second")
        result = check_and_clear_cancellation()
        assert result == "second"


class TestIsCancelRequest:
    """Tests for _is_cancel_request() keyword detection."""

    # ── Should match (cancel keywords) ──────────────────────────────

    @pytest.mark.parametrize("msg", [
        "stop",
        "cancel",
        "nevermind",
        "never mind",
        "abort",
        "quit",
        "halt",
        "Stop",
        "CANCEL",
        "  stop  ",
    ])
    def test_exact_cancel(self, msg):
        assert _is_cancel_request(msg)

    @pytest.mark.parametrize("msg", [
        "stop that",
        "cancel that",
        "stop working",
        "cancel task",
        "forget it",
        "forget that",
        "stop the task",
        "cancel the task",
        "abort task",
    ])
    def test_cancel_phrases(self, msg):
        assert _is_cancel_request(msg)

    # ── Should NOT match (normal conversation) ──────────────────────

    @pytest.mark.parametrize("msg", [
        "hello",
        "what are you working on?",
        "research quantum computing",
        "create a file called notes.md",
        "how do I stop the server from crashing?",
        "can you stop using so many API calls and be more efficient about researching this topic please",
        "",
        "don't stop believing",
        "the bus stop is near the library",
    ])
    def test_not_cancel(self, msg):
        assert not _is_cancel_request(msg)

    def test_long_message_not_cancel(self):
        """Long messages containing cancel words should NOT match (avoid false positives)."""
        long_msg = "I think you should stop researching that and instead focus on something more useful for the project"
        assert not _is_cancel_request(long_msg)

    def test_case_insensitive(self):
        """Cancel detection should be case-insensitive."""
        assert _is_cancel_request("Stop That")
        assert _is_cancel_request("CANCEL TASK")
        assert _is_cancel_request("Nevermind")
