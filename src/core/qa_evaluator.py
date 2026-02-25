"""
QA Evaluator — Post-task and post-goal quality gate with error taxonomy.

Per-task QA (evaluate_task): runs after PlanExecutor completes a task.
  1. Deterministic checks (free): files exist? parseable? not empty? not truncated?
  2. Semantic evaluation (one model call): does the output match the task spec?

Goal-level QA (evaluate_goal): runs after the Integrator, before the Critic.
  Conformance check: do all task outputs together satisfy the original goal?
  Catches gaps that per-task QA misses (dangling references, missing pieces).

Issues are returned as structured dicts with error type, severity, detail,
and optional step/file references. Use format_issues() for human-readable text.

Returns one of:
  - accept: output is good enough
  - reject: specific issues found, should retry with feedback
  - fail: output is fundamentally broken, no point retrying

Created in session 49 (Phase 2: QA + Critic).
Enhanced session 54 (Phase 6: goal-level QA).
Enhanced session 124 (structured error taxonomy + step-level feedback).
"""

import logging
import os
from typing import Any, Dict, List, Optional

from src.utils.parsing import extract_json as _extract_json, read_file_contents
from src.utils.config import get_user_name

logger = logging.getLogger(__name__)

# Maximum retries on QA rejection before accepting whatever we have
MAX_QA_RETRIES = 1


# ── Error Taxonomy ──────────────────────────────────────────────────
# Each issue is classified by type and severity. Types enable targeted
# retries; severity drives verdict logic (critical → reject, note → info).

ERROR_TYPES = {
    # Deterministic (free) checks
    "missing_file": "File reported created but doesn't exist",
    "empty_file": "File exists but has no content",
    "unreadable_file": "File cannot be read",
    "syntax_error": "Code has syntax errors",
    "truncated": "Output appears truncated or has placeholder ending",
    "small_file": "File is suspiciously small (likely placeholder)",
    "incomplete_task": "Task did not complete normally",
    "weak_summary": "Task completion summary is missing or too brief",
    "schema_exhausted": "JSON schema retries exhausted",
    "no_output": "Task reported failure with no output",
    # Semantic (model-based) checks
    "invalid_output": "Output doesn't accomplish what was asked",
    "placeholder_content": "Output contains placeholders instead of real content",
    "missing_precondition": "Skipped necessary preparation or input gathering",
    "stale_data": "Used outdated or incorrect information",
    "wrong_approach": "Used inappropriate tool or approach for the task",
    # Goal-level checks
    "conformance_gap": "Combined output doesn't satisfy goal requirements",
    "dangling_reference": "Cross-file reference to non-existent resource",
    "missing_component": "Required component not produced",
    "no_successful_tasks": "No tasks completed successfully",
}


def make_issue(
    error_type: str,
    detail: str,
    severity: str = "warning",
    step: Optional[int] = None,
    file: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a structured issue dict.

    Args:
        error_type: Key from ERROR_TYPES.
        detail: Human-readable description of the specific issue.
        severity: "critical" | "warning" | "note".
        step: Optional 0-based step index where the issue occurred.
        file: Optional filename associated with the issue.
    """
    issue = {"type": error_type, "severity": severity, "detail": detail}
    if step is not None:
        issue["step"] = step
    if file is not None:
        issue["file"] = file
    return issue


def format_issues(issues: List[Dict[str, Any]]) -> List[str]:
    """Convert structured issues to human-readable strings.

    Used by consumers that need flat string lists for logging or display.
    """
    result = []
    for i in issues:
        if not isinstance(i, dict):
            result.append(str(i))
            continue
        severity = i.get("severity", "warning").upper()
        detail = i.get("detail", "")
        etype = i.get("type", "unknown")
        parts = [f"[{severity}:{etype}]"]
        if i.get("file"):
            parts.append(f"({i['file']})")
        if i.get("step") is not None:
            parts.append(f"(step {i['step']})")
        parts.append(detail)
        result.append(" ".join(parts))
    return result


def format_issues_for_retry(issues: List[Dict[str, Any]]) -> str:
    """Build a targeted feedback string from structured issues for QA retry.

    Groups issues by type so the retry can focus on specific error classes.
    Includes step/file references for precision.
    """
    if not issues:
        return ""

    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for i in issues:
        if not isinstance(i, dict):
            continue
        by_type.setdefault(i.get("type", "unknown"), []).append(i)

    parts = []
    for etype, items in by_type.items():
        label = ERROR_TYPES.get(etype, etype)
        if len(items) == 1:
            i = items[0]
            loc = ""
            if i.get("file"):
                loc += f" in {i['file']}"
            if i.get("step") is not None:
                loc += f" at step {i['step']}"
            parts.append(f"{label}{loc}: {i['detail']}")
        else:
            details = "; ".join(i["detail"] for i in items if i.get("detail"))
            parts.append(f"{label} ({len(items)}x): {details}")

    return "QA feedback: " + " | ".join(parts)


# ── Per-task QA ─────────────────────────────────────────────────────


def evaluate_task(
    task_description: str,
    goal_description: str,
    execution_result: Dict[str, Any],
    router: Any,
) -> Dict[str, Any]:
    """Evaluate a completed task's output quality.

    Args:
        task_description: What the task was supposed to do.
        goal_description: Parent goal context.
        execution_result: The dict returned by PlanExecutor.execute() or
            execute_task(), containing files_created, steps_taken, success, etc.
        router: Model router for the semantic evaluation call.

    Returns:
        dict with:
            verdict: "accept" | "reject" | "fail"
            issues: list of structured issue dicts (type, severity, detail, etc.)
            feedback: human-readable feedback string for retry injection
            cost: cost of the evaluation model call (0 if deterministic-only)
    """
    # Layer 1: Deterministic checks (free)
    det_issues = _deterministic_checks(execution_result)

    # If the task itself failed (schema retries exhausted, no successful steps),
    # skip semantic eval — just report the deterministic issues.
    if not execution_result.get("success", False):
        if execution_result.get("schema_retries_exhausted"):
            schema_issue = make_issue(
                "schema_exhausted",
                "Task stopped: JSON schema retries exhausted",
                severity="critical",
            )
            all_issues = [schema_issue] + det_issues
            return {
                "verdict": "fail",
                "issues": all_issues,
                "feedback": format_issues_for_retry(all_issues),
                "cost": 0,
            }
        if det_issues:
            return {
                "verdict": "fail",
                "issues": det_issues,
                "feedback": format_issues_for_retry(det_issues),
                "cost": 0,
            }
        no_output = make_issue(
            "no_output", "Task reported failure with no output", severity="critical",
        )
        return {
            "verdict": "fail",
            "issues": [no_output],
            "feedback": "Task failed to produce any output.",
            "cost": 0,
        }

    # If deterministic checks found critical issues, reject without model call
    critical = [i for i in det_issues if i.get("severity") == "critical"]
    if critical:
        return {
            "verdict": "reject",
            "issues": det_issues,
            "feedback": format_issues_for_retry(det_issues),
            "cost": 0,
        }

    # Layer 2: Semantic evaluation (one model call)
    sem_result = _semantic_evaluation(
        task_description, goal_description, execution_result, router,
    )
    all_issues = det_issues + sem_result.get("issues", [])

    verdict = sem_result.get("verdict", "accept")
    if det_issues and verdict == "accept":
        # Deterministic issues found but model said accept — downgrade to reject
        # only if the deterministic issues are substantive (not just notes)
        if any(i.get("severity") != "note" for i in det_issues):
            verdict = "reject"

    feedback = format_issues_for_retry(all_issues) if all_issues else ""

    return {
        "verdict": verdict,
        "issues": all_issues,
        "feedback": feedback,
        "cost": sem_result.get("cost", 0),
    }


def _deterministic_checks(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Run free deterministic quality checks on task output.

    Checks:
    - Files that were supposed to be created actually exist
    - Files are not empty
    - Python files parse without syntax errors
    - Files aren't suspiciously short (likely truncated)
    - The "done" summary exists and isn't generic

    Returns list of structured issue dicts (empty = all good).
    """
    issues = []
    files_created = result.get("files_created", [])
    steps = result.get("steps_taken", [])

    # Check each created file
    for fpath in files_created:
        fname = os.path.basename(fpath)

        if not os.path.isfile(fpath):
            issues.append(make_issue(
                "missing_file",
                f"File was reported created but doesn't exist: {fpath}",
                severity="critical", file=fname,
            ))
            continue

        try:
            size = os.path.getsize(fpath)
        except OSError:
            issues.append(make_issue(
                "unreadable_file", f"Cannot read file: {fpath}",
                severity="critical", file=fname,
            ))
            continue

        if size == 0:
            issues.append(make_issue(
                "empty_file", f"File is empty (0 bytes): {fname}",
                severity="critical", file=fname,
            ))
            continue

        # Check for suspiciously small files (likely truncated or placeholder)
        if size < 50:
            issues.append(make_issue(
                "small_file", f"File is very small ({size} bytes): {fname}",
                severity="note", file=fname,
            ))

        # Python syntax check
        if fpath.endswith(".py"):
            try:
                import py_compile
                py_compile.compile(fpath, doraise=True)
            except py_compile.PyCompileError as e:
                issues.append(make_issue(
                    "syntax_error",
                    f"Python syntax error in {fname}: {str(e)[:200]}",
                    severity="critical", file=fname,
                ))

        # Check for truncation markers in content
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(8000)  # Read first 8KB
            # Common truncation signs
            if content.rstrip().endswith(("...", "# TODO", "# ...", "pass  #")):
                issues.append(make_issue(
                    "truncated",
                    f"File may be truncated or has placeholder ending: {fname}",
                    severity="note", file=fname,
                ))
        except Exception as e:
            logger.debug("QA: couldn't read file for truncation check %s: %s", fpath, e)

    # Check that a "done" step exists with a real summary
    done_steps = [s for s in steps if s.get("action") == "done"]
    if done_steps:
        summary = done_steps[-1].get("summary", "")
        if not summary or len(summary) < 20:
            issues.append(make_issue(
                "weak_summary",
                "Task completion summary is missing or too short",
                severity="note",
            ))
        elif summary.startswith("Task force-stopped") or summary.startswith("Task cancelled"):
            issues.append(make_issue(
                "incomplete_task", "Task did not complete normally",
                severity="critical",
            ))

    return issues


def _normalize_model_issues(
    raw_issues: Any, default_type: str,
) -> List[Dict[str, Any]]:
    """Normalize model-returned issues into structured format.

    Handles: None/empty entries (skipped), plain strings (wrapped with
    default_type), dicts with unknown types (remapped to default_type).
    Shared by _semantic_evaluation and evaluate_goal.
    """
    if not isinstance(raw_issues, list):
        return []
    issues = []
    for ri in raw_issues:
        if not ri:
            continue
        if isinstance(ri, str):
            issues.append(make_issue(default_type, ri, severity="warning"))
        elif isinstance(ri, dict):
            etype = ri.get("type", default_type)
            if etype not in ERROR_TYPES:
                etype = default_type
            issues.append(make_issue(
                error_type=etype,
                detail=str(ri.get("detail", "")),
                severity="warning",
                step=ri.get("step"),
                file=ri.get("file"),
            ))
    return issues


def _call_qa_model(
    router: Any, prompt: str, default_type: str, log_prefix: str,
) -> Dict[str, Any]:
    """Make a QA model call, parse the response, and normalize issues.

    Shared by _semantic_evaluation and evaluate_goal. On parse failure
    or exception, returns accept with no issues (don't block progress).
    """
    try:
        resp = router.generate(prompt=prompt, max_tokens=600, temperature=0.2)
        cost = resp.get("cost_usd", 0)
        parsed = _extract_json(resp.get("text", ""))

        if not parsed or not isinstance(parsed, dict):
            logger.warning("%s: couldn't parse model response", log_prefix)
            return {"verdict": "accept", "issues": [], "cost": cost}

        verdict = parsed.get("verdict", "accept")
        if verdict not in ("accept", "reject"):
            verdict = "accept"

        issues = _normalize_model_issues(parsed.get("issues", []), default_type)

        reasoning = parsed.get("reasoning", "")
        if reasoning:
            logger.info("%s: %s — %s", log_prefix, verdict, reasoning[:200])

        return {"verdict": verdict, "issues": issues, "cost": cost}

    except Exception as e:
        logger.warning("%s failed: %s", log_prefix, e)
        return {"verdict": "accept", "issues": [], "cost": 0}


def _build_task_evidence(tasks: List[Dict[str, Any]]) -> str:
    """Build a human-readable summary of task results for goal-level QA."""
    lines = []
    for t in tasks:
        desc = t.get("description", "?")[:120]
        result = t.get("result", {})
        status = "DONE" if result.get("success") else "FAILED"
        expected = t.get("expected_output", "")
        files = [os.path.basename(f) for f in result.get("files_created", [])[:3]]
        line = f"[{status}] {desc}"
        if expected:
            line += f"\n  Expected: {expected[:120]}"
        if files:
            line += f"\n  Files: {', '.join(files)}"
        lines.append(line)
    return "\n\n".join(lines)



def _semantic_evaluation(
    task_description: str,
    goal_description: str,
    result: Dict[str, Any],
    router: Any,
) -> Dict[str, Any]:
    """Model-based semantic quality evaluation with error type classification.

    Asks the model to classify each issue by type from our taxonomy,
    enabling targeted retries. Returns verdict + structured issues.
    """
    if not router:
        return {"verdict": "accept", "issues": [], "cost": 0}

    # Gather output evidence for the model to evaluate
    files_created = result.get("files_created", [])
    steps = result.get("steps_taken", [])

    # Get the done summary
    done_summary = ""
    for s in reversed(steps):
        if s.get("action") == "done":
            done_summary = s.get("summary", "")
            break

    files_block = read_file_contents(files_created, max_files=3, max_chars=2000, total_budget=8000, note_missing=True)

    # Build numbered step log for step-level references
    step_log_lines = []
    for idx, s in enumerate(steps):
        act = s.get("action", "?")
        if act == "think":
            continue
        params = s.get("params", {})
        brief = ""
        if act == "web_search":
            brief = f"query={params.get('query', '')[:60]}"
        elif act in ("create_file", "write_source", "edit_file"):
            brief = f"path={params.get('path', '')}"
        elif act == "read_file":
            brief = f"path={params.get('path', '')}"
        elif act == "done":
            brief = f"summary={s.get('summary', '')[:60]}"
        else:
            brief = str(params)[:60]
        step_log_lines.append(f"  Step {idx}: [{act}] {brief}")

    step_log = "\n".join(step_log_lines[-15:]) if step_log_lines else "(no steps)"

    # Count action types for context
    action_counts = {}
    for s in steps:
        act = s.get("action", "?")
        action_counts[act] = action_counts.get(act, 0) + 1

    actions_summary = ", ".join(f"{k}: {v}" for k, v in sorted(action_counts.items()))

    # Build the error types reference for the model
    semantic_types = [
        "invalid_output", "placeholder_content", "missing_precondition",
        "stale_data", "wrong_approach",
    ]
    types_ref = ", ".join(f'"{t}"' for t in semantic_types)

    user_name = get_user_name()
    prompt = f"""You are a QA evaluator for an AI agent's task output. Be strict but fair.

TASK: {task_description}
GOAL: {goal_description}

COMPLETION SUMMARY: {done_summary[:500]}

ACTIONS TAKEN: {actions_summary}
STEPS: {result.get('total_steps', 0)} total, {result.get('successful_steps', 0)} successful
STEP LOG:
{step_log}

FILES:
{files_block}

Evaluate whether this task output is acceptable. Check:
1. Does the output actually accomplish what the task asked for?
2. Is the content substantive (real data/code, not placeholders or summaries)?
3. If code was created, does it look functional (not truncated, has imports, etc.)?
4. Is the output useful to {user_name}, or is it just a report about what should be done?

Return ONLY a JSON object:
{{
    "verdict": "accept" or "reject",
    "issues": [
        {{
            "type": one of [{types_ref}],
            "detail": "specific description of the issue",
            "step": null or step number where issue occurred,
            "file": null or "filename.ext" if file-related
        }}
    ],
    "reasoning": "brief explanation"
}}

Be strict: reject output that is just a summary/plan instead of actual deliverables.
Be fair: accept output that genuinely accomplishes the task, even if imperfect.
JSON only:"""

    return _call_qa_model(router, prompt, "invalid_output", "QA eval")


# ── Goal-level QA (Phase 6) ──────────────────────────────────────


def evaluate_goal(
    goal_description: str,
    tasks: List[Dict[str, Any]],
    files_created: List[str],
    integrator_summary: str,
    router: Any,
) -> Dict[str, Any]:
    """Evaluate whether all task outputs together satisfy the original goal.

    Runs after the Integrator (which may have created glue files) but before
    the Critic. This is conformance-focused ("does it match the spec?"),
    while the Critic is adversarial ("what's wrong?").

    Args:
        goal_description: What the goal was supposed to accomplish.
        tasks: List of task dicts with description, result, spec fields.
        files_created: All files created across all tasks.
        integrator_summary: Summary from the Integrator (or empty string).
        router: Model router for the evaluation call.

    Returns:
        dict with:
            verdict: "accept" | "reject"
            issues: list of structured issue dicts
            feedback: human-readable feedback string
            cost: cost of the evaluation model call
    """
    if not router:
        return {"verdict": "accept", "issues": [], "feedback": "", "cost": 0}

    successful = [t for t in tasks if t.get("result", {}).get("success")]
    if not successful:
        issue = make_issue(
            "no_successful_tasks", "No tasks completed successfully",
            severity="critical",
        )
        return {
            "verdict": "fail",
            "issues": [issue],
            "feedback": "Goal produced no successful output.",
            "cost": 0,
        }

    tasks_block = _build_task_evidence(tasks)
    files_block = read_file_contents(files_created, max_files=5, max_chars=1200, total_budget=4000, note_missing=True)

    goal_types = ["conformance_gap", "dangling_reference", "missing_component"]
    types_ref = ", ".join(f'"{t}"' for t in goal_types)

    prompt = f"""You are a conformance QA evaluator. Check whether the combined output
of all tasks satisfies the original goal description.

GOAL: {goal_description}

INTEGRATOR SUMMARY: {integrator_summary[:400] if integrator_summary else "(none)"}

TASKS:
{tasks_block}

FILES:
{files_block}

Check:
1. Do all task outputs together satisfy the original goal?
2. Are there dangling references? (file A imports from file B, but B wasn't created)
3. Does the combined output match what the task specs promised?
4. Is anything critically missing that the goal required?

Return ONLY a JSON object:
{{
    "verdict": "accept" or "reject",
    "issues": [
        {{
            "type": one of [{types_ref}],
            "detail": "specific conformance gap description",
            "file": null or "filename.ext" if relevant
        }}
    ],
    "reasoning": "brief explanation"
}}

Be fair: accept if the goal is substantively accomplished, even if imperfect.
Only reject if there are real conformance gaps (not style preferences).
JSON only:"""

    qa_result = _call_qa_model(router, prompt, "conformance_gap", "Goal QA")
    if qa_result.get("issues"):
        qa_result["feedback"] = format_issues_for_retry(qa_result["issues"])
    else:
        qa_result["feedback"] = ""
    return qa_result
