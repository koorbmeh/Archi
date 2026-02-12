"""
Test local model with free web search.
Run with: .\venv\Scripts\python.exe scripts\test_local_search.py

CUDA bootstrap runs on import so the local model can load when available.
"""
import os
import sys

# Project root (parent of tests/scripts/) so "import src..." works
_script_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(os.path.dirname(_script_dir))
sys.path.insert(0, _repo_root)
os.chdir(_repo_root)

# Load .env so GROK_API_KEY is set (router needs it for escalation)
_env_path = os.path.join(_repo_root, ".env")
if os.path.isfile(_env_path):
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        pass

# Ensure CUDA is on PATH before any code loads the local model (llama_cpp)
import src.core.cuda_bootstrap  # noqa: F401

import logging

from src.models.router import ModelRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

print("Testing Local Model with Free Web Search")
print("=" * 60)

router = ModelRouter()

if not router.local_available:
    print("Local model not available; test will use Grok only.")
    print("(For local + search: run from repo root so CUDA bootstrap runs.)")
    print()

query = "What is the current weather in Madison, WI?"
print(f"\nQuery: {query}")
print("-" * 60)

response = router.generate(query, max_tokens=150)

print(f"\nResponse: {response.get('text', '')[:400]}")
print(f"\nModel used: {response.get('model', 'unknown')}")
print(f"Used web search: {response.get('used_web_search', False)}")
print(f"Search results: {response.get('search_results_count', 0)}")
print(f"Cost: ${response.get('cost_usd', 0):.6f}")

if response.get("used_web_search") and response.get("cost_usd", 0) == 0:
    print("\nSUCCESS: Local model used FREE web search.")
elif response.get("cost_usd", 0) > 0:
    print("\nUsed Grok (paid web search).")
else:
    print("\nNo web search used.")

print("\n" + "=" * 60)
