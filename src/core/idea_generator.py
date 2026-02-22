"""
Idea Generator — Work suggestion and goal hygiene for dream cycles.

When Archi is idle with no active goals, this module brainstorms ideas
and presents them to the user via Discord. It never auto-approves or
creates goals on its own — the user always decides.

Also provides goal hygiene utilities: dedup, pruning, relevance checks.
Split from dream_cycle.py in session 11. Reworked in session 31.
"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.goal_manager import GoalManager, TaskStatus
from src.core.idea_history import IdeaHistory, get_idea_history
from src.core.learning_system import LearningSystem
from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# Goal hygiene constants
MAX_ACTIVE_GOALS = 25
SUGGEST_COOLDOWN_SECS = 600  # 10 minutes between suggestion prompts (was 1 hour)

# Module-level state for cache invalidation
_last_brainstorm_prompt: str = ""


def _get_active_project_names(project_context: dict) -> List[str]:
    """Return a flat list of active project names/paths from project context."""
    projects = project_context.get("active_projects", {})
    names = []
    for key, val in projects.items():
        names.append(key.lower().replace("_", " "))
        if isinstance(val, dict):
            desc = val.get("description", "")
            if desc:
                names.append(desc.lower())
            path = val.get("path", "")
            if path:
                names.append(path.lower())
    for p in project_context.get("current_projects", []):
        names.append(p.lower())
    return names


def _get_existing_reports() -> List[str]:
    """Scan workspace/reports/ and return a list of existing report filenames."""
    reports_dir = _base_path() / "workspace" / "reports"
    if not reports_dir.exists():
        return []
    try:
        return [f.name for f in reports_dir.iterdir() if f.is_file()][:30]
    except Exception:
        return []


def _get_completed_goal_summaries(goal_manager: Optional[GoalManager]) -> List[str]:
    """Return short descriptions of recently completed goals."""
    if not goal_manager:
        return []
    completed = [
        g.description[:100]
        for g in goal_manager.goals.values()
        if g.is_complete()
    ]
    return completed[-15:]


def is_goal_relevant(description: str, project_context: dict) -> bool:
    """Check if a goal connects to an active project, user interest, or self-improvement.

    Returns True if the goal references something Jesse actually cares about.
    Goals that are vague busywork (not tied to a project or interest) fail.
    """
    desc_lower = description.lower()

    # Self-improvement goals (fixing/improving Archi's own code) are always relevant
    _SELF_IMPROVEMENT_SIGNALS = (
        "fix ", "patch ", "debug ", "refactor", "improve ", "optimize ",
        "src/", "discord_bot", "plan_executor", "goal_manager", "dream_cycle",
        "task_orchestrator", "agent_loop", "logging", "error handling",
    )
    if any(sig in desc_lower for sig in _SELF_IMPROVEMENT_SIGNALS):
        return True

    # File paths are always relevant (concrete work)
    if "workspace/" in desc_lower or ".md" in desc_lower or ".py" in desc_lower:
        return True

    # Word-level matching against project names, descriptions, and paths.
    # Extract meaningful words (>3 chars) from all project metadata.
    project_names = _get_active_project_names(project_context)
    project_words = set()
    for name in project_names:
        project_words.update(w for w in name.lower().split("/") if len(w) > 3)
        project_words.update(w for w in name.lower().replace("_", " ").split() if len(w) > 3)
    # Also pull words from focus_areas inside each project
    for _key, val in project_context.get("active_projects", {}).items():
        if isinstance(val, dict):
            for fa in val.get("focus_areas", []):
                project_words.update(w for w in fa.lower().split() if len(w) > 3)

    desc_words = set(desc_lower.split())
    if project_words and len(project_words & desc_words) >= 1:
        return True

    # Interest matching (word overlap)
    interests = project_context.get("interests", [])
    for interest in interests:
        words = [w for w in interest.lower().split() if len(w) > 3]
        matches = sum(1 for w in words if w in desc_lower)
        if matches >= 1:
            return True

    return False


# Verbs that indicate a goal produces a concrete change, not just research
_DELIVERABLE_VERBS = {
    "update", "add", "create", "extend", "synthesize", "build", "integrate",
    "consolidate", "restructure", "write", "revise", "merge", "append",
    "generate", "draft", "compile", "organize", "refactor",
}


def is_purpose_driven(description: str) -> bool:
    """Check if a goal description has a concrete purpose beyond standalone research.

    Returns True if the goal references a deliverable verb AND a workspace path
    (or at least a file extension like .md, .py, .json).
    """
    desc_lower = description.lower()

    has_path = (
        "workspace/" in desc_lower
        or ".md" in desc_lower
        or ".py" in desc_lower
        or ".json" in desc_lower
        or ".txt" in desc_lower
        or ".csv" in desc_lower
    )

    words = set(desc_lower.split())
    has_verb = bool(words & _DELIVERABLE_VERBS)

    return has_path and has_verb


def is_duplicate_goal(description: str, goal_manager: Optional[GoalManager]) -> bool:
    """Fuzzy duplicate detection for goal descriptions.

    Catches: exact matches, substring containment, and high word overlap.
    Checks BOTH active AND completed goals to avoid re-researching topics.
    """
    if not goal_manager:
        return False
    desc_lower = description.lower().strip()
    desc_words = set(desc_lower.split())
    _STOP = {"a", "an", "the", "and", "or", "to", "for", "in", "of", "on", "with", "is", "by"}
    desc_sig = desc_words - _STOP

    for g in goal_manager.goals.values():
        existing = g.description.lower().strip()
        if desc_lower == existing:
            return True
        if desc_lower in existing or existing in desc_lower:
            return True
        existing_words = set(existing.split()) - _STOP
        if desc_sig and existing_words:
            overlap = len(desc_sig & existing_words)
            union = len(desc_sig | existing_words)
            if union > 0 and overlap / union > 0.6:
                return True
    return False


def count_active_goals(goal_manager: Optional[GoalManager]) -> int:
    """Count goals that are not complete."""
    if not goal_manager:
        return 0
    return sum(1 for g in goal_manager.goals.values() if not g.is_complete())


def prune_stale_goals(goal_manager: Optional[GoalManager]) -> int:
    """Remove old undecomposed or all-failed goals to keep the list manageable.

    Returns number of goals pruned.
    """
    if not goal_manager:
        return 0
    now = datetime.now()
    to_remove = []
    # Snapshot to avoid iterating while another thread mutates
    for gid, g in list(goal_manager.goals.items()):
        if g.is_complete():
            continue
        age_hours = (now - g.created_at).total_seconds() / 3600
        if not g.is_decomposed and age_hours > 48:
            to_remove.append(gid)
        elif g.is_decomposed and g.tasks and all(
            t.status == TaskStatus.FAILED for t in g.tasks
        ):
            to_remove.append(gid)
    for gid in to_remove:
        goal_manager.remove_goal(gid)
    if to_remove:
        goal_manager.save_state()
        logger.info("Pruned %d stale goals: %s", len(to_remove), to_remove)
    return len(to_remove)


def suggest_work(
    router: Any,
    goal_manager: Optional[GoalManager],
    learning_system: LearningSystem,
    project_context: dict,
    last_suggest: Optional[datetime],
    stop_flag: Any,
    memory: Any = None,
    cooldown_secs: Optional[int] = None,
) -> tuple:
    """Brainstorm work ideas and return them for the user to choose from.

    Unlike the old brainstorm_ideas(), this NEVER creates goals or auto-approves.
    It just generates ideas, filters them, saves to the backlog, and returns
    the best ones for the caller to present to the user via Discord.

    Cooldown: at most once per cooldown_secs (defaults to SUGGEST_COOLDOWN_SECS).

    Returns:
        (ideas_list, updated_last_suggest_timestamp)
        ideas_list is a list of dicts with description, category, reasoning, score.
        May be empty if cooldown not met, no router, or no good ideas found.
    """
    now = datetime.now()
    effective_cooldown = cooldown_secs if cooldown_secs is not None else SUGGEST_COOLDOWN_SECS

    # Cooldown check
    if last_suggest and (now - last_suggest).total_seconds() < effective_cooldown:
        return [], last_suggest

    if not router or not goal_manager:
        return [], last_suggest

    if stop_flag.is_set():
        return [], last_suggest

    logger.info("=== SUGGEST WORK START ===")

    # Prune stale goals first
    prune_stale_goals(goal_manager)

    # --- Opportunity Scanner (session 42) ---
    # Scans actual project files, error logs, unused capabilities, and user
    # context to find real, typed opportunities. Falls back to the old
    # brainstorm prompt if the scanner fails or returns nothing.
    scored = []
    try:
        from src.core.opportunity_scanner import scan_all
        opportunities = scan_all(
            project_context=project_context,
            router=router,
            goal_manager=goal_manager,
            memory=memory,
        )
        if opportunities:
            for opp in opportunities:
                scored.append({
                    "category": _opportunity_type_to_category(opp.type),
                    "description": opp.description,
                    "end_state": opp.user_value or f"Complete: {opp.description}",
                    "target_file": opp.target_files[0] if opp.target_files else "workspace/",
                    "benefit": opp.value_score,
                    "estimated_hours": opp.estimated_hours,
                    "reasoning": opp.reasoning,
                    "project_link": opp.source,
                    "score": round(opp.value_score / max(opp.estimated_hours, 0.1), 1),
                    "opportunity_type": opp.type,
                })
            scored.sort(key=lambda x: x["score"], reverse=True)
            logger.info("Opportunity scanner produced %d ideas", len(scored))
    except Exception as scan_err:
        logger.warning("Opportunity scanner failed, falling back to brainstorm: %s", scan_err)

    # Fallback: old brainstorm if scanner returned nothing
    if not scored:
        scored = _brainstorm_fallback(
            router, goal_manager, learning_system, project_context, memory,
        )

    if not scored:
        logger.warning("Suggest produced no valid ideas (scanner + fallback)")
        return [], now

    # Save all ideas to backlog
    _save_to_backlog(scored, now)

    # Filter ideas, retrying with feedback if all are rejected
    idea_history = get_idea_history()
    filtered = _filter_ideas(scored, goal_manager, project_context, memory, idea_history)

    # Retry loop: if all ideas were filtered, invalidate cache and retry
    # with rejection context, up to MAX_BRAINSTORM_RETRIES times.
    # On final retry, escalate to Claude for a more creative pass.
    retry = 0
    while not filtered and scored and retry < MAX_BRAINSTORM_RETRIES:
        retry += 1
        logger.info(
            "All ideas filtered (attempt %d/%d) — retrying with rejection context",
            retry, MAX_BRAINSTORM_RETRIES,
        )

        # Invalidate the cached brainstorm response so we get fresh output
        _invalidate_brainstorm_cache(router)

        # On final retry, escalate to Claude if available
        escalate_ctx = None
        if retry == MAX_BRAINSTORM_RETRIES and hasattr(router, 'escalate_for_task'):
            try:
                escalate_ctx = router.escalate_for_task("claude-sonnet-4.6")
                escalate_ctx.__enter__()
                logger.info("Brainstorm retry %d: escalated to Claude", retry)
            except Exception:
                escalate_ctx = None

        try:
            scored = _brainstorm_fallback(
                router, goal_manager, learning_system, project_context, memory,
            )
            if scored:
                _save_to_backlog(scored, now)
                filtered = _filter_ideas(scored, goal_manager, project_context, memory, idea_history)
        finally:
            if escalate_ctx is not None:
                try:
                    escalate_ctx.__exit__(None, None, None)
                except Exception:
                    pass

        if stop_flag.is_set():
            break

    logger.info(
        "=== SUGGEST WORK END (%d ideas, %d after filtering, %d retries) ===",
        len(scored), len(filtered), retry,
    )

    return filtered[:5], now


# Maximum brainstorm retries when all ideas are filtered
MAX_BRAINSTORM_RETRIES = 2


def _save_to_backlog(scored: List[Dict], now: datetime) -> None:
    """Persist scored ideas to the idea backlog file."""
    try:
        backlog_path = _base_path() / "data" / "idea_backlog.json"
        backlog = {"ideas": [], "last_suggest": now.isoformat()}
        if backlog_path.exists():
            try:
                with open(backlog_path, "r", encoding="utf-8") as f:
                    backlog = json.load(f)
            except Exception:
                pass

        for idea in scored:
            backlog.setdefault("ideas", []).append({
                **idea,
                "created_at": now.isoformat(),
                "status": "suggested",
            })
        backlog["last_suggest"] = now.isoformat()

        with open(backlog_path, "w", encoding="utf-8") as f:
            json.dump(backlog, f, indent=2)
    except Exception as e:
        logger.debug("Backlog save failed: %s", e)


def _filter_ideas(
    scored: List[Dict],
    goal_manager: Optional[GoalManager],
    project_context: dict,
    memory: Any,
    idea_history: IdeaHistory,
) -> List[Dict]:
    """Filter ideas for relevance, dedup, purpose, staleness.

    Records rejections in idea_history for future context.
    """
    # Cold-start detection: if there are no active projects and no interests,
    # we have nothing to judge relevance against.  Let ideas through so the
    # user can pick what they actually want (which seeds future context).
    cold_start = (
        not project_context.get("active_projects")
        and not project_context.get("interests")
    )
    if cold_start:
        logger.info("Cold start detected — relaxing relevance/purpose filters")

    filtered = []
    for candidate in scored:
        desc = candidate.get("description", "")
        cat = candidate.get("category", "")
        if not desc:
            continue
        if is_duplicate_goal(desc, goal_manager):
            logger.info("Suggest idea skipped (duplicate): %s", desc[:60])
            idea_history.record_auto_filtered(desc, "duplicate goal", cat)
            continue
        # Scanner-sourced ideas already passed project-level relevance checks
        # inside scan_projects(), so skip the word-overlap filter for them.
        from_scanner = bool(candidate.get("opportunity_type"))
        if not cold_start and not from_scanner and not is_goal_relevant(desc, project_context):
            logger.info("Suggest idea skipped (not relevant): %s", desc[:60])
            idea_history.record_auto_filtered(desc, "not relevant", cat)
            continue
        if not cold_start and not from_scanner and not is_purpose_driven(desc):
            logger.info("Suggest idea skipped (not purpose-driven): %s", desc[:60])
            idea_history.record_auto_filtered(desc, "not purpose-driven", cat)
            continue
        if memory:
            try:
                _mem_results = memory.retrieve_relevant(desc, n_results=2)
                _sem = _mem_results.get("semantic", [])
                if any(m.get("distance", 2.0) < 0.5 for m in _sem):
                    logger.info("Suggest idea skipped (already researched): %s", desc[:60])
                    idea_history.record_auto_filtered(desc, "already researched", cat)
                    continue
            except Exception:
                pass
        # Check idea history — skip if tried before and never accepted
        history_match = idea_history.is_stale(desc)
        if history_match:
            times = idea_history.times_rejected(desc)
            logger.info(
                "Suggest idea skipped (stale — rejected %dx): %s",
                times, desc[:60],
            )
            idea_history.record_auto_filtered(desc, f"stale (rejected {times}x previously)", cat)
            continue
        filtered.append(candidate)
    return filtered


def _invalidate_brainstorm_cache(router: Any) -> None:
    """Invalidate the cached brainstorm response so next call hits the LLM fresh."""
    try:
        if _last_brainstorm_prompt and hasattr(router, '_cache'):
            if hasattr(router._cache, 'invalidate'):
                router._cache.invalidate(_last_brainstorm_prompt)
            else:
                router._cache.clear()
    except Exception:
        pass


def _opportunity_type_to_category(opp_type: str) -> str:
    """Map opportunity type to goal category for backward compatibility."""
    return {
        "build": "Capability",
        "ask": "Agency",
        "fix": "Resilience",
        "connect": "Agency",
        "improve": "Capability",
    }.get(opp_type, "Capability")


def _brainstorm_fallback(
    router: Any,
    goal_manager: Optional[GoalManager],
    learning_system: LearningSystem,
    project_context: dict,
    memory: Any = None,
) -> List[Dict]:
    """Legacy brainstorm prompt — used as fallback when scanner returns nothing.

    Preserved from the pre-session-42 suggest_work() implementation.
    """
    focus_areas = project_context.get("focus_areas", []) or ["Health", "Capability"]

    projects_block = ""
    try:
        from src.utils.project_context import scan_project_files
        active_projects = project_context.get("active_projects", {})
        if active_projects:
            parts = []
            for key, val in active_projects.items():
                if isinstance(val, dict):
                    path = val.get("path", "")
                    parts.append(f"- {val.get('description', key)}: {path}")
                    files = scan_project_files(path) if path else []
                    if files:
                        parts.append(f"  Files: {', '.join(files)}")
                    tasks = val.get("autonomous_tasks", [])
                    for t in tasks[:3]:
                        parts.append(f"  - {t}")
            if parts:
                projects_block = "\n\nJesse's active projects:\n" + "\n".join(parts)
    except Exception:
        pass

    # Build rejection context from idea history
    idea_history = get_idea_history()
    rejection_block = idea_history.get_rejection_context()
    accepted_block = idea_history.get_accepted_context()
    history_block = ""
    if rejection_block or accepted_block:
        parts = [p for p in (rejection_block, accepted_block) if p]
        history_block = "\n\n" + "\n\n".join(parts)

    prompt = f"""You are Archi, an autonomous AI agent working on Jesse's projects.
{projects_block}{history_block}

Generate 3-5 ideas for work you could do right now.
Every idea MUST produce a CONCRETE deliverable (code, data structure, tool) — NOT a report.
Every idea MUST name a specific file path to create (workspace/projects/...).
Every idea must be GENUINELY DIFFERENT from previously rejected ideas listed above.

Return ONLY a JSON array:
[{{"category": "Health|Capability", "description": "...", "target_file": "workspace/projects/...", "benefit": 1-10, "estimated_hours": 0.1-2.0, "reasoning": "..."}}]
JSON only:"""

    # Store prompt for cache invalidation if all ideas get filtered
    global _last_brainstorm_prompt
    _last_brainstorm_prompt = prompt

    try:
        resp = router.generate(prompt=prompt, max_tokens=4096, temperature=0.7)
        text = resp.get("text", "")
        logger.info("Brainstorm raw response (%d chars): %.500s", len(text), text)
        from src.utils.parsing import extract_json_array
        ideas = extract_json_array(text)
        if not isinstance(ideas, list):
            return []
        scored = []
        for idea in ideas:
            if not isinstance(idea, dict):
                continue
            benefit = idea.get("benefit", 5)
            hours = max(idea.get("estimated_hours", 1), 0.1)
            idea["score"] = round(benefit / hours, 1)
            scored.append(idea)
        scored.sort(key=lambda x: x["score"], reverse=True)
        logger.info("Brainstorm fallback produced %d ideas", len(scored))
        return scored
    except Exception as e:
        logger.debug("Brainstorm fallback failed: %s", e)
        return []
