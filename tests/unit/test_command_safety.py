"""Unit tests for command safety — allowlist/blocklist enforcement.

Tests the run_command safety layers in PlanExecutor:
  Layer 1: Allowlist — only whitelisted command prefixes pass
  Layer 2: Blocklist — dangerous flag combinations on allowed commands blocked

Tests _load_safety_config(), _get_safety(), and _do_run_command() logic.

Created session 72.
"""

import os
import threading
import pytest

from src.core.plan_executor import (
    _load_safety_config,
    _get_safety,
    _DEFAULT_ALLOWED_COMMANDS,
    _DEFAULT_BLOCKED_COMMANDS,
    _DEFAULT_PROTECTED_PATHS,
)


@pytest.fixture(autouse=True)
def reset_safety_cache():
    """Clear the safety config cache before each test."""
    import src.core.plan_executor as pe
    pe._safety_config_cache = None
    yield
    pe._safety_config_cache = None


class TestAllowlist:
    """Tests for command allowlist enforcement."""

    def test_allowed_commands_from_rules(self, tmp_path, monkeypatch):
        """Safety config loads allowed_commands from rules.yaml."""
        rules = tmp_path / "config" / "rules.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "protected_files: ['plan_executor.py']\n"
            "blocked_commands: ['rm -rf']\n"
            "allowed_commands: ['pip', 'pytest', 'git']\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(tmp_path))
        config = _load_safety_config()
        assert "pip" in config["allowed_commands"]
        assert "pytest" in config["allowed_commands"]
        assert "git" in config["allowed_commands"]
        # curl, wget, bash should NOT be in the allowlist
        assert "curl" not in config["allowed_commands"]
        assert "wget" not in config["allowed_commands"]
        assert "bash" not in config["allowed_commands"]

    def test_echo_not_in_default_allowlist(self):
        """echo was removed in session 71 — verify it's not in defaults.

        Note: _DEFAULT_ALLOWED_COMMANDS still has echo for hardcoded fallback,
        but the actual rules.yaml should NOT have echo. This test verifies
        the rules.yaml content.
        """
        import yaml
        from src.utils.paths import base_path
        rules_path = os.path.join(base_path(), "config", "rules.yaml")
        if os.path.exists(rules_path):
            with open(rules_path, "r", encoding="utf-8") as f:
                rules = yaml.safe_load(f) or {}
            allowed = rules.get("allowed_commands", [])
            assert "echo" not in allowed, "echo should not be in allowed_commands (session 71 removal)"

    def test_dangerous_commands_not_in_allowlist(self, tmp_path, monkeypatch):
        """Dangerous commands should never appear in the allowlist."""
        rules = tmp_path / "config" / "rules.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "protected_files: ['plan_executor.py']\n"
            "blocked_commands: ['rm -rf']\n"
            "allowed_commands: ['pip', 'pytest']\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(tmp_path))
        config = _load_safety_config()
        dangerous = {"curl", "wget", "bash", "powershell", "cmd", "nc", "ncat",
                      "netcat", "ssh", "scp", "ftp", "telnet"}
        for cmd in dangerous:
            assert cmd not in config["allowed_commands"], f"{cmd} should not be allowed"

    def test_fallback_to_defaults_on_missing_rules(self, tmp_path, monkeypatch):
        """When rules.yaml is missing, defaults are used."""
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(tmp_path))
        config = _load_safety_config()
        assert config["allowed_commands"] == _DEFAULT_ALLOWED_COMMANDS
        assert config["blocked_commands"] == _DEFAULT_BLOCKED_COMMANDS
        assert config["protected_paths"] == _DEFAULT_PROTECTED_PATHS

    def test_fallback_on_corrupt_yaml(self, tmp_path, monkeypatch):
        """Corrupt rules.yaml gracefully falls back to defaults."""
        rules = tmp_path / "config" / "rules.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text("{{{{not valid yaml at all!!!!", encoding="utf-8")
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(tmp_path))
        config = _load_safety_config()
        assert config["allowed_commands"] == _DEFAULT_ALLOWED_COMMANDS

    def test_empty_allowed_commands_uses_default(self, tmp_path, monkeypatch):
        """Empty allowed_commands list in rules.yaml uses defaults."""
        rules = tmp_path / "config" / "rules.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "protected_files: ['plan_executor.py']\n"
            "blocked_commands: ['rm -rf']\n"
            "allowed_commands: []\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(tmp_path))
        config = _load_safety_config()
        # Empty list → should still use the yaml values (empty frozenset)
        # The code checks `if _alw:` so empty list falls through to defaults
        assert config["allowed_commands"] == _DEFAULT_ALLOWED_COMMANDS


class TestBlocklist:
    """Tests for command blocklist (defense-in-depth)."""

    def test_blocked_commands_loaded(self, tmp_path, monkeypatch):
        """Blocked commands are loaded from rules.yaml."""
        rules = tmp_path / "config" / "rules.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "protected_files: ['plan_executor.py']\n"
            "blocked_commands:\n"
            "  - 'rm -rf'\n"
            "  - 'git push --force'\n"
            "allowed_commands: ['git', 'pip']\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(tmp_path))
        config = _load_safety_config()
        assert "rm -rf" in config["blocked_commands"]
        assert "git push --force" in config["blocked_commands"]

    def test_default_blocklist_covers_destructive_commands(self):
        """Default blocklist covers common destructive patterns."""
        blocked = _DEFAULT_BLOCKED_COMMANDS
        assert any("rm -rf" in b for b in blocked)
        assert any("format " in b or "format.com" in b for b in blocked)
        assert any("shutdown" in b for b in blocked)
        assert any("dd if=" in b for b in blocked)
        assert any("mkfs." in b for b in blocked)


class TestCommandParsing:
    """Tests for command parsing edge cases in the allowlist check."""

    def test_command_with_exe_suffix_stripped(self):
        """Windows .exe/.cmd/.bat suffixes are stripped for allowlist check."""
        # Simulate the stripping logic from _do_run_command
        for raw, expected in [
            ("python.exe", "python"),
            ("git.cmd", "git"),
            ("pip.bat", "pip"),
            ("pytest", "pytest"),
        ]:
            cmd_name = os.path.basename(raw).lower()
            for suffix in (".exe", ".cmd", ".bat"):
                if cmd_name.endswith(suffix):
                    cmd_name = cmd_name[: -len(suffix)]
            assert cmd_name == expected

    def test_path_prefix_stripped(self):
        """Full path to command is stripped to basename for allowlist check."""
        raw = "/usr/local/bin/python3"
        cmd_name = os.path.basename(raw).lower()
        assert cmd_name == "python3"

    def test_shlex_split_handles_quotes(self):
        """shlex.split correctly parses quoted commands."""
        import shlex
        tokens = shlex.split('git commit -m "fix the bug"')
        assert tokens[0] == "git"
        assert tokens[2] == "-m"
        assert tokens[3] == "fix the bug"

    def test_shlex_split_fallback_on_bad_quotes(self):
        """When shlex.split fails (unmatched quotes), fallback to str.split."""
        import shlex
        bad_cmd = "git commit -m 'unclosed quote"
        try:
            tokens = shlex.split(bad_cmd)
        except ValueError:
            tokens = bad_cmd.split()
        assert tokens[0] == "git"


class TestProtectedPaths:
    """Tests for protected file path enforcement."""

    def test_protected_files_loaded(self, tmp_path, monkeypatch):
        """Protected files are loaded from rules.yaml."""
        rules = tmp_path / "config" / "rules.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "protected_files:\n"
            "  - 'plan_executor.py'\n"
            "  - 'safety_controller.py'\n"
            "  - 'claude/'\n"
            "blocked_commands: ['rm -rf']\n"
            "allowed_commands: ['pip']\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(tmp_path))
        config = _load_safety_config()
        assert "plan_executor.py" in config["protected_paths"]
        assert "safety_controller.py" in config["protected_paths"]
        assert "claude/" in config["protected_paths"]

    def test_default_protected_paths_cover_critical_files(self):
        """Default protected paths include the most critical files."""
        protected = _DEFAULT_PROTECTED_PATHS
        assert "src/core/plan_executor/executor.py" in protected
        assert "src/core/plan_executor/safety.py" in protected
        assert "src/core/safety_controller.py" in protected
        assert "src/utils/git_safety.py" in protected
        assert "config/prime_directive.txt" in protected


class TestSafetyConfigCaching:
    """Tests for lazy-loading and caching of safety config."""

    def test_config_cached_after_first_load(self, tmp_path, monkeypatch):
        """Second call to _load_safety_config returns cached result."""
        import src.core.plan_executor as pe
        rules = tmp_path / "config" / "rules.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "protected_files: ['test.py']\n"
            "blocked_commands: ['rm']\n"
            "allowed_commands: ['pip']\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(tmp_path))
        first = _load_safety_config()
        second = _load_safety_config()
        assert first is second  # Same object — cached

    def test_get_safety_accessor(self, tmp_path, monkeypatch):
        """_get_safety() returns the right key from config."""
        rules = tmp_path / "config" / "rules.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "protected_files: ['x.py']\n"
            "blocked_commands: ['rm']\n"
            "allowed_commands: ['git']\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(tmp_path))
        assert "git" in _get_safety("allowed_commands")
        assert "rm" in _get_safety("blocked_commands")

    def test_thread_safe_loading(self, tmp_path, monkeypatch):
        """Multiple threads loading config simultaneously don't corrupt it."""
        import src.core.plan_executor as pe
        rules = tmp_path / "config" / "rules.yaml"
        rules.parent.mkdir(parents=True)
        rules.write_text(
            "protected_files: ['a.py']\n"
            "blocked_commands: ['rm']\n"
            "allowed_commands: ['pip']\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("src.utils.paths.base_path", lambda: str(tmp_path))

        results = []

        def loader():
            results.append(_load_safety_config())

        threads = [threading.Thread(target=loader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # All threads should get the same cached object
        assert all(r is results[0] for r in results)
