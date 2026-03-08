"""Tests for src/core/morning_digest.py."""

from unittest.mock import patch, MagicMock

import pytest

from src.core.morning_digest import (
    gather_digest,
    _fetch_email_summary,
    _fetch_news_summary,
    _fetch_weather_summary,
    _fetch_calendar_summary,
    _fetch_supplement_status,
    _fetch_finance_status,
)


class TestFetchEmailSummary:
    @patch("src.utils.config.get_email_config", return_value=(None, None))
    def test_empty_when_no_credentials(self, mock_cfg):
        assert _fetch_email_summary() == ""

    @patch("src.utils.email_client.EmailClient")
    @patch("src.utils.config.get_email_config", return_value=("test@test.com", "password"))
    def test_formats_emails(self, mock_cfg, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.read_inbox.return_value = {
            "success": True,
            "count": 2,
            "messages": [
                {"from": "Alice", "subject": "Hello"},
                {"from": "Bob", "subject": "Meeting"},
            ],
        }
        result = _fetch_email_summary()
        assert "2 recent email" in result
        assert "Alice" in result
        assert "Meeting" in result

    @patch("src.utils.email_client.EmailClient")
    @patch("src.utils.config.get_email_config", return_value=("test@test.com", "password"))
    def test_no_new_emails(self, mock_cfg, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.read_inbox.return_value = {"success": True, "count": 0, "messages": []}
        assert _fetch_email_summary() == "No new emails."


class TestFetchNewsSummary:
    @patch("src.utils.news_client.get_headlines", return_value={"summary": "- Big News (HN)"})
    def test_returns_summary(self, mock_hl):
        assert _fetch_news_summary() == "- Big News (HN)"

    @patch("src.utils.news_client.get_headlines", side_effect=Exception("Fail"))
    def test_handles_error(self, mock_hl):
        assert _fetch_news_summary() == ""


class TestFetchWeatherSummary:
    @patch("src.utils.weather_client.get_weather", return_value={"summary": "Currently 45°F"})
    def test_returns_summary(self, mock_w):
        assert _fetch_weather_summary() == "Currently 45°F"

    @patch("src.utils.weather_client.get_weather", side_effect=Exception("Fail"))
    def test_handles_error(self, mock_w):
        assert _fetch_weather_summary() == ""


class TestFetchCalendarSummary:
    @patch("src.utils.calendar_client.get_upcoming_events", return_value={"summary": "Today (1 event):\n  - 2:00 PM: Standup"})
    def test_returns_summary(self, mock_cal):
        assert "Standup" in _fetch_calendar_summary()

    @patch("src.utils.calendar_client.get_upcoming_events", side_effect=Exception("Fail"))
    def test_handles_error(self, mock_cal):
        assert _fetch_calendar_summary() == ""


class TestFetchSupplementStatus:
    @patch("src.tools.supplement_tracker.SupplementTracker")
    def test_returns_status(self, mock_cls):
        mock_tracker = mock_cls.return_value
        supp = MagicMock(name="Creatine", stock_days=5)
        supp.name = "Creatine"
        supp.display_name.return_value = "Creatine 5g"
        mock_tracker.get_active.return_value = [supp, MagicMock()]
        mock_tracker.get_not_taken_today.return_value = [supp]
        mock_tracker.get_low_stock.return_value = [supp]
        result = _fetch_supplement_status()
        assert "1/2 taken" in result
        assert "Creatine" in result

    @patch("src.tools.supplement_tracker.SupplementTracker")
    def test_empty_when_no_supplements(self, mock_cls):
        mock_cls.return_value.get_active.return_value = []
        assert _fetch_supplement_status() == ""

    def test_handles_import_error(self):
        with patch("src.core.morning_digest.logger"):
            # Should return empty on error, not raise
            with patch.dict("sys.modules", {"src.tools.supplement_tracker": None}):
                result = _fetch_supplement_status()
                assert result == ""


class TestFetchFinanceStatus:
    @patch("src.tools.finance_tracker.FinanceTracker")
    def test_returns_spending(self, mock_cls):
        mock_ft = mock_cls.return_value
        mock_ft.get_expenses_for_month.return_value = [
            {"amount": 50, "category": "groceries"},
            {"amount": 30, "category": "dining"},
        ]
        mock_ft.get_monthly_subscription_cost.return_value = 45.99
        mock_ft.format_budget_alert.return_value = ""
        result = _fetch_finance_status()
        assert "$80.00" in result
        assert "2 expenses" in result
        assert "$45.99" in result

    @patch("src.tools.finance_tracker.FinanceTracker")
    def test_empty_when_no_data(self, mock_cls):
        mock_ft = mock_cls.return_value
        mock_ft.get_expenses_for_month.return_value = []
        mock_ft.get_monthly_subscription_cost.return_value = 0
        mock_ft.format_budget_alert.return_value = ""
        assert _fetch_finance_status() == ""

    @patch("src.tools.finance_tracker.FinanceTracker")
    def test_includes_budget_alert(self, mock_cls):
        mock_ft = mock_cls.return_value
        mock_ft.get_expenses_for_month.return_value = []
        mock_ft.get_monthly_subscription_cost.return_value = 0
        mock_ft.format_budget_alert.return_value = "Groceries: $480/$500 (96%)"
        result = _fetch_finance_status()
        assert "Groceries" in result


class TestGatherDigest:
    @patch("src.core.morning_digest._fetch_finance_status", return_value="")
    @patch("src.core.morning_digest._fetch_supplement_status", return_value="")
    @patch("src.core.morning_digest._fetch_calendar_summary", return_value="Today: 1 event")
    @patch("src.core.morning_digest._fetch_weather_summary", return_value="Currently 55°F")
    @patch("src.core.morning_digest._fetch_news_summary", return_value="- AI news")
    @patch("src.core.morning_digest._fetch_email_summary", return_value="3 recent emails")
    def test_combines_all_sources(self, mock_email, mock_news, mock_weather, mock_cal, mock_supp, mock_fin):
        result = gather_digest()
        assert "email" in result
        assert "news" in result
        assert "weather" in result
        assert "calendar" in result
        assert "supplements" in result
        assert "finance" in result
        assert "combined" in result
        assert "55°F" in result["combined"]
        assert "AI news" in result["combined"]

    @patch("src.core.morning_digest._fetch_finance_status", return_value="")
    @patch("src.core.morning_digest._fetch_supplement_status", return_value="")
    @patch("src.core.morning_digest._fetch_calendar_summary", return_value="")
    @patch("src.core.morning_digest._fetch_weather_summary", return_value="Currently 40°F")
    @patch("src.core.morning_digest._fetch_news_summary", return_value="")
    @patch("src.core.morning_digest._fetch_email_summary", return_value="")
    def test_handles_partial_failure(self, mock_email, mock_news, mock_weather, mock_cal, mock_supp, mock_fin):
        result = gather_digest()
        assert result["weather"] == "Currently 40°F"
        assert "40°F" in result["combined"]

    @patch("src.core.morning_digest._fetch_finance_status", return_value="")
    @patch("src.core.morning_digest._fetch_supplement_status", return_value="")
    @patch("src.core.morning_digest._fetch_calendar_summary", return_value="")
    @patch("src.core.morning_digest._fetch_weather_summary", return_value="")
    @patch("src.core.morning_digest._fetch_news_summary", return_value="")
    @patch("src.core.morning_digest._fetch_email_summary", return_value="")
    def test_empty_when_all_fail(self, mock_email, mock_news, mock_weather, mock_cal, mock_supp, mock_fin):
        result = gather_digest()
        assert result["combined"] == ""

    @patch("src.core.morning_digest._fetch_finance_status", return_value="")
    @patch("src.core.morning_digest._fetch_supplement_status", return_value="")
    @patch("src.core.morning_digest._fetch_calendar_summary", return_value="Today: Team Standup")
    @patch("src.core.morning_digest._fetch_weather_summary", return_value="Sunny 70°F")
    @patch("src.core.morning_digest._fetch_news_summary", return_value="")
    @patch("src.core.morning_digest._fetch_email_summary", return_value="")
    def test_calendar_appears_in_combined(self, mock_email, mock_news, mock_weather, mock_cal, mock_supp, mock_fin):
        result = gather_digest()
        assert "Calendar:" in result["combined"]
        assert "Team Standup" in result["combined"]

    @patch("src.core.morning_digest._fetch_finance_status", return_value="Month-to-date: $200")
    @patch("src.core.morning_digest._fetch_supplement_status", return_value="3/5 taken")
    @patch("src.core.morning_digest._fetch_calendar_summary", return_value="")
    @patch("src.core.morning_digest._fetch_weather_summary", return_value="Sunny")
    @patch("src.core.morning_digest._fetch_news_summary", return_value="")
    @patch("src.core.morning_digest._fetch_email_summary", return_value="")
    def test_supplements_and_finance_in_combined(self, mock_email, mock_news, mock_weather, mock_cal, mock_supp, mock_fin):
        result = gather_digest()
        assert "Supplements:" in result["combined"]
        assert "3/5 taken" in result["combined"]
        assert "Finances:" in result["combined"]
        assert "$200" in result["combined"]
