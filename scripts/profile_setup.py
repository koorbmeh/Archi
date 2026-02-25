#!/usr/bin/env python3
r"""
Archi Profile Setup — concrete interview to build a baseline user profile.

Asks specific, mostly multiple-choice questions to seed user_model.json
and archi_identity.yaml so Archi has real context from day one instead of
learning everything gradually through conversation.

Questions are concrete (name, age, location, schedule) — not vague
"what are your goals" prompts that put the user on the spot.

Usage:
    python scripts/profile_setup.py          (interactive interview)
    python scripts/profile_setup.py --show   (display current profile)
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, header

import yaml

# ── Paths ─────────────────────────────────────────────────────────────

DATA_DIR = ROOT / "data"
USER_MODEL_FILE = DATA_DIR / "user_model.json"
IDENTITY_FILE = ROOT / "config" / "archi_identity.yaml"


# ── I/O helpers ───────────────────────────────────────────────────────

def _input(prompt: str, default: str = "") -> str:
    """Text input with optional default."""
    if default:
        raw = input(f"  {prompt} [{default}]: ").strip()
        return raw or default
    return input(f"  {prompt}: ").strip()


def _choice(prompt: str, options: List[str], allow_other: bool = False,
            default: int = 0) -> str:
    """Numbered multiple-choice. Returns the selected string.

    Args:
        prompt: Question text.
        options: List of choices.
        allow_other: If True, adds an "Other (type your own)" option.
        default: 1-based default index (0 = no default).
    """
    print(f"\n  {prompt}\n")
    for i, opt in enumerate(options, 1):
        marker = " *" if i == default else ""
        print(f"    {i}. {opt}{marker}")
    if allow_other:
        n = len(options) + 1
        print(f"    {n}. Other (type your own)")

    suffix = f" [{default}]" if default else ""
    while True:
        raw = input(f"\n  Your choice{suffix}: ").strip()
        if not raw and default:
            return options[default - 1]
        try:
            idx = int(raw)
        except ValueError:
            print("  Please enter a number.")
            continue
        if 1 <= idx <= len(options):
            return options[idx - 1]
        if allow_other and idx == len(options) + 1:
            return _input("Please specify")
        print(f"  Please pick 1–{len(options) + (1 if allow_other else 0)}.")


def _multi_choice(prompt: str, options: List[str],
                  allow_other: bool = False) -> List[str]:
    """Multiple-selection. Returns list of selected strings."""
    print(f"\n  {prompt}")
    print("  (Enter numbers separated by commas, e.g. 1,3,5)\n")
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    if allow_other:
        print(f"    {len(options) + 1}. Other (type your own)")

    while True:
        raw = input("\n  Your choices: ").strip()
        if not raw:
            return []
        try:
            indices = [int(x.strip()) for x in raw.split(",")]
        except ValueError:
            print("  Please enter numbers separated by commas.")
            continue
        results = []
        max_idx = len(options) + (1 if allow_other else 0)
        bad = [i for i in indices if i < 1 or i > max_idx]
        if bad:
            print(f"  Invalid: {bad}. Pick 1–{max_idx}.")
            continue
        for idx in indices:
            if idx <= len(options):
                results.append(options[idx - 1])
            else:
                results.append(_input("Please specify"))
        return results


def _yes_no(prompt: str, default: bool = True) -> bool:
    """Simple yes/no."""
    suffix = "[Y/n]" if default else "[y/N]"
    raw = input(f"  {prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ── Data persistence ──────────────────────────────────────────────────

def _load_user_model() -> Dict[str, Any]:
    """Load existing user_model.json or return empty structure."""
    if USER_MODEL_FILE.exists():
        try:
            return json.loads(USER_MODEL_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "version": 2,
        "last_updated": None,
        "facts": [],
        "preferences": [],
        "corrections": [],
        "patterns": [],
        "style": [],
        "tone_feedback": [],
    }


def _save_user_model(data: Dict[str, Any]) -> None:
    """Write user_model.json atomically."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data["last_updated"] = datetime.now().isoformat()
    tmp = USER_MODEL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(USER_MODEL_FILE)
    print(f"  Saved {USER_MODEL_FILE.relative_to(ROOT)}")


def _add_fact(model: Dict[str, Any], text: str) -> None:
    """Add a fact entry, deduplicating against existing text."""
    facts = model.setdefault("facts", [])
    text = text.strip()
    if not text:
        return
    # Simple dedup: skip if very similar already exists
    text_lower = text.lower()
    for existing in facts:
        existing_lower = existing.get("text", "").lower()
        # Exact or near-exact match
        if existing_lower == text_lower:
            return
        # Word overlap > 60%
        wa = set(existing_lower.split())
        wb = set(text_lower.split())
        if wa and wb and len(wa & wb) / len(wa | wb) > 0.6:
            # Replace with newer version (it may be more accurate)
            existing["text"] = text
            existing["source"] = "onboarding"
            existing["ts"] = datetime.now().isoformat()
            return
    facts.append({
        "text": text,
        "source": "onboarding",
        "ts": datetime.now().isoformat(),
    })


def _add_preference(model: Dict[str, Any], text: str) -> None:
    """Add a preference entry."""
    prefs = model.setdefault("preferences", [])
    text = text.strip()
    if not text:
        return
    for existing in prefs:
        if existing.get("text", "").lower() == text.lower():
            return
    prefs.append({
        "text": text,
        "source": "onboarding",
        "ts": datetime.now().isoformat(),
    })


def _add_style(model: Dict[str, Any], text: str) -> None:
    """Add a communication style entry."""
    styles = model.setdefault("style", [])
    text = text.strip()
    if not text:
        return
    for existing in styles:
        if existing.get("text", "").lower() == text.lower():
            return
    styles.append({
        "text": text,
        "source": "onboarding",
        "ts": datetime.now().isoformat(),
    })


def _load_identity() -> Dict[str, Any]:
    """Load archi_identity.yaml."""
    if IDENTITY_FILE.exists():
        try:
            with open(IDENTITY_FILE, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    return {}


def _save_identity(data: Dict[str, Any]) -> None:
    """Write archi_identity.yaml."""
    with open(IDENTITY_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"  Saved {IDENTITY_FILE.relative_to(ROOT)}")


# ── Timezone detection ────────────────────────────────────────────────

# Common US timezones with their IANA names
US_TIMEZONES = [
    ("Eastern (New York, Miami, Atlanta)", "America/New_York"),
    ("Central (Chicago, Dallas, Nashville)", "America/Chicago"),
    ("Mountain (Denver, Phoenix, Salt Lake City)", "America/Denver"),
    ("Pacific (Los Angeles, Seattle, Portland)", "America/Los_Angeles"),
    ("Alaska", "America/Anchorage"),
    ("Hawaii", "Pacific/Honolulu"),
]


# ── The Interview ─────────────────────────────────────────────────────

def _section_basics(model: Dict[str, Any], identity: Dict[str, Any]) -> None:
    """Section 1: Who you are."""
    header("About You")

    # Name
    current_name = identity.get("user_context", {}).get("name", "")
    name = _input("What's your first name?", current_name)
    if name:
        identity.setdefault("user_context", {})["name"] = name

    # Age
    age = _input("How old are you?")
    if age:
        _add_fact(model, f"{age} years old")

    # Location
    current_loc = identity.get("user_context", {}).get("location", "")
    city = _input("City and state/country?", current_loc)
    if city:
        identity.setdefault("user_context", {})["location"] = city
        _add_fact(model, f"Lives in {city}")

    # Timezone
    tz = _choice(
        "What timezone are you in?",
        [t[0] for t in US_TIMEZONES],
        allow_other=True,
    )
    # Map display name to IANA if it matches
    tz_iana = tz
    for display, iana in US_TIMEZONES:
        if tz == display:
            tz_iana = iana
            break
    identity.setdefault("user_context", {})["timezone"] = tz_iana

    # Living situation
    living = _choice(
        "Living situation?",
        ["Live alone", "With partner/spouse", "With family/kids",
         "With roommate(s)"],
        allow_other=True,
    )
    _add_fact(model, living)

    # Pets
    pets = _choice(
        "Any pets?",
        ["No pets", "Dog(s)", "Cat(s)", "Dog(s) and cat(s)"],
        allow_other=True,
    )
    if pets != "No pets":
        _add_fact(model, f"Has {pets.lower()}")


def _section_work(model: Dict[str, Any], identity: Dict[str, Any]) -> None:
    """Section 2: Work and schedule."""
    header("Work & Schedule")

    # Employment
    status = _choice(
        "Current work situation?",
        ["Employed full-time", "Employed part-time", "Self-employed/freelance",
         "Student", "Retired", "Between jobs"],
        allow_other=True,
    )
    _add_fact(model, status)

    if status in ("Employed full-time", "Employed part-time", "Self-employed/freelance"):
        field = _input("What field or job title? (brief is fine)")
        if field:
            _add_fact(model, f"Works as/in {field}")

        location = _choice(
            "Where do you work?",
            ["From home (remote)", "In an office", "Hybrid (some home, some office)",
             "On the road / field work"],
            allow_other=True,
        )
        _add_fact(model, f"Works {location.lower()}")

    # Work days
    days = _multi_choice(
        "Which days do you typically work? (skip if not applicable)",
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
         "Saturday", "Sunday"],
    )
    if days:
        _add_fact(model, f"Works {', '.join(days)}")

    # Work hours
    hours = _choice(
        "Typical work hours?",
        ["Early bird (before 8 AM start)", "Standard (8-9 AM to 4-5 PM)",
         "Late shift (afternoon to evening)", "Night shift",
         "Flexible / varies"],
        allow_other=True,
    )
    _add_fact(model, f"Work hours: {hours.lower()}")

    # Working hours for identity (when Archi should be active)
    wake = _input("Roughly what time do you wake up? (e.g. 6 AM)", "6 AM")
    sleep = _input("Roughly what time do you go to bed? (e.g. 11 PM)", "11 PM")
    identity.setdefault("user_context", {})["working_hours"] = f"{wake} - {sleep}"


def _section_computer(model: Dict[str, Any]) -> None:
    """Section 3: Computer and tech setup."""
    header("Tech Setup")

    os_choice = _choice(
        "Primary operating system?",
        ["Windows", "macOS", "Linux"],
        allow_other=True,
    )
    _add_fact(model, f"Primary OS: {os_choice}")

    desk = _choice(
        "How much time do you spend at a computer daily?",
        ["Most of the day (8+ hours)", "Half the day (4-8 hours)",
         "A few hours (1-4 hours)", "Minimal (< 1 hour)"],
    )
    _add_fact(model, f"Computer time: {desk.lower()}")

    # Social media / handles
    print("\n  Online handles (press Enter to skip any):\n")
    github = _input("GitHub username")
    if github:
        _add_fact(model, f"GitHub handle: {github}")
    twitter = _input("X/Twitter handle")
    if twitter:
        _add_fact(model, f"X (Twitter) handle: {twitter}")
    discord_name = _input("Discord username")
    if discord_name:
        _add_fact(model, f"Discord username: {discord_name}")


def _section_interests(model: Dict[str, Any]) -> None:
    """Section 4: Interests and hobbies."""
    header("Interests & Hobbies")

    interests = _multi_choice(
        "Which of these interest you? (pick as many as you like)",
        [
            "Technology / AI",
            "Programming / software dev",
            "Health & fitness",
            "Finance & investing",
            "Philosophy / psychology",
            "Science / space",
            "Gaming",
            "Reading / writing",
            "Music",
            "Cooking / food",
            "Sports",
            "Art / design",
            "Movies / TV / anime",
            "Outdoors / nature",
            "Home improvement / DIY",
        ],
        allow_other=True,
    )
    if interests:
        _add_fact(model, f"Interests: {', '.join(interests)}")

    # Activity level
    activity = _choice(
        "How would you describe your activity level?",
        ["Sedentary (mostly sitting)", "Lightly active (some walking)",
         "Moderately active (regular exercise)", "Very active (daily exercise)"],
    )
    _add_fact(model, f"Activity level: {activity.lower()}")


def _section_communication(model: Dict[str, Any]) -> None:
    """Section 5: How you want Archi to talk to you."""
    header("Communication Preferences")

    tone = _choice(
        "How should Archi talk to you?",
        ["Casual and friendly (like a buddy)",
         "Direct and to-the-point (minimal small talk)",
         "Professional but warm",
         "Sarcastic / dry humor is fine"],
    )
    _add_style(model, f"Preferred tone: {tone.lower()}")

    verbosity = _choice(
        "How detailed should Archi's responses be?",
        ["Brief — just the essentials",
         "Medium — enough detail to understand",
         "Detailed — explain your reasoning"],
    )
    _add_preference(model, f"Response detail: {verbosity.lower()}")

    notifications = _choice(
        "How should Archi handle dream-mode notifications?",
        ["Only notify me about important stuff",
         "Give me a quick summary of what was done",
         "Detailed reports of all activity"],
    )
    _add_preference(model, f"Notification preference: {notifications.lower()}")

    proactive = _choice(
        "How proactive should Archi be?",
        ["Wait for me to ask — don't initiate",
         "Suggest things occasionally, but don't overdo it",
         "Be proactive — surface ideas and opportunities"],
    )
    _add_preference(model, f"Proactiveness: {proactive.lower()}")


# ── Main flow ─────────────────────────────────────────────────────────

def _show_profile() -> None:
    """Display current profile from user_model.json."""
    header("Current Profile")
    if not USER_MODEL_FILE.exists():
        print("  No profile found. Run 'python scripts/profile_setup.py' to create one.")
        return

    data = json.loads(USER_MODEL_FILE.read_text(encoding="utf-8"))
    for category in ("facts", "preferences", "corrections", "style"):
        entries = data.get(category, [])
        if not entries:
            continue
        print(f"  {category.upper()}:")
        for e in entries:
            src = e.get("source", "?")
            print(f"    [{src}] {e['text']}")
        print()

    # Also show identity
    if IDENTITY_FILE.exists():
        try:
            with open(IDENTITY_FILE, "r", encoding="utf-8") as f:
                identity = yaml.safe_load(f) or {}
            ctx = identity.get("user_context", {})
            if ctx:
                print("  IDENTITY (archi_identity.yaml):")
                for k, v in ctx.items():
                    print(f"    {k}: {v}")
                print()
        except Exception:
            pass


def main() -> None:
    os.chdir(str(ROOT))

    if len(sys.argv) > 1 and sys.argv[1] == "--show":
        _show_profile()
        return

    header("Archi Profile Setup")
    print("  This helps Archi understand who you are so it can be useful")
    print("  from day one. Takes about 3 minutes.\n")
    print("  Everything stays local on your machine (data/user_model.json).")
    print("  You can re-run this anytime to update your answers.\n")

    if not _yes_no("Ready to go?"):
        print("  No problem. Run this script again when you're ready.")
        return

    model = _load_user_model()
    identity = _load_identity()

    # Run each section
    _section_basics(model, identity)
    _section_work(model, identity)
    _section_computer(model)
    _section_interests(model)
    _section_communication(model)

    # Save everything
    header("Saving Profile")
    _save_user_model(model)
    _save_identity(identity)

    header("Done!")
    print("  Archi now has a baseline to work from. It'll continue learning")
    print("  about you through conversation — this just gives it a head start.")
    print()
    print("  To see your profile anytime: python scripts/profile_setup.py --show")
    print("  To update answers:           python scripts/profile_setup.py")
    print()


if __name__ == "__main__":
    main()
