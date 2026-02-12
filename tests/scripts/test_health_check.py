"""
Test health check system - comprehensive system status.
"""

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

# Load .env so GROK_API_KEY is available (same as other scripts)
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env", override=True)
except ImportError:
    pass

import src.core.cuda_bootstrap  # noqa: F401 - CUDA path
import logging
from src.monitoring.health_check import (
    health_check,
    STATUS_HEALTHY,
    STATUS_DEGRADED,
    STATUS_UNHEALTHY,
)

logging.basicConfig(level=logging.INFO)

print("Health Check System Test")
print("=" * 60)

# Run full health check
print("\nRunning health checks...")
print("-" * 60)

result = health_check.check_all()

print(f"\nOverall Status: {result['overall_status'].upper()}")
print(f"Timestamp: {result['timestamp']}")
print(f"\nSummary: {result['summary']}")

print("\n" + "=" * 60)
print("Component Health:")
print("=" * 60)

for component, check in result["checks"].items():
    status = check.get("status", "unknown")

    if status == STATUS_HEALTHY:
        status_display = f"[OK] {status.upper()}"
    elif status == STATUS_DEGRADED:
        status_display = f"[WARN] {status.upper()}"
    elif status == STATUS_UNHEALTHY:
        status_display = f"[FAIL] {status.upper()}"
    else:
        status_display = f"[?] {status.upper()}"

    print(f"\n{component.upper()}: {status_display}")

    if component == "system":
        print(f"  CPU: {check.get('cpu_percent', 0):.1f}%")
        print(f"  Memory: {check.get('memory_percent', 0):.1f}%")
        print(f"  Disk: {check.get('disk_percent', 0):.1f}%")

    elif component == "models":
        print(f"  Local: {'Available' if check.get('local_available') else 'Unavailable'}")
        print(f"  Grok: {'Available' if check.get('grok_available') else 'Not configured'}")

    elif component == "cache":
        print(f"  Hit rate: {check.get('hit_rate', 0):.1f}%")
        print(f"  Size: {check.get('size', 0)}/{check.get('max_size', 0)}")

    elif component == "monitoring":
        print(f"  Budget: {'OK' if check.get('budget_allowed') else 'Exceeded'}")
        print(f"  Daily usage: {check.get('daily_budget_pct', 0):.1f}%")

    if check.get("issues"):
        print("  Issues:")
        for issue in check.get("issues", []):
            print(f"    - {issue}")

    if "error" in check:
        print(f"  Error: {check['error']}")

print("\n" + "=" * 60)

if result["overall_status"] == STATUS_HEALTHY:
    print("[OK] System healthy!")
elif result["overall_status"] == STATUS_DEGRADED:
    print("[WARNING] System degraded but operational")
else:
    print("[ERROR] System unhealthy - attention needed")
