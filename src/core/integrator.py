"""
Integrator — Post-completion cross-task synthesis for a goal.

Runs after the DAG orchestrator finishes all tasks but before the Critic.
One model call per multi-task goal. Checks that pieces fit together,
catches cross-task issues (mismatched imports, missing entry points,
incompatible interfaces), creates missing glue if needed, and produces
a human-readable summary of what was built and how to use it.

The summary feeds into the Notification Formatter so completion messages
describe what was actually built, not just "completed 3 tasks."

Created session 54 (Phase 6: Integration).
"""

import logging
import os
from typing import Any, Dict, List, Optional

from src.utils.parsing import extract_json as _extract_json

logger = logging.getLogger(__name__)


def integrate_goal(
    goal_description: str,
    tasks: List[Dict[str, Any]],
    files_created: List[str],
    router: Any,
    discovery_brief: Optional[str] = None,
) -> Dict[str, Any]:
    """Run post-completion integration across all task outputs for a goal.

    Args:
        goal_description: What the goal was supposed to accomplish.
        tasks: List of task dicts with keys: description, result (dict with
            success, summary, files_created), spec fields (files_to_create,
            expected_output, interfaces).
        files_created: All files created/modified across all tasks.
        router: Model router for the integration call.
        discovery_brief: Optional Discovery brief for project context.

    Returns:
        dict with:
            summary: human-readable summary of what was built and how to use it
            issues_found: list of cross-task issue descriptions
            glue_created: list of glue files created (paths)
            cost: cost of the integration model call
    """
    if not router:
        return _empty_result()

    # Skip for trivial goals (0-1 tasks) — no cross-task integration needed
    successful_tasks = [t for t in tasks if t.get("result", {}).get("success")]
    if len(successful_tasks) <= 1:
        # Still produce a summary for single-task goals
        if successful_tasks:
            summary = _single_task_summary(successful_tasks[0], files_created)
            return {
                "summary": summary,
                "issues_found": [],
                "glue_created": [],
                "cost": 0,
            }
        return _empty_result()

    # Gather evidence for the model
    task_evidence = _build_task_evidence(tasks)
    file_evidence = _read_file_contents(files_created)

    # Build the integration prompt
    discovery_block = ""
    if discovery_brief:
        # Truncate to keep prompt reasonable
        discovery_block = f"\nPROJECT CONTEXT (what already existed):\n{discovery_brief[:1500]}\n"

    prompt = f"""You are the Integrator for an AI agent that just completed a multi-task goal.
Your job is to check that all pieces fit together and produce a clear summary.

GOAL: {goal_description}
{discovery_block}
TASKS AND THEIR OUTPUTS:
{task_evidence}

FILES PRODUCED:
{file_evidence}

Check for cross-task issues:
1. If code files were created, do imports between them match? (file A imports X from file B — does B actually export X?)
2. Are there missing entry points? (code exists but no way to run it)
3. Are there incompatible interfaces? (task A's output format doesn't match task B's expected input)
4. Is any glue code missing? (__init__.py, config files, setup instructions)
5. Do all the pieces together actually accomplish the goal?

Return ONLY a JSON object:
{{
    "summary": "2-4 sentence human-readable summary of what was built and how to use it. Be specific — mention file names, commands to run, what the user should do next.",
    "issues": ["specific cross-task issue 1", "issue 2"],
    "missing_glue": ["description of missing glue file 1"]
}}

Rules:
- The summary should help the user understand what was built WITHOUT reading every file.
- Only flag real issues — don't invent problems that aren't there.
- If everything fits together well, return empty issues and missing_glue arrays.
- Be concise — this feeds into a Discord notification.
JSON only:"""

    try:
        resp = router.generate(
            prompt=prompt,
            max_tokens=600,
            temperature=0.2,
        )
        cost = resp.get("cost_usd", 0)
        parsed = _extract_json(resp.get("text", ""))

        if not parsed or not isinstance(parsed, dict):
            logger.warning("Integrator: couldn't parse model response")
            return {
                "summary": _fallback_summary(tasks, files_created),
                "issues_found": [],
                "glue_created": [],
                "cost": cost,
            }

        summary = parsed.get("summary", "")
        if not summary or len(summary) < 10:
            summary = _fallback_summary(tasks, files_created)

        issues = parsed.get("issues", [])
        if not isinstance(issues, list):
            issues = []
        issues = [str(i) for i in issues if i]

        missing_glue = parsed.get("missing_glue", [])
        if not isinstance(missing_glue, list):
            missing_glue = []

        # Log results
        if issues:
            logger.info(
                "Integrator found %d issue(s): %s",
                len(issues), "; ".join(i[:60] for i in issues[:3]),
            )
        if missing_glue:
            logger.info(
                "Integrator detected %d missing glue item(s): %s",
                len(missing_glue), "; ".join(str(g)[:60] for g in missing_glue[:3]),
            )
        logger.info("Integrator summary: %s", summary[:200])

        # Session 58: missing_glue is surfaced in output so downstream
        # consumers (Critic, notifications) can act on it. Actual file
        # creation deferred — detection + reporting is sufficient for
        # current usage since workers handle file creation during tasks.
        return {
            "summary": summary,
            "issues_found": issues,
            "missing_glue": [str(g) for g in missing_glue if g],
            "glue_created": [],
            "cost": cost,
        }

    except Exception as e:
        logger.warning("Integrator failed: %s", e)
        return {
            "summary": _fallback_summary(tasks, files_created),
            "issues_found": [],
            "glue_created": [],
            "cost": 0,
        }


# ── Internal helpers ──────────────────────────────────────────────


def _build_task_evidence(tasks: List[Dict[str, Any]]) -> str:
    """Build a concise evidence block from task results."""
    lines = []
    for i, t in enumerate(tasks, 1):
        desc = t.get("description", "?")[:120]
        result = t.get("result", {})
        status = "DONE" if result.get("success") else "FAILED"
        summary = result.get("summary", "")

        # Extract "Done:" portion
        done_text = ""
        if "Done: " in summary:
            done_text = summary.split("Done: ", 1)[1].strip()[:200]
        elif summary:
            done_text = summary[:200]

        files = result.get("files_created", [])
        file_names = [os.path.basename(f) for f in files[:4]]

        spec_output = t.get("expected_output", "")
        interfaces = t.get("interfaces", [])

        parts = [f"Task {i} [{status}]: {desc}"]
        if done_text:
            parts.append(f"  Result: {done_text}")
        if file_names:
            parts.append(f"  Files: {', '.join(file_names)}")
        if spec_output:
            parts.append(f"  Expected: {spec_output[:120]}")
        if interfaces:
            parts.append(f"  Interfaces: {', '.join(str(iface)[:60] for iface in interfaces[:3])}")

        lines.append("\n".join(parts))

    return "\n\n".join(lines)


def _read_file_contents(files: List[str]) -> str:
    """Read and truncate file contents for the integration prompt."""
    blocks = []
    total_chars = 0
    max_total = 6000  # Keep prompt from getting too long

    for fpath in files[:6]:
        if total_chars >= max_total:
            break
        try:
            if not os.path.isfile(fpath):
                continue
            size = os.path.getsize(fpath)
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(1500)
            fname = os.path.basename(fpath)
            block = f"--- {fname} ({size} bytes) ---\n{content}"
            blocks.append(block)
            total_chars += len(block)
        except Exception as e:
            logger.debug("Integrator: couldn't read file %s: %s", fpath, e)
            continue

    return "\n\n".join(blocks) if blocks else "(no files to review)"


def _single_task_summary(task: Dict[str, Any], files: List[str]) -> str:
    """Generate a summary for a single-task goal (no model call needed)."""
    result = task.get("result", {})
    summary = result.get("summary", "")

    if "Done: " in summary:
        done_text = summary.split("Done: ", 1)[1].strip()
        if len(done_text) > 20:
            return done_text[:300]

    file_names = [os.path.basename(f) for f in files[:4]]
    desc = task.get("description", "")[:100]
    if file_names:
        return f"Completed: {desc}. Files: {', '.join(file_names)}"
    return f"Completed: {desc}"


def _fallback_summary(tasks: List[Dict[str, Any]], files: List[str]) -> str:
    """Deterministic fallback summary when model call fails."""
    successful = [t for t in tasks if t.get("result", {}).get("success")]
    file_names = [os.path.basename(f) for f in files[:5]]

    if file_names:
        return f"Completed {len(successful)} tasks. Files created: {', '.join(file_names)}"
    return f"Completed {len(successful)} tasks."


def _empty_result() -> Dict[str, Any]:
    """Return an empty integration result."""
    return {
        "summary": "",
        "issues_found": [],
        "glue_created": [],
        "cost": 0,
    }
