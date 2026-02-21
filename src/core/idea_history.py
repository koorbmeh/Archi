"""
Idea History — Persistent ledger of all work ideas Archi has ever suggested.

Tracks every idea with its outcome: auto-filtered (and why), user-accepted,
user-rejected, user-ignored (expired without response). Consulted during
idea generation to avoid re-proposing stale ideas and to feed rejection
context into retry prompts.

Created session 63 (Cowork).
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# Statuses
STATUS_AUTO_FILTERED = "auto_filtered"   # Archi's own filters rejected it
STATUS_PRESENTED = "presented"           # Shown to user, awaiting response
STATUS_ACCEPTED = "accepted"             # User picked this suggestion
STATUS_USER_REJECTED = "user_rejected"   # User explicitly said no
STATUS_IGNORED = "ignored"               # Suggestions expired without response

# How many recent rejected ideas to include in retry prompts
MAX_REJECTION_CONTEXT = 10

# Similarity threshold (Jaccard word overlap) for dedup against history
SIMILARITY_THRESHOLD = 0.55


def _text_similar(a: str, b: str) -> bool:
    """Quick Jaccard check — True if word overlap exceeds threshold."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) > SIMILARITY_THRESHOLD


class IdeaHistory:
    """Append-only ledger of all ideas with outcomes."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else _base_path() / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.data_dir / "idea_history.json"
        self._ideas: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            self._ideas = data.get("ideas", [])
        except Exception as e:
            logger.warning("Could not load idea history: %s", e)

    def _save(self) -> None:
        data = {
            "version": 1,
            "last_updated": datetime.now().isoformat(),
            "ideas": self._ideas,
        }
        tmp = self._file.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._file)
        except Exception as e:
            logger.warning("Could not save idea history: %s", e)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # ── Recording ────────────────────────────────────────────────────

    def record_auto_filtered(
        self, description: str, reason: str, category: str = ""
    ) -> None:
        """Record an idea that was rejected by Archi's own filters."""
        self._ideas.append({
            "description": description,
            "category": category,
            "status": STATUS_AUTO_FILTERED,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        })
        self._save()
        logger.debug("Idea history: auto_filtered — %s (%s)", description[:60], reason)

    def record_presented(self, descriptions: List[str]) -> str:
        """Record a batch of ideas that were shown to the user.

        Returns a batch_id for later status updates.
        """
        batch_id = datetime.now().isoformat()
        for desc in descriptions:
            self._ideas.append({
                "description": desc,
                "status": STATUS_PRESENTED,
                "batch_id": batch_id,
                "timestamp": batch_id,
            })
        self._save()
        logger.debug("Idea history: presented %d ideas (batch %s)", len(descriptions), batch_id[:19])
        return batch_id

    def record_accepted(self, description: str) -> None:
        """Record that the user accepted/picked an idea."""
        # Update most recent matching presented entry
        for idea in reversed(self._ideas):
            if (idea.get("status") == STATUS_PRESENTED
                    and _text_similar(idea.get("description", ""), description)):
                idea["status"] = STATUS_ACCEPTED
                idea["resolved_at"] = datetime.now().isoformat()
                self._save()
                logger.debug("Idea history: accepted — %s", description[:60])
                return
        # If no match found, record it fresh
        self._ideas.append({
            "description": description,
            "status": STATUS_ACCEPTED,
            "timestamp": datetime.now().isoformat(),
            "resolved_at": datetime.now().isoformat(),
        })
        self._save()

    def record_user_rejected(self, description: str, reason: str = "") -> None:
        """Record that the user explicitly rejected an idea."""
        for idea in reversed(self._ideas):
            if (idea.get("status") == STATUS_PRESENTED
                    and _text_similar(idea.get("description", ""), description)):
                idea["status"] = STATUS_USER_REJECTED
                idea["reason"] = reason or "user declined"
                idea["resolved_at"] = datetime.now().isoformat()
                self._save()
                logger.debug("Idea history: user_rejected — %s", description[:60])
                return

    def mark_batch_ignored(self, batch_id: str) -> int:
        """Mark all still-presented ideas in a batch as ignored.

        Called when new suggestions replace old ones without user response.
        Returns count of ideas marked.
        """
        count = 0
        for idea in self._ideas:
            if (idea.get("batch_id") == batch_id
                    and idea.get("status") == STATUS_PRESENTED):
                idea["status"] = STATUS_IGNORED
                idea["reason"] = "expired without response"
                idea["resolved_at"] = datetime.now().isoformat()
                count += 1
        if count:
            self._save()
            logger.debug("Idea history: marked %d ideas as ignored (batch %s)", count, batch_id[:19])
        return count

    # ── Querying ─────────────────────────────────────────────────────

    def is_stale(self, description: str) -> Optional[Dict[str, Any]]:
        """Check if a similar idea was already tried and didn't land.

        Returns the matching history entry if found (with status/reason),
        or None if the idea is fresh.
        """
        for idea in reversed(self._ideas):
            status = idea.get("status", "")
            if status == STATUS_ACCEPTED:
                continue  # Accepted ideas are fine to revisit/extend
            if _text_similar(idea.get("description", ""), description):
                return idea
        return None

    def times_rejected(self, description: str) -> int:
        """Count how many times a similar idea was filtered, rejected, or ignored."""
        count = 0
        for idea in self._ideas:
            if idea.get("status") in (STATUS_AUTO_FILTERED, STATUS_USER_REJECTED, STATUS_IGNORED):
                if _text_similar(idea.get("description", ""), description):
                    count += 1
        return count

    def get_rejection_context(self, limit: int = MAX_REJECTION_CONTEXT) -> str:
        """Build a compact summary of recently rejected/ignored ideas for the brainstorm prompt.

        Returns a string like:
          - "sleep tracker CLI tool" (rejected 2x: not relevant, user ignored)
          - "step counter script" (rejected 1x: not purpose-driven)
        """
        # Collect unique rejected descriptions with reasons
        seen: Dict[str, Dict[str, Any]] = {}
        for idea in self._ideas:
            status = idea.get("status", "")
            if status not in (STATUS_AUTO_FILTERED, STATUS_USER_REJECTED, STATUS_IGNORED):
                continue
            desc = idea.get("description", "")
            if not desc:
                continue
            # Dedup by similarity
            matched_key = None
            for existing_key in seen:
                if _text_similar(existing_key, desc):
                    matched_key = existing_key
                    break
            if matched_key:
                seen[matched_key]["count"] += 1
                reason = idea.get("reason", status)
                if reason not in seen[matched_key]["reasons"]:
                    seen[matched_key]["reasons"].append(reason)
            else:
                seen[desc] = {
                    "count": 1,
                    "reasons": [idea.get("reason", status)],
                }

        if not seen:
            return ""

        # Sort by count descending, take most-rejected first
        sorted_items = sorted(seen.items(), key=lambda x: x[1]["count"], reverse=True)
        lines = []
        for desc, info in sorted_items[:limit]:
            reasons_str = ", ".join(info["reasons"][:3])
            lines.append(f'- "{desc[:80]}" (rejected {info["count"]}x: {reasons_str})')

        return "Previously rejected ideas (DO NOT suggest these again):\n" + "\n".join(lines)

    def get_accepted_context(self, limit: int = 5) -> str:
        """Build a summary of recently accepted ideas (what worked)."""
        accepted = [
            idea for idea in reversed(self._ideas)
            if idea.get("status") == STATUS_ACCEPTED
        ][:limit]
        if not accepted:
            return ""
        lines = [f'- "{idea.get("description", "")[:80]}"' for idea in accepted]
        return "Previously accepted ideas (similar themes may work):\n" + "\n".join(lines)

    @property
    def total_ideas(self) -> int:
        return len(self._ideas)

    @property
    def stats(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for idea in self._ideas:
            status = idea.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts
