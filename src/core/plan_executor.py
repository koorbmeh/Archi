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
  Self-improvement (source code):
    - write_source: create/modify source files with backup + syntax check
    - run_python: execute Python snippets for testing
  Control:
    - think: internal reasoning note (no execution)
    - done: signal task completion with summary
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Safety limits
MAX_STEPS_PER_TASK = 20
PLAN_MAX_TOKENS = 1000
SUMMARY_MAX_TOKENS = 400

# Crash-recovery state older than this is treated as stale
_STATE_MAX_AGE_HOURS = 24

# Files Archi cannot modify (self-preservation).
# These protect the execution engine, safety infrastructure, and prime directive.
_PROTECTED_PATHS = frozenset({
    "src/core/plan_executor.py",
    "src/core/safety_controller.py",
    "config/prime_directive.txt",
})


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------

def _resolve_workspace_path(relative_path: str) -> str:
    """Resolve a workspace-relative path to a full path, enforcing workspace boundary."""
    from src.utils.paths import base_path
    rel = relative_path.lstrip("/").replace("\\", "/")
    if not rel.startswith("workspace/"):
        rel = "workspace/" + rel
    full = os.path.normpath(os.path.join(base_path(), rel.replace("/", os.sep)))
    workspace_root = os.path.normpath(os.path.join(base_path(), "workspace"))
    if not full.startswith(workspace_root + os.sep) and full != workspace_root:
        raise ValueError(f"Path escapes workspace: {relative_path}")
    return full


def _resolve_project_path(relative_path: str) -> str:
    """Resolve a project-relative path for reading or source modification.

    Allows access to any file within the project root (src/, config/, workspace/, etc.)
    but enforces:
    - Path must stay within the project root
    - Protected files cannot be written to (checked separately by write_source)
    """
    from src.utils.paths import base_path
    rel = relative_path.lstrip("/").replace("\\", "/")
    full = os.path.normpath(os.path.join(base_path(), rel.replace("/", os.sep)))
    project_root = os.path.normpath(base_path())
    if not full.startswith(project_root + os.sep) and full != project_root:
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
        with urllib.request.urlopen(req, timeout=15) as resp:
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


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract JSON from model response (handles <think> blocks and markdown)."""
    text = (text or "").strip()
    # Strip <think> reasoning blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if "<think>" in text:
        text = text.split("<think>")[0].strip()
    text = text.replace("</think>", "").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _state_dir() -> Path:
    """Directory for PlanExecutor crash-recovery state."""
    from src.utils.paths import base_path
    d = Path(base_path()) / "data" / "plan_state"
    d.mkdir(parents=True, exist_ok=True)
    return d


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

    def __init__(self, router: Any, tools: Optional[Any] = None):
        """
        Args:
            router: ModelRouter for generating next-step decisions
            tools: ToolRegistry instance (lazy-created if not provided)
        """
        self._router = router
        self._tools = tools
        self._task_id: Optional[str] = None

    @property
    def tools(self):
        if self._tools is None:
            from src.tools.tool_registry import ToolRegistry
            self._tools = ToolRegistry()
        return self._tools

    # -- Public API --------------------------------------------------------

    def execute(
        self,
        task_description: str,
        goal_context: str = "",
        max_steps: int = MAX_STEPS_PER_TASK,
        task_id: Optional[str] = None,
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

        Returns:
            dict with keys: success, steps_taken, total_steps, executed_steps,
            successful_steps, total_cost, duration_ms, verified, files_created.
        """
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

        start_step = len(steps_taken)

        for step_num in range(start_step, max_steps):
            # Ask model: "what's next?"
            prompt = self._build_step_prompt(
                task_description, goal_context, steps_taken,
            )
            resp = self._router.generate(
                prompt=prompt,
                max_tokens=PLAN_MAX_TOKENS,
                temperature=0.3,
                prefer_local=True,
            )
            total_cost += resp.get("cost_usd", 0)

            parsed = _extract_json(resp.get("text", ""))

            # Retry once on bad JSON
            if not parsed:
                logger.warning(
                    "PlanExecutor: invalid JSON at step %d, retrying", step_num + 1,
                )
                retry = self._router.generate(
                    prompt=prompt + "\n\nRespond with ONLY a valid JSON object.",
                    max_tokens=PLAN_MAX_TOKENS,
                    temperature=0.1,
                    prefer_local=True,
                )
                total_cost += retry.get("cost_usd", 0)
                parsed = _extract_json(retry.get("text", ""))
                if not parsed:
                    logger.warning("PlanExecutor: JSON retry failed, stopping")
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
                logger.info("PlanExecutor step %d: think — %s", step_num + 1, note[:120])
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
            steps_taken.append({
                "step": step_num + 1,
                "action": action_type,
                "params": {k: v for k, v in parsed.items() if k != "action"},
                **result,
            })

            # Track files for verification
            if action_type in ("create_file", "append_file", "write_source") and result.get("success"):
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

        return {
            "success": len(successful) > 0,
            "steps_taken": steps_taken,
            "total_steps": len(steps_taken),
            "executed_steps": len(executed),
            "successful_steps": len(successful),
            "total_cost": total_cost,
            "duration_ms": duration_ms,
            "verified": verified,
            "files_created": files_created,
        }

    # -- Prompt building ---------------------------------------------------

    def _build_step_prompt(
        self,
        task_description: str,
        goal_context: str,
        steps_taken: List[Dict[str, Any]],
    ) -> str:
        """Build the prompt asking the model for the next step."""
        # Summarize prior steps
        history_lines = []
        for s in steps_taken:
            act = s.get("action", "?")
            n = s["step"]
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
                snip = s.get("snippet", "")[:300]
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
            elif act == "run_python":
                snip = s.get("snippet", "")[:200]
                ok = "ok" if s.get("success") else "error"
                history_lines.append(f"  {n}. [run_python] -> {ok}: {snip}")
            else:
                history_lines.append(f"  {n}. [{act}] -> {s.get('error', 'done')}")

        history_block = ""
        if history_lines:
            history_block = "\n\nSteps completed so far:\n" + "\n".join(history_lines)

        goal_block = f"\nGoal: {goal_context}" if goal_context else ""

        return f"""You are Archi, an autonomous AI agent working on a task for Jesse.
{goal_block}
Task: {task_description}
{history_block}

What is the NEXT step? Choose ONE action:

RESEARCH:
- {{"action": "web_search", "query": "specific search query"}}
  Search DuckDuckGo for information. Use multiple searches to go deep.

- {{"action": "fetch_webpage", "url": "https://example.com/article"}}
  Fetch and read the full text content of a web page. Use this after
  web_search to read promising results in detail.

WORKSPACE FILES (reports, research output):
- {{"action": "create_file", "path": "workspace/path/file.ext", "content": "file content"}}
  Save research, reports, code, or any output. Path must start with workspace/.

- {{"action": "append_file", "path": "workspace/path/file.ext", "content": "content to add"}}
  Add content to an existing file. Great for building reports section by section.

FILE READING (project-wide):
- {{"action": "read_file", "path": "src/tools/some_file.py"}}
  Read any file in the project. Use to study existing code before improving it.

- {{"action": "list_files", "path": "src/tools/"}}
  List files in any project directory. Discover what exists.

SELF-IMPROVEMENT (source code):
- {{"action": "write_source", "path": "src/tools/new_tool.py", "content": "python code"}}
  Create or modify source code. Automatic backup + syntax validation.
  Some core files (plan_executor, safety_controller, prime_directive) are protected.

- {{"action": "run_python", "code": "print('hello world')"}}
  Run a Python snippet to test code. 30 second timeout. Output captured.

CONTROL:
- {{"action": "think", "note": "reasoning about approach"}}
  Plan or reason before acting.

- {{"action": "done", "summary": "clear description of what was accomplished"}}
  Signal task completion. Include a meaningful summary.

Rules:
- Be specific and actionable.
- For research: search -> fetch_webpage on promising URLs -> save findings to file.
- Do MULTIPLE searches and reads for comprehensive information.
- Keep file content substantive with specific data, numbers, and actionable details.
- If a step fails, adapt and try differently.
- For self-improvement: read existing code first, then write_source, then run_python to test.
- VERIFY your work: read back files and test code before calling done.

Respond with ONLY a valid JSON object."""

    # -- Action execution --------------------------------------------------

    def _execute_action(
        self, parsed: Dict[str, Any], step_num: int,
    ) -> Dict[str, Any]:
        """Route and execute a single action."""
        action = parsed.get("action", "")
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
            return self._do_write_source(parsed, step_num)
        if action == "run_python":
            return self._do_run_python(parsed, step_num)
        logger.warning("PlanExecutor: unknown action '%s' at step %d", action, step_num)
        return {"success": False, "error": f"Unknown action: {action}"}

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
        """Append content to an existing file (creates if it doesn't exist)."""
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
            return {"success": True, "snippet": content[:800], "full_content": content}
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
        """Write or overwrite a source file with automatic backup + syntax validation.

        Safety mechanisms:
        1. Protected files (plan_executor, safety_controller, etc.) cannot be modified.
        2. Existing files are backed up to data/source_backups/ before modification.
        3. Python files are syntax-checked after writing; if invalid, the backup is restored.
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

        logger.info("PlanExecutor step %d: write_source '%s' (%d chars)", step_num, path, len(content))

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
            return {"success": False, "error": f"Syntax error (rolled back): {error}"}

        return {"success": True, "path": full_path, "backed_up": backup_path is not None}

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
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=base_path(),
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
                prompt=prompt, max_tokens=300, temperature=0.2, prefer_local=True,
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
