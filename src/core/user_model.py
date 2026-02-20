"""
User Model — Structured store of Jesse's preferences, patterns, and style.

Cross-cutting resource queryable by any pipeline stage. Accumulates from
conversations as a side effect of Router processing (no dedicated model call).

Complements the existing UserPreferences (note-based) with structured
categories designed for pipeline consumption:
- preferences: explicit stated preferences ("I prefer X over Y")
- corrections: things Jesse corrected ("don't do X", "that's wrong because Y")
- patterns: observed decision patterns (what he approves/rejects)
- style: communication style notes
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

_instance: Optional["UserModel"] = None

# Max entries per category before oldest are pruned
_MAX_PER_CATEGORY = 50


def get_user_model() -> "UserModel":
    """Return the singleton UserModel instance (lazy-load)."""
    global _instance
    if _instance is None:
        _instance = UserModel()
    return _instance


class UserModel:
    """Structured store of Jesse's preferences, decision patterns, and style."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else _base_path() / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.data_dir / "user_model.json"

        self.preferences: List[Dict[str, Any]] = []
        self.corrections: List[Dict[str, Any]] = []
        self.patterns: List[Dict[str, Any]] = []
        self.style: List[Dict[str, Any]] = []
        self._dirty = False

        self._load()
        total = sum(len(getattr(self, c)) for c in ("preferences", "corrections", "patterns", "style"))
        logger.info("UserModel initialized (%d entries from %s)", total, self._file)

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            self.preferences = data.get("preferences", [])
            self.corrections = data.get("corrections", [])
            self.patterns = data.get("patterns", [])
            self.style = data.get("style", [])
        except Exception as e:
            logger.warning("Could not load user model: %s", e)

    def save(self) -> None:
        if not self._dirty:
            return
        data = {
            "version": 1,
            "last_updated": datetime.now().isoformat(),
            "preferences": self.preferences,
            "corrections": self.corrections,
            "patterns": self.patterns,
            "style": self.style,
        }
        tmp = self._file.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._file)
            self._dirty = False
        except Exception as e:
            logger.warning("Could not save user model: %s", e)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # ── Adding entries ───────────────────────────────────────────────

    def add_preference(self, text: str, source: str = "router") -> None:
        """Record an explicit stated preference."""
        self._add("preferences", text, source)

    def add_correction(self, text: str, source: str = "router") -> None:
        """Record something Jesse corrected."""
        self._add("corrections", text, source)

    def add_pattern(self, text: str, source: str = "router") -> None:
        """Record an observed decision pattern."""
        self._add("patterns", text, source)

    def add_style_note(self, text: str, source: str = "router") -> None:
        """Record a communication style observation."""
        self._add("style", text, source)

    def _add(self, category: str, text: str, source: str) -> None:
        text = text.strip()
        if not text:
            return
        store: List[Dict[str, Any]] = getattr(self, category)
        # Simple dedup: skip if very similar text exists
        for existing in store[-20:]:
            if _text_similar(existing.get("text", ""), text):
                return
        entry = {
            "text": text,
            "source": source,
            "ts": datetime.now().isoformat(),
        }
        store.append(entry)
        self._dirty = True
        # Prune oldest if over cap
        if len(store) > _MAX_PER_CATEGORY:
            store[:] = store[-_MAX_PER_CATEGORY:]
        logger.info("UserModel +%s: %s", category, text[:80])
        self.save()

    # ── Querying ─────────────────────────────────────────────────────

    def get_context_for_router(self) -> str:
        """Compact context string for the Router prompt (~200 tokens max).

        Includes recent preferences, corrections, and style notes so the
        Router can interpret ambiguous messages in context.
        """
        lines = []
        for pref in self.preferences[-5:]:
            lines.append(f"- Prefers: {pref['text']}")
        for corr in self.corrections[-3:]:
            lines.append(f"- Corrected: {corr['text']}")
        for s in self.style[-3:]:
            lines.append(f"- Style: {s['text']}")
        if not lines:
            return ""
        # Trim to ~600 chars (~200 tokens)
        result = "Known about Jesse:\n" + "\n".join(lines)
        if len(result) > 600:
            result = result[:597] + "..."
        return result

    def get_context_for_formatter(self) -> str:
        """Compact context for the Notification Formatter (~150 tokens).

        Returns Jesse's communication style so notifications adapt tone.
        """
        lines = []
        for s in self.style[-5:]:
            lines.append(f"- {s['text']}")
        for pref in self.preferences[-3:]:
            lines.append(f"- Prefers: {pref['text']}")
        if not lines:
            return ""
        result = "Jesse's communication style:\n" + "\n".join(lines)
        return result[:400]

    def get_context_for_discovery(self) -> str:
        """Compact context for the Discovery phase (~150 tokens).

        Returns known project preferences and domain knowledge for
        personalized file ranking.
        """
        lines = []
        for pref in self.preferences[-5:]:
            lines.append(f"- {pref['text']}")
        for pat in self.patterns[-3:]:
            lines.append(f"- {pat['text']}")
        if not lines:
            return ""
        result = "Jesse's project preferences:\n" + "\n".join(lines)
        return result[:400]

    def get_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return all entries grouped by category."""
        return {
            "preferences": list(self.preferences),
            "corrections": list(self.corrections),
            "patterns": list(self.patterns),
            "style": list(self.style),
        }


def extract_user_signals(message: str, router_response: Dict[str, Any]) -> None:
    """Extract preference/correction signals from a message + Router response.

    Called as a side effect of Router processing — no dedicated model call.
    The Router's JSON response may include a `user_signals` field with
    extracted observations.

    Args:
        message: The user's raw message.
        router_response: The Router's parsed JSON response dict.
    """
    signals = router_response.get("user_signals")
    if not signals or not isinstance(signals, list):
        return

    model = get_user_model()
    for signal in signals[:3]:  # Cap at 3 per message
        if not isinstance(signal, dict):
            continue
        category = (signal.get("type") or "").lower()
        text = (signal.get("text") or "").strip()
        if not text:
            continue
        if category == "preference":
            model.add_preference(text)
        elif category == "correction":
            model.add_correction(text)
        elif category == "pattern":
            model.add_pattern(text)
        elif category == "style":
            model.add_style_note(text)


def _text_similar(a: str, b: str) -> bool:
    """Quick Jaccard check — True if >0.6 word overlap."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) > 0.6
