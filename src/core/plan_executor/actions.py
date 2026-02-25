"""
Action handlers for the PlanExecutor.

Each _do_* method handles one action type (web_search, create_file, etc.).
_execute_action() dispatches to the correct handler.

Extracted from plan_executor.py (session 73) for SRP compliance.
"""

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.utils.config import get_user_name
from src.utils.git_safety import post_modify_commit, pre_modify_checkpoint, rollback_last

from .safety import (
    _backup_file,
    _check_pre_approved,
    _check_protected,
    _get_safety,
    _requires_approval,
    _resolve_project_path,
    _resolve_workspace_path,
    _syntax_check,
)
from .web import _fetch_url_text

logger = logging.getLogger(__name__)


class ActionMixin:
    """Mixin providing all action handler methods for PlanExecutor.

    Expects the host class to provide:
        self._tools (or self.tools property)
        self._approval_callback
        self._task_description
        self._source_write_denied
        self._step_history
    """

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
        # Overwrite warning: if file already exists, note it in the result so
        # the rewrite-loop detector in executor.py can track accumulated writes.
        _overwriting = os.path.isfile(full_path)
        if _overwriting:
            logger.info(
                "PlanExecutor step %d: create_file OVERWRITING existing '%s' (%d chars)",
                step_num, path, len(content),
            )
        else:
            logger.info("PlanExecutor step %d: create_file '%s' (%d chars)", step_num, path, len(content))
        try:
            result = self.tools.execute("create_file", {"path": full_path, "content": content})
            if result.get("success"):
                _result = {"success": True, "path": full_path}
                if _overwriting:
                    _result["overwritten"] = True
                return _result
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
        """Write or overwrite a source file with git checkpoint + backup + syntax validation."""
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
                    self._source_write_denied = True
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
                logger.info("write_source BLOCKED (no approval channel): %s", path)
                self._source_write_denied = True
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
            if backup_path:
                shutil.copy2(backup_path, full_path)
                logger.warning("Syntax error in %s, restored from backup", path)
            else:
                os.remove(full_path)
                logger.warning("Syntax error in %s, removed (no backup)", path)
            rollback_last(git_tag)
            return {"success": False, "error": f"Syntax error (rolled back): {error}"}

        post_modify_commit(git_tag, path, f"write_source: {path}")
        return {"success": True, "path": full_path, "backed_up": backup_path is not None, "git_checkpoint": git_tag}

    def _do_edit_file(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """Surgical find-and-replace within a file."""
        path = (parsed.get("path") or "").strip()
        find_str = parsed.get("find", "")
        replace_str = parsed.get("replace", "")
        replace_all = parsed.get("replace_all", False)

        if not path:
            return {"success": False, "error": "No file path"}
        if not find_str:
            return {"success": False, "error": "No 'find' string provided"}

        # Guard: find string must be long enough to be unambiguous.
        # Short find strings (like "    exercise") match at wrong positions and
        # produce mangled code.  Require either 30+ chars or a newline (multi-line).
        _MIN_FIND_LEN = 30
        if len(find_str) < _MIN_FIND_LEN and "\n" not in find_str:
            return {
                "success": False,
                "error": (
                    f"'find' string is too short ({len(find_str)} chars). "
                    f"Use at least {_MIN_FIND_LEN} characters or include surrounding lines "
                    f"for context to avoid matching the wrong location. "
                    f"Copy a larger block from the read_file output."
                ),
            }

        # Validate path and check protection
        try:
            _check_protected(path)
            full_path = _resolve_project_path(path)
        except ValueError as e:
            return {"success": False, "error": str(e)}

        # Approval gate
        if _requires_approval(path):
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
                    self._source_write_denied = True
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
                self._source_write_denied = True
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
            find_lower = find_str.strip().lower()
            similar = [
                line.strip()[:80]
                for line in content.splitlines()
                if find_lower[:20] in line.lower()
            ][:3]
            hint = ""
            if similar:
                hint = f" Similar lines found: {similar}"
            return {"success": False, "error": f"'find' string not found in {path}.{hint}"}

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
            # Capture the broken region before restoring, so the model can see
            # exactly what its edit produced (helps it avoid repeating the mistake).
            _broken_context = ""
            try:
                _err_lines = error.splitlines()
                _lineno = None
                for _el in _err_lines:
                    if "line " in _el.lower():
                        import re
                        _m = re.search(r"line (\d+)", _el)
                        if _m:
                            _lineno = int(_m.group(1))
                            break
                if _lineno:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as _f:
                        _all_lines = _f.readlines()
                    _start = max(0, _lineno - 3)
                    _end = min(len(_all_lines), _lineno + 2)
                    _broken_context = "".join(
                        f"  {'>>>' if i + 1 == _lineno else '   '} {i + 1}: {_all_lines[i]}"
                        for i in range(_start, _end)
                    )
            except Exception:
                pass
            if backup_path:
                shutil.copy2(backup_path, full_path)
                logger.warning("Syntax error in %s after edit, restored from backup", path)
            else:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.warning("Syntax error in %s after edit, restored original", path)
            rollback_last(git_tag)
            _err_msg = f"Syntax error after edit (rolled back): {error}"
            if _broken_context:
                _err_msg += f"\n\nBroken code around the error:\n{_broken_context}"
            return {"success": False, "error": _err_msg}

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
        """Run a shell command and capture output. 60-second timeout."""
        command = (parsed.get("command") or "").strip()
        if not command:
            return {"success": False, "error": "No command provided", "output": "", "snippet": ""}

        # Safety layer 1: allowlist — parse command and check first token
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        if not tokens:
            return {"success": False, "error": "No command provided", "output": "", "snippet": ""}
        cmd_name = os.path.basename(tokens[0]).lower()
        # Strip .exe/.cmd/.bat suffix on Windows
        for suffix in (".exe", ".cmd", ".bat"):
            if cmd_name.endswith(suffix):
                cmd_name = cmd_name[: -len(suffix)]
        allowed = _get_safety("allowed_commands")
        if cmd_name not in allowed:
            logger.warning(
                "PlanExecutor step %d: BLOCKED command not on allowlist: %s (parsed: %s)",
                step_num, command[:80], cmd_name,
            )
            return {
                "success": False,
                "error": f"Command '{cmd_name}' is not on the allowed commands list. "
                         f"Allowed: {', '.join(sorted(allowed))}",
                "output": "",
                "snippet": "blocked",
            }

        # Safety layer 2: blocklist — defense-in-depth substring check
        cmd_lower = command.lower()
        for blocked in _get_safety("blocked_commands"):
            if blocked.lower() in cmd_lower:
                logger.warning(
                    "PlanExecutor step %d: BLOCKED dangerous command pattern: %s",
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

            is_windows = sys.platform == "win32"
            if is_windows:
                result = subprocess.run(
                    ["powershell", "-Command", command],
                    capture_output=True, text=True, timeout=60, cwd=base_path(),
                )
            else:
                result = subprocess.run(
                    command, shell=True,
                    capture_output=True, text=True, timeout=60, cwd=base_path(),
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
            return {"success": False, "error": "Command timed out (60s limit)", "output": "", "snippet": "timeout"}
        except Exception as e:
            return {"success": False, "error": str(e), "output": "", "snippet": f"Error: {e}"}

    def _do_ask_user(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """Ask the user a question via Discord and wait for their reply."""
        question = (parsed.get("question") or "").strip()
        if not question:
            return {"success": False, "error": "No question provided", "snippet": ""}

        logger.info("PlanExecutor step %d: ask_user '%s'", step_num, question[:80])

        try:
            from src.interfaces.discord_bot import ask_user
            reply = ask_user(question=question, timeout=300)

            if reply is not None:
                # Check for temporal deferral signals
                _lower = reply.lower().strip()
                _deferral_signals = [
                    "take a few hours", "take some hours", "couple hours",
                    "few hours", "later", "not right now", "not now",
                    "give me time", "need time", "need a bit", "in a bit",
                    "in an hour", "tomorrow", "busy", "in a meeting",
                    "get back to you", "get that to you", "working on it",
                ]
                if any(sig in _lower for sig in _deferral_signals):
                    if "tomorrow" in _lower:
                        _resume = "tomorrow (~24h)"
                    elif any(w in _lower for w in ("couple hours", "few hours")):
                        _resume = "in ~2 hours"
                    elif "hour" in _lower:
                        _resume = "in ~1 hour"
                    else:
                        _resume = "in ~1 hour (default)"
                    user_name = get_user_name()
                    return {
                        "success": False,
                        "deferred": True,
                        "response": reply,
                        "error": f"{user_name} deferred: \"{reply}\". Suggested resumption: {_resume}",
                        "snippet": f"Deferred — {user_name} said: {reply[:150]}",
                    }

                user_name = get_user_name()
                return {
                    "success": True,
                    "response": reply,
                    "snippet": f"{user_name} replied: {reply[:200]}",
                }
            else:
                user_name = get_user_name()
                return {
                    "success": False,
                    "error": (
                        f"{user_name} didn't respond (may be asleep or busy). "
                        "Use your best judgment and move on."
                    ),
                    "response": None,
                    "snippet": "No response — use best judgment",
                }
        except Exception as e:
            logger.error("PlanExecutor ask_user error: %s", e)
            return {"success": False, "error": f"ask_user failed: {e}", "response": None, "snippet": f"Error: {e}"}

    def _do_run_python(self, parsed: Dict[str, Any], step_num: int) -> Dict[str, Any]:
        """Run a Python snippet and capture output. 30-second timeout.

        Runs with cwd=project root so paths are consistent with create_file
        (both use project-root-relative paths like workspace/projects/...).
        """
        code = (parsed.get("code") or "").strip()
        if not code:
            return {"success": False, "error": "No code provided", "output": "", "snippet": ""}

        logger.info("PlanExecutor step %d: run_python (%d chars)", step_num, len(code))

        try:
            from src.utils.paths import base_path
            root = base_path()
            # Ensure workspace/ exists for any code that writes there
            os.makedirs(os.path.join(root, "workspace"), exist_ok=True)
            pythonpath = os.pathsep.join(
                filter(None, [root, os.environ.get("PYTHONPATH", "")])
            )
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": pythonpath}
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=30, cwd=root, env=env,
            )
            output = result.stdout[:1000]
            errors = result.stderr[:500]

            if result.returncode == 0:
                return {"success": True, "output": output, "snippet": output[:300] if output else "(no output)"}
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
