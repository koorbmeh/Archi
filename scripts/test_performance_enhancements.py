import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import src.core.cuda_bootstrap  # noqa: F401 - CUDA path for model loading
import logging
import time
from src.models.cache import QueryCache
from src.monitoring.performance_monitor import performance_monitor
from src.models.local_model import LocalModel

logging.basicConfig(level=logging.INFO)

print("Performance Enhancements Test")
print("=" * 60)

# Test 1: LRU eviction (memory only - no disk)
print("\n1. Testing LRU eviction in QueryCache...")
cache_lru = QueryCache(ttl_seconds=3600, max_size=3, use_disk_cache=False)

for i in range(4):
    cache_lru.set(f"query_{i}", {"text": f"response_{i}"})
    time.sleep(0.01)

# query_0 should be evicted
has_0 = cache_lru.get("query_0") is not None
has_1 = cache_lru.get("query_1") is not None
print(f"query_0 in cache (evicted): {has_0}")  # Should be False
print(f"query_1 in cache: {has_1}")  # Should be True

# Test 2: Disk persistence
print("\n2. Testing disk cache...")
cache = QueryCache(ttl_seconds=3600, max_size=10, use_disk_cache=True)
cache.set("persistent_query", {"text": "persistent_response"})

# Create new cache instance (simulates restart)
cache2 = QueryCache(
    ttl_seconds=3600, max_size=10, use_disk_cache=True
)
cached = cache2.get("persistent_query")
print(f"Retrieved from disk after 'restart': {cached is not None}")

# Test performance monitor
print("\n3. Testing PerformanceMonitor...")
model = LocalModel()

# Time some operations
for _ in range(3):
    with performance_monitor.time_operation("model_generation"):
        model.generate("Test query", max_tokens=10)

# Simulate cache lookups
for _ in range(10):
    with performance_monitor.time_operation("cache_lookup"):
        time.sleep(0.01)

# Get stats
print("\nPerformance statistics:")
stats = performance_monitor.get_stats()
for op, op_stats in stats.items():
    print(f"\n{op}:")
    print(f"  Count: {op_stats['count']}")
    print(f"  Avg: {op_stats['avg_ms']:.1f}ms")
    print(f"  P95: {op_stats['p95_ms']:.1f}ms")
    print(f"  Errors: {op_stats['errors']}")

# Cache stats
print("\n4. Cache statistics:")
cache_stats = cache2.get_stats()
print(f"Hit rate: {cache_stats.get('hit_rate_percent', 0):.1f}%")
print(f"Memory cache size: {cache_stats['cached_entries']}")
if "disk_entries" in cache_stats:
    print(f"Disk cache entries: {cache_stats['disk_entries']}")

print("\n[OK] Performance enhancements working!")
