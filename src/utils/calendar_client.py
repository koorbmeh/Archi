"""
Calendar integration via ICS feeds (provider-agnostic, no API keys required).

Supports any calendar that publishes an ICS/iCal URL:
  - Outlook.com: Settings → Calendar → Shared calendars → Publish
  - Google Calendar: Settings → Calendar → Integrate → Secret ICS URL
  - Apple iCloud: Calendar app → Share Calendar → Public Calendar

Configure URLs in ARCHI_CALENDAR_URLS env var (comma-separated) or
in archi_identity.yaml under user_context.calendar_urls.

Phase 1: Read-only — fetches and parses events for digest/queries.
Phase 2 (future): Microsoft Graph API for read-write calendar access.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 15


# ── Config ──────────────────────────────────────────────────────────────

def _get_calendar_urls() -> List[str]:
    """Get ICS calendar URLs from config."""
    import os
    # Primary: env var (comma-separated)
    env_urls = os.environ.get("ARCHI_CALENDAR_URLS", "").strip()
    if env_urls:
        return [u.strip() for u in env_urls.split(",") if u.strip()]

    # Fallback: archi_identity.yaml
    try:
        from src.utils.config import _identity
        ctx = _identity().get("user_context", {})
        urls = ctx.get("calendar_urls", [])
        if isinstance(urls, str):
            return [urls] if urls else []
        return [u for u in urls if u]
    except Exception:
        return []


def _get_user_timezone() -> str:
    """Get user timezone string from identity config."""
    try:
        from src.utils.config import _identity
        ctx = _identity().get("user_context", {})
        return ctx.get("timezone", "America/Chicago")
    except Exception:
        return "America/Chicago"


# ── ICS Parsing ─────────────────────────────────────────────────────────

def _fetch_ics(url: str) -> str:
    """Fetch raw ICS text from a URL."""
    req = Request(url, headers={"User-Agent": "Archi/1.0"})
    with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def _parse_ics_datetime(value: str, tz_offset: Optional[timedelta] = None) -> Optional[datetime]:
    """Parse an ICS datetime string into a UTC-aware datetime.

    Handles formats:
      - 20260306T100000Z       (UTC)
      - 20260306T100000        (local time, use tz_offset)
      - 20260306               (all-day event)
      - TZID=America/Chicago:20260306T100000  (explicit timezone)
    """
    # Strip TZID prefix if present — we'll use zoneinfo if available
    tz_name = None
    if ":" in value and not value.startswith("http"):
        parts = value.split(":", 1)
        if parts[0].startswith("TZID="):
            tz_name = parts[0].replace("TZID=", "")
        value = parts[-1]

    value = value.strip()

    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        elif "T" in value:
            dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
            # Try to resolve timezone
            if tz_name:
                try:
                    from zoneinfo import ZoneInfo
                    return dt.replace(tzinfo=ZoneInfo(tz_name))
                except Exception:
                    pass
            # Fall back to configured offset or treat as local
            if tz_offset is not None:
                return dt.replace(tzinfo=timezone(tz_offset))
            return dt.replace(tzinfo=timezone.utc)  # safe default
        else:
            # All-day event: just the date
            dt = datetime.strptime(value, "%Y%m%d")
            return dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as e:
        logger.debug("ICS datetime parse failed for %r: %s", value, e)
        return None


def _unfold_ics(text: str) -> str:
    """Unfold ICS long lines (RFC 5545: lines starting with space/tab are continuations)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded = []
    for line in lines:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return "\n".join(unfolded)


def _parse_events(ics_text: str) -> List[Dict[str, Any]]:
    """Parse VEVENT blocks from ICS text into structured event dicts."""
    ics_text = _unfold_ics(ics_text)
    events: List[Dict[str, Any]] = []
    in_event = False
    current: Dict[str, str] = {}

    for line in ics_text.split("\n"):
        line = line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            current = {}
        elif line == "END:VEVENT":
            in_event = False
            if current:
                events.append(_build_event(current))
        elif in_event and ":" in line:
            # Split on first colon, but handle properties with params (e.g., DTSTART;TZID=...:value)
            key_part, _, val = line.partition(":")
            # Normalize: DTSTART;TZID=X → DTSTART (preserve TZID in value)
            base_key = key_part.split(";")[0].upper()
            # If there's a TZID param, prepend it to the value
            if "TZID=" in key_part:
                for param in key_part.split(";"):
                    if param.startswith("TZID="):
                        val = f"{param}:{val}"
                        break
            current[base_key] = val

    return events


def _build_event(raw: Dict[str, str]) -> Dict[str, Any]:
    """Convert raw ICS key-value pairs into a structured event dict."""
    start = _parse_ics_datetime(raw.get("DTSTART", ""))
    end = _parse_ics_datetime(raw.get("DTEND", ""))
    summary = raw.get("SUMMARY", "").replace("\\,", ",").replace("\\n", " ").strip()
    location = raw.get("LOCATION", "").replace("\\,", ",").replace("\\n", " ").strip()
    description = raw.get("DESCRIPTION", "").replace("\\n", "\n").replace("\\,", ",").strip()

    all_day = False
    if start and not raw.get("DTSTART", "").replace("TZID=", "").split(":")[-1].__contains__("T"):
        all_day = True

    return {
        "summary": summary,
        "start": start,
        "end": end,
        "location": location,
        "description": description[:200] if description else "",
        "all_day": all_day,
        "status": raw.get("STATUS", "CONFIRMED").upper(),
    }


# ── Public API ──────────────────────────────────────────────────────────

def get_upcoming_events(
    days_ahead: int = 2,
    calendar_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Fetch and return upcoming calendar events from all configured ICS feeds.

    Args:
        days_ahead: How many days into the future to include (default 2 = today + tomorrow).
        calendar_urls: Override ICS URLs (otherwise reads from config).

    Returns:
        {
            "events": [{"summary": ..., "start": ..., "end": ..., ...}, ...],
            "summary": "...",    # One-line for digest prompt
            "fetched_at": "...",
        }
    """
    urls = calendar_urls or _get_calendar_urls()
    result: Dict[str, Any] = {
        "events": [],
        "summary": "",
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    if not urls:
        logger.debug("No calendar URLs configured — skipping calendar digest")
        result["summary"] = ""
        return result

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=days_ahead)

    all_events: List[Dict[str, Any]] = []

    for url in urls:
        try:
            ics_text = _fetch_ics(url)
            events = _parse_events(ics_text)
            for ev in events:
                start = ev.get("start")
                if not start:
                    continue
                # Filter to time window
                if start >= now - timedelta(hours=1) and start <= window_end:
                    if ev.get("status") != "CANCELLED":
                        all_events.append(ev)
        except Exception as e:
            logger.warning("Calendar fetch failed for %s: %s", url[:60], e)

    # Sort by start time
    all_events.sort(key=lambda e: e["start"])

    # Build summary
    result["events"] = all_events
    result["summary"] = _build_summary(all_events, now)
    return result


def _build_summary(events: List[Dict[str, Any]], now: datetime) -> str:
    """Build a compact text summary of upcoming events for the digest prompt."""
    if not events:
        return "No upcoming calendar events."

    today = now.date()
    tomorrow = today + timedelta(days=1)

    today_events = []
    tomorrow_events = []
    later_events = []

    for ev in events:
        ev_date = ev["start"].date() if ev["start"] else None
        if not ev_date:
            continue
        if ev_date == today:
            today_events.append(ev)
        elif ev_date == tomorrow:
            tomorrow_events.append(ev)
        else:
            later_events.append(ev)

    lines = []
    if today_events:
        lines.append(f"Today ({len(today_events)} event{'s' if len(today_events) != 1 else ''}):")
        for ev in today_events:
            lines.append(f"  - {_format_event_line(ev)}")
    if tomorrow_events:
        lines.append(f"Tomorrow ({len(tomorrow_events)} event{'s' if len(tomorrow_events) != 1 else ''}):")
        for ev in tomorrow_events:
            lines.append(f"  - {_format_event_line(ev)}")
    if later_events:
        for ev in later_events:
            date_str = ev["start"].strftime("%a %b %d")
            lines.append(f"  - {date_str}: {_format_event_line(ev)}")

    return "\n".join(lines) if lines else "No upcoming calendar events."


def _format_event_line(ev: Dict[str, Any]) -> str:
    """Format a single event as a compact one-liner."""
    summary = ev.get("summary", "Untitled")
    if ev.get("all_day"):
        time_str = "All day"
    elif ev.get("start"):
        time_str = ev["start"].strftime("%I:%M %p").lstrip("0")
        if ev.get("end"):
            time_str += f" – {ev['end'].strftime('%I:%M %p').lstrip('0')}"
    else:
        time_str = ""

    location = ev.get("location", "")
    parts = [f"{time_str}: {summary}" if time_str else summary]
    if location:
        parts.append(f"@ {location}")
    return " ".join(parts)
