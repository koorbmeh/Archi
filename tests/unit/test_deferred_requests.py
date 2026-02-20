"""Tests for deferred request detection and handling.

Covers:
- _is_deferred_request() pattern matching (true positives and negatives)
- Goal creation with correct user_intent tag
- Priority sorting bias for user-requested goals
- User goal progress reporting helper
"""

import pytest
from unittest.mock import MagicMock, patch

# ---- Deferred request detection ----

from src.interfaces.intent_classifier import _is_deferred_request


class TestDeferredRequestDetection:
    """Test _is_deferred_request() pattern matching."""

    # True positives — should detect and extract task
    @pytest.mark.parametrize("msg,expected_contains", [
        # Deferred signal + action verb
        ("When you have time, look into protein powder brands",
         "look into protein powder brands"),
        ("Can you research lithium orotate dosing when you get a chance?",
         "research lithium orotate dosing"),
        ("Check on the server performance when you're free",
         "check on the server performance"),
        ("Look into the best thermal paste options, no rush",
         "look into the best thermal paste options"),
        ("When you can, investigate why the dream cycle cost went up",
         "investigate why the dream cycle cost went up"),
        ("Explore new supplement research when you have time",
         "explore new supplement research"),
        # Reminder starts
        ("Remind me to check the server logs",
         "check the server logs"),
        ("Don't forget to review the budget settings",
         "review the budget settings"),
        ("Remember to look at the new health data",
         "look at the new health data"),
        # "Later" signals
        ("Research creatine timing later",
         "research creatine timing"),
        ("Look into magnesium glycinate eventually",
         "look into magnesium glycinate"),
        # Mixed signals
        ("Hey Archi, when you get a chance can you look into sleep optimization?",
         "look into sleep optimization"),
        ("If you get a chance, check out the latest longevity research",
         "check out the latest longevity research"),
    ])
    def test_true_positives(self, msg, expected_contains):
        result = _is_deferred_request(msg)
        assert result is not None, f"Should detect deferred request in: {msg}"
        assert expected_contains.lower() in result.lower(), (
            f"Expected '{expected_contains}' in result '{result}'"
        )

    # True negatives — should NOT detect as deferred
    @pytest.mark.parametrize("msg", [
        # Direct requests (no deferral signal)
        "Look into protein powder brands",
        "Research the best supplements",
        "What time is it?",
        "Hello",
        "How are you?",
        # Too short
        "remind me",
        "later",
        # Too long (>500 chars)
        "When you have time, " + "x" * 500,
        # Empty / None
        "",
        # Regular conversation with "later" as part of a sentence
        "I'll talk to you later",
        "See you later",
        # Questions about reminders (not actual requests)
        "Can you set reminders?",
    ])
    def test_true_negatives(self, msg):
        result = _is_deferred_request(msg)
        assert result is None, f"Should NOT detect deferred request in: {msg}"

    def test_none_input(self):
        assert _is_deferred_request(None) is None

    def test_extracts_clean_description(self):
        """Task description should not include the deferral signal itself."""
        result = _is_deferred_request(
            "When you have time, look into magnesium supplements"
        )
        assert result is not None
        assert "when you have time" not in result.lower()

    def test_strips_trailing_punctuation(self):
        result = _is_deferred_request(
            "Remind me to check the logs!"
        )
        assert result is not None
        assert not result.endswith("!")

    def test_minimum_length_for_task(self):
        """Very short extracted tasks should be rejected."""
        result = _is_deferred_request("Remind me to X")
        assert result is None  # "X" is too short to be a meaningful task


# ---- Intent classifier integration ----

from src.interfaces.intent_classifier import classify, IntentResult


class TestDeferredRequestClassify:
    """Test that deferred requests route through classify() correctly."""

    def test_deferred_request_returns_correct_action(self):
        """classify() should return action='deferred_request' for deferred messages."""
        result = classify(
            message="When you have time, research the best nootropics",
            effective_message="When you have time, research the best nootropics",
            router=MagicMock(),
            history_messages=[],
            system_prompt="test",
            goal_manager=None,
        )
        assert result.action == "deferred_request"
        assert result.fast_path is True
        assert result.cost == 0.0
        assert "research" in result.params.get("description", "").lower()

    def test_greeting_with_deferred_not_hijacked(self):
        """A greeting followed by deferred request should detect the deferred part."""
        # This tests that action keywords in deferred requests prevent
        # the greeting fast-path from firing
        msg = "Hey Archi, when you have time can you look into sleep optimization?"
        result = classify(
            message=msg, effective_message=msg,
            router=MagicMock(), history_messages=[],
            system_prompt="test", goal_manager=None,
        )
        # Should NOT be greeting (has action keywords)
        # Should be deferred_request
        assert result.action == "deferred_request"


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
