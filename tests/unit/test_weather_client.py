"""Tests for src/utils/weather_client.py."""

import json
from unittest.mock import patch

import pytest

from src.utils.weather_client import get_weather, _get_location


# Sample wttr.in JSON response
_SAMPLE_WTTR = {
    "current_condition": [{
        "temp_F": "45",
        "FeelsLikeF": "40",
        "humidity": "65",
        "windspeedMiles": "12",
        "weatherDesc": [{"value": "Partly cloudy"}],
    }],
    "weather": [
        {
            "maxtempF": "52",
            "mintempF": "38",
            "hourly": [
                {"weatherDesc": [{"value": "Morning fog"}]},
                {"weatherDesc": [{"value": "Partly cloudy"}]},
                {"weatherDesc": [{"value": "Clear"}]},
            ],
        },
        {
            "maxtempF": "48",
            "mintempF": "33",
            "hourly": [
                {"weatherDesc": [{"value": "Light rain"}]},
                {"weatherDesc": [{"value": "Overcast"}]},
                {"weatherDesc": [{"value": "Cloudy"}]},
            ],
        },
    ],
}


class TestGetWeather:
    @patch("src.utils.weather_client._fetch_url")
    def test_parses_current_conditions(self, mock_fetch):
        mock_fetch.return_value = json.dumps(_SAMPLE_WTTR)
        result = get_weather("Madison,WI")
        assert result["current"]["temp_f"] == 45
        assert result["current"]["feels_like_f"] == 40
        assert result["current"]["description"] == "Partly cloudy"
        assert result["current"]["humidity"] == 65
        assert result["current"]["wind_mph"] == 12

    @patch("src.utils.weather_client._fetch_url")
    def test_parses_today_forecast(self, mock_fetch):
        mock_fetch.return_value = json.dumps(_SAMPLE_WTTR)
        result = get_weather("Madison,WI")
        assert result["today"]["high_f"] == 52
        assert result["today"]["low_f"] == 38

    @patch("src.utils.weather_client._fetch_url")
    def test_parses_tomorrow_forecast(self, mock_fetch):
        mock_fetch.return_value = json.dumps(_SAMPLE_WTTR)
        result = get_weather("Madison,WI")
        assert result["tomorrow"]["high_f"] == 48
        assert result["tomorrow"]["low_f"] == 33

    @patch("src.utils.weather_client._fetch_url")
    def test_builds_summary(self, mock_fetch):
        mock_fetch.return_value = json.dumps(_SAMPLE_WTTR)
        result = get_weather("Madison,WI")
        assert "45°F" in result["summary"]
        assert "52°F" in result["summary"]
        assert "Tomorrow" in result["summary"]

    @patch("src.utils.weather_client._fetch_url")
    def test_handles_fetch_failure(self, mock_fetch):
        mock_fetch.side_effect = Exception("Network error")
        result = get_weather("Madison,WI")
        assert result["summary"] == "Weather unavailable."
        assert result["current"] == {}

    @patch("src.utils.weather_client._fetch_url")
    def test_handles_malformed_json(self, mock_fetch):
        mock_fetch.return_value = "not json"
        result = get_weather("Madison,WI")
        assert result["summary"] == "Weather unavailable."

    @patch("src.utils.weather_client._fetch_url")
    def test_location_encoding(self, mock_fetch):
        mock_fetch.return_value = json.dumps(_SAMPLE_WTTR)
        get_weather("New York City")
        url = mock_fetch.call_args[0][0]
        assert "New+York+City" in url

    def test_fetched_at_present(self):
        with patch("src.utils.weather_client._fetch_url") as mock:
            mock.return_value = json.dumps(_SAMPLE_WTTR)
            result = get_weather("Test")
            assert "fetched_at" in result


class TestGetLocation:
    @patch("src.utils.config._identity")
    def test_reads_from_identity(self, mock_id):
        mock_id.return_value = {"user_context": {"location": "McFarland, Wisconsin"}}
        assert _get_location() == "McFarland, Wisconsin"

    @patch("src.utils.config._identity")
    def test_fallback_on_missing(self, mock_id):
        mock_id.return_value = {}
        assert _get_location() == "Madison,WI"
