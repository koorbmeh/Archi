"""
Safety configuration, path resolution, file protection, and error classification.

Extracted from plan_executor.py (session 73) for SRP compliance.

Includes:
- Safety config loading from rules.yaml (lazy, cached, thread-safe)
- Path resolution helpers (workspace boundary, project boundary, symlink resolution)
- Protected file checks and approval-required path checks
- Source code safety (backup, syntax check)
- Error classification for mechanical error recovery
"""

import logging
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safety config defaults (used if rules.yaml is missing or corrupt)
# ---------------------------------------------------------------------------

_DEFAULT_PROTECTED_PATHS = frozenset({
    "src/core/plan_executor/__init__.py",
    "src/core/plan_executor/executor.py",
    "src/core/plan_executor/actions.py",
    "src/core/plan_executor/safety.py",
    "src/core/plan_executor/recovery.py",
    "src/core/plan_executor/web.py",
    "src/core/safety_controller.py",
    "src/utils/git_safety.py",
    "config/prime_directive.txt",
})

_DEFAULT_BLOCKED_COMMANDS = (
    "rm -rf", "rm -r /", "rmdir /s", "del /s", "del /f",
    "format ", "format.com",
    "shutdown", "reboot", "restart-computer", "stop-computer",
    ":(){ ", "fork bomb",
    "mkfs.", "dd if=",
    "reg delete", "reg add",
    "> /dev/sda",
    "chmod -r 000", "chmod 000 /",
    ":(){ :|:& };:",
)

_DEFAULT_APPROVAL_REQUIRED_PATHS = ("src/",)

_DEFAULT_ALLOWED_COMMANDS = frozenset({
    "pip", "pip3", "pytest", "python", "python3",
    "git", "node", "npm", "npx", "tsc",
    "ls", "dir", "echo", "cat", "type", "head", "tail",
    "cd", "pwd", "which", "where",
})

# Lazy-loaded safety config (Critical 3 fix: avoid import-time file I/O).
_safety_config_cache = None
_safety_config_lock = threading.Lock()


def _load_safety_config():
    """Load safety config from rules.yaml. Returns a dict with all safety values.

    Falls back to hardcoded defaults if rules.yaml can't be read.
    Thread-safe: only runs the file I/O once, caches the result.
    """
    global _safety_config_cache
    if _safety_config_cache is not None:
        return _safety_config_cache

    with _safety_config_lock:
        if _safety_config_cache is not None:
            return _safety_config_cache

        protected = _DEFAULT_PROTECTED_PATHS
        blocked = _DEFAULT_BLOCKED_COMMANDS
        approval = _DEFAULT_APPROVAL_REQUIRED_PATHS
        allowed = _DEFAULT_ALLOWED_COMMANDS

        try:
            import yaml
            from src.utils.paths import base_path
            rules_path = os.path.join(base_path(), "config", "rules.yaml")
            with open(rules_path, "r", encoding="utf-8") as f:
                rules = yaml.safe_load(f) or {}
            _prot = rules.get("protected_files", [])
            _blk = rules.get("blocked_commands", [])
            _appr = rules.get("approval_required_paths", [])
            _alw = rules.get("allowed_commands", [])
            if _prot and _blk:
                protected = frozenset(_prot)
                blocked = tuple(_blk)
                approval = tuple(_appr) if _appr else _DEFAULT_APPROVAL_REQUIRED_PATHS
                if _alw:
                    allowed = frozenset(_alw)
                logger.debug(
                    "Loaded safety config from rules.yaml: %d protected, "
                    "%d blocked, %d approval, %d allowed commands",
                    len(protected), len(blocked), len(approval), len(allowed),
                )
        except Exception as e:
            logger.warning("Could not load safety config from rules.yaml: %s (using defaults)", e)

        _safety_config_cache = {
            "protected_paths": protected,
            "blocked_commands": blocked,
            "approval_required_paths": approval,
            "allowed_commands": allowed,
        }
        return _safety_config_cache


def _get_safety(key: str):
    """Lazy accessor for safety config values."""
    return _load_safety_config()[key]


# ---------------------------------------------------------------------------
# Approval and protection checks
# ---------------------------------------------------------------------------

def _requires_approval(relative_path: str) -> bool:
    """Return True if the path requires user approval before modification."""
    rel = relative_path.lstrip("/").replace("\\", "/")
    for prefix in _get_safety("approval_required_paths"):
        if rel.startswith(prefix):
            return True
    return False


def _check_pre_approved(relative_path: str) -> bool:
    """Check if a path has been pre-approved via deferred approval.

    When a user misses an approval timeout, they can later reply
    "approve src/tools/foo.py" to create a pre-approval file.  This
    function checks for that file and consumes it (one-time use).
    """
    try:
        from src.utils.paths import base_path
        rel = relative_path.lstrip("/").replace("\\", "/")
        pa_file = os.path.join(
            base_path(), "data", "pre_approved",
            rel.replace("/", "_").replace("\\", "_") + ".txt",
        )
        if os.path.isfile(pa_file):
            os.remove(pa_file)
            logger.info("Pre-approval consumed for %s", rel)
            return True
    except Exception as e:
        logger.warning("Error checking pre-approval for %s: %s", relative_path, e)
    return False


def _check_protected(relative_path: str) -> None:
    """Raise ValueError if path is a protected file."""
    rel = relative_path.lstrip("/").replace("\\", "/")
    for protected in _get_safety("protected_paths"):
        if rel == protected or rel.endswith("/" + protected):
            raise ValueError(f"Protected file cannot be modified: {protected}")


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------

def _strip_absolute_prefix(raw: str) -> str:
    """Strip Windows absolute prefixes and base_path prefixes from a path.

    The LLM sometimes returns full absolute Windows paths instead of
    relative paths.  If we naively join that with ``base_path()``, we get a
    doubled path because ``os.path.join`` on Linux doesn't recognise a
    Windows drive letter as a root.
    """
    from src.utils.paths import base_path
    s = raw.replace("\\", "/").strip()
    # Strip Windows drive prefix  (e.g. "C:/Users/..." → "Users/...")
    if len(s) >= 3 and s[0].isalpha() and s[1] == ":" and s[2] in ("/", "\\"):
        s = s[3:]
    # Strip the base_path prefix if the LLM echoed the full project root
    bp = base_path().replace("\\", "/").strip("/")
    if s.startswith(bp + "/"):
        s = s[len(bp) + 1:]
    elif s.startswith(bp):
        s = s[len(bp):]
    return s.lstrip("/")


def _resolve_workspace_path(relative_path: str) -> str:
    """Resolve a workspace-relative path to a full path, enforcing workspace boundary.

    Security: resolves symlinks via realpath() before the boundary check
    so symlinks pointing outside workspace/ are rejected.
    """
    from src.utils.paths import base_path
    rel = _strip_absolute_prefix(relative_path)
    if not rel.startswith("workspace/"):
        rel = "workspace/" + rel
    full = os.path.normpath(os.path.join(base_path(), rel.replace("/", os.sep)))
    # Resolve symlinks to get the real path for boundary check
    real = os.path.realpath(full)
    workspace_root = os.path.realpath(os.path.normpath(os.path.join(base_path(), "workspace")))
    if not real.startswith(workspace_root + os.sep) and real != workspace_root:
        logger.warning("Path security: rejected '%s' (resolves outside workspace)", relative_path)
        raise ValueError(f"Path escapes workspace: {relative_path}")
    return full


def _resolve_project_path(relative_path: str) -> str:
    """Resolve a project-relative path for reading or source modification.

    Allows access to any file within the project root (src/, config/, workspace/, etc.)
    but enforces:
    - Path must stay within the project root (symlinks resolved before check)
    - Protected files cannot be written to (checked separately by write_source)
    """
    from src.utils.paths import base_path
    rel = _strip_absolute_prefix(relative_path)
    full = os.path.normpath(os.path.join(base_path(), rel.replace("/", os.sep)))
    # Resolve symlinks to get the real path for boundary check
    real = os.path.realpath(full)
    project_root = os.path.realpath(os.path.normpath(base_path()))
    if not real.startswith(project_root + os.sep) and real != project_root:
        logger.warning("Path security: rejected '%s' (resolves outside project root)", relative_path)
        raise ValueError(f"Path escapes project root: {relative_path}")
    return full


# ---------------------------------------------------------------------------
# Source code safety helpers
# ---------------------------------------------------------------------------

def _backup_file(filepath: str) -> Optional[str]:
    """Create a timestamped backup before modifying a source file.

    Backups are stored in data/source_backups/ with a flattened filename.
    Returns the backup path, or None if there was nothing to back up.
    """
    if not os.path.exists(filepath):
        return None
    from src.utils.paths import base_path
    backup_dir = os.path.join(base_path(), "data", "source_backups")
    os.makedirs(backup_dir, exist_ok=True)

    rel = os.path.relpath(filepath, base_path())
    safe_name = rel.replace(os.sep, "__").replace("/", "__")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"{safe_name}.{timestamp}.bak")

    try:
        shutil.copy2(filepath, backup_path)
        return backup_path
    except Exception as e:
        logger.warning("Backup failed for %s: %s", filepath, e)
        return None


def _syntax_check(filepath: str) -> Optional[str]:
    """Run py_compile on a Python file.

    Returns None on success, or an error message on failure.
    Non-.py files are always accepted.
    """
    if not filepath.endswith(".py"):
        return None
    try:
        import py_compile
        py_compile.compile(filepath, doraise=True)
        return None
    except py_compile.PyCompileError as e:
        return str(e)


# ---------------------------------------------------------------------------
# Error classification for mechanical error recovery
# ---------------------------------------------------------------------------

# Transient: network/service issues that may resolve on retry
_TRANSIENT_PATTERNS = (
    "timed out", "timeout", "connection refused", "connection reset",
    "temporarily unavailable", "rate limit", "429", "503", "502",
    "ssl", "certificate", "too many requests",
)

# Permanent: errors that will never succeed on retry
_PERMANENT_PATTERNS = (
    "protected file", "blocked for safety", "modification denied",
    "no approval channel", "already denied",
)


def _classify_error(action_type: str, error_msg: str) -> tuple[str, str]:
    """Classify an action error for recovery routing.

    Returns (classification, hint) where:
      classification: "transient" | "mechanical" | "permanent"
      hint: targeted fix suggestion for the model (empty for transient/permanent)

    Transient errors get retried with backoff (no step burned).
    Mechanical errors get a hint injected into the next prompt.
    Permanent errors are recorded and the model is left to adapt.
    """
    err_lower = error_msg.lower()

    # Check permanent first — these should never be retried
    for pattern in _PERMANENT_PATTERNS:
        if pattern in err_lower:
            return "permanent", ""

    # Check transient — worth retrying
    for pattern in _TRANSIENT_PATTERNS:
        if pattern in err_lower:
            return "transient", ""

    # Everything else is mechanical — provide targeted fix hints
    hint = ""
    if "file not found" in err_lower or "not found" in err_lower:
        hint = (
            "The file was not found. Use list_files to check what exists "
            "in that directory, then retry with the correct path."
        )
    elif "syntax error" in err_lower:
        hint = (
            "Your code had a syntax error. Read back the file to see "
            "the current state, then use edit_file to fix the specific error."
        )
    elif "find" in err_lower and "not found in" in err_lower:
        hint = (
            "The edit_file 'find' string didn't match. Use read_file "
            "to get the exact current contents, then retry with the "
            "exact text copied from the file."
        )
    elif "not a directory" in err_lower:
        hint = (
            "That path is not a directory. Use list_files on the parent "
            "directory to find the correct path."
        )
    elif "path escapes" in err_lower:
        hint = (
            "The path is outside the allowed boundaries. Use paths "
            "relative to the project root (e.g. workspace/projects/...)."
        )
    elif "empty" in err_lower:
        hint = (
            "A required field was empty. Make sure all fields have "
            "actual content — don't leave them blank."
        )

    return "mechanical", hint
