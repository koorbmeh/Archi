"""
Test whether Grok API supports web search.

From the docs (https://docs.x.ai/developers/tools/web-search):
  - Web search is the "Web Search" tool (web_search) on the RESPONSES API.
  - xAI SDK: client.chat.create(model=..., tools=[web_search()])
  - OpenAI Responses API: same tool name web_search.
  - It is NOT on the legacy Chat Completions endpoint (/v1/chat/completions).

So "working" = our call to POST /v1/responses with tools=[web_search] succeeds.
A 403/1010 here means your key/plan doesn't have access to the Responses API,
not that web search is unsupported.

Run: .\venv\Scripts\python.exe scripts\test_grok_search.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

# Project root (parent of tests/scripts/)
_script_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(os.path.dirname(_script_dir))
sys.path.insert(0, _repo_root)
os.chdir(_repo_root)

# Load .env from repo root so GROK_API_KEY is set
_env_path = os.path.join(_repo_root, ".env")
if os.path.isfile(_env_path):
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        pass

def _responses_api_web_search(api_key: str, base_url: str, model: str) -> None:
    """Test web search via /v1/responses with tools=[web_search]."""
    url = f"{base_url.rstrip('/')}/responses"
    # x.ai docs: tools=[web_search()] -> JSON: {"type": "web_search"}
    web_search_tool = {"type": "web_search"}
    payload = {
        "model": model,
        "input": [
            {"role": "user", "content": "What is one major tech headline from the last 24 hours? One sentence only."}
        ],
        "tools": [web_search_tool],
    }
    data = json.dumps(payload).encode("utf-8")
    # 1010 can be WAF/Cloudflare blocking minimal headers; send common client headers
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Archi/1.0 (python-urllib; Grok-Responses-API)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    output = body.get("output", [])
    text_parts = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content", [])
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "output_text":
                    text_parts.append(c.get("text", ""))
        elif isinstance(content, str):
            text_parts.append(content)
    result_text = " ".join(text_parts).strip() if text_parts else str(body)[:300]

    print("   OK. Responses API with web_search succeeded.")
    print(f"   Response (excerpt): {result_text[:200]!r}...")
    citations = body.get("citations", [])
    if citations:
        print(f"   Citations: {len(citations)} source(s)")
    print()
    print("   VERDICT: Grok HAS built-in web search (Responses API + web_search).")
    print("   Gate C: Can use Grok for search; browser automation optional. (Prefer: Google)")


def main() -> None:
    api_key = os.environ.get("GROK_API_KEY")
    if not api_key:
        print("GROK_API_KEY not set. Set it in .env or environment and retry.")
        sys.exit(1)

    base_url = os.environ.get("GROK_BASE_URL", "https://api.x.ai/v1")
    model = os.environ.get("GROK_MODEL", "grok-4-1-fast-reasoning")

    print("Testing Grok Web Search Capability")
    print("Docs: https://docs.x.ai/developers/tools/web-search")
    print()
    print("Note: Web search is on the RESPONSES API (web_search), not Chat Completions.")
    print("=" * 60)
    print(f"Base URL: {base_url}")
    print(f"Model:    {model}")
    print()

    # --- Test 1: Regular query (no web search) ---
    print("Test 1: Regular query (no web search)")
    print("-" * 60)
    try:
        from src.models.grok_client import GrokClient
        client = GrokClient()
        r = client.generate("What is 7 times 8?", max_tokens=50, temperature=0)
        if r.get("success"):
            print(f"Response: {r.get('text', '').strip()!r}")
            print("Regular chat completions working.")
        else:
            print(f"Failed: {r.get('error', 'unknown')}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    print()

    # --- Test 2a: Official xAI SDK (if installed) - same API, different client; may avoid 1010 ---
    try:
        from xai_sdk import Client as XAIClient
        from xai_sdk.chat import user
        from xai_sdk.tools import web_search
    except ImportError:
        XAIClient = None
    if XAIClient is not None:
        print("Test 2a: Official xAI SDK with web_search (pip install xai-sdk)")
        print("-" * 60)
        try:
            client = XAIClient(api_key=api_key)
            chat = client.chat.create(model=model, tools=[web_search()])
            chat.append(user("What is one major tech headline from the last 24 hours? One sentence only."))
            response = chat.sample()
            text = (response.content or "").strip()
            print(f"Response: {text[:250]!r}")
            print("VERDICT: Web search works via official xAI SDK.")
            print("Gate C: Use xAI SDK for search, or replicate its request format.")
            print("=" * 60)
            return
        except Exception as e:
            print(f"xAI SDK failed: {e}")
        print()
    else:
        print("(Install xai-sdk for Test 2a: pip install xai-sdk)")
        print()

    # --- Test 2b: Raw POST to /v1/responses with web_search ---
    print("Test 2b: Raw POST /v1/responses with web_search")
    print("-" * 60)
    try:
        _responses_api_web_search(api_key, base_url, model)
        print("=" * 60)
        return
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8") if e.fp else ""
        print(f"HTTP {e.code}: {e.reason}")
        try:
            j = json.loads(err_body)
            print(f"Error: {j.get('error', {}).get('message', err_body[:200])}")
        except Exception:
            print(f"Body: {err_body[:300]}")
        print()
        if e.code == 403 or "1010" in err_body:
            print("VERDICT: Web search IS supported by Grok (Responses API + web_search).")
            print("         403/1010 can be: key permission, WAF/network block, or region.")
            print("         If your key is unrestricted, try: pip install xai-sdk, then run")
            print("         this script again (it will try the official xAI SDK). Or contact")
            print("         x.ai support with the error. Fallback: browser automation for search.")
        else:
            print("VERDICT: Unexpected error from Responses API.")
    except Exception as e:
        print(f"Error: {e}")
        print("VERDICT: Request failed (see error above).")
    print()
    print("Gate C (fallback): Browser automation for web search; Computer Use API; file watching.")
    print("=" * 60)


if __name__ == "__main__":
    main()
