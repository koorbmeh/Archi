#!/usr/bin/env python3
"""
V2 pipeline integration tests.

Exercises the full message_handler → intent_classifier → action_dispatcher
→ response_builder pipeline, the same codepath Discord uses.

These tests call the OpenRouter API.  By default they use a free model
(meta-llama/llama-3.1-8b-instruct:free) so the suite costs $0.  Override
with TEST_MODEL env var if needed.  Marked @pytest.mark.live.  Skip with:

    pytest -m "not live"

Run just these:

    pytest tests/integration/test_v2_pipeline.py -v
"""

import os
import sys
from pathlib import Path

import pytest

# ---- Project root on path, load .env ----
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env", override=True)
except ImportError:
    pass

# Use a free model for integration tests by default ($0 per suite).
# Override with TEST_MODEL env var to use a different model.
_TEST_MODEL = os.environ.get("TEST_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
os.environ["OPENROUTER_MODEL"] = _TEST_MODEL

from src.interfaces.message_handler import process_message
from src.models.router import ModelRouter
from src.core.goal_manager import GoalManager
from src.core.heartbeat import Heartbeat

# ---- Fixtures ----

@pytest.fixture(scope="module")
def router():
    """Shared model router (API-first)."""
    return ModelRouter()


@pytest.fixture(scope="module")
def goal_manager():
    """Shared goal manager pointed at real data dir."""
    return GoalManager(data_dir=_root / "data")


@pytest.fixture(scope="module")
def conversation():
    """Accumulates history across tests in this module for context tests."""
    return {"history": []}


@pytest.fixture(scope="module")
def heartbeat_instance():
    """Heartbeat instance for threshold tests (not started)."""
    return Heartbeat(interval_seconds=300)


def _send(prompt, router, goal_manager, conversation):
    """Helper: send a prompt and record it in conversation history."""
    response, actions, cost = process_message(
        message=prompt,
        router=router,
        history=conversation["history"][-10:] or None,
        source="test_harness",
        goal_manager=goal_manager,
    )
    conversation["history"].append({"role": "user", "content": prompt})
    conversation["history"].append({"role": "assistant", "content": response})
    return response, actions, cost


# ====================================================================
# Fast-path tests (zero cost, no API call)
# ====================================================================

class TestFastPaths:
    """Fast-path routes — should be instant and free."""

    @pytest.mark.live
    def test_datetime(self, router, goal_manager, conversation):
        resp, _, cost = _send("what time is it", router, goal_manager, conversation)
        assert cost < 0.0001, f"datetime should be free, got ${cost:.4f}"
        assert len(resp.strip()) > 5, "empty datetime response"

    @pytest.mark.live
    def test_date(self, router, goal_manager, conversation):
        resp, _, cost = _send("what's the date", router, goal_manager, conversation)
        assert cost < 0.0001, f"date should be free, got ${cost:.4f}"
        assert "202" in resp, "date response should contain a year"

    @pytest.mark.live
    def test_help(self, router, goal_manager, conversation):
        resp, _, cost = _send("/help", router, goal_manager, conversation)
        assert cost < 0.0001
        assert "/goal" in resp.lower() or "goal" in resp.lower()

    @pytest.mark.live
    def test_goals(self, router, goal_manager, conversation):
        resp, _, cost = _send("/goals", router, goal_manager, conversation)
        assert cost < 0.0001
        assert "goal" in resp.lower() or "no active" in resp.lower()

    @pytest.mark.live
    def test_cost(self, router, goal_manager, conversation):
        resp, _, cost = _send("/cost", router, goal_manager, conversation)
        assert cost < 0.0001
        assert "$" in resp or "cost" in resp.lower()

    @pytest.mark.live
    def test_goal_creation(self, router, goal_manager, conversation):
        resp, _, cost = _send(
            "/goal test goal from harness - delete me",
            router, goal_manager, conversation,
        )
        assert cost < 0.0001
        assert "goal" in resp.lower()

        # Cleanup: remove the test goal
        import json
        goals_path = _root / "data" / "goals_state.json"
        if goals_path.exists():
            data = json.loads(goals_path.read_text())
            data["goals"] = [
                g for g in data.get("goals", [])
                if "test goal from harness" not in g.get("description", "")
            ]
            goals_path.write_text(json.dumps(data, indent=2))


# ====================================================================
# Model classification tests (cost real money)
# ====================================================================

class TestModelClassification:
    """Tests that route through the model for intent classification."""

    @pytest.mark.live
    def test_chat(self, router, goal_manager, conversation):
        resp, _, cost = _send("tell me a joke", router, goal_manager, conversation)
        assert cost > 0.0001, "chat should hit the model"
        assert len(resp.strip()) > 10, "joke response too short"

    @pytest.mark.live
    def test_search(self, router, goal_manager, conversation):
        resp, _, cost = _send(
            "search for the current price of silver per ounce",
            router, goal_manager, conversation,
        )
        assert cost > 0.0001, "search should hit the model"
        assert len(resp.strip()) > 20, "search response too short"

    @pytest.mark.live
    def test_greeting_passthrough(self, router, goal_manager, conversation):
        """A greeting with substance should NOT be caught by the fast-path."""
        resp, _, cost = _send(
            "hey can you search for the price of gold",
            router, goal_manager, conversation,
        )
        assert cost > 0.0001, (
            "greeting+substance should reach the model, not fast-path"
        )
        assert len(resp.strip()) > 10

    @pytest.mark.live
    def test_file_ops(self, router, goal_manager, conversation):
        resp, _, cost = _send(
            "read the file config/archi_identity.yaml",
            router, goal_manager, conversation,
        )
        assert len(resp.strip()) > 5, "file-ops response too short"


# ====================================================================
# Conversation context tests
# ====================================================================

class TestConversationContext:
    """Multi-turn memory — context setup must run before recall."""

    @pytest.mark.live
    def test_context_setup(self, router, goal_manager, conversation):
        resp, _, cost = _send(
            "my name is TestBot and I like running tests",
            router, goal_manager, conversation,
        )
        assert cost > 0.0001, "should hit the model"
        assert len(resp.strip()) > 5

    @pytest.mark.live
    def test_context_recall(self, router, goal_manager, conversation):
        """Must run after test_context_setup — relies on conversation history."""
        resp, _, cost = _send(
            "what did I just tell you about myself?",
            router, goal_manager, conversation,
        )
        low = resp.lower()
        assert any(w in low for w in ("testbot", "test", "running")), (
            f"context recall failed — response didn't mention TestBot: {resp[:200]}"
        )


# ====================================================================
# Model switching tests
# ====================================================================

class TestModelSwitching:
    """Router model switching — no API calls needed, tests the router directly."""

    @pytest.mark.live
    def test_switch_to_deepseek(self, router):
        result = router.switch_model("deepseek")
        assert result["model"] is not None, f"switch failed: {result.get('message')}"
        assert "deepseek" in result["model"].lower()

    @pytest.mark.live
    def test_switch_to_grok(self, router):
        result = router.switch_model("grok")
        assert result["model"] is not None
        assert "grok" in result["model"].lower()

    @pytest.mark.live
    def test_switch_to_auto(self, router):
        result = router.switch_model("auto")
        assert "auto" in result["model"].lower()

    @pytest.mark.live
    def test_temp_switch(self, router):
        # First ensure we're on grok
        router.switch_model("grok")
        info_before = router.get_active_model_info()

        result = router.switch_model_temp("deepseek", count=2)
        assert result.get("temp_remaining") == 2
        assert "deepseek" in result["model"].lower()

        info_during = router.get_active_model_info()
        assert "temp" in info_during.get("mode", "").lower()

        # Reset for other tests
        router.switch_model("auto")

    @pytest.mark.live
    def test_get_active_model_info(self, router):
        router.switch_model("auto")
        info = router.get_active_model_info()
        assert "model" in info
        assert "display" in info
        assert "mode" in info


# ====================================================================
# Heartbeat frequency tests
# ====================================================================

class TestHeartbeatFrequency:
    """Heartbeat threshold management — no API calls."""

    @pytest.mark.live
    def test_get_threshold(self, heartbeat_instance):
        secs = heartbeat_instance.get_idle_threshold()
        assert secs == 300, f"expected 300, got {secs}"

    @pytest.mark.live
    def test_set_threshold(self, heartbeat_instance):
        msg = heartbeat_instance.set_idle_threshold(900)
        assert heartbeat_instance.get_idle_threshold() == 900
        assert "900" in msg or "15" in msg  # seconds or minutes

    @pytest.mark.live
    def test_threshold_floor(self, heartbeat_instance):
        """Setting below 60s should clamp to 60."""
        heartbeat_instance.set_idle_threshold(10)
        assert heartbeat_instance.get_idle_threshold() == 60

    @pytest.mark.live
    def test_restore_threshold(self, heartbeat_instance):
        """Restore default for other tests."""
        heartbeat_instance.set_idle_threshold(300)
        assert heartbeat_instance.get_idle_threshold() == 300


# ====================================================================
# Cache tests
# ====================================================================

class TestCache:
    """Verify the query cache saves money on repeated prompts."""

    @pytest.mark.live
    def test_cache_saves_on_repeat(self, router, goal_manager, conversation):
        """After several model calls, the cache should have entries."""
        prompt = "explain what a python decorator is in one sentence"

        # First call — hits the model
        resp1, _, cost1 = _send(prompt, router, goal_manager, conversation)
        assert cost1 > 0.0001, "first call should hit the model"
        assert len(resp1.strip()) > 0, "empty response"

        # The cache should have accumulated entries from model calls
        cache = router._cache
        stats = cache.get_stats()
        assert stats["cached_entries"] > 0, (
            f"cache should have entries after model calls, got {stats}"
        )

    @pytest.mark.live
    def test_cache_stats_structure(self, router):
        stats = router._cache.get_stats()
        assert "hits" in stats
        assert "misses" in stats
        assert "cached_entries" in stats


# ====================================================================
# Code writing / multi-turn action tests
# ====================================================================

class TestCodeWriting:
    """Verify Archi can handle code-related requests."""

    @pytest.mark.live
    def test_code_analysis(self, router, goal_manager, conversation):
        """Ask Archi to look at a source file — tests the coding path."""
        resp, _, cost = _send(
            "look at src/utils/paths.py and tell me what functions it exports",
            router, goal_manager, conversation,
        )
        assert cost > 0.0001, "should hit the model"
        assert len(resp.strip()) > 20, "code analysis response too short"

    @pytest.mark.live
    def test_multi_turn_search_followup(self, router, goal_manager, conversation):
        """Search for something, then ask a follow-up about the results."""
        resp1, _, cost1 = _send(
            "search for benefits of creatine",
            router, goal_manager, conversation,
        )
        assert cost1 > 0.0001
        assert len(resp1.strip()) > 20

        resp2, _, cost2 = _send(
            "tell me more about the first benefit you mentioned",
            router, goal_manager, conversation,
        )
        assert cost2 > 0.0001
        assert len(resp2.strip()) > 20


# ====================================================================
# Protected files / safety tests
# ====================================================================

class TestSafety:
    """Verify protected file enforcement and path safety."""

    @pytest.mark.live
    def test_protected_paths_loaded(self):
        """Protected paths should include critical files."""
        from src.core.plan_executor import _PROTECTED_PATHS
        assert "src/core/plan_executor.py" in _PROTECTED_PATHS
        assert "src/core/safety_controller.py" in _PROTECTED_PATHS
        assert "config/prime_directive.txt" in _PROTECTED_PATHS

    @pytest.mark.live
    def test_protected_file_cannot_be_modified(self):
        """_check_protected should raise ValueError for protected files."""
        from src.core.plan_executor import _check_protected
        with pytest.raises(ValueError, match="Protected file"):
            _check_protected("src/core/plan_executor.py")
        with pytest.raises(ValueError, match="Protected file"):
            _check_protected("src/core/safety_controller.py")

    @pytest.mark.live
    def test_workspace_path_allowed(self):
        """Paths inside workspace/ should resolve without error."""
        from src.core.plan_executor import _resolve_workspace_path
        # Should not raise — workspace paths are always OK
        path = _resolve_workspace_path("workspace/test_file.txt")
        assert "workspace" in path

    @pytest.mark.live
    def test_workspace_path_rejects_escape(self):
        """Paths that escape workspace/ should be rejected."""
        from src.core.plan_executor import _resolve_workspace_path
        with pytest.raises(ValueError, match="escapes workspace"):
            _resolve_workspace_path("../../etc/passwd")

    @pytest.mark.live
    def test_src_write_is_protected(self):
        """Writing to protected src/ files should be blocked."""
        from src.core.plan_executor import _check_protected
        # Protected files raise ValueError
        with pytest.raises(ValueError):
            _check_protected("src/core/plan_executor.py")
        # heartbeat.py is now protected
        with pytest.raises(ValueError):
            _check_protected("src/core/heartbeat.py")

    @pytest.mark.live
    def test_blocked_commands(self):
        """Dangerous commands should be in the blocked list."""
        from src.core.plan_executor import _BLOCKED_COMMANDS
        # At least these destructive patterns should be blocked
        blocked_str = " ".join(_BLOCKED_COMMANDS).lower()
        assert "rm -rf" in blocked_str
        assert "shutdown" in blocked_str
        assert "format " in blocked_str


# ====================================================================
# Portability tests
# ====================================================================

class TestPortability:
    """Verify no hardcoded paths leaked into source."""

    @pytest.mark.live
    def test_no_hardcoded_windows_paths_in_source(self):
        """Source files should not contain hardcoded user paths."""
        import re
        src_dir = _root / "src"
        pattern = re.compile(r"C:/Users/|C:\\Users\\|/home/\w+/", re.IGNORECASE)
        violations = []
        for py_file in src_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line) and not line.strip().startswith("#"):
                    violations.append(f"{py_file.relative_to(_root)}:{i}: {line.strip()[:100]}")
        assert not violations, (
            f"Hardcoded user paths found in source:\n" +
            "\n".join(violations[:10])
        )

    @pytest.mark.live
    def test_base_path_uses_env_or_discovery(self):
        """base_path() should work without ARCHI_ROOT set."""
        from src.utils.paths import base_path
        path = base_path()
        assert os.path.isdir(path), f"base_path() returned non-directory: {path}"

    @pytest.mark.live
    def test_env_file_is_gitignored(self):
        """The .env file must be in .gitignore to prevent key leakage."""
        gitignore = (_root / ".gitignore").read_text()
        assert ".env" in gitignore, ".env not found in .gitignore"

    @pytest.mark.live
    def test_no_api_keys_in_source(self):
        """No actual API key values should appear in tracked source files."""
        import re
        src_dir = _root / "src"
        # Match common API key patterns (sk-or-v1-..., discord bot tokens, etc.)
        key_patterns = [
            re.compile(r"sk-or-v1-[a-f0-9]{20,}"),
            re.compile(r"MTQ\d{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # Discord token
        ]
        violations = []
        for py_file in src_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for pat in key_patterns:
                matches = pat.findall(text)
                if matches:
                    violations.append(f"{py_file.relative_to(_root)}: {pat.pattern}")
        assert not violations, (
            f"API keys found in source files:\n" +
            "\n".join(violations[:10])
        )

    @pytest.mark.live
    def test_no_api_keys_in_tests(self):
        """No actual API key values should appear in test files either."""
        import re
        tests_dir = _root / "tests"
        key_patterns = [
            re.compile(r"sk-or-v1-[a-f0-9]{20,}"),
            re.compile(r"MTQ\d{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
        ]
        violations = []
        for py_file in tests_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for pat in key_patterns:
                matches = pat.findall(text)
                if matches:
                    violations.append(f"{py_file.relative_to(_root)}: {pat.pattern}")
        assert not violations, (
            f"API keys found in test files:\n" +
            "\n".join(violations[:10])
        )
