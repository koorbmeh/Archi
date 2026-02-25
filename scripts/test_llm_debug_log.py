"""
Quick smoke test for the LLM debug logger in PlanExecutor.

Runs a mock PlanExecutor through 3 steps (think → create_file → done),
then reads back the JSONL debug log and prints each entry formatted.

Usage:
    python scripts/test_llm_debug_log.py

Expected: 4 JSONL entries (3 steps + 1 verify), printed with timestamps
that you can cross-reference against logs/errors/*.log.
"""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Ensure project root is on path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Force debug logging on
os.environ["LLM_DEBUG_LOG"] = "1"


def make_mock_response(text, cost=0.001):
    return {"text": text, "cost_usd": cost}


def run_test():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    debug_dir = os.path.join(ROOT, "logs", "llm_debug")
    debug_file = os.path.join(debug_dir, f"{today}.jsonl")

    # Clean up any prior run from today (only the test entries — we append,
    # so just note the starting line count)
    start_lines = 0
    if os.path.isfile(debug_file):
        with open(debug_file, "r") as f:
            start_lines = sum(1 for _ in f)

    # Build mock responses: think → create_file → done
    responses = [
        make_mock_response(json.dumps({
            "action": "think",
            "note": "I should create a test file with the research results.",
        })),
        make_mock_response(json.dumps({
            "action": "create_file",
            "path": "workspace/projects/Test/output.md",
            "content": "# Test Output\n\nThis is a test file.",
        })),
        make_mock_response(json.dumps({
            "action": "done",
            "summary": "Created test output file.",
            "confidence": "high",
        })),
        # Verification response
        make_mock_response(json.dumps({
            "quality": 8,
            "issues": "none",
            "strengths": "Clear and complete",
        })),
    ]

    mock_router = MagicMock()
    mock_router.generate = MagicMock(side_effect=responses)

    mock_tools = MagicMock()
    mock_tools.execute = MagicMock(return_value={
        "success": True, "path": os.path.join(ROOT, "workspace", "projects", "Test", "output.md"),
    })

    with patch("src.core.plan_executor.executor.save_state"), \
         patch("src.core.plan_executor.executor.load_state", return_value=None), \
         patch("src.core.plan_executor.executor.clear_state"), \
         patch("src.core.plan_executor.executor.check_and_clear_cancellation", return_value=None):

        from src.core.plan_executor.executor import PlanExecutor
        pe = PlanExecutor(router=mock_router, tools=mock_tools)
        result = pe.execute(
            task_description="Test task for debug log verification",
            goal_context="Verify LLM debug logging works",
            max_steps=10,
            task_id="test_debug_log_001",
        )

    # Read back the debug log
    print(f"\n{'=' * 70}")
    print(f"PlanExecutor result: success={result['success']}, "
          f"steps={result['total_steps']}, cost=${result['total_cost']:.4f}")
    print(f"{'=' * 70}")

    if not os.path.isfile(debug_file):
        print(f"\nFAIL: Debug log not created at {debug_file}")
        return False

    with open(debug_file, "r") as f:
        all_lines = f.readlines()

    new_lines = all_lines[start_lines:]
    if not new_lines:
        print(f"\nFAIL: No new entries in {debug_file}")
        return False

    print(f"\nDebug log: {debug_file}")
    print(f"New entries: {len(new_lines)}\n")

    for i, line in enumerate(new_lines):
        entry = json.loads(line)
        ts = entry.get("ts", "?")
        role = entry.get("role", "?")
        step = entry.get("step", "?")
        action = entry.get("parsed_action", "?")
        cost = entry.get("cost_usd", 0)
        raw_len = len(entry.get("raw_text", ""))
        prompt_tail = entry.get("prompt_tail", "")[:80]

        print(f"  [{i+1}] {ts}  step={step}  role={role:12s}  "
              f"action={action:15s}  cost=${cost:.4f}  raw={raw_len} chars")
        if role == "verify":
            parsed = entry.get("parsed", {})
            print(f"       quality={parsed.get('quality', '?')}/10  "
                  f"issues={parsed.get('issues', '?')}")
        print(f"       prompt_tail: {prompt_tail}...")

    print(f"\n{'=' * 70}")
    print("PASS: All debug log entries written and readable.")
    print(f"Cross-reference with: logs/errors/{today}.log")
    print(f"{'=' * 70}\n")
    return True


if __name__ == "__main__":
    ok = run_test()
    sys.exit(0 if ok else 1)
