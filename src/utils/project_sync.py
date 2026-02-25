"""Sync user signals (from Conversational Router) to project_context.json.

When the Router extracts a preference mentioning a project name + an
intent phrase ("done with X", "focus on Y"), update project_context
automatically.  No model call — keyword matching only.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from src.utils import project_context

logger = logging.getLogger(__name__)

# Intent phrases → action
_DEACTIVATE = ("done with", "not doing", "stopping", "drop ", "retire",
               "pause ", "quit ", "finished with", "no longer", "not interested in")
_BOOST = ("focus on", "prioritize", "more time on", "double down on",
          "spend more on", "ramp up")
_NEW_INTEREST = ("interested in", "want to try", "want to learn",
                 "curious about", "new project")


def _match_project(text_lower: str, active_projects: Dict) -> Optional[str]:
    """Return the project key if text mentions any active project name."""
    for key, info in active_projects.items():
        # Match key ("health_optimization") and its parts ("health", "optimization")
        names = [key] + key.split("_")
        # Also match path-derived name ("Health_Optimization" → "health optimization")
        path = (info.get("path") or "").rsplit("/", 1)[-1]
        if path:
            names.append(path.lower().replace("_", " "))
        for name in names:
            if len(name) >= 4 and name in text_lower:
                return key
    return None


def _detect_intent(text_lower: str) -> Optional[str]:
    """Return 'deactivate', 'boost', or 'new_interest' if text has intent phrases."""
    for phrase in _DEACTIVATE:
        if phrase in text_lower:
            return "deactivate"
    for phrase in _BOOST:
        if phrase in text_lower:
            return "boost"
    for phrase in _NEW_INTEREST:
        if phrase in text_lower:
            return "new_interest"
    return None


def sync_signals_to_project_context(user_signals: List[Dict]) -> None:
    """Check user signals for project-related preferences; update context if found."""
    if not user_signals:
        return

    prefs = [s for s in user_signals
             if isinstance(s, dict) and s.get("type") == "preference"]
    if not prefs:
        return

    ctx = project_context.load()
    active = ctx.get("active_projects", {})
    changed = False

    for signal in prefs:
        text = (signal.get("text") or "").lower()
        if not text:
            continue

        intent = _detect_intent(text)
        if not intent:
            continue

        if intent == "new_interest":
            # Extract the topic after the intent phrase
            for phrase in _NEW_INTEREST:
                if phrase in text:
                    topic = text.split(phrase, 1)[1].strip().rstrip(".")
                    if topic:
                        try:
                            from src.core.user_model import get_user_model
                            get_user_model().add_interest(topic)
                            logger.info("Project sync: added interest '%s' to user model", topic)
                        except Exception as exc:
                            logger.warning("Project sync: could not add interest: %s", exc)
                    break
            continue

        # For deactivate/boost, need a project match
        project_key = _match_project(text, active)
        if not project_key:
            continue

        if intent == "deactivate":
            old = active[project_key].get("priority", "medium")
            if old != "inactive":
                active[project_key]["priority"] = "inactive"
                changed = True
                logger.info("Project sync: deactivated '%s' (was %s)", project_key, old)

        elif intent == "boost":
            old = active[project_key].get("priority", "medium")
            if old != "high":
                active[project_key]["priority"] = "high"
                changed = True
                logger.info("Project sync: boosted '%s' to high (was %s)", project_key, old)

    if changed:
        ctx["last_updated"] = datetime.now().isoformat()
        project_context.save(ctx)
