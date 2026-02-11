"""
Test the LanceDB-based VectorStore wrapper.
Run from repo root: .\venv\Scripts\python.exe test_vector_store.py
"""
import time

from src.memory.vector_store import VectorStore

print("Testing VectorStore (LanceDB)...")

# Initialize
store = VectorStore()
print("[OK] Vector store initialized")
print(f"  Current memories: {store.get_memory_count()}")

# Add test memories
print("\n1. Adding memories...")
memories_to_add = [
    ("User prefers Python over JavaScript", {"type": "preference", "language": "Python"}),
    ("User is working on an AI agent project called Archi", {"type": "project", "project_name": "Archi"}),
    ("User has an RTX 5070 Ti GPU with 12GB memory", {"type": "hardware", "device": "GPU"}),
    ("User installed Visual Studio for C++ compilation", {"type": "event", "software": "Visual Studio"}),
    ("User's local model runs at 26 tokens per second", {"type": "metric", "performance": 26}),
]

for text, metadata in memories_to_add:
    memory_id = store.add_memory(text, metadata)
    print(f"  Added: {text[:50]}... (ID: {memory_id[:8]})")

time.sleep(0.5)

print(f"\n  Total memories: {store.get_memory_count()}")

# Semantic search
print("\n2. Testing semantic search...")
test_queries = [
    "What programming languages does the user like?",
    "What hardware does the user have?",
    "What is the user building?",
]

for query in test_queries:
    print(f"\n  Query: '{query}'")
    results = store.search(query, n_results=2)
    for i, result in enumerate(results, 1):
        print(f"    {i}. {result['text'][:60]}...")
        print(f"       Distance: {result['distance']:.3f}")

# Metadata filtering
print("\n3. Testing metadata filtering...")
preference_results = store.search(
    "What are the user's preferences?",
    n_results=3,
    filter_metadata={"type": "preference"},
)
print(f"  Found {len(preference_results)} preference memories")
if preference_results:
    print(f"  Result: {preference_results[0]['text']}")

print("\n[OK] Vector store tests passed!")
