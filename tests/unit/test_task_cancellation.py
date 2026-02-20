"""Unit tests for task cancellation — stop/cancel multi-step tasks.

Tests the cancellation signal (plan_executor) including the sticky
shutdown mode introduced in session 59.
"""

import threading
import pytest

from src.core.plan_executor import (
    signal_task_cancellation,
    check_and_clear_cancellation,
    clear_shutdown_flag,
)


class TestCancelSignal:
    """Tests for the cancellation signal mechanism."""

    def setup_method(self):
        """Clear any stale cancellation before each test."""
        clear_shutdown_flag()

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


class TestStickyShutdown:
    """Tests for the sticky shutdown mode (session 59 fix).

    When signal_task_cancellation is called with 'shutdown' or
    'service_shutdown', the flag should NOT be cleared on first read,
    so all concurrent PlanExecutors see it.
    """

    def setup_method(self):
        clear_shutdown_flag()

    def test_shutdown_is_sticky(self):
        """Shutdown flag survives multiple reads."""
        signal_task_cancellation("shutdown")
        assert check_and_clear_cancellation() == "shutdown"
        assert check_and_clear_cancellation() == "shutdown"
        assert check_and_clear_cancellation() == "shutdown"

    def test_service_shutdown_is_sticky(self):
        """service_shutdown flag also survives multiple reads."""
        signal_task_cancellation("service_shutdown")
        assert check_and_clear_cancellation() == "service_shutdown"
        assert check_and_clear_cancellation() == "service_shutdown"

    def test_clear_shutdown_flag_clears_sticky(self):
        """clear_shutdown_flag() resets the sticky flag."""
        signal_task_cancellation("shutdown")
        assert check_and_clear_cancellation() == "shutdown"
        clear_shutdown_flag()
        assert check_and_clear_cancellation() is None

    def test_normal_cancel_not_sticky(self):
        """Non-shutdown cancellation is still cleared on first read."""
        signal_task_cancellation("user requested stop")
        assert check_and_clear_cancellation() == "user requested stop"
        assert check_and_clear_cancellation() is None

    def test_concurrent_readers_all_see_shutdown(self):
        """Multiple threads all see the shutdown flag."""
        signal_task_cancellation("service_shutdown")
        results = [None] * 5

        def reader(idx):
            results[idx] = check_and_clear_cancellation()

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2)

        assert all(r == "service_shutdown" for r in results), f"results: {results}"
        clear_shutdown_flag()
