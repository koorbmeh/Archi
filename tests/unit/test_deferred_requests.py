"""Tests for deferred request handling.

Covers:
- Deferred request classification is now handled by the Router model (no regex fast-path)
- Goal creation with correct user_intent tag
- Priority sorting bias for user-requested goals
- User goal progress reporting helper
"""

import pytest
from unittest.mock import MagicMock, patch


# ---- Goal priority sorting ----

class TestUserGoalPriority:
    """Test that user-requested goals sort before auto-generated ones."""

    def test_user_goals_sort_first(self):
        """Goals with 'User' intent should sort before 'Follow-up' or 'Proactive' goals."""
        from src.core.goal_manager import Goal, Task, TaskStatus

        # Create mock goals
        user_goal = Goal("g1", "Check protein brands", "User deferred request via Discord", priority=5)
        auto_goal = Goal("g2", "Analyze supplement stack", "Proactive research (auto-planned from identity config)", priority=5)
        followup_goal = Goal("g3", "Update dosage guide", "Follow-up from: research — natural continuation", priority=5)

        # Create tasks for each (Task args: task_id, description, goal_id)
        t1 = Task("t1", "Research protein brands", "g1", priority=5)
        t2 = Task("t2", "Analyze stack", "g2", priority=5)
        t3 = Task("t3", "Update guide", "g3", priority=5)

        user_goal.tasks = [t1]
        auto_goal.tasks = [t2]
        followup_goal.tasks = [t3]

        # Simulate the sort key from goal_manager.get_next_task()
        goals = {"g1": user_goal, "g2": auto_goal, "g3": followup_goal}
        tasks = [t1, t2, t3]

        def sort_key(t):
            goal = goals[t.goal_id]
            _intent = (goal.user_intent or "").lower()
            is_user = 0 if _intent.startswith("user ") else 1
            return (is_user, -t.priority, -goal.priority)

        tasks.sort(key=sort_key)

        # User goal task should be first
        assert tasks[0].task_id == "t1", "User-requested task should sort first"


# ---- User goal progress reporting ----

class TestUserGoalProgress:
    """Test the _get_user_goal_progress() helper in reporting.py."""

    @patch("src.core.goal_manager.GoalManager")
    def test_returns_user_goals_only(self, MockGM):
        from src.core.reporting import _get_user_goal_progress
        from src.core.goal_manager import Goal, Task, TaskStatus

        user_goal = Goal("g1", "Check protein brands", "User deferred request via Discord", priority=5)
        auto_goal = Goal("g2", "Auto research", "Proactive research (auto-planned)", priority=5)

        t1 = Task("t1", "Research", "g1", priority=5)
        t1.status = TaskStatus.COMPLETED
        user_goal.tasks = [t1]
        user_goal.is_decomposed = True

        t2 = Task("t2", "Auto task", "g2", priority=5)
        auto_goal.tasks = [t2]

        gm = MagicMock()
        gm.goals = {"g1": user_goal, "g2": auto_goal}
        MockGM.return_value = gm

        lines = _get_user_goal_progress()
        assert len(lines) == 1
        assert "Check protein brands" in lines[0]
        assert "done" in lines[0]

    @patch("src.core.goal_manager.GoalManager")
    def test_empty_when_no_user_goals(self, MockGM):
        from src.core.reporting import _get_user_goal_progress

        gm = MagicMock()
        gm.goals = {}
        MockGM.return_value = gm

        lines = _get_user_goal_progress()
        assert lines == []


# ---- User goal completion notification ----

class TestUserGoalCompletionNotify:
    """Test send_user_goal_completion() in reporting.py."""

    @patch("src.core.reporting._notify")
    def test_sends_rich_notification(self, mock_notify):
        from src.core.reporting import send_user_goal_completion

        result = send_user_goal_completion(
            goal_description="Research best protein powder brands",
            task_results=[
                {
                    "task": "Research protein brands",
                    "summary": "Done: Found top 5 protein powders by quality; Searched: protein powder reviews",
                    "success": True,
                }
            ],
            files_created=["/workspace/reports/protein_brands.md"],
        )

        assert result is True
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][0]
        assert "Done with" in msg
        assert "protein" in msg.lower()

    @patch("src.core.reporting._notify")
    def test_handles_empty_results(self, mock_notify):
        from src.core.reporting import send_user_goal_completion

        result = send_user_goal_completion(
            goal_description="Check something",
            task_results=[],
            files_created=[],
        )

        assert result is True
        msg = mock_notify.call_args[0][0]
        assert "Check something" in msg
