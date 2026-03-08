"""Capability Assessor — identifies what Archi can't do but wants to.

Session 236: Self-Extension Phase 2.

Periodically gathers evidence from learning system (failed tasks),
worldview (interests, personal projects), content creator (missing
formats/platforms), tool registry (available tools), behavioral rules
(avoidance patterns), and goals (stalled/failed).  Uses model to
identify concrete capability gaps ranked by impact.  Proposes projects
to Jesse via Discord.

Heartbeat integration: Phase 1.5 (every 20 cycles).  Produces at most
1 gap assessment per run.
"""

import json
import logging
import os
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

_ASSESSMENT_PATH = "data/self_extension/assessments.json"
_COOLDOWN_HOURS = 48  # Minimum hours between full assessments
_MAX_EVIDENCE_PER_SOURCE = 10  # Cap evidence items from each source
_MAX_GAPS = 5  # Maximum gaps to identify per assessment
_ASSESSMENT_COST_CAP = 0.08  # USD — keep assessment cheap
_MAX_ASSESSMENTS_STORED = 20  # Keep last N assessments on disk

_lock = threading.Lock()


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class CapabilityGap:
    """A concrete thing Archi wants to do but can't."""
    name: str = ""                    # Short label: "music generation"
    description: str = ""             # What the gap is
    evidence: List[str] = field(default_factory=list)  # Why we think this is a gap
    impact: float = 0.0               # 0-1, how much capability this unlocks
    category: str = ""                # infrastructure / content / integration / skill
    requires_from_jesse: str = ""     # Credentials, accounts, decisions needed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "evidence": self.evidence,
            "impact": self.impact,
            "category": self.category,
            "requires_from_jesse": self.requires_from_jesse,
        }


@dataclass
class ProjectProposal:
    """A proposed project to close a capability gap."""
    gap_name: str = ""
    title: str = ""                   # Project title
    description: str = ""             # What needs to be built
    research_needed: str = ""         # What to research first
    estimated_phases: int = 1         # Rough phase count
    jesse_actions: List[str] = field(default_factory=list)  # What Jesse needs to do
    priority: str = "medium"          # low / medium / high

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gap_name": self.gap_name,
            "title": self.title,
            "description": self.description,
            "research_needed": self.research_needed,
            "estimated_phases": self.estimated_phases,
            "jesse_actions": self.jesse_actions,
            "priority": self.priority,
        }


# ── Persistence ──────────────────────────────────────────────────────

def _assessment_path() -> str:
    return str(_base_path() / _ASSESSMENT_PATH)


def _load_assessments() -> dict:
    """Load assessment history from disk."""
    path = _assessment_path()
    if not os.path.isfile(path):
        return {"assessments": [], "last_assessed": None}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load assessments: %s", e)
        return {"assessments": [], "last_assessed": None}


def _save_assessments(data: dict) -> None:
    """Atomically write assessments to disk."""
    path = _assessment_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Trim to max stored
    if len(data.get("assessments", [])) > _MAX_ASSESSMENTS_STORED:
        data["assessments"] = data["assessments"][-_MAX_ASSESSMENTS_STORED:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


def is_assessment_due() -> bool:
    """Check if enough time has passed since last assessment."""
    data = _load_assessments()
    last = data.get("last_assessed")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        return datetime.now() - last_dt > timedelta(hours=_COOLDOWN_HOURS)
    except (ValueError, TypeError):
        return True


# ── Evidence gathering ───────────────────────────────────────────────

def _gather_failed_tasks(learning_system) -> List[str]:
    """Extract failure patterns from learning system."""
    evidence = []
    try:
        failures = [e for e in learning_system.experiences
                    if e.experience_type == "failure"]
        # Focus on recent failures (last 100)
        for exp in failures[-_MAX_EVIDENCE_PER_SOURCE:]:
            summary = f"Failed: {exp.action[:80]}"
            if exp.lesson:
                summary += f" — lesson: {exp.lesson[:80]}"
            evidence.append(summary)
    except Exception as e:
        logger.debug("Failed to gather failure evidence: %s", e)
    return evidence


def _gather_worldview_gaps() -> List[str]:
    """Extract unmet desires from worldview interests and projects."""
    evidence = []
    try:
        from src.core.worldview import get_interests, get_personal_projects
        # High-curiosity interests that haven't been explored recently
        interests = get_interests(min_curiosity=0.5, limit=_MAX_EVIDENCE_PER_SOURCE)
        for i in interests:
            topic = i.get("topic", "unknown")
            notes = i.get("notes", "")
            evidence.append(f"High interest: {topic}" + (f" ({notes[:60]})" if notes else ""))

        # Stalled personal projects
        active = get_personal_projects(status="active")
        for p in active[:5]:
            title = p.get("title", "unknown")
            sessions = p.get("work_sessions", 0)
            if sessions >= 3:
                evidence.append(f"Stalled project ({sessions} sessions): {title}")
    except Exception as e:
        logger.debug("Worldview evidence gathering failed: %s", e)
    return evidence


def _gather_tool_inventory() -> List[str]:
    """List available tools for the model to reason about gaps."""
    tools = []
    try:
        from src.tools.tool_registry import get_shared_registry
        registry = get_shared_registry()
        available = sorted(registry.tools.keys())
        tools.append(f"Available tools: {', '.join(available)}")
        if registry._mcp_tools:
            tools.append(f"MCP tools: {', '.join(sorted(registry._mcp_tools))}")
    except Exception as e:
        logger.debug("Tool inventory gathering failed: %s", e)
    return tools


def _gather_avoidance_patterns() -> List[str]:
    """Get behavioral avoidance rules that might indicate missing capabilities."""
    evidence = []
    try:
        from src.core.behavioral_rules import load as load_rules
        rules = load_rules()
        for rule in rules.get("avoidance", [])[:_MAX_EVIDENCE_PER_SOURCE]:
            pattern = rule.get("pattern", "")
            reason = rule.get("reason", "")
            if pattern:
                evidence.append(f"Avoidance rule: {pattern[:80]} — {reason[:60]}")
    except Exception as e:
        logger.debug("Avoidance pattern gathering failed: %s", e)
    return evidence


def _gather_content_capabilities() -> List[str]:
    """Assess content creation capabilities and gaps."""
    evidence = []
    try:
        # Check which platforms have credentials configured
        from src.utils.config import get_email_config
        env = os.environ

        platforms = {
            "GitHub Blog": bool(env.get("GITHUB_PAT") and env.get("GITHUB_BLOG_REPO")),
            "Twitter/X": bool(env.get("TWITTER_API_KEY")),
            "Reddit": bool(env.get("REDDIT_CLIENT_ID")),
            "YouTube": bool(env.get("YOUTUBE_REFRESH_TOKEN")),
            "Facebook": bool(env.get("META_PAGE_ACCESS_TOKEN")),
            "Instagram": bool(env.get("META_INSTAGRAM_ACCESS_TOKEN") or env.get("META_PAGE_ACCESS_TOKEN")),
            "Replicate/Flux": bool(env.get("REPLICATE_API_TOKEN")),
        }

        configured = [p for p, v in platforms.items() if v]
        unconfigured = [p for p, v in platforms.items() if not v]

        if configured:
            evidence.append(f"Content platforms configured: {', '.join(configured)}")
        if unconfigured:
            evidence.append(f"Content platforms NOT configured: {', '.join(unconfigured)}")

        # Check for content format capabilities
        evidence.append("Content formats: blog, tweet, tweet_thread, reddit, video_script")
        evidence.append("Missing formats: music/audio, carousel images, short-form video")
    except Exception as e:
        logger.debug("Content capability assessment failed: %s", e)
    return evidence


def _gather_stalled_goals() -> List[str]:
    """Find goals that repeatedly failed or stalled."""
    evidence = []
    try:
        goals_path = str(_base_path() / "data" / "goals_state.json")
        if not os.path.isfile(goals_path):
            return evidence
        with open(goals_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        goals = data.get("goals", [])
        for g in goals:
            tasks = g.get("tasks", [])
            failed_count = sum(1 for t in tasks if t.get("status") == "FAILED")
            if failed_count >= 2:
                evidence.append(
                    f"Goal with {failed_count} failed tasks: {g.get('description', '')[:80]}"
                )
    except Exception as e:
        logger.debug("Stalled goals gathering failed: %s", e)
    return evidence[:_MAX_EVIDENCE_PER_SOURCE]


def gather_all_evidence(learning_system=None) -> Dict[str, List[str]]:
    """Collect evidence from all sources. Returns dict keyed by source name."""
    evidence = {}
    if learning_system:
        evidence["failed_tasks"] = _gather_failed_tasks(learning_system)
    evidence["worldview"] = _gather_worldview_gaps()
    evidence["tools"] = _gather_tool_inventory()
    evidence["avoidance_patterns"] = _gather_avoidance_patterns()
    evidence["content_capabilities"] = _gather_content_capabilities()
    evidence["stalled_goals"] = _gather_stalled_goals()
    return evidence


# ── Model-based gap analysis ─────────────────────────────────────────

_ASSESS_PROMPT = """You are Archi's capability assessor. Analyze the evidence below and identify
concrete capability gaps — things Archi wants to do or should be able to do, but currently can't.

EVIDENCE:
{evidence_text}

EXISTING CAPABILITIES (already built, don't suggest these):
- Web search (DuckDuckGo + Tavily deep research)
- Email (send/receive via Outlook SMTP/IMAP)
- Content creation (blog, tweets, reddit posts, video scripts)
- Publishing (GitHub Pages, Twitter, Reddit, YouTube, Facebook, Instagram)
- Image generation (local SDXL)
- Calendar reading (ICS feeds)
- Weather + news (morning digest)
- File management (create, edit, read files)
- Browser automation (Playwright)
- Scheduled tasks (cron-based)
- Self-extending skills (sandboxed Python modules)
- Learning from experience, worldview, behavioral rules

Identify 1-3 CONCRETE capability gaps. For each, provide:
- name: short label (e.g., "music_generation", "text_to_image_with_text_rendering")
- description: what the gap is and why it matters
- impact: 0.0-1.0 (how much new capability this would unlock)
- category: one of infrastructure, content, integration, skill
- requires_from_jesse: what Jesse needs to provide (credentials, accounts, decisions), or "nothing" if self-serviceable
- evidence_refs: which evidence items support this gap

IMPORTANT:
- Only identify gaps with REAL evidence. Don't hallucinate needs.
- Focus on gaps that are ACTIONABLE — things that can actually be built.
- Prioritize gaps that unlock the most new capabilities.
- Don't suggest re-building things that already work.

Respond in JSON:
{{"gaps": [
  {{"name": "...", "description": "...", "impact": 0.8, "category": "...",
    "requires_from_jesse": "...", "evidence_refs": ["..."]}}
]}}"""


def _parse_gaps(raw: str) -> List[CapabilityGap]:
    """Parse model response into CapabilityGap objects."""
    # Extract JSON from response
    try:
        # Try direct parse first
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try extracting JSON block
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start < 0 or end <= start:
            logger.warning("No JSON found in assessment response")
            return []
        try:
            data = json.loads(raw[start:end])
        except json.JSONDecodeError:
            logger.warning("Failed to parse assessment JSON")
            return []

    gaps = []
    for item in data.get("gaps", [])[:_MAX_GAPS]:
        gap = CapabilityGap(
            name=item.get("name", "unknown"),
            description=item.get("description", ""),
            evidence=item.get("evidence_refs", []),
            impact=min(1.0, max(0.0, float(item.get("impact", 0.5)))),
            category=item.get("category", "infrastructure"),
            requires_from_jesse=item.get("requires_from_jesse", ""),
        )
        gaps.append(gap)

    # Sort by impact descending
    gaps.sort(key=lambda g: g.impact, reverse=True)
    return gaps


async def assess(router, learning_system=None) -> List[CapabilityGap]:
    """Run a full capability assessment.

    Gathers evidence from all sources, uses model to identify gaps,
    persists results, and returns ranked gaps.

    Args:
        router: Model router for LLM calls.
        learning_system: Optional LearningSystem for failure data.

    Returns:
        List of CapabilityGap, sorted by impact descending. Empty on error.
    """
    t0 = time.time()
    evidence = gather_all_evidence(learning_system)

    # Build evidence text for prompt
    parts = []
    for source, items in evidence.items():
        if items:
            parts.append(f"[{source}]")
            for item in items:
                parts.append(f"  - {item}")
    evidence_text = "\n".join(parts)

    if not evidence_text.strip():
        logger.info("No evidence gathered — skipping assessment")
        return []

    prompt = _ASSESS_PROMPT.format(evidence_text=evidence_text)

    try:
        response = router.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1200,
        )
        cost = getattr(response, "cost", 0.0) or 0.0
        raw = response.content if hasattr(response, "content") else str(response)

        # Handle dict response from router
        if isinstance(response, dict):
            raw = response.get("content", response.get("text", str(response)))
            cost = response.get("cost", 0.0) or 0.0

        if cost > _ASSESSMENT_COST_CAP:
            logger.warning("Assessment cost $%.4f exceeded cap $%.2f", cost, _ASSESSMENT_COST_CAP)

    except Exception as e:
        logger.error("Assessment model call failed: %s", e)
        return []

    gaps = _parse_gaps(raw)
    elapsed = time.time() - t0

    # Persist
    with _lock:
        data = _load_assessments()
        data["last_assessed"] = datetime.now().isoformat()
        data["assessments"].append({
            "timestamp": datetime.now().isoformat(),
            "gaps": [g.to_dict() for g in gaps],
            "evidence_sources": list(evidence.keys()),
            "evidence_count": sum(len(v) for v in evidence.values()),
            "elapsed_seconds": round(elapsed, 1),
            "cost": cost,
        })
        _save_assessments(data)

    logger.info("Capability assessment complete: %d gaps found in %.1fs ($%.4f)",
                len(gaps), elapsed, cost)
    return gaps


# ── Project proposal ─────────────────────────────────────────────────

_PROPOSE_PROMPT = """You are Archi's project planner. Given a capability gap, propose a project to close it.

GAP:
Name: {name}
Description: {description}
Impact: {impact}
Category: {category}
Jesse needs to provide: {requires}

Propose a concrete project:
- title: clear project name
- description: 2-3 sentences on what needs to be built
- research_needed: what Archi should research first (APIs, docs, alternatives)
- estimated_phases: number of implementation phases (1-5)
- jesse_actions: list of specific things Jesse needs to do
- priority: low/medium/high based on impact and effort

Respond in JSON:
{{"title": "...", "description": "...", "research_needed": "...",
  "estimated_phases": 3, "jesse_actions": ["..."], "priority": "high"}}"""


async def propose_project(gap: CapabilityGap, router) -> Optional[ProjectProposal]:
    """Generate a project proposal for a capability gap.

    Args:
        gap: The identified capability gap.
        router: Model router for LLM calls.

    Returns:
        ProjectProposal or None on failure.
    """
    prompt = _PROPOSE_PROMPT.format(
        name=gap.name,
        description=gap.description,
        impact=gap.impact,
        category=gap.category,
        requires=gap.requires_from_jesse or "nothing",
    )

    try:
        response = router.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=600,
        )
        raw = response.content if hasattr(response, "content") else str(response)
        if isinstance(response, dict):
            raw = response.get("content", response.get("text", str(response)))
    except Exception as e:
        logger.error("Project proposal model call failed: %s", e)
        return None

    # Parse JSON
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start:end])
        except json.JSONDecodeError:
            return None

    return ProjectProposal(
        gap_name=gap.name,
        title=data.get("title", gap.name),
        description=data.get("description", gap.description),
        research_needed=data.get("research_needed", ""),
        estimated_phases=min(5, max(1, int(data.get("estimated_phases", 2)))),
        jesse_actions=data.get("jesse_actions", []),
        priority=data.get("priority", "medium"),
    )


# ── Formatting for Discord ───────────────────────────────────────────

def format_gap_message(gap: CapabilityGap, proposal: Optional[ProjectProposal] = None) -> str:
    """Format a gap + optional proposal as a Discord-friendly message.

    Kept concise — one idea, not a wall of text.
    """
    parts = [f"**Capability gap:** {gap.name}"]
    parts.append(gap.description[:200])

    if proposal:
        parts.append(f"\n**Proposal:** {proposal.title}")
        parts.append(proposal.description[:200])
        if proposal.jesse_actions:
            actions = "; ".join(proposal.jesse_actions[:3])
            parts.append(f"**What I need from you:** {actions}")
        parts.append(f"Estimated: {proposal.estimated_phases} phase(s). Want me to go for it?")

    return "\n".join(parts)


def get_recent_gaps(limit: int = 5) -> List[dict]:
    """Get gaps from the most recent assessment."""
    data = _load_assessments()
    assessments = data.get("assessments", [])
    if not assessments:
        return []
    latest = assessments[-1]
    return latest.get("gaps", [])[:limit]


def get_assessment_stats() -> Dict[str, Any]:
    """Return summary stats for diagnostics."""
    data = _load_assessments()
    assessments = data.get("assessments", [])
    return {
        "total_assessments": len(assessments),
        "last_assessed": data.get("last_assessed"),
        "latest_gaps": len(assessments[-1].get("gaps", [])) if assessments else 0,
    }


# ── Pending proposal tracking (session 238 — Phase 4 wiring) ────────
# Stores the most recent proposal sent to Jesse so the approval handler
# in discord_bot.py can invoke the strategic planner when Jesse says "go for it".

_pending_proposal: Optional[ProjectProposal] = None
_pending_gap: Optional[CapabilityGap] = None


def set_pending_proposal(gap: CapabilityGap, proposal: ProjectProposal) -> None:
    """Store a proposal awaiting Jesse's approval."""
    global _pending_proposal, _pending_gap
    with _lock:
        _pending_proposal = proposal
        _pending_gap = gap


def get_pending_proposal() -> Optional[tuple]:
    """Get the pending proposal+gap, if any. Returns (gap, proposal) or None."""
    with _lock:
        if _pending_proposal and _pending_gap:
            return (_pending_gap, _pending_proposal)
    return None


def clear_pending_proposal() -> None:
    """Clear the pending proposal (after acceptance or rejection)."""
    global _pending_proposal, _pending_gap
    with _lock:
        _pending_proposal = None
        _pending_gap = None
