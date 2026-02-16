"""Unit tests for smart step estimates in PlanExecutor progress messages.

Tests _estimate_total_steps() — the heuristic that replaces the hard
step cap with a smarter estimate based on actions taken so far.
"""

import pytest

from src.core.plan_executor import _estimate_total_steps


class TestEstimateTotalSteps:
    """Tests for _estimate_total_steps()."""

    def test_too_early_returns_max(self):
        """First 2 steps should return max_steps (not enough data)."""
        steps = [{"action": "web_search"}]
        assert _estimate_total_steps(steps, 12) == 12

    def test_empty_returns_max(self):
        """No steps should return max_steps."""
        assert _estimate_total_steps([], 12) == 12

    def test_one_step_returns_max(self):
        """Single step returns max."""
        steps = [{"action": "think"}]
        assert _estimate_total_steps(steps, 15) == 15

    def test_research_phase_estimate(self):
        """During research (search/fetch), estimate ~6-8 total steps."""
        steps = [
            {"action": "web_search"},
            {"action": "fetch_webpage"},
        ]
        estimate = _estimate_total_steps(steps, 12)
        # 2 done + ~1 more research + 3 (write+verify+done) = ~6
        assert 5 <= estimate <= 8

    def test_deep_research_narrows_estimate(self):
        """After 3+ research steps, estimate shrinks (write phase imminent)."""
        steps = [
            {"action": "web_search"},
            {"action": "fetch_webpage"},
            {"action": "web_search"},
            {"action": "fetch_webpage"},
        ]
        estimate = _estimate_total_steps(steps, 12)
        # 4 done + 0 more research + 3 (write+verify+done) = ~7
        assert 6 <= estimate <= 8

    def test_writing_phase_nearly_done(self):
        """Once files are being written, estimate only ~2 more steps."""
        steps = [
            {"action": "web_search"},
            {"action": "fetch_webpage"},
            {"action": "create_file"},
        ]
        estimate = _estimate_total_steps(steps, 12)
        # 3 done + 2 more = 5
        assert estimate == 5

    def test_multiple_writes_still_close(self):
        """Multiple writes still estimates ~2 more."""
        steps = [
            {"action": "web_search"},
            {"action": "create_file"},
            {"action": "append_file"},
        ]
        estimate = _estimate_total_steps(steps, 12)
        assert estimate == 5

    def test_never_exceeds_max(self):
        """Estimate should never exceed max_steps."""
        steps = [
            {"action": "think"},
            {"action": "think"},
        ]
        estimate = _estimate_total_steps(steps, 5)
        assert estimate <= 5

    def test_never_below_current_plus_one(self):
        """Estimate should be at least current step count + 1."""
        steps = [
            {"action": "create_file"},
            {"action": "create_file"},
            {"action": "create_file"},
            {"action": "create_file"},
            {"action": "create_file"},
            {"action": "create_file"},
            {"action": "create_file"},
            {"action": "create_file"},
            {"action": "create_file"},
            {"action": "create_file"},
        ]
        estimate = _estimate_total_steps(steps, 12)
        assert estimate >= 11  # at least len(steps) + 1

    def test_thinking_phase_estimate(self):
        """Pure thinking phase still needs several more steps."""
        steps = [
            {"action": "think"},
            {"action": "think"},
        ]
        estimate = _estimate_total_steps(steps, 12)
        # 2 done + 5 = 7
        assert estimate == 7

    def test_write_source_counts_as_writing(self):
        """write_source action should trigger 'near completion' estimate."""
        steps = [
            {"action": "read_file"},
            {"action": "write_source"},
        ]
        estimate = _estimate_total_steps(steps, 25)
        assert estimate == 4  # 2 + 2

    def test_edit_file_counts_as_writing(self):
        """edit_file action should trigger 'near completion' estimate."""
        steps = [
            {"action": "read_file"},
            {"action": "edit_file"},
        ]
        estimate = _estimate_total_steps(steps, 25)
        assert estimate == 4

    def test_realistic_research_flow(self):
        """Full research flow: search→fetch→search→create_file should estimate correctly."""
        # Step 1-2: still researching
        steps_early = [
            {"action": "web_search"},
            {"action": "fetch_webpage"},
        ]
        early_est = _estimate_total_steps(steps_early, 12)

        # Step 3-4: more research
        steps_mid = steps_early + [
            {"action": "web_search"},
            {"action": "fetch_webpage"},
        ]
        mid_est = _estimate_total_steps(steps_mid, 12)

        # Step 5: writing
        steps_late = steps_mid + [
            {"action": "create_file"},
        ]
        late_est = _estimate_total_steps(steps_late, 12)

        # Remaining steps should decrease as we progress
        early_remaining = early_est - len(steps_early)
        mid_remaining = mid_est - len(steps_mid)
        late_remaining = late_est - len(steps_late)
        assert late_remaining <= early_remaining

    def test_max_steps_small(self):
        """With a small max_steps, estimate stays within bounds."""
        steps = [
            {"action": "web_search"},
            {"action": "fetch_webpage"},
            {"action": "web_search"},
        ]
        estimate = _estimate_total_steps(steps, 5)
        assert estimate <= 5
        assert estimate >= 4  # at least n + 1
