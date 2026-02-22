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
    """Log goal status on startup."""
    logger.info("Running startup recovery check...")
    try:
        status = goal_manager.get_status()
        active = status.get("active_goals", 0)
        pending = status.get("pending_tasks", 0)
        if active:
            logger.info("Goals: %d active, %d pending tasks", active, pending)
    except Exception as e:
        logger.warning("Startup recovery: goal status check failed: %s", e)
    logger.info("Startup recovery complete")
