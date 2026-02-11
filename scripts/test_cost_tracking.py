import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import logging
from src.monitoring.cost_tracker import CostTracker

logging.basicConfig(level=logging.INFO)

print("Cost Tracking Test")
print("=" * 60)

# Initialize with test budgets
tracker = CostTracker(
    daily_budget_usd=1.0,
    monthly_budget_usd=10.0,
)

# Simulate some API usage
print("\n1. Recording API usage...")

# Local model calls (free)
for _ in range(5):
    tracker.record_usage(
        provider="local",
        model="qwen3vl-8b",
        input_tokens=500,
        output_tokens=200,
    )

# Grok API calls (paid)
for _ in range(3):
    tracker.record_usage(
        provider="grok",
        model="grok-beta",
        input_tokens=1000,
        output_tokens=500,
    )

# Check budget status
print("\n2. Checking budget...")
budget = tracker.check_budget()
print(f"Allowed: {budget['allowed']}")
print(f"Daily spent: ${budget.get('daily_spent', 0):.4f}")
print(f"Daily remaining: ${budget.get('daily_remaining', 0):.4f}")
print(f"Monthly spent: ${budget.get('monthly_spent', 0):.4f}")

# Get summary
print("\n3. Cost summary...")
summary = tracker.get_summary("all")
print(f"Total cost: ${summary['total_cost']:.4f}")
print(f"Total calls: {summary['total_calls']}")
total_tokens = (
    summary["total_input_tokens"] + summary["total_output_tokens"]
)
print(f"Total tokens: {total_tokens:,}")

print("\nBy provider:")
for provider, usage in summary["by_provider"].items():
    print(f"  {provider}:")
    print(f"    Calls: {usage['calls']}")
    print(f"    Cost: ${usage['cost_usd']:.4f}")

# Get recommendations
print("\n4. Cost optimization recommendations...")
recommendations = tracker.get_recommendations()
for i, rec in enumerate(recommendations, 1):
    print(f"  {i}. {rec}")

print("\n[OK] Cost tracking working!")
print("Usage saved to data/cost_usage.json")
