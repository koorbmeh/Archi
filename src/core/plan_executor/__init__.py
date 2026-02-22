"""
plan_executor — Multi-step autonomous task execution.

Split into submodules (session 73) for SRP compliance:
  executor.py  — PlanExecutor class (core loop, prompt building, verification)
  actions.py   — Action handlers (_do_web_search, _do_create_file, etc.)
  safety.py    — Safety config, path resolution, backup, syntax check, error classification
  recovery.py  — Crash recovery state + task cancellation signals
  web.py       — SSL context, URL fetching, SSRF guard

All public symbols re-exported here for backward compatibility:
  from src.core.plan_executor import PlanExecutor  # still works
"""

import sys as _sys

# -- Executor & constants --
from .executor import (
    MAX_STEPS_CHAT,
    MAX_STEPS_CODING,
    MAX_STEPS_PER_TASK,
    PLAN_MAX_TOKENS,
    SUMMARY_MAX_TOKENS,
    PlanExecutor,
    _estimate_total_steps,
)

# -- Cancellation signals --
from .recovery import (
    check_and_clear_cancellation,
    clear_shutdown_flag,
    signal_task_cancellation,
)

# -- Safety & path helpers --
from .safety import (
    _DEFAULT_ALLOWED_COMMANDS,
    _DEFAULT_BLOCKED_COMMANDS,
    _DEFAULT_PROTECTED_PATHS,
    _check_protected,
    _classify_error,
    _get_safety,
    _load_safety_config,
    _resolve_project_path,
    _resolve_workspace_path,
)
from . import safety as _safety_mod

# -- Web helpers --
from .web import _fetch_url_text, _is_private_url

# -- Backward-compat aliases (old names used by integration tests) --
_PROTECTED_PATHS = _DEFAULT_PROTECTED_PATHS
_BLOCKED_COMMANDS = _DEFAULT_BLOCKED_COMMANDS

# Expose _safety_config_cache: tests do `pe._safety_config_cache = None` to
# force a reload.  We need writes to propagate to the canonical location in
# safety.py.  A module-level __getattr__/__setattr__ pair handles this.
_safety_config_cache = _safety_mod._safety_config_cache


def __getattr__(name):
    """Proxy reads of _safety_config_cache to the safety submodule."""
    if name == "_safety_config_cache":
        return _safety_mod._safety_config_cache
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# For `pe._safety_config_cache = None` — we need a custom module class.
class _PlanExecutorModule(_sys.modules[__name__].__class__):
    def __setattr__(self, name, value):
        if name == "_safety_config_cache":
            _safety_mod._safety_config_cache = value
            return
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name == "_safety_config_cache":
            return _safety_mod._safety_config_cache
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_sys.modules[__name__].__class__ = _PlanExecutorModule

__all__ = [
    # Core
    "PlanExecutor",
    "MAX_STEPS_PER_TASK",
    "MAX_STEPS_CODING",
    "MAX_STEPS_CHAT",
    "PLAN_MAX_TOKENS",
    "SUMMARY_MAX_TOKENS",
    "_estimate_total_steps",
    # Cancellation
    "signal_task_cancellation",
    "check_and_clear_cancellation",
    "clear_shutdown_flag",
    # Safety
    "_check_protected",
    "_resolve_workspace_path",
    "_resolve_project_path",
    "_get_safety",
    "_load_safety_config",
    "_classify_error",
    "_DEFAULT_PROTECTED_PATHS",
    "_DEFAULT_BLOCKED_COMMANDS",
    "_DEFAULT_ALLOWED_COMMANDS",
    "_PROTECTED_PATHS",
    "_BLOCKED_COMMANDS",
    "_safety_config_cache",
    # Web
    "_fetch_url_text",
    "_is_private_url",
]
