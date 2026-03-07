"""
Morning Digest — combines email inbox, news headlines, weather, and
calendar events into a single morning briefing for Jesse.

Called from reporting.send_morning_report() to enrich the daily message.
All data sources are best-effort: if one fails, the others still work.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _fetch_email_summary() -> str:
    """Get a brief inbox summary (unread count + top senders/subjects)."""
    try:
        from src.utils.config import get_email_config
        addr, pwd = get_email_config()
        if not addr or not pwd:
            return ""

        from src.utils.email_client import EmailClient
        client = EmailClient(addr, pwd)
        result = client.read_inbox(max_count=5, unread_only=False)
        if not result.get("success"):
            return ""

        messages = result.get("messages", [])
        if not messages:
            return "No new emails."

        lines = [f"{len(messages)} recent email(s):"]
        for em in messages[:5]:
            sender = em.get("from", "Unknown")[:30]
            subject = em.get("subject", "(no subject)")[:50]
            lines.append(f"  - {sender}: {subject}")
        return "\n".join(lines)
    except Exception as e:
        logger.debug("Email digest fetch failed: %s", e)
        return ""


def _fetch_news_summary() -> str:
    """Get combined news headlines."""
    try:
        from src.utils.news_client import get_headlines
        data = get_headlines(hn_count=5, rss_per_feed=2)
        return data.get("summary", "")
    except Exception as e:
        logger.debug("News digest fetch failed: %s", e)
        return ""


def _fetch_weather_summary() -> str:
    """Get weather summary."""
    try:
        from src.utils.weather_client import get_weather
        data = get_weather()
        return data.get("summary", "")
    except Exception as e:
        logger.debug("Weather digest fetch failed: %s", e)
        return ""


def _fetch_calendar_summary() -> str:
    """Get upcoming calendar events summary."""
    try:
        from src.utils.calendar_client import get_upcoming_events
        data = get_upcoming_events(days_ahead=2)
        return data.get("summary", "")
    except Exception as e:
        logger.debug("Calendar digest fetch failed: %s", e)
        return ""


def gather_digest() -> Dict[str, str]:
    """Gather all morning digest components concurrently.

    Returns dict with keys: email, news, weather, calendar, combined.
    Each value is a string summary (empty if unavailable).
    """
    results: Dict[str, str] = {"email": "", "news": "", "weather": "", "calendar": ""}

    fetchers = {
        "email": _fetch_email_summary,
        "news": _fetch_news_summary,
        "weather": _fetch_weather_summary,
        "calendar": _fetch_calendar_summary,
    }

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): key for key, fn in fetchers.items()}
        for fut in as_completed(futures, timeout=20):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception as e:
                logger.debug("Digest %s failed: %s", key, e)

    # Build combined summary for the morning report prompt
    sections = []
    if results["weather"]:
        sections.append(f"Weather: {results['weather']}")
    if results["calendar"]:
        sections.append(f"Calendar:\n{results['calendar']}")
    if results["email"]:
        sections.append(f"Inbox:\n{results['email']}")
    if results["news"]:
        sections.append(f"Headlines:\n{results['news']}")

    results["combined"] = "\n\n".join(sections) if sections else ""
    return results
