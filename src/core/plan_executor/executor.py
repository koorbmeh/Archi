"""
PlanExecutor — Multi-step autonomous task execution engine.

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

Extracted from plan_executor.py (session 73) for SRP compliance.
The action handlers live in actions.py, safety in safety.py,
crash recovery in recovery.py, and web helpers in web.py.
"""

import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

from src.utils.parsing import extract_json as _extract_json

from .actions import ActionMixin
from .recovery import check_and_clear_cancellation, clear_state, load_state, save_state
from .safety import _classify_error

logger = logging.getLogger(__name__)

# Safety limits
MAX_STEPS_PER_TASK = 50
MAX_STEPS_CODING = 25
MAX_STEPS_CHAT = 12
PLAN_MAX_TOKENS = 4096
SUMMARY_MAX_TOKENS = 400


def _estimate_total_steps(steps_taken: List[Dict], max_steps: int) -> int:
    """Estimate how many total steps this task will likely need.

    Uses a simple heuristic based on the actions taken so far.
    Returns an estimated total step count (not remaining).
    """
    n = len(steps_taken)
    if n < 2:
        return max_steps

    actions = [s.get("action", "") for s in steps_taken]
    researching = sum(1 for a in actions if a in ("web_search", "research", "fetch_webpage"))
    writing = sum(1 for a in actions if a in ("create_file", "append_file", "write_source", "edit_file"))
    thinking = sum(1 for a in actions if a == "think")

    if writing > 0:
        estimate = n + 2
    elif researching > 0:
        remaining_research = max(0, 3 - researching)
        estimate = n + remaining_research + 3
    elif thinking > 0:
        estimate = n + 5
    else:
        estimate = max_steps

    return max(n + 1, min(estimate, max_steps))


class PlanExecutor(ActionMixin):
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
            task_id="task_42",
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
        self._router = router
        self._tools = tools
        self._learning_system = learning_system
        self._hints = hints or []
        self._approval_callback = approval_callback
        self._task_id: Optional[str] = None
        self._task_description: Optional[str] = None
        self._source_write_denied = False

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
        """Execute a task through multi-step reasoning."""
        self._conversation_history = conversation_history
        self._progress_callback = progress_callback
        self._task_description = task_description
        self._source_write_denied = False
        self._schema_retries_exhausted = False
        t0 = time.monotonic()
        steps_taken: List[Dict[str, Any]] = []
        total_cost = 0.0
        files_created: List[str] = []

        # Crash recovery: set task_id and check for interrupted state
        self._task_id = task_id or f"plan_{int(time.time())}_{id(self)}"

        resumed = load_state(self._task_id)
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

            resp = self._router.generate(
                prompt=prompt,
                max_tokens=PLAN_MAX_TOKENS,
                temperature=0.3,
                classify_hint="plan_step",
            )
            total_cost += resp.get("cost_usd", 0)

            parsed = _extract_json(resp.get("text", ""))

            # Structured output validation with retry
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
                        break
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
                # Exhausted retries — one last attempt with Claude
                if not parsed:
                    try:
                        with self._router.escalate_for_task("claude-sonnet-4.6") as _esc:
                            if _esc.get("model"):
                                logger.info("PlanExecutor: escalating schema retry to Claude")
                                _claude_resp = self._router.generate(
                                    prompt=prompt + "\n\nRespond with ONLY a valid JSON object.",
                                    max_tokens=PLAN_MAX_TOKENS,
                                    temperature=0.1,
                                )
                                total_cost += _claude_resp.get("cost_usd", 0)
                                parsed = _extract_json(_claude_resp.get("text", ""))
                    except Exception:
                        logger.warning("PlanExecutor: Claude escalation failed", exc_info=True)
                    if not parsed:
                        logger.warning("PlanExecutor: JSON/schema retries exhausted, stopping")
                        self._schema_retries_exhausted = True
                        break

            action_type = parsed.get("action", "")

            # -- Task complete --
            if action_type == "done":
                summary = parsed.get("summary", "Task completed.")
                logger.info("PlanExecutor: done after %d steps — %s", step_num + 1, summary[:120])
                steps_taken.append({"step": step_num + 1, "action": "done", "summary": summary})
                break

            # -- Internal reasoning --
            if action_type == "think":
                note = parsed.get("note", "")
                logger.info("PlanExecutor step %d: think — %s", step_num + 1, note[:120])
                steps_taken.append({"step": step_num + 1, "action": "think", "note": note, "success": True})
                save_state(
                    self._task_id, task_description, goal_context,
                    steps_taken, total_cost, files_created,
                )
                continue

            # -- Execute an action --
            result = self._execute_action(parsed, step_num + 1)

            # Mechanical Error Recovery
            if not result.get("success", False):
                err_class, err_hint = _classify_error(
                    action_type, result.get("error", ""),
                )
                if err_class == "transient":
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
                    result["error_hint"] = err_hint

            steps_taken.append({
                "step": step_num + 1,
                "action": action_type,
                "params": {k: v for k, v in parsed.items() if k != "action"},
                **result,
            })

            # Send progress update to caller
            if self._progress_callback:
                try:
                    progress_msg = self._describe_step(action_type, parsed, result)
                    estimated_total = _estimate_total_steps(steps_taken, max_steps)
                    self._progress_callback(step_num + 1, estimated_total, progress_msg)
                except Exception:
                    pass

            # Record action outcome for learning
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
            save_state(
                self._task_id, task_description, goal_context,
                steps_taken, total_cost, files_created,
            )

            if not result.get("success", False):
                logger.warning(
                    "PlanExecutor step %d failed (%s): %s",
                    step_num + 1, action_type, result.get("error", ""),
                )

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
        clear_state(self._task_id)

        _has_successful_steps = len(successful) > 0
        _verification_ok = verified or not files_created
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
        """Generate a short, human-readable description of a completed step."""
        ok = result.get("success", False)
        if action_type == "web_search":
            query = (parsed.get("query") or "")[:50]
            return f"Searching: {query}..." if ok else f"Search failed: {query}"
        if action_type == "fetch_webpage":
            url = (parsed.get("url") or "")
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
        """Compress a step to a one-liner for context compression."""
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
        full fidelity (snippets, file contents, etc.).
        """
        _FULL_FIDELITY_WINDOW = 5
        _COMPRESS_AFTER = 8
        compress = len(steps_taken) > _COMPRESS_AFTER

        history_lines = []
        for idx, s in enumerate(steps_taken):
            act = s.get("action", "?")
            n = s["step"]
            is_old = compress and idx < len(steps_taken) - _FULL_FIDELITY_WINDOW
            if is_old:
                history_lines.append(self._compress_step(s))
                continue
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
            elif act == "ask_user":
                q = s.get("params", {}).get("question", "")[:100]
                resp = s.get("response")
                if resp:
                    history_lines.append(f'  {n}. [ask_user "{q}"] -> Jesse replied: "{resp[:200]}"')
                else:
                    history_lines.append(f'  {n}. [ask_user "{q}"] -> {s.get("error", "no response")}')
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
                # Cap at 10 most recent to avoid prompt bloat
                _sorted = sorted(failed_domains)
                if len(_sorted) > 10:
                    _sorted = _sorted[:10]
                domains_str = ", ".join(_sorted)
                warnings.append(f"BLOCKED DOMAINS (do NOT fetch again): {domains_str}")
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
                history_block += "\n\n\u26a0\ufe0f " + "\n\u26a0\ufe0f ".join(warnings)

            # Inject error recovery hints from the most recent failed step
            if steps_taken:
                _last_hint = steps_taken[-1].get("error_hint", "")
                if _last_hint:
                    history_block += f"\n\n\U0001f4a1 FIX HINT: {_last_hint}"

        goal_block = f"\nGoal: {goal_context}" if goal_context else ""

        conv_block = ""
        if getattr(self, "_conversation_history", ""):
            conv_block = f"\n\nRecent conversation with user:\n{self._conversation_history}\n---"

        hints_block = ""
        if self._hints:
            hints_block = "\n\nHints from past work:\n" + "\n".join(
                f"- {h}" for h in self._hints[:5]
            )

        remaining = max_steps - step_num
        budget_block = f"\n\n\u23f1 STEP BUDGET: Step {step_num + 1} of {max_steps} ({remaining} remaining)."
        if remaining <= 3:
            budget_block += (
                "\n\u26a0\ufe0f LOW BUDGET: You are running out of steps. "
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

    # -- Self-verification -------------------------------------------------

    def _verify_work(
        self,
        task_description: str,
        goal_context: str,
        steps_taken: List[Dict[str, Any]],
        files_created: List[str],
    ) -> Dict[str, Any]:
        """Verify the quality of completed work by reading back created files."""
        if not files_created:
            return {"passed": True, "cost": 0.0}

        logger.info("PlanExecutor: verifying %d created file(s)", len(files_created))

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
            resp = self._router.generate(prompt=prompt, max_tokens=300, temperature=0.2)
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

    # -- Crash recovery (class-level) --------------------------------------

    @classmethod
    def get_interrupted_tasks(cls) -> List[Dict[str, Any]]:
        """List any interrupted tasks that can be resumed."""
        from .recovery import get_interrupted_tasks
        return get_interrupted_tasks()
