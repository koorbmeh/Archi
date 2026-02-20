"""
Plan Executor — Multi-step autonomous task execution.

Gives Archi the ability to work through complex tasks by chaining
research, analysis, file operations, and self-improvement in a
reasoning loop.

Instead of the single-shot "ask model -> one action -> done" pattern,
the PlanExecutor:
  1. Asks the model what the next step is
  2. Executes that step via the tool registry or built-in handlers
  3. Records the result
  4. Loops until done or step limit reached

After completion, runs a self-verification pass that reads back any
files created and checks their quality.

Crash recovery: execution state is persisted to disk after each step.
If the process is interrupted (power loss, Windows update, etc.), the
next run can resume from where it left off by passing the same task_id.

Supported actions:
  Research:
    - web_search: search DuckDuckGo for information
    - fetch_webpage: fetch and read full content of a URL
  Workspace files (reports, research output):
    - create_file: write files to workspace/
    - append_file: add content to existing workspace files
  File reading (project-wide):
    - read_file: read any project file (workspace/ or src/)
    - list_files: list contents of any project directory
  Self-improvement / code agency:
    - write_source: create/modify source files with git checkpoint + backup + syntax check
    - edit_file: surgical find-and-replace with git checkpoint + backup + syntax check + rollback
    - run_python: execute Python snippets for testing
    - run_command: execute shell commands (pip, pytest, git, etc.) with safety
  Control:
    - think: internal reasoning note (no execution)
    - done: signal task completion with summary
"""

import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Build a reusable SSL context from certifi's CA bundle.
# This fixes CERTIFICATE_VERIFY_FAILED on Windows (e.g. arxiv.org).
try:
    import certifi
    _ssl_context = ssl.create_default_context(cafile=certifi.where())
    _ssl_source = f"certifi ({certifi.where()})"
except ImportError:
    _ssl_context = ssl.create_default_context()
    _ssl_source = "system default (certifi not installed)"

from src.utils.git_safety import post_modify_commit, pre_modify_checkpoint, rollback_last
from src.utils.parsing import extract_json as _extract_json

logger = logging.getLogger(__name__)
logger.debug("SSL context: %s", _ssl_source)

# Safety limits
# Note: Dream cycle tasks are also bounded by per-cycle budget ($0.50) and
# time cap (120 min), so this step limit is a secondary safety net — not the
# primary constraint. Set high enough to let complex project work finish.
MAX_STEPS_PER_TASK = 50
MAX_STEPS_CODING = 25  # Coding tasks need more steps (read → edit → run → fix → verify)
MAX_STEPS_CHAT = 12    # Interactive chat tasks: enough for research-write-verify, fast enough for a user waiting
PLAN_MAX_TOKENS = 4096
SUMMARY_MAX_TOKENS = 400

# Crash-recovery state older than this is treated as stale
_STATE_MAX_AGE_HOURS = 24

# ── Task cancellation signal ─────────────────────────────────────────
# Two modes:
#   1. "user_cancel" — single-shot: one user "stop" cancels one task,
#      flag is cleared on first read so the next task starts clean.
#   2. "shutdown" — sticky: service is shutting down, ALL concurrent
#      PlanExecutors must stop.  Flag stays set until explicitly reset
#      (only reset by clear_shutdown_flag, called at next service start).
_cancel_lock = threading.Lock()
_cancel_requested: bool = False
_cancel_message: str = ""
_shutdown_requested: bool = False  # sticky — survives read


def signal_task_cancellation(message: str = "") -> None:
    """Signal running PlanExecutor(s) to stop after their current step.

    If *message* is ``"shutdown"`` or ``"service_shutdown"``, the flag is
    sticky and will be seen by ALL concurrent executors (not just the
    first one to check).  Otherwise it's single-shot for user cancels.
    """
    global _cancel_requested, _cancel_message, _shutdown_requested
    with _cancel_lock:
        _cancel_requested = True
        _cancel_message = message
        if message in ("shutdown", "service_shutdown"):
            _shutdown_requested = True
    logger.info("Task cancellation signalled: %s", message[:80] if message else "(no message)")


def check_and_clear_cancellation() -> Optional[str]:
    """Check if cancellation was requested.

    For user cancels (single-shot): clears the flag so only one executor
    picks it up.  For shutdown: returns the message but leaves the flag
    set so every concurrent executor sees it.
    """
    global _cancel_requested, _cancel_message, _shutdown_requested
    with _cancel_lock:
        if _shutdown_requested:
            # Sticky — don't clear, every executor should see this
            return _cancel_message or "shutdown"
        if _cancel_requested:
            msg = _cancel_message
            _cancel_requested = False
            _cancel_message = ""
            return msg
        return None


def clear_shutdown_flag() -> None:
    """Reset the sticky shutdown flag.  Call at service startup."""
    global _cancel_requested, _cancel_message, _shutdown_requested
    with _cancel_lock:
        _shutdown_requested = False
        _cancel_requested = False
        _cancel_message = ""


def _estimate_total_steps(steps_taken: List[Dict], max_steps: int) -> int:
    """Estimate how many total steps this task will likely need.

    Uses a simple heuristic based on the actions taken so far:
    - If we've seen a file-write action (create_file, write_source, etc.),
      the task is probably wrapping up soon (~2-3 more steps for verification).
    - If we're still in a research phase (web_search, fetch_webpage),
      estimate based on typical research patterns (~6-8 total).
    - For the first 2 steps, just show the max (not enough data yet).
    - Never estimate more than max_steps.

    Returns an estimated total step count (not remaining).
    """
    n = len(steps_taken)

    # Not enough data in the first 2 steps — show max
    if n < 2:
        return max_steps

    actions = [s.get("action", "") for s in steps_taken]

    # Count action types
    researching = sum(1 for a in actions if a in ("web_search", "research", "fetch_webpage"))
    writing = sum(1 for a in actions if a in ("create_file", "append_file", "write_source", "edit_file"))
    thinking = sum(1 for a in actions if a == "think")

    # If we've already started writing files, we're near the end
    if writing > 0:
        # Typically: 1-2 more steps (verify, done)
        estimate = n + 2
    # Pure research phase — typical pattern is search/fetch 2-4 times then write
    elif researching > 0:
        # Estimate: current research steps + ~2 more research + write + verify + done
        remaining_research = max(0, 3 - researching)
        estimate = n + remaining_research + 3  # write + verify + done
    # Thinking/planning phase
    elif thinking > 0:
        estimate = n + 5  # still early
    else:
        estimate = max_steps

    # Clamp: at least current step count + 1, at most max_steps
    return max(n + 1, min(estimate, max_steps))


# Hardcoded fallbacks — used if rules.yaml is missing or corrupt.
# The canonical values live in config/rules.yaml (protected_files, blocked_commands).
_DEFAULT_PROTECTED_PATHS = frozenset({
    "src/core/plan_executor.py",
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


def _load_safety_config():
    """Load protected_files, blocked_commands, and approval_required_paths from rules.yaml.

    Returns (frozenset, tuple, tuple) — the protected file paths, blocked command
    patterns, and path prefixes requiring user approval before modification.
    Falls back to hardcoded defaults if rules.yaml can't be read.
    """
    try:
        import yaml
        from src.utils.paths import base_path
        rules_path = os.path.join(base_path(), "config", "rules.yaml")
        with open(rules_path, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f) or {}
        protected = rules.get("protected_files", [])
        blocked = rules.get("blocked_commands", [])
        approval = rules.get("approval_required_paths", [])
        if protected and blocked:
            logger.debug(
                "Loaded safety config from rules.yaml: %d protected files, "
                "%d blocked commands, %d approval-required paths",
                len(protected), len(blocked), len(approval),
            )
            return (
                frozenset(protected),
                tuple(blocked),
                tuple(approval) if approval else _DEFAULT_APPROVAL_REQUIRED_PATHS,
            )
    except Exception as e:
        logger.warning("Could not load safety config from rules.yaml: %s (using defaults)", e)
    return _DEFAULT_PROTECTED_PATHS, _DEFAULT_BLOCKED_COMMANDS, _DEFAULT_APPROVAL_REQUIRED_PATHS


_PROTECTED_PATHS, _BLOCKED_COMMANDS, _APPROVAL_REQUIRED_PATHS = _load_safety_config()


def _requires_approval(relative_path: str) -> bool:
    """Return True if the path requires user approval before modification.

    Checks against the approval_required_paths list from rules.yaml.
    Protected files are already blocked outright and don't need this check.
    """
    rel = relative_path.lstrip("/").replace("\\", "/")
    for prefix in _APPROVAL_REQUIRED_PATHS:
        if rel.startswith(prefix):
            return True
    return False


def _check_pre_approved(relative_path: str) -> bool:
    """Check if a path has been pre-approved via deferred approval.

    When a user misses an approval timeout, they can later reply
    "approve src/tools/foo.py" to create a pre-approval file.  This
    function checks for that file and consumes it (one-time use).

    Returns True if a valid pre-approval was found and consumed.
    """
    try:
        from src.utils.paths import base_path
        rel = relative_path.lstrip("/").replace("\\", "/")
        pa_file = os.path.join(
            base_path(), "data", "pre_approved",
            rel.replace("/", "_").replace("\\", "_") + ".txt",
        )
        if os.path.isfile(pa_file):
            # Consume: one-time use, delete after reading
            os.remove(pa_file)
            logger.info("Pre-approval consumed for %s", rel)
            return True
    except Exception as e:
        logger.warning("Error checking pre-approval for %s: %s", relative_path, e)
    return False


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------

def _strip_absolute_prefix(raw: str) -> str:
    """Strip Windows absolute prefixes and base_path prefixes from a path.

    The LLM sometimes returns full absolute Windows paths instead of
    relative paths.  If we naively join that with ``base_path()``, we get a
    doubled path because ``os.path.join`` on Linux doesn't recognise a
    Windows drive letter as a root.

    This helper:
    1. Strips a leading Windows drive letter (``C:/``, ``D:\\``, etc.).
    2. Strips a leading copy of the project root so the remainder is relative.
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


def _check_protected(relative_path: str) -> None:
    """Raise ValueError if path is a protected file."""
    rel = relative_path.lstrip("/").replace("\\", "/")
    for protected in _PROTECTED_PATHS:
        if rel == protected or rel.endswith("/" + protected):
            raise ValueError(f"Protected file cannot be modified: {protected}")


# ---------------------------------------------------------------------------
# Source code safety helpers
# ---------------------------------------------------------------------------

def _backup_file(filepath: str) -> Optional[str]:
    """Create a timestamped backup before modifying a source file.

    Backups are stored in data/source_backups/ with a flattened filename
    so they're easy to find and restore from.
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
# Web content helpers
# ---------------------------------------------------------------------------

def _fetch_url_text(url: str, max_chars: int = 5000) -> str:
    """Fetch a URL and extract readable text from the HTML.

    Strips scripts, styles, and HTML tags. Returns plain text limited
    to max_chars. This gives Archi the ability to actually read web
    pages — not just search snippets.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context) as resp:
            raw = resp.read()
        # Try to decode
        html = raw.decode("utf-8", errors="replace")
        # Strip <script> and <style> blocks
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Strip HTML tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Decode common HTML entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        return f"Error fetching {url}: {e}"


def _state_dir() -> Path:
    """Directory for PlanExecutor crash-recovery state."""
    from src.utils.paths import base_path
    d = Path(base_path()) / "data" / "plan_state"
    d.mkdir(parents=True, exist_ok=True)
    return d


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


# ===========================================================================
# PlanExecutor
# ===========================================================================

class PlanExecutor:
    """
    Multi-step task execution engine for autonomous overnight work.

    Key capabilities:
    - Self-verification: after "done", reads back created files and rates quality
    - Crash recovery: state saved after each step; interrupted tasks auto-resume
    - Deep research: web_search + fetch_webpage for reading full articles
    - Self-improvement: write_source + run_python for modifying Archi's own code
    - Safety: protected files list, automatic backups, syntax validation

    Usage:
        executor = PlanExecutor(router=model_router)
        result = executor.execute(
            task_description="Research vitamin D dosing strategies",
            goal_context="Health optimization protocol",
            task_id="task_42",  # enables crash recovery
        )
    """

    def __init__(
        self,
        router: Any,
        tools: Optional[Any] = None,
        learning_system: Optional[Any] = None,
        hints: Optional[List[str]] = None,
        approval_callback: Optional[Callable[[str, str, str], bool]] = None,
    ):
        """
        Args:
            router: ModelRouter for generating next-step decisions
            tools: ToolRegistry instance (lazy-created if not provided)
            learning_system: Optional LearningSystem for recording action outcomes
            hints: Optional list of short insight strings to inject into step prompts
            approval_callback: Optional function(action, path, task_description) -> bool.
                Called before write_source or edit_file on approval-required paths
                (e.g. src/).  Must return True to proceed, False to deny.
                If None and the path requires approval, the modification is denied
                by default (safe for autonomous/dream mode).
        """
        self._router = router
        self._tools = tools
        self._learning_system = learning_system
        self._hints = hints or []
        self._approval_callback = approval_callback
        self._task_id: Optional[str] = None
        self._task_description: Optional[str] = None
        self._source_write_denied = False  # Set True after any write_source/edit_file denial; blocks further attempts

    @property
    def tools(self):
        if self._tools is None:
            from src.tools.tool_registry import get_shared_registry
            self._tools = get_shared_registry()
        return self._tools

    # -- Public API --------------------------------------------------------

    def execute(
        self,
        task_description: str,
        goal_context: str = "",
        max_steps: int = MAX_STEPS_PER_TASK,
        task_id: Optional[str] = None,
        conversation_history: str = "",
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a task through multi-step reasoning.

        The model decides what to do at each step, and the executor runs it.
        This continues until the model says "done" or we hit the step limit.
        After completion, any files created are verified for quality.

        If task_id matches a previously interrupted execution, the task
        resumes from where it left off (crash recovery).

        Args:
            task_description: What to accomplish.
            goal_context: Parent goal description (provides broader context).
            max_steps: Safety limit on iterations.
            task_id: Identifier for crash recovery (e.g. GoalManager task_id).
            conversation_history: Optional chat history for interactive requests.
                When present, the model gets user conversation context so it can
                give relevant responses in its "done" summary.
            progress_callback: Optional callback for progress updates during
                interactive chat. Called as progress_callback(step_num, max_steps, message)
                after each non-think action. Allows the caller (e.g. Discord bot) to
                send live status updates to the user.

        Returns:
            dict with keys: success, steps_taken, total_steps, executed_steps,
            successful_steps, total_cost, duration_ms, verified, files_created.
        """
        self._conversation_history = conversation_history
        self._progress_callback = progress_callback
        self._task_description = task_description
        self._source_write_denied = False  # Reset per-execution
        self._schema_retries_exhausted = False  # Set True when JSON schema retries exhausted
        t0 = time.monotonic()
        steps_taken: List[Dict[str, Any]] = []
        total_cost = 0.0
        files_created: List[str] = []

        # Crash recovery: set task_id and check for interrupted state
        self._task_id = task_id or f"plan_{int(time.time())}_{id(self)}"

        resumed = self._load_state()
        if resumed:
            steps_taken = resumed.get("steps_taken", [])
            total_cost = resumed.get("total_cost", 0.0)
            files_created = resumed.get("files_created", [])
            logger.info(
                "PlanExecutor: RESUMING task '%s' from step %d",
                self._task_id, len(steps_taken),
            )
        else:
            logger.info(
                "PlanExecutor: starting (max %d steps): %s",
                max_steps, task_description[:120],
            )

        # Shared reference — must be set AFTER crash recovery may reassign steps_taken
        self._step_history = steps_taken

        start_step = len(steps_taken)

        for step_num in range(start_step, max_steps):
            # Check for user cancellation between steps
            _cancel_msg = check_and_clear_cancellation()
            if _cancel_msg is not None:
                logger.info(
                    "PlanExecutor: cancelled by user at step %d — %s",
                    step_num + 1, _cancel_msg[:80],
                )
                _findings = []
                for s in steps_taken:
                    if s.get("action") in ("web_search", "research", "fetch_webpage") and s.get("success"):
                        _findings.append(s.get("snippet", "")[:200])
                    elif s.get("action") in ("create_file", "append_file") and s.get("success"):
                        _findings.append(f"[Created: {s.get('params', {}).get('path', '?')}]")
                _partial = "; ".join(f for f in _findings if f) or "No results yet"
                steps_taken.append({
                    "step": step_num + 1,
                    "action": "done",
                    "summary": f"Task cancelled by user after {step_num} steps. Partial progress: {_partial[:400]}",
                    "cancelled": True,
                })
                break

            # -- Rewrite-loop detection ────────────────────────────
            # If the model has been rewriting the same file repeatedly,
            # inject a warning so it knows to stop or try something different.
            _rewrite_warning = ""
            if step_num > 0 and steps_taken:
                _write_counts: Dict[str, int] = {}
                for _s in steps_taken:
                    if _s.get("action") in ("create_file", "write_source", "append_file") and _s.get("success"):
                        _wpath = (_s.get("params") or {}).get("path", "")
                        if _wpath:
                            _write_counts[_wpath] = _write_counts.get(_wpath, 0) + 1
                for _wpath, _wcount in _write_counts.items():
                    if _wcount >= 7:
                        # Hard abort — the model is stuck in an infinite loop
                        logger.warning(
                            "PlanExecutor: force-stopping — file '%s' written %d times (loop detected)",
                            _wpath, _wcount,
                        )
                        steps_taken.append({
                            "step": step_num + 1,
                            "action": "done",
                            "summary": f"Task stopped: rewrite loop detected on {_wpath} ({_wcount} writes). Partial work saved.",
                            "loop_aborted": True,
                        })
                        _rewrite_warning = "__ABORT__"
                        break
                    elif _wcount >= 5:
                        _rewrite_warning = (
                            f"\n\nWARNING: You have written '{_wpath}' {_wcount} times already. "
                            f"Stop rewriting the same file. Either the file is done and you should "
                            f"move on to the next step, or something is fundamentally wrong and you "
                            f"should report done with what you have."
                        )
                        break
                    elif _wcount >= 3:
                        _rewrite_warning = (
                            f"\n\nNOTE: You've written '{_wpath}' {_wcount} times. "
                            f"If it's correct now, move on. Don't keep rewriting it."
                        )
                if _rewrite_warning == "__ABORT__":
                    break

            # Ask model: "what's next?"
            prompt = self._build_step_prompt(
                task_description, goal_context, steps_taken,
                step_num=step_num, max_steps=max_steps,
            )
            if _rewrite_warning:
                prompt += _rewrite_warning

            # API-first: all plan steps route to Grok via OpenRouter.
            # classify_hint="plan_step" lets the router classify by task
            # description length rather than the inflated full prompt.
            resp = self._router.generate(
                prompt=prompt,
                max_tokens=PLAN_MAX_TOKENS,
                temperature=0.3,
                classify_hint="plan_step",
            )
            total_cost += resp.get("cost_usd", 0)

            parsed = _extract_json(resp.get("text", ""))

            # Structured output validation with retry (max 2 retries for
            # bad JSON or schema violations).  Each retry gets a targeted
            # error message so the model knows exactly what to fix.
            _retries = 0
            _MAX_RETRIES = 2
            while _retries < _MAX_RETRIES:
                if not parsed:
                    _retry_hint = "Respond with ONLY a valid JSON object."
                    logger.warning(
                        "PlanExecutor: invalid JSON at step %d (retry %d/%d)",
                        step_num + 1, _retries + 1, _MAX_RETRIES,
                    )
                else:
                    from src.core.output_schemas import validate_action
                    _schema_err = validate_action(parsed)
                    if _schema_err is None:
                        break  # Valid action — proceed
                    _retry_hint = (
                        f"Schema error: {_schema_err}\n"
                        f"Fix the error and respond with ONLY a valid JSON object."
                    )
                    logger.warning(
                        "PlanExecutor: schema violation at step %d (retry %d/%d): %s",
                        step_num + 1, _retries + 1, _MAX_RETRIES, _schema_err[:120],
                    )

                retry = self._router.generate(
                    prompt=prompt + f"\n\n{_retry_hint}",
                    max_tokens=PLAN_MAX_TOKENS,
                    temperature=0.1,
                )
                total_cost += retry.get("cost_usd", 0)
                parsed = _extract_json(retry.get("text", ""))
                _retries += 1
            else:
                # Exhausted retries — check if we ended up with something usable
                if not parsed:
                    logger.warning("PlanExecutor: JSON/schema retries exhausted, stopping")
                    self._schema_retries_exhausted = True
                    break

            action_type = parsed.get("action", "")

            # -- Task complete --
            if action_type == "done":
                summary = parsed.get("summary", "Task completed.")
                logger.info(
                    "PlanExecutor: done after %d steps — %s",
                    step_num + 1, summary[:120],
                )
                steps_taken.append({
                    "step": step_num + 1,
                    "action": "done",
                    "summary": summary,
                })
                break

            # -- Internal reasoning --
            if action_type == "think":
                note = parsed.get("note", "")
                logger.info(
                    "PlanExecutor step %d: think — %s",
                    step_num + 1, note[:120],
                )
                steps_taken.append({
                    "step": step_num + 1,
                    "action": "think",
                    "note": note,
                    "success": True,
                })
                self._save_state(
                    task_description, goal_context, steps_taken,
                    total_cost, files_created,
                )
                continue

            # -- Execute an action --
            result = self._execute_action(parsed, step_num + 1)

            # ── Mechanical Error Recovery ─────────────────────────────
            # Classify failed actions and handle appropriately:
            #   transient  → retry with backoff (no step burned)
            #   mechanical → record step, inject targeted fix hint
            #   permanent  → record step, no retry possible
            if not result.get("success", False):
                err_class, err_hint = _classify_error(
                    action_type, result.get("error", ""),
                )
                if err_class == "transient":
                    # Retry once with backoff — don't burn a step
                    logger.info(
                        "PlanExecutor step %d: transient error on %s, retrying after 2s",
                        step_num + 1, action_type,
                    )
                    time.sleep(2)
                    result = self._execute_action(parsed, step_num + 1)
                    if not result.get("success", False):
                        logger.warning(
                            "PlanExecutor step %d: transient retry failed for %s",
                            step_num + 1, action_type,
                        )
                elif err_class == "mechanical" and err_hint:
                    # Inject targeted fix hint into the step record so
                    # _build_step_prompt sees the hint in the history.
                    result["error_hint"] = err_hint

            steps_taken.append({
                "step": step_num + 1,
                "action": action_type,
                "params": {k: v for k, v in parsed.items() if k != "action"},
                **result,
            })

            # Send progress update to caller (e.g. Discord typing status)
            if self._progress_callback:
                try:
                    progress_msg = self._describe_step(action_type, parsed, result)
                    estimated_total = _estimate_total_steps(steps_taken, max_steps)
                    self._progress_callback(step_num + 1, estimated_total, progress_msg)
                except Exception:
                    pass  # Never let progress reporting break execution

            # Record action outcome for learning (closes the feedback loop)
            if self._learning_system and action_type not in ("think", "done"):
                try:
                    self._learning_system.record_action_outcome(
                        action_type, result.get("success", False),
                    )
                except Exception:
                    pass

            # Track files for verification
            if action_type in ("create_file", "append_file", "write_source", "edit_file") and result.get("success"):
                path = result.get("path", "")
                if path and path not in files_created:
                    files_created.append(path)

            # Persist state for crash recovery
            self._save_state(
                task_description, goal_context, steps_taken,
                total_cost, files_created,
            )

            if not result.get("success", False):
                logger.warning(
                    "PlanExecutor step %d failed (%s): %s",
                    step_num + 1, action_type, result.get("error", ""),
                )
                # Don't abort — let the model adapt on next iteration

        # -- Self-verification pass --
        verified = False
        if files_created:
            ver_result = self._verify_work(
                task_description, goal_context, steps_taken, files_created,
            )
            verified = ver_result.get("passed", False)
            total_cost += ver_result.get("cost", 0)

        duration_ms = int((time.monotonic() - t0) * 1000)

        executed = [s for s in steps_taken if s["action"] not in ("think", "done")]
        successful = [s for s in executed if s.get("success", False)]

        logger.info(
            "PlanExecutor: finished (%d/%d steps OK, %d ms, $%.4f, verified=%s)",
            len(successful), len(executed), duration_ms, total_cost, verified,
        )

        # Clean up crash-recovery state on completion
        self._clear_state()

        # Determine overall success:
        # - Schema-retry-exhausted tasks are ALWAYS failures, regardless of what
        #   steps ran.  Exhausting retries means the model couldn't produce valid
        #   JSON — recording as success pollutes the learning system.
        # - Must have at least one successful step
        # - If files were created and verification ran, verification must pass.
        #   A task that produces broken output (quality < 6/10) is NOT a success,
        #   even if individual steps "worked."  This prevents the learning system
        #   from recording garbage as success and blocks follow-up goal creation
        #   from low-quality work.
        _has_successful_steps = len(successful) > 0
        _verification_ok = verified or not files_created  # no files = nothing to verify
        _success = _has_successful_steps and _verification_ok and not self._schema_retries_exhausted

        if self._schema_retries_exhausted and _has_successful_steps:
            logger.info(
                "PlanExecutor: task had %d successful steps but JSON schema retries "
                "exhausted — marking as FAILURE to prevent bad learning/follow-up goals",
                len(successful),
            )

        return {
            "success": _success,
            "steps_taken": steps_taken,
            "total_steps": len(steps_taken),
            "executed_steps": len(executed),
            "successful_steps": len(successful),
            "total_cost": total_cost,
            "duration_ms": duration_ms,
            "verified": verified,
            "files_created": files_created,
            "schema_retries_exhausted": self._schema_retries_exhausted,
        }

    # -- Progress reporting ------------------------------------------------

    @staticmethod
    def _describe_step(
        action_type: str, parsed: Dict[str, Any], result: Dict[str, Any],
    ) -> str:
        """Generate a short, human-readable description of a completed step.

        Used for live progress updates during interactive chat so the user
        sees what Archi is doing instead of staring at silence.
        """
        ok = result.get("success", False)
        if action_type == "web_search":
            query = (parsed.get("query") or "")[:50]
            return f"Searching: {query}..." if ok else f"Search failed: {query}"
        if action_type == "fetch_webpage":
            url = (parsed.get("url") or "")
            # Show just the domain for readability
            try:
                domain = url.split("//", 1)[1].split("/", 1)[0] if "//" in url else url[:40]
            except Exception:
                domain = url[:40]
            return f"Reading {domain}..." if ok else f"Couldn't read {domain}"
        if action_type == "create_file":
            path = (parsed.get("path") or "")
            name = os.path.basename(path) if path else "file"
            return f"Writing {name}..."
        if action_type == "append_file":
            path = (parsed.get("path") or "")
            name = os.path.basename(path) if path else "file"
            return f"Updating {name}..."
        if action_type == "read_file":
            path = (parsed.get("path") or "")
            name = os.path.basename(path) if path else "file"
            return f"Reading {name}..."
        if action_type in ("write_source", "edit_file"):
            path = (parsed.get("path") or "")
            name = os.path.basename(path) if path else "file"
            return f"Editing {name}..."
        if action_type == "run_python":
            return "Running code..."
        if action_type == "run_command":
            cmd = (parsed.get("command") or "")[:40]
            return f"Running: {cmd}..."
        if action_type == "list_files":
            return "Checking files..."
        return f"{action_type}..."

    # -- Prompt building ---------------------------------------------------

    @staticmethod
    def _compress_step(s: Dict[str, Any]) -> str:
        """Compress a step to a one-liner for context compression.

        Keeps: step number, action, path/query, success/fail status.
        Drops: full snippets, file contents, detailed error messages.
        This saves ~200-500 tokens per compressed step.
        """
        act = s.get("action", "?")
        n = s.get("step", "?")
        ok = "ok" if s.get("success") else "FAIL"
        params = s.get("params", {})
        if act in ("web_search", "research"):
            return f"  {n}. [{act} \"{params.get('query', '')[:40]}\"] -> {ok}"
        if act == "fetch_webpage":
            return f"  {n}. [fetch {params.get('url', '')[:40]}] -> {ok}"
        if act in ("create_file", "append_file", "write_source", "edit_file"):
            return f"  {n}. [{act} {params.get('path', '')[:40]}] -> {ok}"
        if act in ("read_file", "list_files"):
            return f"  {n}. [{act} {params.get('path', '')[:40]}] -> {ok}"
        if act == "run_python":
            return f"  {n}. [run_python] -> {ok}"
        if act == "run_command":
            return f"  {n}. [run_command '{params.get('command', '')[:30]}'] -> {ok}"
        if act == "think":
            return f"  {n}. [think] {s.get('note', '')[:60]}"
        if act == "done":
            return f"  {n}. [done] {s.get('summary', '')[:60]}"
        return f"  {n}. [{act}] -> {ok}"

    def _build_step_prompt(
        self,
        task_description: str,
        goal_context: str,
        steps_taken: List[Dict[str, Any]],
        step_num: int = 0,
        max_steps: int = MAX_STEPS_PER_TASK,
    ) -> str:
        """Build the prompt asking the model for the next step.

        Context compression: after 8 steps, older steps are compressed to
        one-line summaries (action + outcome). The 5 most recent steps keep
        full fidelity (snippets, file contents, etc.). This prevents prompt
        bloat on long tasks without losing recent context.
        """
        # Context compression: full detail for recent steps, compressed for older
        _FULL_FIDELITY_WINDOW = 5
        _COMPRESS_AFTER = 8
        compress = len(steps_taken) > _COMPRESS_AFTER

        history_lines = []
        for idx, s in enumerate(steps_taken):
            act = s.get("action", "?")
            n = s["step"]
            # Compressed one-liner for older steps
            is_old = compress and idx < len(steps_taken) - _FULL_FIDELITY_WINDOW
            if is_old:
                history_lines.append(self._compress_step(s))
                continue
            # Full fidelity for recent steps
            if act == "think":
                history_lines.append(f"  {n}. [think] {s.get('note', '')[:150]}")
            elif act == "web_search":
                q = s.get("params", {}).get("query", "")
                snip = s.get("snippet", "no results")[:300]
                history_lines.append(f"  {n}. [web_search \"{q}\"] -> {snip}")
            elif act == "fetch_webpage":
                u = s.get("params", {}).get("url", "")
                snip = s.get("snippet", "")[:300]
                history_lines.append(f"  {n}. [fetch_webpage {u}] -> {snip}")
            elif act == "create_file":
                p = s.get("params", {}).get("path", "")
                ok = "saved" if s.get("success") else s.get("error", "failed")
                history_lines.append(f"  {n}. [create_file {p}] -> {ok}")
            elif act == "append_file":
                p = s.get("params", {}).get("path", "")
                ok = "appended" if s.get("success") else s.get("error", "failed")
                history_lines.append(f"  {n}. [append_file {p}] -> {ok}")
            elif act == "read_file":
                p = s.get("params", {}).get("path", "")
                snip = s.get("snippet", "")[:600]
                history_lines.append(f"  {n}. [read_file {p}] -> {snip}")
            elif act == "list_files":
                p = s.get("params", {}).get("path", "")
                snip = s.get("snippet", "")[:300]
                history_lines.append(f"  {n}. [list_files {p}] -> {snip}")
            elif act == "write_source":
                p = s.get("params", {}).get("path", "")
                ok = "saved" if s.get("success") else s.get("error", "failed")
                bu = " (backed up)" if s.get("backed_up") else ""
                history_lines.append(f"  {n}. [write_source {p}] -> {ok}{bu}")
            elif act == "edit_file":
                p = s.get("params", {}).get("path", "")
                ok = "edited" if s.get("success") else s.get("error", "failed")
                history_lines.append(f"  {n}. [edit_file {p}] -> {ok}")
            elif act == "run_python":
                snip = s.get("snippet", "")[:200]
                ok = "ok" if s.get("success") else "error"
                history_lines.append(f"  {n}. [run_python] -> {ok}: {snip}")
            elif act == "run_command":
                cmd = s.get("params", {}).get("command", "")[:60]
                snip = s.get("snippet", "")[:200]
                ok = "ok" if s.get("success") else "error"
                history_lines.append(f"  {n}. [run_command '{cmd}'] -> {ok}: {snip}")
            else:
                history_lines.append(f"  {n}. [{act}] -> {s.get('error', 'done')}")

        history_block = ""
        if history_lines:
            header = "\n\nSteps completed so far"
            if compress:
                compressed_count = len(steps_taken) - _FULL_FIDELITY_WINDOW
                header += f" (steps 1-{compressed_count} summarized)"
            header += ":\n"
            history_block = header + "\n".join(history_lines)

            # Inject hard warnings about failed fetches and repeated searches
            failed_domains = set()
            search_queries = []
            for s in self._step_history:
                act = s.get("action", "")
                if act == "fetch_webpage" and not s.get("success"):
                    url = s.get("params", {}).get("url", "")
                    try:
                        from urllib.parse import urlparse
                        domain = urlparse(url).netloc
                        if domain:
                            failed_domains.add(domain)
                    except Exception:
                        pass
                if act == "web_search":
                    search_queries.append(s.get("params", {}).get("query", ""))

            warnings = []
            if failed_domains:
                domains_str = ", ".join(sorted(failed_domains))
                warnings.append(f"BLOCKED DOMAINS (do NOT fetch again): {domains_str}")
            # Detect repeated similar searches (Jaccard word overlap > 0.5)
            if len(search_queries) >= 3:
                seen_groups: list[set[str]] = []
                for q in search_queries:
                    qw = set(q.lower().split())
                    matched = False
                    for g in seen_groups:
                        overlap = len(qw & g) / max(len(qw | g), 1)
                        if overlap > 0.5:
                            matched = True
                            break
                    if not matched:
                        seen_groups.append(qw)
                repeated = len(search_queries) - len(seen_groups)
                if repeated >= 2:
                    warnings.append(
                        f"You have done {len(search_queries)} searches with significant overlap. "
                        "STOP searching and USE the information you already have to produce output."
                    )
            if warnings:
                history_block += "\n\n⚠️ " + "\n⚠️ ".join(warnings)

            # Inject error recovery hints from the most recent failed step
            if steps_taken:
                _last_hint = steps_taken[-1].get("error_hint", "")
                if _last_hint:
                    history_block += f"\n\n💡 FIX HINT: {_last_hint}"

        goal_block = f"\nGoal: {goal_context}" if goal_context else ""

        # Include conversation history for interactive chat requests
        conv_block = ""
        if getattr(self, "_conversation_history", ""):
            conv_block = f"\n\nRecent conversation with user:\n{self._conversation_history}\n---"

        hints_block = ""
        if self._hints:
            hints_block = "\n\nHints from past work:\n" + "\n".join(
                f"- {h}" for h in self._hints[:2]
            )

        # Step budget awareness — tell the model how much runway remains
        remaining = max_steps - step_num
        budget_block = f"\n\n⏱ STEP BUDGET: Step {step_num + 1} of {max_steps} ({remaining} remaining)."
        if remaining <= 3:
            budget_block += (
                "\n⚠️ LOW BUDGET: You are running out of steps. "
                "Stop reading/researching and produce your output NOW. "
                "Use create_file to save your findings, then call done."
            )
        elif remaining <= max_steps // 2:
            budget_block += (
                "\nYou're past the halfway point. Start transitioning from "
                "research/reading to producing output (create_file, then done)."
            )

        return f"""You are Archi, an autonomous AI agent working on a task for Jesse.
ENVIRONMENT: Windows (PowerShell). Do NOT use Unix commands (find, grep, cat, ls).
For file operations, use run_python (os.listdir, pathlib, open) — not shell commands.
{goal_block}{conv_block}
Task: {task_description}
{hints_block}{history_block}{budget_block}

What is the NEXT step? Choose ONE action:

RESEARCH:
- {{"action": "web_search", "query": "specific search query"}}
  Search DuckDuckGo for information. Use multiple searches to go deep.

- {{"action": "fetch_webpage", "url": "https://example.com/article"}}
  Fetch and read the full text content of a web page. Use this after
  web_search to read promising results in detail.

WORKSPACE FILES (project deliverables, code, content):
- {{"action": "create_file", "path": "workspace/projects/ProjectName/file.ext", "content": "file content"}}
  Create project files: code, protocols, configurations, documentation, data files.
  Path must start with workspace/. Save under the project's folder
  (e.g. workspace/projects/Health_Optimization/), NOT under workspace/reports/.
  For code: use .py, .js, .json, etc. For content: use .md with FULL substantive content.

- {{"action": "append_file", "path": "workspace/projects/ProjectName/file.ext", "content": "content to add"}}
  Add content to an existing file. Use sparingly — prefer create_file with complete content.

FILE READING (project-wide):
- {{"action": "read_file", "path": "src/tools/some_file.py"}}
  Read any file in the project. Use to study existing code before improving it.

- {{"action": "list_files", "path": "workspace/projects/"}}
  List files in any project directory. User projects live under workspace/projects/.

SELF-IMPROVEMENT (source code):
- {{"action": "write_source", "path": "src/tools/new_tool.py", "content": "python code"}}
  Create or modify source code (full file write). Automatic backup + syntax validation.
  Some core files (plan_executor, safety_controller, prime_directive) are protected.

- {{"action": "edit_file", "path": "src/tools/foo.py", "find": "exact old code", "replace": "exact new code"}}
  Surgical find-and-replace in a file. The "find" string must match exactly once.
  Automatic backup + syntax validation + rollback on error.
  Use "replace_all": true for renaming (e.g., variable renames across a file).
  PREFER edit_file over write_source for modifying existing files — it's safer and cheaper.
  RULE: You MUST read_file BEFORE edit_file. The "find" string must be copied from actual
  file contents, NOT guessed from memory. edit_file WILL FAIL if "find" doesn't match exactly.

- {{"action": "run_python", "code": "print('hello world')"}}
  Run a Python snippet to test code. 30 second timeout. Output captured.
  IMPORTANT: The working directory is workspace/, so relative paths resolve inside
  workspace/. Use 'projects/Health_Optimization/...' NOT 'workspace/projects/...'.
  To import from Archi's source code, the project root is on PYTHONPATH automatically.

- {{"action": "run_command", "command": "pytest tests/ -v"}}
  Run a shell command (pip, pytest, git, npm, etc.). 60 second timeout.
  Dangerous commands (rm -rf, format, shutdown, etc.) are blocked.
  Use ONLY for: running tests, installing packages, git operations.
  DO NOT use for file operations (listing, searching, reading files) — use
  run_python with os/pathlib instead. Unix commands (find, grep, cat, ls) WILL FAIL.
  IMPORTANT: Use run_python to call YOUR OWN built-in tools instead of web searching:

  System health (CPU, memory, disk, temperature):
    from src.monitoring.system_monitor import SystemMonitor
    m = SystemMonitor(); h = m.check_health()
    print(f"CPU: {{h.cpu}}%, Mem: {{h.memory}}%, Disk: {{h.disk}}%, Temp: {{h.temperature}}")
    m.log_metrics()  # saves to data/metrics.db

  Component health (models, cache, storage):
    from src.monitoring.health_check import health_check
    result = health_check.check_all()
    print(result)

  Cost tracking:
    from src.monitoring.cost_tracker import get_cost_tracker
    t = get_cost_tracker(); print(t.get_summary('today'))

  Performance stats:
    from src.monitoring.performance_monitor import performance_monitor
    print(performance_monitor.get_stats())

  ALWAYS prefer run_python with these modules over web_search for system tasks.

EFFICIENCY RULES:
- Research phase: do 2-4 searches MAX, then WRITE your output.
  Do NOT search-append-read-search-append in a loop.
- Synthesize all your research into ONE create_file call with complete content.
  Avoid repeated append_file calls that produce bloated, repetitive output.
- If a fetch_webpage fails (403, 404), move on — don't retry the same site.
- When you have enough information to write a good output, STOP researching.

CONTROL:
- {{"action": "think", "note": "reasoning about approach"}}
  Plan or reason before acting.

- {{"action": "ask_user", "question": "Which variant should I use: A or B?"}}
  Ask Jesse a question via Discord and wait for his reply (up to 5 min).
  Time-aware: won't send during quiet hours (11 PM - 9 AM).
  Use this when you need clarification, are choosing between options, or
  lack information that only Jesse can provide. Don't overuse it —
  if you can make a reasonable choice yourself, do that instead.
  Returns his reply text, or an error if he didn't respond in time.

- {{"action": "done", "summary": "clear description of what was accomplished", "confidence": "high|medium|low"}}
  BEFORE calling done, STOP and self-check:
    1. Re-read the task description above. Did you actually do what was asked?
    2. If you created files, did you verify they exist and contain correct content?
    3. If you wrote code, did you test it? Does it run without errors?
    4. Are there obvious gaps or placeholders in your output?
  If any check fails, fix the issue first — don't call done with incomplete work.
  Signal task completion. Your summary is shown to Jesse, so make it useful:
  - Say what you made and what it does (e.g. "Created health_tracker.py — it logs daily symptoms and supplement adherence to a JSON file.")
  - If you wrote code, briefly say how to run/use it (e.g. "Run `python health_tracker.py log` to add a daily entry.")
  - If this is a user chat request, write a direct conversational response.
  Set "confidence" to reflect how reliable your output is:
    - "high": verified data, multiple sources, concrete evidence
    - "medium": partial info, single source, or some assumptions made
    - "low": couldn't find solid data, best-effort answer, or blocked by missing info
  When confidence is medium or low, say so in the summary (e.g. "I found limited info on this"
  or "I'm not fully confident in these numbers"). Never present uncertain results as definitive.

MINDSET — BUILD, DON'T REPORT:
- FUNCTIONAL OUTPUT PRIORITY:
  * run_python: TEST your code after writing it. Don't just create files — verify they work.
  * ask_user: When you need data Jesse already has (supplements, preferences, schedule), ASK
    rather than researching what he already knows. This is a powerful tool — use it.
  * write_source + run_python is your most powerful combo: build → test → iterate.
  * A working 30-line Python script beats a 200-line markdown report EVERY time.
  * When building something for Jesse, think: "Will he actually USE this, or just read it once?"
- Your job is to PRODUCE real, usable deliverables — NOT summaries, gap analyses,
  or reports about what needs to be done.
- CODE IS YOUR SUPERPOWER. You can write Python scripts, automations, data pipelines,
  web scrapers, utilities, and tools. When a problem can be solved with code, WRITE THE CODE.
  Don't describe a solution — implement it. Don't outline an algorithm — write it in Python.
- When a task says "build X" or "advance project X", that means write the actual code/content.
  If the project needs a health tracker, write the Python script. If it needs data analysis,
  write code that loads, processes, and outputs real results. If it needs a protocol, write
  the complete protocol with specific data, not a summary of what a protocol might contain.
- PREFER code over documents. A working .py file that automates something is worth more than
  a .md file describing how to do it manually. If the deliverable could be either a document
  or a script, lean toward the script.
- Research is a MEANS, not the deliverable. Every web_search should lead to concrete output
  (code, filled-in content, real recommendations) — never to a summary of what you found.
- If you find yourself writing "Next steps:" or "Gaps identified:" or "Recommendations for
  future work:", STOP. Do those next steps NOW instead of documenting them.
- When building systems: use write_source or create_file to write real, runnable code.
  Use run_python to test it. Iterate until it works. A script that runs is DONE. A script
  that doesn't run is NOT done — fix it before moving on.
- KEEP SCRIPTS SHORT. Each write_source or create_file should produce under 80 lines of
  code. If you need more, write the core logic first, test it, then use append_file or
  edit_file to add features incrementally. NEVER try to write a 200+ line script in one
  create_file call — it will get cut off and be incomplete.
- If you notice your code was truncated or incomplete after writing, DO NOT rewrite the
  entire file from scratch. Instead, use edit_file to fix the specific incomplete section,
  or append_file to add the missing parts.

Rules:
- Be specific and actionable.
- For research: search -> fetch_webpage on promising URLs -> USE what you learned to build something.
- Do MULTIPLE searches and reads for comprehensive information.
- Keep file content substantive with specific data, numbers, and actionable details.
- If a step fails, adapt and try differently.
- For code changes: read existing code first, then edit_file (prefer) or write_source, then run tests.
- PREFER edit_file over write_source for modifying existing files — it's safer and preserves code you didn't change.
- Use run_command for running tests (pytest), installing packages (pip), git operations, etc.
- VERIFY your work: read back files and test code before calling done.
- DATA VERIFICATION: If a task requires reading specific data (logs, metrics, dietary logs, etc.),
  use read_file or list_files FIRST to confirm the data exists. If the data does not exist,
  call done with summary "Blocked: prerequisite data not found at <path>". NEVER fabricate
  data, timestamps, metrics, or analysis based on files that don't exist.

Respond with ONLY a valid JSON object."""

    # -- Action execution --------------------------------------------------

    def _execute_action(
        self, parsed: Dict[str, Any], step_num: int,
    ) -> Dict[str, Any]:
        """Route and execute a single action."""
        action = parsed.get("action", "")
        # Map common model hallucinations to real actions
        if action in ("research", "analyze", "search"):
            action = "web_search"
            parsed["action"] = action
        if action == "web_search":
            return self._do_web_search(parsed, step_num)
        if action == "fetch_webpage":
            return self._do_fetch_webpage(parsed, step_num)
        if action == "create_file":
            return self._do_create_file(parsed, step_num)
        if action == "append_file":
            return self._do_append_file(parsed, step_num)
        if action == "read_file":
            return self._do_read_file(parsed, step_num)
        if action == "list_files":
            return self._do_list_files(parsed, step_num)
        if action == "write_source":
            if self._source_write_denied:
                logger.info(
                    "write_source BLOCKED (previous denial in this task): %s — "
                    "redirecting model to use workspace/ via create_file instead",
                    parsed.get("path", "?"),
                )
                return {
                    "success": False,
                    "error": (
                        "Source modification was already denied in this task. "
                        "You cannot use write_source or edit_file for the rest of this task. "
                        "Use create_file to write to workspace/ instead."
                    ),
                }
            return self._do_write_source(parsed, step_num)
        if action == "edit_file":
            if self._source_write_denied:
                logger.info(
                    "edit_file BLOCKED (previous denial in this task): %s",
                    parsed.get("path", "?"),
                )
                return {
                    "success": False,
                    "error": (
                        "Source modification was already denied in this task. "
                        "You cannot use write_source or edit_file for the rest of this task. "
                        "Use create_file to write to workspace/ instead."
                    ),
                }
            # Enforce read-before-edit: check if this file was read in recent steps
            edit_path = parsed.get("path", "")
            was_read = any(
                s.get("action") == "read_file"
                and s.get("params", {}).get("path", "") == edit_path
                for s in self._step_history[-8:]  # last 8 steps
            )
            if not was_read and edit_path:
                logger.warning(
                    "edit_file step %d: file not read recently, injecting read first: %s",
                    step_num, edit_path,
                )
                return {
                    "success": False,
                    "error": (
                        f"You must read_file '{edit_path}' before using edit_file on it. "
                        "The 'find' string must be copied from actual file contents, not guessed. "
                        "Do read_file first, then retry edit_file with the exact text."
                    ),
                }
            return self._do_edit_file(parsed, step_num)
        if action == "run_python":
            return self._do_run_python(parsed, step_num)
        if action == "run_command":
            return self._do_run_command(parsed, step_num)
        if action == "ask_user":
            return self._do_ask_user(parsed, step_num)
        # Fallback: route to tool registry (handles MCP-provided tools like
        # GitHub operations). This lets any MCP server add tools without
        # needing explicit action handlers here.
        logger.info("PlanExecutor step %d: routing '%s' to tool registry", step_num, action)
        result = self.tools.execute(action, parsed)
        if result.get("error") == f"Unknown tool: {action}":
            logger.warning("PlanExecutor: unknown action '%s' at step %d", action, step_num)
        return result

    # -- Research actions --------------------------------------------------

    def _do_web_search(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        query = (parsed.get("query") or "").strip()
        if not query:
            return {"success": False, "error": "Empty search query", "snippet": ""}
        logger.info("PlanExecutor step %d: web_search '%s'", step_num, query[:80])
        try:
            result = self.tools.execute("web_search", {"query": query, "max_results": 5})
            if result.get("success"):
                formatted = result.get("formatted", "No results")
                return {"success": True, "snippet": formatted[:800], "full_results": formatted}
            return {"success": False, "error": result.get("error", "Search failed"), "snippet": "Search failed"}
        except Exception as e:
            logger.error("PlanExecutor web_search error: %s", e)
            return {"success": False, "error": str(e), "snippet": f"Error: {e}"}

    def _do_fetch_webpage(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """Fetch a URL and extract readable text content."""
        url = (parsed.get("url") or "").strip()
        if not url:
            return {"success": False, "error": "No URL provided", "snippet": ""}
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        logger.info("PlanExecutor step %d: fetch_webpage '%s'", step_num, url[:100])
        try:
            text = _fetch_url_text(url, max_chars=5000)
            if text.startswith("Error fetching"):
                return {"success": False, "error": text, "snippet": text[:300]}
            return {"success": True, "snippet": text[:800], "full_content": text}
        except Exception as e:
            logger.error("PlanExecutor fetch_webpage error: %s", e)
            return {"success": False, "error": str(e), "snippet": f"Error: {e}"}

    # -- Workspace file actions --------------------------------------------

    def _do_create_file(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        path = (parsed.get("path") or "").strip()
        content = parsed.get("content", "")
        if not path:
            return {"success": False, "error": "No file path"}
        try:
            full_path = _resolve_workspace_path(path)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        logger.info("PlanExecutor step %d: create_file '%s' (%d chars)", step_num, path, len(content))
        try:
            result = self.tools.execute("create_file", {"path": full_path, "content": content})
            if result.get("success"):
                return {"success": True, "path": full_path}
            return {"success": False, "error": result.get("error", "File creation failed")}
        except Exception as e:
            logger.error("PlanExecutor create_file error: %s", e)
            return {"success": False, "error": str(e)}

    def _do_append_file(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """Append content to an existing file (creates if it doesn't exist).

        Includes duplicate-content guard: if the file already contains text
        that overlaps heavily with the new content, skip the append to prevent
        report stacking (the same guide being written 4+ times into one file).
        """
        path = (parsed.get("path") or "").strip()
        content = parsed.get("content", "")
        if not path:
            return {"success": False, "error": "No file path"}
        try:
            full_path = _resolve_workspace_path(path)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        logger.info("PlanExecutor step %d: append_file '%s' (%d chars)", step_num, path, len(content))
        try:
            Path(full_path).parent.mkdir(parents=True, exist_ok=True)
            # Guard: if file already has content, check for substantial overlap
            if os.path.isfile(full_path) and content:
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        existing = f.read()
                    if existing and len(existing) > 100:
                        # Check if the new content's first 200 chars already appear in the file
                        # (catches the common case of the same report appended multiple times)
                        sample = content.strip()[:200].lower()
                        if sample and sample in existing.lower():
                            logger.warning(
                                "PlanExecutor: SKIPPING append — content already present in '%s' "
                                "(duplicate guard triggered)", path,
                            )
                            return {
                                "success": True,
                                "path": full_path,
                                "note": "Content already present in file, append skipped to prevent duplication.",
                            }
                except Exception as e:
                    logger.debug("Duplicate check read failed (proceeding with append): %s", e)
            with open(full_path, "a", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "path": full_path}
        except Exception as e:
            logger.error("PlanExecutor append_file error: %s", e)
            return {"success": False, "error": str(e)}

    # -- File reading (project-wide) ---------------------------------------

    def _do_read_file(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """Read any file within the project (not just workspace)."""
        path = (parsed.get("path") or "").strip()
        if not path:
            return {"success": False, "error": "No file path", "snippet": ""}
        try:
            full_path = _resolve_project_path(path)
        except ValueError as e:
            return {"success": False, "error": str(e), "snippet": ""}
        logger.info("PlanExecutor step %d: read_file '%s'", step_num, path)
        try:
            if not os.path.exists(full_path):
                return {"success": False, "error": f"File not found: {path}", "snippet": ""}
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return {"success": True, "snippet": content[:2000], "full_content": content}
        except Exception as e:
            logger.error("PlanExecutor read_file error: %s", e)
            return {"success": False, "error": str(e), "snippet": f"Error: {e}"}

    def _do_list_files(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """List files in any project directory (not just workspace)."""
        path = (parsed.get("path") or ".").strip()
        try:
            full_path = _resolve_project_path(path)
        except ValueError as e:
            return {"success": False, "error": str(e), "snippet": ""}
        logger.info("PlanExecutor step %d: list_files '%s'", step_num, path)
        try:
            if not os.path.isdir(full_path):
                return {"success": False, "error": f"Not a directory: {path}", "snippet": ""}
            entries = []
            for entry in sorted(os.listdir(full_path)):
                entry_path = os.path.join(full_path, entry)
                if os.path.isdir(entry_path):
                    entries.append(f"  {entry}/")
                else:
                    size = os.path.getsize(entry_path)
                    entries.append(f"  {entry} ({size} bytes)")
            listing = "\n".join(entries) if entries else "(empty directory)"
            return {"success": True, "snippet": listing[:800], "full_listing": listing}
        except Exception as e:
            logger.error("PlanExecutor list_files error: %s", e)
            return {"success": False, "error": str(e), "snippet": f"Error: {e}"}

    # -- Self-improvement actions ------------------------------------------

    def _do_write_source(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """Write or overwrite a source file with git checkpoint + backup + syntax validation.

        Safety mechanisms:
        1. Protected files (plan_executor, safety_controller, etc.) cannot be modified.
        2. Approval-required paths (src/) need explicit user approval via Discord.
        3. Git checkpoint created before modification (enables ``git revert``).
        4. Existing files are backed up to data/source_backups/ before modification.
        5. Python files are syntax-checked after writing; if invalid, the backup is restored
           and git rolls back to the checkpoint.
        """
        path = (parsed.get("path") or "").strip()
        content = parsed.get("content", "")
        if not path:
            return {"success": False, "error": "No file path"}

        # Validate path and check protection
        try:
            _check_protected(path)
            full_path = _resolve_project_path(path)
        except ValueError as e:
            return {"success": False, "error": str(e)}

        # Approval gate: source code modifications require user permission
        if _requires_approval(path):
            # Check for deferred (pre-)approval first — the user may have
            # retroactively approved this path after a previous timeout.
            if _check_pre_approved(path):
                logger.info("write_source PRE-APPROVED (deferred): %s", path)
            elif self._approval_callback:
                try:
                    approved = self._approval_callback(
                        "write_source", path, self._task_description or "",
                    )
                except Exception as e:
                    logger.warning("Approval callback failed for %s: %s", path, e)
                    approved = False
                if not approved:
                    logger.info("write_source DENIED by user: %s", path)
                    self._source_write_denied = True  # Block further attempts this task
                    return {
                        "success": False,
                        "error": (
                            f"Source modification denied by user: {path}. "
                            f"write_source and edit_file are now blocked for this task. "
                            f"Use create_file to write to workspace/ instead."
                        ),
                    }
                logger.info("write_source APPROVED by user: %s", path)
            else:
                # No approval channel available — deny by default (safe for dream mode)
                logger.info(
                    "write_source BLOCKED (no approval channel): %s", path,
                )
                self._source_write_denied = True  # Block further attempts this task
                return {
                    "success": False,
                    "error": (
                        f"Source modification to {path} requires user approval, "
                        f"but no approval channel is available. "
                        f"write_source and edit_file are now blocked for this task. "
                        f"Use create_file to write to workspace/ instead."
                    ),
                }

        logger.info("PlanExecutor step %d: write_source '%s' (%d chars)", step_num, path, len(content))

        # Git checkpoint before modification
        git_tag = pre_modify_checkpoint("write_source", path)

        # Back up existing file
        backup_path = _backup_file(full_path)
        if backup_path:
            logger.info("Backed up %s -> %s", path, os.path.basename(backup_path))

        # Write the new content
        try:
            Path(full_path).parent.mkdir(parents=True, exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            rollback_last(git_tag)
            return {"success": False, "error": f"Write failed: {e}"}

        # Syntax check for Python files
        error = _syntax_check(full_path)
        if error:
            # Restore from backup on syntax error
            if backup_path:
                shutil.copy2(backup_path, full_path)
                logger.warning("Syntax error in %s, restored from backup", path)
            else:
                os.remove(full_path)
                logger.warning("Syntax error in %s, removed (no backup)", path)
            rollback_last(git_tag)
            return {"success": False, "error": f"Syntax error (rolled back): {error}"}

        # Commit the successful modification
        post_modify_commit(git_tag, path, f"write_source: {path}")
        return {"success": True, "path": full_path, "backed_up": backup_path is not None, "git_checkpoint": git_tag}

    def _do_edit_file(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """Surgical find-and-replace within a file.

        Much safer and cheaper than write_source for modifying existing files:
        - The "find" string must match exactly once (unless replace_all=True)
        - Approval-required paths (src/) need explicit user approval via Discord
        - Git checkpoint before modification (enables ``git revert``)
        - Automatic backup before modification
        - Python files syntax-checked after edit; rolled back on error
        - Protected files cannot be edited

        Usage:
            {"action": "edit_file", "path": "src/foo.py", "find": "old code", "replace": "new code"}
            {"action": "edit_file", "path": "src/foo.py", "find": "old_name", "replace": "new_name", "replace_all": true}
        """
        path = (parsed.get("path") or "").strip()
        find_str = parsed.get("find", "")
        replace_str = parsed.get("replace", "")
        replace_all = parsed.get("replace_all", False)

        if not path:
            return {"success": False, "error": "No file path"}
        if not find_str:
            return {"success": False, "error": "No 'find' string provided"}

        # Validate path and check protection
        try:
            _check_protected(path)
            full_path = _resolve_project_path(path)
        except ValueError as e:
            return {"success": False, "error": str(e)}

        # Approval gate: source code modifications require user permission
        if _requires_approval(path):
            # Check for deferred (pre-)approval first
            if _check_pre_approved(path):
                logger.info("edit_file PRE-APPROVED (deferred): %s", path)
            elif self._approval_callback:
                try:
                    approved = self._approval_callback(
                        "edit_file", path, self._task_description or "",
                    )
                except Exception as e:
                    logger.warning("Approval callback failed for %s: %s", path, e)
                    approved = False
                if not approved:
                    logger.info("edit_file DENIED by user: %s", path)
                    self._source_write_denied = True  # Block further attempts this task
                    return {
                        "success": False,
                        "error": (
                            f"Source modification denied by user: {path}. "
                            f"write_source and edit_file are now blocked for this task. "
                            f"Use create_file to write to workspace/ instead."
                        ),
                    }
                logger.info("edit_file APPROVED by user: %s", path)
            else:
                logger.info("edit_file BLOCKED (no approval channel): %s", path)
                self._source_write_denied = True  # Block further attempts this task
                return {
                    "success": False,
                    "error": (
                        f"Source modification to {path} requires user approval, "
                        f"but no approval channel is available. "
                        f"write_source and edit_file are now blocked for this task. "
                        f"Use create_file to write to workspace/ instead."
                    ),
                }

        if not os.path.isfile(full_path):
            return {"success": False, "error": f"File not found: {path}"}

        logger.info(
            "PlanExecutor step %d: edit_file '%s' (find=%d chars, replace=%d chars, replace_all=%s)",
            step_num, path, len(find_str), len(replace_str), replace_all,
        )

        # Read current content
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"success": False, "error": f"Read failed: {e}"}

        # Check find string exists
        count = content.count(find_str)
        if count == 0:
            # Provide helpful context: show similar lines
            find_lower = find_str.strip().lower()
            similar = [
                line.strip()[:80]
                for line in content.splitlines()
                if find_lower[:20] in line.lower()
            ][:3]
            hint = ""
            if similar:
                hint = f" Similar lines found: {similar}"
            return {
                "success": False,
                "error": f"'find' string not found in {path}.{hint}",
            }

        if count > 1 and not replace_all:
            return {
                "success": False,
                "error": (
                    f"'find' string matches {count} times in {path}. "
                    "Use replace_all: true for multiple replacements, "
                    "or provide a more specific find string."
                ),
            }

        # Git checkpoint before modification
        git_tag = pre_modify_checkpoint("edit_file", path)

        # Back up existing file
        backup_path = _backup_file(full_path)
        if backup_path:
            logger.info("Backed up %s -> %s", path, os.path.basename(backup_path))

        # Do the replacement
        if replace_all:
            new_content = content.replace(find_str, replace_str)
        else:
            new_content = content.replace(find_str, replace_str, 1)

        # Write the modified content
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            rollback_last(git_tag)
            return {"success": False, "error": f"Write failed: {e}"}

        # Syntax check for Python files
        error = _syntax_check(full_path)
        if error:
            # Restore from backup on syntax error
            if backup_path:
                shutil.copy2(backup_path, full_path)
                logger.warning("Syntax error in %s after edit, restored from backup", path)
            else:
                # Restore original content
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.warning("Syntax error in %s after edit, restored original", path)
            rollback_last(git_tag)
            return {"success": False, "error": f"Syntax error after edit (rolled back): {error}"}

        # Commit the successful modification
        post_modify_commit(git_tag, path, f"edit_file: {path}")
        replacements = count if replace_all else 1
        return {
            "success": True,
            "path": full_path,
            "backed_up": backup_path is not None,
            "replacements": replacements,
            "git_checkpoint": git_tag,
        }

    def _do_run_command(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """Run a shell command and capture output.

        For running tests, installing packages, git operations, etc.
        60-second timeout. Dangerous commands are blocked.

        Usage:
            {"action": "run_command", "command": "pytest tests/ -v"}
            {"action": "run_command", "command": "pip install requests"}
            {"action": "run_command", "command": "git status"}
        """
        command = (parsed.get("command") or "").strip()
        if not command:
            return {"success": False, "error": "No command provided", "output": "", "snippet": ""}

        # Safety: check for blocked commands
        cmd_lower = command.lower()
        for blocked in _BLOCKED_COMMANDS:
            if blocked.lower() in cmd_lower:
                logger.warning(
                    "PlanExecutor step %d: BLOCKED dangerous command: %s",
                    step_num, command[:80],
                )
                return {
                    "success": False,
                    "error": f"Command blocked for safety: contains '{blocked}'",
                    "output": "",
                    "snippet": "blocked",
                }

        logger.info("PlanExecutor step %d: run_command '%s'", step_num, command[:100])

        try:
            from src.utils.paths import base_path

            # Determine shell: PowerShell on Windows, bash on Linux/Mac
            is_windows = sys.platform == "win32"
            if is_windows:
                # Use PowerShell for Windows
                result = subprocess.run(
                    ["powershell", "-Command", command],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=base_path(),
                )
            else:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=base_path(),
                )

            output = result.stdout[:2000]
            errors = result.stderr[:1000]
            combined = output
            if errors:
                combined += f"\n[stderr]: {errors}"

            if result.returncode == 0:
                return {
                    "success": True,
                    "output": combined,
                    "exit_code": 0,
                    "snippet": combined[:400] if combined else "(no output)",
                }
            else:
                return {
                    "success": False,
                    "error": f"Exit code {result.returncode}",
                    "output": combined,
                    "exit_code": result.returncode,
                    "snippet": combined[:400] if combined else f"Exit code {result.returncode}",
                }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Command timed out (60s limit)",
                "output": "",
                "snippet": "timeout",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "output": "",
                "snippet": f"Error: {e}",
            }

    def _do_ask_user(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """Ask Jesse a question via Discord and wait for his reply.

        Time-aware: returns a fallback if it's quiet hours so the model can
        use its best judgment instead of failing.

        Usage:
            {"action": "ask_user", "question": "Which approach should I use?"}
        """
        question = (parsed.get("question") or "").strip()
        if not question:
            return {"success": False, "error": "No question provided", "snippet": ""}

        logger.info("PlanExecutor step %d: ask_user '%s'", step_num, question[:80])

        try:
            from src.interfaces.discord_bot import ask_user
            reply = ask_user(question=question, timeout=300)

            if reply is not None:
                return {
                    "success": True,
                    "response": reply,
                    "snippet": f"Jesse replied: {reply[:200]}",
                }
            else:
                # Quiet hours or timeout — tell the model to use best judgment
                return {
                    "success": False,
                    "error": (
                        "Jesse didn't respond (may be asleep or busy). "
                        "Use your best judgment and move on."
                    ),
                    "response": None,
                    "snippet": "No response — use best judgment",
                }
        except Exception as e:
            logger.error("PlanExecutor ask_user error: %s", e)
            return {
                "success": False,
                "error": f"ask_user failed: {e}",
                "response": None,
                "snippet": f"Error: {e}",
            }

    def _do_run_python(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """Run a Python snippet and capture output. For testing code changes.

        Runs in the project directory so Archi's imports work.
        30-second timeout. Output capped at 1000 chars.
        """
        code = (parsed.get("code") or "").strip()
        if not code:
            return {"success": False, "error": "No code provided", "output": "", "snippet": ""}

        logger.info("PlanExecutor step %d: run_python (%d chars)", step_num, len(code))

        try:
            from src.utils.paths import base_path
            root = base_path()
            workspace = os.path.join(root, "workspace")
            os.makedirs(workspace, exist_ok=True)
            # Force UTF-8 encoding for subprocess — Windows defaults to
            # cp1252 which crashes on non-ASCII chars in project files.
            # cwd is workspace/ so relative paths stay sandboxed there.
            # Project root is on PYTHONPATH so `import src.*` still works.
            pythonpath = os.pathsep.join(
                filter(None, [root, os.environ.get("PYTHONPATH", "")])
            )
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": pythonpath}
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=workspace,
                env=env,
            )
            output = result.stdout[:1000]
            errors = result.stderr[:500]

            if result.returncode == 0:
                return {
                    "success": True,
                    "output": output,
                    "snippet": output[:300] if output else "(no output)",
                }
            else:
                return {
                    "success": False,
                    "error": f"Exit code {result.returncode}: {errors}",
                    "output": output,
                    "snippet": errors[:300],
                }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Execution timed out (30s limit)", "output": "", "snippet": "timeout"}
        except Exception as e:
            return {"success": False, "error": str(e), "output": "", "snippet": f"Error: {e}"}

    # -- Self-verification -------------------------------------------------

    def _verify_work(
        self,
        task_description: str,
        goal_context: str,
        steps_taken: List[Dict[str, Any]],
        files_created: List[str],
    ) -> Dict[str, Any]:
        """Verify the quality of completed work by reading back created files.

        Asks the model to rate the output quality on a 1-10 scale.
        Returns dict with 'passed' (bool) and 'cost' (float).
        Verification passes if quality >= 6/10.
        """
        if not files_created:
            return {"passed": True, "cost": 0.0}

        logger.info("PlanExecutor: verifying %d created file(s)", len(files_created))

        # Read back the files (limit to 3 to control cost)
        file_contents: Dict[str, str] = {}
        for fpath in files_created[:3]:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                file_contents[os.path.basename(fpath)] = content[:2000]
            except Exception:
                pass

        if not file_contents:
            return {"passed": True, "cost": 0.0}

        files_block = ""
        for name, content in file_contents.items():
            files_block += f"\n--- {name} ---\n{content}\n"

        prompt = f"""You are reviewing work done by an autonomous AI agent.

Task: {task_description}
Goal: {goal_context}

Files produced:
{files_block}

Rate the quality of this work on a scale of 1-10 where:
1-3 = Poor (generic filler, no specific data, placeholder text)
4-5 = Below average (some useful info but thin or has gaps)
6-7 = Good (substantive, specific, actionable information)
8-10 = Excellent (comprehensive, well-organized, highly actionable)

Return ONLY a JSON object:
{{"quality": <1-10>, "issues": "brief description of problems if any", "strengths": "what was done well"}}"""

        cost = 0.0
        try:
            resp = self._router.generate(
                prompt=prompt, max_tokens=300, temperature=0.2,
            )
            cost = resp.get("cost_usd", 0)
            parsed = _extract_json(resp.get("text", ""))
            if parsed:
                quality = parsed.get("quality", 5)
                issues = parsed.get("issues", "")
                strengths = parsed.get("strengths", "")
                logger.info(
                    "PlanExecutor verification: quality=%d/10, issues='%s', strengths='%s'",
                    quality, issues[:100], strengths[:100],
                )
                return {"passed": quality >= 6, "cost": cost, "quality": quality}
            return {"passed": True, "cost": cost}
        except Exception as e:
            logger.warning("Verification failed: %s", e)
            return {"passed": True, "cost": cost}

    # -- Crash recovery ----------------------------------------------------

    def _save_state(
        self,
        task_description: str,
        goal_context: str,
        steps_taken: List[Dict[str, Any]],
        total_cost: float,
        files_created: List[str],
    ) -> None:
        """Persist current execution state for crash recovery."""
        if not self._task_id:
            return
        try:
            state = {
                "task_id": self._task_id,
                "task_description": task_description,
                "goal_context": goal_context,
                "steps_taken": steps_taken,
                "total_cost": total_cost,
                "files_created": files_created,
                "saved_at": datetime.now().isoformat(),
            }
            path = _state_dir() / f"{self._task_id}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.debug("State save failed (non-critical): %s", e)

    def _load_state(self) -> Optional[Dict[str, Any]]:
        """Load interrupted execution state if it exists and isn't stale."""
        if not self._task_id:
            return None
        try:
            path = _state_dir() / f"{self._task_id}.json"
            if not path.exists():
                return None
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            # Check staleness
            saved_at = state.get("saved_at", "")
            if saved_at:
                saved_dt = datetime.fromisoformat(saved_at)
                age_hours = (datetime.now() - saved_dt).total_seconds() / 3600
                if age_hours > _STATE_MAX_AGE_HOURS:
                    logger.info(
                        "PlanExecutor: stale state for '%s' (%.1fh old), starting fresh",
                        self._task_id, age_hours,
                    )
                    path.unlink(missing_ok=True)
                    return None
            return state
        except Exception as e:
            logger.debug("State load failed: %s", e)
        return None

    def _clear_state(self) -> None:
        """Remove crash-recovery state after successful completion."""
        if not self._task_id:
            return
        try:
            path = _state_dir() / f"{self._task_id}.json"
            if path.exists():
                path.unlink()
        except Exception as e:
            logger.debug("State cleanup failed: %s", e)

    @classmethod
    def get_interrupted_tasks(cls) -> List[Dict[str, Any]]:
        """List any interrupted tasks that can be resumed.

        Returns list of dicts with task_id, description, steps_completed, saved_at.
        Only returns non-stale entries.
        """
        try:
            interrupted = []
            sd = _state_dir()
            for f in sd.glob("*.json"):
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        state = json.load(fh)
                    # Check staleness
                    saved_at = state.get("saved_at", "")
                    if saved_at:
                        saved_dt = datetime.fromisoformat(saved_at)
                        age_hours = (datetime.now() - saved_dt).total_seconds() / 3600
                        if age_hours > _STATE_MAX_AGE_HOURS:
                            continue
                    interrupted.append({
                        "task_id": state.get("task_id"),
                        "description": state.get("task_description", ""),
                        "steps_completed": len(state.get("steps_taken", [])),
                        "saved_at": saved_at,
                    })
                except Exception:
                    pass
            return interrupted
        except Exception:
            return []
