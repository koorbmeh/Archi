"""
Notification Formatter — Model-based conversational message generation.

Replaces all hardcoded notification strings across reporting.py,
goal_worker_pool.py, and heartbeat.py with a single model call per
notification that produces natural, varied messages matching Archi's persona.

Takes structured data (event type, goal results, stats) and returns a
conversational message. Cost: ~$0.0002 per call (Grok 4.1 Fast).

Created in session 50 (Phase 3: Notifications + Feedback).
"""

import logging
import os
from typing import Any, Dict, List, Optional

from src.utils.parsing import extract_json as _extract_json
from src.utils.config import get_persona_prompt_cached, get_user_name

logger = logging.getLogger(__name__)

# Persona instructions shared across all notification types.
# Kept short to minimize token cost per call.
def _get_persona() -> str:
    """Build persona string from personality.yaml + notification-specific rules."""
    return (
        f"{get_persona_prompt_cached()} "
        "Never use bullet points, headers, or markdown formatting. "
        "Keep it short (1-4 sentences for simple updates, up to ~8 for morning reports). "
        "Vary your phrasing — don't start every message the same way. "
        "Sound like a person, not a system alert. "
        "IMPORTANT: Only reference information actually provided below. Never claim "
        "ideas came from past conversations, user interests, or context that isn't "
        "explicitly given to you."
    )


def format_goal_completion(
    goal_description: str,
    tasks_completed: int,
    tasks_failed: int,
    total_cost: float,
    task_summaries: List[str],
    files_created: List[str],
    is_user_requested: bool,
    hit_budget: bool,
    is_significant: bool,
    router: Any,
) -> Dict[str, Any]:
    """Format a goal completion notification.

    Args:
        goal_description: What the goal was about.
        tasks_completed: Number of tasks that succeeded.
        tasks_failed: Number of tasks that failed.
        total_cost: Total cost of the goal execution.
        task_summaries: "Done:" summary lines from PlanExecutor results.
        files_created: List of file paths created.
        is_user_requested: Whether the user explicitly asked for this.
        hit_budget: Whether the goal hit its budget cap.
        is_significant: Whether the goal was significant (3+ tasks or 10+ min).
        router: Model router for the formatting call.

    Returns:
        dict with: message (str), cost (float)
    """
    file_names = [os.path.basename(f) for f in files_created[:5]]
    user_name = get_user_name()

    data = {
        "event": "goal_completion",
        "goal": goal_description[:400],
        "tasks_completed": tasks_completed,
        "tasks_failed": tasks_failed,
        "summaries": [s[:400] for s in task_summaries[:3]],
        "files": file_names,
        "user_requested": is_user_requested,
        "hit_budget": hit_budget,
    }

    # Adjust tone based on failure ratio — don't let the model brush
    # off significant failures as minor hiccups (session 166).
    if tasks_failed > 0 and tasks_completed == 0:
        failure_guidance = (
            f"All {tasks_failed} tasks failed — be upfront about this. "
            "Don't say the goal is done; say it didn't work out and "
            "briefly describe what went wrong (use summaries)."
        )
    elif tasks_failed > 0:
        failure_guidance = (
            f"{tasks_failed} of {tasks_completed + tasks_failed} tasks failed. "
            "Be clear about what succeeded and what didn't — "
            "don't minimize the failures or claim everything is done."
        )
    else:
        failure_guidance = ""

    prompt = f"""{_get_persona()}

Write a Discord DM to {user_name} about this completed goal. Include what was accomplished (use the summaries if available, otherwise mention files). {failure_guidance} If the budget was hit, note that work paused.

{"This was something " + user_name + " asked for — lead with the result he's waiting for." if is_user_requested else "This was background work."}

Data: {data}

{"End with: Anything you'd change?" if is_significant else ""}

Message only (no JSON, no quotes):"""

    return _call_formatter(prompt, router, fallback=_fallback_goal_completion(data))


def format_morning_report(
    successes: List[Dict[str, Any]],
    failures: List[Dict[str, Any]],
    total_cost: float,
    user_goal_lines: List[str],
    finding_summary: Optional[str],
    router: Any,
) -> Dict[str, Any]:
    """Format a morning report summarizing overnight work.

    Args:
        successes: Successful task result dicts.
        failures: Failed task result dicts.
        total_cost: Total cost of overnight work.
        user_goal_lines: Progress lines for user-requested goals.
        finding_summary: Optional interesting finding to append.
        router: Model router.

    Returns:
        dict with: message (str), cost (float)
    """
    # Build a concise structured summary for the model
    success_items = []
    for r in successes[:6]:
        summary = r.get("summary", "")
        # Extract "Done:" portion if present
        if "Done: " in summary:
            done_text = summary.split("Done: ", 1)[1].split(";")[0].strip()
            success_items.append(done_text[:150])
        else:
            success_items.append(r.get("task", "")[:80])

    failure_items = [r.get("task", "")[:80] for r in failures[:3]]
    user_name = get_user_name()

    data = {
        "event": "morning_report",
        "successes": success_items,
        "failures": failure_items,
        "total_cost": round(total_cost, 4),
        "user_goals": user_goal_lines[:5],
        "finding": finding_summary[:400] if finding_summary else None,
    }

    prompt = f"""{_get_persona()}

Write a morning update for {user_name} summarizing overnight work. Open with a natural greeting (vary it — "Morning", "Hey", "Good morning", etc.). Lead with user-requested goal progress if any. Mention what got done, what had issues, and any interesting findings. Include cost. Keep it readable but not formal.

Data: {data}

Message only (no JSON, no quotes):"""

    return _call_formatter(prompt, router, fallback=_fallback_morning_report(data))


def format_hourly_summary(
    successes: List[Dict[str, Any]],
    failures: List[Dict[str, Any]],
    files_created: List[str],
    user_goal_lines: List[str],
    finding_summary: Optional[str],
    router: Any,
) -> Dict[str, Any]:
    """Format an hourly work summary.

    Returns:
        dict with: message (str), cost (float)
    """
    file_names = list(dict.fromkeys(
        os.path.basename(f) for r in (successes + failures)
        for f in r.get("files_created", [])
    ))[:5]

    success_items = []
    for r in successes[:4]:
        summary = r.get("summary", "")
        if "Done: " in summary:
            done_text = summary.split("Done: ", 1)[1].split(";")[0].strip()
            success_items.append(done_text[:120])
        else:
            success_items.append(r.get("task", "")[:80])

    user_name = get_user_name()

    data = {
        "event": "hourly_summary",
        "tasks_done": len(successes),
        "tasks_failed": len(failures),
        "successes": success_items,
        "files": file_names,
        "user_goals": user_goal_lines[:5],
        "finding": finding_summary[:400] if finding_summary else None,
    }

    prompt = f"""{_get_persona()}

Write a brief hourly update for {user_name}. Keep it short — just the highlights. Mention what got done, any issues, user goal progress, and files if relevant.

Data: {data}

Message only (no JSON, no quotes):"""

    return _call_formatter(prompt, router, fallback=_fallback_hourly_summary(data))


def format_suggestions(
    suggestions: List[Dict[str, Any]],
    router: Any,
) -> Dict[str, Any]:
    """Format work suggestions for the user.

    Args:
        suggestions: List of suggestion dicts with 'description' and 'category'.
        router: Model router.

    Returns:
        dict with: message (str), cost (float)
    """
    items = _build_suggestion_items(suggestions)
    if len(items) == 1:
        prompt = _build_single_suggestion_prompt(items[0])
    else:
        prompt = _build_multi_suggestion_prompt(items)
    return _call_formatter(prompt, router, fallback=_fallback_suggestions(items))


def _build_suggestion_items(suggestions: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Extract description, category, and reasoning from raw suggestions."""
    items = []
    for s in suggestions[:5]:
        item: Dict[str, str] = {
            "desc": s.get("description", "")[:250],
            "cat": s.get("category", ""),
        }
        reasoning = s.get("reasoning", "")
        if reasoning:
            item["why"] = reasoning[:250]
        items.append(item)
    return items


def _build_single_suggestion_prompt(item: Dict[str, str]) -> str:
    """Build prompt for presenting a single work suggestion."""
    user_name = get_user_name()
    why_block = f"\nWhy it's useful: {item.get('why', '')}" if item.get('why') else ""
    return f"""{_get_persona()}

You have one work suggestion for {user_name}. Present it conversationally — like offering to do something helpful, not listing options. Explain what it does and why it would be useful in a sentence or two. End with something like "Want me to go ahead?" or similar.

Suggestion: {item['desc']} (category: {item['cat']}){why_block}

Message only (no JSON, no quotes):"""


def _build_multi_suggestion_prompt(items: List[Dict[str, str]]) -> str:
    """Build prompt for presenting multiple work suggestions."""
    user_name = get_user_name()
    return f"""{_get_persona()}

You have some free time and want to suggest work ideas to {user_name}. Present them as a numbered list (just numbers, no bullets). For each idea, include a brief explanation of what it does and why it would be useful — don't just give the title, give {user_name} enough context to make an informed decision. End with "Just reply with a number, or tell me something else."

Suggestions: {items}

Message only (no JSON, no quotes):"""


def format_finding(
    goal_description: str,
    finding_summary: str,
    files_created: List[str],
    router: Any,
) -> Dict[str, Any]:
    """Format a proactive finding notification.

    Returns:
        dict with: message (str), cost (float)
    """
    file_names = [os.path.basename(f) for f in files_created[:3]]
    user_name = get_user_name()

    data = {
        "event": "finding",
        "goal": goal_description[:300],
        "finding": finding_summary[:300],
        "files": file_names,
    }

    prompt = f"""{_get_persona()}

You found something interesting while working on a background task. Share it conversationally with {user_name} — like mentioning something you came across, not delivering a report.

Data: {data}

Message only (no JSON, no quotes):"""

    return _call_formatter(prompt, router, fallback=_fallback_finding(data))


def format_initiative_announcement(
    title: str,
    why: str,
    router: Any,
    reasoning: str = "",
    source: str = "",
) -> Dict[str, Any]:
    """Format a proactive initiative start announcement.

    Returns:
        dict with: message (str), cost (float)
    """
    user_name = get_user_name()
    context_parts = [f"Task: {title[:200]}", f"Why: {why[:200]}"]
    if reasoning:
        context_parts.append(f"Background: {reasoning[:200]}")
    if source:
        context_parts.append(f"Found via: {source}")
    context_block = "\n".join(context_parts)

    prompt = f"""{_get_persona()}

You're starting work on something proactively ({user_name} didn't ask for it). Briefly explain what this project/file is and why you're working on it — {user_name} may not know it exists. Keep it casual, 2-3 sentences max.

{context_block}

Message only (no JSON, no quotes):"""

    fallback = f"Working on {title[:100]} — {why[:100]}"
    return _call_formatter(prompt, router, fallback=fallback)


def format_conversation_starter(
    user_facts: List[str],
    conversation_memories: List[str],
    router: Any,
) -> Dict[str, Any]:
    """Format a proactive conversation starter based on what Archi knows about the user.

    Different from work suggestions — this is social/relational, not task-oriented.
    Archi references something the user told him, shares something interesting related
    to the user's interests, or callbacks to a past conversation.

    Args:
        user_facts: Known facts about the user from the UserModel.
        conversation_memories: Relevant past conversation summaries from LanceDB.
        router: Model router.

    Returns:
        dict with: message (str), cost (float), or None message if nothing good.
    """
    if not user_facts and not conversation_memories:
        return {"message": "", "cost": 0.0}

    user_name = get_user_name()
    context_parts = []
    if user_facts:
        context_parts.append(f"Known about {user_name}:\n" + "\n".join(f"- {f}" for f in user_facts[:8]))
    if conversation_memories:
        context_parts.append("Past conversations:\n" + "\n".join(f"- {m}" for m in conversation_memories[:3]))
    context_block = "\n\n".join(context_parts)

    prompt = f"""{_get_persona()}

You have some downtime and want to connect with {user_name} — not about work, but as a friend. Based on what you know about them, start a conversation. Pick ONE approach:
- Callback to something they mentioned before ("Hey, did you ever end up trying X?")
- Share something interesting related to their hobbies/interests
- React to something from a past conversation ("I was thinking about what you said about X...")
- Share a brief thought or observation that relates to something they care about

RULES:
- Do NOT ask questions that require the user to produce content (favorite quotes, opinions on philosophers, recommendations, etc.). That puts work on them.
- Simple yes/no or "how'd it go?" follow-ups are fine. Open-ended "tell me about X" questions are not.
- Lean toward SHARING something rather than ASKING something.
- Keep it to 1-2 sentences. Be natural, not forced.
- If nothing feels organic, return exactly "SKIP".

{context_block}

Message only (no JSON, no quotes):"""

    result = _call_formatter(prompt, router, fallback="SKIP")
    # If the model couldn't find anything natural, signal to skip
    if result["message"].strip().upper() == "SKIP":
        result["message"] = ""
    return result


def format_idle_prompt(router: Any) -> Dict[str, Any]:
    """Format an idle "anything you'd like me to work on?" message.

    Returns:
        dict with: message (str), cost (float)
    """
    user_name = get_user_name()
    prompt = f"""{_get_persona()}

You're caught up on all your work and have free time. Ask {user_name} if there's anything they'd like you to work on. Keep it to one sentence. Vary the phrasing.

Message only (no JSON, no quotes):"""

    return _call_formatter(
        prompt, router,
        fallback="All caught up — anything you'd like me to work on?",
    )


def format_interrupted_tasks(
    tasks: List[Dict[str, Any]],
    router: Any,
) -> Dict[str, Any]:
    """Format a notification about crash-recovered interrupted tasks.

    Returns:
        dict with: message (str), cost (float)
    """
    if len(tasks) == 1:
        desc = tasks[0].get("description", "unknown task")[:100]
        fallback = f"Picking up where I left off — {desc}"
    else:
        fallback = f"Resuming {len(tasks)} tasks from before the restart."

    user_name = get_user_name()
    descriptions = [t.get("description", "")[:100] for t in tasks[:3]]
    prompt = f"""{_get_persona()}

You just restarted and have interrupted tasks to resume. Let {user_name} know casually. Keep it brief.

Tasks: {descriptions}

Message only (no JSON, no quotes):"""

    return _call_formatter(prompt, router, fallback=fallback)


def format_decomposition_failure(
    goal_description: str,
    router: Any,
) -> Dict[str, Any]:
    """Format a notification about a failed goal decomposition.

    Returns:
        dict with: message (str), cost (float)
    """
    user_name = get_user_name()
    fallback = f"Couldn't break down the goal into tasks: {goal_description[:100]}"

    prompt = f"""{_get_persona()}

You tried to break a goal into tasks but it failed. Let {user_name} know briefly and conversationally. Don't be overly apologetic.

Goal: {goal_description[:200]}

Message only (no JSON, no quotes):"""

    return _call_formatter(prompt, router, fallback=fallback)


# ── Internal helpers ──────────────────────────────────────────────


def _call_formatter(
    prompt: str,
    router: Any,
    fallback: str,
) -> Dict[str, Any]:
    """Make the model call and return the formatted message.

    Injects User Model style context so notifications adapt to the user's
    communication preferences (session 58).
    Falls back to a hardcoded string if the model call fails.
    """
    if not router:
        return {"message": fallback, "cost": 0.0}

    # Inject user style context into the prompt (session 58)
    try:
        from src.core.user_model import get_user_model
        style_ctx = get_user_model().get_context_for_formatter()
        if style_ctx:
            prompt = f"{prompt}\n\n{style_ctx}"
    except Exception as e:
        logger.debug("User model unavailable for formatter: %s", e)

    try:
        resp = router.generate(
            prompt=prompt,
            max_tokens=400,
            temperature=0.7,
        )
        text = (resp.get("text") or "").strip()
        cost = resp.get("cost_usd", 0)

        # Strip any wrapping quotes the model might add
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]

        # Sanity check: if the model returned something too short or clearly
        # broken (JSON, empty, just whitespace), use the fallback.
        if not text or len(text) < 10 or text.startswith("{"):
            logger.debug("Formatter output rejected (too short or JSON): %s", text[:60])
            return {"message": fallback, "cost": cost}

        return {"message": text, "cost": cost}

    except Exception as e:
        logger.debug("Notification formatter failed: %s", e)
        return {"message": fallback, "cost": 0.0}


# ── Fallback formatters (zero-cost, for when model is unavailable) ──


def _fallback_goal_completion(data: Dict[str, Any]) -> str:
    """Deterministic fallback for goal completion."""
    goal = data.get("goal", "unknown")
    label = goal.split(",")[0].split(".")[0].split(":")[0].strip()
    if len(label) > 60:
        label = label[:57] + "…"

    completed = data.get("tasks_completed", 0)
    failed = data.get("tasks_failed", 0)

    if failed == 0:
        msg = f"Done with {label}."
    elif completed == 0:
        msg = f"Couldn't make progress on {label} — ran into issues on all {failed} tasks."
    else:
        msg = f"Done with {label} — {completed} tasks finished, {failed} had issues."

    if data.get("hit_budget"):
        msg = f"Pausing {label} — hit the budget. Got {completed} tasks done."

    summaries = data.get("summaries", [])
    if summaries:
        msg += "\n" + "\n".join(summaries[:3])
    elif data.get("files"):
        msg += f"\nFiles: {', '.join(data['files'][:4])}"

    return msg


def _fallback_morning_report(data: Dict[str, Any]) -> str:
    """Deterministic fallback for morning report."""
    successes = data.get("successes", [])
    failures = data.get("failures", [])
    lines = []

    if successes and not failures:
        lines.append(f"Morning — got {len(successes)} things done overnight.\n")
    elif successes and failures:
        lines.append(f"Morning — {len(successes)} tasks done, {len(failures)} ran into issues.\n")
    elif failures:
        lines.append(f"Morning. Rough night — {len(failures)} tasks hit problems.\n")
    else:
        lines.append("Morning — quiet night, nothing to report.\n")

    for gl in data.get("user_goals", []):
        lines.append(gl)
    if data.get("user_goals"):
        lines.append("")

    for s in successes[:5]:
        lines.append(f"- {s}")
    for f in failures[:3]:
        lines.append(f"- (failed) {f}")

    lines.append(f"\nCost: ${data.get('total_cost', 0):.4f}")

    finding = data.get("finding")
    if finding:
        lines.append(f"\nAlso — {finding}")

    return "\n".join(lines)


def _fallback_hourly_summary(data: Dict[str, Any]) -> str:
    """Deterministic fallback for hourly summary."""
    done = data.get("tasks_done", 0)
    failed = data.get("tasks_failed", 0)

    if done and failed:
        msg = f"Quick update — finished {done} tasks this hour, {failed} had issues."
    elif done:
        msg = f"Quick update — finished {done} tasks this hour."
    else:
        msg = f"Quick update — {failed} tasks ran into problems this hour."

    for gl in data.get("user_goals", []):
        msg += f"\n{gl}"

    files = data.get("files", [])
    if files:
        msg += f"\nFiles: {', '.join(files[:5])}"

    return msg


def _fallback_suggestions(items: List[Dict[str, str]]) -> str:
    """Deterministic fallback for work suggestions."""
    if len(items) == 1:
        desc = items[0]["desc"]
        _d = desc[0].lower() + desc[1:] if desc and desc[0].isupper() else desc
        return f"Hey — I could {_d}\nWant me to go ahead?"

    lines = ["Got some free time. A few ideas:"]
    for i, item in enumerate(items, 1):
        why = item.get("why", "")
        if why:
            lines.append(f"{i}. {item['desc']} — {why}")
        else:
            lines.append(f"{i}. {item['desc']}")
    lines.append("\nJust reply with a number, or tell me something else.")
    return "\n".join(lines)


def _fallback_finding(data: Dict[str, Any]) -> str:
    """Deterministic fallback for finding notification."""
    finding = data.get("finding", "")
    files = data.get("files", [])
    file_note = f"\n({', '.join(files)})" if files else ""
    return f"Hey — came across something while working: {finding}{file_note}"
