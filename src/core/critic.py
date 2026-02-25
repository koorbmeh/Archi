"""
Critic — Adversarial per-goal quality evaluation with error taxonomy.

Runs after all tasks in a goal complete (and pass QA). Takes an adversarial
stance: "What's wrong? What edge cases fail? What assumptions are bad?
Would the user actually use this?"

If significant concerns are found, returns remediation task descriptions
that get added to the goal for a fix-up pass.

Phase 6 enhancement: queries the User Model for the user's preferences,
corrections, and style — so the Critic can also flag style/approach
mismatches, not just functional issues.

Session 124: concerns are now structured dicts with error type classification,
matching the QA evaluator taxonomy for consistency.

Created in session 49 (Phase 2: QA + Critic).
Enhanced session 54 (Phase 6: User Model integration).
Enhanced session 124 (structured error taxonomy).
"""

import logging
import os
from typing import Any, Dict, List, Optional

from src.utils.config import get_user_name
from src.utils.parsing import extract_json as _extract_json

logger = logging.getLogger(__name__)

# Critic-specific error types (supplements QA evaluator taxonomy)
CRITIC_ERROR_TYPES = {
    "style_mismatch": "Doesn't match user's known preferences or style",
    "edge_case": "Missing edge case handling",
    "quality_concern": "General quality issue",
    "missing_validation": "Missing input validation or error handling",
    "non_functional": "Code won't run or is missing critical pieces",
    "not_useful": "Output is busy work — user wouldn't actually use it",
}


def format_concerns(concerns: List[Dict[str, Any]]) -> List[str]:
    """Convert structured concerns to human-readable strings."""
    result = []
    for c in concerns:
        if isinstance(c, str):
            result.append(c)
        elif isinstance(c, dict):
            etype = c.get("type", "quality_concern")
            detail = c.get("detail", "")
            result.append(f"[{etype}] {detail}" if detail else f"[{etype}]")
    return result


def critique_goal(
    goal_description: str,
    task_results: List[Dict[str, Any]],
    files_created: List[str],
    router: Any,
) -> Dict[str, Any]:
    """Run adversarial critique on a completed goal's output.

    Args:
        goal_description: What the goal was supposed to accomplish.
        task_results: List of overnight_results entries for this goal's tasks.
        files_created: All files created across all tasks in this goal.
        router: Model router for the critique call.

    Returns:
        dict with:
            concerns: list of structured concern dicts (type, detail)
            remediation_tasks: list of task description strings to add to the goal
            severity: "none" | "minor" | "significant"
            cost: cost of the critique model call
    """
    if not router:
        return _no_concerns()

    # Don't critique goals with no successful output
    successful_tasks = [r for r in task_results if r.get("success")]
    if not successful_tasks:
        logger.info("Critic: skipping — no successful tasks in goal")
        return _no_concerns()

    # Gather evidence for the critic
    task_summaries = []
    for r in task_results:
        status = "completed" if r.get("success") else "FAILED"
        summary = r.get("summary", "")
        task_summaries.append(f"[{status}] {r.get('task', '?')}: {summary[:200]}")

    # Read file contents (truncated)
    file_evidence = []
    for fpath in files_created[:5]:
        try:
            if not os.path.isfile(fpath):
                continue
            size = os.path.getsize(fpath)
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(1500)
            fname = os.path.basename(fpath)
            file_evidence.append(f"--- {fname} ({size} bytes) ---\n{content}")
        except Exception as e:
            logger.debug("Critic: couldn't read file %s: %s", fpath, e)
            continue

    if not file_evidence and not task_summaries:
        return _no_concerns()

    tasks_block = "\n".join(task_summaries)
    files_block = "\n\n".join(file_evidence) if file_evidence else "(no files to review)"

    # Phase 6: Query User Model for preferences to inform the critique
    user_model_block = ""
    try:
        from src.core.user_model import get_user_model
        um = get_user_model()
        if um:
            user_model_block = um.get_context_for_critic()
    except Exception:
        pass

    # Build the error types reference for the model
    all_types = list(CRITIC_ERROR_TYPES.keys())
    types_ref = ", ".join(f'"{t}"' for t in all_types)

    prompt = f"""You are an adversarial critic reviewing an AI agent's completed goal.
Your job is to find real problems — things that would make {get_user_name()} disappointed
or that would fail in practice. Be tough but honest. Don't invent problems
that aren't there.

GOAL: {goal_description}

TASKS COMPLETED:
{tasks_block}

FILES PRODUCED:
{files_block}
{user_model_block}
Answer these questions honestly:
1. Does the output actually accomplish the goal, or does it just look busy?
2. If code was produced, would it run? Are there obvious bugs or missing pieces?
3. Are there edge cases or assumptions that would break in real use?
4. Would {get_user_name()} actually USE this output, or would they look at it and say "this isn't what I wanted"?
5. Is anything obviously wrong, misleading, or low-quality?
6. Does the approach match {get_user_name()}'s known preferences and past corrections?

Return ONLY a JSON object:
{{
    "severity": "none" or "minor" or "significant",
    "concerns": [
        {{
            "type": one of [{types_ref}],
            "detail": "specific concern description"
        }}
    ],
    "remediation_tasks": ["Fix X by doing Y", "Add Z to handle edge case"],
    "summary": "one-sentence overall assessment"
}}

Rules:
- "none": output genuinely accomplishes the goal. No remediation needed.
- "minor": small issues but output is usable. No remediation tasks — just log.
- "significant": real problems that would disappoint {get_user_name()}. Include remediation tasks.
- remediation_tasks should be concrete, actionable tasks (not vague "improve X").
- Only include remediation_tasks for "significant" severity.
- Max 2 remediation tasks. Focus on the most impactful fixes.
- If {get_user_name()} has known preferences that conflict with the approach used, flag it.
JSON only:"""

    try:
        resp = router.generate(
            prompt=prompt,
            max_tokens=600,
            temperature=0.3,
        )
        cost = resp.get("cost_usd", 0)
        parsed = _extract_json(resp.get("text", ""))

        if not parsed or not isinstance(parsed, dict):
            logger.warning("Critic: couldn't parse model response")
            return {**_no_concerns(), "cost": cost}

        severity = parsed.get("severity", "none")
        if severity not in ("none", "minor", "significant"):
            severity = "none"

        raw_concerns = parsed.get("concerns", [])
        if not isinstance(raw_concerns, list):
            raw_concerns = []

        # Normalize concerns into structured format
        concerns = []
        for rc in raw_concerns:
            if not rc:
                continue
            if isinstance(rc, str):
                concerns.append({"type": "quality_concern", "detail": rc})
            elif isinstance(rc, dict):
                etype = rc.get("type", "quality_concern")
                if etype not in CRITIC_ERROR_TYPES:
                    etype = "quality_concern"
                detail = str(rc.get("detail", ""))
                if detail:
                    concerns.append({"type": etype, "detail": detail})

        remediation = []
        if severity == "significant":
            raw_tasks = parsed.get("remediation_tasks", [])
            if isinstance(raw_tasks, list):
                remediation = [str(t) for t in raw_tasks[:2] if t]

        summary = parsed.get("summary", "")
        if summary:
            logger.info("Critic [%s]: %s", severity, summary[:200])
        if concerns:
            formatted = format_concerns(concerns)
            logger.info("Critic concerns: %s", "; ".join(c[:80] for c in formatted[:3]))

        return {
            "concerns": concerns,
            "remediation_tasks": remediation,
            "severity": severity,
            "cost": cost,
        }

    except Exception as e:
        logger.warning("Critic evaluation failed: %s", e)
        return {**_no_concerns(), "cost": 0}


def _no_concerns() -> Dict[str, Any]:
    """Return a clean critic result with no concerns."""
    return {
        "concerns": [],
        "remediation_tasks": [],
        "severity": "none",
        "cost": 0,
    }
