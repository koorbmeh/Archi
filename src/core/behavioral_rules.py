"""Behavioral rules — memory that shapes action.

Session 200: initial implementation (DESIGN_BECOMING_SOMEONE.md Phase 2).

Unlike worldview (opinions/preferences), behavioral rules are *habits of
action* derived from repeated task outcomes.  After the Nth time an approach
fails, Archi adds an avoidance rule.  After the Nth time an approach succeeds,
Archi adds a preference rule.  These are injected into PlanExecutor hints so
Archi actually changes what it *does*, not just what it *says*.

Data lives in ``data/behavioral_rules.json``.  Thread-safe via module lock.
Automatic pruning: low-strength rules decay.  Rules cap at _MAX_RULES.
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

_RULES_PATH = "data/behavioral_rules.json"
_DATE_FMT = "%Y-%m-%d"

# Thresholds
_MIN_OCCURRENCES = 3  # Need 3+ similar outcomes to form a rule
_MAX_RULES = 80  # Total cap (avoidance + preference)
_MIN_STRENGTH = 0.15  # Below this, pruned on save
_DECAY_RATE = 0.05  # Rules not reinforced in 30 days lose this
_DECAY_DAYS = 30  # Days before decay kicks in
_KEYWORD_MIN_LEN = 3  # Minimum word length for keyword matching
_KEYWORD_MIN_OVERLAP = 2  # Minimum matching keywords for relevance

_lock = threading.Lock()


# ── Persistence ──────────────────────────────────────────────────────

def _rules_path() -> str:
    return str(_base_path() / _RULES_PATH)


def _empty_rules() -> dict:
    return {"avoidance": [], "preference": []}


def load() -> dict:
    """Load behavioral rules from disk.  Returns empty skeleton if missing."""
    path = _rules_path()
    if not os.path.isfile(path):
        return _empty_rules()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in ("avoidance", "preference"):
            if key not in data:
                data[key] = []
        return data
    except Exception as e:
        logger.error("Failed to load behavioral rules: %s", e)
        return _empty_rules()


def save(data: dict) -> None:
    """Atomically write behavioral rules to disk.  Prunes before writing."""
    _prune(data)
    path = _rules_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        dir_name = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        logger.error("Failed to save behavioral rules: %s", e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Pruning / decay ─────────────────────────────────────────────────

def _prune(data: dict) -> None:
    """Decay old rules, remove weak ones, enforce caps."""
    today = date.today()

    for rule_type in ("avoidance", "preference"):
        rules = data.get(rule_type, [])
        for rule in rules:
            last = _parse_date(rule.get("last_reinforced"))
            if last and (today - last).days > _DECAY_DAYS:
                rule["strength"] = max(0.0, rule.get("strength", 0.5) - _DECAY_RATE)
        # Remove below threshold, cap total
        data[rule_type] = [
            r for r in rules if r.get("strength", 0.5) >= _MIN_STRENGTH
        ]

    # Enforce total cap — keep strongest across both types
    total = len(data.get("avoidance", [])) + len(data.get("preference", []))
    if total > _MAX_RULES:
        all_rules = [(r, "avoidance") for r in data["avoidance"]] + \
                    [(r, "preference") for r in data["preference"]]
        all_rules.sort(key=lambda x: x[0].get("strength", 0), reverse=True)
        keep = all_rules[:_MAX_RULES]
        data["avoidance"] = [r for r, t in keep if t == "avoidance"]
        data["preference"] = [r for r, t in keep if t == "preference"]


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], _DATE_FMT).date()
    except (ValueError, TypeError):
        return None


# ── Tokenization ────────────────────────────────────────────────────

def _tokenize(text: str) -> set:
    """Extract meaningful lowercase tokens from text."""
    return {w for w in text.lower().split() if len(w) >= _KEYWORD_MIN_LEN and w.isalpha()}


# ── Rule management ─────────────────────────────────────────────────

def add_avoidance_rule(
    pattern: str,
    reason: str,
    keywords: List[str],
    strength: float = 0.5,
    evidence_count: int = 1,
) -> None:
    """Add or reinforce an avoidance rule (don't do X for problem type Y)."""
    with _lock:
        data = load()
        rules = data.get("avoidance", [])
        existing = _find_matching_rule(rules, keywords)
        today_str = date.today().strftime(_DATE_FMT)

        if existing is not None:
            existing["strength"] = min(1.0, existing.get("strength", 0.5) + 0.1)
            existing["evidence_count"] = existing.get("evidence_count", 0) + evidence_count
            existing["last_reinforced"] = today_str
            existing["reason"] = reason  # Keep latest reason
        else:
            rules.append({
                "pattern": pattern,
                "reason": reason,
                "keywords": keywords,
                "strength": min(1.0, max(0.0, strength)),
                "evidence_count": evidence_count,
                "formed": today_str,
                "last_reinforced": today_str,
            })

        data["avoidance"] = rules
        save(data)
        logger.debug("Avoidance rule updated: %s (%.2f)", pattern[:60], strength)


def add_preference_rule(
    pattern: str,
    reason: str,
    keywords: List[str],
    strength: float = 0.5,
    evidence_count: int = 1,
) -> None:
    """Add or reinforce a preference rule (prefer X for problem type Y)."""
    with _lock:
        data = load()
        rules = data.get("preference", [])
        existing = _find_matching_rule(rules, keywords)
        today_str = date.today().strftime(_DATE_FMT)

        if existing is not None:
            existing["strength"] = min(1.0, existing.get("strength", 0.5) + 0.1)
            existing["evidence_count"] = existing.get("evidence_count", 0) + evidence_count
            existing["last_reinforced"] = today_str
            existing["reason"] = reason
        else:
            rules.append({
                "pattern": pattern,
                "reason": reason,
                "keywords": keywords,
                "strength": min(1.0, max(0.0, strength)),
                "evidence_count": evidence_count,
                "formed": today_str,
                "last_reinforced": today_str,
            })

        data["preference"] = rules
        save(data)
        logger.debug("Preference rule updated: %s (%.2f)", pattern[:60], strength)


# ── Query for hint injection ────────────────────────────────────────

def get_relevant_rules(
    task_description: str,
    goal_description: str = "",
    limit: int = 4,
    min_strength: float = 0.3,
) -> List[str]:
    """Get behavioral rules relevant to a task, formatted as hint strings.

    Returns strings like:
        'AVOID: [pattern] — [reason] (strength 0.7, seen 5 times)'
        'PREFER: [pattern] — [reason] (strength 0.8, seen 8 times)'
    """
    with _lock:
        data = load()

    task_words = _tokenize(f"{task_description} {goal_description}")
    if len(task_words) < 2:
        return []

    scored = []
    for rule_type in ("avoidance", "preference"):
        for rule in data.get(rule_type, []):
            if rule.get("strength", 0) < min_strength:
                continue
            rule_words = set(rule.get("keywords", []))
            overlap = len(task_words & rule_words)
            if overlap >= _KEYWORD_MIN_OVERLAP:
                scored.append((overlap, rule_type, rule))

    if not scored:
        return []

    scored.sort(key=lambda x: (-x[0], -x[2].get("strength", 0)))

    hints = []
    for _, rule_type, rule in scored[:limit]:
        prefix = "AVOID" if rule_type == "avoidance" else "PREFER"
        strength = rule.get("strength", 0.5)
        evidence = rule.get("evidence_count", 1)
        hints.append(
            f"{prefix}: {rule['pattern']} — {rule['reason']} "
            f"(strength {strength:.1f}, seen {evidence}x)"
        )

    if hints:
        logger.info("Injected %d behavioral rules for: %s", len(hints), task_description[:60])
    return hints


# ── Post-task rule extraction ───────────────────────────────────────

def process_task_outcome(
    task_description: str,
    goal_description: str,
    outcome: str,
    success: bool,
) -> Optional[dict]:
    """Lightweight post-task analysis: detect patterns and create/reinforce rules.

    Called after each task completion.  Does NOT use a model — uses keyword
    matching against existing experiences via the LearningSystem.

    Returns dict of changes made, or None.
    """
    combined = f"{task_description} {goal_description} {outcome}".lower()
    keywords = list(_tokenize(combined))[:8]  # Top keywords for this task

    if len(keywords) < 2:
        return None

    changes = {}

    with _lock:
        data = load()

    # Check if this outcome reinforces any existing rule
    for rule_type in ("avoidance", "preference"):
        for rule in data.get(rule_type, []):
            rule_kw = set(rule.get("keywords", []))
            overlap = len(set(keywords) & rule_kw)
            if overlap >= _KEYWORD_MIN_OVERLAP:
                is_avoidance = rule_type == "avoidance"
                # Reinforce if outcome matches rule type
                if (not success and is_avoidance) or (success and not is_avoidance):
                    if is_avoidance:
                        add_avoidance_rule(
                            rule["pattern"], rule["reason"],
                            rule.get("keywords", keywords),
                        )
                    else:
                        add_preference_rule(
                            rule["pattern"], rule["reason"],
                            rule.get("keywords", keywords),
                        )
                    changes[f"reinforced:{rule['pattern'][:40]}"] = rule_type

    return changes if changes else None


def extract_rules_from_experiences(
    experiences: List[Dict[str, Any]],
    min_occurrences: int = _MIN_OCCURRENCES,
) -> List[dict]:
    """Scan recent experiences for patterns worth crystallizing into rules.

    Looks for repeated failures or successes with keyword overlap.
    Returns list of proposed rules (not yet added — caller decides).

    Each returned dict has: type ("avoidance"/"preference"), pattern, reason,
    keywords, evidence_count.
    """
    if len(experiences) < min_occurrences:
        return []

    # Group by outcome type
    failures = [e for e in experiences if e.get("type") == "failure"]
    successes = [e for e in experiences if e.get("type") == "success"]

    proposals = []

    # Look for repeated failure patterns
    proposals.extend(_find_clusters(failures, "avoidance", min_occurrences))
    proposals.extend(_find_clusters(successes, "preference", min_occurrences))

    return proposals


def _find_clusters(
    experiences: List[dict],
    rule_type: str,
    min_count: int,
) -> List[dict]:
    """Find clusters of similar experiences that suggest a rule."""
    if len(experiences) < min_count:
        return []

    # Simple clustering: group by keyword overlap
    clusters: List[List[dict]] = []
    used = set()

    for i, exp_i in enumerate(experiences):
        if i in used:
            continue
        words_i = _tokenize(f"{exp_i.get('context', '')} {exp_i.get('action', '')}")
        if len(words_i) < 2:
            continue
        cluster = [exp_i]
        used.add(i)

        for j, exp_j in enumerate(experiences):
            if j in used or j == i:
                continue
            words_j = _tokenize(f"{exp_j.get('context', '')} {exp_j.get('action', '')}")
            if len(words_i & words_j) >= _KEYWORD_MIN_OVERLAP:
                cluster.append(exp_j)
                used.add(j)

        if len(cluster) >= min_count:
            # Extract common keywords
            all_words = [_tokenize(f"{e.get('context', '')} {e.get('action', '')}") for e in cluster]
            common = set.intersection(*all_words) if all_words else set()
            if len(common) < 2:
                # Fall back to most frequent words
                from collections import Counter
                word_counts = Counter(w for ws in all_words for w in ws)
                common = {w for w, c in word_counts.most_common(5) if c >= min_count}

            if len(common) >= 2:
                keywords = sorted(common)[:6]
                outcome_sample = cluster[0].get("outcome", "")[:100]
                action_sample = cluster[0].get("action", "")[:80]

                if rule_type == "avoidance":
                    pattern = f"Don't use approach similar to '{action_sample}'"
                    reason = f"Failed {len(cluster)} times. Example: {outcome_sample}"
                else:
                    pattern = f"Prefer approach similar to '{action_sample}'"
                    reason = f"Succeeded {len(cluster)} times"

                clusters.append({
                    "type": rule_type,
                    "pattern": pattern,
                    "reason": reason,
                    "keywords": keywords,
                    "evidence_count": len(cluster),
                })

    return clusters


# ── Helpers ──────────────────────────────────────────────────────────

def _find_matching_rule(rules: list, keywords: List[str]) -> Optional[dict]:
    """Find a rule with significant keyword overlap."""
    kw_set = set(keywords)
    best = None
    best_overlap = 0
    for rule in rules:
        rule_kw = set(rule.get("keywords", []))
        overlap = len(kw_set & rule_kw)
        if overlap >= _KEYWORD_MIN_OVERLAP and overlap > best_overlap:
            best = rule
            best_overlap = overlap
    return best
