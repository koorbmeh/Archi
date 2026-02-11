"""
Test LanceDB: connection, embedding model, table create, semantic search.
Run from repo root: .\venv\Scripts\python.exe test_lancedb.py
"""
import os
from sentence_transformers import SentenceTransformer

print("Testing LanceDB...")

import lancedb

# Initialize embedding model
print("Loading embedding model...")
model = SentenceTransformer("all-MiniLM-L6-v2")

# Create LanceDB connection
data_dir = os.path.join(os.getcwd(), "data", "vectors")
os.makedirs(data_dir, exist_ok=True)

db = lancedb.connect(data_dir)
print(f"[OK] Connected to LanceDB at {data_dir}")

# Create test data with embeddings
print("\nAdding test documents...")
test_docs = [
    "The capital of France is Paris",
    "Python is a programming language",
    "Machine learning uses neural networks",
]

embeddings = model.encode(test_docs)

data = []
for i, (doc, embedding) in enumerate(zip(test_docs, embeddings)):
    data.append({
        "id": f"doc{i+1}",
        "text": doc,
        "vector": embedding,
        "source": ["geography", "programming", "ai"][i],
    })

table = db.create_table("test_memories", data=data, mode="overwrite")
print(f"[OK] Created table with {len(data)} documents")

# Test semantic search
print("\nQuerying: 'What is the capital of France?'")
query_text = "What is the capital of France?"
query_embedding = model.encode([query_text])[0]

results = table.search(query_embedding).limit(2).to_list()

print("\nTop results:")
for i, result in enumerate(results, 1):
    dist = result.get("_distance", result.get("_lance_distance", 0))
    print(f"  {i}. {result['text']}")
    print(f"     Distance: {dist:.3f}")

print("\n[OK] LanceDB is working!")
