"""Tests for src/models/fallback.py — ProviderFallbackChain."""

import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from src.core.resilience import CircuitBreaker, CircuitBreakerError, CircuitState
from src.models.fallback import (
    ProviderFallbackChain,
    DEFAULT_CHAIN,
    _INITIAL_RECOVERY,
    _MAX_RECOVERY,
    _BACKOFF_FACTOR,
)


def _fake_get_api_key(provider):
    """Simulate all providers having API keys."""
    return f"fake-key-{provider}"


def _fake_get_api_key_limited(provider):
    """Only xai and openrouter have keys."""
    return f"fake-key-{provider}" if provider in ("xai", "openrouter") else ""


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestInit(unittest.TestCase):
    """Tests for ProviderFallbackChain.__init__."""

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_primary_is_first_in_chain(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        self.assertEqual(chain.get_chain()[0], "xai")

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_primary_not_duplicated(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        providers = chain.get_chain()
        self.assertEqual(providers.count("xai"), 1)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_only_keyed_providers_in_chain(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        providers = chain.get_chain()
        self.assertIn("xai", providers)
        self.assertIn("openrouter", providers)
        self.assertNotIn("deepseek", providers)

    @patch("src.models.fallback.get_api_key", return_value="")
    def test_empty_chain_when_no_keys(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        self.assertEqual(chain.get_chain(), [])
        self.assertIsNone(chain.active_provider)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_custom_chain_order(self, _):
        chain = ProviderFallbackChain(
            primary_provider="anthropic",
            chain_order=["anthropic", "mistral"],
        )
        providers = chain.get_chain()
        self.assertEqual(providers[0], "anthropic")
        self.assertIn("mistral", providers)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_breakers_created_for_all_providers(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        for p in chain.get_chain():
            self.assertIn(p, chain._breakers)
            self.assertIsInstance(chain._breakers[p], CircuitBreaker)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_initial_state_not_degraded(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        self.assertFalse(chain.is_degraded)
        self.assertEqual(chain.active_provider, "xai")


# ---------------------------------------------------------------------------
# Properties and status
# ---------------------------------------------------------------------------

class TestProperties(unittest.TestCase):

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_get_chain_returns_copy(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        c1 = chain.get_chain()
        c2 = chain.get_chain()
        self.assertEqual(c1, c2)
        c1.append("bogus")
        self.assertNotIn("bogus", chain.get_chain())

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_get_provider_health(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        health = chain.get_provider_health()
        self.assertIn("xai", health)
        self.assertEqual(health["xai"]["state"], "closed")
        self.assertEqual(health["xai"]["failures"], 0)
        self.assertTrue(health["xai"]["is_primary"])

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_all_providers_down_initially_false(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        self.assertFalse(chain.all_providers_down())

    @patch("src.models.fallback.get_api_key", return_value="")
    def test_all_providers_down_empty_chain(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        self.assertTrue(chain.all_providers_down())


# ---------------------------------------------------------------------------
# call_with_fallback — success paths
# ---------------------------------------------------------------------------

class TestCallWithFallbackSuccess(unittest.TestCase):

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_first_provider_succeeds(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        fn = MagicMock(return_value={"success": True, "text": "hello"})

        result, provider = chain.call_with_fallback(fn)
        self.assertTrue(result["success"])
        self.assertEqual(provider, "xai")
        fn.assert_called_once_with("xai")

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_fallback_to_second_provider(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        providers_in_chain = chain.get_chain()

        call_count = [0]
        def fn(provider):
            call_count[0] += 1
            if provider == "xai":
                raise ConnectionError("xai down")
            return {"success": True, "text": "from fallback"}

        result, used = chain.call_with_fallback(fn)
        self.assertTrue(result["success"])
        self.assertNotEqual(used, "xai")

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_non_transient_error_returns_immediately(self, _):
        """Budget/auth errors don't cascade to other providers."""
        chain = ProviderFallbackChain(primary_provider="xai")
        fn = MagicMock(return_value={
            "success": False, "error": "insufficient credits"
        })

        result, provider = chain.call_with_fallback(fn)
        self.assertFalse(result["success"])
        self.assertEqual(provider, "xai")
        fn.assert_called_once_with("xai")


# ---------------------------------------------------------------------------
# call_with_fallback — failure paths
# ---------------------------------------------------------------------------

class TestCallWithFallbackFailure(unittest.TestCase):

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_total_failure_returns_error_response(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        fn = MagicMock(side_effect=ConnectionError("all down"))

        result, provider = chain.call_with_fallback(fn)
        self.assertFalse(result["success"])
        self.assertTrue(result.get("all_providers_down"))
        self.assertEqual(provider, "none")
        self.assertIn("All providers failed", result["error"])

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_total_failure_calls_all_providers(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        called_providers = []
        def fn(provider):
            called_providers.append(provider)
            raise ConnectionError(f"{provider} down")

        chain.call_with_fallback(fn)
        self.assertIn("xai", called_providers)
        self.assertIn("openrouter", called_providers)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_transient_api_error_cascades(self, _):
        """A transient error (e.g., 503) from API response cascades to next provider."""
        chain = ProviderFallbackChain(primary_provider="xai")
        call_count = [0]
        def fn(provider):
            call_count[0] += 1
            if provider == "xai":
                return {"success": False, "error": "503 service unavailable"}
            return {"success": True, "text": "from openrouter"}

        result, used = chain.call_with_fallback(fn)
        self.assertTrue(result["success"])
        self.assertEqual(used, "openrouter")


# ---------------------------------------------------------------------------
# _is_transient_error
# ---------------------------------------------------------------------------

class TestIsTransientError(unittest.TestCase):

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def setUp(self, _):
        self.chain = ProviderFallbackChain(primary_provider="xai")

    def test_timeout_is_transient(self):
        self.assertTrue(self.chain._is_transient_error("Connection timeout"))

    def test_502_is_transient(self):
        self.assertTrue(self.chain._is_transient_error("502 bad gateway"))

    def test_rate_limit_is_transient(self):
        self.assertTrue(self.chain._is_transient_error("rate limit exceeded"))

    def test_429_is_transient(self):
        self.assertTrue(self.chain._is_transient_error("Error 429 too many requests"))

    def test_overloaded_is_transient(self):
        self.assertTrue(self.chain._is_transient_error("Server overloaded"))

    def test_auth_error_not_transient(self):
        self.assertFalse(self.chain._is_transient_error("Invalid API key"))

    def test_budget_error_not_transient(self):
        self.assertFalse(self.chain._is_transient_error("insufficient credits"))

    def test_empty_string_not_transient(self):
        self.assertFalse(self.chain._is_transient_error(""))

    def test_case_insensitive(self):
        self.assertTrue(self.chain._is_transient_error("CONNECTION REFUSED"))


# ---------------------------------------------------------------------------
# Degradation state tracking
# ---------------------------------------------------------------------------

class TestDegradationTracking(unittest.TestCase):

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_degraded_on_fallback_success(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        # Simulate: xai fails, openrouter succeeds
        chain._on_provider_success("openrouter")
        self.assertTrue(chain.is_degraded)
        self.assertEqual(chain.active_provider, "openrouter")

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_recovered_on_primary_success(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        # First degrade
        chain._on_provider_success("openrouter")
        self.assertTrue(chain.is_degraded)
        # Then primary recovers
        chain._on_provider_success("xai")
        self.assertFalse(chain.is_degraded)
        self.assertEqual(chain.active_provider, "xai")

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_degradation_callback_on_degrade(self, _):
        callback = MagicMock()
        chain = ProviderFallbackChain(
            primary_provider="xai",
            on_degradation_change=callback,
        )
        chain._on_provider_success("openrouter")
        callback.assert_called_once()
        args = callback.call_args[0]
        self.assertEqual(args[0], "degraded")
        self.assertEqual(args[1], "openrouter")

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_recovery_callback(self, _):
        callback = MagicMock()
        chain = ProviderFallbackChain(
            primary_provider="xai",
            on_degradation_change=callback,
        )
        chain._on_provider_success("openrouter")  # degrade
        callback.reset_mock()
        chain._on_provider_success("xai")  # recover
        callback.assert_called_once()
        self.assertEqual(callback.call_args[0][0], "recovered")

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_total_outage_callback(self, _):
        callback = MagicMock()
        chain = ProviderFallbackChain(
            primary_provider="xai",
            on_degradation_change=callback,
        )
        chain._on_total_outage()
        callback.assert_called_once()
        self.assertEqual(callback.call_args[0][0], "total_outage")
        self.assertTrue(chain.is_degraded)
        self.assertIsNone(chain.active_provider)


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------

class TestCircuitBreakerIntegration(unittest.TestCase):

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_failure_increments_breaker(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        chain._on_provider_failure("xai", "timeout")
        self.assertEqual(chain._breakers["xai"].failure_count, 1)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_three_failures_opens_circuit(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        for _ in range(3):
            chain._on_provider_failure("xai", "timeout")
        self.assertEqual(chain._breakers["xai"].state, CircuitState.OPEN)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_backoff_increases_on_open(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        for _ in range(3):
            chain._on_provider_failure("xai", "timeout")
        # After opening, recovery timeout should have increased
        expected = _INITIAL_RECOVERY * _BACKOFF_FACTOR
        self.assertEqual(chain._recovery_timeouts["xai"], expected)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_backoff_caps_at_max(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        # Force many open/failure cycles to hit the cap
        for _ in range(20):
            chain._on_provider_failure("xai", "timeout")
            chain._breakers["xai"].state = CircuitState.CLOSED  # reset for next round
        self.assertLessEqual(chain._recovery_timeouts["xai"], _MAX_RECOVERY)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_success_resets_recovery_timeout(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        # Open the circuit
        for _ in range(3):
            chain._on_provider_failure("xai", "timeout")
        self.assertGreater(chain._recovery_timeouts["xai"], _INITIAL_RECOVERY)
        # Success resets
        chain._on_provider_success("xai")
        self.assertEqual(chain._recovery_timeouts["xai"], _INITIAL_RECOVERY)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_open_circuit_skips_provider(self, _):
        """Provider with OPEN circuit is skipped during call_with_fallback."""
        chain = ProviderFallbackChain(primary_provider="xai")
        breaker = chain._breakers["xai"]
        breaker.state = CircuitState.OPEN
        breaker.last_failure_time = datetime.now()  # recent failure
        breaker.recovery_timeout = 9999  # won't attempt reset

        called_providers = []
        def fn(provider):
            called_providers.append(provider)
            return {"success": True, "text": "ok"}

        result, used = chain.call_with_fallback(fn)
        self.assertNotIn("xai", called_providers)
        self.assertTrue(result["success"])


# ---------------------------------------------------------------------------
# reset_provider
# ---------------------------------------------------------------------------

class TestResetProvider(unittest.TestCase):

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_reset_existing_provider(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        # Open the circuit
        for _ in range(3):
            chain._on_provider_failure("xai", "timeout")
        self.assertEqual(chain._breakers["xai"].state, CircuitState.OPEN)

        result = chain.reset_provider("xai")
        self.assertTrue(result)
        self.assertEqual(chain._breakers["xai"].state, CircuitState.CLOSED)
        self.assertEqual(chain._breakers["xai"].failure_count, 0)
        self.assertEqual(chain._recovery_timeouts["xai"], _INITIAL_RECOVERY)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_reset_unknown_provider(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        result = chain.reset_provider("nonexistent")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# _time_until_recovery
# ---------------------------------------------------------------------------

class TestTimeUntilRecovery(unittest.TestCase):

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_zero_when_closed(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        self.assertEqual(chain._time_until_recovery("xai"), 0.0)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_positive_when_open_recent_failure(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        breaker = chain._breakers["xai"]
        breaker.state = CircuitState.OPEN
        breaker.last_failure_time = datetime.now()
        breaker.recovery_timeout = 60
        remaining = chain._time_until_recovery("xai")
        self.assertGreater(remaining, 0.0)
        self.assertLessEqual(remaining, 60.0)

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key)
    def test_zero_when_open_but_recovery_elapsed(self, _):
        chain = ProviderFallbackChain(primary_provider="xai")
        breaker = chain._breakers["xai"]
        breaker.state = CircuitState.OPEN
        breaker.last_failure_time = datetime.now() - timedelta(seconds=120)
        breaker.recovery_timeout = 60
        self.assertEqual(chain._time_until_recovery("xai"), 0.0)


# ---------------------------------------------------------------------------
# Half-open recovery in call_with_fallback
# ---------------------------------------------------------------------------

class TestHalfOpenRecovery(unittest.TestCase):

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_half_open_on_recovery_time_elapsed(self, _):
        """When recovery timeout has elapsed, circuit moves to HALF_OPEN and tries."""
        chain = ProviderFallbackChain(primary_provider="xai")
        breaker = chain._breakers["xai"]
        breaker.state = CircuitState.OPEN
        breaker.last_failure_time = datetime.now() - timedelta(seconds=9999)
        breaker.recovery_timeout = 30

        fn = MagicMock(return_value={"success": True, "text": "recovered"})
        result, used = chain.call_with_fallback(fn)
        self.assertTrue(result["success"])
        self.assertEqual(used, "xai")

    @patch("src.models.fallback.get_api_key", side_effect=_fake_get_api_key_limited)
    def test_half_open_failure_cascades(self, _):
        """If HALF_OPEN attempt fails, cascades to next provider."""
        chain = ProviderFallbackChain(primary_provider="xai")
        breaker = chain._breakers["xai"]
        breaker.state = CircuitState.OPEN
        breaker.last_failure_time = datetime.now() - timedelta(seconds=9999)
        breaker.recovery_timeout = 30

        def fn(provider):
            if provider == "xai":
                raise ConnectionError("still down")
            return {"success": True, "text": "from fallback"}

        result, used = chain.call_with_fallback(fn)
        self.assertTrue(result["success"])
        self.assertEqual(used, "openrouter")


if __name__ == "__main__":
    unittest.main()
