"""
User Preferences — Persistent memory of what Archi learns about Jesse.

Accumulates notes from conversations: preferences, reactions, things tried,
health observations, etc.  Stored as flat notes with categories and tags
for easy relevance-based retrieval.

Designed for budget-conscious operation:
- Rule-based pattern matching (free) detects preference signals
- Optional model call only when rule-based finds something to refine
- Batch saves to minimize disk I/O
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# Singleton instance
_instance: Optional["UserPreferences"] = None

# Categories for preference notes
CATEGORIES = (
    "supplement", "health", "fitness", "food", "financial",
    "preference", "reaction", "project", "communication", "general",
)

# ── Rule-based preference signal patterns ─────────────────────────────

_SUPPLEMENT_PATTERN = re.compile(
    r"(?:i\s+)?(?:tried|taking|started|stopped|using|quit|dropped|added|take|use)\s+"
    r"([\w][\w\s\-]{2,35})",
    re.IGNORECASE,
)

_REACTION_PATTERN = re.compile(
    r"(?:gives?\s+me|gave\s+me|causes?\s*(?:me)?|caused\s*(?:me)?|makes?\s+me|made\s+me)\s+"
    r"(.+?)[\.,!?]",
    re.IGNORECASE,
)

_PREFERENCE_PATTERN = re.compile(
    r"(?:i\s+)?(?:don'?t\s+|do\s+not\s+)?(?:like|prefer|want|hate|love|enjoy|dislike|avoid)\s+"
    r"(.+?)[\.,!?]",
    re.IGNORECASE,
)

_EXPERIENCE_PATTERN = re.compile(
    r"(?:i\s+)?(?:noticed|found\s+that|realized|discovered|learned)\s+"
    r"(.+?)[\.,!?]",
    re.IGNORECASE,
)

# Health/supplement context keywords that confirm a match is relevant
_HEALTH_KEYWORDS = {
    "supplement", "vitamin", "mineral", "mg", "dose", "dosage",
    "sleep", "energy", "pain", "anxiety", "mood", "weight",
    "exercise", "workout", "diet", "nutrition", "protein",
    "creatine", "magnesium", "zinc", "omega", "ashwagandha",
    "melatonin", "caffeine", "iron", "vitamin d", "b12",
    "probiotic", "collagen", "turmeric", "fish oil",
}


def get_preferences() -> "UserPreferences":
    """Return the singleton UserPreferences instance (lazy-load)."""
    global _instance
    if _instance is None:
        _instance = UserPreferences()
    return _instance


class UserPreferences:
    """Persistent store for things Archi learns about Jesse."""

    _FLUSH_INTERVAL = 3  # Save to disk every N new notes

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else _base_path() / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.data_dir / "user_preferences.json"

        self.version: int = 1
        self.last_updated: str = ""
        self.notes: List[Dict[str, Any]] = []
        self._dirty_count: int = 0

        self._load()
        logger.info(
            "UserPreferences initialized (%d notes from %s)",
            len(self.notes), self._file,
        )

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load preferences from disk."""
        if not self._file.exists():
            return
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.version = data.get("version", 1)
            self.last_updated = data.get("last_updated", "")
            self.notes = data.get("notes", [])
        except Exception as e:
            logger.warning("Could not load user preferences: %s", e)

    def save(self) -> None:
        """Atomically write preferences to disk."""
        self.last_updated = datetime.now().isoformat()
        data = {
            "version": self.version,
            "last_updated": self.last_updated,
            "notes": self.notes,
        }
        tmp = self._file.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            tmp.replace(self._file)
            self._dirty_count = 0
        except Exception as e:
            logger.warning("Could not save user preferences: %s", e)
            # Clean up temp file on failure
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def flush(self) -> None:
        """Force save if there are unsaved changes."""
        if self._dirty_count > 0:
            self.save()

    def _maybe_flush(self) -> None:
        """Save if enough unsaved changes have accumulated."""
        self._dirty_count += 1
        if self._dirty_count >= self._FLUSH_INTERVAL:
            self.save()

    # ── Note management ──────────────────────────────────────────────

    def add_note(
        self,
        category: str,
        text: str,
        tags: Optional[List[str]] = None,
        source: str = "conversation",
    ) -> Optional[str]:
        """Add a preference note, deduplicating against existing notes.

        If a note with high tag+category overlap exists, update it instead
        of creating a duplicate.

        Args:
            category: One of CATEGORIES (supplement, health, etc.)
            text: Human-readable note text
            tags: Keywords for relevance matching
            source: Where this was learned (discord, web, etc.)

        Returns:
            Note ID if created/updated, None if rejected as duplicate.
        """
        if not text or not text.strip():
            return None

        category = category.lower().strip()
        if category not in CATEGORIES:
            category = "general"
        tags = [t.lower().strip() for t in (tags or []) if t.strip()]
        text = text.strip()

        # Check for near-duplicate (same category + high tag overlap)
        existing = self._find_duplicate(category, tags, text)
        if existing:
            # Update existing note instead of creating new one
            existing["text"] = text
            existing["tags"] = list(set(existing.get("tags", []) + tags))
            existing["source"] = source
            existing["created_at"] = datetime.now().isoformat()
            logger.info("Updated existing preference note: %s", existing["id"])
            self._maybe_flush()
            return existing["id"]

        note_id = f"note_{uuid.uuid4().hex[:8]}"
        note = {
            "id": note_id,
            "category": category,
            "text": text,
            "tags": tags,
            "source": source,
            "created_at": datetime.now().isoformat(),
            "superseded_by": None,
        }
        self.notes.append(note)
        logger.info("Added preference note [%s]: %s", category, text[:60])
        self._maybe_flush()
        return note_id

    def _find_duplicate(
        self, category: str, tags: List[str], text: str
    ) -> Optional[Dict[str, Any]]:
        """Find an existing note that's a near-duplicate.

        Uses Jaccard similarity on tags within the same category.
        """
        if not tags:
            return None
        tag_set = set(tags)

        for note in self.notes:
            if note.get("superseded_by"):
                continue
            if note.get("category") != category:
                continue
            existing_tags = set(note.get("tags", []))
            if not existing_tags:
                continue
            overlap = len(tag_set & existing_tags)
            union = len(tag_set | existing_tags)
            if union > 0 and overlap / union > 0.6:
                return note
        return None

    # ── Querying ─────────────────────────────────────────────────────

    def get_relevant(self, topic: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get notes relevant to a topic by keyword matching.

        Args:
            topic: Topic string to match against tags and note text.
            limit: Max notes to return.

        Returns:
            List of matching notes, most recent first.
        """
        topic_words = set(topic.lower().split())
        scored: List[tuple] = []

        for note in self.notes:
            if note.get("superseded_by"):
                continue
            # Score by tag overlap + text keyword overlap
            tags = set(note.get("tags", []))
            text_words = set(note.get("text", "").lower().split())
            tag_hits = len(topic_words & tags)
            text_hits = len(topic_words & text_words)
            score = tag_hits * 3 + text_hits  # Tags worth more
            if score > 0:
                scored.append((score, note))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [note for _, note in scored[:limit]]

    def get_recent(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get the most recently added/updated notes.

        Returns:
            List of notes, newest first.
        """
        active = [n for n in self.notes if not n.get("superseded_by")]
        active.sort(
            key=lambda n: n.get("created_at", ""),
            reverse=True,
        )
        return active[:limit]

    def get_all_for_category(self, category: str) -> List[Dict[str, Any]]:
        """Get all active notes in a category."""
        return [
            n for n in self.notes
            if n.get("category") == category and not n.get("superseded_by")
        ]

    def format_for_prompt(self, limit: int = 8) -> str:
        """Format recent preferences as a context block for system prompts.

        Returns a compact string (~200 tokens max) or empty string.
        """
        recent = self.get_recent(limit)
        if not recent:
            return ""

        lines = ["Things I know about Jesse:"]
        char_count = len(lines[0])
        for note in recent:
            text = note.get("text", "")
            if not text:
                continue
            # Truncate individual notes
            if len(text) > 100:
                text = text[:97] + "..."
            line = f"- {text}"
            if char_count + len(line) > 600:  # ~200 token budget
                break
            lines.append(line)
            char_count += len(line)

        return "\n".join(lines) if len(lines) > 1 else ""


# ── Preference extraction from conversations ─────────────────────────

def detect_preference_signals(message: str) -> List[Dict[str, str]]:
    """Rule-based detection of preference signals in a user message.

    Returns list of raw matches: [{pattern, match_text, full_context}]
    These are candidates that may need model refinement.
    """
    if not message or len(message) < 10:
        return []

    signals: List[Dict[str, str]] = []
    msg_lower = message.lower()

    # Check if message has health/supplement context
    has_health_context = any(kw in msg_lower for kw in _HEALTH_KEYWORDS)

    # Supplement usage patterns
    for m in _SUPPLEMENT_PATTERN.finditer(message):
        matched = m.group(1).strip()
        if len(matched) > 3 and (has_health_context or matched.lower() in _HEALTH_KEYWORDS):
            signals.append({
                "pattern": "supplement",
                "match_text": matched,
                "full_context": message[max(0, m.start() - 20):m.end() + 40].strip(),
            })

    # Negative reaction patterns
    for m in _REACTION_PATTERN.finditer(message):
        matched = m.group(1).strip()
        if len(matched) > 3:
            signals.append({
                "pattern": "reaction",
                "match_text": matched,
                "full_context": message[max(0, m.start() - 30):m.end() + 20].strip(),
            })

    # Preference patterns (like/dislike/prefer)
    for m in _PREFERENCE_PATTERN.finditer(message):
        matched = m.group(1).strip()
        if len(matched) > 3:
            signals.append({
                "pattern": "preference",
                "match_text": matched,
                "full_context": message[max(0, m.start() - 20):m.end() + 20].strip(),
            })

    # Experience/discovery patterns
    for m in _EXPERIENCE_PATTERN.finditer(message):
        matched = m.group(1).strip()
        if len(matched) > 5:
            signals.append({
                "pattern": "experience",
                "match_text": matched,
                "full_context": message[max(0, m.start() - 20):m.end() + 20].strip(),
            })

    return signals[:5]  # Cap at 5 signals per message


def extract_and_record(
    message: str,
    source: str = "conversation",
    router: Optional[Any] = None,
) -> List[str]:
    """Detect preference signals and record them.

    Uses rule-based detection first (free), then optionally refines
    with a model call if signals are found and a router is available.

    Args:
        message: User's message text.
        source: Where the message came from.
        router: Optional model router for refinement.

    Returns:
        List of created/updated note IDs.
    """
    signals = detect_preference_signals(message)
    if not signals:
        return []

    prefs = get_preferences()
    note_ids: List[str] = []

    # Try model-assisted refinement if router available
    if router and len(signals) <= 3:
        try:
            note_ids = _model_refine_and_record(signals, message, source, router, prefs)
            if note_ids:
                return note_ids
        except Exception as e:
            logger.debug("Model refinement failed, using rule-based: %s", e)

    # Fallback: rule-based recording
    for signal in signals:
        category = _signal_to_category(signal["pattern"])
        text = signal["full_context"]
        tags = [w.lower() for w in signal["match_text"].split() if len(w) > 2]

        nid = prefs.add_note(
            category=category,
            text=text,
            tags=tags,
            source=source,
        )
        if nid:
            note_ids.append(nid)

    return note_ids


def _model_refine_and_record(
    signals: List[Dict[str, str]],
    message: str,
    source: str,
    router: Any,
    prefs: "UserPreferences",
) -> List[str]:
    """Use model to refine raw signal matches into structured notes."""
    signals_text = "\n".join(
        f"- [{s['pattern']}] {s['full_context']}" for s in signals
    )

    prompt = f"""Extract preference notes from this message by Jesse:

Message: "{message}"

Detected signals:
{signals_text}

For each real preference, return a JSON array:
[
  {{"category": "supplement|health|fitness|food|financial|preference|reaction|project",
    "text": "Brief note about what Jesse said (3rd person, e.g. 'Tried creatine but it caused gas')",
    "tags": ["keyword1", "keyword2"]}}
]

If a signal is noise (not a real preference), skip it. Return [] if none are real.
JSON only:"""

    resp = router.generate(
        prompt=prompt, max_tokens=250, temperature=0.2, prefer_local=True,
    )
    text = resp.get("text", "")

    from src.utils.parsing import extract_json_array
    items = extract_json_array(text)

    if not isinstance(items, list):
        return []

    note_ids: List[str] = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        category = (item.get("category") or "general").lower()
        note_text = (item.get("text") or "").strip()
        tags = item.get("tags", [])
        if not note_text:
            continue

        nid = prefs.add_note(
            category=category,
            text=note_text,
            tags=[str(t).lower() for t in tags] if isinstance(tags, list) else [],
            source=source,
        )
        if nid:
            note_ids.append(nid)

    return note_ids


def _signal_to_category(pattern: str) -> str:
    """Map a rule-based pattern name to a preference category."""
    return {
        "supplement": "supplement",
        "reaction": "reaction",
        "preference": "preference",
        "experience": "health",
    }.get(pattern, "general")
