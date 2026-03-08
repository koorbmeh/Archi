"""Personal Finance Tracker — logs expenses, tracks subscriptions, budget alerts.

Session 245: Practical daily-life financial management capability.

Discord commands: "spent $50 on groceries", "add subscription Netflix $15.99/month",
"what did I spend this week?", "budget report", "what subscriptions do I have?"

Persistence: data/finance_tracker.json — expenses + subscriptions + budgets.
"""

import json
import logging
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple

from src.utils.paths import base_path

logger = logging.getLogger(__name__)

_DATA_PATH = os.path.join(base_path(), "data", "finance_tracker.json")
_lock = threading.Lock()

# ── Data classes ─────────────────────────────────────────────────────

CATEGORIES = [
    "groceries", "food", "dining", "transport", "gas", "utilities",
    "entertainment", "shopping", "health", "fitness", "subscriptions",
    "housing", "insurance", "education", "gifts", "travel", "personal",
    "tech", "clothing", "other",
]


@dataclass
class Expense:
    """A single expense entry."""
    amount: float
    category: str = "other"
    description: str = ""
    timestamp: str = ""  # ISO datetime
    recurring: bool = False


@dataclass
class Subscription:
    """A recurring subscription."""
    name: str
    amount: float
    frequency: str = "monthly"  # monthly, yearly, weekly
    category: str = "subscriptions"
    next_due: str = ""  # ISO date
    active: bool = True
    notes: str = ""
    added_date: str = ""


@dataclass
class Budget:
    """A spending budget for a category or overall."""
    category: str  # "total" for overall budget
    monthly_limit: float
    alert_threshold: float = 0.8  # alert at 80% by default


class FinanceTracker:
    """Manages expenses, subscriptions, and budgets."""

    def __init__(self, data_path: str = ""):
        self._path = data_path or _DATA_PATH
        self._expenses: List[dict] = []
        self._subscriptions: Dict[str, Subscription] = {}
        self._budgets: Dict[str, Budget] = {}
        self._load()

    # ── Expense tracking ─────────────────────────────────────────────

    def log_expense(self, amount: float, category: str = "other",
                    description: str = "") -> Expense:
        """Log a new expense."""
        if amount <= 0:
            raise ValueError("Amount must be positive")

        cat = _normalize_category(category)
        expense = Expense(
            amount=round(amount, 2),
            category=cat,
            description=description.strip(),
            timestamp=datetime.now().isoformat(),
        )
        with _lock:
            self._expenses.append(asdict(expense))
            self._save()
        logger.info("Logged expense: $%.2f %s (%s)", amount, cat, description)
        return expense

    def get_expenses_for_period(self, days: int = 7) -> List[dict]:
        """Return expenses from the last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        return [e for e in self._expenses if e.get("timestamp", "") >= cutoff]

    def get_expenses_for_month(self, year: int = 0, month: int = 0) -> List[dict]:
        """Return expenses for a specific month (defaults to current)."""
        if not year:
            year = date.today().year
        if not month:
            month = date.today().month
        prefix = f"{year:04d}-{month:02d}"
        return [e for e in self._expenses
                if e.get("timestamp", "").startswith(prefix)]

    def get_spending_by_category(self, days: int = 30) -> Dict[str, float]:
        """Return total spending grouped by category for the last N days."""
        expenses = self.get_expenses_for_period(days)
        by_cat: Dict[str, float] = {}
        for e in expenses:
            cat = e.get("category", "other")
            by_cat[cat] = by_cat.get(cat, 0) + e.get("amount", 0)
        return dict(sorted(by_cat.items(), key=lambda x: x[1], reverse=True))

    def get_total_spending(self, days: int = 30) -> float:
        """Return total spending for the last N days."""
        return sum(e.get("amount", 0) for e in self.get_expenses_for_period(days))

    # ── Subscription management ──────────────────────────────────────

    def add_subscription(self, name: str, amount: float,
                         frequency: str = "monthly",
                         category: str = "subscriptions",
                         notes: str = "") -> Subscription:
        """Add or update a subscription."""
        key = name.lower().strip()
        if not key:
            raise ValueError("Subscription name cannot be empty")
        if amount <= 0:
            raise ValueError("Amount must be positive")

        freq = frequency.strip().lower()
        if freq not in ("monthly", "yearly", "weekly"):
            freq = "monthly"

        # Calculate next due date
        today = date.today()
        if freq == "monthly":
            next_month = today.month % 12 + 1
            next_year = today.year + (1 if next_month == 1 else 0)
            next_due = date(next_year, next_month, min(today.day, 28))
        elif freq == "yearly":
            next_due = date(today.year + 1, today.month, today.day)
        else:  # weekly
            next_due = today + timedelta(weeks=1)

        sub = Subscription(
            name=name.strip(),
            amount=round(amount, 2),
            frequency=freq,
            category=_normalize_category(category),
            next_due=next_due.isoformat(),
            active=True,
            notes=notes.strip(),
            added_date=today.isoformat(),
        )
        with _lock:
            self._subscriptions[key] = sub
            self._save()
        logger.info("Added subscription: %s $%.2f/%s", name, amount, freq)
        return sub

    def cancel_subscription(self, name: str) -> bool:
        """Cancel (deactivate) a subscription."""
        key = name.lower().strip()
        with _lock:
            if key in self._subscriptions:
                self._subscriptions[key].active = False
                self._save()
                logger.info("Cancelled subscription: %s", name)
                return True
        return False

    def get_active_subscriptions(self) -> List[Subscription]:
        """Return all active subscriptions."""
        return [s for s in self._subscriptions.values() if s.active]

    def get_monthly_subscription_cost(self) -> float:
        """Calculate total monthly cost of all active subscriptions."""
        total = 0.0
        for sub in self.get_active_subscriptions():
            if sub.frequency == "monthly":
                total += sub.amount
            elif sub.frequency == "yearly":
                total += sub.amount / 12
            elif sub.frequency == "weekly":
                total += sub.amount * 4.33
        return round(total, 2)

    def get_due_subscriptions(self, within_days: int = 7) -> List[Subscription]:
        """Return subscriptions due within the next N days."""
        cutoff = (date.today() + timedelta(days=within_days)).isoformat()
        today_str = date.today().isoformat()
        return [
            s for s in self.get_active_subscriptions()
            if s.next_due and today_str <= s.next_due <= cutoff
        ]

    # ── Budgets ──────────────────────────────────────────────────────

    def set_budget(self, category: str, monthly_limit: float,
                   alert_threshold: float = 0.8) -> Budget:
        """Set a monthly spending budget for a category (or 'total')."""
        cat = category.lower().strip() if category.lower().strip() != "total" else "total"
        if cat != "total":
            cat = _normalize_category(cat)

        budget = Budget(
            category=cat,
            monthly_limit=round(monthly_limit, 2),
            alert_threshold=max(0.1, min(1.0, alert_threshold)),
        )
        with _lock:
            self._budgets[cat] = budget
            self._save()
        logger.info("Set budget: %s $%.2f/month", cat, monthly_limit)
        return budget

    def check_budgets(self) -> List[Tuple[Budget, float, bool]]:
        """Check all budgets against current month spending.

        Returns list of (budget, current_spending, is_over_threshold).
        """
        month_expenses = self.get_expenses_for_month()
        month_by_cat: Dict[str, float] = {}
        month_total = 0.0
        for e in month_expenses:
            cat = e.get("category", "other")
            amt = e.get("amount", 0)
            month_by_cat[cat] = month_by_cat.get(cat, 0) + amt
            month_total += amt

        results = []
        for budget in self._budgets.values():
            if budget.category == "total":
                spent = month_total
            else:
                spent = month_by_cat.get(budget.category, 0)
            over = spent >= (budget.monthly_limit * budget.alert_threshold)
            results.append((budget, round(spent, 2), over))
        return results

    # ── Formatting ───────────────────────────────────────────────────

    def format_spending_summary(self, days: int = 7) -> str:
        """Format a spending summary for Discord display."""
        expenses = self.get_expenses_for_period(days)
        if not expenses:
            return f"No expenses logged in the last {days} days."

        total = sum(e.get("amount", 0) for e in expenses)
        by_cat = self.get_spending_by_category(days)

        lines = [f"**Spending Summary ({days} days): ${total:.2f}**"]
        for cat, amount in by_cat.items():
            lines.append(f"  • {cat}: ${amount:.2f}")

        return "\n".join(lines)

    def format_subscription_list(self) -> str:
        """Format active subscriptions for Discord display."""
        subs = self.get_active_subscriptions()
        if not subs:
            return "No active subscriptions tracked."

        monthly_total = self.get_monthly_subscription_cost()
        lines = [f"**Your Subscriptions** (${monthly_total:.2f}/month):"]
        for s in sorted(subs, key=lambda x: x.name.lower()):
            freq_label = {"monthly": "/mo", "yearly": "/yr", "weekly": "/wk"}
            freq = freq_label.get(s.frequency, f"/{s.frequency}")
            line = f"  • **{s.name}** — ${s.amount:.2f}{freq}"
            if s.notes:
                line += f" ({s.notes})"
            lines.append(line)

        return "\n".join(lines)

    def format_budget_report(self) -> str:
        """Format budget status for Discord display."""
        if not self._budgets:
            return "No budgets set. Try: \"set budget groceries $500/month\""

        checks = self.check_budgets()
        lines = ["**Budget Report (this month):**"]
        for budget, spent, over in checks:
            pct = (spent / budget.monthly_limit * 100) if budget.monthly_limit else 0
            status = "OVER" if spent > budget.monthly_limit else ("WARNING" if over else "OK")
            icon = {"OK": "✅", "WARNING": "⚠️", "OVER": "🚨"}.get(status, "")
            lines.append(
                f"  {icon} **{budget.category}**: ${spent:.2f} / ${budget.monthly_limit:.2f} ({pct:.0f}%)"
            )

        return "\n".join(lines)

    def format_monthly_report(self) -> str:
        """Format a comprehensive monthly report."""
        month_expenses = self.get_expenses_for_month()
        total = sum(e.get("amount", 0) for e in month_expenses)
        by_cat: Dict[str, float] = {}
        for e in month_expenses:
            cat = e.get("category", "other")
            by_cat[cat] = by_cat.get(cat, 0) + e.get("amount", 0)

        sub_cost = self.get_monthly_subscription_cost()
        today = date.today()
        month_name = today.strftime("%B %Y")

        lines = [
            f"**Financial Report — {month_name}**",
            f"Total spending: ${total:.2f}",
            f"Subscriptions: ${sub_cost:.2f}/month",
        ]

        if by_cat:
            lines.append("\n**By category:**")
            for cat, amt in sorted(by_cat.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  • {cat}: ${amt:.2f}")

        budget_checks = self.check_budgets()
        alerts = [(b, s, o) for b, s, o in budget_checks if o]
        if alerts:
            lines.append("\n**Budget alerts:**")
            for budget, spent, _ in alerts:
                lines.append(f"  ⚠️ {budget.category}: ${spent:.2f} / ${budget.monthly_limit:.2f}")

        due_subs = self.get_due_subscriptions()
        if due_subs:
            lines.append("\n**Due soon:**")
            for sub in due_subs:
                lines.append(f"  • {sub.name} (${sub.amount:.2f}) — due {sub.next_due}")

        return "\n".join(lines)

    def format_budget_alert(self) -> str:
        """Format budget alert for proactive notification. Returns empty if no alerts."""
        if not self._budgets:
            return ""
        checks = self.check_budgets()
        alerts = [(b, s) for b, s, over in checks if over]
        if not alerts:
            return ""

        lines = ["**Budget alert:**"]
        for budget, spent in alerts:
            pct = (spent / budget.monthly_limit * 100) if budget.monthly_limit else 0
            lines.append(
                f"  ⚠️ {budget.category}: ${spent:.2f} / ${budget.monthly_limit:.2f} ({pct:.0f}%)"
            )
        return "\n".join(lines)

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load data from disk."""
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._expenses = data.get("expenses", [])
            for key, sdict in data.get("subscriptions", {}).items():
                self._subscriptions[key] = Subscription(**sdict)
            for key, bdict in data.get("budgets", {}).items():
                self._budgets[key] = Budget(**bdict)
            # Trim old expenses (keep last 365 days)
            cutoff = (date.today() - timedelta(days=365)).isoformat()
            self._expenses = [
                e for e in self._expenses
                if e.get("timestamp", "") >= cutoff
            ]
            logger.debug("Loaded %d expenses, %d subscriptions, %d budgets",
                         len(self._expenses), len(self._subscriptions),
                         len(self._budgets))
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.error("Failed to load finance data: %s", e)

    def _save(self) -> None:
        """Persist data to disk."""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        data = {
            "expenses": self._expenses,
            "subscriptions": {k: asdict(v) for k, v in self._subscriptions.items()},
            "budgets": {k: asdict(v) for k, v in self._budgets.items()},
            "last_updated": datetime.now().isoformat(),
        }
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.error("Failed to save finance data: %s", e)


# ── Helpers ──────────────────────────────────────────────────────────

def _normalize_category(category: str) -> str:
    """Normalize a category string to a known category."""
    cat = category.strip().lower()
    if cat in CATEGORIES:
        return cat
    # Fuzzy matching for common variants
    aliases = {
        "food": ["restaurant", "takeout", "delivery", "eating out", "lunch", "dinner", "breakfast"],
        "dining": ["restaurant", "eating out"],
        "groceries": ["grocery", "supermarket", "market"],
        "transport": ["transportation", "uber", "lyft", "taxi", "bus", "train", "subway", "transit"],
        "gas": ["fuel", "petrol", "gasoline"],
        "entertainment": ["movies", "games", "gaming", "streaming", "concert", "show"],
        "shopping": ["amazon", "online", "store", "retail"],
        "health": ["medical", "doctor", "pharmacy", "medicine", "dental", "hospital"],
        "fitness": ["gym", "workout", "sports", "yoga"],
        "subscriptions": ["subscription", "membership", "recurring"],
        "housing": ["rent", "mortgage", "home"],
        "tech": ["technology", "software", "hardware", "electronics", "computer", "phone"],
        "clothing": ["clothes", "apparel", "shoes"],
        "personal": ["grooming", "haircut", "beauty"],
    }
    for canonical, variants in aliases.items():
        if cat in variants:
            return canonical
    return "other"


# ── Singleton ────────────────────────────────────────────────────────

_instance: Optional[FinanceTracker] = None
_instance_lock = threading.Lock()


def get_tracker() -> FinanceTracker:
    """Get or create the singleton FinanceTracker."""
    global _instance
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is not None:
            return _instance
        _instance = FinanceTracker()
        return _instance


def _reset_for_testing() -> None:
    """Clear the singleton for test isolation."""
    global _instance
    with _instance_lock:
        _instance = None
