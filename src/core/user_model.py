"""
User Model — Structured store of the user's preferences, patterns, style, and facts.

Cross-cutting resource queryable by any pipeline stage. Accumulates from
conversations as a side effect of Router processing (no dedicated model call).

Complements the existing UserPreferences (note-based) with structured
categories designed for pipeline consumption:
- facts: personal/biographical info ("the user is 32", "works in finance")
- preferences: explicit stated preferences ("I prefer X over Y")
- corrections: things the user corrected ("don't do X", "that's wrong because Y")
- patterns: observed decision patterns (what he approves/rejects)
- style: communication style notes
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.config import get_user_name
from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

_instance: Optional["UserModel"] = None
_instance_lock = threading.Lock()

# Max entries per category before oldest are pruned
_MAX_PER_CATEGORY = 50
_MAX_FACTS = 100  # Facts are biographical/stable — higher cap


def get_user_model() -> "UserModel":
    """Return the singleton UserModel instance (lazy-load). Thread-safe."""
    global _instance
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is None:
            _instance = UserModel()
    return _instance


def _reset_for_testing() -> None:
    """Clear the singleton — for test isolation only."""
    global _instance
    with _instance_lock:
        _instance = None


class UserModel:
    """Structured store of the user's preferences, decision patterns, style, and facts."""

    _CATEGORIES = ("facts", "preferences", "corrections", "patterns", "style", "tone_feedback")

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else _base_path() / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.data_dir / "user_model.json"

        self.facts: List[Dict[str, Any]] = []
        self.preferences: List[Dict[str, Any]] = []
        self.corrections: List[Dict[str, Any]] = []
        self.patterns: List[Dict[str, Any]] = []
        self.style: List[Dict[str, Any]] = []
        self.tone_feedback: List[Dict[str, Any]] = []
        self._dirty = False

        self._load()
        total = sum(len(getattr(self, c)) for c in self._CATEGORIES)
        logger.info("UserModel initialized (%d entries from %s)", total, self._file)

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            self.facts = data.get("facts", [])
            self.preferences = data.get("preferences", [])
            self.corrections = data.get("corrections", [])
            self.patterns = data.get("patterns", [])
            self.style = data.get("style", [])
            self.tone_feedback = data.get("tone_feedback", [])
        except Exception as e:
            logger.warning("Could not load user model: %s", e)

    def save(self) -> None:
        if not self._dirty:
            return
        data = {
            "version": 2,
            "last_updated": datetime.now().isoformat(),
            "facts": self.facts,
            "preferences": self.preferences,
            "corrections": self.corrections,
            "patterns": self.patterns,
            "style": self.style,
            "tone_feedback": self.tone_feedback,
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

    def add_fact(self, text: str, source: str = "router") -> None:
        """Record a personal/biographical fact about the user."""
        self._add("facts", text, source)

    def add_preference(self, text: str, source: str = "router") -> None:
        """Record an explicit stated preference."""
        self._add("preferences", text, source)

    def add_correction(self, text: str, source: str = "router") -> None:
        """Record something the user corrected."""
        self._add("corrections", text, source)

    def add_pattern(self, text: str, source: str = "router") -> None:
        """Record an observed decision pattern."""
        self._add("patterns", text, source)

    def add_style_note(self, text: str, source: str = "router") -> None:
        """Record a communication style observation."""
        self._add("style", text, source)

    def add_tone_feedback(self, sentiment: str, message_snippet: str, source: str = "reaction") -> None:
        """Record tone feedback from a Discord reaction on a chat response.

        Args:
            sentiment: "positive" or "negative".
            message_snippet: First ~100 chars of the response the user reacted to.
            source: How the feedback was captured (default "reaction").
        """
        text = f"{sentiment}: {message_snippet}"
        self._add("tone_feedback", text, source)

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
        max_cap = _MAX_FACTS if category == "facts" else _MAX_PER_CATEGORY
        if len(store) > max_cap:
            store[:] = store[-max_cap:]
        logger.info("UserModel +%s: %s", category, text[:80])
        self.save()

    # ── Querying ─────────────────────────────────────────────────────

    def get_context_for_chat(self) -> str:
        """Rich context for the chat system prompt (~500 tokens).

        Includes all personal facts prominently, preferences, and style
        so the chat model can engage naturally and personally.
        """
        lines = []
        if self.facts:
            for f in self.facts[-15:]:
                lines.append(f"- {f['text']}")
        if self.preferences:
            for pref in self.preferences[-5:]:
                lines.append(f"- Prefers: {pref['text']}")
        if self.corrections:
            for corr in self.corrections[-3:]:
                lines.append(f"- Corrected: {corr['text']}")
        if self.style:
            for s in self.style[-3:]:
                lines.append(f"- Style: {s['text']}")
        # Tone guidance from reaction feedback
        tone_hint = self._get_tone_guidance()
        if tone_hint:
            lines.append(f"- Tone: {tone_hint}")
        if not lines:
            return ""
        result = f"What you know about {get_user_name()}:\n" + "\n".join(lines)
        if len(result) > 2000:
            result = result[:1997] + "..."
        return result

    def get_context_for_router(self) -> str:
        """Compact context string for the Router prompt (~200 tokens max).

        Includes recent facts, preferences, corrections, and style notes so
        the Router can interpret ambiguous messages in context.
        """
        lines = []
        for f in self.facts[-3:]:
            lines.append(f"- Fact: {f['text']}")
        for pref in self.preferences[-3:]:
            lines.append(f"- Prefers: {pref['text']}")
        for corr in self.corrections[-2:]:
            lines.append(f"- Corrected: {corr['text']}")
        for s in self.style[-2:]:
            lines.append(f"- Style: {s['text']}")
        if not lines:
            return ""
        result = f"Known about {get_user_name()}:\n" + "\n".join(lines)
        if len(result) > 600:
            result = result[:597] + "..."
        return result

    def get_context_for_formatter(self) -> str:
        """Compact context for the Notification Formatter (~150 tokens).

        Returns the user's communication style so notifications adapt tone.
        """
        lines = []
        for s in self.style[-5:]:
            lines.append(f"- {s['text']}")
        for pref in self.preferences[-3:]:
            lines.append(f"- Prefers: {pref['text']}")
        if not lines:
            return ""
        result = f"{get_user_name()}'s communication style:\n" + "\n".join(lines)
        return result[:400]

    def get_context_for_discovery(self) -> str:
        """Compact context for the Discovery phase (~150 tokens).

        Returns known project preferences, domain knowledge, and relevant
        personal facts for personalized file ranking.
        """
        lines = []
        for f in self.facts[-5:]:
            lines.append(f"- {f['text']}")
        for pref in self.preferences[-5:]:
            lines.append(f"- {pref['text']}")
        for pat in self.patterns[-3:]:
            lines.append(f"- {pat['text']}")
        if not lines:
            return ""
        result = f"{get_user_name()}'s context:\n" + "\n".join(lines)
        return result[:400]

    def _get_tone_guidance(self) -> str:
        """Derive a tone guidance hint from accumulated tone feedback.

        Looks at recent positive/negative reaction ratios and the content
        of liked vs disliked messages to produce a short style instruction.
        Returns empty string if insufficient data.
        """
        if len(self.tone_feedback) < 3:
            return ""
        recent = self.tone_feedback[-20:]
        positive = [e for e in recent if e["text"].startswith("positive:")]
        negative = [e for e in recent if e["text"].startswith("negative:")]
        total = len(positive) + len(negative)
        if total < 3:
            return ""
        pos_ratio = len(positive) / total
        # Only generate guidance if there's a clear signal
        if 0.3 < pos_ratio < 0.7:
            return ""  # Mixed signals, no clear preference
        if pos_ratio >= 0.7:
            return f"{get_user_name()} tends to react positively to your current tone — keep it up"
        # Mostly negative — suggest adjustment
        return f"{get_user_name()} has reacted negatively to some responses — try being more concise and direct"

    def get_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return all entries grouped by category."""
        return {
            "facts": list(self.facts),
            "preferences": list(self.preferences),
            "corrections": list(self.corrections),
            "patterns": list(self.patterns),
            "style": list(self.style),
            "tone_feedback": list(self.tone_feedback),
        }


def extract_user_signals(message: str, router_response: Dict[str, Any]) -> List[str]:
    """Extract preference/correction/fact signals from a message + Router response.

    Called as a side effect of Router processing — no dedicated model call.
    The Router's JSON response may include a `user_signals` field with
    extracted observations.

    Args:
        message: The user's raw message.
        router_response: The Router's parsed JSON response dict.

    Returns:
        List of config_request descriptions (may be empty). These represent
        config changes the user requested that Archi can't autonomously apply.
    """
    signals = router_response.get("user_signals")
    if not signals or not isinstance(signals, list):
        return []

    model = get_user_model()
    config_requests: List[str] = []
    for signal in signals[:5]:  # Cap at 5 per message (raised from 3 for fact-heavy messages)
        if not isinstance(signal, dict):
            continue
        category = (signal.get("type") or "").lower()
        text = (signal.get("text") or "").strip()
        if not text:
            continue
        if category == "config_request":
            config_requests.append(text)
            # Also store as a correction so the preference is remembered
            model.add_correction(text, source="config_request")
        elif category == "fact":
            model.add_fact(text)
        elif category == "preference":
            model.add_preference(text)
        elif category == "correction":
            model.add_correction(text)
        elif category == "pattern":
            model.add_pattern(text)
        elif category == "style":
            model.add_style_note(text)
    return config_requests


def _text_similar(a: str, b: str) -> bool:
    """Quick Jaccard check — True if >0.6 word overlap."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) > 0.6
