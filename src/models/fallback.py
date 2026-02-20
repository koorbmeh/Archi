"""
Provider fallback chain with per-provider circuit breakers.

When the primary LLM provider fails, cascades through a priority-ordered
list of backup providers. Each provider has its own CircuitBreaker that
tracks failures and prevents hammering a known-down endpoint.

Usage:
    chain = ProviderFallbackChain(primary_provider="xai", cache=query_cache)
    result = chain.call_with_fallback(generate_fn, prompt, max_tokens, ...)

The chain only includes providers whose API keys are set in .env.
"""

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.resilience import CircuitBreaker, CircuitBreakerError, CircuitState
from src.models.providers import PROVIDERS, get_api_key

logger = logging.getLogger(__name__)

# Default priority order: cheapest first, most reliable first within tier.
# Only providers with API keys set in .env are actually used.
DEFAULT_CHAIN = ["xai", "openrouter", "deepseek", "openai", "anthropic", "mistral"]

# Recovery backoff: 30s → 60s → 120s → 300s cap.
_INITIAL_RECOVERY = 30
_MAX_RECOVERY = 300
_BACKOFF_FACTOR = 2.0


class ProviderFallbackChain:
    """Manages provider failover with circuit breakers and auto-recovery.

    Thread-safe: multiple callers (PlanExecutor workers, Router, etc.)
    can call call_with_fallback() concurrently.
    """

    def __init__(
        self,
        primary_provider: str = "xai",
        chain_order: Optional[List[str]] = None,
        on_degradation_change: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        """
        Args:
            primary_provider: The preferred provider (first in chain).
            chain_order: Override the default chain order.
            on_degradation_change: Callback(event, provider, message) when
                degradation state changes. Events: "degraded", "recovered",
                "total_outage", "partial_recovery".
        """
        self._primary = primary_provider
        self._on_change = on_degradation_change
        self._lock = threading.Lock()

        # Build the active chain: primary first, then others in order,
        # filtered to only providers with API keys available.
        raw_chain = chain_order or DEFAULT_CHAIN
        ordered = [primary_provider] + [p for p in raw_chain if p != primary_provider]
        self._chain: List[str] = [
            p for p in ordered if p in PROVIDERS and get_api_key(p)
        ]

        if not self._chain:
            logger.warning("No providers have API keys set — fallback chain is empty")

        # Per-provider circuit breakers.
        # Tight thresholds: 3 consecutive failures → open.
        # Recovery timeout starts at _INITIAL_RECOVERY, grows with backoff.
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._recovery_timeouts: Dict[str, float] = {}
        for provider in self._chain:
            self._breakers[provider] = CircuitBreaker(
                failure_threshold=3,
                recovery_timeout=_INITIAL_RECOVERY,
                success_threshold=1,
            )
            self._recovery_timeouts[provider] = _INITIAL_RECOVERY

        # Track which provider is currently active (for status display).
        self._active_provider: Optional[str] = self._chain[0] if self._chain else None
        self._degraded = False  # True if using a non-primary provider

        logger.info(
            "Fallback chain initialized: %s (primary: %s)",
            " → ".join(self._chain) or "EMPTY", primary_provider,
        )

    @property
    def active_provider(self) -> Optional[str]:
        """The provider currently being used (may differ from primary if degraded)."""
        return self._active_provider

    @property
    def is_degraded(self) -> bool:
        """True if operating on a non-primary provider."""
        return self._degraded

    def get_chain(self) -> List[str]:
        """Return the ordered list of available providers."""
        return list(self._chain)

    def get_provider_health(self) -> Dict[str, Dict[str, Any]]:
        """Return health status of each provider in the chain.

        Returns:
            {provider: {"state": "closed|open|half_open",
                        "failures": int, "is_primary": bool}}
        """
        health = {}
        for provider in self._chain:
            breaker = self._breakers[provider]
            health[provider] = {
                "state": breaker.state.value,
                "failures": breaker.failure_count,
                "is_primary": provider == self._primary,
                "recovery_timeout": self._recovery_timeouts.get(provider, _INITIAL_RECOVERY),
            }
        return health

    def all_providers_down(self) -> bool:
        """True if every provider's circuit breaker is OPEN."""
        if not self._chain:
            return True
        return all(
            self._breakers[p].state == CircuitState.OPEN
            for p in self._chain
        )

    def call_with_fallback(
        self,
        make_client_and_call: Callable[[str], Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], str]:
        """Try each provider in order until one succeeds.

        Args:
            make_client_and_call: A function that accepts a provider name
                and returns the API response dict.  The caller is responsible
                for creating the OpenRouterClient for that provider and
                calling generate() on it.

        Returns:
            (response_dict, provider_used).
            On total failure, response_dict has success=False.
        """
        errors = []

        for provider in self._chain:
            breaker = self._breakers[provider]

            # Skip providers whose circuit is OPEN (unless recovery time elapsed)
            if breaker.state == CircuitState.OPEN:
                if not breaker._should_attempt_reset():
                    logger.debug("Skipping %s (circuit OPEN, recovery in %.0fs)",
                                 provider, self._time_until_recovery(provider))
                    continue
                else:
                    breaker.state = CircuitState.HALF_OPEN
                    logger.info("Attempting recovery for %s (HALF_OPEN)", provider)

            try:
                response = make_client_and_call(provider)

                if response.get("success"):
                    self._on_provider_success(provider)
                    return response, provider
                else:
                    # API returned but with an error (e.g. bad request, budget)
                    error_msg = response.get("error", "unknown")
                    # Don't count budget/auth errors as provider failures
                    if self._is_transient_error(error_msg):
                        self._on_provider_failure(provider, error_msg)
                        errors.append(f"{provider}: {error_msg}")
                    else:
                        # Non-transient (budget, auth) — don't cascade, just return
                        logger.info("Non-transient error from %s: %s", provider, error_msg)
                        return response, provider

            except CircuitBreakerError:
                errors.append(f"{provider}: circuit breaker open")
                continue
            except Exception as e:
                self._on_provider_failure(provider, str(e))
                errors.append(f"{provider}: {e}")
                continue

        # Total failure: all providers exhausted
        self._on_total_outage()
        return {
            "text": "",
            "error": f"All providers failed: {'; '.join(errors)}",
            "tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "duration_ms": 0,
            "cost_usd": 0.0,
            "model": "",
            "success": False,
            "all_providers_down": True,
        }, "none"

    def _is_transient_error(self, error_msg: str) -> bool:
        """True if the error suggests a provider outage (worth cascading)."""
        lower = error_msg.lower()
        transient_signals = (
            "timeout", "timed out", "connection", "connect",
            "500", "502", "503", "504", "529",
            "rate limit", "rate_limit", "too many requests", "429",
            "internal server error", "service unavailable",
            "bad gateway", "gateway timeout", "overloaded",
        )
        return any(s in lower for s in transient_signals)

    def _on_provider_success(self, provider: str) -> None:
        """Handle a successful call to a provider."""
        breaker = self._breakers[provider]
        breaker._on_success()

        # Reset recovery timeout on success
        self._recovery_timeouts[provider] = _INITIAL_RECOVERY
        breaker.recovery_timeout = _INITIAL_RECOVERY

        with self._lock:
            was_degraded = self._degraded
            self._active_provider = provider

            if provider == self._primary:
                if was_degraded:
                    self._degraded = False
                    logger.info("PRIMARY RECOVERED: %s is back online", provider)
                    if self._on_change:
                        self._on_change(
                            "recovered", provider,
                            f"Primary provider ({provider}) is back online."
                        )
            else:
                if not was_degraded:
                    self._degraded = True
                    logger.warning(
                        "DEGRADED: primary %s failed, using fallback %s",
                        self._primary, provider,
                    )
                    if self._on_change:
                        self._on_change(
                            "degraded", provider,
                            f"Primary provider ({self._primary}) is down. "
                            f"Using {provider} as fallback."
                        )

    def _on_provider_failure(self, provider: str, error: str) -> None:
        """Handle a failed call to a provider."""
        breaker = self._breakers[provider]
        breaker._on_failure()

        # If circuit just opened, apply exponential backoff to recovery timeout
        if breaker.state == CircuitState.OPEN:
            current = self._recovery_timeouts.get(provider, _INITIAL_RECOVERY)
            new_timeout = min(current * _BACKOFF_FACTOR, _MAX_RECOVERY)
            self._recovery_timeouts[provider] = new_timeout
            breaker.recovery_timeout = int(new_timeout)
            logger.warning(
                "Provider %s circuit OPEN (failures: %d, next recovery in %ds): %s",
                provider, breaker.failure_count, int(new_timeout), error,
            )

    def _on_total_outage(self) -> None:
        """Handle total outage (all providers down)."""
        with self._lock:
            self._degraded = True
            self._active_provider = None
        logger.error("TOTAL OUTAGE: all providers in fallback chain are down")
        if self._on_change:
            self._on_change(
                "total_outage", "none",
                "All LLM providers are currently down. "
                "Operating in cache-only mode until a provider recovers."
            )

    def _time_until_recovery(self, provider: str) -> float:
        """Seconds until a provider's circuit breaker attempts recovery."""
        breaker = self._breakers[provider]
        if breaker.state != CircuitState.OPEN or not breaker.last_failure_time:
            return 0.0
        from datetime import datetime
        elapsed = (datetime.now() - breaker.last_failure_time).total_seconds()
        return max(0, breaker.recovery_timeout - elapsed)

    def reset_provider(self, provider: str) -> bool:
        """Manually reset a provider's circuit breaker (e.g. from Discord command)."""
        if provider not in self._breakers:
            return False
        breaker = self._breakers[provider]
        breaker.state = CircuitState.CLOSED
        breaker.failure_count = 0
        breaker.success_count = 0
        self._recovery_timeouts[provider] = _INITIAL_RECOVERY
        breaker.recovery_timeout = _INITIAL_RECOVERY
        logger.info("Provider %s circuit breaker manually reset", provider)
        return True
