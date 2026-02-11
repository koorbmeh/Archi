"""
Resilience Layer - Robust error handling and recovery.

Provides retry logic, circuit breakers, fallback strategies,
and graceful degradation for reliable operation.
"""

import functools
import logging
import time
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, block requests
    HALF_OPEN = "half_open"  # Testing if recovered


class CircuitBreakerError(Exception):
    """Circuit breaker is open."""

    pass


class CircuitBreaker:
    """
    Circuit breaker pattern for failing services.

    Prevents cascading failures by stopping calls to
    failing services temporarily.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        success_threshold: int = 2,
    ) -> None:
        """
        Args:
            failure_threshold: Failures before opening circuit
            recovery_timeout: Seconds before trying again
            success_threshold: Successes needed to close circuit
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute function through circuit breaker."""
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                logger.info("Circuit breaker: Attempting reset (HALF_OPEN)")
                self.state = CircuitState.HALF_OPEN
            else:
                raise CircuitBreakerError(
                    f"Circuit breaker OPEN (failed {self.failure_count} times)"
                )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result

        except Exception as e:
            self._on_failure()
            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time passed to try again."""
        if not self.last_failure_time:
            return True

        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout

    def _on_success(self) -> None:
        """Handle successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1

            if self.success_count >= self.success_threshold:
                logger.info("Circuit breaker: CLOSED (recovered)")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0

        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def _on_failure(self) -> None:
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        self.success_count = 0

        if self.failure_count >= self.failure_threshold:
            logger.warning(
                "Circuit breaker: OPEN (threshold %d reached)",
                self.failure_threshold,
            )
            self.state = CircuitState.OPEN


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    exceptions: tuple = (Exception,),
) -> Callable[..., Callable[..., Any]]:
    """
    Decorator for retry with exponential backoff.

    Args:
        max_retries: Maximum retry attempts
        initial_delay: Initial delay in seconds
        backoff_factor: Multiply delay by this each retry
        max_delay: Maximum delay between retries
        exceptions: Exception types to catch and retry

    Usage:
        @retry_with_backoff(max_retries=3)
        def unstable_function():
            pass
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            last_exception: Optional[Exception] = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except exceptions as e:
                    last_exception = e

                    if attempt == max_retries:
                        logger.error(
                            "%s failed after %d retries: %s",
                            func.__name__,
                            max_retries,
                            e,
                        )
                        raise

                    logger.warning(
                        "%s attempt %d failed: %s. Retrying in %.1fs...",
                        func.__name__,
                        attempt + 1,
                        e,
                        delay,
                    )

                    time.sleep(delay)
                    delay = min(delay * backoff_factor, max_delay)

            if last_exception:
                raise last_exception
            return None

        return wrapper

    return decorator


class FallbackChain:
    """
    Chain of fallback strategies.

    Tries primary method, falls back to alternatives on failure.
    """

    def __init__(self, strategies: List[Dict[str, Any]]) -> None:
        """
        Args:
            strategies: List of dicts with 'name', 'func', 'args', 'kwargs'
        """
        self.strategies = strategies

    def execute(self) -> Dict[str, Any]:
        """Execute with fallback chain."""
        errors: List[str] = []

        for i, strategy in enumerate(self.strategies):
            name = strategy["name"]
            func = strategy["func"]
            args = strategy.get("args", [])
            kwargs = strategy.get("kwargs", {})

            logger.info(
                "Trying strategy %d/%d: %s", i + 1, len(self.strategies), name
            )

            try:
                result = func(*args, **kwargs)

                if isinstance(result, dict) and result.get("success"):
                    logger.info("Strategy '%s' succeeded", name)
                    return {
                        "success": True,
                        "result": result,
                        "strategy_used": name,
                        "attempts": i + 1,
                    }
                else:
                    error = (
                        result.get("error", "Unknown error")
                        if isinstance(result, dict)
                        else "Unknown error"
                    )
                    errors.append(f"{name}: {error}")
                    logger.warning("Strategy '%s' failed: %s", name, error)

            except Exception as e:
                errors.append(f"{name}: {str(e)}")
                logger.warning("Strategy '%s' raised exception: %s", name, e)

        return {
            "success": False,
            "error": "All strategies failed",
            "errors": errors,
            "attempts": len(self.strategies),
        }


class GracefulDegradation:
    """
    Graceful degradation strategies.

    Provides reduced functionality when full features unavailable.
    """

    @staticmethod
    def simple_response(query: str) -> Dict[str, Any]:
        """Simple canned response when AI unavailable."""
        return {
            "success": True,
            "text": (
                f"I understand you asked: '{query}'\n\n"
                "I'm currently running in degraded mode with limited AI capabilities. "
                "I can still help with basic tasks like file operations and system commands. "
                "Full AI features will be restored shortly."
            ),
            "provider": "degraded",
            "cost_usd": 0.0,
        }

    @staticmethod
    def cached_only_response(
        query: str, cache: Any
    ) -> Optional[Dict[str, Any]]:
        """Try to answer from cache only."""
        cached = cache.get(query)

        if cached:
            return {
                "success": True,
                **cached,
                "provider": "cache_only",
                "degraded": True,
            }

        return None

    @staticmethod
    def template_response(
        query: str, templates: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """Use template responses for common queries."""
        query_lower = query.lower()

        for pattern, response in templates.items():
            if pattern in query_lower:
                return {
                    "success": True,
                    "text": response,
                    "provider": "template",
                    "cost_usd": 0.0,
                }

        return None


def safe_execute(
    func: Callable[[], Any],
    default: Any = None,
    log_errors: bool = True,
) -> Any:
    """
    Execute function with exception handling.

    Args:
        func: Function to execute (no args)
        default: Default value if error
        log_errors: Whether to log errors

    Returns:
        Function result or default
    """
    try:
        return func()
    except Exception as e:
        if log_errors:
            logger.error(
                "Error in %s: %s", getattr(func, "__name__", "unknown"), e
            )
        return default


# Global circuit breakers for common services
grok_circuit = CircuitBreaker(failure_threshold=5, recovery_timeout=120)
vision_circuit = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
