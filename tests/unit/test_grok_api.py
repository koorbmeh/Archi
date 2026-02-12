"""
Test Grok API (x.ai). Requires GROK_API_KEY in .env.
Run from repo root: .\venv\Scripts\python.exe tests/unit/test_grok_api.py
"""
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

# Load .env from repo root
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass

if not os.getenv("GROK_API_KEY"):
    print("ERROR: GROK_API_KEY not found in .env")
    print("Copy .env.example to .env and set GROK_API_KEY (get key at x.ai)")
    sys.exit(1)

print("Testing Grok API...")

from src.models.grok_client import GrokClient

client = GrokClient()
print("[OK] Grok client initialized")

print("\nTest 1: Simple question")
result = client.generate("What is 7 * 8? Answer with just the number.", max_tokens=10)

if not result["success"]:
    print("ERROR:", result.get("error", "Unknown"))
    sys.exit(1)

print("Response:", result["text"].strip())
print("Tokens:", result["tokens"], f"({result['input_tokens']} in + {result['output_tokens']} out)")
print("Cost: $%.6f" % result["cost_usd"])
print("Duration: %d ms" % result["duration_ms"])

print("\nTest 2: One-sentence explanation")
result = client.generate("Explain what an AI agent is in one sentence.", max_tokens=50)
if not result["success"]:
    print("ERROR:", result.get("error"))
    sys.exit(1)
print("Response:", result["text"].strip()[:200])
print("Cost: $%.6f" % result["cost_usd"])

print("\n[OK] Grok API is working!")
