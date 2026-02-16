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
from src.core.learning_system import LearningSystem
from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# Goal hygiene constants
MAX_ACTIVE_GOALS = 25
SUGGEST_COOLDOWN_SECS = 3600  # 1 hour between suggestion prompts


def _get_active_project_names(identity: dict) -> List[str]:
    """Return a flat list of active project names/paths from identity config."""
    projects = identity.get("user_context", {}).get("active_projects", {})
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
    for p in identity.get("user_context", {}).get("current_projects", []):
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


def is_goal_relevant(description: str, identity: dict) -> bool:
    """Check if a goal connects to an active project or user interest.

    Returns True if the goal references something Jesse actually cares about.
    Goals that are vague busywork (not tied to a project or interest) fail.
    """
    desc_lower = description.lower()
    project_names = _get_active_project_names(identity)

    for name in project_names:
        if name in desc_lower:
            return True

    interests = identity.get("user_context", {}).get("interests", [])
    for interest in interests:
        words = [w for w in interest.lower().split() if len(w) > 3]
        matches = sum(1 for w in words if w in desc_lower)
        if matches >= 2:
            return True

    if "workspace/" in desc_lower or ".md" in desc_lower or ".py" in desc_lower:
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
    for gid, g in goal_manager.goals.items():
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
        del goal_manager.goals[gid]
    if to_remove:
        goal_manager.save_state()
        logger.info("Pruned %d stale goals: %s", len(to_remove), to_remove)
    return len(to_remove)


def suggest_work(
    router: Any,
    goal_manager: Optional[GoalManager],
    learning_system: LearningSystem,
    identity: dict,
    last_suggest: Optional[datetime],
    stop_flag: Any,
    memory: Any = None,
) -> tuple:
    """Brainstorm work ideas and return them for the user to choose from.

    Unlike the old brainstorm_ideas(), this NEVER creates goals or auto-approves.
    It just generates ideas, filters them, saves to the backlog, and returns
    the best ones for the caller to present to the user via Discord.

    Cooldown: at most once per SUGGEST_COOLDOWN_SECS.

    Returns:
        (ideas_list, updated_last_suggest_timestamp)
        ideas_list is a list of dicts with description, category, reasoning, score.
        May be empty if cooldown not met, no router, or no good ideas found.
    """
    now = datetime.now()

    # Cooldown check
    if last_suggest and (now - last_suggest).total_seconds() < SUGGEST_COOLDOWN_SECS:
        return [], last_suggest

    if not router or not goal_manager:
        return [], last_suggest

    if stop_flag.is_set():
        return [], last_suggest

    logger.info("=== SUGGEST WORK START ===")

    # Prune stale goals first
    prune_stale_goals(goal_manager)

    # Load focus areas and context
    focus_areas = identity.get("focus_areas", [])
    if not focus_areas:
        focus_areas = ["Health", "Wealth", "Happiness", "Capability"]

    existing_goals = [
        g.description for g in goal_manager.goals.values()
        if not g.is_complete()
    ]
    existing_block = ""
    if existing_goals:
        existing_block = "\n\nCurrent active goals (avoid duplicates):\n" + "\n".join(
            f"- {g}" for g in existing_goals[:10]
        )

    # Inject lessons learned
    lessons_block = ""
    try:
        insights = learning_system.get_active_insights(3)
        action_summary = learning_system.get_action_summary()
        if insights or action_summary:
            parts = []
            if insights:
                parts.extend(f"- {i}" for i in insights)
            if action_summary:
                parts.append(f"- Tool reliability: {action_summary}")
            lessons_block = "\n\nLessons from past work:\n" + "\n".join(parts)
    except Exception:
        pass

    # Include recently completed goals
    completed_block = ""
    try:
        completed_summaries = _get_completed_goal_summaries(goal_manager)
        if completed_summaries:
            completed_block = "\n\nRecently completed work (DO NOT duplicate these):\n" + "\n".join(
                f"- {s}" for s in completed_summaries[-8:]
            )
    except Exception:
        pass

    # Include existing reports
    reports_block = ""
    try:
        existing_reports = _get_existing_reports()
        if existing_reports:
            reports_block = "\n\nExisting reports in workspace/reports/ (DO NOT recreate):\n" + "\n".join(
                f"- {r}" for r in existing_reports[:15]
            )
    except Exception:
        pass

    # Query long-term memory for previously researched topics
    memory_block = ""
    if memory:
        try:
            _all_topics = set()
            for fa in focus_areas[:4]:
                results = memory.retrieve_relevant(fa, n_results=3)
                for m in results.get("semantic", []):
                    if m.get("distance", 2.0) < 1.0:
                        _topic = m.get("metadata", {}).get("task_description", m["text"][:80])
                        _all_topics.add(_topic.strip())
            if _all_topics:
                memory_block = (
                    "\n\nPreviously researched topics (DO NOT repeat — "
                    "build on existing work or explore NEW angles):\n"
                    + "\n".join(f"- {t[:100]}" for t in list(_all_topics)[:12])
                )
                logger.info("Suggest: injected %d prior research topics from memory", len(_all_topics))
        except Exception as me:
            logger.debug("Memory query for suggest skipped: %s", me)

    # Active projects with file paths
    projects_block = ""
    try:
        active_projects = identity.get("user_context", {}).get("active_projects", {})
        if active_projects:
            parts = []
            for key, val in active_projects.items():
                if isinstance(val, dict):
                    parts.append(f"- {val.get('description', key)}: {val.get('path', '')}")
                    tasks = val.get("autonomous_tasks", [])
                    for t in tasks[:3]:
                        parts.append(f"  - {t}")
            if parts:
                projects_block = "\n\nJesse's active projects (ideas MUST connect to one of these):\n" + "\n".join(parts)
        current = identity.get("user_context", {}).get("current_projects", [])
        if current and not projects_block:
            projects_block = "\n\nJesse's current projects (ideas MUST connect to one of these):\n" + "\n".join(
                f"- {p}" for p in current
            )
    except Exception:
        pass

    prompt = f"""You are Archi, an autonomous AI agent working on Jesse's projects.

Jesse's interests:
{chr(10).join('- ' + fa for fa in focus_areas)}
{projects_block}

Your capabilities: web research, creating/updating files, analyzing data, organizing information.
You CANNOT: spend money, contact people, install software, or access external accounts.
{existing_block}{lessons_block}{completed_block}{reports_block}{memory_block}

Generate 3-5 ideas for work you could do right now.

PURPOSE-DRIVEN GOALS: Every idea must produce a CONCRETE CHANGE in a project — not
just standalone research. Research is a means, not an end. Ask: "What does the project
look like when this is DONE?" If the answer is just "a report exists," the idea is bad.

BAD ideas (research as the end product):
- "Research creatine timing studies"
- "Compile a report on sleep optimization supplements"

GOOD ideas (research serves a concrete change):
- "Update workspace/projects/Health_Optimization/supplements.md with latest creatine timing evidence"
- "Create a comparison table in Health_Optimization/stack_risks.md identifying contradictions"

CRITICAL RULES:
1. Every idea MUST connect to one of Jesse's active projects above
2. Every idea MUST name a specific file path to create or update (workspace/projects/...)
3. The description must include a DELIVERABLE VERB: update, add, create, extend, synthesize, build
4. DO NOT recreate existing reports (see list above)
5. DO NOT generate ideas similar to recently completed work

Return ONLY a JSON array:
[
  {{
    "category": "Health|Wealth|Happiness|Capability|Agency|Synthesis",
    "description": "Action-oriented task that changes a project file",
    "end_state": "What the project looks like when this is done",
    "target_file": "workspace/projects/ProjectName/filename.ext",
    "benefit": 1-10,
    "estimated_hours": 0.1-2.0,
    "reasoning": "Why this moves the project forward",
    "project_link": "Which active project this connects to"
  }}
]
JSON only:"""

    try:
        resp = router.generate(
            prompt=prompt, max_tokens=800, temperature=0.7,
        )
        text = resp.get("text", "")

        from src.utils.parsing import extract_json_array
        ideas = extract_json_array(text)

        if not isinstance(ideas, list) or not ideas:
            logger.warning("Suggest produced no valid ideas")
            return [], now

        # Score by benefit per hour
        scored = []
        for idea in ideas:
            if not isinstance(idea, dict):
                continue
            benefit = idea.get("benefit", 5)
            hours = max(idea.get("estimated_hours", 1), 0.1)
            score = benefit / hours
            idea["score"] = round(score, 1)
            scored.append(idea)

        scored.sort(key=lambda x: x["score"], reverse=True)

        # Save all ideas to backlog
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

        # Filter to only relevant, non-duplicate, purpose-driven ideas
        filtered = []
        for candidate in scored:
            desc = candidate.get("description", "")
            if not desc:
                continue
            if is_duplicate_goal(desc, goal_manager):
                logger.info("Suggest idea skipped (duplicate): %s", desc[:60])
                continue
            if not is_goal_relevant(desc, identity):
                logger.info("Suggest idea skipped (not relevant): %s", desc[:60])
                continue
            if not is_purpose_driven(desc):
                logger.info("Suggest idea skipped (not purpose-driven): %s", desc[:60])
                continue
            if memory:
                try:
                    _mem_results = memory.retrieve_relevant(desc, n_results=2)
                    _sem = _mem_results.get("semantic", [])
                    if any(m.get("distance", 2.0) < 0.5 for m in _sem):
                        logger.info("Suggest idea skipped (already researched): %s", desc[:60])
                        continue
                except Exception:
                    pass
            filtered.append(candidate)

        logger.info(
            "=== SUGGEST WORK END (%d ideas, %d after filtering) ===",
            len(scored), len(filtered),
        )

        return filtered[:5], now

    except Exception as e:
        logger.error("Suggest work failed: %s", e, exc_info=True)
        return [], now
