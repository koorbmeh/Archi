"""Unit tests for src/tools/finance_tracker.py — session 245."""

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest
from src.tools.finance_tracker import (
    FinanceTracker,
    _normalize_category,
    _reset_for_testing,
    get_tracker,
    CATEGORIES,
)


@pytest.fixture
def tracker():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)  # start with no file
    yield FinanceTracker(data_path=path)
    if os.path.isfile(path):
        os.unlink(path)


# ── Category normalization ───────────────────────────────────────────

class TestNormalizeCategory:
    def test_known_category(self):
        assert _normalize_category("groceries") == "groceries"
        assert _normalize_category("GROCERIES") == "groceries"

    def test_alias_mapping(self):
        assert _normalize_category("restaurant") == "food"
        assert _normalize_category("uber") == "transport"
        assert _normalize_category("gym") == "fitness"
        assert _normalize_category("amazon") == "shopping"
        assert _normalize_category("pharmacy") == "health"
        assert _normalize_category("rent") == "housing"

    def test_unknown_category(self):
        assert _normalize_category("random_thing") == "other"

    def test_whitespace_handling(self):
        assert _normalize_category("  groceries  ") == "groceries"


# ── Expense tracking ────────────────────────────────────────────────

class TestExpenses:
    def test_log_expense(self, tracker):
        e = tracker.log_expense(50.0, "groceries", "weekly shop")
        assert e.amount == 50.0
        assert e.category == "groceries"
        assert e.description == "weekly shop"
        assert e.timestamp

    def test_log_expense_negative_raises(self, tracker):
        with pytest.raises(ValueError):
            tracker.log_expense(-10)

    def test_log_expense_zero_raises(self, tracker):
        with pytest.raises(ValueError):
            tracker.log_expense(0)

    def test_expense_rounding(self, tracker):
        e = tracker.log_expense(10.999, "food")
        assert e.amount == 11.0

    def test_get_expenses_for_period(self, tracker):
        tracker.log_expense(10, "food")
        tracker.log_expense(20, "transport")
        expenses = tracker.get_expenses_for_period(7)
        assert len(expenses) == 2

    def test_get_expenses_for_month(self, tracker):
        tracker.log_expense(30, "groceries")
        today = date.today()
        expenses = tracker.get_expenses_for_month(today.year, today.month)
        assert len(expenses) == 1

    def test_spending_by_category(self, tracker):
        tracker.log_expense(50, "groceries")
        tracker.log_expense(30, "groceries")
        tracker.log_expense(20, "transport")
        by_cat = tracker.get_spending_by_category(30)
        assert by_cat["groceries"] == 80
        assert by_cat["transport"] == 20

    def test_total_spending(self, tracker):
        tracker.log_expense(50, "food")
        tracker.log_expense(25, "transport")
        assert tracker.get_total_spending(30) == 75


# ── Subscriptions ────────────────────────────────────────────────────

class TestSubscriptions:
    def test_add_subscription(self, tracker):
        sub = tracker.add_subscription("Netflix", 15.99)
        assert sub.name == "Netflix"
        assert sub.amount == 15.99
        assert sub.frequency == "monthly"
        assert sub.active is True

    def test_add_subscription_empty_name_raises(self, tracker):
        with pytest.raises(ValueError):
            tracker.add_subscription("", 10)

    def test_add_subscription_zero_amount_raises(self, tracker):
        with pytest.raises(ValueError):
            tracker.add_subscription("Test", 0)

    def test_cancel_subscription(self, tracker):
        tracker.add_subscription("Netflix", 15.99)
        assert tracker.cancel_subscription("Netflix") is True
        assert len(tracker.get_active_subscriptions()) == 0

    def test_cancel_nonexistent(self, tracker):
        assert tracker.cancel_subscription("Nonexistent") is False

    def test_monthly_cost_calculation(self, tracker):
        tracker.add_subscription("Netflix", 15.99, "monthly")
        tracker.add_subscription("Yearly Service", 120, "yearly")
        cost = tracker.get_monthly_subscription_cost()
        assert cost == round(15.99 + 120 / 12, 2)

    def test_due_subscriptions(self, tracker):
        sub = tracker.add_subscription("Soon", 10, "weekly")
        due = tracker.get_due_subscriptions(within_days=14)
        assert len(due) == 1
        assert due[0].name == "Soon"

    def test_invalid_frequency_defaults_monthly(self, tracker):
        sub = tracker.add_subscription("Test", 10, "biweekly")
        assert sub.frequency == "monthly"


# ── Budgets ──────────────────────────────────────────────────────────

class TestBudgets:
    def test_set_budget(self, tracker):
        b = tracker.set_budget("groceries", 500)
        assert b.category == "groceries"
        assert b.monthly_limit == 500
        assert b.alert_threshold == 0.8

    def test_set_total_budget(self, tracker):
        b = tracker.set_budget("total", 3000)
        assert b.category == "total"

    def test_check_budgets_under(self, tracker):
        tracker.set_budget("groceries", 500)
        tracker.log_expense(100, "groceries")
        checks = tracker.check_budgets()
        assert len(checks) == 1
        budget, spent, over = checks[0]
        assert spent == 100
        assert over is False

    def test_check_budgets_over_threshold(self, tracker):
        tracker.set_budget("groceries", 100, alert_threshold=0.8)
        tracker.log_expense(85, "groceries")
        checks = tracker.check_budgets()
        _, spent, over = checks[0]
        assert spent == 85
        assert over is True

    def test_check_total_budget(self, tracker):
        tracker.set_budget("total", 200)
        tracker.log_expense(50, "food")
        tracker.log_expense(100, "transport")
        tracker.log_expense(60, "entertainment")
        checks = tracker.check_budgets()
        _, spent, over = checks[0]
        assert spent == 210
        assert over is True


# ── Formatting ───────────────────────────────────────────────────────

class TestFormatting:
    def test_spending_summary_empty(self, tracker):
        result = tracker.format_spending_summary(7)
        assert "No expenses" in result

    def test_spending_summary_with_data(self, tracker):
        tracker.log_expense(50, "groceries")
        result = tracker.format_spending_summary(7)
        assert "$50.00" in result
        assert "groceries" in result

    def test_subscription_list_empty(self, tracker):
        result = tracker.format_subscription_list()
        assert "No active" in result

    def test_subscription_list_with_data(self, tracker):
        tracker.add_subscription("Netflix", 15.99)
        result = tracker.format_subscription_list()
        assert "Netflix" in result
        assert "$15.99" in result

    def test_budget_report_empty(self, tracker):
        result = tracker.format_budget_report()
        assert "No budgets" in result

    def test_budget_report_with_data(self, tracker):
        tracker.set_budget("groceries", 500)
        tracker.log_expense(100, "groceries")
        result = tracker.format_budget_report()
        assert "groceries" in result
        assert "$100.00" in result

    def test_monthly_report(self, tracker):
        tracker.log_expense(50, "food", "lunch")
        tracker.add_subscription("Netflix", 15.99)
        result = tracker.format_monthly_report()
        assert "Financial Report" in result
        assert "$50.00" in result

    def test_budget_alert_no_budgets(self, tracker):
        assert tracker.format_budget_alert() == ""

    def test_budget_alert_no_alerts(self, tracker):
        tracker.set_budget("groceries", 1000)
        tracker.log_expense(10, "groceries")
        assert tracker.format_budget_alert() == ""

    def test_budget_alert_with_warning(self, tracker):
        tracker.set_budget("groceries", 100)
        tracker.log_expense(85, "groceries")
        result = tracker.format_budget_alert()
        assert "Budget alert" in result
        assert "groceries" in result


# ── Persistence ──────────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_load(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)
        try:
            t1 = FinanceTracker(data_path=path)
            t1.log_expense(50, "food")
            t1.add_subscription("Netflix", 15.99)
            t1.set_budget("food", 300)

            t2 = FinanceTracker(data_path=path)
            assert len(t2.get_expenses_for_period(30)) == 1
            assert len(t2.get_active_subscriptions()) == 1
            assert len(t2.check_budgets()) == 1
        finally:
            if os.path.isfile(path):
                os.unlink(path)

    def test_corrupted_file_handled(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w") as f:
                f.write("not json")
            tracker = FinanceTracker(data_path=path)
            assert len(tracker.get_expenses_for_period(30)) == 0
        finally:
            if os.path.isfile(path):
                os.unlink(path)


# ── Singleton ────────────────────────────────────────────────────────

class TestSingleton:
    def test_get_tracker_returns_same_instance(self):
        _reset_for_testing()
        with patch("src.tools.finance_tracker._DATA_PATH", "nonexistent_path.json"):
            t1 = get_tracker()
            t2 = get_tracker()
            assert t1 is t2
        _reset_for_testing()

    def test_reset_clears_instance(self):
        _reset_for_testing()
        with patch("src.tools.finance_tracker._DATA_PATH", "nonexistent_path.json"):
            t1 = get_tracker()
            _reset_for_testing()
            t2 = get_tracker()
            assert t1 is not t2
        _reset_for_testing()
