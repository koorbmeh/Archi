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


def _fetch_supplement_status() -> str:
    """Get today's supplement intake status."""
    try:
        from src.tools.supplement_tracker import SupplementTracker
        tracker = SupplementTracker()
        if not tracker.get_active():
            return ""
        not_taken = tracker.get_not_taken_today()
        active = tracker.get_active()
        taken = len(active) - len(not_taken)
        lines = [f"{taken}/{len(active)} taken"]
        if not_taken:
            names = ", ".join(s.name for s in not_taken[:5])
            lines.append(f"Still need: {names}")
        low = tracker.get_low_stock()
        if low:
            lines.append("Low stock: " + ", ".join(f"{s.name} ({s.stock_days}d)" for s in low[:3]))
        return "\n".join(lines)
    except Exception as e:
        logger.debug("Supplement digest fetch failed: %s", e)
        return ""


def _fetch_habit_status() -> str:
    """Get today's habit completion status."""
    try:
        from src.tools.habit_tracker import HabitTracker
        tracker = HabitTracker()
        if not tracker.get_active():
            return ""
        incomplete = tracker.get_incomplete_today()
        active = tracker.get_active()
        done = len(active) - len(incomplete)
        lines = [f"{done}/{len(active)} complete"]
        if incomplete:
            names = ", ".join(h.display_name() for h in incomplete[:5])
            lines.append(f"Still need: {names}")
        streak_val = tracker.streak()
        if streak_val > 0:
            lines.append(f"Current streak: {streak_val} days")
        return "\n".join(lines)
    except Exception as e:
        logger.debug("Habit digest fetch failed: %s", e)
        return ""


def _fetch_finance_status() -> str:
    """Get a brief spending + budget snapshot."""
    try:
        from src.tools.finance_tracker import FinanceTracker
        ft = FinanceTracker()
        parts = []
        # Month-to-date spending
        month_expenses = ft.get_expenses_for_month()
        if month_expenses:
            total = sum(e.get("amount", 0) for e in month_expenses)
            parts.append(f"Month-to-date: ${total:.2f} across {len(month_expenses)} expenses")
        # Subscriptions
        sub_cost = ft.get_monthly_subscription_cost()
        if sub_cost > 0:
            parts.append(f"Subscriptions: ${sub_cost:.2f}/mo")
        # Budget alerts
        alert = ft.format_budget_alert()
        if alert:
            parts.append(alert)
        return "\n".join(parts) if parts else ""
    except Exception as e:
        logger.debug("Finance digest fetch failed: %s", e)
        return ""


def gather_digest() -> Dict[str, str]:
    """Gather all morning digest components concurrently.

    Returns dict with keys: email, news, weather, calendar, supplements,
    finance, combined. Each value is a string summary (empty if unavailable).
    """
    results: Dict[str, str] = {
        "email": "", "news": "", "weather": "", "calendar": "",
        "supplements": "", "habits": "", "finance": "",
    }

    fetchers = {
        "email": _fetch_email_summary,
        "news": _fetch_news_summary,
        "weather": _fetch_weather_summary,
        "calendar": _fetch_calendar_summary,
        "supplements": _fetch_supplement_status,
        "habits": _fetch_habit_status,
        "finance": _fetch_finance_status,
    }

    with ThreadPoolExecutor(max_workers=6) as pool:
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
    if results["supplements"]:
        sections.append(f"Supplements:\n{results['supplements']}")
    if results["habits"]:
        sections.append(f"Habits:\n{results['habits']}")
    if results["finance"]:
        sections.append(f"Finances:\n{results['finance']}")
    if results["email"]:
        sections.append(f"Inbox:\n{results['email']}")
    if results["news"]:
        sections.append(f"Headlines:\n{results['news']}")

    results["combined"] = "\n\n".join(sections) if sections else ""
    return results
