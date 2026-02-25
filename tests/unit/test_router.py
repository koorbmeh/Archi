"""Tests for src/models/router.py — ModelRouter."""

import unittest
from unittest.mock import MagicMock, patch, call
import threading
import time

from src.models.router import ModelRouter
from src.models.cache import QueryCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_openrouter_client(
    provider="xai",
    active_model="test-model",
    generate_response=None,
    raise_on_init=False,
):
    """Create a MagicMock for OpenRouterClient with sensible defaults."""
    if raise_on_init:
        side_effect = ValueError("No API key")
        return MagicMock(side_effect=side_effect)

    client = MagicMock()
    client.provider = provider
    client._runtime_model = None
    client.get_active_model.return_value = active_model

    if generate_response is None:
        generate_response = {
            "text": "Test response",
            "model": active_model,
            "success": True,
            "cost_usd": 0.01,
            "input_tokens": 10,
            "output_tokens": 20,
        }
    client.generate.return_value = generate_response
    client.generate_with_vision.return_value = generate_response
    client.switch_model.return_value = None
    client.reset_model.return_value = None
    client.close.return_value = None

    return client


# ---------------------------------------------------------------------------
# Initialization tests
# ---------------------------------------------------------------------------

class TestInit(unittest.TestCase):
    """Tests for ModelRouter.__init__."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_init_with_explicit_client(self, mock_fallback, mock_or_client):
        """Explicit client passed in."""
        client = _mock_openrouter_client(provider="xai")
        mock_fallback.return_value = MagicMock()

        router = ModelRouter(api_client=client)

        self.assertEqual(router._api, client)
        self.assertIsNotNone(router._cache)
        mock_fallback.assert_called_once()

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_init_auto_create_xai(self, mock_fallback, mock_or_client):
        """Auto-create xAI client when none provided."""
        mock_client = _mock_openrouter_client(provider="xai")
        mock_or_client.side_effect = lambda provider=None: mock_client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()

        self.assertEqual(router._api, mock_client)
        mock_or_client.assert_called_once_with(provider="xai")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_init_fallback_to_openrouter(self, mock_fallback, mock_or_client):
        """Fall back to OpenRouter if xAI fails."""
        mock_client = _mock_openrouter_client(provider="openrouter")

        def side_effect(provider=None):
            if provider == "xai":
                raise ValueError("XAI_API_KEY not set")
            return mock_client

        mock_or_client.side_effect = side_effect
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()

        self.assertEqual(router._api, mock_client)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_init_raises_when_no_keys(self, mock_fallback, mock_or_client):
        """Raise RuntimeError if no API keys configured."""
        def side_effect(provider=None):
            raise ValueError("No API key")

        mock_or_client.side_effect = side_effect

        with self.assertRaises(RuntimeError) as cm:
            ModelRouter()
        self.assertIn("No LLM API key configured", str(cm.exception))

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_init_creates_cache_if_none(self, mock_fallback, mock_or_client):
        """Create QueryCache if none provided."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()

        self.assertIsNotNone(router._cache)
        self.assertIsInstance(router._cache, QueryCache)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_init_uses_provided_cache(self, mock_fallback, mock_or_client):
        """Use provided cache instead of creating one."""
        client = _mock_openrouter_client()
        cache = MagicMock(spec=QueryCache)
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter(cache=cache)

        self.assertEqual(router._cache, cache)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_init_fallback_chain_setup(self, mock_fallback, mock_or_client):
        """ProviderFallbackChain created with primary provider."""
        client = _mock_openrouter_client(provider="xai")
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        mock_fallback.return_value = mock_fallback_instance

        router = ModelRouter(api_client=client)

        mock_fallback.assert_called_once()
        call_kwargs = mock_fallback.call_args[1]
        self.assertEqual(call_kwargs["primary_provider"], "xai")
        self.assertIsNotNone(call_kwargs["on_degradation_change"])


# ---------------------------------------------------------------------------
# Provider property tests
# ---------------------------------------------------------------------------

class TestProviderProperty(unittest.TestCase):
    """Tests for ModelRouter.provider property."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_provider_returns_client_provider(self, mock_fallback, mock_or_client):
        """Return the client's provider name."""
        client = _mock_openrouter_client(provider="grok-direct")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()

        self.assertEqual(router.provider, "grok-direct")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_provider_unknown_when_no_client(self, mock_fallback, mock_or_client):
        """Return 'unknown' when no client."""
        mock_fallback.return_value = MagicMock()
        mock_or_client.side_effect = ValueError("No keys")

        with self.assertRaises(RuntimeError):
            # Router requires a client, so we can't test this without one
            router = ModelRouter(api_client=None)


# ---------------------------------------------------------------------------
# Ping tests
# ---------------------------------------------------------------------------

class TestPing(unittest.TestCase):
    """Tests for ModelRouter.ping."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_ping_openrouter_reuses_client(self, mock_fallback, mock_or_client):
        """Ping via OpenRouter reuses the active client."""
        client = _mock_openrouter_client(provider="openrouter")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        result = router.ping()

        # Should use the same client
        client.generate.assert_called_once()
        call_kwargs = client.generate.call_args[1]
        self.assertEqual(call_kwargs["model"], "openrouter/free")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_ping_non_openrouter_creates_temp_client(self, mock_fallback, mock_or_client):
        """Ping creates temporary OpenRouter client if active provider isn't OpenRouter."""
        active_client = _mock_openrouter_client(provider="xai")
        temp_client = _mock_openrouter_client(provider="openrouter")

        def or_client_side_effect(provider=None):
            if provider == "openrouter":
                return temp_client
            return active_client

        mock_or_client.side_effect = or_client_side_effect
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        result = router.ping()

        # Should have created a temp openrouter client and used it
        temp_client.generate.assert_called_once()

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_ping_no_client_returns_error(self, mock_fallback, mock_or_client):
        """Ping returns error when no API client."""
        client = _mock_openrouter_client(provider="xai")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        router._api = None  # Manually clear the client

        result = router.ping()

        self.assertFalse(result["success"])
        self.assertIn("error", result)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_ping_creates_temp_client_with_fallback(self, mock_fallback, mock_or_client):
        """If temp OpenRouter client creation fails, fallback to active client."""
        active_client = _mock_openrouter_client(provider="xai")

        def or_client_side_effect(provider=None):
            if provider == "openrouter":
                raise ImportError("openrouter not installed")
            return active_client

        mock_or_client.side_effect = or_client_side_effect
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        result = router.ping()

        # Should have fallen back to active client
        active_client.generate.assert_called_once()


# ---------------------------------------------------------------------------
# Switch model tests
# ---------------------------------------------------------------------------

class TestSwitchModel(unittest.TestCase):
    """Tests for ModelRouter.switch_model."""

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_switch_model_auto_resets(self, mock_fallback, mock_or_client, mock_resolve):
        """Switching to 'auto' resets overrides."""
        client = _mock_openrouter_client(provider="xai")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        router._force_api_override = True

        result = router.switch_model("auto")

        self.assertFalse(router._force_api_override)
        self.assertEqual(result["model"], "openrouter/auto")
        self.assertEqual(result["provider"], "openrouter")

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_switch_model_alias_resolution(self, mock_fallback, mock_or_client, mock_resolve):
        """Resolve alias to provider/model pair."""
        old_client = _mock_openrouter_client(provider="openrouter")
        new_client = _mock_openrouter_client(provider="anthropic")
        mock_or_client.side_effect = [old_client, new_client]
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("anthropic", "claude-sonnet-4.6")

        router = ModelRouter()
        result = router.switch_model("claude")

        self.assertIsNotNone(result["model"])
        self.assertEqual(result["provider"], "anthropic")

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_switch_model_provider_change(self, mock_fallback, mock_or_client, mock_resolve):
        """Switch provider when transitioning models."""
        old_client = _mock_openrouter_client(provider="xai")
        new_client = _mock_openrouter_client(provider="anthropic")

        mock_or_client.side_effect = [old_client, new_client, new_client]
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("anthropic", "claude-sonnet-4.6")

        router = ModelRouter()
        self.assertEqual(router._api.provider, "xai")

        result = router.switch_model("claude")

        self.assertEqual(router._api.provider, "anthropic")

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_switch_model_unknown_alias_error(self, mock_fallback, mock_or_client, mock_resolve):
        """Return error dict for unknown alias."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()
        mock_resolve.side_effect = ValueError("Unknown alias: foobar")

        router = ModelRouter()
        result = router.switch_model("foobar")

        self.assertIsNone(result["model"])
        self.assertIn("Unknown alias", result["message"])

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_switch_model_sets_force_override(self, mock_fallback, mock_or_client, mock_resolve):
        """Switching to specific model sets force override."""
        client = _mock_openrouter_client(provider="openrouter")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("openrouter", "grok-via-openrouter")

        router = ModelRouter()
        self.assertFalse(router._force_api_override)

        router.switch_model("grok")

        self.assertTrue(router._force_api_override)

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_switch_model_provider_creation_failure(self, mock_fallback, mock_or_client, mock_resolve):
        """Return error if new provider client creation fails."""
        client = _mock_openrouter_client(provider="xai")

        def or_client_side_effect(provider=None):
            if provider == "anthropic":
                raise ValueError("ANTHROPIC_API_KEY not set")
            return client

        mock_or_client.side_effect = or_client_side_effect
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("anthropic", "claude-sonnet-4.6")

        router = ModelRouter()
        result = router.switch_model("claude")

        self.assertIsNone(result["model"])


# ---------------------------------------------------------------------------
# Get active model info tests
# ---------------------------------------------------------------------------

class TestGetActiveModelInfo(unittest.TestCase):
    """Tests for ModelRouter.get_active_model_info."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_get_active_model_info_auto_mode(self, mock_fallback, mock_or_client):
        """Info in auto mode (no override)."""
        client = _mock_openrouter_client(provider="openrouter", active_model="grok-4.1-fast")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        router._force_api_override = False

        info = router.get_active_model_info()

        self.assertEqual(info["mode"], "auto")
        self.assertEqual(info["model"], "grok-4.1-fast")
        self.assertEqual(info["provider"], "openrouter")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_get_active_model_info_forced_mode(self, mock_fallback, mock_or_client):
        """Info in forced mode."""
        client = _mock_openrouter_client(provider="anthropic", active_model="claude-sonnet-4.6")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        router._force_api_override = True

        info = router.get_active_model_info()

        self.assertEqual(info["mode"], "forced_api")
        self.assertIn("claude-sonnet-4.6", info["display"])

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_get_active_model_info_with_temp_remaining(self, mock_fallback, mock_or_client):
        """Info includes temp countdown."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        router._temp_remaining = 3

        info = router.get_active_model_info()

        self.assertEqual(info["temp_remaining"], "3")
        self.assertIn("temp: 3 left", info["mode"])


# ---------------------------------------------------------------------------
# Switch model temp tests
# ---------------------------------------------------------------------------

class TestSwitchModelTemp(unittest.TestCase):
    """Tests for ModelRouter.switch_model_temp."""

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_switch_model_temp_sets_state(self, mock_fallback, mock_or_client, mock_resolve):
        """Temp switch sets countdown and snapshot."""
        client = _mock_openrouter_client(provider="openrouter")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("openrouter", "claude-sonnet")

        router = ModelRouter()

        result = router.switch_model_temp("claude", count=5)

        self.assertEqual(router._temp_remaining, 5)
        self.assertIsNotNone(router._temp_previous)
        # Message format is "...\n_This is temporary — will revert after 5 messages._"
        self.assertIn("5", result["message"])

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_switch_model_temp_failed_switch_clears_temp(self, mock_fallback, mock_or_client, mock_resolve):
        """Failed switch doesn't leave temp state."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()
        mock_resolve.side_effect = ValueError("Unknown alias")

        router = ModelRouter()
        result = router.switch_model_temp("unknown")

        self.assertEqual(router._temp_remaining, 0)
        self.assertIsNone(router._temp_previous)
        self.assertIsNone(result["model"])

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_switch_model_temp_minimum_count_one(self, mock_fallback, mock_or_client, mock_resolve):
        """Temp count defaults to at least 1."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("openrouter", "claude")

        router = ModelRouter()
        router.switch_model_temp("claude", count=0)

        self.assertEqual(router._temp_remaining, 1)

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_switch_model_temp_snapshots_state(self, mock_fallback, mock_or_client, mock_resolve):
        """Snapshot includes provider."""
        client = _mock_openrouter_client(provider="xai")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("anthropic", "claude")

        router = ModelRouter()
        router._force_api_override = True
        original_provider = router._api.provider

        router.switch_model_temp("claude", count=1)

        prev_override, prev_model, prev_provider = router._temp_previous
        self.assertEqual(prev_override, True)
        self.assertEqual(prev_provider, original_provider)


# ---------------------------------------------------------------------------
# Tick temp switch tests
# ---------------------------------------------------------------------------

class TestTickTempSwitch(unittest.TestCase):
    """Tests for ModelRouter._tick_temp_switch."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_tick_temp_switch_no_temp_active(self, mock_fallback, mock_or_client):
        """No tick when temp not active."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        result = router._tick_temp_switch()

        self.assertIsNone(result)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_tick_temp_switch_decrements(self, mock_fallback, mock_or_client):
        """Counter decrements without expiring."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        router._temp_remaining = 3
        router._temp_previous = (False, None, "xai")

        result = router._tick_temp_switch()

        self.assertEqual(router._temp_remaining, 2)
        self.assertIsNone(result)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_tick_temp_switch_expires(self, mock_fallback, mock_or_client):
        """Returns revert message on expiry."""
        client = _mock_openrouter_client(provider="openrouter")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        router._temp_remaining = 1
        router._temp_previous = (False, None, "openrouter")

        result = router._tick_temp_switch()

        self.assertIsNotNone(result)
        self.assertIn("Reverted", result)
        self.assertEqual(router._temp_remaining, 0)
        self.assertIsNone(router._temp_previous)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_tick_temp_switch_restores_provider(self, mock_fallback, mock_or_client):
        """Restores original provider on expiry."""
        original_client = _mock_openrouter_client(provider="xai")
        new_client = _mock_openrouter_client(provider="anthropic")
        restored_client = _mock_openrouter_client(provider="xai")

        def or_client_side_effect(provider=None):
            if provider == "xai":
                return restored_client
            elif provider == "anthropic":
                return new_client
            return original_client

        mock_or_client.side_effect = or_client_side_effect
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        self.assertEqual(router._api.provider, "xai")

        # Simulate temp switch to anthropic
        router._api = new_client
        router._temp_remaining = 1
        router._temp_previous = (False, None, "xai")

        router._tick_temp_switch()

        # After tick, should restore to xai
        self.assertEqual(router._api.provider, "xai")


# ---------------------------------------------------------------------------
# Complete temp task tests
# ---------------------------------------------------------------------------

class TestCompleteTempTask(unittest.TestCase):
    """Tests for ModelRouter.complete_temp_task."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_complete_temp_task_no_temp(self, mock_fallback, mock_or_client):
        """No-op when no temp switch active."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        result = router.complete_temp_task()

        self.assertIsNone(result)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_complete_temp_task_force_expires(self, mock_fallback, mock_or_client):
        """Force-expires temp switch immediately."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        router._temp_remaining = 10
        router._temp_previous = (False, None, "xai")

        result = router.complete_temp_task()

        self.assertIsNotNone(result)
        self.assertIn("Reverted", result)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_complete_temp_task_calls_tick(self, mock_fallback, mock_or_client):
        """Calls _tick_temp_switch internally."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        router._temp_remaining = 5
        router._temp_previous = (False, None, "xai")

        router.complete_temp_task()

        self.assertEqual(router._temp_remaining, 0)


# ---------------------------------------------------------------------------
# Escalate for task tests
# ---------------------------------------------------------------------------

class TestEscalateForTask(unittest.TestCase):
    """Tests for ModelRouter.escalate_for_task context manager (thread-local)."""

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_escalate_sets_thread_local(self, mock_fallback, mock_or_client, mock_resolve):
        """Context manager sets thread-local escalation state."""
        client = _mock_openrouter_client(provider="xai")
        esc_client = _mock_openrouter_client(provider="openrouter")
        mock_or_client.side_effect = [client, esc_client]
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("openrouter", "gemini-3.1-pro")

        router = ModelRouter()

        with router.escalate_for_task("gemini-3.1-pro") as result:
            self.assertEqual(result["model"], "gemini-3.1-pro")
            self.assertEqual(result["provider"], "openrouter")
            # Thread-local should be set
            self.assertIsNotNone(getattr(router._thread_local, "escalation_client", None))
            self.assertEqual(router._thread_local.escalation_model, "gemini-3.1-pro")

        # After exit, thread-local should be cleared
        self.assertIsNone(router._thread_local.escalation_client)
        self.assertIsNone(router._thread_local.escalation_model)

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_escalate_does_not_mutate_shared_state(self, mock_fallback, mock_or_client, mock_resolve):
        """Escalation does NOT change _force_api_override or _api."""
        client = _mock_openrouter_client(provider="xai")
        esc_client = _mock_openrouter_client(provider="openrouter")
        mock_or_client.side_effect = [client, esc_client]
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("openrouter", "gemini-3.1-pro")

        router = ModelRouter()
        original_api = router._api
        original_override = router._force_api_override

        with router.escalate_for_task("gemini-3.1-pro"):
            # Shared state must NOT change
            self.assertIs(router._api, original_api)
            self.assertEqual(router._force_api_override, original_override)

        self.assertIs(router._api, original_api)
        self.assertEqual(router._force_api_override, original_override)

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_escalate_exception_clears_thread_local(self, mock_fallback, mock_or_client, mock_resolve):
        """Thread-local cleared even if exception in block."""
        client = _mock_openrouter_client(provider="xai")
        esc_client = _mock_openrouter_client(provider="openrouter")
        mock_or_client.side_effect = [client, esc_client]
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("openrouter", "gemini-3.1-pro")

        router = ModelRouter()

        try:
            with router.escalate_for_task("gemini-3.1-pro"):
                raise ValueError("Test exception")
        except ValueError:
            pass

        self.assertIsNone(router._thread_local.escalation_client)
        self.assertIsNone(router._thread_local.escalation_model)

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_escalate_failed_alias(self, mock_fallback, mock_or_client, mock_resolve):
        """Failed alias resolution yields error result."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()
        mock_resolve.side_effect = ValueError("Unknown alias")

        router = ModelRouter()

        with router.escalate_for_task("unknown") as result:
            self.assertIsNone(result.get("model"))

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_escalate_failed_client_creation(self, mock_fallback, mock_or_client, mock_resolve):
        """Failed client creation yields error result."""
        client = _mock_openrouter_client(provider="xai")

        def or_client_side_effect(provider=None):
            if provider == "openrouter":
                raise ValueError("No OPENROUTER_API_KEY")
            return client

        mock_or_client.side_effect = or_client_side_effect
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("openrouter", "gemini-3.1-pro")

        router = ModelRouter()

        with router.escalate_for_task("gemini-3.1-pro") as result:
            self.assertIsNone(result.get("model"))

    @patch("src.models.router.resolve_alias")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_escalate_thread_isolation(self, mock_fallback, mock_or_client, mock_resolve):
        """Escalation on one thread does not affect another thread."""
        client = _mock_openrouter_client(provider="xai")
        esc_client = _mock_openrouter_client(provider="openrouter")
        mock_or_client.side_effect = [client, esc_client]
        mock_fallback.return_value = MagicMock()
        mock_resolve.return_value = ("openrouter", "gemini-3.1-pro")

        router = ModelRouter()
        other_thread_saw_escalation = [None]
        barrier = threading.Barrier(2, timeout=5)

        def _escalating_thread():
            with router.escalate_for_task("gemini-3.1-pro"):
                barrier.wait()  # Signal: escalation is active
                barrier.wait()  # Wait: other thread checked

        def _normal_thread():
            barrier.wait()  # Wait: escalation is active on other thread
            # This thread should NOT see the escalation
            esc = getattr(router._thread_local, "escalation_model", None)
            other_thread_saw_escalation[0] = esc
            barrier.wait()  # Signal: done checking

        t1 = threading.Thread(target=_escalating_thread)
        t2 = threading.Thread(target=_normal_thread)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertIsNone(other_thread_saw_escalation[0])


# ---------------------------------------------------------------------------
# Classify complexity tests
# ---------------------------------------------------------------------------

class TestClassifyComplexity(unittest.TestCase):
    """Tests for ModelRouter._classify_complexity."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_classify_simple_short(self, mock_fallback, mock_or_client):
        """Short prompts are simple."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        result = router._classify_complexity("hi there")

        self.assertEqual(result, "simple")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_classify_complex_long(self, mock_fallback, mock_or_client):
        """Long prompts are complex."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        long_prompt = " ".join(["word"] * 100)
        result = router._classify_complexity(long_prompt)

        self.assertEqual(result, "complex")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_classify_complex_keywords(self, mock_fallback, mock_or_client):
        """Complex keywords trigger complex classification."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        # "in detail" is 2 words, need more for length > 50 or a keyword match
        result = router._classify_complexity("Please analyze in detail the implications and compare the options")

        self.assertEqual(result, "complex")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_classify_simple_keywords(self, mock_fallback, mock_or_client):
        """Simple keywords trigger simple classification."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        result = router._classify_complexity("What is 2+2?")

        self.assertEqual(result, "simple")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_classify_medium_default(self, mock_fallback, mock_or_client):
        """Medium-length without keywords is medium."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        prompt = " ".join(["word"] * 20)
        result = router._classify_complexity(prompt)

        self.assertEqual(result, "medium")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_classify_plan_step_with_task_section(self, mock_fallback, mock_or_client):
        """plan_step hint extracts task section."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        prompt = "TASK: Do something simple\n\nGoal: Achieve it\n\n" + " ".join(["boilerplate"] * 100)
        result = router._classify_complexity(prompt, classify_hint="plan_step")

        # Should be simple because task section is short
        self.assertEqual(result, "simple")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_classify_plan_step_without_task_section(self, mock_fallback, mock_or_client):
        """plan_step hint without TASK: caps word count."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        prompt = "No task marker here but " + " ".join(["word"] * 100)
        result = router._classify_complexity(prompt, classify_hint="plan_step")

        # Should cap at 45 words -> between simple(10) and complex(50)
        # Actually, n=45 means not complex(>50) and not simple(<10)
        self.assertEqual(result, "medium")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_classify_case_insensitive(self, mock_fallback, mock_or_client):
        """Keyword matching is case-insensitive."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        # Need >10 words to avoid short prompt classification
        # "ANALYZE IN DETAIL" contains complex keyword
        result = router._classify_complexity("PLEASE ANALYZE IN DETAIL THE IMPLICATIONS FOR THE ENTIRE PROJECT SCOPE")

        self.assertEqual(result, "complex")


# ---------------------------------------------------------------------------
# Needs web search tests
# ---------------------------------------------------------------------------

class TestNeedsWebSearch(unittest.TestCase):
    """Tests for ModelRouter._needs_web_search."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_needs_web_search_current_keyword(self, mock_fallback, mock_or_client):
        """'current' keyword triggers web search."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        result = router._needs_web_search("What is the current weather?")

        self.assertTrue(result)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_needs_web_search_news_keyword(self, mock_fallback, mock_or_client):
        """'news' keyword triggers web search."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        result = router._needs_web_search("What's the latest news?")

        self.assertTrue(result)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_needs_web_search_no_keyword(self, mock_fallback, mock_or_client):
        """No keyword returns False."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        result = router._needs_web_search("What is the meaning of life?")

        self.assertFalse(result)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_needs_web_search_from_messages(self, mock_fallback, mock_or_client):
        """Extracts user query from messages."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "What is the current price of bitcoin?"}
        ]
        result = router._needs_web_search("", messages=messages)

        self.assertTrue(result)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_needs_web_search_case_insensitive(self, mock_fallback, mock_or_client):
        """Keyword matching is case-insensitive."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        result = router._needs_web_search("WHAT IS THE CURRENT WEATHER")

        self.assertTrue(result)


# ---------------------------------------------------------------------------
# Extract user query tests
# ---------------------------------------------------------------------------

class TestExtractUserQuery(unittest.TestCase):
    """Tests for ModelRouter._extract_user_query (static method)."""

    def test_extract_from_messages_list(self):
        """Extract last user message from messages list."""
        messages = [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"}
        ]
        result = ModelRouter._extract_user_query("", messages=messages)

        self.assertEqual(result, "Second question")

    def test_extract_from_prompt_user_lines(self):
        """Extract from 'User:' lines in flat prompt."""
        prompt = "System: Be helpful\nUser: What is 2+2?\nArchi: 4"
        result = ModelRouter._extract_user_query(prompt)

        self.assertIn("What is 2+2?", result)

    def test_extract_last_non_empty_line_fallback(self):
        """Fall back to last non-empty line."""
        prompt = "Some text\n\nLast meaningful line"
        result = ModelRouter._extract_user_query(prompt)

        self.assertEqual(result, "Last meaningful line")

    def test_extract_prefers_messages_over_prompt(self):
        """Prefers structured messages over prompt."""
        messages = [{"role": "user", "content": "Structured message"}]
        prompt = "User: Flat prompt"
        result = ModelRouter._extract_user_query(prompt, messages=messages)

        self.assertEqual(result, "Structured message")

    def test_extract_multiline_user_section(self):
        """Extract multiline user text up to next marker."""
        prompt = "User: First line\nSecond line\nThird line\nRespond with:"
        result = ModelRouter._extract_user_query(prompt)

        self.assertIn("First line", result)
        self.assertIn("Second line", result)


# ---------------------------------------------------------------------------
# Generate tests
# ---------------------------------------------------------------------------

class TestGenerate(unittest.TestCase):
    """Tests for ModelRouter.generate."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_generate_cache_hit(self, mock_fallback, mock_or_client):
        """Cache hit returns cached response."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        cached_response = {"text": "cached", "success": True, "cost_usd": 0.01}
        router._cache.set("[model:test-model]test prompt", cached_response)

        result = router.generate("test prompt")

        self.assertEqual(result["text"], "cached")
        self.assertTrue(result["cached"])
        self.assertEqual(result["cost_usd"], 0.0)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_generate_cache_miss_calls_api(self, mock_fallback, mock_or_client):
        """Cache miss calls API."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        mock_fallback_instance.call_with_fallback.return_value = (
            {"text": "api response", "success": True, "cost_usd": 0.01},
            "xai"
        )
        mock_fallback.return_value = mock_fallback_instance

        router = ModelRouter()
        result = router.generate("new prompt")

        self.assertEqual(result["text"], "api response")
        mock_fallback_instance.call_with_fallback.assert_called_once()

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_generate_multi_turn_skips_cache(self, mock_fallback, mock_or_client):
        """Multi-turn (messages) skips cache lookup."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        mock_fallback_instance.call_with_fallback.return_value = (
            {"text": "response", "success": True, "cost_usd": 0.01},
            "xai"
        )
        mock_fallback.return_value = mock_fallback_instance

        router = ModelRouter()
        messages = [{"role": "user", "content": "hi"}]
        result = router.generate("", messages=messages)

        # Should call API, not cache
        mock_fallback_instance.call_with_fallback.assert_called_once()

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_generate_complexity_classification(self, mock_fallback, mock_or_client):
        """Complexity is classified during generate."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        mock_fallback_instance.call_with_fallback.return_value = (
            {"text": "response", "success": True, "cost_usd": 0.01},
            "xai"
        )
        mock_fallback.return_value = mock_fallback_instance

        router = ModelRouter()
        router.generate("What is 2+2?")

        # Should have classified as simple
        # (We can't directly check this, but the method should complete without error)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_generate_with_temp_tick(self, mock_fallback, mock_or_client):
        """Temp counter ticked after generate."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        mock_fallback_instance.call_with_fallback.return_value = (
            {"text": "response", "success": True, "cost_usd": 0.01},
            "xai"
        )
        mock_fallback.return_value = mock_fallback_instance

        router = ModelRouter()
        router._temp_remaining = 2
        router._temp_previous = (False, None, "xai")

        result = router.generate("test")

        self.assertEqual(router._temp_remaining, 1)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_generate_caches_response(self, mock_fallback, mock_or_client):
        """Response cached for future use."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        response = {"text": "api response", "success": True, "cost_usd": 0.01}
        mock_fallback_instance.call_with_fallback.return_value = (response, "xai")
        mock_fallback.return_value = mock_fallback_instance

        router = ModelRouter()
        router.generate("test prompt")

        # Verify it was cached
        cached = router._cache.get("[model:test-model]test prompt")
        self.assertIsNotNone(cached)


# ---------------------------------------------------------------------------
# Use API tests
# ---------------------------------------------------------------------------

class TestUseApi(unittest.TestCase):
    """Tests for ModelRouter._use_api."""

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_use_api_budget_blocked(self, mock_fallback, mock_or_client, mock_get_tracker):
        """Budget hard stop blocks API call."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        mock_tracker = MagicMock()
        mock_tracker.check_budget.return_value = {
            "allowed": False,
            "reason": "daily_limit",
            "daily_spent": 10.0,
            "daily_limit": 10.0
        }
        mock_get_tracker.return_value = mock_tracker

        router = ModelRouter()
        result = router._use_api("test", max_tokens=100, temperature=0.7, enable_web_search=False)

        self.assertFalse(result["success"])
        self.assertIn("Budget", result["error"])

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_use_api_thread_local_escalation(self, mock_fallback, mock_or_client, mock_get_tracker):
        """Thread-local escalation uses escalated client+model."""
        client = _mock_openrouter_client(provider="xai")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        mock_tracker = MagicMock()
        mock_tracker.check_budget.return_value = {"allowed": True}
        mock_get_tracker.return_value = mock_tracker

        esc_client = _mock_openrouter_client(provider="openrouter")
        esc_response = {"text": "escalated", "success": True, "cost_usd": 0.05,
                        "model": "gemini-3.1-pro", "input_tokens": 50, "output_tokens": 100}
        esc_client.generate.return_value = esc_response

        router = ModelRouter()
        # Simulate thread-local escalation
        router._thread_local.escalation_client = esc_client
        router._thread_local.escalation_model = "gemini-3.1-pro"

        result = router._use_api("test", max_tokens=100, temperature=0.7, enable_web_search=False)

        self.assertTrue(result["success"])
        self.assertEqual(result["text"], "escalated")
        # Escalated client should have been called with explicit model
        esc_client.generate.assert_called_once()
        call_kwargs = esc_client.generate.call_args[1]
        self.assertEqual(call_kwargs["model"], "gemini-3.1-pro")
        # Normal client should NOT have been called
        client.generate.assert_not_called()

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_use_api_escalation_failure_falls_through(self, mock_fallback, mock_or_client, mock_get_tracker):
        """If escalated client fails, falls through to normal routing."""
        client = _mock_openrouter_client(provider="xai")
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        mock_fallback_instance.call_with_fallback.return_value = (
            {"text": "fallback", "success": True, "cost_usd": 0.01},
            "xai"
        )
        mock_fallback.return_value = mock_fallback_instance

        mock_tracker = MagicMock()
        mock_tracker.check_budget.return_value = {"allowed": True}
        mock_get_tracker.return_value = mock_tracker

        esc_client = _mock_openrouter_client(provider="openrouter")
        esc_client.generate.return_value = {"success": False, "error": "API error"}

        router = ModelRouter()
        router._thread_local.escalation_client = esc_client
        router._thread_local.escalation_model = "gemini-3.1-pro"

        result = router._use_api("test", max_tokens=100, temperature=0.7, enable_web_search=False)

        self.assertTrue(result["success"])
        self.assertEqual(result["text"], "fallback")

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_use_api_force_override_success(self, mock_fallback, mock_or_client, mock_get_tracker):
        """Force override tries user's provider first."""
        client = _mock_openrouter_client(provider="anthropic")
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        mock_fallback.return_value = mock_fallback_instance

        mock_tracker = MagicMock()
        mock_tracker.check_budget.return_value = {"allowed": True}
        mock_get_tracker.return_value = mock_tracker

        response = {"text": "response", "success": True, "cost_usd": 0.01}
        client.generate.return_value = response

        router = ModelRouter()
        router._force_api_override = True

        result = router._use_api("test", max_tokens=100, temperature=0.7, enable_web_search=False)

        self.assertTrue(result["success"])

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_use_api_fallback_on_user_provider_failure(self, mock_fallback, mock_or_client, mock_get_tracker):
        """User provider fails, cascades to chain."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        mock_fallback_instance.call_with_fallback.return_value = (
            {"text": "fallback response", "success": True, "cost_usd": 0.01},
            "openrouter"
        )
        mock_fallback.return_value = mock_fallback_instance

        mock_tracker = MagicMock()
        mock_tracker.check_budget.return_value = {"allowed": True}
        mock_get_tracker.return_value = mock_tracker

        # User provider fails
        client.generate.return_value = {"success": False, "error": "API error"}

        router = ModelRouter()
        router._force_api_override = True

        result = router._use_api("test", max_tokens=100, temperature=0.7, enable_web_search=False)

        # Should fall through to chain
        self.assertTrue(result["success"])

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_use_api_fallback_chain_success(self, mock_fallback, mock_or_client, mock_get_tracker):
        """Fallback chain returns successful response."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        response = {"text": "success", "success": True, "cost_usd": 0.01}
        mock_fallback_instance.call_with_fallback.return_value = (response, "xai")
        mock_fallback.return_value = mock_fallback_instance

        mock_tracker = MagicMock()
        mock_tracker.check_budget.return_value = {"allowed": True}
        mock_get_tracker.return_value = mock_tracker

        router = ModelRouter()
        result = router._use_api("test", max_tokens=100, temperature=0.7, enable_web_search=False)

        self.assertTrue(result["success"])

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_use_api_total_outage_cache_fallback(self, mock_fallback, mock_or_client, mock_get_tracker):
        """Total outage falls back to cache."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        mock_fallback_instance.call_with_fallback.return_value = (
            {"success": False, "error": "All providers down"},
            "none"
        )
        mock_fallback.return_value = mock_fallback_instance

        mock_tracker = MagicMock()
        mock_tracker.check_budget.return_value = {"allowed": True}
        mock_get_tracker.return_value = mock_tracker

        router = ModelRouter()
        # Pre-populate cache
        cached = {"text": "cached response", "success": True, "cost_usd": 0.01}
        router._cache.set("test", cached)

        result = router._use_api("test", max_tokens=100, temperature=0.7, enable_web_search=False)

        self.assertEqual(result["text"], "cached response")
        self.assertTrue(result["degraded"])

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_use_api_total_outage_friendly_error(self, mock_fallback, mock_or_client, mock_get_tracker):
        """Total outage with no cache returns friendly error."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback_instance = MagicMock()
        mock_fallback_instance.call_with_fallback.return_value = (
            {"success": False, "error": "All providers down"},
            "none"
        )
        mock_fallback.return_value = mock_fallback_instance

        mock_tracker = MagicMock()
        mock_tracker.check_budget.return_value = {"allowed": True}
        mock_get_tracker.return_value = mock_tracker

        router = ModelRouter()
        result = router._use_api("test", max_tokens=100, temperature=0.7, enable_web_search=False)

        self.assertFalse(result["success"])
        self.assertIn("trouble reaching", result["text"])


# ---------------------------------------------------------------------------
# Record success tests
# ---------------------------------------------------------------------------

class TestRecordSuccess(unittest.TestCase):
    """Tests for ModelRouter._record_success."""

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_record_success_updates_stats(self, mock_fallback, mock_or_client, mock_get_tracker):
        """Success updates internal stats."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()
        mock_get_tracker.return_value = MagicMock()

        router = ModelRouter()
        initial_count = router._stats["api_used"]

        response = {"cost_usd": 0.05, "model": "test"}
        router._record_success(response)

        self.assertEqual(router._stats["api_used"], initial_count + 1)
        self.assertEqual(router._stats["total_cost"], 0.05)

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_record_success_calls_cost_tracker(self, mock_fallback, mock_or_client, mock_get_tracker):
        """Success records to CostTracker."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        mock_tracker = MagicMock()
        mock_get_tracker.return_value = mock_tracker

        router = ModelRouter()
        response = {
            "cost_usd": 0.01,
            "model": "test-model",
            "input_tokens": 10,
            "output_tokens": 20,
        }
        router._record_success(response, provider="xai")

        mock_tracker.record_usage.assert_called_once()

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_record_success_handles_tracker_failure(self, mock_fallback, mock_or_client, mock_get_tracker):
        """CostTracker failure doesn't crash."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        mock_tracker = MagicMock()
        mock_tracker.record_usage.side_effect = Exception("Tracker error")
        mock_get_tracker.return_value = mock_tracker

        router = ModelRouter()
        response = {"cost_usd": 0.01, "model": "test"}

        # Should not raise
        router._record_success(response)
        self.assertEqual(router._stats["api_used"], 1)


# ---------------------------------------------------------------------------
# Get or create client tests
# ---------------------------------------------------------------------------

class TestGetOrCreateClient(unittest.TestCase):
    """Tests for ModelRouter._get_or_create_client."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_get_or_create_client_cached_reuse(self, mock_fallback, mock_or_client):
        """Reuse cached client."""
        client = _mock_openrouter_client(provider="xai")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        retrieved = router._get_or_create_client("xai")

        self.assertEqual(retrieved, client)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_get_or_create_client_new_creation(self, mock_fallback, mock_or_client):
        """Create new client for uncached provider."""
        init_client = _mock_openrouter_client(provider="xai")
        new_client = _mock_openrouter_client(provider="anthropic")

        mock_or_client.side_effect = [init_client, new_client]
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        retrieved = router._get_or_create_client("anthropic")

        self.assertEqual(retrieved.provider, "anthropic")
        self.assertIn("anthropic", router._fallback_clients)

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_get_or_create_client_creation_failure(self, mock_fallback, mock_or_client):
        """Creation failure raises RuntimeError."""
        init_client = _mock_openrouter_client(provider="xai")

        def or_client_side_effect(provider=None):
            if provider == "xai":
                return init_client
            raise ValueError("ANTHROPIC_API_KEY not set")

        mock_or_client.side_effect = or_client_side_effect
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()

        with self.assertRaises(RuntimeError):
            router._get_or_create_client("anthropic")


# ---------------------------------------------------------------------------
# Chat with image tests
# ---------------------------------------------------------------------------

class TestChatWithImage(unittest.TestCase):
    """Tests for ModelRouter.chat_with_image."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    @patch("builtins.open")
    def test_chat_with_image_success(self, mock_open, mock_fallback, mock_or_client):
        """Successful image analysis."""
        client = _mock_openrouter_client()
        client.generate_with_vision.return_value = {
            "text": "Image description",
            "success": True,
            "cost_usd": 0.02,
            "model": "vision-model"
        }
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        mock_open.return_value.__enter__.return_value.read.return_value = b"fake image data"

        router = ModelRouter()
        result = router.chat_with_image("Describe this", "/path/to/image.png")

        self.assertEqual(result["text"], "Image description")
        self.assertTrue(result["success"])

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_chat_with_image_vision_fails_fallback_to_text(self, mock_fallback, mock_or_client):
        """Vision fails, falls back to text."""
        client = _mock_openrouter_client()
        client.generate_with_vision.side_effect = Exception("Vision unavailable")
        mock_or_client.return_value = client

        mock_fallback_instance = MagicMock()
        mock_fallback_instance.call_with_fallback.return_value = (
            {"text": "text fallback", "success": True, "cost_usd": 0.01},
            "xai"
        )
        mock_fallback.return_value = mock_fallback_instance

        router = ModelRouter()
        result = router.chat_with_image("Describe this", "/path/to/image.png")

        # Should fall back to text API
        self.assertTrue(result["success"])

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_chat_with_image_no_client(self, mock_fallback, mock_or_client):
        """No vision client, uses fallback text API."""
        client = _mock_openrouter_client()
        client.generate_with_vision.side_effect = Exception("No vision")
        mock_or_client.return_value = client

        mock_fallback_instance = MagicMock()
        mock_fallback_instance.call_with_fallback.return_value = (
            {"text": "text only", "success": True, "cost_usd": 0.01},
            "openrouter"
        )
        mock_fallback.return_value = mock_fallback_instance

        router = ModelRouter()
        result = router.chat_with_image("Describe this", "/path/to/image.png")

        self.assertTrue(result["success"])


# ---------------------------------------------------------------------------
# Generate image tests
# ---------------------------------------------------------------------------

class TestGenerateImage(unittest.TestCase):
    """Tests for ModelRouter.generate_image."""

    @patch("src.tools.image_gen.ImageGenerator")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_generate_image_success(self, mock_fallback, mock_or_client, mock_image_gen_class):
        """Successful local image generation."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        mock_gen = MagicMock()
        mock_gen.generate.return_value = {
            "success": True,
            "image_path": "/tmp/image.png",
            "duration_ms": 1000,
            "model_used": "sdxl-local"
        }
        mock_image_gen_class.return_value = mock_gen

        router = ModelRouter()
        result = router.generate_image("A beautiful landscape")

        self.assertTrue(result["success"])
        self.assertEqual(result["cost_usd"], 0.0)
        self.assertEqual(result["model"], "sdxl-local")

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_generate_image_diffusers_not_installed(self, mock_fallback, mock_or_client):
        """ImportError when diffusers not installed."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        with patch("src.tools.image_gen.ImageGenerator", side_effect=ImportError("diffusers")):
            router = ModelRouter()
            result = router.generate_image("A landscape")

        self.assertFalse(result["success"])
        self.assertIn("diffusers", result["error"])

    @patch("src.tools.image_gen.ImageGenerator")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_generate_image_general_exception(self, mock_fallback, mock_or_client, mock_image_gen_class):
        """General exception handled gracefully."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        mock_gen = MagicMock()
        mock_gen.generate.side_effect = Exception("GPU out of memory")
        mock_image_gen_class.return_value = mock_gen

        router = ModelRouter()
        result = router.generate_image("A landscape")

        self.assertFalse(result["success"])
        self.assertIn("GPU", result["error"])


# ---------------------------------------------------------------------------
# Close tests
# ---------------------------------------------------------------------------

class TestClose(unittest.TestCase):
    """Tests for ModelRouter.close."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_close_all_clients(self, mock_fallback, mock_or_client):
        """Close all cached clients."""
        client1 = _mock_openrouter_client(provider="xai")
        client2 = _mock_openrouter_client(provider="anthropic")

        mock_or_client.side_effect = [client1, client2]
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        router._fallback_clients["anthropic"] = client2

        router.close()

        client1.close.assert_called_once()
        client2.close.assert_called_once()

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_close_no_double_close(self, mock_fallback, mock_or_client):
        """Don't double-close active API client."""
        client = _mock_openrouter_client(provider="xai")
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        router.close()

        # Should only be called once (through fallback_clients)
        self.assertEqual(client.close.call_count, 1)


# ---------------------------------------------------------------------------
# Get stats tests
# ---------------------------------------------------------------------------

class TestGetStats(unittest.TestCase):
    """Tests for ModelRouter.get_stats."""

    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_get_stats_empty(self, mock_fallback, mock_or_client):
        """Stats on fresh router."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()

        router = ModelRouter()
        stats = router.get_stats()

        self.assertEqual(stats["api_used"], 0)
        self.assertEqual(stats["total_cost_usd"], 0.0)
        self.assertEqual(stats["avg_cost_per_query"], 0.0)

    @patch("src.monitoring.cost_tracker.get_cost_tracker")
    @patch("src.models.router.OpenRouterClient")
    @patch("src.models.router.ProviderFallbackChain")
    def test_get_stats_after_usage(self, mock_fallback, mock_or_client, mock_get_tracker):
        """Stats include usage counts."""
        client = _mock_openrouter_client()
        mock_or_client.return_value = client
        mock_fallback.return_value = MagicMock()
        mock_get_tracker.return_value = MagicMock()

        router = ModelRouter()
        response = {"cost_usd": 0.02, "model": "test"}
        router._record_success(response)
        router._record_success(response)

        stats = router.get_stats()

        self.assertEqual(stats["api_used"], 2)
        self.assertEqual(stats["total_cost_usd"], 0.04)
        self.assertAlmostEqual(stats["avg_cost_per_query"], 0.02)


if __name__ == "__main__":
    unittest.main()
