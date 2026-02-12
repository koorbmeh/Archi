"""
Test budget_hard_stop enforcement.

Verifies that CostTracker + rules.yaml budget limit blocks paid API calls
when daily budget is exceeded.
"""

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

import logging
from src.monitoring.cost_tracker import (
    CostTracker,
    get_budget_limit_from_rules,
)

logging.basicConfig(level=logging.INFO)

print("Budget Enforcement Test")
print("=" * 60)

# Verify rules.yaml budget is loaded
print("\n1. Loading budget limit from rules.yaml...")
limit = get_budget_limit_from_rules()
print(f"   budget_hard_stop from rules: ${limit:.2f}")

# Initialize CostTracker with LOW budget for testing
print("\n2. Initializing CostTracker with test budget ($0.01)...")
tracker = CostTracker(
    daily_budget_usd=0.01,
    monthly_budget_usd=0.10,
)

# Simulate spending close to budget
print("\n3. Simulating API usage ($0.009)...")
tracker.record_usage(
    provider="grok",
    model="grok-beta",
    input_tokens=1000,
    output_tokens=500,
    cost_usd=0.009,
)

budget = tracker.check_budget()
print(f"   Daily spent: ${budget.get('daily_spent', 0):.4f}")
print(f"   Daily limit: $0.01")
print(f"   Allowed: {budget.get('allowed', False)}")

# Test: Under budget - should be allowed
assert budget.get("allowed") is True, "Should be allowed under budget"
print("   [OK] Under budget - allowed")

# Push over budget
print("\n4. Simulating one more API call ($0.002) - pushes over budget...")
tracker.record_usage(
    provider="grok",
    model="grok-beta",
    input_tokens=500,
    output_tokens=200,
    cost_usd=0.002,
)

budget = tracker.check_budget()
print(f"   Daily spent: ${budget.get('daily_spent', 0):.4f}")
print(f"   Budget exceeded: {budget.get('daily_spent', 0) > 0.01}")

# Test: Over budget - should be blocked
assert budget.get("allowed") is False, "Should be blocked over budget"
assert budget.get("reason") == "daily_budget_exceeded", "Reason should be daily_budget_exceeded"
print("   [OK] Over budget - blocked")

# Test: Local model (free) - check_budget with 0 estimated cost
# When we're over budget, even estimate 0 might still block if we're checking
# "would we exceed" - we're already over so allowed=False. Correct.
# For "local" we wouldn't call _use_grok, so we'd never hit the budget check.
# The local path doesn't go through the budget check in Router.

# Simulate fresh tracker for "local" scenario - local doesn't incur cost
print("\n5. Local model (free) - no budget impact...")
tracker_local = CostTracker(daily_budget_usd=0.01, monthly_budget_usd=0.10)
# Record only local usage
tracker_local.record_usage(
    provider="local",
    model="qwen3vl-8b",
    input_tokens=1000,
    output_tokens=500,
)
budget_local = tracker_local.check_budget()
print(f"   After local-only usage: ${budget_local.get('daily_spent', 0):.4f}")
print(f"   Allowed: {budget_local.get('allowed', False)}")
assert budget_local.get("allowed") is True, "Local usage is free, should stay under budget"
print("   [OK] Local model usage does not affect budget")

print("\n" + "=" * 60)
print("[OK] Budget enforcement working!")
print("- budget_hard_stop loaded from rules.yaml")
print("- Grok calls blocked when over budget")
print("- Local model (free) does not affect budget")
