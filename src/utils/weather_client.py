"""
Weather data via wttr.in (no API key required).

Uses the user's location from archi_identity.yaml.
Falls back to OpenWeatherMap if OPENWEATHER_API_KEY is set in .env.
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 10


def _get_location() -> str:
    """Get user location from archi_identity.yaml."""
    try:
        from src.utils.config import _identity
        ctx = _identity().get("user_context", {})
        return ctx.get("location", "Madison,WI")
    except Exception:
        return "Madison,WI"


def _fetch_url(url: str) -> str:
    """Fetch text from a URL using stdlib."""
    req = Request(url, headers={"User-Agent": "Archi/1.0"})
    with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def get_weather(location: Optional[str] = None) -> Dict[str, Any]:
    """Fetch current weather + forecast via wttr.in.

    Returns:
        {
            "location": "McFarland, Wisconsin",
            "current": {"temp_f": 45, "feels_like_f": 40, "description": "Partly cloudy", "humidity": 65, "wind_mph": 12},
            "today": {"high_f": 52, "low_f": 38, "description": "Partly cloudy"},
            "tomorrow": {"high_f": 48, "low_f": 33, "description": "Light rain"},
            "summary": "...",  # One-line for digest prompt
            "fetched_at": "...",
        }
    """
    loc = location or _get_location()
    # URL-encode spaces
    loc_encoded = loc.replace(" ", "+")

    result: Dict[str, Any] = {
        "location": loc,
        "current": {},
        "today": {},
        "tomorrow": {},
        "summary": "",
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    try:
        raw = _fetch_url(f"https://wttr.in/{loc_encoded}?format=j1")
        data = json.loads(raw)
    except Exception as e:
        logger.warning("wttr.in fetch failed: %s", e)
        result["summary"] = "Weather unavailable."
        return result

    # Parse current conditions
    try:
        cc = data["current_condition"][0]
        result["current"] = {
            "temp_f": int(cc.get("temp_F", 0)),
            "feels_like_f": int(cc.get("FeelsLikeF", 0)),
            "description": cc.get("weatherDesc", [{}])[0].get("value", "Unknown"),
            "humidity": int(cc.get("humidity", 0)),
            "wind_mph": int(cc.get("windspeedMiles", 0)),
        }
    except (KeyError, IndexError, ValueError) as e:
        logger.debug("Weather current parse error: %s", e)

    # Parse today + tomorrow forecast
    try:
        weather_days = data.get("weather", [])
        for i, key in enumerate(["today", "tomorrow"]):
            if i < len(weather_days):
                day = weather_days[i]
                # Use midday hourly entry for description
                hourly = day.get("hourly", [])
                midday = hourly[len(hourly) // 2] if hourly else {}
                result[key] = {
                    "high_f": int(day.get("maxtempF", 0)),
                    "low_f": int(day.get("mintempF", 0)),
                    "description": midday.get("weatherDesc", [{}])[0].get("value", ""),
                }
    except (KeyError, IndexError, ValueError) as e:
        logger.debug("Weather forecast parse error: %s", e)

    # Build summary
    cur = result["current"]
    today = result["today"]
    tmrw = result["tomorrow"]

    parts = []
    if cur.get("temp_f"):
        parts.append(f"Currently {cur['temp_f']}°F ({cur.get('description', '')})")
    if today.get("high_f"):
        parts.append(f"Today: {today['high_f']}°F high, {today['low_f']}°F low — {today.get('description', '')}")
    if tmrw.get("high_f"):
        parts.append(f"Tomorrow: {tmrw['high_f']}°F high, {tmrw['low_f']}°F low — {tmrw.get('description', '')}")

    result["summary"] = ". ".join(parts) if parts else "Weather data unavailable."
    return result
