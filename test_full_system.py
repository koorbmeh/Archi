"""Test full system: memory + router together without running the agent loop."""

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
from src.memory.memory_manager import MemoryManager
from src.models.router import ModelRouter

print("Testing Full System Integration...")
print("=" * 60)

# Initialize all systems
print("\n1. Initializing systems...")
memory = MemoryManager()
router = ModelRouter()
print(f"[OK] Memory initialized: {memory.get_stats()}")
print(f"[OK] Router initialized: local={router.local_available}")

# Test memory + router together
print("\n2. Testing memory storage...")
memory.store_action(
    action_type="test_query",
    parameters={"query": "What is AI?"},
    result="success",
    confidence=0.85,
)
print(f"[OK] Action stored: {memory.get_stats()}")

# Test router with both simple and complex
print("\n3. Testing router decisions...")

queries = [
    ("What is 5+5?", "simple"),
    ("Explain quantum computing in detail", "complex"),
]

for query, expected in queries:
    print(f"\nQuery: {query}")
    print(f"Expected: {expected}")

    start = time.time()
    response = router.generate(query, max_tokens=100)
    elapsed = time.time() - start

    print(f"  Model: {response.get('model', '?')}")
    print(f"  Cost: ${response.get('cost_usd', 0):.6f}")
    print(f"  Duration: {elapsed:.2f}s")

    # Store in memory
    memory.store_action(
        action_type="query",
        parameters={"query": query},
        result=(response.get("text") or "")[:50],
        confidence=response.get("confidence", 0.0),
    )

# Show final stats
print("\n" + "=" * 60)
print("FINAL SYSTEM STATS")
print("=" * 60)
print(f"Memory: {memory.get_stats()}")
print(f"Router: {router.get_stats()}")

print("\n[OK] Full system integration test complete!")
