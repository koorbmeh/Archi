"""
Idea Generator — Work suggestion and goal hygiene for heartbeat cycles.

When Archi is idle with no active goals, this module brainstorms ideas
and presents them to the user via Discord. It never auto-approves or
creates goals on its own — the user always decides.

Also provides goal hygiene utilities: dedup, pruning, relevance checks.
Split from dream_cycle.py (now heartbeat.py) in session 11. Reworked in session 31.
"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.goal_manager import GoalManager, TaskStatus
from src.core.idea_history import IdeaHistory, get_idea_history
from src.core.learning_system import LearningSystem
from src.utils.config import get_user_name
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

    Returns True if the goal references something the user actually cares about.
    Goals that are vague busywork (not tied to a project or interest) fail.
    """
    desc_lower = description.lower()

    # Self-improvement goals (fixing/improving Archi's own code) are always relevant
    _SELF_IMPROVEMENT_SIGNALS = (
        "fix ", "patch ", "debug ", "refactor", "improve ", "optimize ",
        "src/", "discord_bot", "plan_executor", "goal_manager", "heartbeat",
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

    # Interest matching (word overlap) — interests live in the user model
    try:
        from src.core.user_model import get_user_model
        interests = get_user_model().get_interests()
    except Exception:
        interests = []
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
    """Remove stale goals: old undecomposed, all-terminal, or empty zombies.

    Returns number of goals pruned.
    """
    if not goal_manager:
        return 0
    _terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED}
    now = datetime.now()
    to_remove = []
    # Snapshot to avoid iterating while another thread mutates
    for gid, g in list(goal_manager.goals.items()):
        if g.is_complete():
            continue
        age_hours = (now - g.created_at).total_seconds() / 3600
        if not g.is_decomposed and age_hours > 48:
            to_remove.append(gid)
        elif g.is_decomposed and not g.tasks and age_hours > 1:
            # Zombie: decomposed with 0 tasks (e.g. misrouted fast-path)
            to_remove.append(gid)
        elif g.is_decomposed and g.tasks and all(
            t.status in _terminal for t in g.tasks
        ):
            # All tasks terminal but not all completed — dead goal
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

    # Scanner pass → brainstorm fallback
    scored = _scan_for_opportunities(router, goal_manager, learning_system, project_context, memory)
    if not scored:
        logger.warning("Suggest produced no valid ideas (scanner + fallback)")
        return [], now

    # Save and filter, retrying if all ideas rejected
    _save_to_backlog(scored, now)
    idea_history = get_idea_history()
    filtered = _filter_ideas(scored, goal_manager, project_context, memory, idea_history)

    filtered, retry = _retry_filtered_ideas(
        filtered, scored, now, router, goal_manager, learning_system,
        project_context, memory, idea_history, stop_flag,
    )

    logger.info(
        "=== SUGGEST WORK END (%d ideas, %d after filtering, %d retries) ===",
        len(scored), len(filtered), retry,
    )

    return filtered[:5], now


def _scan_for_opportunities(
    router: Any,
    goal_manager: Optional[GoalManager],
    learning_system: LearningSystem,
    project_context: dict,
    memory: Any = None,
) -> List[Dict]:
    """Run opportunity scanner, fall back to brainstorm prompt if empty.

    Returns scored idea dicts sorted by score (highest first).
    """
    scored: List[Dict] = []
    try:
        from src.core.opportunity_scanner import scan_all
        opportunities = scan_all(
            project_context=project_context,
            router=router,
            goal_manager=goal_manager,
            memory=memory,
        )
        for opp in (opportunities or []):
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
        if scored:
            scored.sort(key=lambda x: x["score"], reverse=True)
            logger.info("Opportunity scanner produced %d ideas", len(scored))
    except Exception as scan_err:
        logger.warning("Opportunity scanner failed, falling back to brainstorm: %s", scan_err)

    if not scored:
        scored = _brainstorm_fallback(
            router, goal_manager, learning_system, project_context, memory,
        )
    return scored


def _retry_filtered_ideas(
    filtered: List[Dict],
    scored: List[Dict],
    now: "datetime",
    router: Any,
    goal_manager: Optional[GoalManager],
    learning_system: LearningSystem,
    project_context: dict,
    memory: Any,
    idea_history: "IdeaHistory",
    stop_flag: Any,
) -> tuple:
    """Retry brainstorm with rejection context if all ideas were filtered.

    On final retry, escalates to Claude for a more creative pass.

    Returns (filtered_ideas, retry_count).
    """
    retry = 0
    while not filtered and scored and retry < MAX_BRAINSTORM_RETRIES:
        retry += 1
        logger.info(
            "All ideas filtered (attempt %d/%d) — retrying with rejection context",
            retry, MAX_BRAINSTORM_RETRIES,
        )
        _invalidate_brainstorm_cache(router)

        # No Claude escalation — session 170 live test showed it doesn't
        # produce meaningfully better ideas (4/5 still filtered) and costs
        # $0.01-0.06 per cycle. Just retry with Grok + rejection context.
        scored = _brainstorm_fallback(
            router, goal_manager, learning_system, project_context, memory,
        )
        if scored:
            _save_to_backlog(scored, now)
            filtered = _filter_ideas(scored, goal_manager, project_context, memory, idea_history)

        if stop_flag.is_set():
            break
    return filtered, retry


# Maximum brainstorm retries when all ideas are filtered.
# Reduced from 2 to 1 (session 170): Claude escalation on retry 2 wasn't
# producing better ideas and cost $0.01-0.06 per cycle for nothing.
MAX_BRAINSTORM_RETRIES = 1


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


_LIFE_CATEGORIES = {
    "health", "puppy", "fitness", "finance", "personal", "wellness",
    "outdoor", "outdoors", "nature", "nutrition", "meal", "walking",
    "exercise", "self-improvement", "growth", "hobby",
}


def _is_life_category(candidate: Dict) -> bool:
    """Check if an idea is a life-content category (bypass strict filters)."""
    cat = (candidate.get("category", "") or "").lower()
    return any(lc in cat for lc in _LIFE_CATEGORIES)


def _filter_ideas(
    scored: List[Dict],
    goal_manager: Optional[GoalManager],
    project_context: dict,
    memory: Any,
    idea_history: IdeaHistory,
) -> List[Dict]:
    """Filter ideas for relevance, dedup, purpose, staleness.

    Records rejections in idea_history for future context.
    Life-category ideas bypass relevance/purpose filters since those were
    designed for dev work and incorrectly reject practical life content.
    """
    # Cold-start detection: if there are no active projects and no interests,
    # we have nothing to judge relevance against.  Let ideas through so the
    # user can pick what they actually want (which seeds future context).
    try:
        from src.core.user_model import get_user_model
        _interests = get_user_model().get_interests()
    except Exception:
        _interests = []
    cold_start = (
        not project_context.get("active_projects")
        and not _interests
    )
    if cold_start:
        logger.info("Cold start detected — relaxing relevance/purpose filters")

    filtered = []
    for candidate in scored:
        desc = candidate.get("description", "")
        cat = candidate.get("category", "")
        if not desc:
            continue
        # Combine description with target_file for filter checks — the model
        # often puts file paths in target_file rather than the description text.
        target_file = candidate.get("target_file", "")
        desc_for_filters = f"{desc} {target_file}" if target_file else desc
        if is_duplicate_goal(desc, goal_manager):
            logger.info("Suggest idea skipped (duplicate): %s", desc[:60])
            idea_history.record_auto_filtered(desc, "duplicate goal", cat)
            continue
        # Life-category ideas bypass relevance/purpose filters — these filters
        # were designed for dev work and incorrectly reject practical life content.
        life_cat = _is_life_category(candidate)
        # Scanner-sourced ideas already passed project-level relevance checks
        # inside scan_projects(), so skip the word-overlap filter for them.
        from_scanner = bool(candidate.get("opportunity_type"))
        if not cold_start and not from_scanner and not life_cat and not is_goal_relevant(desc_for_filters, project_context):
            logger.info("Suggest idea skipped (not relevant): %s", desc[:60])
            idea_history.record_auto_filtered(desc, "not relevant", cat)
            continue
        if not cold_start and not from_scanner and not life_cat and not is_purpose_driven(desc_for_filters):
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


def _build_user_context_block() -> str:
    """Build personalized user context (facts + preferences + suggestion style) for brainstorm prompt."""
    try:
        from src.core.user_model import get_user_model
        um = get_user_model()
        parts = []
        # Suggestion style is the most important signal — put it first
        suggestion_ctx = um.get_suggestion_context()
        if suggestion_ctx:
            parts.append(suggestion_ctx)
        if um.facts:
            parts.append(f"About {get_user_name()}:")
            for f in um.facts[-10:]:
                parts.append(f"  - {f.get('text', '')}")
        if um.preferences:
            parts.append(f"{get_user_name()}'s stated preferences:")
            for p in um.preferences[-5:]:
                parts.append(f"  - {p.get('text', '')}")
        return "\n" + "\n".join(parts) if parts else ""
    except Exception:
        return ""


def _build_projects_block(project_context: dict) -> str:
    """Build active projects summary for brainstorm prompt."""
    try:
        from src.utils.project_context import scan_project_files
        active_projects = project_context.get("active_projects", {})
        if not active_projects:
            return ""
        parts = []
        for key, val in active_projects.items():
            if not isinstance(val, dict):
                continue
            path = val.get("path", "")
            parts.append(f"- {val.get('description', key)}: {path}")
            files = scan_project_files(path) if path else []
            if files:
                parts.append(f"  Files: {', '.join(files)}")
            for t in val.get("autonomous_tasks", [])[:3]:
                parts.append(f"  - {t}")
        return f"\n\n{get_user_name()}'s active projects:\n" + "\n".join(parts) if parts else ""
    except Exception:
        return ""


def _score_brainstorm_ideas(text: str) -> List[Dict]:
    """Parse brainstorm model response and compute benefit/hour scores."""
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
    return scored


def _brainstorm_fallback(
    router: Any,
    goal_manager: Optional[GoalManager],
    learning_system: LearningSystem,
    project_context: dict,
    memory: Any = None,
) -> List[Dict]:
    """Legacy brainstorm prompt — used as fallback when scanner returns nothing."""
    user_context_block = _build_user_context_block()
    projects_block = _build_projects_block(project_context)

    # Build rejection context from idea history
    idea_history = get_idea_history()
    rejection_block = idea_history.get_rejection_context()
    accepted_block = idea_history.get_accepted_context()
    history_parts = [p for p in (rejection_block, accepted_block) if p]
    history_block = "\n\n" + "\n\n".join(history_parts) if history_parts else ""

    prompt = f"""You are Archi, an autonomous AI agent working for {get_user_name()}.
{user_context_block}{projects_block}{history_block}

Generate 3-5 ideas for work you could do right now that {get_user_name()} would ACTUALLY WANT.

WHAT {get_user_name().upper()} ACTUALLY ACCEPTS (learn from this pattern):
- Practical life content: stretch routines, walking routes, puppy training plans, meal plans,
  health tips, fitness guides, personal growth content, local recommendations
- Things he can USE in his daily life — not code projects or developer tools

WHAT {get_user_name().upper()} ALWAYS REJECTS (never suggest these):
- Developer tooling (pytest, coverage, linting, CI/CD, package installs)
- Code refactoring, API tools, monitoring dashboards, automation scripts
- Anything that sounds like it's for a software engineer, not a person

REQUIREMENTS:
1. Description MUST include a deliverable verb (create, draft, compile, write, build, generate)
   AND a target file path (workspace/... or ending in .md/.json/.html).
2. Description MUST connect to {get_user_name()}'s real life — his interests, his puppy, his
   health, his daily routine, his hobbies, or his personal goals.
3. Every idea MUST be GENUINELY DIFFERENT from previously rejected ideas listed above.
4. Describe ideas from the USER'S perspective — what they GET, not how it's built. NO shell
   commands (pip, pytest, npm, etc.), tool names, or implementation details in descriptions.
5. At least 3 of 5 ideas should be PRACTICAL LIFE content. At most 1 may be a technical tool
   IF it directly serves a personal interest (e.g., a budget tracker, not a code linter).

Return ONLY a JSON array:
[{{"category": "Health|Puppy|Fitness|Finance|Personal", "description": "...", "target_file": "workspace/projects/...", "benefit": 1-10, "estimated_hours": 0.1-2.0, "reasoning": "..."}}]
JSON only:"""

    global _last_brainstorm_prompt
    _last_brainstorm_prompt = prompt

    try:
        resp = router.generate(prompt=prompt, max_tokens=4096, temperature=0.7)
        text = resp.get("text", "")
        logger.info("Brainstorm raw response (%d chars): %.500s", len(text), text)
        scored = _score_brainstorm_ideas(text)
        logger.info("Brainstorm fallback produced %d ideas", len(scored))
        return scored
    except Exception as e:
        logger.debug("Brainstorm fallback failed: %s", e)
        return []


# ── Adaptive retirement (session 199) ───────────────────────────────

def check_retirement_candidates() -> List[Dict[str, Any]]:
    """Check for scheduled tasks that are consistently ignored and propose retirement.

    User-created tasks: returned as proposals (caller notifies Jesse).
    Archi-created tasks: disabled silently, returned as notifications.

    Returns a list of dicts with:
        task_id, description, action ("propose" or "retired"), ignore_rate, created_by
    """
    try:
        from src.core import scheduler
    except ImportError:
        return []

    ignored = scheduler.get_ignored_tasks()
    if not ignored:
        return []

    results = []
    for task in ignored:
        total = task.stats.times_acknowledged + task.stats.times_ignored
        rate = task.stats.times_ignored / total if total > 0 else 0

        if task.created_by == "archi":
            # Auto-retire Archi-created tasks silently
            scheduler.modify_task(task.id, enabled=False)
            results.append({
                "task_id": task.id,
                "description": task.description,
                "action": "retired",
                "ignore_rate": rate,
                "created_by": "archi",
            })
            logger.info(
                "Auto-retired ignored task '%s' (%.0f%% ignore rate)",
                task.id, rate * 100,
            )
        else:
            # Propose retirement to user
            results.append({
                "task_id": task.id,
                "description": task.description,
                "action": "propose",
                "ignore_rate": rate,
                "created_by": "user",
            })
            logger.info(
                "Proposing retirement for ignored task '%s' (%.0f%% ignore rate)",
                task.id, rate * 100,
            )

    return results


def format_retirement_message(candidates: List[Dict[str, Any]]) -> str:
    """Format retirement candidates into a conversational Discord message."""
    retired = [c for c in candidates if c["action"] == "retired"]
    proposed = [c for c in candidates if c["action"] == "propose"]

    parts = []
    if retired:
        names = ", ".join(f"**{c['description']}**" for c in retired)
        parts.append(
            f"I noticed I keep sending reminders that go unanswered, so I turned off: {names}."
        )
    if proposed:
        for c in proposed:
            parts.append(
                f"Your reminder **{c['description']}** has been ignored "
                f"{c['ignore_rate']:.0%} of the time. Want me to turn it off?"
            )
    return " ".join(parts)


# ── Autonomous scheduling (session 199) ──────────────────────────────

_SCHEDULE_PROPOSE_COOLDOWN = 86400  # Once per day at most
_last_schedule_propose: Optional[float] = None


def suggest_scheduled_tasks(router: Any) -> List[Dict[str, Any]]:
    """Analyze recent activity for recurring patterns and propose scheduled tasks.

    Called from heartbeat (every 10 cycles, offset by 7). Looks at:
    - Journal entries for repeated task types at similar times
    - Conversation patterns (similar requests at regular intervals)

    Returns list of proposals:
        [{"task_id": ..., "description": ..., "cron": ..., "action": ...,
          "payload": ..., "reasoning": ..., "notify_user": True/False}]
    """
    global _last_schedule_propose

    # Cooldown — at most once per day
    now = time.time()
    if _last_schedule_propose and (now - _last_schedule_propose) < _SCHEDULE_PROPOSE_COOLDOWN:
        return []
    _last_schedule_propose = now

    if not router:
        return []

    # Gather evidence from journal + conversations
    evidence = _gather_scheduling_evidence()
    if not evidence:
        return []

    # Check existing schedules to avoid duplicates
    existing = _get_existing_schedule_descriptions()

    proposals = _model_schedule_proposal(router, evidence, existing)
    return proposals


def _gather_scheduling_evidence() -> str:
    """Collect recent activity for pattern analysis."""
    parts = []

    # Journal: last 7 days of task completions and conversations
    try:
        from src.core.journal import get_recent_entries
        entries = get_recent_entries(days=7)
        if entries:
            task_entries = [e for e in entries if e.get("type") == "task_completed"][:20]
            conv_entries = [e for e in entries if e.get("type") == "conversation"][:15]

            if task_entries:
                task_lines = []
                for e in task_entries:
                    time_str = e.get("time", "")
                    task_lines.append(f"  [{time_str}] {e.get('content', '')[:100]}")
                parts.append("Recent tasks:\n" + "\n".join(task_lines))

            if conv_entries:
                conv_lines = []
                for e in conv_entries:
                    time_str = e.get("time", "")
                    conv_lines.append(f"  [{time_str}] {e.get('content', '')[:100]}")
                parts.append("Recent conversations:\n" + "\n".join(conv_lines))
    except Exception as e:
        logger.debug("Journal unavailable for schedule suggestion: %s", e)

    # Conversations log: look for temporal patterns
    try:
        conv_path = _base_path() / "logs" / "conversations.jsonl"
        if conv_path.is_file():
            lines = conv_path.read_text(encoding="utf-8").strip().split("\n")
            recent = lines[-50:]  # Last 50 conversations
            conv_times = []
            for line in recent:
                try:
                    entry = json.loads(line)
                    ts = entry.get("timestamp", "")
                    msg = entry.get("user_message", entry.get("message", ""))
                    if ts and msg:
                        conv_times.append(f"  [{ts[:19]}] {msg[:80]}")
                except (json.JSONDecodeError, KeyError):
                    continue
            if conv_times:
                parts.append("Conversation log (timestamps):\n" + "\n".join(conv_times[-20:]))
    except Exception as e:
        logger.debug("Conversation log unavailable: %s", e)

    return "\n\n".join(parts)


def _get_existing_schedule_descriptions() -> List[str]:
    """Get descriptions of existing scheduled tasks for dedup."""
    try:
        from src.core.scheduler import load_schedule
        tasks = load_schedule()
        return [t.description.lower() for t in tasks]
    except Exception:
        return []


def _model_schedule_proposal(
    router: Any,
    evidence: str,
    existing: List[str],
) -> List[Dict[str, Any]]:
    """Use model to detect patterns and propose scheduled tasks."""
    existing_block = "\n".join(f"  - {d}" for d in existing) if existing else "  (none)"

    prompt = f"""Analyze this activity data for RECURRING PATTERNS that suggest scheduled tasks.

{evidence}

Existing scheduled tasks (do NOT duplicate these):
{existing_block}

Look for:
1. Activities that happen at similar times of day regularly
2. Requests that repeat weekly or daily
3. Maintenance patterns (cleanup, reviews, check-ins)

Only propose a task if you see CLEAR evidence of repetition (at least 2-3 occurrences).
Each task needs a valid cron expression (minute hour dayOfMonth month dayOfWeek).

Return a JSON array (empty if no clear patterns):
[{{"task_id": "short-kebab-id", "description": "What the task does", "cron": "0 9 * * *", "action": "notify", "payload": "Message to send", "reasoning": "Evidence for this pattern"}}]

Rules:
- action must be "notify" (sends Discord message) or "create_goal" (creates a work item)
- task_id must be unique kebab-case, 3-30 chars
- Keep descriptions conversational, not technical
- At most 2 proposals per analysis
- For notify tasks: payload is the reminder message
- For create_goal tasks: payload is the goal description

JSON only:"""

    try:
        resp = router.generate(prompt=prompt, max_tokens=600, temperature=0.3)
        text = resp.get("text", "").strip()
        if not text:
            return []

        from src.utils.parsing import extract_json_array
        proposals = extract_json_array(text)
        if not isinstance(proposals, list):
            return []

        # Validate and filter
        valid = []
        for p in proposals[:2]:  # Cap at 2
            if not isinstance(p, dict):
                continue
            tid = p.get("task_id", "")
            desc = p.get("description", "")
            cron = p.get("cron", "")
            if not (tid and desc and cron):
                continue

            # Dedup against existing
            if any(desc.lower() in ex or ex in desc.lower() for ex in existing):
                logger.debug("Skipping duplicate schedule proposal: %s", desc)
                continue

            # Validate cron
            try:
                from src.core.scheduler import validate_cron
                if not validate_cron(cron):
                    continue
            except Exception:
                continue

            valid.append({
                "task_id": tid[:30],
                "description": desc[:200],
                "cron": cron,
                "action": p.get("action", "notify"),
                "payload": p.get("payload", desc)[:500],
                "reasoning": p.get("reasoning", "")[:200],
                "notify_user": p.get("action", "notify") == "notify",
            })

        if valid:
            logger.info("Schedule proposals: %d", len(valid))
        return valid

    except Exception as e:
        logger.debug("Schedule proposal failed: %s", e)
        return []


def create_proposed_schedules(
    proposals: List[Dict[str, Any]],
) -> tuple:
    """Create scheduled tasks from proposals.

    Notify tasks are proposed to user (returned as message); create_goal
    tasks are created silently.

    Returns:
        (created_silently: list, user_proposals: list)
    """
    try:
        from src.core import scheduler
    except ImportError:
        return [], []

    created = []
    proposed = []

    for p in proposals:
        action = p.get("action", "notify")
        if action == "notify":
            # Notify tasks need user approval — don't create, just return for messaging
            proposed.append(p)
        else:
            # Non-notify tasks (create_goal, etc.) — create silently
            task = scheduler.create_task(
                task_id=p["task_id"],
                description=p["description"],
                cron_expr=p["cron"],
                action=action,
                payload=p.get("payload", ""),
                created_by="archi",
            )
            if task:
                created.append(p)
                logger.info("Auto-created scheduled task '%s'", p["task_id"])

    return created, proposed


def format_schedule_proposal_message(proposals: List[Dict[str, Any]]) -> str:
    """Format schedule proposals for Jesse as a conversational Discord message."""
    if not proposals:
        return ""

    parts = ["I noticed some patterns in our activity and wanted to suggest:"]
    for p in proposals:
        cron = p.get("cron", "")
        desc = p.get("description", "")
        reasoning = p.get("reasoning", "")
        parts.append(f"\n**{desc}** (schedule: `{cron}`)")
        if reasoning:
            parts.append(f"  _{reasoning}_")

    parts.append("\nWant me to set any of these up? Just say the word.")
    return "\n".join(parts)
