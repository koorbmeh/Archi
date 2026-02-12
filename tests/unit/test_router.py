"""Test the model router: local vs Grok routing by complexity and confidence."""

import os
import sys
import time
from pathlib import Path

# Load .env from repo root so GROK_API_KEY (and optionally LOCAL_MODEL_PATH) are set
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)
_env = _root / ".env"
if _env.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env)
    except ImportError:
        pass

import src.core.cuda_bootstrap  # noqa: F401 -- CUDA on PATH before loading local model
from src.models.router import ModelRouter

print("Testing Model Router...")
print("=" * 60)

router = ModelRouter()
if router.local_available:
    print("[OK] Router initialized (local + Grok ready)\n")
else:
    print("[WARN] Running in Grok-only mode (local model not available)\n")

test_queries = [
    ("What is 5 + 3?", "simple"),
    ("Define machine learning in one sentence.", "simple"),
    ("Explain how neural networks work.", "medium"),
    ("Write a detailed analysis of transformer architecture.", "complex"),
]

for i, (query, expected_complexity) in enumerate(test_queries, 1):
    print(f"\nTest {i}: {query}")
    print(f"Expected complexity: {expected_complexity}")
    print("-" * 60)

    start = time.time()
    response = router.generate(query, max_tokens=100)
    elapsed = time.time() - start

    if response.get("success"):
        print(f"Model used: {response.get('model', '?')}")
        text = response.get("text", "")
        print(f"Response: {text[:100]}{'...' if len(text) > 100 else ''}")
        print(f"Cost: ${response.get('cost_usd', 0):.6f}")
        print(f"Duration: {elapsed:.2f}s")
        if "confidence" in response:
            print(f"Confidence: {response['confidence']:.2f}")
    else:
        print(f"ERROR: {response.get('error', 'Unknown error')}")

print("\n" + "=" * 60)
print("ROUTING STATISTICS")
print("=" * 60)
stats = router.get_stats()
print(f"Local model used: {stats['local_used']} ({stats['local_percentage']:.1f}%)")
print(f"Grok API used: {stats['grok_used']}")
print(f"Total queries: {stats['total_queries']}")
print(f"Total cost: ${stats['total_cost_usd']:.6f}")
print(f"Avg cost/query: ${stats['avg_cost_per_query']:.6f}")

print("\n[OK] Router tests complete!")
