"""Tests for dream cycle config command parsing.

Verifies that _parse_dream_cycle_interval handles:
- Original patterns (set/switch/change + dream cycle + N unit)
- Polite prefix stripping (can you, could you, please, etc.)
- Extended synonyms (delay, timeout, frequency)
- Status query matching
- Non-dream-cycle messages are NOT matched
"""

import pytest
import sys
from pathlib import Path

# Add project root to path for imports
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.interfaces.discord_bot import _parse_dream_cycle_interval


# ── Original patterns (should still work) ────────────────────────────

@pytest.mark.parametrize("msg, expected_seconds", [
    ("set dream cycle to 15 minutes", 900),
    ("set dream cycle to 2 minutes", 120),
    ("switch dream cycle to 30 minutes", 1800),
    ("change dream cycle to 5 minutes", 300),
    ("dream cycle 10 minutes", 600),
    ("dream cycle 60 seconds", 60),
    ("15 minute dream cycles", 900),
    ("5 min dream cycle", 300),
    ("set dream interval to 900 seconds", 900),
    ("set dream cycle to 1 hour", 3600),
    ("2 hour dream cycle", 7200),
])
def test_original_patterns(msg, expected_seconds):
    assert _parse_dream_cycle_interval(msg) == expected_seconds


# ── Polite prefix stripping ──────────────────────────────────────────

@pytest.mark.parametrize("msg, expected_seconds", [
    ("can you change the dream cycle delay to 2 minutes?", 120),
    ("can you set the dream cycle to 15 minutes", 900),
    ("could you change the dream cycle to 5 minutes?", 300),
    ("would you change the dream cycle to 10 minutes", 600),
    ("please set dream cycle to 3 minutes", 180),
    ("can you please change the dream cycle delay to 2 minutes?", 120),
    ("could you please adjust the dream cycle to 1 hour?", 3600),
    ("will you set dream cycle to 60 seconds", 60),
])
def test_polite_prefix_patterns(msg, expected_seconds):
    assert _parse_dream_cycle_interval(msg) == expected_seconds


# ── Extended synonyms (delay, timeout, frequency) ─────────────────────

@pytest.mark.parametrize("msg, expected_seconds", [
    ("set dream delay to 5 minutes", 300),
    ("change dream timeout to 10 minutes", 600),
    ("adjust dream frequency to 15 minutes", 900),
    ("dream delay 3 minutes", 180),
    ("dream timeout to 120 seconds", 120),
    ("5 minute dream delay", 300),
    ("10 min dream frequency", 600),
])
def test_extended_synonyms(msg, expected_seconds):
    assert _parse_dream_cycle_interval(msg) == expected_seconds


# ── Trailing punctuation stripped ─────────────────────────────────────

@pytest.mark.parametrize("msg, expected_seconds", [
    ("set dream cycle to 5 minutes?", 300),
    ("set dream cycle to 5 minutes!", 300),
    ("set dream cycle to 5 minutes.", 300),
])
def test_trailing_punctuation(msg, expected_seconds):
    assert _parse_dream_cycle_interval(msg) == expected_seconds


# ── Non-matching messages (should return None) ────────────────────────

@pytest.mark.parametrize("msg", [
    "hello",
    "search for dream interpretation",
    "set timer to 5 minutes",
    "change the volume to 50",
    "what time is it",
    "can you help me with something",
    "set interval to 5 minutes",  # no "dream" keyword
    "",
])
def test_non_matching(msg):
    assert _parse_dream_cycle_interval(msg) is None


# ── 60s floor enforcement is NOT in the parser ───────────────────────
# The parser just extracts seconds; the floor is enforced in
# DreamCycle.set_idle_threshold(). Parser should return the raw value.

def test_parser_returns_raw_value_below_floor():
    assert _parse_dream_cycle_interval("set dream cycle to 10 seconds") == 10
