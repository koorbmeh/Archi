"""
Centralised configuration loader for Archi.

Loads values from config/rules.yaml and config/heartbeat.yaml once,
then exposes them through simple accessor functions so that no module
needs to hard-code magic numbers or duplicate YAML-loading logic.

Usage:
    from src.utils.config import get_monitoring, get_browser_config

Single source of truth — if you need a threshold, port, or timeout,
add it to the relevant YAML file and expose it here.
"""

import logging
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.utils.paths import base_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal cache
# ---------------------------------------------------------------------------
_rules_cache: Optional[Dict[str, Any]] = None
_heartbeat_cache: Optional[Dict[str, Any]] = None
_identity_cache: Optional[Dict[str, Any]] = None
_personality_cache: Optional[Dict[str, Any]] = None
_brand_cache: Optional[Dict[str, Any]] = None


def _load_yaml(filename: str) -> Dict[str, Any]:
    """Load a YAML file from config/ and return as dict (empty on failure)."""
    path = os.path.join(base_path(), "config", filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.debug("Could not load %s: %s", path, e)
        return {}


def _rules() -> Dict[str, Any]:
    """Return cached rules.yaml contents."""
    global _rules_cache
    if _rules_cache is None:
        _rules_cache = _load_yaml("rules.yaml")
    return _rules_cache


def _heartbeat() -> Dict[str, Any]:
    """Return cached heartbeat.yaml contents."""
    global _heartbeat_cache
    if _heartbeat_cache is None:
        _heartbeat_cache = _load_yaml("heartbeat.yaml")
    return _heartbeat_cache


def _identity() -> Dict[str, Any]:
    """Return cached archi_identity.yaml contents."""
    global _identity_cache
    if _identity_cache is None:
        _identity_cache = _load_yaml("archi_identity.yaml")
    return _identity_cache


def _personality() -> Dict[str, Any]:
    """Return cached personality.yaml contents."""
    global _personality_cache
    if _personality_cache is None:
        _personality_cache = _load_yaml("personality.yaml")
    return _personality_cache


_reload_hooks: List = []


def on_reload(hook) -> None:
    """Register a callback to run after config.reload() clears caches."""
    _reload_hooks.append(hook)


def reload() -> None:
    """Force re-read of all config files (useful after editing YAML)."""
    global _rules_cache, _heartbeat_cache, _identity_cache, _personality_cache
    global _persona_prompt_cache, _brand_cache
    _rules_cache = None
    _heartbeat_cache = None
    _identity_cache = None
    _personality_cache = None
    _persona_prompt_cache = None
    _brand_cache = None
    for hook in _reload_hooks:
        try:
            hook()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Identity & user name
# ---------------------------------------------------------------------------

def get_user_name() -> str:
    """Return the user's name from archi_identity.yaml (default: 'User')."""
    ctx = _identity().get("user_context", {}) or {}
    return ctx.get("name") or "User"


def _brand() -> Dict[str, Any]:
    """Return cached archi_brand.yaml contents."""
    global _brand_cache
    if _brand_cache is None:
        _brand_cache = _load_yaml("archi_brand.yaml")
    return _brand_cache


def get_brand_config() -> Dict[str, Any]:
    """Return the full brand config from archi_brand.yaml."""
    return dict(_brand())


def get_identity() -> Dict[str, Any]:
    """Return the full identity config from archi_identity.yaml."""
    return dict(_identity())


def get_personality() -> Dict[str, Any]:
    """Return the full personality config from personality.yaml."""
    return dict(_personality())


def get_persona_prompt() -> str:
    """Build a compact persona string from personality.yaml for system prompts.

    Returns a paragraph-style description suitable for injecting into any
    system prompt that needs Archi to speak in-character. Falls back to a
    minimal hardcoded persona if personality.yaml is missing.
    """
    p = _personality()
    if not p:
        return (
            "You are Archi, a warm, direct, and slightly wry AI agent. "
            "Speak like a capable peer, not a helpdesk."
        )

    identity = p.get("identity", {})
    voice = p.get("voice", {})
    tone = voice.get("tone", {})
    humor = voice.get("humor", {})
    anti = voice.get("anti_patterns", [])

    essence = identity.get("essence", "").strip()
    delivery_comment = voice.get("delivery", "")
    default_tone = tone.get("default", "Direct, warm, unhurried")
    pressure_tone = tone.get("under_pressure", "Calm, focused")
    humor_style = humor.get("style", "")
    humor_freq = humor.get("frequency", "")

    anti_str = "; ".join(anti[:5]) if anti else ""

    parts = [f"You are Archi. {essence}"]
    parts.append(f"Voice: {delivery_comment}. Default tone: {default_tone}. "
                 f"Under pressure: {pressure_tone}.")
    if humor_style:
        parts.append(f"Humor: {humor_style}. {humor_freq}.")
    if anti_str:
        parts.append(f"Never: {anti_str}.")
    return " ".join(parts)


# Cached persona prompt — rebuilt only on config reload
_persona_prompt_cache: Optional[str] = None


def get_persona_prompt_cached() -> str:
    """Return the persona prompt, caching across calls until reload()."""
    global _persona_prompt_cache
    if _persona_prompt_cache is None:
        _persona_prompt_cache = get_persona_prompt()
    return _persona_prompt_cache


# ── Quote injection ───────────────────────────────────────────────────
# Keyword → quote index mappings derived from the "use" hints in
# personality.yaml's guiding_quotes section. When a user message matches
# keywords, get_relevant_quote() MAY return a quote (~20% chance) to
# keep usage occasional, per the personality framework's guidance.

_QUOTE_KEYWORDS: List[Tuple[List[str], int]] = [
    # (keywords, quote index in guiding_quotes list)
    (["obstacle", "block", "stuck", "wall", "barrier", "impediment", "way forward"], 0),   # Marcus Aurelius
    (["frustrat", "external", "uncontrol", "can't change", "out of our hands"], 1),          # Epictetus
    (["anxi", "worr", "overthink", "dread", "nervous", "scared"], 2),                          # Seneca
    (["prepar", "planning", "strategy", "ready", "paid off", "before we start"], 3),          # Sun Tzu
    (["research", "learn", "knowledge", "invest", "study", "reading up"], 4),                 # Franklin
    (["easier", "shortcut", "simpl", "wish it", "take the easy"], 5),                         # Rohn
    # index 6 = Carlin — skipped per personality.yaml ("Never")
    (["purpose", "why we", "meaning", "motivation", "reason for"], 7),                        # Nietzsche
    (["simplicity", "minimal", "less is", "overengineer", "too complex"], 8),                 # Diogenes
    (["promise", "word", "commit", "integrit", "said I would"], 9),                           # Ruiz
    (["honest", "hard truth", "uncomfortab", "nobody wants to hear"], 10),                    # Orwell
    (["consensus", "popular", "everyone", "majority", "bandwagon", "herd"], 11),              # Dobelli
    (["habit", "routine", "system", "consistentl", "repeatedl", "discipline"], 12),           # Aristotle
    (["skill", "combine", "breadth", "versatil", "cross-disciplin"], 13),                     # Greene
    (["curiosity", "wonder", "question", "explore", "fascin"], 14),                           # Gaarder
]

_QUOTE_PROBABILITY = 0.20  # Surface a quote ~1 in 5 matches


def get_relevant_quote(message: str) -> Optional[Dict[str, str]]:
    """Return a relevant guiding quote for *message*, or None.

    Matches message text against keyword hints from personality.yaml's
    guiding_quotes. Even on a match, returns None ~80% of the time so
    quotes stay occasional and never feel performative.
    """
    quotes = _personality().get("guiding_quotes") or []
    if not quotes:
        return None

    msg_lower = message.lower()
    matches: List[int] = []
    for keywords, idx in _QUOTE_KEYWORDS:
        if idx >= len(quotes):
            continue
        if any(kw in msg_lower for kw in keywords):
            matches.append(idx)

    if not matches:
        return None

    # Probability gate — keep it occasional
    if random.random() > _QUOTE_PROBABILITY:
        return None

    pick = random.choice(matches)
    q = quotes[pick]
    return {"text": q.get("text", ""), "source": q.get("source", "")}


# ---------------------------------------------------------------------------
# Monitoring thresholds
# ---------------------------------------------------------------------------

# Defaults match the historical hard-coded values so that Archi behaves
# identically even if the YAML section is missing.
_MONITORING_DEFAULTS: Dict[str, Any] = {
    "cpu_threshold": 80,
    "memory_threshold": 90,
    "temp_threshold": 80,
    "disk_threshold": 90,
    "budget_warning_pct": 80,
}


def get_monitoring() -> Dict[str, Any]:
    """Return the ``monitoring`` section of rules.yaml with defaults."""
    section = _rules().get("monitoring", {}) or {}
    merged = dict(_MONITORING_DEFAULTS)
    merged.update({k: v for k, v in section.items() if v is not None})
    return merged


# ---------------------------------------------------------------------------
# Browser automation
# ---------------------------------------------------------------------------

_BROWSER_DEFAULTS: Dict[str, int] = {
    "default_timeout_ms": 5000,
    "navigation_timeout_ms": 30000,
}


def get_browser_config() -> Dict[str, int]:
    """Return the ``browser`` section of rules.yaml with defaults."""
    section = _rules().get("browser", {}) or {}
    merged = dict(_BROWSER_DEFAULTS)
    merged.update({k: int(v) for k, v in section.items() if v is not None})
    return merged


# ---------------------------------------------------------------------------
# Heartbeat timing (session 89: renamed from dream_cycle)
# ---------------------------------------------------------------------------

_HEARTBEAT_DEFAULTS: Dict[str, int] = {
    "interval": 300,            # 5 minutes between background cycles
    "min_interval": 300,        # Adaptive scheduling floor (session 115)
    "max_interval": 7200,       # Adaptive scheduling ceiling (session 115)
    "max_parallel_tasks": 3,    # Max concurrent tasks per wave (session 120)
}


def get_heartbeat_config() -> Dict[str, int]:
    """Return the ``heartbeat`` section of heartbeat.yaml with defaults.

    Falls back to ``dream_cycle`` key for old config files.
    Accepts legacy ``idle_threshold`` as a fallback alias.
    """
    section = _heartbeat().get("heartbeat", {}) or {}
    # Fall back to legacy key
    if not section:
        section = _heartbeat().get("dream_cycle", {}) or {}
    merged = dict(_HEARTBEAT_DEFAULTS)
    if "idle_threshold" in section and "interval" not in section:
        section["interval"] = section.pop("idle_threshold")
    merged.update({k: int(v) for k, v in section.items()
                   if v is not None and k in merged})
    return merged


# Back-compat alias (autonomous_executor, tests)
get_dream_cycle_config = get_heartbeat_config


# ---------------------------------------------------------------------------
# Heartbeat budget
# ---------------------------------------------------------------------------

def get_heartbeat_budget() -> float:
    """Return the per-cycle budget limit from rules.yaml ``heartbeat_budget``."""
    _DEFAULT = 0.50
    for rule in _rules().get("non_override_rules", []):
        # Accept both new and legacy key names
        if rule.get("name") in ("heartbeat_budget", "dream_cycle_budget") and rule.get("enabled", True):
            return float(rule.get("limit", _DEFAULT))
    return _DEFAULT


# ---------------------------------------------------------------------------
# Email configuration
# ---------------------------------------------------------------------------

def get_email_config() -> Tuple[Optional[str], Optional[str]]:
    """Return (address, app_password) from env vars, or (None, None) if not set."""
    address = os.environ.get("ARCHI_EMAIL_ADDRESS", "").strip() or None
    password = os.environ.get("ARCHI_EMAIL_APP_PASSWORD", "").strip() or None
    return (address, password)


# Back-compat alias
get_dream_cycle_budget = get_heartbeat_budget
