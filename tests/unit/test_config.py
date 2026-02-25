"""Comprehensive unit tests for src/utils/config.py.

Tests caching behavior, YAML loading, configuration merging, persona generation,
quote matching, and reload mechanics. Session 150.
"""

import logging
import random
from unittest.mock import patch, MagicMock, mock_open

import pytest

import src.utils.config as config


@pytest.fixture(autouse=True)
def reset_caches():
    """Reset all module-level caches before and after each test."""
    # Store original cache references
    original_rules = config._rules_cache
    original_heartbeat = config._heartbeat_cache
    original_identity = config._identity_cache
    original_personality = config._personality_cache
    original_persona_prompt = config._persona_prompt_cache
    original_hooks = config._reload_hooks[:]

    # Reset before test
    config._rules_cache = None
    config._heartbeat_cache = None
    config._identity_cache = None
    config._personality_cache = None
    config._persona_prompt_cache = None
    config._reload_hooks = []

    yield

    # Restore after test
    config._rules_cache = original_rules
    config._heartbeat_cache = original_heartbeat
    config._identity_cache = original_identity
    config._personality_cache = original_personality
    config._persona_prompt_cache = original_persona_prompt
    config._reload_hooks = original_hooks


# ─────────────────────────────────────────────────────────────────────────────
# TestLoadYaml
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadYaml:
    """Tests for _load_yaml() function."""

    def test_load_yaml_success(self):
        """Successfully load and parse a valid YAML file."""
        yaml_content = "key: value\nnested:\n  inner: 42"
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch("src.utils.paths.base_path", return_value="/base"):
                result = config._load_yaml("test.yaml")
                assert result == {"key": "value", "nested": {"inner": 42}}

    def test_load_yaml_empty_file(self):
        """Empty YAML file returns empty dict."""
        with patch("builtins.open", mock_open(read_data="")):
            with patch("src.utils.paths.base_path", return_value="/base"):
                result = config._load_yaml("empty.yaml")
                assert result == {}

    def test_load_yaml_file_not_found(self):
        """Missing file returns empty dict and logs debug message."""
        with patch("builtins.open", side_effect=OSError("File not found")):
            with patch("src.utils.paths.base_path", return_value="/base"):
                with patch.object(config.logger, "debug") as mock_debug:
                    result = config._load_yaml("missing.yaml")
                    assert result == {}
                    mock_debug.assert_called_once()

    def test_load_yaml_invalid_yaml(self):
        """Invalid YAML returns empty dict and logs debug message."""
        invalid_yaml = "key: value\n  bad indent:\n    broken"
        with patch("builtins.open", mock_open(read_data=invalid_yaml)):
            with patch("src.utils.paths.base_path", return_value="/base"):
                with patch.object(config.logger, "debug") as mock_debug:
                    result = config._load_yaml("bad.yaml")
                    assert result == {}
                    mock_debug.assert_called_once()

    def test_load_yaml_path_construction(self):
        """YAML file path is constructed correctly."""
        with patch("builtins.open", mock_open(read_data="key: val")):
            with patch("src.utils.config.base_path", return_value="/project/root"):
                result = config._load_yaml("rules.yaml")
                assert result == {"key": "val"}


# ─────────────────────────────────────────────────────────────────────────────
# TestCaching
# ─────────────────────────────────────────────────────────────────────────────

class TestCaching:
    """Tests for caching behavior of _rules(), _heartbeat(), etc."""

    def test_rules_caches_result(self):
        """_rules() caches result on first call."""
        data = {"key": "value"}
        with patch("src.utils.config._load_yaml", return_value=data) as mock_load:
            result1 = config._rules()
            result2 = config._rules()
            # Should only load once
            assert mock_load.call_count == 1
            assert result1 is result2

    def test_heartbeat_caches_result(self):
        """_heartbeat() caches result on first call."""
        data = {"cycle": 300}
        with patch("src.utils.config._load_yaml", return_value=data) as mock_load:
            result1 = config._heartbeat()
            result2 = config._heartbeat()
            assert mock_load.call_count == 1
            assert result1 is result2

    def test_identity_caches_result(self):
        """_identity() caches result on first call."""
        data = {"user": "alice"}
        with patch("src.utils.config._load_yaml", return_value=data) as mock_load:
            result1 = config._identity()
            result2 = config._identity()
            assert mock_load.call_count == 1
            assert result1 is result2

    def test_personality_caches_result(self):
        """_personality() caches result on first call."""
        data = {"tone": "direct"}
        with patch("src.utils.config._load_yaml", return_value=data) as mock_load:
            result1 = config._personality()
            result2 = config._personality()
            assert mock_load.call_count == 1
            assert result1 is result2


# ─────────────────────────────────────────────────────────────────────────────
# TestReload
# ─────────────────────────────────────────────────────────────────────────────

class TestReload:
    """Tests for reload() and on_reload() mechanisms."""

    def test_reload_clears_all_caches(self):
        """reload() clears all cache variables."""
        with patch("src.utils.config._load_yaml", return_value={"data": "test"}):
            # Populate caches
            _ = config._rules()
            _ = config._heartbeat()
            _ = config._identity()
            _ = config._personality()
            _ = config._persona_prompt_cache
            assert config._rules_cache is not None
            assert config._heartbeat_cache is not None
            assert config._identity_cache is not None
            assert config._personality_cache is not None

            # Clear caches
            config.reload()
            assert config._rules_cache is None
            assert config._heartbeat_cache is None
            assert config._identity_cache is None
            assert config._personality_cache is None
            assert config._persona_prompt_cache is None

    def test_reload_calls_registered_hooks(self):
        """reload() calls all registered hooks."""
        hook1 = MagicMock()
        hook2 = MagicMock()
        config.on_reload(hook1)
        config.on_reload(hook2)

        config.reload()

        hook1.assert_called_once()
        hook2.assert_called_once()

    def test_reload_continues_on_hook_exception(self):
        """reload() continues calling hooks even if one raises."""
        hook1 = MagicMock(side_effect=Exception("Hook error"))
        hook2 = MagicMock()
        config.on_reload(hook1)
        config.on_reload(hook2)

        # Should not raise
        config.reload()

        hook1.assert_called_once()
        hook2.assert_called_once()

    def test_on_reload_registers_hook(self):
        """on_reload() adds hook to the hooks list."""
        hook = MagicMock()
        initial_count = len(config._reload_hooks)
        config.on_reload(hook)
        assert len(config._reload_hooks) == initial_count + 1
        assert hook in config._reload_hooks


# ─────────────────────────────────────────────────────────────────────────────
# TestGetUserName
# ─────────────────────────────────────────────────────────────────────────────

class TestGetUserName:
    """Tests for get_user_name()."""

    def test_returns_user_name_from_identity(self):
        """Returns name from archi_identity.yaml."""
        with patch("src.utils.config._identity", return_value={"user_context": {"name": "Alice"}}):
            assert config.get_user_name() == "Alice"

    def test_returns_default_when_no_name(self):
        """Returns 'User' when no name is configured."""
        with patch("src.utils.config._identity", return_value={}):
            assert config.get_user_name() == "User"

    def test_returns_default_when_user_context_none(self):
        """Returns 'User' when user_context is None."""
        with patch("src.utils.config._identity", return_value={"user_context": None}):
            assert config.get_user_name() == "User"

    def test_returns_default_when_user_context_missing(self):
        """Returns 'User' when user_context key is missing."""
        with patch("src.utils.config._identity", return_value={"other": "value"}):
            assert config.get_user_name() == "User"

    def test_returns_default_when_name_empty_string(self):
        """Returns 'User' when name is empty string."""
        with patch("src.utils.config._identity", return_value={"user_context": {"name": ""}}):
            assert config.get_user_name() == "User"


# ─────────────────────────────────────────────────────────────────────────────
# TestGetIdentity
# ─────────────────────────────────────────────────────────────────────────────

class TestGetIdentity:
    """Tests for get_identity()."""

    def test_returns_copy_of_identity(self):
        """Returns a dict copy of identity config."""
        identity_data = {"user": "alice", "role": "dev"}
        with patch("src.utils.config._identity", return_value=identity_data):
            result = config.get_identity()
            assert result == identity_data
            assert result is not identity_data  # Should be a copy


# ─────────────────────────────────────────────────────────────────────────────
# TestGetPersonality
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPersonality:
    """Tests for get_personality()."""

    def test_returns_copy_of_personality(self):
        """Returns a dict copy of personality config."""
        personality_data = {"tone": "direct", "style": "wry"}
        with patch("src.utils.config._personality", return_value=personality_data):
            result = config.get_personality()
            assert result == personality_data
            assert result is not personality_data  # Should be a copy


# ─────────────────────────────────────────────────────────────────────────────
# TestGetPersonaPrompt
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPersonaPrompt:
    """Tests for get_persona_prompt()."""

    def test_returns_default_when_personality_empty(self):
        """Returns hardcoded default when personality config is empty."""
        with patch("src.utils.config._personality", return_value={}):
            result = config.get_persona_prompt()
            expected = (
                "You are Archi, a warm, direct, and slightly wry AI agent. "
                "Speak like a capable peer, not a helpdesk."
            )
            assert result == expected

    def test_returns_default_when_personality_none(self):
        """Returns hardcoded default when personality is None."""
        with patch("src.utils.config._personality", return_value=None):
            result = config.get_persona_prompt()
            assert "You are Archi, a warm, direct, and slightly wry AI agent" in result

    def test_builds_persona_with_full_config(self):
        """Builds persona string from complete personality config."""
        personality = {
            "identity": {
                "essence": "a thoughtful AI companion."
            },
            "voice": {
                "delivery": "Conversational and clear",
                "tone": {
                    "default": "Friendly and approachable",
                    "under_pressure": "Steadfast and decisive"
                },
                "humor": {
                    "style": "Dry wit",
                    "frequency": "Occasionally"
                },
                "anti_patterns": ["robotic", "condescending"]
            }
        }
        with patch("src.utils.config._personality", return_value=personality):
            result = config.get_persona_prompt()
            assert "You are Archi. a thoughtful AI companion." in result
            assert "Conversational and clear" in result
            assert "Friendly and approachable" in result
            assert "Steadfast and decisive" in result
            assert "Dry wit" in result
            assert "Occasionally" in result
            assert "robotic; condescending" in result

    def test_omits_humor_when_empty(self):
        """Omits humor section when style is empty."""
        personality = {
            "identity": {"essence": "test"},
            "voice": {
                "delivery": "Clear",
                "tone": {"default": "Direct", "under_pressure": "Calm"},
                "humor": {"style": "", "frequency": ""}
            }
        }
        with patch("src.utils.config._personality", return_value=personality):
            result = config.get_persona_prompt()
            assert "Humor:" not in result

    def test_omits_anti_patterns_when_empty(self):
        """Omits anti_patterns section when list is empty."""
        personality = {
            "identity": {"essence": "test"},
            "voice": {
                "delivery": "Clear",
                "tone": {"default": "Direct", "under_pressure": "Calm"},
                "anti_patterns": []
            }
        }
        with patch("src.utils.config._personality", return_value=personality):
            result = config.get_persona_prompt()
            assert "Never:" not in result

    def test_truncates_anti_patterns_to_five(self):
        """Limits anti_patterns to first 5 items."""
        anti = ["a", "b", "c", "d", "e", "f", "g"]
        personality = {
            "identity": {"essence": "test"},
            "voice": {
                "delivery": "Clear",
                "tone": {"default": "Direct", "under_pressure": "Calm"},
                "anti_patterns": anti
            }
        }
        with patch("src.utils.config._personality", return_value=personality):
            result = config.get_persona_prompt()
            # Should only have first 5
            assert "a; b; c; d; e" in result
            assert "; f" not in result

    def test_uses_defaults_for_missing_keys(self):
        """Uses defaults for missing nested keys."""
        personality = {
            "identity": {},
            "voice": {
                "delivery": "",
                "tone": {},
                "anti_patterns": []
            }
        }
        with patch("src.utils.config._personality", return_value=personality):
            result = config.get_persona_prompt()
            assert "Direct, warm, unhurried" in result  # default tone
            assert "Calm, focused" in result  # default pressure tone


# ─────────────────────────────────────────────────────────────────────────────
# TestGetPersonaPromptCached
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPersonaPromptCached:
    """Tests for get_persona_prompt_cached()."""

    def test_caches_persona_prompt(self):
        """Caches the persona prompt and reuses it."""
        with patch("src.utils.config.get_persona_prompt", return_value="cached prompt") as mock_prompt:
            result1 = config.get_persona_prompt_cached()
            result2 = config.get_persona_prompt_cached()
            assert result1 == "cached prompt"
            assert result2 == "cached prompt"
            # Should only call underlying function once
            assert mock_prompt.call_count == 1

    def test_cache_cleared_by_reload(self):
        """Cache is cleared by reload()."""
        with patch("src.utils.config.get_persona_prompt", return_value="first"):
            _ = config.get_persona_prompt_cached()
            assert config._persona_prompt_cache == "first"

        config.reload()
        assert config._persona_prompt_cache is None


# ─────────────────────────────────────────────────────────────────────────────
# TestGetRelevantQuote
# ─────────────────────────────────────────────────────────────────────────────

class TestGetRelevantQuote:
    """Tests for get_relevant_quote()."""

    def test_returns_none_when_no_quotes(self):
        """Returns None when personality has no guiding_quotes."""
        with patch("src.utils.config._personality", return_value={}):
            result = config.get_relevant_quote("obstacle in the way")
            assert result is None

    def test_returns_none_when_quotes_empty_list(self):
        """Returns None when guiding_quotes is empty list."""
        with patch("src.utils.config._personality", return_value={"guiding_quotes": []}):
            result = config.get_relevant_quote("obstacle")
            assert result is None

    def test_returns_none_when_no_keyword_match(self):
        """Returns None when message doesn't match any keywords."""
        quotes = [{"text": "Quote 1", "source": "Author 1"}]
        with patch("src.utils.config._personality", return_value={"guiding_quotes": quotes}):
            result = config.get_relevant_quote("random message with no keywords")
            assert result is None

    def test_matches_keyword_case_insensitive(self):
        """Keyword matching is case-insensitive."""
        quotes = [{"text": "Quote", "source": "Author"}]
        with patch("src.utils.config._personality", return_value={"guiding_quotes": quotes}):
            with patch("random.random", return_value=0.1):  # Pass probability gate
                with patch("random.choice", return_value=0):
                    result = config.get_relevant_quote("OBSTACLE in the way")
                    assert result is not None

    def test_probability_gate_blocks_quote(self):
        """Probability gate (~80% chance) can block quote return."""
        quotes = [{"text": "Quote", "source": "Author"}]
        with patch("src.utils.config._personality", return_value={"guiding_quotes": quotes}):
            with patch("random.random", return_value=0.95):  # Fail the gate (> 0.20)
                result = config.get_relevant_quote("obstacle")
                assert result is None

    def test_probability_gate_allows_quote(self):
        """Probability gate allows quote with low random value."""
        quotes = [{"text": "Quote", "source": "Author"}]
        with patch("src.utils.config._personality", return_value={"guiding_quotes": quotes}):
            with patch("random.random", return_value=0.1):  # Pass the gate (<= 0.20)
                with patch("random.choice", return_value=0):
                    result = config.get_relevant_quote("obstacle")
                    assert result is not None

    def test_returns_correct_quote_text_and_source(self):
        """Returns dict with correct text and source."""
        quotes = [
            {"text": "Quote 0", "source": "Author 0"},
            {"text": "Quote 1", "source": "Author 1"}
        ]
        with patch("src.utils.config._personality", return_value={"guiding_quotes": quotes}):
            with patch("random.random", return_value=0.1):
                with patch("random.choice", return_value=1):
                    result = config.get_relevant_quote("frustration")
                    assert result == {"text": "Quote 1", "source": "Author 1"}

    def test_multiple_keyword_matches(self):
        """Selects from multiple matching keywords."""
        quotes = [{"text": "Q0", "source": "A0"}] * 15
        with patch("src.utils.config._personality", return_value={"guiding_quotes": quotes}):
            with patch("random.random", return_value=0.1):
                with patch("random.choice", return_value=5) as mock_choice:
                    config.get_relevant_quote("obstacle and frustration")
                    # Should pass a list with multiple indices to random.choice
                    assert mock_choice.called
                    call_args = mock_choice.call_args[0][0]
                    assert len(call_args) >= 2

    def test_skips_quote_index_beyond_length(self):
        """Skips keywords pointing to quotes beyond list length."""
        quotes = [{"text": "Q0", "source": "A0"}]  # Only 1 quote
        with patch("src.utils.config._personality", return_value={"guiding_quotes": quotes}):
            # "frustration" keyword would map to index 1, which is out of bounds
            # But we have quotes for index 0 and others
            result = config.get_relevant_quote("no matching keywords at all")
            assert result is None

    def test_quote_missing_text_key(self):
        """Handles quote dict missing 'text' key gracefully."""
        quotes = [{"source": "Author"}]  # Missing text
        with patch("src.utils.config._personality", return_value={"guiding_quotes": quotes}):
            with patch("random.random", return_value=0.1):
                with patch("random.choice", return_value=0):
                    result = config.get_relevant_quote("obstacle")
                    assert result == {"text": "", "source": "Author"}

    def test_quote_missing_source_key(self):
        """Handles quote dict missing 'source' key gracefully."""
        quotes = [{"text": "Quote"}]  # Missing source
        with patch("src.utils.config._personality", return_value={"guiding_quotes": quotes}):
            with patch("random.random", return_value=0.1):
                with patch("random.choice", return_value=0):
                    result = config.get_relevant_quote("obstacle")
                    assert result == {"text": "Quote", "source": ""}


# ─────────────────────────────────────────────────────────────────────────────
# TestGetMonitoring
# ─────────────────────────────────────────────────────────────────────────────

class TestGetMonitoring:
    """Tests for get_monitoring()."""

    def test_returns_defaults_when_section_empty(self):
        """Returns defaults when monitoring section is missing."""
        with patch("src.utils.config._rules", return_value={}):
            result = config.get_monitoring()
            assert result == config._MONITORING_DEFAULTS

    def test_returns_defaults_when_section_none(self):
        """Returns defaults when monitoring section is None."""
        with patch("src.utils.config._rules", return_value={"monitoring": None}):
            result = config.get_monitoring()
            assert result == config._MONITORING_DEFAULTS

    def test_overrides_defaults_with_config(self):
        """Config values override defaults."""
        rules = {"monitoring": {"cpu_threshold": 75, "memory_threshold": 85}}
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_monitoring()
            assert result["cpu_threshold"] == 75
            assert result["memory_threshold"] == 85
            assert result["temp_threshold"] == 80  # Default

    def test_ignores_none_values_in_config(self):
        """None values in config don't override defaults."""
        rules = {"monitoring": {"cpu_threshold": None, "memory_threshold": 85}}
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_monitoring()
            assert result["cpu_threshold"] == 80  # Default
            assert result["memory_threshold"] == 85

    def test_includes_additional_keys_from_config(self):
        """Additional keys from config are included."""
        rules = {"monitoring": {"cpu_threshold": 75, "custom_key": 999}}
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_monitoring()
            assert result["custom_key"] == 999


# ─────────────────────────────────────────────────────────────────────────────
# TestGetBrowserConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestGetBrowserConfig:
    """Tests for get_browser_config()."""

    def test_returns_defaults_when_section_empty(self):
        """Returns defaults when browser section is missing."""
        with patch("src.utils.config._rules", return_value={}):
            result = config.get_browser_config()
            assert result == config._BROWSER_DEFAULTS

    def test_returns_defaults_when_section_none(self):
        """Returns defaults when browser section is None."""
        with patch("src.utils.config._rules", return_value={"browser": None}):
            result = config.get_browser_config()
            assert result == config._BROWSER_DEFAULTS

    def test_converts_string_values_to_int(self):
        """String values are converted to int."""
        rules = {"browser": {"default_timeout_ms": "3000", "navigation_timeout_ms": "25000"}}
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_browser_config()
            assert result["default_timeout_ms"] == 3000
            assert result["navigation_timeout_ms"] == 25000
            assert isinstance(result["default_timeout_ms"], int)

    def test_ignores_none_values_in_config(self):
        """None values in config don't override defaults."""
        rules = {"browser": {"default_timeout_ms": None, "navigation_timeout_ms": 25000}}
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_browser_config()
            assert result["default_timeout_ms"] == 5000  # Default
            assert result["navigation_timeout_ms"] == 25000

    def test_overrides_defaults_with_config(self):
        """Config values override defaults."""
        rules = {"browser": {"default_timeout_ms": 7000}}
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_browser_config()
            assert result["default_timeout_ms"] == 7000


# ─────────────────────────────────────────────────────────────────────────────
# TestGetHeartbeatConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestGetHeartbeatConfig:
    """Tests for get_heartbeat_config()."""

    def test_returns_defaults_when_section_empty(self):
        """Returns defaults when heartbeat section is missing."""
        with patch("src.utils.config._heartbeat", return_value={}):
            result = config.get_heartbeat_config()
            assert result == config._HEARTBEAT_DEFAULTS

    def test_falls_back_to_dream_cycle_key(self):
        """Falls back to legacy 'dream_cycle' key when 'heartbeat' is missing."""
        heartbeat = {"dream_cycle": {"interval": 600}}
        with patch("src.utils.config._heartbeat", return_value=heartbeat):
            result = config.get_heartbeat_config()
            assert result["interval"] == 600

    def test_prefers_heartbeat_over_dream_cycle(self):
        """Prefers 'heartbeat' key over 'dream_cycle'."""
        heartbeat = {
            "heartbeat": {"interval": 600},
            "dream_cycle": {"interval": 300}
        }
        with patch("src.utils.config._heartbeat", return_value=heartbeat):
            result = config.get_heartbeat_config()
            assert result["interval"] == 600

    def test_converts_idle_threshold_to_interval(self):
        """Legacy 'idle_threshold' is converted to 'interval'."""
        heartbeat = {"heartbeat": {"idle_threshold": 450}}
        with patch("src.utils.config._heartbeat", return_value=heartbeat):
            result = config.get_heartbeat_config()
            assert result["interval"] == 450
            assert "idle_threshold" not in result

    def test_idle_threshold_ignored_if_interval_present(self):
        """'interval' takes precedence over 'idle_threshold'."""
        heartbeat = {"heartbeat": {"interval": 600, "idle_threshold": 450}}
        with patch("src.utils.config._heartbeat", return_value=heartbeat):
            result = config.get_heartbeat_config()
            assert result["interval"] == 600

    def test_converts_string_values_to_int(self):
        """String values are converted to int."""
        heartbeat = {"heartbeat": {"interval": "400", "max_parallel_tasks": "5"}}
        with patch("src.utils.config._heartbeat", return_value=heartbeat):
            result = config.get_heartbeat_config()
            assert result["interval"] == 400
            assert result["max_parallel_tasks"] == 5
            assert isinstance(result["interval"], int)

    def test_ignores_none_values(self):
        """None values don't override defaults."""
        heartbeat = {"heartbeat": {"interval": None, "max_parallel_tasks": 2}}
        with patch("src.utils.config._heartbeat", return_value=heartbeat):
            result = config.get_heartbeat_config()
            assert result["interval"] == 300  # Default
            assert result["max_parallel_tasks"] == 2

    def test_ignores_unknown_keys(self):
        """Unknown keys are not included in result."""
        heartbeat = {"heartbeat": {"interval": 400, "unknown_key": 999}}
        with patch("src.utils.config._heartbeat", return_value=heartbeat):
            result = config.get_heartbeat_config()
            assert "unknown_key" not in result
            assert result["interval"] == 400

    def test_get_dream_cycle_config_alias(self):
        """get_dream_cycle_config is an alias for get_heartbeat_config."""
        assert config.get_dream_cycle_config is config.get_heartbeat_config


# ─────────────────────────────────────────────────────────────────────────────
# TestGetHeartbeatBudget
# ─────────────────────────────────────────────────────────────────────────────

class TestGetHeartbeatBudget:
    """Tests for get_heartbeat_budget()."""

    def test_returns_default_when_no_rules(self):
        """Returns 0.50 when no non_override_rules exist."""
        with patch("src.utils.config._rules", return_value={}):
            result = config.get_heartbeat_budget()
            assert result == 0.50

    def test_returns_default_when_rules_empty(self):
        """Returns 0.50 when non_override_rules is empty."""
        with patch("src.utils.config._rules", return_value={"non_override_rules": []}):
            result = config.get_heartbeat_budget()
            assert result == 0.50

    def test_returns_heartbeat_budget_limit(self):
        """Returns limit from heartbeat_budget rule."""
        rules = {
            "non_override_rules": [
                {"name": "heartbeat_budget", "enabled": True, "limit": 0.75}
            ]
        }
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_heartbeat_budget()
            assert result == 0.75

    def test_returns_dream_cycle_budget_limit(self):
        """Returns limit from legacy dream_cycle_budget rule."""
        rules = {
            "non_override_rules": [
                {"name": "dream_cycle_budget", "enabled": True, "limit": 0.60}
            ]
        }
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_heartbeat_budget()
            assert result == 0.60

    def test_ignores_disabled_rules(self):
        """Skips rules with enabled=False."""
        rules = {
            "non_override_rules": [
                {"name": "heartbeat_budget", "enabled": False, "limit": 0.75}
            ]
        }
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_heartbeat_budget()
            assert result == 0.50  # Default

    def test_defaults_to_enabled_if_not_specified(self):
        """Treats enabled as True if not specified."""
        rules = {
            "non_override_rules": [
                {"name": "heartbeat_budget", "limit": 0.65}
            ]
        }
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_heartbeat_budget()
            assert result == 0.65

    def test_uses_first_matching_rule(self):
        """Uses the first matching rule found."""
        rules = {
            "non_override_rules": [
                {"name": "heartbeat_budget", "enabled": True, "limit": 0.75},
                {"name": "heartbeat_budget", "enabled": True, "limit": 0.80}
            ]
        }
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_heartbeat_budget()
            assert result == 0.75

    def test_returns_default_when_limit_missing(self):
        """Returns default if rule has no limit key."""
        rules = {
            "non_override_rules": [
                {"name": "heartbeat_budget", "enabled": True}
            ]
        }
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_heartbeat_budget()
            assert result == 0.50  # Default

    def test_converts_to_float(self):
        """Converts limit to float."""
        rules = {
            "non_override_rules": [
                {"name": "heartbeat_budget", "enabled": True, "limit": "0.55"}
            ]
        }
        with patch("src.utils.config._rules", return_value=rules):
            result = config.get_heartbeat_budget()
            assert result == 0.55
            assert isinstance(result, float)

    def test_get_dream_cycle_budget_alias(self):
        """get_dream_cycle_budget is an alias for get_heartbeat_budget."""
        assert config.get_dream_cycle_budget is config.get_heartbeat_budget
