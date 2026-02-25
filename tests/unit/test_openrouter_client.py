"""Tests for src/models/openrouter_client.py — OpenRouterClient."""

import unittest
from unittest.mock import MagicMock, patch, call
import time
import sys

# Mock openai module before importing openrouter_client
if 'openai' not in sys.modules:
    sys.modules['openai'] = MagicMock()
    sys.modules['openai'].OpenAI = MagicMock()

from src.models.openrouter_client import (
    OpenRouterClient,
    LLMClient,
    _error_result,
    MAX_RETRIES,
    INITIAL_BACKOFF,
    TIMEOUT_SEC,
)


# ---------------------------------------------------------------------------
# Test fixtures and helpers
# ---------------------------------------------------------------------------

def _fake_get_api_key(provider):
    """Return API key for known providers, empty for unknown."""
    keys = {
        "openrouter": "sk-openrouter-test",
        "xai": "sk-xai-test",
        "anthropic": "sk-anthropic-test",
        "deepseek": "sk-deepseek-test",
        "openai": "sk-openai-test",
        "mistral": "sk-mistral-test",
    }
    return keys.get(provider, "")


def _fake_get_base_url(provider):
    """Return base URL for known providers."""
    urls = {
        "openrouter": "https://openrouter.ai/api/v1",
        "xai": "https://api.x.ai/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "deepseek": "https://api.deepseek.com",
        "openai": "https://api.openai.com/v1",
        "mistral": "https://api.mistral.ai/v1",
    }
    return urls.get(provider)


def _fake_get_default_model(provider):
    """Return default model for known providers."""
    models = {
        "openrouter": "x-ai/grok-4.1-fast",
        "xai": "grok-4-1-fast-reasoning",
        "anthropic": "claude-sonnet-4-6",
        "deepseek": "deepseek-chat",
        "openai": "gpt-4o-mini",
        "mistral": "mistral-medium-latest",
    }
    return models.get(provider)


def _fake_get_headers(provider):
    """Return headers for a provider."""
    if provider == "openrouter":
        return {"HTTP-Referer": "https://github.com/archi-agent", "X-Title": "Archi"}
    return {}


def _fake_get_pricing(model):
    """Return pricing for a model."""
    pricing = {
        "x-ai/grok-4.1-fast": {"input": 0.20, "output": 0.50},
        "grok-4-1-fast-reasoning": {"input": 0.20, "output": 0.50},
        "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
        "deepseek-chat": {"input": 0.14, "output": 0.28},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "mistral-medium-latest": {"input": 0.40, "output": 0.40},
    }
    return pricing.get(model, {"input": 0.50, "output": 2.00})


def _fake_providers_dict():
    """Return PROVIDERS dict for mocking."""
    return {
        "openrouter": {
            "api_key_env": "OPENROUTER_API_KEY",
            "base_url": "https://openrouter.ai/api/v1",
            "default_model": "x-ai/grok-4.1-fast",
        },
        "xai": {
            "api_key_env": "XAI_API_KEY",
            "base_url": "https://api.x.ai/v1",
            "default_model": "grok-4-1-fast-reasoning",
        },
        "anthropic": {
            "api_key_env": "ANTHROPIC_API_KEY",
            "base_url": "https://api.anthropic.com/v1",
            "default_model": "claude-sonnet-4-6",
        },
        "deepseek": {
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url": "https://api.deepseek.com",
            "default_model": "deepseek-chat",
        },
        "openai": {
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4o-mini",
        },
        "mistral": {
            "api_key_env": "MISTRAL_API_KEY",
            "base_url": "https://api.mistral.ai/v1",
            "default_model": "mistral-medium-latest",
        },
    }


def _mock_response(text="Hello, world!", model="test-model",
                   input_tokens=10, output_tokens=20):
    """Create a mock API response object."""
    usage = MagicMock()
    usage.prompt_tokens = input_tokens
    usage.completion_tokens = output_tokens
    usage.total_tokens = input_tokens + output_tokens

    message = MagicMock()
    message.content = text

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.model = model
    response.usage = usage

    return response


# ---------------------------------------------------------------------------
# TestInit — Initialization and validation
# ---------------------------------------------------------------------------

class TestInit(unittest.TestCase):
    """Tests for OpenRouterClient.__init__."""

    def setUp(self):
        """Set up patches for init tests."""
        self.providers_patch = patch("src.models.openrouter_client.PROVIDERS", _fake_providers_dict())
        self.api_key_patch = patch("src.models.openrouter_client.get_api_key", side_effect=_fake_get_api_key)
        self.base_url_patch = patch("src.models.openrouter_client.get_base_url", side_effect=_fake_get_base_url)
        self.default_model_patch = patch("src.models.openrouter_client.get_default_model", side_effect=_fake_get_default_model)
        self.headers_patch = patch("src.models.openrouter_client.get_headers", side_effect=_fake_get_headers)
        self.openai_patch = patch("openai.OpenAI")

        self.providers_patch.start()
        self.api_key_patch.start()
        self.base_url_patch.start()
        self.default_model_patch.start()
        self.headers_patch.start()

    def tearDown(self):
        """Clean up patches."""
        self.providers_patch.stop()
        self.api_key_patch.stop()
        self.base_url_patch.stop()
        self.default_model_patch.stop()
        self.headers_patch.stop()

    def test_init_with_defaults(self):
        """Test initialization with default provider (openrouter)."""
        with self.openai_patch as mock_openai:
            client = OpenRouterClient()
            self.assertEqual(client.provider, "openrouter")
            self.assertEqual(client._default_model, "x-ai/grok-4.1-fast")
            mock_openai.assert_called_once()

    def test_init_with_explicit_provider(self):
        """Test initialization with explicit provider."""
        with self.openai_patch as mock_openai:
            client = OpenRouterClient(provider="xai")
            self.assertEqual(client.provider, "xai")
            self.assertEqual(client._default_model, "grok-4-1-fast-reasoning")

    def test_init_unknown_provider_raises_error(self):
        """Test that unknown provider raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            OpenRouterClient(provider="unknown_provider")
        self.assertIn("Unknown provider", str(ctx.exception))

    @patch("src.models.openrouter_client.PROVIDERS", _fake_providers_dict())
    @patch("src.models.openrouter_client.get_api_key", return_value="")
    @patch("src.models.openrouter_client.get_base_url", side_effect=_fake_get_base_url)
    @patch("src.models.openrouter_client.get_default_model", side_effect=_fake_get_default_model)
    @patch("src.models.openrouter_client.get_headers", side_effect=_fake_get_headers)
    def test_init_missing_api_key_raises_error(self, *args):
        """Test that missing API key raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            OpenRouterClient(provider="openrouter")
        self.assertIn("OPENROUTER_API_KEY not set", str(ctx.exception))

    def test_init_explicit_api_key_overrides_env(self):
        """Test that explicit API key parameter overrides env."""
        with self.openai_patch:
            client = OpenRouterClient(provider="openrouter", api_key="explicit-key")
            self.assertEqual(client._api_key, "explicit-key")

    def test_init_explicit_base_url_overrides_default(self):
        """Test that explicit base_url overrides provider default."""
        with self.openai_patch:
            custom_url = "https://custom.url/v1"
            client = OpenRouterClient(provider="openrouter", base_url=custom_url)
            self.assertEqual(client._base_url, custom_url)

    def test_init_explicit_default_model(self):
        """Test that explicit default_model takes precedence."""
        with self.openai_patch:
            client = OpenRouterClient(
                provider="openrouter",
                default_model="custom-model"
            )
            self.assertEqual(client._default_model, "custom-model")

    def test_init_openrouter_env_override(self):
        """Test that OPENROUTER_MODEL env var takes precedence for openrouter."""
        with self.openai_patch, patch("os.environ", {"OPENROUTER_MODEL": "env-model"}):
            client = OpenRouterClient(provider="openrouter")
            self.assertEqual(client._default_model, "env-model")

    def test_init_openai_import_error(self):
        """Test that missing openai package raises ImportError."""
        with patch("openai.OpenAI", side_effect=ImportError("No module named 'openai'")):
            with self.assertRaises(ImportError) as ctx:
                OpenRouterClient(provider="openrouter")
            self.assertIn("openai package required", str(ctx.exception))

    def test_init_timeout_parameter(self):
        """Test that custom timeout is stored."""
        with self.openai_patch:
            client = OpenRouterClient(provider="openrouter", timeout=120.0)
            self.assertEqual(client._timeout, 120.0)

    def test_init_default_timeout(self):
        """Test that default timeout matches TIMEOUT_SEC."""
        with self.openai_patch:
            client = OpenRouterClient(provider="openrouter")
            self.assertEqual(client._timeout, TIMEOUT_SEC)

    def test_init_runtime_model_starts_none(self):
        """Test that runtime model starts as None."""
        with self.openai_patch:
            client = OpenRouterClient(provider="openrouter")
            self.assertIsNone(client._runtime_model)

    def test_init_closed_flag_starts_false(self):
        """Test that closed flag starts as False."""
        with self.openai_patch:
            client = OpenRouterClient(provider="openrouter")
            self.assertFalse(client._closed)


# ---------------------------------------------------------------------------
# TestModelSwitching — Model switching operations
# ---------------------------------------------------------------------------

class TestModelSwitching(unittest.TestCase):
    """Tests for model switching: switch_model, get_active_model, reset_model."""

    def setUp(self):
        """Set up client for model switching tests."""
        with patch("src.models.openrouter_client.PROVIDERS", _fake_providers_dict()), \
             patch("src.models.openrouter_client.get_api_key", side_effect=_fake_get_api_key), \
             patch("src.models.openrouter_client.get_base_url", side_effect=_fake_get_base_url), \
             patch("src.models.openrouter_client.get_default_model", side_effect=_fake_get_default_model), \
             patch("src.models.openrouter_client.get_headers", side_effect=_fake_get_headers), \
             patch("openai.OpenAI"):
            self.client = OpenRouterClient(provider="openrouter")

    def test_switch_model_sets_runtime_model(self):
        """Test that switch_model sets the runtime model."""
        result = self.client.switch_model("new-model-id")
        self.assertEqual(result, "new-model-id")
        self.assertEqual(self.client._runtime_model, "new-model-id")

    def test_switch_model_strips_whitespace(self):
        """Test that switch_model strips leading/trailing whitespace."""
        self.client.switch_model("  trimmed-model  ")
        self.assertEqual(self.client._runtime_model, "trimmed-model")

    def test_get_active_model_returns_runtime_when_set(self):
        """Test that get_active_model returns runtime model if set."""
        self.client.switch_model("runtime-model")
        self.assertEqual(self.client.get_active_model(), "runtime-model")

    def test_get_active_model_returns_default_when_no_runtime(self):
        """Test that get_active_model returns default when no runtime override."""
        self.assertEqual(self.client.get_active_model(), "x-ai/grok-4.1-fast")

    def test_reset_model_clears_runtime(self):
        """Test that reset_model clears the runtime model override."""
        self.client.switch_model("runtime-model")
        result = self.client.reset_model()
        self.assertIsNone(self.client._runtime_model)
        self.assertEqual(result, "x-ai/grok-4.1-fast")

    def test_reset_model_returns_default(self):
        """Test that reset_model returns the default model."""
        self.client.switch_model("runtime-model")
        result = self.client.reset_model()
        self.assertEqual(result, self.client._default_model)


# ---------------------------------------------------------------------------
# TestGenerate — Public generate() method
# ---------------------------------------------------------------------------

class TestGenerate(unittest.TestCase):
    """Tests for the public generate() method."""

    def setUp(self):
        """Set up client for generate tests."""
        with patch("src.models.openrouter_client.PROVIDERS", _fake_providers_dict()), \
             patch("src.models.openrouter_client.get_api_key", side_effect=_fake_get_api_key), \
             patch("src.models.openrouter_client.get_base_url", side_effect=_fake_get_base_url), \
             patch("src.models.openrouter_client.get_default_model", side_effect=_fake_get_default_model), \
             patch("src.models.openrouter_client.get_headers", side_effect=_fake_get_headers), \
             patch("openai.OpenAI"):
            self.client = OpenRouterClient(provider="openrouter")

    @patch("src.models.openrouter_client.OpenRouterClient._generate_chat_completions")
    def test_generate_delegates_to_internal_method(self, mock_internal):
        """Test that generate delegates to _generate_chat_completions."""
        mock_internal.return_value = {"success": True, "text": "result"}
        self.client.generate(prompt="test", max_tokens=100, temperature=0.7)
        mock_internal.assert_called_once()

    @patch("src.models.openrouter_client.OpenRouterClient._generate_chat_completions")
    def test_generate_uses_runtime_model(self, mock_internal):
        """Test that generate uses runtime model if set."""
        mock_internal.return_value = {"success": True}
        self.client.switch_model("runtime-model")
        self.client.generate(prompt="test")
        args, kwargs = mock_internal.call_args
        self.assertEqual(args[1], "runtime-model")  # model is second arg

    @patch("src.models.openrouter_client.OpenRouterClient._generate_chat_completions")
    def test_generate_uses_explicit_model(self, mock_internal):
        """Test that explicit model parameter takes precedence."""
        mock_internal.return_value = {"success": True}
        self.client.generate(prompt="test", model="explicit-model")
        args, kwargs = mock_internal.call_args
        self.assertEqual(args[1], "explicit-model")

    @patch("src.models.openrouter_client.OpenRouterClient._generate_chat_completions")
    def test_generate_uses_default_model(self, mock_internal):
        """Test that default model is used if no runtime or explicit model."""
        mock_internal.return_value = {"success": True}
        self.client.generate(prompt="test")
        args, kwargs = mock_internal.call_args
        self.assertEqual(args[1], "x-ai/grok-4.1-fast")

    @patch("src.models.openrouter_client.OpenRouterClient._generate_chat_completions")
    def test_generate_passes_prompt(self, mock_internal):
        """Test that prompt is passed correctly."""
        mock_internal.return_value = {"success": True}
        self.client.generate(prompt="test prompt")
        args, _ = mock_internal.call_args
        self.assertEqual(args[0], "test prompt")

    @patch("src.models.openrouter_client.OpenRouterClient._generate_chat_completions")
    def test_generate_passes_max_tokens(self, mock_internal):
        """Test that max_tokens is passed correctly."""
        mock_internal.return_value = {"success": True}
        self.client.generate(prompt="test", max_tokens=200)
        args, _ = mock_internal.call_args
        self.assertEqual(args[2], 200)

    @patch("src.models.openrouter_client.OpenRouterClient._generate_chat_completions")
    def test_generate_passes_temperature(self, mock_internal):
        """Test that temperature is passed correctly."""
        mock_internal.return_value = {"success": True}
        self.client.generate(prompt="test", temperature=0.9)
        args, _ = mock_internal.call_args
        self.assertEqual(args[3], 0.9)

    @patch("src.models.openrouter_client.OpenRouterClient._generate_chat_completions")
    def test_generate_passes_system_prompt(self, mock_internal):
        """Test that system_prompt is passed as kwarg."""
        mock_internal.return_value = {"success": True}
        self.client.generate(prompt="test", system_prompt="Be helpful")
        _, kwargs = mock_internal.call_args
        self.assertEqual(kwargs.get("system_prompt"), "Be helpful")

    @patch("src.models.openrouter_client.OpenRouterClient._generate_chat_completions")
    def test_generate_passes_messages(self, mock_internal):
        """Test that messages array is passed as kwarg."""
        mock_internal.return_value = {"success": True}
        msgs = [{"role": "user", "content": "hello"}]
        self.client.generate(prompt="", messages=msgs)
        _, kwargs = mock_internal.call_args
        self.assertEqual(kwargs.get("messages"), msgs)

    @patch("src.models.openrouter_client.OpenRouterClient._generate_chat_completions")
    def test_generate_ignores_enable_web_search(self, mock_internal):
        """Test that enable_web_search is ignored (logged but not used)."""
        mock_internal.return_value = {"success": True}
        # Should not raise an error or change behavior
        self.client.generate(prompt="test", enable_web_search=True)
        mock_internal.assert_called_once()


# ---------------------------------------------------------------------------
# TestGenerateWithVision — Vision API
# ---------------------------------------------------------------------------

class TestGenerateWithVision(unittest.TestCase):
    """Tests for generate_with_vision() method."""

    def setUp(self):
        """Set up client for vision tests."""
        with patch("src.models.openrouter_client.PROVIDERS", _fake_providers_dict()), \
             patch("src.models.openrouter_client.get_api_key", side_effect=_fake_get_api_key), \
             patch("src.models.openrouter_client.get_base_url", side_effect=_fake_get_base_url), \
             patch("src.models.openrouter_client.get_default_model", side_effect=_fake_get_default_model), \
             patch("src.models.openrouter_client.get_headers", side_effect=_fake_get_headers), \
             patch("openai.OpenAI"):
            self.client = OpenRouterClient(provider="openrouter")

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_with_vision_success(self, mock_time, mock_pricing):
        """Test successful vision API call."""
        mock_time.side_effect = [0.0, 0.1]  # start, end
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response(text="Image description",
                                       model="test-vision-model",
                                       input_tokens=50, output_tokens=30)
        )

        result = self.client.generate_with_vision(
            prompt="Describe this image",
            image_base64="iVBORw0KG...",
            image_media_type="image/png",
            max_tokens=200,
            temperature=0.2
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["text"], "Image description")
        self.assertEqual(result["input_tokens"], 50)
        self.assertEqual(result["output_tokens"], 30)
        self.assertEqual(result["model"], "test-vision-model")
        self.assertIn("cost_usd", result)
        self.assertIn("duration_ms", result)

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_with_vision_error_handling(self, mock_time, mock_pricing):
        """Test vision API error handling."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._client.chat.completions.create = MagicMock(
            side_effect=Exception("Vision API failed")
        )

        result = self.client.generate_with_vision(
            prompt="Describe this",
            image_base64="invalid"
        )

        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertEqual(result["cost_usd"], 0.0)

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_with_vision_explicit_model(self, mock_time, mock_pricing):
        """Test vision with explicit model parameter."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response()
        )

        self.client.generate_with_vision(
            prompt="test",
            image_base64="data",
            model="vision-model"
        )

        call_kwargs = self.client._client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["model"], "vision-model")

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    @patch("os.environ", {"OPENROUTER_VISION_MODEL": "env-vision-model"})
    def test_generate_with_vision_env_model(self, mock_time, mock_pricing):
        """Test vision with OPENROUTER_VISION_MODEL env var."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response()
        )

        self.client.generate_with_vision(
            prompt="test",
            image_base64="data"
        )

        call_kwargs = self.client._client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["model"], "env-vision-model")

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_with_vision_image_url_format(self, mock_time, mock_pricing):
        """Test that image is formatted correctly in content."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response()
        )

        image_b64 = "abc123def456"
        self.client.generate_with_vision(
            prompt="test",
            image_base64=image_b64,
            image_media_type="image/jpeg"
        )

        call_kwargs = self.client._client.chat.completions.create.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        image_content = [c for c in content if c["type"] == "image_url"][0]
        expected_url = f"data:image/jpeg;base64,{image_b64}"
        self.assertEqual(image_content["image_url"]["url"], expected_url)


# ---------------------------------------------------------------------------
# TestGenerateChatCompletions — Internal chat completions with retry logic
# ---------------------------------------------------------------------------

class TestGenerateChatCompletions(unittest.TestCase):
    """Tests for _generate_chat_completions() and retry logic."""

    def setUp(self):
        """Set up client for chat completions tests."""
        with patch("src.models.openrouter_client.PROVIDERS", _fake_providers_dict()), \
             patch("src.models.openrouter_client.get_api_key", side_effect=_fake_get_api_key), \
             patch("src.models.openrouter_client.get_base_url", side_effect=_fake_get_base_url), \
             patch("src.models.openrouter_client.get_default_model", side_effect=_fake_get_default_model), \
             patch("src.models.openrouter_client.get_headers", side_effect=_fake_get_headers), \
             patch("openai.OpenAI"):
            self.client = OpenRouterClient(provider="openrouter")

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_chat_completions_success_first_try(self, mock_time, mock_pricing):
        """Test successful chat completion on first attempt."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response(text="Hello!", model="test-model",
                                       input_tokens=5, output_tokens=2)
        )

        result = self.client._generate_chat_completions(
            prompt="Hi", model="test-model", max_tokens=100, temperature=0.7
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["text"], "Hello!")
        self.client._client.chat.completions.create.assert_called_once()

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    @patch("src.models.openrouter_client.time.sleep")
    def test_generate_chat_completions_retry_success(self, mock_sleep, mock_time, mock_pricing):
        """Test successful retry after initial failure."""
        mock_time.side_effect = [0.0, 0.15, 0.15, 0.25]  # start, after fail, after sleep, end

        # First call fails, second succeeds
        self.client._client.chat.completions.create = MagicMock(
            side_effect=[
                Exception("Temporary error"),
                _mock_response(text="Success after retry", model="test-model")
            ]
        )

        result = self.client._generate_chat_completions(
            prompt="test", model="test-model", max_tokens=100, temperature=0.7
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["text"], "Success after retry")
        # Should have slept with backoff
        mock_sleep.assert_called_once()
        backoff = mock_sleep.call_args[0][0]
        self.assertEqual(backoff, INITIAL_BACKOFF * (2 ** 0))

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    @patch("src.models.openrouter_client.time.sleep")
    def test_generate_chat_completions_exponential_backoff(self, mock_sleep, mock_time, mock_pricing):
        """Test exponential backoff timing in retries."""
        mock_time.side_effect = [0.0, 0.1, 0.1, 0.2, 0.2, 0.3]

        self.client._client.chat.completions.create = MagicMock(
            side_effect=[
                Exception("Fail 1"),
                Exception("Fail 2"),
                _mock_response(text="Success")
            ]
        )

        result = self.client._generate_chat_completions(
            prompt="test", model="test-model", max_tokens=100, temperature=0.7
        )

        self.assertTrue(result["success"])
        # Check backoff calls
        self.assertEqual(mock_sleep.call_count, 2)
        calls = [c[0][0] for c in mock_sleep.call_args_list]
        self.assertEqual(calls[0], INITIAL_BACKOFF * (2 ** 0))  # 1.0
        self.assertEqual(calls[1], INITIAL_BACKOFF * (2 ** 1))  # 2.0

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    @patch("src.models.openrouter_client.time.sleep")
    def test_generate_chat_completions_all_retries_fail(self, mock_sleep, mock_time, mock_pricing):
        """Test that all retries exhausted returns error."""
        mock_time.side_effect = [0.0, 0.1, 0.1, 0.2, 0.2, 0.3, 0.3, 0.4]

        self.client._client.chat.completions.create = MagicMock(
            side_effect=Exception("API down")
        )

        result = self.client._generate_chat_completions(
            prompt="test", model="test-model", max_tokens=100, temperature=0.7
        )

        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertEqual(self.client._client.chat.completions.create.call_count, MAX_RETRIES)
        self.assertEqual(mock_sleep.call_count, MAX_RETRIES - 1)

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_chat_completions_closed_client_initial(self, mock_time, mock_pricing):
        """Test that closed client returns error immediately."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._closed = True

        result = self.client._generate_chat_completions(
            prompt="test", model="test-model", max_tokens=100, temperature=0.7
        )

        self.assertFalse(result["success"])
        self.assertIn("client closed", result["error"])
        self.client._client.chat.completions.create.assert_not_called()

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    @patch("src.models.openrouter_client.time.sleep")
    def test_generate_chat_completions_closed_during_retry(self, mock_sleep, mock_time, mock_pricing):
        """Test that client closure during retry returns error."""
        mock_time.side_effect = [0.0, 0.1, 0.2, 0.3]

        def fail_then_close(*args, **kwargs):
            raise Exception("API error")

        self.client._client.chat.completions.create = MagicMock(side_effect=fail_then_close)

        # Close client during first exception handler
        original_sleep = time.sleep
        def sleep_and_close(duration):
            self.client._closed = True

        with patch("src.models.openrouter_client.time.sleep", side_effect=sleep_and_close):
            result = self.client._generate_chat_completions(
                prompt="test", model="test-model", max_tokens=100, temperature=0.7
            )

        self.assertFalse(result["success"])
        self.assertIn("client closed", result["error"])

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_chat_completions_messages_only(self, mock_time, mock_pricing):
        """Test chat completions with messages array (no system/prompt)."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response()
        )

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "How are you?"}
        ]

        result = self.client._generate_chat_completions(
            prompt="", model="test-model", max_tokens=100, temperature=0.7,
            messages=messages
        )

        self.assertTrue(result["success"])
        call_kwargs = self.client._client.chat.completions.create.call_args[1]
        # Should pass a shallow copy of messages
        self.assertEqual(len(call_kwargs["messages"]), 3)
        self.assertEqual(call_kwargs["messages"][0]["role"], "user")

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_chat_completions_system_prompt_only(self, mock_time, mock_pricing):
        """Test chat completions with system_prompt + prompt."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response()
        )

        result = self.client._generate_chat_completions(
            prompt="User message", model="test-model", max_tokens=100, temperature=0.7,
            system_prompt="You are helpful"
        )

        self.assertTrue(result["success"])
        call_kwargs = self.client._client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "You are helpful")
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "User message")

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_chat_completions_prompt_only(self, mock_time, mock_pricing):
        """Test chat completions with prompt only."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response()
        )

        result = self.client._generate_chat_completions(
            prompt="Just a user prompt", model="test-model", max_tokens=100, temperature=0.7
        )

        self.assertTrue(result["success"])
        call_kwargs = self.client._client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "Just a user prompt")

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_chat_completions_api_params(self, mock_time, mock_pricing):
        """Test that API parameters are passed correctly."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response()
        )

        self.client._generate_chat_completions(
            prompt="test", model="custom-model", max_tokens=250, temperature=0.9
        )

        call_kwargs = self.client._client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["model"], "custom-model")
        self.assertEqual(call_kwargs["max_tokens"], 250)
        self.assertEqual(call_kwargs["temperature"], 0.9)
        self.assertEqual(call_kwargs["timeout"], self.client._timeout)

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_chat_completions_cost_calculation(self, mock_time, mock_pricing):
        """Test cost calculation from tokens."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response(model="x-ai/grok-4.1-fast",
                                       input_tokens=100, output_tokens=50)
        )

        result = self.client._generate_chat_completions(
            prompt="test", model="x-ai/grok-4.1-fast", max_tokens=100, temperature=0.7
        )

        # Pricing: input=0.20/1M, output=0.50/1M
        # Cost = 100 * (0.20/1M) + 50 * (0.50/1M) = 0.00002 + 0.000025 = 0.000045
        expected_cost = 100 * (0.20 / 1_000_000) + 50 * (0.50 / 1_000_000)
        self.assertAlmostEqual(result["cost_usd"], expected_cost, places=6)

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_chat_completions_response_fields(self, mock_time, mock_pricing):
        """Test all response fields are populated correctly."""
        mock_time.side_effect = [0.0, 0.15]  # 150ms duration
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response(text="Test response", model="test-model",
                                       input_tokens=10, output_tokens=5)
        )

        result = self.client._generate_chat_completions(
            prompt="test", model="test-model", max_tokens=100, temperature=0.7
        )

        self.assertEqual(result["text"], "Test response")
        self.assertEqual(result["input_tokens"], 10)
        self.assertEqual(result["output_tokens"], 5)
        self.assertEqual(result["tokens"], 15)
        self.assertEqual(result["model"], "test-model")
        self.assertTrue(result["success"])
        self.assertGreaterEqual(result["duration_ms"], 0)
        self.assertIn("cost_usd", result)

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    @patch("src.models.openrouter_client.time.perf_counter")
    def test_generate_chat_completions_messages_shallow_copy(self, mock_time, mock_pricing):
        """Test that messages is shallow-copied (not mutated)."""
        mock_time.side_effect = [0.0, 0.1]
        self.client._client.chat.completions.create = MagicMock(
            return_value=_mock_response()
        )

        original_messages = [{"role": "user", "content": "test"}]
        self.client._generate_chat_completions(
            prompt="", model="test", max_tokens=100, temperature=0.7,
            messages=original_messages
        )

        # Original should be unchanged
        self.assertEqual(len(original_messages), 1)


# ---------------------------------------------------------------------------
# TestEstimateCost — Cost estimation
# ---------------------------------------------------------------------------

class TestEstimateCost(unittest.TestCase):
    """Tests for _estimate_cost() static method."""

    def setUp(self):
        """Set up client for cost estimation tests."""
        with patch("src.models.openrouter_client.PROVIDERS", _fake_providers_dict()), \
             patch("src.models.openrouter_client.get_api_key", side_effect=_fake_get_api_key), \
             patch("src.models.openrouter_client.get_base_url", side_effect=_fake_get_base_url), \
             patch("src.models.openrouter_client.get_default_model", side_effect=_fake_get_default_model), \
             patch("src.models.openrouter_client.get_headers", side_effect=_fake_get_headers), \
             patch("openai.OpenAI"):
            self.client = OpenRouterClient(provider="openrouter")

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    def test_estimate_cost_basic(self, mock_pricing):
        """Test basic cost calculation."""
        cost = OpenRouterClient._estimate_cost("x-ai/grok-4.1-fast", 100, 50)
        expected = 100 * (0.20 / 1_000_000) + 50 * (0.50 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=8)

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    def test_estimate_cost_zero_tokens(self, mock_pricing):
        """Test cost with zero tokens."""
        cost = OpenRouterClient._estimate_cost("x-ai/grok-4.1-fast", 0, 0)
        self.assertEqual(cost, 0.0)

    @patch("src.models.openrouter_client.get_pricing", side_effect=_fake_get_pricing)
    def test_estimate_cost_different_models(self, mock_pricing):
        """Test cost calculation for different models."""
        cost1 = OpenRouterClient._estimate_cost("grok-4-1-fast-reasoning", 1000, 1000)
        cost2 = OpenRouterClient._estimate_cost("claude-sonnet-4-6", 1000, 1000)
        # Claude should be more expensive
        self.assertGreater(cost2, cost1)

    @patch("src.models.openrouter_client.get_pricing")
    def test_estimate_cost_fallback_pricing(self, mock_pricing):
        """Test that fallback pricing is used for unknown models."""
        mock_pricing.return_value = {"input": 0.50, "output": 2.00}
        cost = OpenRouterClient._estimate_cost("unknown-model", 100, 50)
        expected = 100 * (0.50 / 1_000_000) + 50 * (2.00 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=8)


# ---------------------------------------------------------------------------
# TestClose — Client closure
# ---------------------------------------------------------------------------

class TestClose(unittest.TestCase):
    """Tests for close() method."""

    def setUp(self):
        """Set up client for close tests."""
        with patch("src.models.openrouter_client.PROVIDERS", _fake_providers_dict()), \
             patch("src.models.openrouter_client.get_api_key", side_effect=_fake_get_api_key), \
             patch("src.models.openrouter_client.get_base_url", side_effect=_fake_get_base_url), \
             patch("src.models.openrouter_client.get_default_model", side_effect=_fake_get_default_model), \
             patch("src.models.openrouter_client.get_headers", side_effect=_fake_get_headers), \
             patch("openai.OpenAI"):
            self.client = OpenRouterClient(provider="openrouter")

    def test_close_sets_closed_flag(self):
        """Test that close() sets _closed flag."""
        self.assertFalse(self.client._closed)
        self.client.close()
        self.assertTrue(self.client._closed)

    def test_close_calls_client_close(self):
        """Test that close() calls underlying client.close()."""
        self.client._client.close = MagicMock()
        self.client.close()
        self.client._client.close.assert_called_once()

    def test_close_handles_client_close_error(self):
        """Test that close() handles errors from client.close()."""
        self.client._client.close = MagicMock(side_effect=Exception("Close failed"))
        # Should not raise
        self.client.close()
        self.assertTrue(self.client._closed)

    def test_close_idempotent(self):
        """Test that close() can be called multiple times safely."""
        self.client._client.close = MagicMock()
        self.client.close()
        self.client.close()
        # Should not raise error on second call
        self.assertTrue(self.client._closed)


# ---------------------------------------------------------------------------
# TestIsAvailable — API key availability check
# ---------------------------------------------------------------------------

class TestIsAvailable(unittest.TestCase):
    """Tests for is_available() method."""

    def setUp(self):
        """Set up for is_available tests."""
        self.providers_patch = patch("src.models.openrouter_client.PROVIDERS", _fake_providers_dict())
        self.api_key_patch = patch("src.models.openrouter_client.get_api_key", side_effect=_fake_get_api_key)
        self.base_url_patch = patch("src.models.openrouter_client.get_base_url", side_effect=_fake_get_base_url)
        self.default_model_patch = patch("src.models.openrouter_client.get_default_model", side_effect=_fake_get_default_model)
        self.headers_patch = patch("src.models.openrouter_client.get_headers", side_effect=_fake_get_headers)
        self.openai_patch = patch("openai.OpenAI")

        self.providers_patch.start()
        self.api_key_patch.start()
        self.base_url_patch.start()
        self.default_model_patch.start()
        self.headers_patch.start()

    def tearDown(self):
        """Clean up patches."""
        self.providers_patch.stop()
        self.api_key_patch.stop()
        self.base_url_patch.stop()
        self.default_model_patch.stop()
        self.headers_patch.stop()

    def test_is_available_with_env_var(self):
        """Test is_available returns True when env var is set."""
        with self.openai_patch, patch("os.environ", {"OPENROUTER_API_KEY": "sk-test"}):
            client = OpenRouterClient(provider="openrouter")
            self.assertTrue(client.is_available())

    def test_is_available_without_env_var(self):
        """Test is_available returns False when env var is not set."""
        with self.openai_patch:
            with patch("src.models.openrouter_client.get_api_key", return_value="sk-test"):
                client = OpenRouterClient(provider="openrouter")

            with patch("os.environ", {}):
                self.assertFalse(client.is_available())

    def test_is_available_for_different_provider(self):
        """Test is_available for non-openrouter provider."""
        with self.openai_patch, patch("os.environ", {"XAI_API_KEY": "sk-xai"}):
            client = OpenRouterClient(provider="xai")
            self.assertTrue(client.is_available())


# ---------------------------------------------------------------------------
# TestErrorResult — Error result helper
# ---------------------------------------------------------------------------

class TestErrorResult(unittest.TestCase):
    """Tests for _error_result() helper function."""

    def test_error_result_structure(self):
        """Test error result has all required fields."""
        result = _error_result("Something went wrong", 0.5)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Something went wrong")
        self.assertIn("duration_ms", result)

    def test_error_result_duration_conversion(self):
        """Test duration is converted to milliseconds."""
        result = _error_result("Error", 1.5)
        self.assertEqual(result["duration_ms"], 1500)

    def test_error_result_zero_duration(self):
        """Test error result with zero duration."""
        result = _error_result("Fast error", 0.0)
        self.assertEqual(result["duration_ms"], 0)

    def test_error_result_default_values(self):
        """Test error result has correct default values."""
        result = _error_result("Test error", 0.1)
        self.assertEqual(result["text"], "")
        self.assertEqual(result["tokens"], 0)
        self.assertEqual(result["input_tokens"], 0)
        self.assertEqual(result["output_tokens"], 0)
        self.assertEqual(result["cost_usd"], 0.0)
        self.assertEqual(result["model"], "")


# ---------------------------------------------------------------------------
# TestLLMClientAlias — Alias compatibility
# ---------------------------------------------------------------------------

class TestLLMClientAlias(unittest.TestCase):
    """Tests for LLMClient alias."""

    def test_llmclient_is_openrouterclient(self):
        """Test that LLMClient is an alias for OpenRouterClient."""
        self.assertIs(LLMClient, OpenRouterClient)


# ---------------------------------------------------------------------------
# TestProviderProperty — Provider property accessor
# ---------------------------------------------------------------------------

class TestProviderProperty(unittest.TestCase):
    """Tests for provider property."""

    def setUp(self):
        """Set up client for property tests."""
        self.providers_patch = patch("src.models.openrouter_client.PROVIDERS", _fake_providers_dict())
        self.api_key_patch = patch("src.models.openrouter_client.get_api_key", side_effect=_fake_get_api_key)
        self.base_url_patch = patch("src.models.openrouter_client.get_base_url", side_effect=_fake_get_base_url)
        self.default_model_patch = patch("src.models.openrouter_client.get_default_model", side_effect=_fake_get_default_model)
        self.headers_patch = patch("src.models.openrouter_client.get_headers", side_effect=_fake_get_headers)
        self.openai_patch = patch("openai.OpenAI")

        self.providers_patch.start()
        self.api_key_patch.start()
        self.base_url_patch.start()
        self.default_model_patch.start()
        self.headers_patch.start()

        with self.openai_patch:
            self.client = OpenRouterClient(provider="openrouter")

    def tearDown(self):
        """Clean up patches."""
        self.providers_patch.stop()
        self.api_key_patch.stop()
        self.base_url_patch.stop()
        self.default_model_patch.stop()
        self.headers_patch.stop()

    def test_provider_property_returns_provider(self):
        """Test provider property returns the provider name."""
        self.assertEqual(self.client.provider, "openrouter")

    def test_provider_property_for_different_provider(self):
        """Test provider property for different provider."""
        with self.openai_patch:
            client = OpenRouterClient(provider="xai")
            self.assertEqual(client.provider, "xai")


if __name__ == "__main__":
    unittest.main()
