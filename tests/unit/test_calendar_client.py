"""Tests for src/utils/calendar_client.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.utils.calendar_client import (
    _parse_ics_datetime,
    _unfold_ics,
    _parse_events,
    _build_summary,
    _format_event_line,
    _get_calendar_urls,
    get_upcoming_events,
)


# ── Sample ICS data ─────────────────────────────────────────────────────

_SAMPLE_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
DTSTART:20260306T140000Z
DTEND:20260306T150000Z
SUMMARY:Team Standup
LOCATION:Zoom
DESCRIPTION:Daily sync meeting
STATUS:CONFIRMED
END:VEVENT
BEGIN:VEVENT
DTSTART:20260307T100000Z
DTEND:20260307T113000Z
SUMMARY:Dentist Appointment
LOCATION:123 Main St
STATUS:CONFIRMED
END:VEVENT
BEGIN:VEVENT
DTSTART:20260306T180000Z
DTEND:20260306T190000Z
SUMMARY:Cancelled Meeting
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""

_ALLDAY_ICS = """\
BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260306
DTEND;VALUE=DATE:20260307
SUMMARY:Jesse's Birthday
END:VEVENT
END:VCALENDAR
"""

_TZID_ICS = """\
BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/Chicago:20260306T090000
DTEND;TZID=America/Chicago:20260306T100000
SUMMARY:Morning Coffee
LOCATION:Kitchen
END:VEVENT
END:VCALENDAR
"""

_FOLDED_ICS = """\
BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260306T140000Z
DTEND:20260306T150000Z
SUMMARY:This is a very long event title that spans
 multiple lines in the ICS file
LOCATION:Conference Room A\\, Building 3
END:VEVENT
END:VCALENDAR
"""


# ── ICS Datetime Parsing ────────────────────────────────────────────────

class TestParseIcsDatetime:
    def test_utc_format(self):
        dt = _parse_ics_datetime("20260306T140000Z")
        assert dt == datetime(2026, 3, 6, 14, 0, 0, tzinfo=timezone.utc)

    def test_local_format_no_tz(self):
        dt = _parse_ics_datetime("20260306T090000")
        assert dt is not None
        assert dt.hour == 9
        assert dt.day == 6

    def test_date_only(self):
        dt = _parse_ics_datetime("20260306")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 6

    def test_tzid_format(self):
        dt = _parse_ics_datetime("TZID=America/Chicago:20260306T090000")
        assert dt is not None
        assert dt.hour == 9

    def test_invalid_returns_none(self):
        assert _parse_ics_datetime("not-a-date") is None
        assert _parse_ics_datetime("") is None


# ── ICS Unfolding ────────────────────────────────────────────────────────

class TestUnfoldIcs:
    def test_unfolds_continuation_lines(self):
        text = "SUMMARY:Hello\r\n World\r\nLOCATION:Here"
        result = _unfold_ics(text)
        assert "SUMMARY:HelloWorld" in result
        assert "LOCATION:Here" in result

    def test_no_folding_passthrough(self):
        text = "SUMMARY:Hello\nLOCATION:Here"
        result = _unfold_ics(text)
        assert "SUMMARY:Hello" in result


# ── Event Parsing ────────────────────────────────────────────────────────

class TestParseEvents:
    def test_parses_basic_events(self):
        events = _parse_events(_SAMPLE_ICS)
        assert len(events) == 3
        assert events[0]["summary"] == "Team Standup"
        assert events[0]["location"] == "Zoom"

    def test_all_day_event(self):
        events = _parse_events(_ALLDAY_ICS)
        assert len(events) == 1
        assert events[0]["all_day"] is True
        assert events[0]["summary"] == "Jesse's Birthday"

    def test_tzid_event(self):
        events = _parse_events(_TZID_ICS)
        assert len(events) == 1
        assert events[0]["summary"] == "Morning Coffee"
        assert events[0]["start"].hour == 9

    def test_folded_lines(self):
        events = _parse_events(_FOLDED_ICS)
        assert len(events) == 1
        assert "very long event title" in events[0]["summary"]
        assert "multiple lines" in events[0]["summary"]

    def test_escaped_characters(self):
        events = _parse_events(_FOLDED_ICS)
        assert events[0]["location"] == "Conference Room A, Building 3"

    def test_empty_ics(self):
        events = _parse_events("BEGIN:VCALENDAR\nEND:VCALENDAR")
        assert events == []


# ── Summary Building ────────────────────────────────────────────────────

class TestBuildSummary:
    def test_empty_events(self):
        result = _build_summary([], datetime.now(timezone.utc))
        assert "No upcoming" in result

    def test_today_events(self):
        now = datetime(2026, 3, 6, 12, 0, 0, tzinfo=timezone.utc)
        events = [{
            "summary": "Team Standup",
            "start": datetime(2026, 3, 6, 14, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 3, 6, 15, 0, 0, tzinfo=timezone.utc),
            "location": "Zoom",
            "all_day": False,
            "status": "CONFIRMED",
        }]
        result = _build_summary(events, now)
        assert "Today" in result
        assert "Team Standup" in result

    def test_tomorrow_events(self):
        now = datetime(2026, 3, 6, 12, 0, 0, tzinfo=timezone.utc)
        events = [{
            "summary": "Dentist",
            "start": datetime(2026, 3, 7, 10, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 3, 7, 11, 0, 0, tzinfo=timezone.utc),
            "location": "",
            "all_day": False,
            "status": "CONFIRMED",
        }]
        result = _build_summary(events, now)
        assert "Tomorrow" in result
        assert "Dentist" in result


# ── Event Line Formatting ───────────────────────────────────────────────

class TestFormatEventLine:
    def test_timed_event(self):
        ev = {
            "summary": "Standup",
            "start": datetime(2026, 3, 6, 14, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 3, 6, 15, 0, 0, tzinfo=timezone.utc),
            "location": "Zoom",
            "all_day": False,
        }
        line = _format_event_line(ev)
        assert "Standup" in line
        assert "Zoom" in line

    def test_all_day_event(self):
        ev = {
            "summary": "Birthday",
            "start": datetime(2026, 3, 6, tzinfo=timezone.utc),
            "end": None,
            "location": "",
            "all_day": True,
        }
        line = _format_event_line(ev)
        assert "All day" in line
        assert "Birthday" in line

    def test_no_location(self):
        ev = {
            "summary": "Think",
            "start": datetime(2026, 3, 6, 10, 0, tzinfo=timezone.utc),
            "end": None,
            "location": "",
            "all_day": False,
        }
        line = _format_event_line(ev)
        assert "Think" in line
        assert "@" not in line


# ── Config Loading ──────────────────────────────────────────────────────

class TestGetCalendarUrls:
    @patch.dict("os.environ", {"ARCHI_CALENDAR_URLS": "https://cal1.ics,https://cal2.ics"})
    def test_reads_from_env(self):
        urls = _get_calendar_urls()
        assert len(urls) == 2
        assert urls[0] == "https://cal1.ics"

    @patch.dict("os.environ", {"ARCHI_CALENDAR_URLS": ""})
    @patch("src.utils.config._identity")
    def test_reads_from_identity(self, mock_id):
        mock_id.return_value = {
            "user_context": {"calendar_urls": ["https://cal.ics"]}
        }
        urls = _get_calendar_urls()
        assert urls == ["https://cal.ics"]

    @patch.dict("os.environ", {"ARCHI_CALENDAR_URLS": ""})
    @patch("src.utils.config._identity")
    def test_empty_when_not_configured(self, mock_id):
        mock_id.return_value = {}
        urls = _get_calendar_urls()
        assert urls == []

    @patch.dict("os.environ", {"ARCHI_CALENDAR_URLS": "  https://cal.ics  , https://cal2.ics  "})
    def test_strips_whitespace(self):
        urls = _get_calendar_urls()
        assert urls == ["https://cal.ics", "https://cal2.ics"]


# ── Integration: get_upcoming_events ─────────────────────────────────────

class TestGetUpcomingEvents:
    @patch("src.utils.calendar_client._fetch_ics")
    @patch("src.utils.calendar_client._get_calendar_urls")
    def test_filters_to_time_window(self, mock_urls, mock_fetch):
        mock_urls.return_value = ["https://example.com/cal.ics"]
        # Create ICS with event today (within window)
        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y%m%dT%H%M%S") + "Z"
        tomorrow = now + timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y%m%dT%H%M%S") + "Z"
        far_future = now + timedelta(days=30)
        far_str = far_future.strftime("%Y%m%dT%H%M%S") + "Z"

        ics = f"""\
BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:{today_str}
SUMMARY:Today Event
END:VEVENT
BEGIN:VEVENT
DTSTART:{far_str}
SUMMARY:Far Future Event
END:VEVENT
END:VCALENDAR
"""
        mock_fetch.return_value = ics
        result = get_upcoming_events(days_ahead=2)
        summaries = [e["summary"] for e in result["events"]]
        assert "Today Event" in summaries
        assert "Far Future Event" not in summaries

    @patch("src.utils.calendar_client._fetch_ics")
    @patch("src.utils.calendar_client._get_calendar_urls")
    def test_excludes_cancelled_events(self, mock_urls, mock_fetch):
        mock_urls.return_value = ["https://example.com/cal.ics"]
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y%m%dT%H%M%S") + "Z"
        ics = f"""\
BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:{now_str}
SUMMARY:Active Meeting
STATUS:CONFIRMED
END:VEVENT
BEGIN:VEVENT
DTSTART:{now_str}
SUMMARY:Dead Meeting
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""
        mock_fetch.return_value = ics
        result = get_upcoming_events(days_ahead=2)
        summaries = [e["summary"] for e in result["events"]]
        assert "Active Meeting" in summaries
        assert "Dead Meeting" not in summaries

    @patch("src.utils.calendar_client._get_calendar_urls")
    def test_no_urls_returns_empty(self, mock_urls):
        mock_urls.return_value = []
        result = get_upcoming_events()
        assert result["events"] == []
        assert result["summary"] == ""

    @patch("src.utils.calendar_client._fetch_ics")
    @patch("src.utils.calendar_client._get_calendar_urls")
    def test_handles_fetch_failure_gracefully(self, mock_urls, mock_fetch):
        mock_urls.return_value = ["https://bad.url/cal.ics"]
        mock_fetch.side_effect = Exception("Network error")
        result = get_upcoming_events()
        assert result["events"] == []

    @patch("src.utils.calendar_client._fetch_ics")
    @patch("src.utils.calendar_client._get_calendar_urls")
    def test_sorts_events_by_start_time(self, mock_urls, mock_fetch):
        mock_urls.return_value = ["https://example.com/cal.ics"]
        now = datetime.now(timezone.utc)
        later = now + timedelta(hours=3)
        soon = now + timedelta(hours=1)
        ics = f"""\
BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:{later.strftime("%Y%m%dT%H%M%SZ")}
SUMMARY:Later Event
END:VEVENT
BEGIN:VEVENT
DTSTART:{soon.strftime("%Y%m%dT%H%M%SZ")}
SUMMARY:Soon Event
END:VEVENT
END:VCALENDAR
"""
        mock_fetch.return_value = ics
        result = get_upcoming_events(days_ahead=2)
        assert result["events"][0]["summary"] == "Soon Event"
        assert result["events"][1]["summary"] == "Later Event"

    @patch("src.utils.calendar_client._fetch_ics")
    @patch("src.utils.calendar_client._get_calendar_urls")
    def test_multiple_calendars(self, mock_urls, mock_fetch):
        mock_urls.return_value = ["https://a.ics", "https://b.ics"]
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y%m%dT%H%M%SZ")

        def side_effect(url):
            if "a.ics" in url:
                return f"BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:{now_str}\nSUMMARY:Cal A Event\nEND:VEVENT\nEND:VCALENDAR"
            return f"BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:{now_str}\nSUMMARY:Cal B Event\nEND:VEVENT\nEND:VCALENDAR"

        mock_fetch.side_effect = side_effect
        result = get_upcoming_events(days_ahead=2)
        summaries = [e["summary"] for e in result["events"]]
        assert "Cal A Event" in summaries
        assert "Cal B Event" in summaries
