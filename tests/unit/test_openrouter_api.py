"""
Test OpenRouter API. Requires OPENROUTER_API_KEY in .env.
Run from repo root: .\venv\Scripts\python.exe tests/unit/test_openrouter_api.py
"""
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

# Load .env from repo root
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass

if not os.getenv("OPENROUTER_API_KEY"):
    print("ERROR: OPENROUTER_API_KEY not found in .env")
    print("Copy .env.example to .env and set OPENROUTER_API_KEY")
    print("Get a key at https://openrouter.ai/keys")
    sys.exit(1)

print("Testing OpenRouter API...")

from src.models.openrouter_client import OpenRouterClient

client = OpenRouterClient()
print("[OK] OpenRouter client initialized")
print("     Default model:", os.getenv("OPENROUTER_MODEL", "x-ai/grok-4.1-fast"))

# -------------------------------------------------------------------
# Test 1: Simple question (default model)
# -------------------------------------------------------------------
print("\nTest 1: Simple question (default model)")
result = client.generate("What is 7 * 8? Answer with just the number.", max_tokens=10)

if not result["success"]:
    print("ERROR:", result.get("error", "Unknown"))
    sys.exit(1)

print("Response:", result["text"].strip())
print("Model used:", result["model"])
print("Tokens:", result["tokens"], f"({result['input_tokens']} in + {result['output_tokens']} out)")
print("Cost: $%.6f" % result["cost_usd"])
print("Duration: %d ms" % result["duration_ms"])

# -------------------------------------------------------------------
# Test 2: One-sentence explanation
# -------------------------------------------------------------------
print("\nTest 2: One-sentence explanation")
result = client.generate("Explain what an AI agent is in one sentence.", max_tokens=50)
if not result["success"]:
    print("ERROR:", result.get("error"))
    sys.exit(1)
print("Response:", result["text"].strip()[:200])
print("Model used:", result["model"])
print("Cost: $%.6f" % result["cost_usd"])

# -------------------------------------------------------------------
# Test 3: Explicit model override (if env has a different default)
# -------------------------------------------------------------------
print("\nTest 3: Explicit model override")
result = client.generate(
    "What is the capital of France? Answer in one word.",
    max_tokens=10,
    model="openrouter/auto",
)
if not result["success"]:
    print("WARNING: Model override failed:", result.get("error"))
    print("(This may happen if the model is not available on your plan)")
else:
    print("Response:", result["text"].strip())
    print("Model used:", result["model"])
    print("Cost: $%.6f" % result["cost_usd"])

# -------------------------------------------------------------------
# Test 4: enable_web_search compat (should work, just ignored)
# -------------------------------------------------------------------
print("\nTest 4: enable_web_search compat (no-op, should not error)")
result = client.generate(
    "What is 2+2?",
    max_tokens=10,
    enable_web_search=True,
)
if not result["success"]:
    print("ERROR:", result.get("error"))
    sys.exit(1)
print("Response:", result["text"].strip())
print("[OK] enable_web_search param accepted (no-op)")

print("\n[OK] OpenRouter API is working!")
