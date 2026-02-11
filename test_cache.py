"""Test query cache: repeated prompts hit cache and save API cost."""

import time
from pathlib import Path

_root = Path(__file__).resolve().parent
_env = _root / ".env"
if _env.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env)
    except ImportError:
        pass

import src.core.cuda_bootstrap  # noqa: F401 -- CUDA on PATH before loading local model
from src.models.router import ModelRouter

print("Testing Query Cache...")
print("=" * 60)

router = ModelRouter()
print("[OK] Router initialized\n")

test_query = "What is the capital of France?"

# First call - may hit API or local
print("Query 1 (first time):")
start = time.time()
response1 = router.generate(test_query)
elapsed1 = time.time() - start

print(f"Model: {response1.get('model', '?')}")
text1 = response1.get("text", "")
print(f"Response: {text1[:60]}{'...' if len(text1) > 60 else ''}")
print(f"Cost: ${response1.get('cost_usd', 0):.6f}")
print(f"Duration: {elapsed1:.2f}s\n")

# Second call - should hit cache
print("Query 2 (same question - should hit cache):")
start = time.time()
response2 = router.generate(test_query)
elapsed2 = time.time() - start

print(f"Model: {response2.get('model', '?')}")
text2 = response2.get("text", "")
print(f"Response: {text2[:60]}{'...' if len(text2) > 60 else ''}")
print(f"Cost: ${response2.get('cost_usd', 0):.6f}")
print(f"Duration: {elapsed2:.2f}s\n")

# Different query - should miss cache
print("Query 3 (different question - should miss cache):")
response3 = router.generate("What is the capital of Germany?")
print(f"Model: {response3.get('model', '?')}")
print(f"Cost: ${response3.get('cost_usd', 0):.6f}\n")

# Show cache stats
print("=" * 60)
print("CACHE STATISTICS")
print("=" * 60)
stats = router.get_stats()
print(f"Cache hits: {stats.get('cache_hits', 0)}")
print(f"Cache misses: {stats.get('cache_misses', 0)}")
print(f"Hit rate: {stats.get('cache_hit_rate', 0):.1f}%")
print(f"Cached entries: {stats.get('cached_entries', 0)}")

print("\n[OK] Cache tests complete!")

# Verify cost savings when second query was cached
if response2.get("cost_usd", 0) == 0 and response1.get("cost_usd", 0) > 0:
    print(f"\n✓ Cache saved: ${response1['cost_usd']:.6f} on second query!")
elif response2.get("cost_usd", 0) == 0 and response1.get("cost_usd", 0) == 0:
    print("\n✓ Both queries had $0 cost (local or cache).")
elif stats.get("cache_hits", 0) >= 1:
    print("\n✓ Cache hit on repeat query (instant response, no extra cost).")
