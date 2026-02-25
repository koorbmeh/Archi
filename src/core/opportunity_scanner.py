"""
Opportunity Scanner — Identify real, actionable work by scanning project state.

Replaces the old brainstorm-prompt approach in suggest_work() with structured
analysis of actual project files, error logs, unused capabilities, and user
context. Each scanner reads real data and makes one focused LLM call to
identify typed opportunities (build / ask / fix / connect / improve).

Created session 42 (Cowork).
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.config import get_user_name
from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# Cache vision file excerpts to avoid re-reading every cycle
_vision_cache: Dict[str, str] = {}
_vision_cache_ts: Dict[str, float] = {}
_VISION_CACHE_TTL = 3600  # 1 hour


@dataclass
class Opportunity:
    """A typed, actionable work item identified by a scanner."""
    type: str           # "build", "ask", "fix", "connect", "improve"
    description: str    # actionable goal description (will become goal.description)
    target_files: List[str] = field(default_factory=list)
    value_score: int = 5
    estimated_hours: float = 1.0
    user_value: str = ""      # why the user should care
    source: str = ""          # "project_gap", "error_pattern", "unused_capability", "user_context"
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Scanner 1: Project gap analysis
# ---------------------------------------------------------------------------

def scan_projects(project_context: dict, router: Any) -> List[Opportunity]:
    """Read active project files and find gaps between vision and reality.

    For each active project, reads the vision/overview file and compares to
    what actually exists on disk. Returns build/ask/improve opportunities.
    """
    active = project_context.get("active_projects", {})
    if not active:
        logger.debug("scan_projects: no active projects in context")
        return []

    # Load user context for personalized opportunity discovery
    user_context = ""
    try:
        from src.core.user_model import get_user_model
        um = get_user_model()
        _parts = []
        if um.facts:
            _parts.append(f"About {get_user_name()}: " + "; ".join(
                f.get("text", "") for f in um.facts[-5:]
            ))
        if um.preferences:
            _parts.append("Cares about: " + "; ".join(
                p.get("text", "") for p in um.preferences[-3:]
            ))
        if _parts:
            user_context = "\n" + "\n".join(_parts) + "\n"
    except Exception as e:
        logger.debug("scan_projects: user model unavailable: %s", e)

    opportunities: List[Opportunity] = []

    for key, val in active.items():
        if not isinstance(val, dict):
            continue
        path = val.get("path", "")
        if not path:
            continue

        # Read vision/overview file
        vision_excerpt = _read_vision_file(path)
        if not vision_excerpt:
            continue

        # List what actually exists
        from src.utils.project_context import scan_project_files
        existing_files = scan_project_files(path)
        files_block = ", ".join(existing_files[:20]) if existing_files else "(nothing built yet)"

        desc = val.get("description", key)

        prompt = f"""You are Archi, an autonomous AI agent. Analyze this project and find 2-3 things to BUILD.
{user_context}
PROJECT: {desc}
VISION (from project overview):
{vision_excerpt}

WHAT CURRENTLY EXISTS ON DISK:
{files_block}

{get_user_name()}'s available tools you can use: Python scripts, JSON data files, markdown content,
web research, asking {get_user_name()} questions via Discord, running Python code.

Find 2-3 GAPS where the vision describes something that doesn't exist yet.
Focus on things you can ACTUALLY BUILD with code and files — not documentation gaps.

Priority order:
1. Data structures / databases / trackers the vision describes but don't exist
2. Python scripts / tools that would automate something described in the vision
3. Asking {get_user_name()} for information they have (supplements they take, schedule, preferences)
   so you can populate a tracker or database
4. Integrating or connecting existing files into something functional

DO NOT suggest: writing more markdown reports, researching topics, creating summaries.
DO suggest: building a supplement_tracker.json schema, writing a Python script that
analyzes data, asking {get_user_name()} what supplements they take so you can build their database.

Return ONLY a JSON array:
[{{"type": "build|ask|improve", "description": "actionable goal", "target_file": "workspace/projects/...", "value": 1-10, "hours": 0.2-2.0, "user_value": "why the user cares", "reasoning": "why this matters"}}]
JSON only:"""

        try:
            resp = router.generate(prompt=prompt, max_tokens=600, temperature=0.5)
            text = resp.get("text", "")
            from src.utils.parsing import extract_json_array
            items = extract_json_array(text)
            if not isinstance(items, list):
                continue

            for item in items[:3]:
                if not isinstance(item, dict):
                    continue
                opp = Opportunity(
                    type=item.get("type", "build"),
                    description=item.get("description", ""),
                    target_files=[item.get("target_file", f"workspace/projects/{key}/")],
                    value_score=min(10, max(1, item.get("value", 5))),
                    estimated_hours=max(0.2, min(4.0, item.get("hours", 1.0))),
                    user_value=item.get("user_value", ""),
                    source="project_gap",
                    reasoning=item.get("reasoning", ""),
                )
                if opp.description:
                    opportunities.append(opp)

        except Exception as e:
            logger.debug("scan_projects failed for %s: %s", key, e)

    logger.info("scan_projects: found %d opportunities", len(opportunities))
    return opportunities


# ---------------------------------------------------------------------------
# Scanner 2: Error pattern analysis
# ---------------------------------------------------------------------------

def scan_errors(router: Any) -> List[Opportunity]:
    """Read recent error logs and find repeated patterns Archi could fix.

    Looks at the last 3 days of error logs, groups by module/type, and asks
    the model which ones are fixable. Returns fix opportunities.
    """
    errors_dir = _base_path() / "logs" / "errors"
    if not errors_dir.exists():
        return []

    # Collect recent error lines
    error_lines: List[str] = []
    today = datetime.now().date()
    for days_back in range(3):
        d = today - timedelta(days=days_back)
        log_file = errors_dir / f"{d.isoformat()}.log"
        if not log_file.exists():
            continue
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "ERROR" in line or "EXCEPTION" in line or "Traceback" in line:
                        error_lines.append(line.strip()[:200])
        except Exception as e:
            logger.debug("scan_errors: couldn't read %s: %s", log_file, e)
            continue

    if len(error_lines) < 3:
        logger.debug("scan_errors: fewer than 3 error lines, skipping")
        return []

    # Deduplicate and take a sample
    seen = set()
    unique_errors = []
    for line in error_lines:
        # Normalize timestamps for dedup
        key = line[25:] if len(line) > 25 else line
        if key not in seen:
            seen.add(key)
            unique_errors.append(line)
    unique_errors = unique_errors[:30]

    prompt = f"""You are Archi, an autonomous AI agent. Analyze these error log entries from the last 3 days.

ERRORS ({len(unique_errors)} unique):
{chr(10).join(unique_errors[:20])}

Identify 1-2 ERROR PATTERNS that:
1. Appear repeatedly (not one-offs)
2. Could be fixed by modifying source code
3. Are within Archi's own codebase (src/)

For each fixable pattern, describe:
- What the error is and which module it's in
- What the likely root cause is
- What code change would fix it

DO NOT suggest: restarting services, changing config, or anything requiring external access.
DO suggest: specific code fixes in specific files.

Return ONLY a JSON array:
[{{"type": "fix", "description": "Fix [error] in [module] by [change]", "target_file": "src/...", "value": 1-10, "hours": 0.3-1.0, "user_value": "why fixing this matters", "reasoning": "root cause analysis"}}]
Return empty array [] if no fixable patterns found.
JSON only:"""

    try:
        resp = router.generate(prompt=prompt, max_tokens=500, temperature=0.3)
        text = resp.get("text", "")
        from src.utils.parsing import extract_json_array
        items = extract_json_array(text)
        if not isinstance(items, list):
            return []

        opportunities = []
        for item in items[:2]:
            if not isinstance(item, dict):
                continue
            opp = Opportunity(
                type="fix",
                description=item.get("description", ""),
                target_files=[item.get("target_file", "src/")],
                value_score=min(10, max(1, item.get("value", 6))),
                estimated_hours=max(0.2, min(2.0, item.get("hours", 0.5))),
                user_value=item.get("user_value", ""),
                source="error_pattern",
                reasoning=item.get("reasoning", ""),
            )
            if opp.description:
                opportunities.append(opp)

        logger.info("scan_errors: found %d fixable patterns", len(opportunities))
        return opportunities

    except Exception as e:
        logger.debug("scan_errors failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Scanner 3: Unused capabilities
# ---------------------------------------------------------------------------

_CAPABILITIES = {
    "ask_user": f"Ask {get_user_name()} questions via Discord and wait for a reply",
    "run_python": "Execute Python code to test, analyze, compute, or automate",
    "write_source": "Create or modify Archi's own source code (with approval)",
    "edit_file": "Surgically edit existing source code files",
    "run_command": "Run shell commands (pip, pytest, git, etc.)",
}


def scan_capabilities(
    project_context: dict,
    goal_manager: Any,
    router: Any,
) -> List[Opportunity]:
    """Find underused tools that could unlock new work.

    Checks which PlanExecutor actions have been used in completed tasks.
    Suggests ways to leverage rarely-used capabilities for active projects.
    """
    # Figure out which tools have actually been used
    used_tools: set = set()
    if goal_manager:
        for goal in goal_manager.goals.values():
            for task in goal.tasks:
                desc_lower = (task.description or "").lower()
                for cap in _CAPABILITIES:
                    if cap in desc_lower:
                        used_tools.add(cap)

    unused = {k: v for k, v in _CAPABILITIES.items() if k not in used_tools}
    if not unused:
        logger.debug("scan_capabilities: all capabilities in use")
        return []

    # Build project summary for context
    active = project_context.get("active_projects", {})
    project_lines = []
    for key, val in active.items():
        if isinstance(val, dict):
            project_lines.append(f"- {val.get('description', key)}")

    if not project_lines:
        return []

    unused_block = "\n".join(f"- {k}: {v}" for k, v in unused.items())
    projects_block = "\n".join(project_lines)

    # Load user context for personalized capability suggestions
    user_ctx = ""
    try:
        from src.core.user_model import get_user_model
        um = get_user_model()
        if um.facts:
            user_ctx = f"\nAbout {get_user_name()}: " + "; ".join(
                f.get("text", "") for f in um.facts[-5:]
            ) + "\n"
    except Exception as e:
        logger.debug("scan_capabilities: user model unavailable: %s", e)

    prompt = f"""You are Archi, an autonomous AI agent. You have powerful tools you've never used.
{user_ctx}
UNUSED CAPABILITIES:
{unused_block}

{get_user_name().upper()}'S ACTIVE PROJECTS:
{projects_block}

For each unused capability, suggest ONE specific way it could help {get_user_name()}'s projects.
Focus on practical, surprising applications — things {get_user_name()} wouldn't think to ask for.

Examples:
- ask_user: "Ask {get_user_name()} what supplements they take so I can build a tracked database"
- run_python: "Write and run a Python script to analyze my own error patterns"
- write_source: "Add a new Discord command that shows {get_user_name()}'s health dashboard"

Return ONLY a JSON array:
[{{"type": "connect", "description": "actionable goal using [capability]", "target_file": "workspace/projects/...", "value": 1-10, "hours": 0.3-2.0, "user_value": "why the user benefits", "reasoning": "how this capability unlocks new value"}}]
JSON only:"""

    try:
        resp = router.generate(prompt=prompt, max_tokens=500, temperature=0.6)
        text = resp.get("text", "")
        from src.utils.parsing import extract_json_array
        items = extract_json_array(text)
        if not isinstance(items, list):
            return []

        opportunities = []
        for item in items[:3]:
            if not isinstance(item, dict):
                continue
            opp = Opportunity(
                type="connect",
                description=item.get("description", ""),
                target_files=[item.get("target_file", "workspace/")],
                value_score=min(10, max(1, item.get("value", 6))),
                estimated_hours=max(0.2, min(2.0, item.get("hours", 0.5))),
                user_value=item.get("user_value", ""),
                source="unused_capability",
                reasoning=item.get("reasoning", ""),
            )
            if opp.description:
                opportunities.append(opp)

        logger.info("scan_capabilities: found %d opportunities", len(opportunities))
        return opportunities

    except Exception as e:
        logger.debug("scan_capabilities failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Scanner 4: User context
# ---------------------------------------------------------------------------

def scan_user_context(memory: Any, router: Any) -> List[Opportunity]:
    """Learn from recent conversations what the user wants.

    Queries vector memory and recent conversation logs to find unaddressed
    requests, recurring topics, and implicit needs.
    """
    # Gather recent conversation snippets
    convos_path = _base_path() / "logs" / "conversations.jsonl"
    recent_messages: List[str] = []
    if convos_path.exists():
        try:
            with open(convos_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in lines[-20:]:
                try:
                    entry = json.loads(line)
                    user_msg = entry.get("user_message", "") or entry.get("message", "")
                    if user_msg:
                        recent_messages.append(user_msg[:150])
                except (json.JSONDecodeError, AttributeError):
                    continue
        except Exception as e:
            logger.debug("scan_user_context: couldn't read conversations: %s", e)

    # Gather memory topics
    memory_topics: List[str] = []
    if memory:
        try:
            for query in ["health", "project", "build", "fix", "want"]:
                results = memory.retrieve_relevant(query, n_results=3)
                for m in results.get("semantic", []):
                    if m.get("distance", 2.0) < 1.0:
                        topic = m.get("metadata", {}).get("task_description", m["text"][:80])
                        memory_topics.append(topic.strip())
        except Exception as e:
            logger.debug("scan_user_context: memory retrieval failed: %s", e)

    if not recent_messages and not memory_topics:
        logger.debug("scan_user_context: no conversation data available")
        return []

    convo_block = "\n".join(f"- {m}" for m in recent_messages[-10:]) if recent_messages else "(no recent messages)"
    memory_block = "\n".join(f"- {t[:100]}" for t in memory_topics[:8]) if memory_topics else "(no prior research)"

    prompt = f"""You are Archi, an autonomous AI agent for {get_user_name()}. Analyze recent context to find useful work.

RECENT MESSAGES FROM {get_user_name().upper()}:
{convo_block}

TOPICS IN LONG-TERM MEMORY:
{memory_block}

Based on this, suggest 1-2 things {get_user_name()} would find genuinely useful if you built them.
Think about: what has {get_user_name()} asked about but not followed up on? What patterns suggest
an unmet need? What would save them time or effort?

DO NOT suggest: writing reports, doing more research, creating summaries.
DO suggest: building tools, asking {get_user_name()} for data to populate something, creating
something functional that addresses what they've been talking about.

Return ONLY a JSON array:
[{{"type": "build|ask|improve", "description": "actionable goal", "target_file": "workspace/projects/...", "value": 1-10, "hours": 0.3-2.0, "user_value": "why the user cares", "reasoning": "what context led to this"}}]
JSON only:"""

    try:
        resp = router.generate(prompt=prompt, max_tokens=400, temperature=0.5)
        text = resp.get("text", "")
        from src.utils.parsing import extract_json_array
        items = extract_json_array(text)
        if not isinstance(items, list):
            return []

        opportunities = []
        for item in items[:2]:
            if not isinstance(item, dict):
                continue
            opp = Opportunity(
                type=item.get("type", "build"),
                description=item.get("description", ""),
                target_files=[item.get("target_file", "workspace/")],
                value_score=min(10, max(1, item.get("value", 7))),
                estimated_hours=max(0.2, min(2.0, item.get("hours", 0.5))),
                user_value=item.get("user_value", ""),
                source="user_context",
                reasoning=item.get("reasoning", ""),
            )
            if opp.description:
                opportunities.append(opp)

        logger.info("scan_user_context: found %d opportunities", len(opportunities))
        return opportunities

    except Exception as e:
        logger.debug("scan_user_context failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Combiner: merge, deduplicate, rank
# ---------------------------------------------------------------------------

def combine_and_rank(all_opportunities: List[Opportunity]) -> List[Opportunity]:
    """Deduplicate and rank opportunities by value/effort ratio."""
    if not all_opportunities:
        return []

    # Deduplicate by word overlap
    unique: List[Opportunity] = []
    for opp in all_opportunities:
        words = set(opp.description.lower().split())
        is_dup = False
        for existing in unique:
            existing_words = set(existing.description.lower().split())
            if words and existing_words:
                overlap = len(words & existing_words) / len(words | existing_words)
                if overlap > 0.5:
                    # Keep the higher-scored one
                    if opp.value_score > existing.value_score:
                        unique.remove(existing)
                        unique.append(opp)
                    is_dup = True
                    break
        if not is_dup:
            unique.append(opp)

    # Sort by value per hour (higher is better)
    unique.sort(key=lambda o: o.value_score / max(o.estimated_hours, 0.2), reverse=True)

    logger.info(
        "combine_and_rank: %d unique from %d total",
        len(unique), len(all_opportunities),
    )
    return unique[:7]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def scan_all(
    project_context: dict,
    router: Any,
    goal_manager: Any = None,
    memory: Any = None,
) -> List[Opportunity]:
    """Run all scanners and return ranked opportunities.

    Each scanner is independent and failure-isolated — if one fails,
    the others still contribute.
    """
    logger.info("=== OPPORTUNITY SCAN START ===")
    all_opps: List[Opportunity] = []

    # Scanner 1: Project gaps (highest value — reads actual files)
    try:
        all_opps.extend(scan_projects(project_context, router))
    except Exception as e:
        logger.debug("Project scanner failed: %s", e)

    # Scanner 2: Error patterns (self-improvement)
    try:
        all_opps.extend(scan_errors(router))
    except Exception as e:
        logger.debug("Error scanner failed: %s", e)

    # Scanner 3: Unused capabilities (unlock new value)
    try:
        all_opps.extend(scan_capabilities(project_context, goal_manager, router))
    except Exception as e:
        logger.debug("Capability scanner failed: %s", e)

    # Scanner 4: User context (respond to what the user cares about)
    if memory:
        try:
            all_opps.extend(scan_user_context(memory, router))
        except Exception as e:
            logger.debug("User context scanner failed: %s", e)

    ranked = combine_and_rank(all_opps)
    logger.info("=== OPPORTUNITY SCAN END (%d results) ===", len(ranked))
    return ranked


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_vision_file(project_path: str) -> str:
    """Read and cache the vision/overview file for a project.

    Looks for common names: PROJECT OVERVIEW*, VISION*, README*, overview*.
    Returns first 2000 chars of the first match found.
    """
    now = time.time()
    if project_path in _vision_cache:
        age = now - _vision_cache_ts.get(project_path, 0)
        if age < _VISION_CACHE_TTL:
            return _vision_cache[project_path]

    root = _base_path() / project_path
    if not root.exists():
        return ""

    # Search for vision/overview files
    candidates = [
        "PROJECT OVERVIEW*", "VISION*", "README*", "overview*", "OVERVIEW*",
    ]
    vision_file = None
    try:
        for pattern in candidates:
            matches = list(root.glob(pattern))
            if matches:
                vision_file = matches[0]
                break
        # Also check subdirectories one level deep
        if not vision_file:
            for subdir in root.iterdir():
                if subdir.is_dir():
                    for pattern in candidates:
                        matches = list(subdir.glob(pattern))
                        if matches:
                            vision_file = matches[0]
                            break
                    if vision_file:
                        break
    except Exception as e:
        logger.debug("_read_vision_file: directory traversal error for %s: %s", project_path, e)
        return ""

    if not vision_file or not vision_file.is_file():
        return ""

    try:
        with open(vision_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(2000)
        _vision_cache[project_path] = content
        _vision_cache_ts[project_path] = now
        return content
    except Exception as e:
        logger.debug("_read_vision_file: couldn't read %s: %s", vision_file, e)
        return ""


def infer_opportunity_type(description: str) -> str:
    """Infer opportunity type from a goal description via keywords.

    Used by goal_manager.py to select decomposition hints.
    """
    d = description.lower()

    if any(w in d for w in ("fix ", "repair ", "error", "bug", "crash", "fail")):
        return "fix"
    if any(w in d for w in ("ask jesse", "ask user", "request ", "collect ", "what does jesse")):
        return "ask"
    if any(w in d for w in ("connect", "integrate", "hook up", "wire ", "enable ", "leverage ")):
        return "connect"
    if any(w in d for w in ("improve", "enhance", "optimize", "upgrade", "refine")):
        return "improve"
    # Default to build — it's the most useful
    return "build"
