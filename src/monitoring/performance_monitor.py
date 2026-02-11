"""
Performance Monitor - Track operation-level performance metrics.

Complements SystemMonitor (health) with timing and throughput metrics.
"""

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PerformanceMonitor:
    """
    Track performance of individual operations.

    Different from SystemMonitor:
    - SystemMonitor: CPU, memory, disk (health metrics)
    - PerformanceMonitor: Operation timings, throughput (performance metrics)
    """

    def __init__(self) -> None:
        self.timings: Dict[str, List[float]] = {}
        self.counts: Dict[str, int] = {}
        self.errors: Dict[str, int] = {}
        self._lock = threading.Lock()

        logger.info("Performance monitor initialized")

    @contextmanager
    def time_operation(self, operation: str):
        """
        Context manager for timing operations.

        Usage:
            with perf_monitor.time_operation('model_generation'):
                result = model.generate(prompt)
        """
        start_time = time.time()
        error_occurred = False

        try:
            yield
        except Exception:
            error_occurred = True
            raise
        finally:
            duration = time.time() - start_time
            self.record(operation, duration, error_occurred)

    def record(self, operation: str, duration: float, error: bool = False) -> None:
        """Record operation timing and result."""
        with self._lock:
            if operation not in self.timings:
                self.timings[operation] = []
                self.counts[operation] = 0
                self.errors[operation] = 0

            self.timings[operation].append(duration)
            if len(self.timings[operation]) > 100:
                self.timings[operation].pop(0)

            self.counts[operation] += 1
            if error:
                self.errors[operation] += 1

    def get_stats(self, operation: Optional[str] = None) -> Dict[str, Any]:
        """Get performance statistics."""
        with self._lock:
            if operation:
                return self._stats_for_operation(operation)

            return {
                op: self._stats_for_operation(op)
                for op in self.timings
            }

    def _stats_for_operation(self, operation: str) -> Dict[str, Any]:
        """Get stats for a specific operation."""
        if operation not in self.timings:
            return {}

        timings = self.timings[operation]
        if not timings:
            return {}

        count = self.counts[operation]
        err_count = self.errors[operation]

        return {
            "count": count,
            "errors": err_count,
            "error_rate": (err_count / count * 100) if count > 0 else 0,
            "avg_ms": sum(timings) / len(timings) * 1000,
            "min_ms": min(timings) * 1000,
            "max_ms": max(timings) * 1000,
            "p50_ms": self._percentile(timings, 50) * 1000,
            "p95_ms": self._percentile(timings, 95) * 1000,
        }

    def _percentile(self, values: List[float], percentile: int) -> float:
        """Calculate percentile."""
        if not values:
            return 0.0

        sorted_values = sorted(values)
        index = int(len(sorted_values) * percentile / 100)
        return sorted_values[min(index, len(sorted_values) - 1)]


# Global instance
performance_monitor = PerformanceMonitor()
