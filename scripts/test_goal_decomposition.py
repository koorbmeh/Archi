import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import src.core.cuda_bootstrap  # noqa: F401 - CUDA path for model loading
import logging
from src.core.goal_manager import GoalManager
from src.models.local_model import LocalModel

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(message)s"
)

print("Goal Decomposition Test")
print("=" * 60)

# Initialize
manager = GoalManager()
model = LocalModel()

# Create a goal
print("\n1. Creating goal...")
goal = manager.create_goal(
    description="Build a personal budget tracking system",
    user_intent="I want to track my spending and save money",
    priority=8,
)

print(f"Goal created: {goal.goal_id}")
print(f"Description: {goal.description}")

# Decompose into tasks
print("\n2. Decomposing goal into tasks...")
tasks = manager.decompose_goal(goal.goal_id, model)

print(f"\nGenerated {len(tasks)} tasks:")
for task in tasks:
    deps = (
        f" (depends on: {', '.join(task.dependencies)})"
        if task.dependencies
        else ""
    )
    print(f"  - {task.task_id}: {task.description}{deps}")
    print(
        f"    Estimated: {task.estimated_duration_minutes} min, Priority: {task.priority}"
    )

# Get next task to work on
print("\n3. Getting next task to work on...")
next_task = manager.get_next_task()

if next_task:
    print(f"Next task: {next_task.task_id}")
    print(f"Description: {next_task.description}")

    # Simulate completing it
    print("\n4. Simulating task execution...")
    manager.start_task(next_task.task_id)
    print(f"Task started: {next_task.task_id}")

    manager.complete_task(next_task.task_id, {"status": "success"})
    print(f"Task completed: {next_task.task_id}")

    # Check progress
    print(f"\nGoal progress: {goal.completion_percentage:.1f}%")

# Get overall status
print("\n5. Overall status:")
status = manager.get_status()
print(f"Total goals: {status['total_goals']}")
print(f"Total tasks: {status['total_tasks']}")
print(f"Completed: {status['completed_tasks']}")
print(f"Pending: {status['pending_tasks']}")

# Save state
manager.save_state()
print("\n[OK] Goal state saved to data/goals_state.json")
