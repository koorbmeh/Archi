import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

import logging
import time
from src.core.dream_cycle import DreamCycle

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(message)s"
)

print("Dream Cycle Test")
print("=" * 60)

# Create dream cycle with short idle threshold for testing (10 seconds)
dream = DreamCycle(idle_threshold_seconds=10)

# Queue some test tasks
dream.queue_task(
    {
        "type": "research",
        "description": "Research latest Python best practices",
        "priority": 5,
    }
)

dream.queue_task(
    {
        "type": "optimization",
        "description": "Review code for performance improvements",
        "priority": 3,
    }
)

# Start monitoring
dream.start_monitoring()

print("\nDream cycle monitoring started")
print("Idle threshold: 10 seconds")
print(f"Queued tasks: {len(dream.task_queue)}")
print("\nWaiting for idle period...")
print("(System will start dreaming after 10 seconds of no activity)")

# Simulate: wait, let it dream, then interrupt with activity
for i in range(30):
    status = dream.get_status()
    print(f"\nSecond {i+1}:")
    print(f"  Idle: {status['is_idle']}, Dreaming: {status['is_dreaming']}")
    print(f"  Idle time: {status['idle_seconds']:.1f}s")
    print(f"  Queued tasks: {status['queued_tasks']}")

    # Simulate user activity at 20 seconds (interrupt dream)
    if i == 19:
        print("\n>>> SIMULATING USER ACTIVITY <<<")
        dream.mark_activity()

    time.sleep(1)

# Stop monitoring
dream.stop_monitoring()

print("\n" + "=" * 60)
print("Dream cycle test complete!")
print(f"Total dream cycles: {len(dream.dream_history)}")

if dream.dream_history:
    print("\nDream history:")
    for idx, d in enumerate(dream.dream_history, 1):
        print(
            f"  Dream {idx}: {d['duration_seconds']:.1f}s, "
            f"{d['tasks_processed']} tasks, "
            f"interrupted: {d['interrupted']}"
        )
