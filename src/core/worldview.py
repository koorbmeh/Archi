"""Evolving worldview — Archi's opinions, preferences, and interests.

Session 199: initial implementation (DESIGN_BECOMING_SOMEONE.md Phase 2).

Unlike personality.yaml (static, hand-written), the worldview evolves from
actual experiences.  Opinions trace back to evidence; preferences have
strength scores; interests have curiosity levels that decay with time.

Data lives in ``data/worldview.json``.  Thread-safe via module lock.
Automatic pruning: low-confidence opinions and stale interests decay
on each save.
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

_WORLDVIEW_PATH = "data/worldview.json"
_DT_FMT = "%Y-%m-%dT%H:%M:%S"
_DATE_FMT = "%Y-%m-%d"

# Limits
_MAX_OPINIONS = 50
_MAX_PREFERENCES = 50
_MAX_INTERESTS = 30
_OPINION_MIN_CONFIDENCE = 0.15  # Below this, pruned on save
_INTEREST_STALE_DAYS = 30  # Interests not explored in this many days decay
_INTEREST_DECAY_AMOUNT = 0.15  # Curiosity decay per prune cycle
_CONFIDENCE_DECAY_RATE = 0.05  # Opinions not updated in 30 days lose this

_lock = threading.Lock()


# ── Persistence ──────────────────────────────────────────────────────

def _worldview_path() -> str:
    return str(_base_path() / _WORLDVIEW_PATH)


def _empty_worldview() -> dict:
    return {"opinions": [], "preferences": [], "interests": [], "pending_revisions": []}


def load() -> dict:
    """Load worldview from disk.  Returns empty skeleton if missing."""
    path = _worldview_path()
    if not os.path.isfile(path):
        return _empty_worldview()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Ensure all keys present
        for key in ("opinions", "preferences", "interests", "pending_revisions"):
            if key not in data:
                data[key] = []
        return data
    except Exception as e:
        logger.error("Failed to load worldview: %s", e)
        return _empty_worldview()


def save(data: dict) -> None:
    """Atomically write worldview to disk.  Prunes before writing."""
    _prune(data)
    path = _worldview_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        dir_name = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        logger.error("Failed to save worldview: %s", e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Pruning / decay ─────────────────────────────────────────────────

def _prune(data: dict) -> None:
    """Prune low-confidence opinions, stale interests, enforce caps."""
    today = date.today()

    # Decay old opinions
    opinions = data.get("opinions", [])
    for op in opinions:
        last = _parse_date(op.get("last_updated"))
        if last and (today - last).days > 30:
            op["confidence"] = max(0.0, op.get("confidence", 0.5) - _CONFIDENCE_DECAY_RATE)
    # Remove below threshold
    data["opinions"] = [
        o for o in opinions if o.get("confidence", 0.5) >= _OPINION_MIN_CONFIDENCE
    ][:_MAX_OPINIONS]

    # Decay stale interests
    interests = data.get("interests", [])
    for interest in interests:
        last = _parse_date(interest.get("last_explored"))
        if last and (today - last).days > _INTEREST_STALE_DAYS:
            interest["curiosity_level"] = max(
                0.0, interest.get("curiosity_level", 0.5) - _INTEREST_DECAY_AMOUNT
            )
    # Remove dead interests
    data["interests"] = [
        i for i in interests if i.get("curiosity_level", 0.5) > 0.1
    ][:_MAX_INTERESTS]

    # Cap preferences
    data["preferences"] = data.get("preferences", [])[:_MAX_PREFERENCES]


def _parse_date(s: Optional[str]) -> Optional[date]:
    """Parse YYYY-MM-DD date string, return None on failure."""
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], _DATE_FMT).date()
    except (ValueError, TypeError):
        return None


# ── Opinions ─────────────────────────────────────────────────────────

def add_opinion(
    topic: str,
    position: str,
    confidence: float = 0.5,
    basis: str = "",
) -> None:
    """Add or update an opinion.  If topic exists, update it."""
    with _lock:
        data = load()
        opinions = data.get("opinions", [])

        existing = _find_by_field(opinions, "topic", topic)
        today_str = date.today().strftime(_DATE_FMT)

        if existing is not None:
            old_position = existing.get("position", "")
            old_confidence = existing.get("confidence", 0.5)
            new_confidence = min(1.0, max(0.0, confidence))

            existing["position"] = position
            existing["confidence"] = new_confidence
            existing["basis"] = basis
            existing["last_updated"] = today_str
            if "history" not in existing:
                existing["history"] = []
            existing["history"].append({
                "position": position, "confidence": confidence,
                "date": today_str,
            })
            # Keep history bounded
            existing["history"] = existing["history"][-10:]

            # Detect significant opinion change → flag for proactive notification (session 201)
            confidence_delta = abs(new_confidence - old_confidence)
            position_changed = position.lower().strip() != old_position.lower().strip()
            if position_changed and (confidence_delta >= 0.3 or new_confidence >= 0.6):
                revisions = data.get("pending_revisions", [])
                # Don't duplicate if same topic is already pending
                if not any(r.get("topic", "").lower() == topic.lower() for r in revisions):
                    revisions.append({
                        "topic": topic,
                        "old_position": old_position,
                        "new_position": position,
                        "old_confidence": old_confidence,
                        "new_confidence": new_confidence,
                        "date": today_str,
                    })
                    # Cap pending revisions at 5
                    data["pending_revisions"] = revisions[-5:]
                    logger.info("Opinion revision flagged: %s", topic)
        else:
            opinions.append({
                "topic": topic,
                "position": position,
                "confidence": min(1.0, max(0.0, confidence)),
                "basis": basis,
                "formed": today_str,
                "last_updated": today_str,
                "history": [],
            })

        data["opinions"] = opinions
        save(data)
        logger.debug("Worldview opinion updated: %s (%.2f)", topic, confidence)


def get_opinion(topic: str) -> Optional[dict]:
    """Get a specific opinion by topic.  Returns None if not found."""
    with _lock:
        data = load()
        return _find_by_field(data.get("opinions", []), "topic", topic)


# ── Opinion revisions ("I changed my mind", session 201) ──────────

def get_pending_revisions() -> List[dict]:
    """Get pending opinion revisions for proactive notification."""
    with _lock:
        data = load()
        return list(data.get("pending_revisions", []))


def clear_revision(topic: str) -> None:
    """Remove a revision from pending list after notification is sent."""
    with _lock:
        data = load()
        revisions = data.get("pending_revisions", [])
        data["pending_revisions"] = [
            r for r in revisions if r.get("topic", "").lower() != topic.lower()
        ]
        save(data)


def clear_all_revisions() -> None:
    """Clear all pending revisions (e.g. after batch notification)."""
    with _lock:
        data = load()
        data["pending_revisions"] = []
        save(data)


def get_strong_opinions(min_confidence: float = 0.6, limit: int = 5) -> List[dict]:
    """Get opinions above a confidence threshold, sorted by confidence desc."""
    with _lock:
        data = load()
        opinions = data.get("opinions", [])
    strong = [o for o in opinions if o.get("confidence", 0) >= min_confidence]
    strong.sort(key=lambda o: o.get("confidence", 0), reverse=True)
    return strong[:limit]


# ── Preferences ──────────────────────────────────────────────────────

def add_preference(
    domain: str,
    preference: str,
    strength: float = 0.5,
    evidence_count: int = 1,
) -> None:
    """Add or strengthen a preference.  If domain+preference match, update."""
    with _lock:
        data = load()
        prefs = data.get("preferences", [])

        # Match on domain + similar preference text
        existing = None
        for p in prefs:
            if p.get("domain") == domain and p.get("preference") == preference:
                existing = p
                break

        if existing is not None:
            existing["strength"] = min(1.0, max(0.0, strength))
            existing["evidence_count"] = existing.get("evidence_count", 0) + evidence_count
            existing["last_updated"] = date.today().strftime(_DATE_FMT)
        else:
            prefs.append({
                "domain": domain,
                "preference": preference,
                "strength": min(1.0, max(0.0, strength)),
                "evidence_count": evidence_count,
                "last_updated": date.today().strftime(_DATE_FMT),
            })

        data["preferences"] = prefs
        save(data)
        logger.debug("Worldview preference updated: %s — %s", domain, preference[:60])


def get_preferences(domain: Optional[str] = None, limit: int = 10) -> List[dict]:
    """Get preferences, optionally filtered by domain."""
    with _lock:
        data = load()
        prefs = data.get("preferences", [])
    if domain:
        prefs = [p for p in prefs if p.get("domain") == domain]
    prefs.sort(key=lambda p: p.get("strength", 0), reverse=True)
    return prefs[:limit]


# ── Interests ────────────────────────────────────────────────────────

def add_interest(
    topic: str,
    curiosity_level: float = 0.5,
    notes: str = "",
) -> None:
    """Add or update an interest.  Refreshes last_explored on update."""
    with _lock:
        data = load()
        interests = data.get("interests", [])

        existing = _find_by_field(interests, "topic", topic)
        today_str = date.today().strftime(_DATE_FMT)

        if existing is not None:
            existing["curiosity_level"] = min(1.0, max(0.0, curiosity_level))
            existing["last_explored"] = today_str
            if notes:
                existing["notes"] = notes
        else:
            interests.append({
                "topic": topic,
                "curiosity_level": min(1.0, max(0.0, curiosity_level)),
                "last_explored": today_str,
                "notes": notes,
            })

        data["interests"] = interests
        save(data)
        logger.debug("Worldview interest updated: %s (%.2f)", topic, curiosity_level)


def get_interests(min_curiosity: float = 0.3, limit: int = 10) -> List[dict]:
    """Get interests above a curiosity threshold, sorted desc."""
    with _lock:
        data = load()
        interests = data.get("interests", [])
    filtered = [i for i in interests if i.get("curiosity_level", 0) >= min_curiosity]
    filtered.sort(key=lambda i: i.get("curiosity_level", 0), reverse=True)
    return filtered[:limit]


# ── Query for prompt injection ───────────────────────────────────────

def get_worldview_context(max_chars: int = 600) -> str:
    """Build a compact worldview summary for injection into system prompts.

    Returns a string like:
        'Your opinions: [topic] — [position] (confidence 0.8). ...'
    Capped at max_chars to avoid bloating the prompt.
    """
    parts = []

    # Strong opinions
    opinions = get_strong_opinions(min_confidence=0.5, limit=3)
    if opinions:
        op_lines = []
        for o in opinions:
            op_lines.append(
                f"{o['topic']}: {o['position']} (confidence {o.get('confidence', 0.5):.1f})"
            )
        parts.append("Your opinions from experience: " + "; ".join(op_lines) + ".")

    # Top preferences
    prefs = get_preferences(limit=3)
    if prefs:
        pref_lines = [f"{p['domain']}: {p['preference']}" for p in prefs]
        parts.append("Your preferences: " + "; ".join(pref_lines) + ".")

    # Current interests
    interests = get_interests(min_curiosity=0.5, limit=3)
    if interests:
        int_lines = [f"{i['topic']} (curiosity {i.get('curiosity_level', 0.5):.1f})" for i in interests]
        parts.append("Currently curious about: " + ", ".join(int_lines) + ".")

    result = " ".join(parts)
    if len(result) > max_chars:
        result = result[:max_chars - 3] + "..."
    return result


# ── Post-task reflection ─────────────────────────────────────────────

def reflect_on_task(
    task_description: str,
    goal_description: str,
    outcome: str,
    success: bool,
    model: Any = None,
) -> Optional[dict]:
    """Post-task reflection: did this experience change any views?

    If a model is provided, uses it for deeper reflection.  Otherwise
    does lightweight keyword-based updates (bump confidence on matching
    opinions, note new interests from task domain).

    Returns a dict of changes made, or None if nothing changed.
    """
    changes = {}

    if model:
        changes = _model_reflection(task_description, goal_description, outcome, success, model)
    else:
        changes = _lightweight_reflection(task_description, goal_description, outcome, success)

    if changes:
        logger.info("Worldview updated after task: %s", list(changes.keys()))
    return changes if changes else None


def _lightweight_reflection(
    task_description: str,
    goal_description: str,
    outcome: str,
    success: bool,
) -> dict:
    """Quick keyword-based worldview update (no model call)."""
    changes = {}
    combined = f"{task_description} {goal_description} {outcome}".lower()

    # Check existing opinions for relevance — reinforce or weaken
    with _lock:
        data = load()

    for opinion in data.get("opinions", []):
        topic = opinion.get("topic", "").lower()
        if not topic:
            continue
        # Simple keyword match: topic words appear in task context
        topic_words = {w for w in topic.split() if len(w) >= 3}
        context_words = {w for w in combined.split() if len(w) >= 3}
        if len(topic_words & context_words) >= 2:
            conf = opinion.get("confidence", 0.5)
            if success:
                new_conf = min(1.0, conf + 0.05)
            else:
                new_conf = max(0.0, conf - 0.05)
            if abs(new_conf - conf) > 0.01:
                add_opinion(
                    opinion["topic"], opinion["position"],
                    new_conf, opinion.get("basis", ""),
                )
                changes[f"opinion:{opinion['topic']}"] = f"{conf:.2f} -> {new_conf:.2f}"

    return changes


def _model_reflection(
    task_description: str,
    goal_description: str,
    outcome: str,
    success: bool,
    model: Any,
) -> dict:
    """Use model to reflect on task outcome and update worldview."""
    # Get current worldview for context
    context = get_worldview_context(max_chars=400)
    status = "succeeded" if success else "failed"

    prompt = f"""You just completed a task. Reflect on whether this changes any of your views.

Task: {task_description}
Goal: {goal_description}
Outcome: {outcome[:300]}
Status: {status}

Current worldview: {context if context else "No established views yet."}

If this experience suggests a new opinion, strengthens/weakens an existing one, reveals a new preference, or sparks a new interest, return a JSON object:
{{
  "new_opinions": [{{"topic": "...", "position": "...", "confidence": 0.5, "basis": "..."}}],
  "updated_opinions": [{{"topic": "...", "confidence_delta": 0.1}}],
  "new_preferences": [{{"domain": "...", "preference": "...", "strength": 0.5}}],
  "new_interests": [{{"topic": "...", "curiosity_level": 0.5, "notes": "..."}}]
}}

Only include sections where you have something to say. Return {{}} if nothing changed.
Keep it grounded — only form views from direct experience, not speculation."""

    try:
        response = model.generate(prompt, max_tokens=400, temperature=0.4)
        text = response.get("text", "").strip()
        if not text:
            return {}

        from src.utils.parsing import extract_json
        updates = extract_json(text)
        if not isinstance(updates, dict):
            return {}

        return _apply_model_updates(updates)
    except Exception as e:
        logger.debug("Model reflection failed: %s", e)
        return {}


def _apply_model_updates(updates: dict) -> dict:
    """Apply updates from model reflection to worldview."""
    changes = {}

    for op in updates.get("new_opinions", []):
        if isinstance(op, dict) and "topic" in op and "position" in op:
            add_opinion(
                op["topic"], op["position"],
                op.get("confidence", 0.5), op.get("basis", ""),
            )
            changes[f"new_opinion:{op['topic']}"] = op["position"]

    for op in updates.get("updated_opinions", []):
        if isinstance(op, dict) and "topic" in op:
            existing = get_opinion(op["topic"])
            if existing:
                delta = op.get("confidence_delta", 0.0)
                new_conf = existing.get("confidence", 0.5) + delta
                add_opinion(
                    existing["topic"], existing["position"],
                    new_conf, existing.get("basis", ""),
                )
                changes[f"updated_opinion:{op['topic']}"] = f"delta {delta:+.2f}"

    for pref in updates.get("new_preferences", []):
        if isinstance(pref, dict) and "domain" in pref and "preference" in pref:
            add_preference(
                pref["domain"], pref["preference"],
                pref.get("strength", 0.5),
            )
            changes[f"new_preference:{pref['domain']}"] = pref["preference"]

    for interest in updates.get("new_interests", []):
        if isinstance(interest, dict) and "topic" in interest:
            add_interest(
                interest["topic"],
                interest.get("curiosity_level", 0.5),
                interest.get("notes", ""),
            )
            changes[f"new_interest:{interest['topic']}"] = interest.get("notes", "")

    return changes


# ── Helpers ──────────────────────────────────────────────────────────

def _find_by_field(items: list, field: str, value: str) -> Optional[dict]:
    """Find first item in list where item[field] matches value (case-insensitive)."""
    value_lower = value.lower()
    for item in items:
        if item.get(field, "").lower() == value_lower:
            return item
    return None
