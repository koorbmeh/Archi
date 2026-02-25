"""Unit tests for src/core/resilience.py."""

import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.core.resilience import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitState,
    FallbackChain,
    GracefulDegradation,
    retry_with_backoff,
    safe_execute,
)


# ── CircuitBreaker ──────────────────────────────────────────────────


class TestCircuitBreakerInit:
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.success_count == 0

    def test_custom_thresholds(self):
        cb = CircuitBreaker(failure_threshold=10, recovery_timeout=300, success_threshold=5)
        assert cb.failure_threshold == 10
        assert cb.recovery_timeout == 300
        assert cb.success_threshold == 5


class TestCircuitBreakerCall:
    def test_successful_call_returns_result(self):
        cb = CircuitBreaker()
        result = cb.call(lambda: 42)
        assert result == 42

    def test_successful_call_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=5)
        cb.failure_count = 3
        cb.call(lambda: "ok")
        assert cb.failure_count == 0

    def test_failed_call_increments_failure_count(self):
        cb = CircuitBreaker(failure_threshold=5)
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("boom")))
        # Use a real function that raises
        def fail():
            raise ValueError("boom")
        with pytest.raises(ValueError):
            cb.call(fail)
        assert cb.failure_count >= 1

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=2)
        def fail():
            raise RuntimeError("fail")
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(fail)
        assert cb.state == CircuitState.OPEN

    def test_open_circuit_raises_breaker_error(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=9999)
        def fail():
            raise RuntimeError("fail")
        with pytest.raises(RuntimeError):
            cb.call(fail)
        assert cb.state == CircuitState.OPEN
        with pytest.raises(CircuitBreakerError):
            cb.call(lambda: "should not run")


class TestCircuitBreakerRecovery:
    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0)
        def fail():
            raise RuntimeError("fail")
        with pytest.raises(RuntimeError):
            cb.call(fail)
        assert cb.state == CircuitState.OPEN
        # Recovery timeout = 0 means immediate retry
        result = cb.call(lambda: "recovered")
        assert result == "recovered"
        # After success_threshold=2 (default), first success puts us in HALF_OPEN
        # Actually: first call transitions to HALF_OPEN, then succeeds → success_count=1

    def test_closes_after_success_threshold(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0, success_threshold=2)
        def fail():
            raise RuntimeError("fail")
        with pytest.raises(RuntimeError):
            cb.call(fail)
        assert cb.state == CircuitState.OPEN
        # First success in half-open
        cb.call(lambda: "ok")
        assert cb.state == CircuitState.HALF_OPEN
        # Second success closes it
        cb.call(lambda: "ok")
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0, success_threshold=3)
        def fail():
            raise RuntimeError("fail")
        with pytest.raises(RuntimeError):
            cb.call(fail)
        # Transition to half-open and fail again
        with pytest.raises(RuntimeError):
            cb.call(fail)
        assert cb.state == CircuitState.OPEN

    def test_should_attempt_reset_no_failure_time(self):
        cb = CircuitBreaker()
        assert cb._should_attempt_reset() is True

    def test_should_attempt_reset_expired(self):
        cb = CircuitBreaker(recovery_timeout=10)
        cb.last_failure_time = datetime.now() - timedelta(seconds=20)
        assert cb._should_attempt_reset() is True

    def test_should_attempt_reset_not_expired(self):
        cb = CircuitBreaker(recovery_timeout=10)
        cb.last_failure_time = datetime.now()
        assert cb._should_attempt_reset() is False


# ── retry_with_backoff ──────────────────────────────────────────────


class TestRetryWithBackoff:
    @patch("src.core.resilience.time.sleep")
    def test_succeeds_first_try(self, mock_sleep):
        @retry_with_backoff(max_retries=3)
        def ok():
            return "done"
        assert ok() == "done"
        mock_sleep.assert_not_called()

    @patch("src.core.resilience.time.sleep")
    def test_retries_then_succeeds(self, mock_sleep):
        call_count = 0
        @retry_with_backoff(max_retries=3, initial_delay=0.1)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "ok"
        assert flaky() == "ok"
        assert call_count == 3
        assert mock_sleep.call_count == 2

    @patch("src.core.resilience.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep):
        @retry_with_backoff(max_retries=2, initial_delay=0.1)
        def always_fail():
            raise RuntimeError("permanent")
        with pytest.raises(RuntimeError, match="permanent"):
            always_fail()
        assert mock_sleep.call_count == 2

    @patch("src.core.resilience.time.sleep")
    def test_respects_max_delay(self, mock_sleep):
        @retry_with_backoff(max_retries=5, initial_delay=10.0, backoff_factor=10.0, max_delay=20.0)
        def always_fail():
            raise RuntimeError("fail")
        with pytest.raises(RuntimeError):
            always_fail()
        # Delays should be capped at max_delay=20
        for call_args in mock_sleep.call_args_list:
            assert call_args[0][0] <= 20.0

    @patch("src.core.resilience.time.sleep")
    def test_only_catches_specified_exceptions(self, mock_sleep):
        @retry_with_backoff(max_retries=3, exceptions=(ValueError,))
        def type_error():
            raise TypeError("wrong type")
        with pytest.raises(TypeError):
            type_error()
        mock_sleep.assert_not_called()

    @patch("src.core.resilience.time.sleep")
    def test_preserves_function_name(self, mock_sleep):
        @retry_with_backoff()
        def my_special_func():
            return 1
        assert my_special_func.__name__ == "my_special_func"


# ── FallbackChain ───────────────────────────────────────────────────


class TestFallbackChain:
    def test_first_strategy_succeeds(self):
        chain = FallbackChain([
            {"name": "primary", "func": lambda: {"success": True, "data": "ok"}},
            {"name": "backup", "func": lambda: {"success": True, "data": "fallback"}},
        ])
        result = chain.execute()
        assert result["success"] is True
        assert result["strategy_used"] == "primary"
        assert result["attempts"] == 1

    def test_falls_back_on_failure(self):
        chain = FallbackChain([
            {"name": "primary", "func": lambda: {"success": False, "error": "down"}},
            {"name": "backup", "func": lambda: {"success": True, "data": "ok"}},
        ])
        result = chain.execute()
        assert result["success"] is True
        assert result["strategy_used"] == "backup"
        assert result["attempts"] == 2

    def test_falls_back_on_exception(self):
        def explode():
            raise ConnectionError("timeout")
        chain = FallbackChain([
            {"name": "primary", "func": explode},
            {"name": "backup", "func": lambda: {"success": True}},
        ])
        result = chain.execute()
        assert result["success"] is True
        assert result["strategy_used"] == "backup"

    def test_all_fail_returns_errors(self):
        chain = FallbackChain([
            {"name": "a", "func": lambda: {"success": False, "error": "err1"}},
            {"name": "b", "func": lambda: {"success": False, "error": "err2"}},
        ])
        result = chain.execute()
        assert result["success"] is False
        assert len(result["errors"]) == 2
        assert result["attempts"] == 2

    def test_passes_args_and_kwargs(self):
        def add(x, y, z=0):
            return {"success": True, "total": x + y + z}
        chain = FallbackChain([
            {"name": "add", "func": add, "args": [1, 2], "kwargs": {"z": 3}},
        ])
        result = chain.execute()
        assert result["success"] is True
        assert result["result"]["total"] == 6

    def test_non_dict_result_treated_as_failure(self):
        chain = FallbackChain([
            {"name": "string_return", "func": lambda: "not a dict"},
            {"name": "backup", "func": lambda: {"success": True}},
        ])
        result = chain.execute()
        assert result["strategy_used"] == "backup"


# ── GracefulDegradation ────────────────────────────────────────────


class TestGracefulDegradation:
    def test_simple_response(self):
        result = GracefulDegradation.simple_response("what is AI?")
        assert result["success"] is True
        assert "what is AI?" in result["text"]
        assert result["provider"] == "degraded"
        assert result["cost_usd"] == 0.0

    def test_cached_only_hit(self):
        cache = MagicMock()
        cache.get.return_value = {"text": "cached answer", "cost_usd": 0.0}
        result = GracefulDegradation.cached_only_response("query", cache)
        assert result is not None
        assert result["success"] is True
        assert result["provider"] == "cache_only"
        assert result["degraded"] is True

    def test_cached_only_miss(self):
        cache = MagicMock()
        cache.get.return_value = None
        result = GracefulDegradation.cached_only_response("query", cache)
        assert result is None

    def test_template_match(self):
        templates = {"hello": "Hi there!", "help": "I can assist."}
        result = GracefulDegradation.template_response("Hello world", templates)
        assert result is not None
        assert result["text"] == "Hi there!"
        assert result["provider"] == "template"

    def test_template_no_match(self):
        templates = {"hello": "Hi!", "help": "Assist."}
        result = GracefulDegradation.template_response("goodbye", templates)
        assert result is None


# ── safe_execute ────────────────────────────────────────────────────


class TestSafeExecute:
    def test_returns_result_on_success(self):
        assert safe_execute(lambda: 42) == 42

    def test_returns_default_on_error(self):
        def fail():
            raise RuntimeError("boom")
        assert safe_execute(fail, default="fallback") == "fallback"

    def test_logs_error_by_default(self):
        def fail():
            raise RuntimeError("boom")
        with patch("src.core.resilience.logger") as mock_log:
            safe_execute(fail)
            mock_log.error.assert_called_once()

    def test_suppresses_log_when_disabled(self):
        def fail():
            raise RuntimeError("boom")
        with patch("src.core.resilience.logger") as mock_log:
            safe_execute(fail, log_errors=False)
            mock_log.error.assert_not_called()
