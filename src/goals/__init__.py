"""Goal management — redirects to src.core.goal_manager.

The old SQLite-based GoalManager (src/goals/goal_manager.py) has been
superseded by the AI-powered GoalManager in src/core/goal_manager.py.
This package re-exports the core version for backward compatibility.
"""

from src.core.goal_manager import Goal, GoalManager

__all__ = ["Goal", "GoalManager"]
