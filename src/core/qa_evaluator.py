"""
QA Evaluator — Post-task and post-goal quality gate.

Per-task QA (evaluate_task): runs after PlanExecutor completes a task.
  1. Deterministic checks (free): files exist? parseable? not empty? not truncated?
  2. Semantic evaluation (one model call): does the output match the task spec?

Goal-level QA (evaluate_goal): runs after the Integrator, before the Critic.
  Conformance check: do all task outputs together satisfy the original goal?
  Catches gaps that per-task QA misses (dangling references, missing pieces).

Returns one of:
  - accept: output is good enough
  - reject: specific issues found, should retry with feedback
  - fail: output is fundamentally broken, no point retrying

Created in session 49 (Phase 2: QA + Critic).
Enhanced session 54 (Phase 6: goal-level QA).
"""

import logging
import os
from typing import Any, Dict, List, Optional

from src.utils.parsing import extract_json as _extract_json
from src.utils.config import get_user_name

logger = logging.getLogger(__name__)

# Maximum retries on QA rejection before accepting whatever we have
MAX_QA_RETRIES = 1


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
            issues: list of specific issue descriptions (empty if accepted)
            feedback: human-readable feedback string for retry injection
            cost: cost of the evaluation model call (0 if deterministic-only)
    """
    # Layer 1: Deterministic checks (free)
    det_issues = _deterministic_checks(execution_result)

    # If the task itself failed (schema retries exhausted, no successful steps),
    # skip semantic eval — just report the deterministic issues.
    if not execution_result.get("success", False):
        if execution_result.get("schema_retries_exhausted"):
            return {
                "verdict": "fail",
                "issues": ["Task stopped: JSON schema retries exhausted"] + det_issues,
                "feedback": "Task failed because the model couldn't produce valid JSON. Focus on completing the work.",
                "cost": 0,
            }
        if det_issues:
            return {
                "verdict": "fail",
                "issues": det_issues,
                "feedback": "; ".join(det_issues),
                "cost": 0,
            }
        # Task reported failure but no specific deterministic issues
        return {
            "verdict": "fail",
            "issues": ["Task reported failure with no output"],
            "feedback": "Task failed to produce any output.",
            "cost": 0,
        }

    # If deterministic checks found critical issues, reject without model call
    critical = [i for i in det_issues if i.startswith("CRITICAL:")]
    if critical:
        return {
            "verdict": "reject",
            "issues": det_issues,
            "feedback": "Fix these issues: " + "; ".join(det_issues),
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
        # only if the deterministic issues are substantive
        if any(not i.startswith("NOTE:") for i in det_issues):
            verdict = "reject"

    feedback = ""
    if all_issues:
        feedback = "QA found issues: " + "; ".join(all_issues)

    return {
        "verdict": verdict,
        "issues": all_issues,
        "feedback": feedback,
        "cost": sem_result.get("cost", 0),
    }


def _deterministic_checks(result: Dict[str, Any]) -> List[str]:
    """Run free deterministic quality checks on task output.

    Checks:
    - Files that were supposed to be created actually exist
    - Files are not empty
    - Python files parse without syntax errors
    - Files aren't suspiciously short (likely truncated)
    - The "done" summary exists and isn't generic

    Returns list of issue descriptions (empty = all good).
    """
    issues = []
    files_created = result.get("files_created", [])
    steps = result.get("steps_taken", [])

    # Check each created file
    for fpath in files_created:
        if not os.path.isfile(fpath):
            issues.append(f"CRITICAL: File was reported created but doesn't exist: {fpath}")
            continue

        try:
            size = os.path.getsize(fpath)
        except OSError:
            issues.append(f"CRITICAL: Cannot read file: {fpath}")
            continue

        if size == 0:
            issues.append(f"CRITICAL: File is empty (0 bytes): {os.path.basename(fpath)}")
            continue

        # Check for suspiciously small files (likely truncated or placeholder)
        if size < 50:
            issues.append(f"NOTE: File is very small ({size} bytes): {os.path.basename(fpath)}")

        # Python syntax check
        if fpath.endswith(".py"):
            try:
                import py_compile
                py_compile.compile(fpath, doraise=True)
            except py_compile.PyCompileError as e:
                issues.append(
                    f"CRITICAL: Python syntax error in {os.path.basename(fpath)}: "
                    f"{str(e)[:200]}"
                )

        # Check for truncation markers in content
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(8000)  # Read first 8KB
            # Common truncation signs
            if content.rstrip().endswith(("...", "# TODO", "# ...", "pass  #")):
                issues.append(
                    f"NOTE: File may be truncated or has placeholder ending: "
                    f"{os.path.basename(fpath)}"
                )
        except Exception as e:
            logger.debug("QA: couldn't read file for truncation check %s: %s", fpath, e)

    # Check that a "done" step exists with a real summary
    done_steps = [s for s in steps if s.get("action") == "done"]
    if done_steps:
        summary = done_steps[-1].get("summary", "")
        if not summary or len(summary) < 20:
            issues.append("NOTE: Task completion summary is missing or too short")
        elif summary.startswith("Task force-stopped") or summary.startswith("Task cancelled"):
            issues.append("CRITICAL: Task did not complete normally")

    return issues


def _semantic_evaluation(
    task_description: str,
    goal_description: str,
    result: Dict[str, Any],
    router: Any,
) -> Dict[str, Any]:
    """Model-based semantic quality evaluation.

    Reads the task output and judges whether it actually accomplishes
    what was asked. Returns verdict + specific issues.
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

    # Read file contents (truncated) for evaluation
    file_evidence = []
    for fpath in files_created[:3]:
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(2000)
            fname = os.path.basename(fpath)
            file_evidence.append(f"--- {fname} ({os.path.getsize(fpath)} bytes) ---\n{content}")
        except Exception as e:
            logger.debug("QA semantic eval: couldn't read %s: %s", fpath, e)
            continue

    # Count action types for context
    action_counts = {}
    for s in steps:
        act = s.get("action", "?")
        action_counts[act] = action_counts.get(act, 0) + 1

    actions_summary = ", ".join(f"{k}: {v}" for k, v in sorted(action_counts.items()))

    files_block = "\n\n".join(file_evidence) if file_evidence else "(no files created)"

    user_name = get_user_name()
    prompt = f"""You are a QA evaluator for an AI agent's task output. Be strict but fair.

TASK: {task_description}
GOAL: {goal_description}

COMPLETION SUMMARY: {done_summary[:500]}

ACTIONS TAKEN: {actions_summary}
STEPS: {result.get('total_steps', 0)} total, {result.get('successful_steps', 0)} successful

FILES CREATED:
{files_block}

Evaluate whether this task output is acceptable. Check:
1. Does the output actually accomplish what the task asked for?
2. Is the content substantive (real data/code, not placeholders or summaries)?
3. If code was created, does it look functional (not truncated, has imports, etc.)?
4. Is the output useful to {user_name}, or is it just a report about what should be done?

Return ONLY a JSON object:
{{
    "verdict": "accept" or "reject",
    "issues": ["specific issue 1", "specific issue 2"],
    "reasoning": "brief explanation"
}}

Be strict: reject output that is just a summary/plan instead of actual deliverables.
Be fair: accept output that genuinely accomplishes the task, even if imperfect.
JSON only:"""

    try:
        resp = router.generate(
            prompt=prompt,
            max_tokens=500,
            temperature=0.2,
        )
        cost = resp.get("cost_usd", 0)
        parsed = _extract_json(resp.get("text", ""))

        if not parsed or not isinstance(parsed, dict):
            logger.warning("QA semantic eval: couldn't parse model response")
            return {"verdict": "accept", "issues": [], "cost": cost}

        verdict = parsed.get("verdict", "accept")
        if verdict not in ("accept", "reject"):
            verdict = "accept"

        issues = parsed.get("issues", [])
        if not isinstance(issues, list):
            issues = []
        # Ensure issues are strings
        issues = [str(i) for i in issues if i]

        reasoning = parsed.get("reasoning", "")
        if reasoning:
            logger.info("QA eval: %s — %s", verdict, reasoning[:200])

        return {"verdict": verdict, "issues": issues, "cost": cost}

    except Exception as e:
        logger.warning("QA semantic evaluation failed: %s", e)
        # On failure, don't block — accept and move on
        return {"verdict": "accept", "issues": [], "cost": 0}


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
            issues: list of specific conformance issues
            feedback: human-readable feedback string
            cost: cost of the evaluation model call
    """
    if not router:
        return {"verdict": "accept", "issues": [], "feedback": "", "cost": 0}

    successful = [t for t in tasks if t.get("result", {}).get("success")]
    if not successful:
        return {
            "verdict": "fail",
            "issues": ["No tasks completed successfully"],
            "feedback": "Goal produced no successful output.",
            "cost": 0,
        }

    # Build task evidence
    task_lines = []
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
        task_lines.append(line)

    # Read file contents for cross-reference checking
    file_blocks = []
    total_chars = 0
    for fpath in files_created[:5]:
        if total_chars > 4000:
            break
        try:
            if not os.path.isfile(fpath):
                file_blocks.append(f"MISSING: {os.path.basename(fpath)}")
                continue
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(1200)
            fname = os.path.basename(fpath)
            block = f"--- {fname} ---\n{content}"
            file_blocks.append(block)
            total_chars += len(block)
        except Exception as e:
            logger.debug("Goal QA: couldn't read %s: %s", fpath, e)
            continue

    tasks_block = "\n\n".join(task_lines)
    files_block = "\n\n".join(file_blocks) if file_blocks else "(no files)"

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
    "issues": ["specific conformance gap 1", "gap 2"],
    "reasoning": "brief explanation"
}}

Be fair: accept if the goal is substantively accomplished, even if imperfect.
Only reject if there are real conformance gaps (not style preferences).
JSON only:"""

    try:
        resp = router.generate(
            prompt=prompt,
            max_tokens=500,
            temperature=0.2,
        )
        cost = resp.get("cost_usd", 0)
        parsed = _extract_json(resp.get("text", ""))

        if not parsed or not isinstance(parsed, dict):
            logger.warning("Goal-level QA: couldn't parse model response")
            return {"verdict": "accept", "issues": [], "feedback": "", "cost": cost}

        verdict = parsed.get("verdict", "accept")
        if verdict not in ("accept", "reject"):
            verdict = "accept"

        issues = parsed.get("issues", [])
        if not isinstance(issues, list):
            issues = []
        issues = [str(i) for i in issues if i]

        reasoning = parsed.get("reasoning", "")
        if reasoning:
            logger.info("Goal QA: %s — %s", verdict, reasoning[:200])

        feedback = ""
        if issues:
            feedback = "Goal-level QA issues: " + "; ".join(issues)

        return {"verdict": verdict, "issues": issues, "feedback": feedback, "cost": cost}

    except Exception as e:
        logger.warning("Goal-level QA failed: %s", e)
        return {"verdict": "accept", "issues": [], "feedback": "", "cost": 0}
