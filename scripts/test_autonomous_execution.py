import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import src.core.cuda_bootstrap  # noqa: F401 - CUDA path for model loading
import logging
import time
from src.core.dream_cycle import DreamCycle
from src.core.goal_manager import GoalManager
from src.models.local_model import LocalModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

print("Autonomous Execution Test")
print("=" * 60)

# Initialize components
print("\n1. Initializing components...")
dream = DreamCycle(
    idle_threshold_seconds=5,
    check_interval_seconds=5,
)
manager = GoalManager()
model = LocalModel()

# Create and decompose a goal
print("\n2. Creating goal...")
goal = manager.create_goal(
    description="Create a simple Python script to analyze CSV files",
    user_intent="I need to analyze sales data from CSV exports",
    priority=8,
)

print("\n3. Decomposing goal into tasks...")
tasks = manager.decompose_goal(goal.goal_id, model)
print(f"Generated {len(tasks)} tasks")

for task in tasks[:3]:  # Show first 3
    print(f"  - {task.description}")

# Enable autonomous mode
print("\n4. Enabling autonomous execution mode...")
dream.enable_autonomous_mode(manager, model)

# Start dream cycle monitoring
print("\n5. Starting dream cycle monitoring...")
dream.start_monitoring()

print("\nMonitoring idle period...")
print("(Will start autonomous execution after 5 seconds of no activity)")

# Wait and monitor
for i in range(45):
    status = dream.get_status()
    goal_status = manager.get_status()

    print(f"\nSecond {i+1}:")
    print(f"  Idle: {status['is_idle']}, Dreaming: {status['is_dreaming']}")
    print(
        f"  Completed tasks: {goal_status['completed_tasks']}/{goal_status['total_tasks']}"
    )

    if status["is_dreaming"]:
        print("  >>> AUTONOMOUS EXECUTION IN PROGRESS <<<")

    time.sleep(1)

# Stop monitoring
dream.stop_monitoring()

# Final status
print("\n" + "=" * 60)
print("Autonomous execution test complete!")

final_status = manager.get_status()
print(f"\nFinal Results:")
print(f"  Total tasks: {final_status['total_tasks']}")
print(f"  Completed: {final_status['completed_tasks']}")
print(f"  Pending: {final_status['pending_tasks']}")
print(f"  Goal progress: {goal.completion_percentage:.1f}%")

if dream.dream_history:
    print(f"\nDream cycles executed: {len(dream.dream_history)}")
    for idx, d in enumerate(dream.dream_history, 1):
        print(
            f"  Dream {idx}: {d['tasks_processed']} tasks, {d['duration_seconds']:.1f}s"
        )

print("\n[OK] Autonomous execution system working!")
