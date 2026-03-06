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
import re
from typing import Any, Dict, List, Optional

from src.utils.parsing import extract_json as _extract_json
from src.utils.config import get_persona_prompt_cached, get_user_name

logger = logging.getLogger(__name__)

# Regex to strip internal tool name references from user-facing messages.
# Catches patterns like "via run_command", "via run_python", "Edit_file a ...",
# "using write_source", etc. (Added session 178.)
_TOOL_NAMES = (
    r'run_command|run_python|write_source|create_file|edit_file|append_file|'
    r'read_file|web_search|fetch_webpage|list_files|generate_image'
)
# Pattern 1: "via/using/with <tool>" — e.g. "via run_command"
_TOOL_NAME_RE = re.compile(
    r'\b(?:via|using|with)\s+(?:' + _TOOL_NAMES + r')\b',
    re.IGNORECASE,
)
# Pattern 2: "Tool_name ..." as sentence start — e.g. "Edit_file a scheduler"
_TOOL_ACTION_PREFIX_RE = re.compile(
    r'\b(?:Run_command|Run_python|Write_source|Create_file|Edit_file|Append_file|'
    r'Read_file|Web_search|Fetch_webpage|List_files|Generate_image)\s+',
)
# Pattern 3: natural-language tool references — "Use edit_file to", "Fire off a run_command"
# Also catches backtick-wrapped variants like "Run `run_python` with ..." (session 189).
_TOOL_NATURAL_RE = re.compile(
    r'\b(?:use|fire\s+off(?:\s+a)?|try|run|execute|call|invoke)\s+'
    r'(?:a\s+)?`?(?:' + _TOOL_NAMES + r')`?\b[^.]*',
    re.IGNORECASE,
)
# Pattern 4: shell/dev commands that leak into suggestions
_DEV_COMMAND_RE = re.compile(
    r'(?:'
    r"`[^`]*(?:pip install|pytest|crontab|npm|apt-get|curl|wget|docker)[^`]*`"  # backtick-wrapped
    r"|'[^']*(?:pip install|pytest|crontab|npm|apt-get|curl|wget|docker)[^']*'"  # single-quoted
    r'|\bpip install\s+\S+'  # bare pip install
    r'|\bpytest\s+\S+'  # bare pytest invocations
    r'|\bcrontab\b[^.]*'  # crontab references
    r')',
    re.IGNORECASE,
)
# Pattern 5: "Run/Execute <dev-tool>" — catches "Run pytest", "Run pip install", etc.
_DEV_VERB_CMD_RE = re.compile(
    r'\b(?:Run|Execute|Fire\s+off)\s+(?:a\s+|the\s+)?'
    r'(?:pip|pytest|npm|apt|curl|wget|docker|yarn|brew|make|git)\b[^.]*',
    re.IGNORECASE,
)
# Pattern 6: dev jargon — "X library/package install"
_DEV_INSTALL_RE = re.compile(
    r'\b\w+\s+(?:library|package|module|dependency)\s+install\w*\b[^.]*',
    re.IGNORECASE,
)

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
    journal_context: str = "",
    worldview_context: str = "",
) -> Dict[str, Any]:
    """Format a morning report summarizing overnight work.

    Args:
        successes: Successful task result dicts.
        failures: Failed task result dicts.
        total_cost: Total cost of overnight work.
        user_goal_lines: Progress lines for user-requested goals.
        finding_summary: Optional interesting finding to append.
        router: Model router.
        journal_context: Recent journal orientation for continuity (session 198).
        worldview_context: Evolving worldview for personality (session 199).

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

    # Journal context gives Archi awareness of recent days (session 198)
    _journal_hint = ""
    if journal_context and journal_context != "No recent journal entries.":
        data["recent_context"] = journal_context[:600]
        _journal_hint = " You can briefly reference what happened yesterday or recently if it's relevant — this gives your message continuity."

    # Worldview context gives Archi its developing perspective (session 199)
    _worldview_hint = ""
    if worldview_context:
        data["worldview"] = worldview_context[:400]
        _worldview_hint = " Your evolving opinions and preferences are included — let them subtly color your tone and observations, but don't list them explicitly."

    prompt = f"""{_get_persona()}

Write a morning update for {user_name} summarizing overnight work. Open with a natural greeting (vary it — "Morning", "Hey", "Good morning", etc.). Lead with user-requested goal progress if any. Mention what got done, what had issues, and any interesting findings. Include cost. Keep it readable but not formal.{_journal_hint}{_worldview_hint}

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

You have one work suggestion for {user_name}. Present it conversationally — like offering to do something helpful, not listing options. Explain what it does and why it would be useful in a sentence or two. End with something like "Want me to go ahead?" or similar. IMPORTANT: Describe the VALUE to the user, not the technical implementation. Never mention shell commands, package names, dev tools (pip, pytest, npm, git, etc.), or tool names.

Suggestion: {item['desc']} (category: {item['cat']}){why_block}

Message only (no JSON, no quotes):"""


def _build_multi_suggestion_prompt(items: List[Dict[str, str]]) -> str:
    """Build prompt for presenting multiple work suggestions."""
    user_name = get_user_name()
    return f"""{_get_persona()}

You have some free time and want to suggest work ideas to {user_name}. Present them as a numbered list (just numbers, no bullets). For each idea, include a brief explanation of what it does and why it would be useful — don't just give the title, give {user_name} enough context to make an informed decision. End with "Just reply with a number, or tell me something else." IMPORTANT: Describe the VALUE to the user, not the technical implementation. Never mention shell commands, package names, dev tools (pip, pytest, npm, git, etc.), or tool names.

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
    recent_starters: Optional[List[str]] = None,
    banned_topics: Optional[List[str]] = None,
    required_category: Optional[str] = None,
) -> Dict[str, Any]:
    """Format a proactive conversation starter based on what Archi knows about the user.

    Different from work suggestions — this is social/relational, not task-oriented.
    Archi references something the user told him, shares something interesting related
    to the user's interests, or callbacks to a past conversation.

    Args:
        user_facts: Known facts about the user from the UserModel.
        conversation_memories: Relevant past conversation summaries from LanceDB.
        router: Model router.
        recent_starters: Recently sent starters for dedup (session 181).
        banned_topics: Extracted topic keywords to avoid (session 183).
        required_category: Forced topic category for diversity (session 189).

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

    # Dedup: inject recent starters + banned topics to prevent paraphrases (session 181+183)
    dedup_block = ""
    dedup_parts = []
    if recent_starters:
        dedup_parts.append(
            "DO NOT repeat or closely paraphrase these recent messages — "
            "pick a COMPLETELY DIFFERENT topic or angle:\n"
            + "\n".join(f'- "{s[:120]}"' for s in recent_starters[-10:])
        )
    if banned_topics:
        unique_topics = list(dict.fromkeys(banned_topics))[:20]  # dedup, cap at 20
        dedup_parts.append(
            "BANNED TOPICS (do NOT mention any of these subjects, even indirectly): "
            + ", ".join(unique_topics)
        )
    if dedup_parts:
        dedup_block = "\n" + "\n".join(dedup_parts) + "\n"

    # Forced category rotation (session 189): strongest diversity mechanism.
    # Overrides the model's tendency to pick the most salient user fact.
    category_directive = ""
    if required_category:
        category_directive = (
            f"\n**MANDATORY TOPIC**: Your message MUST be about: {required_category}. "
            f"Do NOT write about any other subject. Find a connection between this "
            f"topic and what you know about {user_name}.\n"
        )

    prompt = f"""{_get_persona()}

You have some downtime and want to connect with {user_name} — not about work, but as a friend. Based on what you know about them, start a conversation. Pick ONE approach:
- Callback to something they mentioned before ("Hey, did you ever end up trying X?")
- Share something interesting related to their hobbies/interests
- React to something from a past conversation ("I was thinking about what you said about X...")
- Share a brief thought or observation that relates to something they care about
{category_directive}
RULES:
- Do NOT ask questions that require the user to produce content (favorite quotes, opinions on philosophers, recommendations, etc.). That puts work on them.
- Simple yes/no or "how'd it go?" follow-ups are fine. Open-ended "tell me about X" questions are not.
- Lean toward SHARING something rather than ASKING something.
- Keep it to 1-2 sentences. Be natural, not forced.
- If nothing feels organic, return exactly "SKIP".
{dedup_block}
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


def format_opinion_revision(
    topic: str,
    old_position: str,
    new_position: str,
    old_confidence: float,
    new_confidence: float,
    router: Any,
) -> Dict[str, Any]:
    """Format a proactive 'I changed my mind' notification (session 201).

    Returns:
        dict with: message (str), cost (float)
    """
    user_name = get_user_name()
    data = {
        "topic": topic,
        "old_position": old_position[:200],
        "new_position": new_position[:200],
        "old_confidence": round(old_confidence, 2),
        "new_confidence": round(new_confidence, 2),
    }

    prompt = f"""{_get_persona()}

You've changed your mind about something based on experience and want to tell {user_name}. Be genuine and specific — reference what you used to think, what changed, and why. This should feel like intellectual honesty, not a system alert. Keep it to 2-3 sentences.

Data: {data}

Message only (no JSON, no quotes):"""

    fallback = (
        f"Hey — I've been rethinking my take on {topic}. "
        f"I used to think '{old_position[:80]}' but based on recent experience, "
        f"I'm now leaning toward '{new_position[:80]}'."
    )
    return _call_formatter(prompt, router, fallback=fallback)


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


def format_exploration_sharing(
    topic: str,
    summary: str,
    commentary: str,
    router: Any,
) -> Dict[str, Any]:
    """Format a message sharing something Archi found while exploring out of curiosity.

    Unlike format_finding (task-driven), this is personality-driven — Archi
    explored because it was *curious*, not because someone asked.

    Returns:
        dict with: message (str), cost (float)
    """
    user_name = get_user_name()
    fallback = f"I was poking around {topic} and found something interesting — {summary[:150]}"

    prompt = f"""{_get_persona()}

You went down a rabbit hole on "{topic}" because you were curious — {user_name} didn't ask you to. Share what you found like you'd tell a friend about something cool you stumbled on. Include your personal take.

What you found: {summary[:300]}
Your take: {commentary[:200]}

Keep it natural — 2-4 sentences. Sound genuinely interested, not like you're delivering a report. {user_name} should feel like you're sharing something you actually care about.

Message only (no JSON, no quotes):"""

    return _call_formatter(prompt, router, fallback=fallback)


# ── Internal helpers ──────────────────────────────────────────────


def strip_tool_names(text: str) -> str:
    """Remove internal tool name references from user-facing messages.

    Strips patterns like "via run_command", "Use edit_file to tweak...",
    "Fire off a run_command with 'pip install...'", backtick-wrapped shell
    commands, and bare pip/pytest/crontab invocations.
    (Added session 178. Broadened session 181. Made public session 189.)

    Public API — also used by autonomous_executor for task completion text
    that goes directly to the user without going through _call_formatter().
    """
    text = _TOOL_NATURAL_RE.sub("", text)
    text = _TOOL_NAME_RE.sub("", text)
    text = _TOOL_ACTION_PREFIX_RE.sub("", text)
    text = _DEV_COMMAND_RE.sub("", text)
    text = _DEV_VERB_CMD_RE.sub("", text)
    text = _DEV_INSTALL_RE.sub("", text)
    # Strip any remaining bare or backtick-wrapped tool names (session 189)
    text = re.sub(r'`?(?:' + _TOOL_NAMES + r')`?', '', text, flags=re.IGNORECASE)
    # Clean up resulting artifacts: double spaces, orphaned punctuation, empty sentences
    text = re.sub(r'\s*[,;]\s*[,;.]\s*', '. ', text)
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\.\s*\.', '.', text)
    return text.strip()


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

    # Inject user style + mood context into the prompt (session 58, 201)
    try:
        from src.core.user_model import get_user_model
        um = get_user_model()
        style_ctx = um.get_context_for_formatter()
        if style_ctx:
            prompt = f"{prompt}\n\n{style_ctx}"
        mood_ctx = um.get_mood_context()
        if mood_ctx:
            prompt = f"{prompt}\n\n{mood_ctx}"
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

        # Strip internal tool name references from user-facing text
        text = strip_tool_names(text)

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
