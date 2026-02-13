# Archi API Reference

Technical reference for Archi's internal APIs.

## Core Components

### Agent Loop (`src/core/agent_loop.py`)

Main execution loop with heartbeat, safety checks, and goal processing.

```python
from src.core.agent_loop import run_agent_loop, main

# Run agent loop (blocks until Ctrl+C)
run_agent_loop()

# Or use main() for full setup with logging
main()
```

### Goal Manager (`src/core/goal_manager.py`)

Goal decomposition and task management.

```python
from src.core.goal_manager import GoalManager

manager = GoalManager()

# Create goal
goal = manager.create_goal(
    description="Create Python script",
    user_intent="Need to process CSV files",
    priority=7
)

# Decompose into tasks (uses AI)
tasks = manager.decompose_goal(goal.goal_id, model)

# Get next task
next_task = manager.get_next_task()

# Complete task
manager.complete_task(task.task_id, result={'status': 'success'})

# Check status
status = manager.get_status()

# Save state
manager.save_state()
```

### Dream Cycle (`src/core/dream_cycle.py`)

Autonomous background processing.

```python
from src.core.dream_cycle import DreamCycle

dream = DreamCycle(idle_threshold_seconds=300)

# Start monitoring
dream.start_monitoring()

# Enable autonomous mode
dream.enable_autonomous_mode(goal_manager, model)

# Get status
status = dream.get_status()

# Stop
dream.stop_monitoring()
```

## Monitoring

### Cost Tracker (`src/monitoring/cost_tracker.py`)

Track API costs and enforce budgets.

```python
from src.monitoring.cost_tracker import get_cost_tracker

tracker = get_cost_tracker()

# Record usage
tracker.record_usage(
    provider='openrouter',
    model='x-ai/grok-4.1-fast',
    input_tokens=1000,
    output_tokens=500
)

# Check budget
budget = tracker.check_budget(estimated_cost=0.01)
if not budget['allowed']:
    print(f"Budget exceeded: {budget['reason']}")

# Get summary
summary = tracker.get_summary('today')
recommendations = tracker.get_recommendations()
```

### Health Check (`src/monitoring/health_check.py`)

System health monitoring.

```python
from src.monitoring.health_check import health_check

# Run all checks
health = health_check.check_all()

print(f"Status: {health['overall_status']}")
print(f"Summary: {health['summary']}")

# Check specific component
system_health = health['checks']['system']
print(f"CPU: {system_health['cpu_percent']}%")
```

### Performance Monitor (`src/monitoring/performance_monitor.py`)

Operation timing and metrics.

```python
from src.monitoring.performance_monitor import performance_monitor

# Time operation
with performance_monitor.time_operation('my_function'):
    # Your code here
    pass

# Get stats
stats = performance_monitor.get_stats('my_function')
print(f"Average: {stats['avg_ms']}ms")
print(f"P95: {stats['p95_ms']}ms")
```

## Web Dashboard

### Routes

**Read:**
- `GET /api/health` - System health status
- `GET /api/costs` - Cost information
- `GET /api/performance` - Performance metrics
- `GET /api/goals` - Goals and tasks
- `GET /api/dream` - Dream cycle status

**Control:**
- `POST /api/goals/create` - Create new goal
  ```json
  {
    "description": "Goal description",
    "user_intent": "Why user wants this",
    "priority": 5
  }
  ```

---

For complete examples, see `scripts/` directory and `scripts/README.md`.
