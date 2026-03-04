"""
Backward-compatibility shim — session 89 merged agent_loop into heartbeat.

The real loop lives in src.core.heartbeat.Heartbeat._monitor_loop().
Signal handling and MCP init moved to src.service.archi_service.

This module re-exports EmergencyStop and startup_recovery for any code
that still imports them from the old location.
"""

import logging

from src.core.heartbeat import EmergencyStop  # noqa: F401

logger = logging.getLogger(__name__)


def startup_recovery(goal_manager):
    """Reset stale IN_PROGRESS tasks and log goal status on startup."""
    logger.info("Running startup recovery check...")
    try:
        # Reset tasks left IN_PROGRESS from a previous crash/shutdown
        from src.core.goal_manager import TaskStatus
        reset_count = 0
        for goal in goal_manager.goals.values():
            for task in goal.tasks:
                if task.status == TaskStatus.IN_PROGRESS:
                    task.status = TaskStatus.PENDING
                    task.started_at = None
                    reset_count += 1
                    logger.info(
                        "Startup recovery: reset %s to PENDING (was IN_PROGRESS)",
                        task.task_id,
                    )
        if reset_count:
            goal_manager.save_state()
        status = goal_manager.get_status()
        active = status.get("active_goals", 0)
        pending = status.get("pending_tasks", 0)
        if active:
            logger.info("Goals: %d active, %d pending tasks", active, pending)
    except Exception as e:
        logger.warning("Startup recovery: goal status check failed: %s", e)
    logger.info("Startup recovery complete")
