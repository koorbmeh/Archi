#!/usr/bin/env python3
"""
Test harness — run test prompts through the v2 message pipeline
without needing Discord. Same codepath as discord_bot.py uses.

Usage:
    python test_harness.py              # Run all tests
    python test_harness.py --quick      # Quick smoke test (5 prompts)
    python test_harness.py --category model   # Run only 'model/*' tests
    python test_harness.py --dry-run    # Show tests without running
"""
import argparse
import os
import sys
import json
import time
import logging

# Project root
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
for name in ("urllib3", "httpcore", "httpx", "sentence_transformers", "huggingface_hub"):
    logging.getLogger(name).setLevel(logging.WARNING)

from src.interfaces.message_handler import process_message
from src.models.router import ModelRouter
from src.core.goal_manager import GoalManager

# ---- Test definitions ----
# (category, prompt, expected_description, validator_func_name)

ALL_TESTS = [
    # Fast-path: datetime (zero cost, instant)
    ("fast-path/datetime", "what time is it",
     "instant clock response, $0", "check_fast_path"),
    ("fast-path/datetime", "what's the date",
     "instant date response, $0", "check_fast_path"),

    # Fast-path: slash commands
    ("fast-path/slash", "/help",
     "help text", "check_has_response"),
    ("fast-path/slash", "/goals",
     "goals list or 'no active goals'", "check_has_response"),
    ("fast-path/slash", "/cost",
     "cost summary", "check_has_response"),

    # Fast-path: greeting
    ("fast-path/greeting", "hey",
     "greeting response", "check_has_response"),

    # Model classification: chat
    ("model/chat", "tell me a joke",
     "conversational response from Grok", "check_model_call"),

    # Model classification: search
    ("model/search", "search for the current price of silver per ounce",
     "web search + summary", "check_model_call"),

    # Greeting + substance (should NOT fast-path — must reach model)
    ("model/greeting-passthrough", "hey can you search for the price of gold",
     "should reach model, not greeting fast-path", "check_model_call"),

    # Goal creation via slash command
    ("fast-path/goal", "/goal test goal from harness - delete me",
     "goal confirmed created", "check_has_response"),

    # Conversation context: setup
    ("context/setup", "my name is TestBot and I like running tests",
     "acknowledges name", "check_model_call"),

    # File operations
    ("model/file-ops", "read the file config/archi_identity.yaml",
     "file content or graceful 'not found'", "check_has_response"),

    # Conversation context: recall (must come after context/setup)
    ("context/recall", "what did I just tell you about myself?",
     "references TestBot and running tests", "check_context_recall"),
]

QUICK_TESTS = [
    "what time is it",
    "search for the current price of gold",
    "/goals",
    "tell me a joke",
    "hey can you search for the price of silver",
]


# ---- Validators ----

def check_fast_path(response, cost, elapsed, **_):
    """Fast-path: must be free and instant."""
    if cost > 0.0001:
        return False, f"should be free but cost ${cost:.4f}"
    if elapsed > 2.0:
        return False, f"too slow for fast-path: {elapsed:.1f}s"
    if not response or len(response.strip()) < 3:
        return False, "empty response"
    return True, ""


def check_has_response(response, **_):
    """Just needs a non-empty response."""
    if not response or len(response.strip()) < 3:
        return False, "empty or too-short response"
    return True, ""


def check_model_call(response, cost, **_):
    """Must have hit the model (non-zero cost) and returned something."""
    if not response or len(response.strip()) < 3:
        return False, "empty response"
    if cost < 0.0001:
        return False, f"expected model call but cost was ${cost:.6f}"
    return True, ""


def check_context_recall(response, **_):
    """Should reference TestBot or tests from prior context."""
    low = response.lower()
    if "testbot" in low or "test" in low or "running" in low:
        return True, ""
    return False, "did not reference TestBot or test context"


VALIDATORS = {
    "check_fast_path": check_fast_path,
    "check_has_response": check_has_response,
    "check_model_call": check_model_call,
    "check_context_recall": check_context_recall,
}


# ---- Runner ----

def run_tests(tests, quick=False):
    router = ModelRouter()
    goal_manager = GoalManager(data_dir=os.path.join(ROOT, "data"))

    results = []
    history = []
    total_cost = 0.0
    created_test_goals = []

    print("\n" + "=" * 70)
    print("ARCHI V2 TEST HARNESS" + ("  [QUICK MODE]" if quick else ""))
    print("=" * 70)

    for i, (category, prompt, expected, validator_name) in enumerate(tests, 1):
        print(f"\n--- Test {i}/{len(tests)}: [{category}] ---")
        print(f"  Prompt: {prompt}")
        print(f"  Expected: {expected}")

        start = time.time()
        try:
            response, actions, cost = process_message(
                message=prompt,
                router=router,
                history=history[-10:] if history else None,
                source="test_harness",
                goal_manager=goal_manager,
            )
            elapsed = time.time() - start
            total_cost += cost

            display_resp = response[:300] + "..." if len(response) > 300 else response
            print(f"  Response: {display_resp}")
            print(f"  Cost: ${cost:.4f} | Time: {elapsed:.1f}s | Actions: {len(actions)}")

            # Run validator
            validator = VALIDATORS[validator_name]
            passed, fail_reason = validator(
                response=response, cost=cost, elapsed=elapsed, actions=actions,
            )

            status = "PASS" if passed else f"FAIL: {fail_reason}"
            print(f"  Status: {status}")

            results.append({
                "test": i,
                "category": category,
                "prompt": prompt,
                "status": "pass" if passed else "fail",
                "fail_reason": fail_reason,
                "response_preview": display_resp,
                "cost": cost,
                "elapsed": round(elapsed, 2),
            })

            # Track test goals for cleanup
            if "test goal from harness" in prompt:
                created_test_goals.append("test goal from harness")

            # Build conversation history for context tests
            history.append({"role": "user", "content": prompt})
            history.append({"role": "assistant", "content": response})

        except Exception as e:
            elapsed = time.time() - start
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "test": i,
                "category": category,
                "prompt": prompt,
                "status": "error",
                "fail_reason": str(e),
                "elapsed": round(elapsed, 2),
            })

    # ---- Cleanup test goals ----
    if created_test_goals:
        try:
            goals_path = os.path.join(ROOT, "data", "goals_state.json")
            with open(goals_path) as f:
                data = json.load(f)
            before = len(data.get("goals", []))
            data["goals"] = [
                g for g in data.get("goals", [])
                if "test goal from harness" not in g.get("description", "")
            ]
            removed = before - len(data["goals"])
            with open(goals_path, "w") as f:
                json.dump(data, f, indent=2)
            print(f"\n  Cleaned up {removed} test goal(s)")
        except Exception as e:
            print(f"\n  Warning: could not clean test goals: {e}")

    # ---- Summary ----
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    errors = sum(1 for r in results if r["status"] == "error")
    total = len(results)

    print(f"  Total: {total} | Passed: {passed} | Failed: {failed} | Errors: {errors}")
    print(f"  Total cost: ${total_cost:.4f}")

    if failed + errors > 0:
        print("\n  FAILURES:")
        for r in results:
            if r["status"] != "pass":
                print(f"    [{r['category']}] {r['prompt']}")
                print(f"      -> {r['status'].upper()}: {r['fail_reason']}")

    # Save results
    results_path = os.path.join(ROOT, "test_results.json")
    with open(results_path, "w") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "quick" if quick else "full",
            "results": results,
            "total_cost": total_cost,
            "summary": {
                "total": total, "passed": passed, "failed": failed, "errors": errors,
            },
        }, f, indent=2)
    print(f"\n  Results saved to: {results_path}")

    return 0 if (failed + errors) == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="Archi V2 Test Harness")
    parser.add_argument("--quick", action="store_true",
                        help="Run only the 5 quick smoke tests")
    parser.add_argument("--category", type=str, default=None,
                        help="Filter tests by category prefix (e.g. 'model', 'fast-path')")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show tests without running them")
    args = parser.parse_args()

    if args.quick:
        tests = [t for t in ALL_TESTS if t[1] in QUICK_TESTS]
    elif args.category:
        tests = [t for t in ALL_TESTS if t[0].startswith(args.category)]
    else:
        tests = ALL_TESTS

    if args.dry_run:
        print(f"\n{len(tests)} tests would run:\n")
        for i, (cat, prompt, expected, _) in enumerate(tests, 1):
            print(f"  {i}. [{cat}] {prompt}")
            print(f"     Expected: {expected}")
        return 0

    return run_tests(tests, quick=args.quick)


if __name__ == "__main__":
    sys.exit(main())
