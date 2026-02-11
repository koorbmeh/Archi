"""
Cost Tracker - Monitor and control API usage costs.

Tracks token usage, API calls, and enforces budget limits
to prevent unexpected expenses. Complements ModelRouter's
in-session total_cost with persistent storage and budgets.
Integrates with rules.yaml budget_hard_stop.
"""

import json
import logging
import os
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_budget_limit_from_rules() -> float:
    """
    Load budget_hard_stop value from config/rules.yaml.
    Returns the daily limit in USD, or 5.0 if not found.
    """
    base = os.environ.get("ARCHI_ROOT")
    if not base:
        cur = Path(__file__).resolve().parent
        for _ in range(5):
            if (cur / "config").is_dir():
                base = str(cur)
                break
            cur = cur.parent
        if not base:
            base = os.getcwd()

    rules_path = os.path.join(base, "config", "rules.yaml")
    try:
        import yaml

        with open(rules_path, encoding="utf-8") as f:
            rules = yaml.safe_load(f) or {}
        for rule in rules.get("non_override_rules", []):
            if rule.get("name") == "budget_hard_stop" and rule.get("enabled", True):
                return float(rule.get("value", 5.0))
    except Exception as e:
        logger.debug("Could not load budget from rules: %s", e)
    return 5.0

# Match GrokClient pricing: $0.20/1M input, $1.00/1M output
DEFAULT_GROK_INPUT_PER_1M = 0.20
DEFAULT_GROK_OUTPUT_PER_1M = 1.00


def _default_usage() -> Dict[str, Any]:
    return {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }


class CostTracker:
    """
    Track API costs and enforce budget limits.

    Monitors:
    - Token usage (input/output)
    - API calls by provider
    - Costs by model
    - Budget consumption (daily/monthly)
    """

    PRICING: Dict[str, Dict[str, float]] = {
        "grok": {
            "input": DEFAULT_GROK_INPUT_PER_1M,
            "output": DEFAULT_GROK_OUTPUT_PER_1M,
        },
        "local": {"input": 0.0, "output": 0.0},
    }

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        daily_budget_usd: float = 10.0,
        monthly_budget_usd: float = 100.0,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else Path("data")
        self.data_dir.mkdir(exist_ok=True)

        self.daily_budget = daily_budget_usd
        self.monthly_budget = monthly_budget_usd

        self.usage: Dict[str, Dict[str, Any]] = defaultdict(_default_usage)
        self.daily_usage: Dict[str, float] = {}
        self.monthly_usage: Dict[str, float] = {}

        self._lock = threading.Lock()

        self._load_usage()

        logger.info(
            "Cost tracker initialized (daily: $%.2f, monthly: $%.2f)",
            daily_budget_usd,
            monthly_budget_usd,
        )

    def record_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Optional[float] = None,
    ) -> None:
        """
        Record API usage.

        Args:
            provider: 'grok', 'local', etc.
            model: Model name
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            cost_usd: Actual cost (if known), otherwise calculated from tokens
        """
        with self._lock:
            if cost_usd is None:
                cost_usd = self._calculate_cost(
                    provider, input_tokens, output_tokens
                )

            key = f"{provider}/{model}"
            self.usage[key]["calls"] += 1
            self.usage[key]["input_tokens"] += input_tokens
            self.usage[key]["output_tokens"] += output_tokens
            self.usage[key]["cost_usd"] += cost_usd

            today = datetime.now().date().isoformat()
            month = datetime.now().strftime("%Y-%m")

            self.daily_usage[today] = self.daily_usage.get(today, 0.0) + cost_usd
            self.monthly_usage[month] = (
                self.monthly_usage.get(month, 0.0) + cost_usd
            )

            if self.usage[key]["calls"] % 10 == 0:
                self._save_usage()

            logger.debug("Recorded: %s - $%.6f", key, cost_usd)

    def _calculate_cost(
        self, provider: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Calculate cost based on token usage."""
        if provider not in self.PRICING:
            return 0.0

        pricing = self.PRICING[provider]
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost

    def check_budget(
        self, estimated_cost: float = 0.0
    ) -> Dict[str, Any]:
        """
        Check if within budget limits.

        Args:
            estimated_cost: Cost of planned operation

        Returns:
            Dict with 'allowed', 'reason', 'remaining', etc.
        """
        with self._lock:
            today = datetime.now().date().isoformat()
            month = datetime.now().strftime("%Y-%m")

            daily_spent = self.daily_usage.get(today, 0.0)
            monthly_spent = self.monthly_usage.get(month, 0.0)

            if daily_spent + estimated_cost > self.daily_budget:
                return {
                    "allowed": False,
                    "reason": "daily_budget_exceeded",
                    "daily_spent": daily_spent,
                    "daily_limit": self.daily_budget,
                    "monthly_spent": monthly_spent,
                    "monthly_limit": self.monthly_budget,
                }

            if monthly_spent + estimated_cost > self.monthly_budget:
                return {
                    "allowed": False,
                    "reason": "monthly_budget_exceeded",
                    "daily_spent": daily_spent,
                    "daily_limit": self.daily_budget,
                    "monthly_spent": monthly_spent,
                    "monthly_limit": self.monthly_budget,
                }

            return {
                "allowed": True,
                "reason": "within_budget",
                "daily_spent": daily_spent,
                "daily_remaining": self.daily_budget - daily_spent,
                "monthly_spent": monthly_spent,
                "monthly_remaining": self.monthly_budget - monthly_spent,
            }

    def get_summary(self, period: str = "all") -> Dict[str, Any]:
        """
        Get cost summary.

        Args:
            period: 'all', 'today', 'month'

        Returns:
            Summary of costs and usage
        """
        with self._lock:
            today = datetime.now().date().isoformat()
            month = datetime.now().strftime("%Y-%m")

            if period == "today":
                total = self.daily_usage.get(today, 0.0)
                return {
                    "period": "today",
                    "date": today,
                    "total_cost": total,
                    "budget": self.daily_budget,
                    "percentage": (
                        (total / self.daily_budget * 100)
                        if self.daily_budget > 0
                        else 0
                    ),
                }

            if period == "month":
                total = self.monthly_usage.get(month, 0.0)
                return {
                    "period": "month",
                    "month": month,
                    "total_cost": total,
                    "budget": self.monthly_budget,
                    "percentage": (
                        (total / self.monthly_budget * 100)
                        if self.monthly_budget > 0
                        else 0
                    ),
                }

            total_cost = sum(v["cost_usd"] for v in self.usage.values())
            total_calls = sum(v["calls"] for v in self.usage.values())
            total_input = sum(v["input_tokens"] for v in self.usage.values())
            total_output = sum(v["output_tokens"] for v in self.usage.values())

            return {
                "period": "all_time",
                "total_cost": total_cost,
                "total_calls": total_calls,
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "by_provider": dict(self.usage),
                "today": self._get_summary_cached("today", today, month),
                "month": self._get_summary_cached("month", today, month),
            }

    def _get_summary_cached(
        self, period: str, today: str, month: str
    ) -> Dict[str, Any]:
        """Get today/month summary (caller holds lock)."""
        if period == "today":
            total = self.daily_usage.get(today, 0.0)
            return {
                "total_cost": total,
                "budget": self.daily_budget,
                "percentage": (
                    (total / self.daily_budget * 100)
                    if self.daily_budget > 0
                    else 0
                ),
            }
        total = self.monthly_usage.get(month, 0.0)
        return {
            "total_cost": total,
            "budget": self.monthly_budget,
            "percentage": (
                (total / self.monthly_budget * 100)
                if self.monthly_budget > 0
                else 0
            ),
        }

    def get_recommendations(self) -> List[str]:
        """Get cost optimization recommendations."""
        recommendations: List[str] = []

        with self._lock:
            total_cost = sum(v["cost_usd"] for v in self.usage.values())

            if total_cost == 0:
                return ["No API usage yet - costs are zero!"]

            by_cost = sorted(
                self.usage.items(),
                key=lambda x: x[1]["cost_usd"],
                reverse=True,
            )

            if by_cost:
                top_key, top_usage = by_cost[0]
                pct = (top_usage["cost_usd"] / total_cost * 100)
                if pct > 80:
                    recommendations.append(
                        f"{top_key} accounts for {pct:.0f}% of costs - "
                        "consider caching more aggressively"
                    )

            grok_cost = sum(
                v["cost_usd"]
                for k, v in self.usage.items()
                if "grok" in k.lower()
            )
            if total_cost > 0 and grok_cost / total_cost > 0.5:
                recommendations.append(
                    "Over 50% of costs are from Grok API - "
                    "consider using local model more for simple queries"
                )

            today = datetime.now().date().isoformat()
            month = datetime.now().strftime("%Y-%m")
            daily_pct = (
                (self.daily_usage.get(today, 0.0) / self.daily_budget * 100)
                if self.daily_budget > 0
                else 0
            )
            if daily_pct > 80:
                recommendations.append(
                    f"Daily budget {daily_pct:.0f}% used - "
                    "consider pausing non-essential API calls"
                )

            monthly_pct = (
                (
                    self.monthly_usage.get(month, 0.0)
                    / self.monthly_budget
                    * 100
                )
                if self.monthly_budget > 0
                else 0
            )
            if monthly_pct > 80:
                recommendations.append(
                    f"Monthly budget {monthly_pct:.0f}% used - "
                    "review usage patterns"
                )

        return recommendations or ["No optimization needed - costs are low!"]

    def _save_usage(self) -> None:
        """Save usage data to disk."""
        usage_file = self.data_dir / "cost_usage.json"
        data = {
            "usage": dict(self.usage),
            "daily_usage": self.daily_usage,
            "monthly_usage": self.monthly_usage,
            "last_updated": datetime.now().isoformat(),
        }
        try:
            with open(usage_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error("Failed to save usage data: %s", e)

    def _load_usage(self) -> None:
        """Load usage data from disk."""
        usage_file = self.data_dir / "cost_usage.json"
        if not usage_file.exists():
            return

        try:
            with open(usage_file) as f:
                data = json.load(f)

            loaded = data.get("usage", {})
            for k, v in loaded.items():
                self.usage[k] = {
                    "calls": v.get("calls", 0),
                    "input_tokens": v.get("input_tokens", 0),
                    "output_tokens": v.get("output_tokens", 0),
                    "cost_usd": v.get("cost_usd", 0.0),
                }
            self.daily_usage = data.get("daily_usage", {})
            self.monthly_usage = data.get("monthly_usage", {})

            logger.info("Loaded usage data from disk")

        except Exception as e:
            logger.error("Failed to load usage data: %s", e)


# Global instance (optional - use constructor for custom budgets)
cost_tracker: Optional[CostTracker] = None


def get_cost_tracker(
    daily_budget: Optional[float] = None,
    monthly_budget: float = 100.0,
) -> CostTracker:
    """
    Get or create global cost tracker instance.
    If daily_budget is None, uses budget_hard_stop from rules.yaml (default 5.0).
    """
    global cost_tracker
    if cost_tracker is None:
        daily = (
            daily_budget
            if daily_budget is not None
            else get_budget_limit_from_rules()
        )
        cost_tracker = CostTracker(
            daily_budget_usd=daily,
            monthly_budget_usd=monthly_budget,
        )
    return cost_tracker
