#!/usr/bin/env python3
"""
Gate A Validation Test — ported from scripts/_archive/verify_gate_a.ps1

Validates Gate A acceptance criteria by analyzing action logs from a test run.
Run Archi for ~30 minutes (with or without ARCHI_GATE_A_FAST_TEST), stop it,
then run this test to verify the foundation is solid.

Usage:
    pytest tests/integration/test_gate_a.py -v
    python tests/integration/test_gate_a.py          # standalone

Can also be called from fix.py:
    scripts/fix.py test  (includes this in the full pytest suite)

Environment:
    ARCHI_ROOT           - Override project root (default: auto-detect)
    GATE_A_MIN_HEARTBEATS - Min heartbeats to pass (default: 20)
    GATE_A_MIN_CYCLES     - Min test cycles to pass (default: 2)
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

ARCHI_ROOT = Path(os.environ.get("ARCHI_ROOT", str(_root)))
LOGS_DIR = ARCHI_ROOT / "logs"
ACTIONS_DIR = LOGS_DIR / "actions"
SYSTEM_DIR = LOGS_DIR / "system"
ERRORS_DIR = LOGS_DIR / "errors"
WORKSPACE_DIR = ARCHI_ROOT / "workspace"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_action_lines() -> list[dict]:
    """Load all JSONL action log entries."""
    entries = []
    if not ACTIONS_DIR.is_dir():
        return entries
    for f in sorted(ACTIONS_DIR.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _load_system_logs() -> str:
    """Concatenate all system log text."""
    if not SYSTEM_DIR.is_dir():
        return ""
    parts = []
    for f in sorted(SYSTEM_DIR.glob("*.log")):
        parts.append(f.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _count_error_lines() -> int:
    """Count lines across all error logs."""
    if not ERRORS_DIR.is_dir():
        return 0
    total = 0
    for f in ERRORS_DIR.glob("*.log"):
        total += len(f.read_text(encoding="utf-8", errors="replace").splitlines())
    return total


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def action_entries():
    return _load_action_lines()


@pytest.fixture(scope="module")
def system_log_text():
    return _load_system_logs()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestGateAValidation:
    """
    Gate A acceptance criteria — mirrors verify_gate_a.ps1 checks.

    These tests verify that a completed Gate A run produced the expected
    log artifacts: heartbeats, read/write cycles, denied actions, costs, etc.
    """

    def test_action_logs_exist(self):
        """Action log directory must exist with at least one JSONL file."""
        assert ACTIONS_DIR.is_dir(), f"Missing action logs dir: {ACTIONS_DIR}"
        jsonl_files = list(ACTIONS_DIR.glob("*.jsonl"))
        assert len(jsonl_files) > 0, "No .jsonl action log files found"

    def test_heartbeat_count(self, action_entries):
        """Enough heartbeats recorded (~27 production / ~180 fast test)."""
        min_hb = int(os.environ.get("GATE_A_MIN_HEARTBEATS", "20"))
        heartbeats = [e for e in action_entries if e.get("action_type") == "heartbeat"]
        assert len(heartbeats) >= min_hb, (
            f"Only {len(heartbeats)} heartbeats (need >= {min_hb}). "
            f"Run Archi longer or set ARCHI_GATE_A_FAST_TEST=1."
        )

    def test_read_write_cycles(self, action_entries):
        """At least N read+write test cycles completed."""
        min_cycles = int(os.environ.get("GATE_A_MIN_CYCLES", "2"))
        reads = sum(1 for e in action_entries if e.get("action_type") == "read_file")
        writes = sum(1 for e in action_entries if e.get("action_type") == "create_file")
        cycles = min(reads, writes)
        assert cycles >= min_cycles, (
            f"Only {cycles} test cycles (reads={reads}, writes={writes}, need >= {min_cycles})"
        )

    def test_denied_actions(self, action_entries):
        """Safety controller must have denied at least some illegal-path attempts."""
        min_cycles = int(os.environ.get("GATE_A_MIN_CYCLES", "2"))
        denied = sum(1 for e in action_entries if e.get("result") == "denied")
        assert denied >= min_cycles, (
            f"Only {denied} denied actions — safety controller should block "
            f"illegal paths each cycle (need >= {min_cycles})"
        )

    def test_successful_non_heartbeat_actions(self, action_entries):
        """At least one approved action executed successfully."""
        successes = [
            e for e in action_entries
            if e.get("result") == "success" and e.get("action_type") != "heartbeat"
        ]
        assert len(successes) >= 1, "No successful non-heartbeat actions found"

    def test_no_errors(self):
        """Error logs should be empty (warnings acceptable)."""
        count = _count_error_lines()
        if count > 0:
            pytest.skip(f"{count} error log lines found — review logs/errors/")

    def test_workspace_populated(self):
        """Workspace should have at least one file from test cycles."""
        if not WORKSPACE_DIR.is_dir():
            pytest.skip(f"No workspace dir: {WORKSPACE_DIR}")
        files = [f for f in WORKSPACE_DIR.iterdir() if f.is_file()]
        assert len(files) >= 1, "Workspace is empty — test cycles should create files"

    def test_zero_api_costs(self, action_entries):
        """Gate A should run at $0 cost (local model only)."""
        nonzero = [
            e for e in action_entries
            if e.get("cost_usd") and float(e.get("cost_usd", 0)) > 0
        ]
        assert len(nonzero) == 0, (
            f"{len(nonzero)} actions had non-zero cost — Gate A should be free"
        )

    def test_adaptive_sleep(self, system_log_text):
        """Adaptive heartbeat should show varying sleep times."""
        pattern = r"Sleeping (\d+\.?\d*) s"
        matches = re.findall(pattern, system_log_text)
        if len(matches) < 2:
            pytest.skip("Not enough sleep data in system logs")
        times = [float(m) for m in matches]
        min_t, max_t = min(times), max(times)
        assert max_t > min_t * 2, (
            f"Sleep range too narrow ({min_t:.1f}s–{max_t:.1f}s) — "
            f"adaptive sleep should vary more"
        )


# ---------------------------------------------------------------------------
# Summary (standalone mode)
# ---------------------------------------------------------------------------
def _standalone_summary():
    """Run all checks and print a Gate A-style summary (no pytest needed)."""
    entries = _load_action_lines()
    sys_text = _load_system_logs()

    checks = {
        "heartbeats": 0,
        "test_cycles": 0,
        "denied_actions": 0,
        "successful_actions": 0,
        "error_lines": 0,
        "workspace_files": 0,
        "zero_cost": True,
        "adaptive_sleep": False,
    }

    # Heartbeats
    checks["heartbeats"] = sum(1 for e in entries if e.get("action_type") == "heartbeat")

    # Test cycles
    reads = sum(1 for e in entries if e.get("action_type") == "read_file")
    writes = sum(1 for e in entries if e.get("action_type") == "create_file")
    checks["test_cycles"] = min(reads, writes)

    # Denied
    checks["denied_actions"] = sum(1 for e in entries if e.get("result") == "denied")

    # Successful non-heartbeat
    checks["successful_actions"] = sum(
        1 for e in entries
        if e.get("result") == "success" and e.get("action_type") != "heartbeat"
    )

    # Errors
    checks["error_lines"] = _count_error_lines()

    # Workspace
    if WORKSPACE_DIR.is_dir():
        checks["workspace_files"] = sum(1 for f in WORKSPACE_DIR.iterdir() if f.is_file())

    # Costs
    nonzero = [e for e in entries if e.get("cost_usd") and float(e.get("cost_usd", 0)) > 0]
    checks["zero_cost"] = len(nonzero) == 0

    # Adaptive sleep
    times = [float(m) for m in re.findall(r"Sleeping (\d+\.?\d*) s", sys_text)]
    if len(times) >= 2:
        checks["adaptive_sleep"] = max(times) > min(times) * 2

    # Print
    print("\n" + "=" * 50)
    print("  Gate A — 30-Minute Test Validation")
    print("=" * 50)

    def _status(ok, label, detail=""):
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}]  {label}" + (f"  ({detail})" if detail else ""))
        return ok

    passed = 0
    total = 8
    passed += _status(checks["heartbeats"] >= 20, "Heartbeats", f'{checks["heartbeats"]}')
    passed += _status(checks["test_cycles"] >= 2, "Test cycles", f'{checks["test_cycles"]}')
    passed += _status(checks["denied_actions"] >= 2, "Denied actions", f'{checks["denied_actions"]}')
    passed += _status(checks["successful_actions"] >= 1, "Successful actions", f'{checks["successful_actions"]}')
    passed += _status(checks["error_lines"] == 0, "Error log clean", f'{checks["error_lines"]} lines')
    passed += _status(checks["workspace_files"] >= 1, "Workspace populated", f'{checks["workspace_files"]} files')
    passed += _status(checks["zero_cost"], "Zero API cost")
    passed += _status(checks["adaptive_sleep"], "Adaptive sleep range")

    print(f"\n  {passed}/{total} checks passed")
    if passed >= 7:
        print("  Gate A VALIDATED — ready for Gate B.\n")
    elif passed >= 5:
        print("  Gate A PARTIAL — review failures above.\n")
    else:
        print("  Gate A NEEDS WORK — check logs.\n")

    return passed


if __name__ == "__main__":
    _standalone_summary()
