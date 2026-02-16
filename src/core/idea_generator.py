"""
Idea Generator — Brainstorming and proactive goal creation for dream cycles.

Handles idea brainstorming, brainstorm approval gating, proactive work
planning, and goal hygiene (dedup, pruning, caps).
Split from dream_cycle.py in session 11.
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
MAX_PROACTIVE_GOALS = 1   # Was 3. One at a time; must complete before spawning new.
PROACTIVE_COOLDOWN_SECS = 3600  # 1 hour between proactive goal creation
MAX_FOLLOW_UP_DEPTH = 2   # Follow-up goals cannot spawn more than 2 levels deep


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
    # Also add current_projects
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
    return completed[-15:]  # Last 15


def is_goal_relevant(description: str, identity: dict) -> bool:
    """Check if a goal connects to an active project or user interest.

    Returns True if the goal references something Jesse actually cares about.
    Goals that are vague busywork (not tied to a project or interest) fail.
    """
    desc_lower = description.lower()
    project_names = _get_active_project_names(identity)

    # Check against active projects
    for name in project_names:
        if name in desc_lower:
            return True

    # Check against user interests
    interests = identity.get("user_context", {}).get("interests", [])
    for interest in interests:
        # Match if 2+ significant words from the interest appear in the description
        words = [w for w in interest.lower().split() if len(w) > 3]
        matches = sum(1 for w in words if w in desc_lower)
        if matches >= 2:
            return True

    # Check for references to concrete files/paths
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
    (or at least a file extension like .md, .py, .json).  Goals that are purely
    "research X" or "investigate Y" without a concrete output location fail.
    """
    desc_lower = description.lower()

    # Must reference a concrete output location
    has_path = (
        "workspace/" in desc_lower
        or ".md" in desc_lower
        or ".py" in desc_lower
        or ".json" in desc_lower
        or ".txt" in desc_lower
        or ".csv" in desc_lower
    )

    # Must contain a deliverable verb
    words = set(desc_lower.split())
    has_verb = bool(words & _DELIVERABLE_VERBS)

    return has_path and has_verb


def get_follow_up_depth(goal_manager: Optional[GoalManager], goal_id: str) -> int:
    """Count how many levels deep a follow-up chain goes.

    A goal created as a follow-up from another follow-up has depth 2, etc.
    """
    if not goal_manager:
        return 0
    depth = 0
    current_id = goal_id
    visited = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        goal = goal_manager.goals.get(current_id)
        if not goal:
            break
        intent = goal.user_intent or ""
        if "Follow-up from:" not in intent and "Synthesis:" not in intent:
            break
        depth += 1
        # Try to find parent goal ID from the intent text
        # Intent format: "Follow-up from: <goal_desc>[:60] — <reasoning>"
        # We match by description substring
        parent_desc = intent.split("Follow-up from:")[-1].split("—")[0].strip()[:50]
        found_parent = False
        for gid, g in goal_manager.goals.items():
            if gid != current_id and parent_desc and parent_desc.lower() in g.description.lower():
                current_id = gid
                found_parent = True
                break
        if not found_parent:
            break
    return depth


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


def brainstorm_ideas(
    router: Any,
    goal_manager: Optional[GoalManager],
    learning_system: LearningSystem,
    identity: dict,
    last_brainstorm: Optional[datetime],
    stop_flag: Any,
    memory: Any = None,
) -> Optional[datetime]:
    """Generate improvement ideas and create a goal for the best one.

    Runs at most once per 24 hours, during night hours (11 PM - 5 AM).
    Uses focus areas from archi_identity.yaml to guide brainstorming.
    Scores ideas by estimated benefit-per-hour and picks the winner.

    Returns:
        Updated last_brainstorm timestamp, or the original value if skipped.
    """
    now = datetime.now()

    # Only brainstorm during night hours
    if not (23 <= now.hour or now.hour <= 5):
        return last_brainstorm

    # At most once per 24 hours
    if last_brainstorm and (now - last_brainstorm).total_seconds() < 86400:
        return last_brainstorm

    if not router or not goal_manager:
        return last_brainstorm

    if stop_flag.is_set():
        return last_brainstorm

    logger.info("=== IDEA BRAINSTORM START ===")

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

    # Include existing reports so brainstorm avoids re-generating them
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
            # Search for what's already been researched using focus areas as queries
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
                logger.info("Brainstorm: injected %d prior research topics from memory", len(_all_topics))
        except Exception as me:
            logger.debug("Memory query for brainstorm skipped: %s", me)

    # Active projects with file paths for grounding
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
                projects_block = "\n\nJesse's active projects (goals MUST connect to one of these):\n" + "\n".join(parts)
        # Also include current_projects
        current = identity.get("user_context", {}).get("current_projects", [])
        if current and not projects_block:
            projects_block = "\n\nJesse's current projects (goals MUST connect to one of these):\n" + "\n".join(
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

Generate 3-5 ideas to work on TONIGHT while Jesse sleeps.

PURPOSE-DRIVEN GOALS: Every goal must produce a CONCRETE CHANGE in a project — not
just standalone research. Research is a means, not an end. Ask: "What does the project
look like when this is DONE?" If the answer is just "a report exists," the goal is bad.

BAD goals (research as the end product):
- "Research creatine timing studies"
- "Compile a report on sleep optimization supplements"
- "Investigate longevity interventions and write findings"

GOOD goals (research serves a concrete change):
- "Update workspace/projects/Health_Optimization/supplements.md with latest creatine timing evidence from peer-reviewed sources"
- "Add a sleep_protocol.md to Health_Optimization/ synthesizing current stack with new melatonin/magnesium dosing data"
- "Create a comparison table in Health_Optimization/stack_risks.md identifying contradictions or risks in the current supplement stack"
- "Extend the Archi README troubleshooting section with the 5 most common setup failures found in issue trackers"

CRITICAL RULES:
1. Every idea MUST connect to one of Jesse's active projects above
2. Every idea MUST name a specific file path to create or update (workspace/projects/...)
3. The description must include a DELIVERABLE VERB: update, add, create, extend, synthesize, build, integrate, consolidate, restructure
4. DO NOT recreate existing reports (see list above)
5. DO NOT generate goals similar to recently completed work

Return ONLY a JSON array:
[
  {{
    "category": "Health|Wealth|Happiness|Capability|Agency|Synthesis",
    "description": "Action-oriented task that changes a project file",
    "end_state": "What the project looks like when this is done",
    "target_file": "workspace/projects/ProjectName/filename.ext",
    "benefit": 1-10,
    "estimated_hours": 0.1-2.0,
    "reasoning": "Why this moves the project forward (not just adds information)",
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
            logger.warning("Brainstorm produced no valid ideas")
            return now

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
        backlog = {"ideas": [], "last_brainstorm": now.isoformat()}
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
                "status": "pending",
            })
        backlog["last_brainstorm"] = now.isoformat()

        with open(backlog_path, "w", encoding="utf-8") as f:
            json.dump(backlog, f, indent=2)

        # Create a goal for the best RELEVANT idea — ask user first
        best = None
        for candidate in scored:
            desc = candidate.get("description", "")
            if not desc:
                continue
            if is_duplicate_goal(desc, goal_manager):
                logger.info("Brainstorm idea skipped (duplicate): %s", desc[:60])
                continue
            if not is_goal_relevant(desc, identity):
                logger.info("Brainstorm idea skipped (not relevant to projects): %s", desc[:60])
                continue
            if not is_purpose_driven(desc):
                logger.info("Brainstorm idea skipped (not purpose-driven — missing deliverable verb or file path): %s", desc[:60])
                continue
            # Check long-term memory for semantically similar past research
            if memory:
                try:
                    _mem_results = memory.retrieve_relevant(desc, n_results=2)
                    _sem = _mem_results.get("semantic", [])
                    # Very close match (distance < 0.5) means this topic was already researched
                    if any(m.get("distance", 2.0) < 0.5 for m in _sem):
                        logger.info(
                            "Brainstorm idea skipped (already researched in memory): %s",
                            desc[:60],
                        )
                        continue
                except Exception:
                    pass
            best = candidate
            break

        if not best:
            logger.info("Brainstorm: no relevant, non-duplicate ideas found")
            return now

        desc = best.get("description", "")
        category = best.get("category", "General")
        if desc and count_active_goals(goal_manager) < MAX_ACTIVE_GOALS:
            _goal_approved = request_brainstorm_approval(best)
            if _goal_approved:
                goal = goal_manager.create_goal(
                    description=desc,
                    user_intent=f"Auto-brainstormed ({category}, score={best['score']}): {best.get('reasoning', '')}",
                    priority=7,
                )
                logger.info(
                    "Brainstorm winner: [%s] %s (score=%.1f) -> %s",
                    category, desc[:80], best["score"], goal.goal_id,
                )
                backlog["ideas"][-len(scored)]["status"] = "goal_created"
                backlog["ideas"][-len(scored)]["goal_id"] = goal.goal_id
                with open(backlog_path, "w", encoding="utf-8") as f:
                    json.dump(backlog, f, indent=2)
            else:
                logger.info(
                    "Brainstorm idea REJECTED by user (or timed out): [%s] %s",
                    category, desc[:80],
                )
                backlog["ideas"][-len(scored)]["status"] = "rejected"
                with open(backlog_path, "w", encoding="utf-8") as f:
                    json.dump(backlog, f, indent=2)

        logger.info(
            "=== IDEA BRAINSTORM END (%d ideas, best score=%.1f) ===",
            len(scored), scored[0]["score"] if scored else 0,
        )

    except Exception as e:
        logger.error("Brainstorm failed: %s", e, exc_info=True)

    return now


def request_brainstorm_approval(idea: Dict[str, Any]) -> bool:
    """Ask the user via Discord whether to pursue a brainstormed idea.

    Sends a concise summary and waits for yes/no.
    Auto-approves after 120 seconds (lower risk than code modifications).

    Returns True if approved (or timed out), False if denied.
    """
    try:
        from src.interfaces.discord_bot import send_notification, is_outbound_ready
    except ImportError:
        return True

    if not is_outbound_ready():
        logger.debug("Discord not ready for brainstorm approval — auto-approving")
        return True

    category = idea.get("category", "General")
    desc = idea.get("description", "?")
    reasoning = idea.get("reasoning", "")
    score = idea.get("score", 0)

    msg = (
        f"\U0001f4a1 **Brainstorm idea — should I work on this?**\n"
        f"**Category:** {category} (score: {score})\n"
        f"**Idea:** {desc[:300]}\n"
        f"**Why:** {reasoning[:200]}\n\n"
        f"Reply **yes** to approve or **no** to skip. "
        f"(Auto-approves in 120s if no response)"
    )

    send_notification(msg)

    import threading as _th

    _brainstorm_event = _th.Event()
    _brainstorm_result = [True]  # Default: auto-approve on timeout

    # Store where Discord message handler can find it
    # NOTE: This uses a module-level reference that discord_bot checks
    import src.core.idea_generator as _self_module
    _self_module._brainstorm_approval_event = _brainstorm_event
    _self_module._brainstorm_approval_result = _brainstorm_result

    responded = _brainstorm_event.wait(timeout=120)

    # Clean up
    _self_module._brainstorm_approval_event = None
    _self_module._brainstorm_approval_result = None

    if not responded:
        logger.info("Brainstorm approval timed out — auto-approving idea")
        import threading as _th2
        _th2.Thread(
            target=send_notification,
            args=(f"\u23f0 No response — auto-approved: {desc[:100]}",),
            daemon=True,
        ).start()
        return True

    return _brainstorm_result[0]


# Module-level state for brainstorm approval (set by request_brainstorm_approval,
# read by discord_bot's message handler)
_brainstorm_approval_event = None
_brainstorm_approval_result = None


def plan_future_work(
    goal_manager: Optional[GoalManager],
    identity: dict,
    dream_history: list,
    stop_flag: Any,
    last_proactive_goal_time: float,
) -> tuple:
    """Plan proactive work based on Prime Directive and identity config.

    Creates actual goals from plans, with robust duplicate detection,
    a hard cap on active goals, a proactive-goal-specific cap, and a
    cooldown timer.

    Returns:
        (plans_list, updated_last_proactive_goal_time)
    """
    plans = []

    if stop_flag.is_set():
        return plans, last_proactive_goal_time

    if not identity:
        return plans, last_proactive_goal_time

    # Prune stale goals first
    prune_stale_goals(goal_manager)

    active = count_active_goals(goal_manager)
    if active >= MAX_ACTIVE_GOALS:
        logger.info(
            "Skipping plan creation: %d active goals (cap=%d)",
            active, MAX_ACTIVE_GOALS,
        )
        return plans, last_proactive_goal_time

    # Proactive-specific throttle
    now = time.monotonic()
    elapsed = now - last_proactive_goal_time
    if last_proactive_goal_time > 0 and elapsed < PROACTIVE_COOLDOWN_SECS:
        logger.info(
            "Skipping proactive planning: cooldown (%d/%ds elapsed)",
            int(elapsed), PROACTIVE_COOLDOWN_SECS,
        )
        return plans, last_proactive_goal_time

    # Count existing proactive goals
    proactive_active = 0
    if goal_manager:
        for g in goal_manager.goals.values():
            if not g.is_complete() and "auto-planned" in (g.user_intent or ""):
                proactive_active += 1
    if proactive_active >= MAX_PROACTIVE_GOALS:
        logger.info(
            "Skipping proactive planning: %d proactive goals active (cap=%d)",
            proactive_active, MAX_PROACTIVE_GOALS,
        )
        return plans, last_proactive_goal_time

    logger.info("Planning future work...")
    current_hour = datetime.now().hour
    proactive = identity.get("proactive_tasks", {})

    if 2 <= current_hour <= 5:
        research = proactive.get("research", [])
        if research:
            idx = len(dream_history) % len(research)
            task = research[idx]
            plans.append({"type": "research", "description": task, "priority": 5})
            logger.info("Planned research: %s", task)

    monitoring = proactive.get("monitoring", [])
    if monitoring and len(dream_history) % 5 == 0:
        task = monitoring[0]
        plans.append({"type": "monitoring", "description": task, "priority": 7})
        logger.info("Planned monitoring: %s", task)

    # Convert plans into actual goals
    if plans and goal_manager:
        for plan in plans:
            if count_active_goals(goal_manager) >= MAX_ACTIVE_GOALS:
                logger.info("Goal cap reached, skipping remaining plans")
                break
            desc = plan["description"]
            if is_duplicate_goal(desc, goal_manager):
                logger.info("Skipping duplicate plan: %s", desc[:60])
                continue
            try:
                goal = goal_manager.create_goal(
                    description=desc,
                    user_intent=f"Proactive {plan['type']} (auto-planned from identity config)",
                    priority=plan.get("priority", 5),
                )
                logger.info(
                    "Created goal from plan: %s -> %s", desc[:60], goal.goal_id,
                )
                last_proactive_goal_time = time.monotonic()
            except Exception as e:
                logger.warning("Failed to create goal from plan: %s", e)

    return plans, last_proactive_goal_time
