"""Unit tests for the Performance Monitor module — operation timing and throughput.

Tests PerformanceMonitor init, record, time_operation context manager,
get_stats, percentile calculation, and global instance.

Created session 149.
"""

import time
import pytest
from unittest.mock import patch

from src.monitoring.performance_monitor import PerformanceMonitor, performance_monitor


# ── Init tests ─────────────────────────────────────────────────────


class TestPerformanceMonitorInit:
    """Tests for PerformanceMonitor initialization."""

    def test_empty_on_init(self):
        pm = PerformanceMonitor()
        assert pm.timings == {}
        assert pm.counts == {}
        assert pm.errors == {}

    def test_global_instance_exists(self):
        assert isinstance(performance_monitor, PerformanceMonitor)


# ── record() tests ─────────────────────────────────────────────────


class TestRecord:
    """Tests for record() — operation timing recording."""

    def test_basic_record(self):
        pm = PerformanceMonitor()
        pm.record("test_op", 0.5)
        assert pm.counts["test_op"] == 1
        assert len(pm.timings["test_op"]) == 1
        assert pm.errors["test_op"] == 0

    def test_multiple_records(self):
        pm = PerformanceMonitor()
        pm.record("op", 0.1)
        pm.record("op", 0.2)
        pm.record("op", 0.3)
        assert pm.counts["op"] == 3
        assert len(pm.timings["op"]) == 3

    def test_error_record(self):
        pm = PerformanceMonitor()
        pm.record("op", 0.5, error=True)
        assert pm.errors["op"] == 1
        assert pm.counts["op"] == 1

    def test_separate_operations(self):
        pm = PerformanceMonitor()
        pm.record("op_a", 0.1)
        pm.record("op_b", 0.2)
        assert "op_a" in pm.timings
        assert "op_b" in pm.timings
        assert pm.counts["op_a"] == 1
        assert pm.counts["op_b"] == 1

    def test_maxlen_100(self):
        """Deque should cap at 100 entries."""
        pm = PerformanceMonitor()
        for i in range(150):
            pm.record("op", float(i))
        assert len(pm.timings["op"]) == 100
        assert pm.counts["op"] == 150


# ── time_operation() tests ─────────────────────────────────────────


class TestTimeOperation:
    """Tests for time_operation() context manager."""

    def test_records_successful_operation(self):
        pm = PerformanceMonitor()
        with pm.time_operation("test"):
            pass  # Instant operation
        assert pm.counts["test"] == 1
        assert pm.errors["test"] == 0
        assert len(pm.timings["test"]) == 1

    def test_records_failed_operation(self):
        pm = PerformanceMonitor()
        with pytest.raises(ValueError):
            with pm.time_operation("fail_op"):
                raise ValueError("boom")
        assert pm.counts["fail_op"] == 1
        assert pm.errors["fail_op"] == 1

    def test_timing_is_positive(self):
        pm = PerformanceMonitor()
        with pm.time_operation("sleep"):
            time.sleep(0.01)
        assert pm.timings["sleep"][0] >= 0.01

    def test_exception_propagates(self):
        pm = PerformanceMonitor()
        with pytest.raises(RuntimeError, match="test error"):
            with pm.time_operation("op"):
                raise RuntimeError("test error")


# ── get_stats() tests ──────────────────────────────────────────────


class TestGetStats:
    """Tests for get_stats() — statistics retrieval."""

    def test_empty_stats(self):
        pm = PerformanceMonitor()
        assert pm.get_stats() == {}

    def test_single_operation_stats(self):
        pm = PerformanceMonitor()
        pm.record("op", 0.1)
        pm.record("op", 0.2)
        pm.record("op", 0.3)
        stats = pm.get_stats("op")
        assert stats["count"] == 3
        assert stats["errors"] == 0
        assert stats["error_rate"] == 0
        assert stats["avg_ms"] == pytest.approx(200.0, rel=0.01)
        assert stats["min_ms"] == pytest.approx(100.0, rel=0.01)
        assert stats["max_ms"] == pytest.approx(300.0, rel=0.01)

    def test_all_operations_stats(self):
        pm = PerformanceMonitor()
        pm.record("a", 0.1)
        pm.record("b", 0.2)
        stats = pm.get_stats()
        assert "a" in stats
        assert "b" in stats

    def test_nonexistent_operation(self):
        pm = PerformanceMonitor()
        stats = pm.get_stats("nonexistent")
        assert stats == {}

    def test_error_rate_calculation(self):
        pm = PerformanceMonitor()
        pm.record("op", 0.1, error=False)
        pm.record("op", 0.2, error=True)
        pm.record("op", 0.3, error=False)
        pm.record("op", 0.4, error=True)
        stats = pm.get_stats("op")
        assert stats["error_rate"] == pytest.approx(50.0)

    def test_percentiles(self):
        pm = PerformanceMonitor()
        # Add 100 values from 0.01 to 1.00
        for i in range(1, 101):
            pm.record("op", i / 100.0)
        stats = pm.get_stats("op")
        assert stats["p50_ms"] == pytest.approx(500.0, rel=0.05)
        assert stats["p95_ms"] == pytest.approx(950.0, rel=0.05)


# ── _stats_for_operation() tests ───────────────────────────────────


class TestStatsForOperation:
    """Tests for _stats_for_operation() internal method."""

    def test_missing_operation(self):
        pm = PerformanceMonitor()
        assert pm._stats_for_operation("nope") == {}

    def test_has_all_keys(self):
        pm = PerformanceMonitor()
        pm.record("op", 0.5)
        stats = pm._stats_for_operation("op")
        expected_keys = {"count", "errors", "error_rate", "avg_ms", "min_ms", "max_ms", "p50_ms", "p95_ms"}
        assert set(stats.keys()) == expected_keys


# ── _percentile() tests ────────────────────────────────────────────


class TestPercentile:
    """Tests for _percentile() calculation."""

    def test_empty_values(self):
        pm = PerformanceMonitor()
        assert pm._percentile([], 50) == 0.0

    def test_single_value(self):
        pm = PerformanceMonitor()
        assert pm._percentile([0.5], 50) == 0.5

    def test_sorted_values(self):
        pm = PerformanceMonitor()
        values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        p50 = pm._percentile(values, 50)
        assert p50 == pytest.approx(0.6, abs=0.1)

    def test_p0_returns_minimum(self):
        pm = PerformanceMonitor()
        assert pm._percentile([1.0, 2.0, 3.0], 0) == 1.0

    def test_p100_returns_maximum(self):
        pm = PerformanceMonitor()
        assert pm._percentile([1.0, 2.0, 3.0], 100) == 3.0
