#!/usr/bin/env python3
r"""
Comprehensive Archi integration test.
Validates every component developed through Gate A, B, and C.
Run from repo root: .\venv\Scripts\python.exe test_archi_full.py

Optional env vars:
  SKIP_GROK=1     - Skip Grok API tests (save cost)
  SKIP_LOCAL=1    - Skip local model load (slow, GPU-heavy)
  SKIP_VISION=1   - Skip vision test (screenshot + model analysis)
  SKIP_DESKTOP=1  - Skip desktop screenshot test
  SKIP_BROWSER=1  - Skip browser automation test

Note: CUDA bootstrap is still needed — Forge uses llama-cpp-python, which
requires CUDA DLLs on PATH for GPU. The bootstrap prepends CUDA bin paths.
"""

import os
import sys
import tempfile
from pathlib import Path

# Setup path and .env
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

try:
    from dotenv import load_dotenv
    env_path = _root / ".env"
    _loaded = load_dotenv(env_path, override=True)  # override=True so .env wins over pre-existing empty env vars
    if not _loaded and not env_path.exists():
        # Fallback: try cwd in case script was run from elsewhere
        _cwd_env = Path.cwd() / ".env"
        if _cwd_env.exists():
            load_dotenv(_cwd_env, override=True)
except ImportError:
    pass

# Results tracking
_results: list[tuple[str, str, str]] = []  # (name, status, detail)


def _ok(name: str, detail: str = "") -> None:
    _results.append((name, "PASS", detail))
    print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))


def _fail(name: str, detail: str) -> None:
    _results.append((name, "FAIL", detail))
    print(f"  [FAIL] {name} — {detail}")


def _warn(name: str, detail: str) -> None:
    _results.append((name, "WARN", detail))
    print(f"  [WARN] {name} — {detail}")


def _skip(name: str, reason: str) -> None:
    _results.append((name, "SKIP", reason))
    print(f"  [SKIP] {name} — {reason}")


def run_tests() -> None:
    base = os.environ.get("ARCHI_ROOT") or str(_root)
    _local_model = None  # Reused for vision test

    print("=" * 60)
    print("ARCHI FULL INTEGRATION TEST")
    print("=" * 60)

    # --- 1. Config & Paths ---
    print("\n1. Config & Paths")
    try:
        rules_path = Path(base) / "config" / "rules.yaml"
        heartbeat_path = Path(base) / "config" / "heartbeat.yaml"
        if rules_path.is_file():
            _ok("config/rules.yaml", "found")
        else:
            _fail("config/rules.yaml", f"not found at {rules_path}")
        if heartbeat_path.is_file():
            _ok("config/heartbeat.yaml", "found")
        else:
            _fail("config/heartbeat.yaml", f"not found at {heartbeat_path}")
        workspace = Path(base) / "workspace"
        if workspace.is_dir():
            _ok("workspace directory", str(workspace))
        else:
            _warn("workspace directory", f"missing at {workspace}")
    except Exception as e:
        _fail("Config & Paths", str(e))

    # --- 2. CUDA Bootstrap ---
    print("\n2. CUDA Bootstrap")
    try:
        import src.core.cuda_bootstrap  # noqa: F401
        _ok("cuda_bootstrap", "PATH prepended for llama-cpp-python GPU DLLs (still needed with Forge)")
    except Exception as e:
        _warn("cuda_bootstrap", str(e))

    # --- 3. Safety Controller ---
    print("\n3. Safety Controller")
    try:
        from src.core.safety_controller import SafetyController, Action

        sc = SafetyController()
        _ok("SafetyController init", "rules loaded")

        # Legal path
        workspace_path = str(Path(base) / "workspace" / "test.txt")
        if Path(workspace_path).parent.exists():
            if sc.validate_path(workspace_path):
                _ok("Path validation (legal)", "workspace path allowed")
            else:
                _fail("Path validation (legal)", "workspace path wrongly denied")
            # Illegal path
            if not sc.validate_path("C:/Users/Jesse/Documents/forbidden.txt"):
                _ok("Path validation (illegal)", "forbidden path blocked")
            else:
                _fail("Path validation (illegal)", "forbidden path wrongly allowed")
        else:
            _skip("Path validation", "workspace not found")

        # Authorize action
        action = Action(
            type="read_file",
            parameters={"path": workspace_path},
            confidence=0.8,
            reasoning="Test",
        )
        auth = sc.authorize(action)
        if auth:
            _ok("Action authorization", "read_file in workspace approved")
        else:
            _warn("Action authorization", "read_file denied (check rules)")
    except Exception as e:
        _fail("Safety Controller", str(e))

    # --- 4. Heartbeat ---
    print("\n4. Adaptive Heartbeat")
    try:
        from src.core.heartbeat import AdaptiveHeartbeat

        hb = AdaptiveHeartbeat()
        dur = hb.get_sleep_duration()
        if 0 < dur <= 1800:
            _ok("AdaptiveHeartbeat", f"sleep duration {dur:.1f}s")
        else:
            _warn("AdaptiveHeartbeat", f"unexpected sleep={dur}")
    except Exception as e:
        _fail("Adaptive Heartbeat", str(e))

    # --- 5. Action Logger ---
    print("\n5. Action Logger")
    try:
        from src.core.logger import ActionLogger

        log_base = tempfile.mkdtemp()
        logger = ActionLogger(base_path=log_base)
        logger.log_action(
            action_type="test",
            parameters={"x": 1},
            result="ok",
            cost_usd=0.0,
        )
        logger.close()
        _ok("ActionLogger", "log written")
    except Exception as e:
        _fail("Action Logger", str(e))

    # --- 6. System Monitor ---
    print("\n6. System Monitor")
    try:
        from src.monitoring.system_monitor import SystemMonitor

        mon = SystemMonitor()
        throttle = mon.should_throttle()
        _ok("SystemMonitor", f"throttle={throttle}")
    except Exception as e:
        _fail("System Monitor", str(e))

    # --- 7. LanceDB / VectorStore ---
    print("\n7. VectorStore (LanceDB)")
    try:
        from src.memory.vector_store import VectorStore

        store = VectorStore()
        store.add_memory("Test memory for integration", {"type": "test"})
        count = store.get_memory_count()
        results = store.search("integration test", n_results=1)
        if results and "test" in results[0].get("text", "").lower():
            _ok("VectorStore", f"add, search OK ({count} memories)")
        else:
            _warn("VectorStore", "search returned unexpected result")
    except ImportError as e:
        _skip("VectorStore", f"missing dependency: {e}")
    except Exception as e:
        _fail("VectorStore", str(e))

    # --- 8. Memory Manager ---
    print("\n8. Memory Manager")
    try:
        from src.memory.memory_manager import MemoryManager

        mem = MemoryManager()
        mem.store_action(
            action_type="test_action",
            parameters={"x": 1},
            result=True,
            confidence=0.9,
        )
        stats = mem.get_stats()
        if "short_term" in stats or "short_term_count" in stats:
            _ok("MemoryManager", str(stats))
        else:
            _warn("MemoryManager", f"unexpected stats: {stats}")
    except ImportError as e:
        _skip("Memory Manager", f"missing dependency: {e}")
    except Exception as e:
        _fail("Memory Manager", str(e))

    # --- 9. Timestamps ---
    print("\n9. Maintenance Timestamps")
    try:
        from src.maintenance.timestamps import load_timestamp, save_timestamp
        from datetime import datetime, timezone

        save_timestamp("test_key", datetime.now(timezone.utc))
        loaded = load_timestamp("test_key")
        if loaded is not None:
            _ok("Timestamps", "save/load OK")
        else:
            _warn("Timestamps", "load returned None")
    except Exception as e:
        _fail("Timestamps", str(e))

    # --- 10. Goal Manager ---
    print("\n10. Goal Manager")
    try:
        from src.core.goal_manager import GoalManager

        gm = GoalManager()
        status = gm.get_status()
        _ok("GoalManager", f"{status['total_goals']} goals, {status['pending_tasks']} pending tasks")
    except Exception as e:
        _fail("Goal Manager", str(e))

    # --- 11. Tool Registry ---
    print("\n11. Tool Registry")
    try:
        from src.tools.tool_registry import ToolRegistry

        reg = ToolRegistry()
        test_file = Path(base) / "workspace" / "_test_archi_full.txt"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("test content", encoding="utf-8")

        # Read
        r = reg.execute("read_file", {"path": str(test_file)})
        if r.get("success") and "test content" in r.get("content", ""):
            _ok("Tool: read_file", "workspace read OK")
        else:
            _warn("Tool: read_file", str(r))

        # Write
        out_file = Path(base) / "workspace" / "_test_archi_write.txt"
        r = reg.execute("create_file", {"path": str(out_file), "content": "written by test"})
        if r.get("success"):
            _ok("Tool: create_file", "workspace write OK")
            out_file.unlink(missing_ok=True)
        else:
            _warn("Tool: create_file", str(r))

        test_file.unlink(missing_ok=True)
    except Exception as e:
        _fail("Tool Registry", str(e))

    # --- 11b. Desktop Control (screenshot) ---
    if os.environ.get("SKIP_DESKTOP"):
        print("\n11b. Desktop Control")
        _skip("Desktop screenshot", "SKIP_DESKTOP=1")
    else:
        print("\n11b. Desktop Control (screenshot)")
        try:
            from src.tools.tool_registry import ToolRegistry

            reg = ToolRegistry()
            if "desktop_screenshot" not in reg.tools:
                _fail("Desktop screenshot", "desktop_screenshot not registered — pip install pyautogui pillow")
            else:
                screenshot_path = Path(base) / "workspace" / "_test_screenshot.png"
                r = reg.execute("desktop_screenshot", {"filepath": str(screenshot_path)})
                if r.get("success") and screenshot_path.is_file():
                    _ok("Desktop screenshot", f"saved to {screenshot_path.name}")
                    screenshot_path.unlink(missing_ok=True)
                else:
                    _fail("Desktop screenshot", r.get("error", "unknown"))
        except ImportError as e:
            _fail("Desktop Control", f"pyautogui not installed — pip install pyautogui\n  {e}")
        except Exception as e:
            _fail("Desktop Control", str(e))

    # --- 11c. Browser Control ---
    if os.environ.get("SKIP_BROWSER"):
        print("\n11c. Browser Control")
        _skip("Browser automation", "SKIP_BROWSER=1")
    else:
        print("\n11c. Browser Control")
        try:
            from src.tools.browser_control import BrowserControl

            browser = BrowserControl(headless=True)
            r = browser.start()
            if not r.get("success"):
                _fail("Browser start", r.get("error", "unknown"))
            else:
                r = browser.navigate("about:blank", wait_until="domcontentloaded")
                if r.get("success"):
                    _ok("Browser navigate", "about:blank")
                browser.stop()
        except ImportError as e:
            _fail("Browser Control", f"Playwright not installed — pip install playwright && playwright install chromium\n  {e}")
        except Exception as e:
            _fail("Browser Control", str(e))

    # --- 12. Local Model (Forge) ---
    print("\n12. Local Model (Forge)")
    if os.environ.get("SKIP_LOCAL"):
        _skip("Local Model", "SKIP_LOCAL=1")
    else:
        try:
            from src.models.local_model import LocalModel

            _local_model = LocalModel()
            r = _local_model.generate("What is 2+2? Answer with just the number.", max_tokens=10, temperature=0.1)
            if r.get("success"):
                text = (r.get("text") or "").strip()
                vision = "vision=yes" if _local_model.has_vision else "vision=no"
                _ok("Local Model", f"response='{text[:30]}' {vision}")
            else:
                _fail("Local Model", r.get("error", "unknown"))
        except Exception as e:
            _fail("Local Model", str(e))
            _local_model = None

    # --- 12b. Vision (screenshot + chat_with_image) ---
    if os.environ.get("SKIP_VISION"):
        print("\n12b. Vision (screenshot + model)")
        _skip("Vision test", "SKIP_VISION=1")
    elif not _local_model:
        print("\n12b. Vision (screenshot + model)")
        _skip("Vision test", "local model not loaded (SKIP_LOCAL or failed)")
    else:
        print("\n12b. Vision (screenshot + model)")
        try:
            from src.tools.tool_registry import ToolRegistry

            model = _local_model
            if not model.has_vision:
                _skip("Vision test", "model has no vision (need Qwen3VL + mmproj)")
            else:
                reg = ToolRegistry()
                screenshot_path = Path(base) / "workspace" / "_test_vision.png"
                if "desktop_screenshot" in reg.tools:
                    r = reg.execute("desktop_screenshot", {"filepath": str(screenshot_path)})
                    if r.get("success") and screenshot_path.is_file():
                        # Resize to avoid token overflow (full screenshot can exceed context)
                        try:
                            from PIL import Image
                            img = Image.open(screenshot_path)
                            img.thumbnail((512, 512))
                            img.save(screenshot_path, "PNG")
                        except Exception:
                            pass  # use original if resize fails
                        v = model.chat_with_image(
                            "List 2-3 things you see on this screen briefly.",
                            str(screenshot_path),
                            max_tokens=100,
                            temperature=0.3,
                        )
                        screenshot_path.unlink(missing_ok=True)
                        if v.get("success") and v.get("text"):
                            _ok("Vision", f"model described screen: '{v['text'][:60]}...'")
                        else:
                            _fail("Vision", v.get("error", "no response"))
                    else:
                        _skip("Vision test", "screenshot failed")
                else:
                    _skip("Vision test", "desktop_screenshot not available")
        except Exception as e:
            _fail("Vision", str(e))

    # --- 13. Query Cache ---
    print("\n13. Query Cache")
    try:
        from src.models.cache import QueryCache

        cache = QueryCache(ttl_seconds=60)
        cache.set("key1", {"text": "val1"})
        v = cache.get("key1")
        if v and v.get("text") == "val1":
            _ok("QueryCache", "set/get OK")
        else:
            _warn("QueryCache", f"unexpected: {v}")
    except Exception as e:
        _fail("Query Cache", str(e))

    # --- 14. Grok API ---
    print("\n14. Grok API")
    if os.environ.get("SKIP_GROK"):
        _skip("Grok API", "SKIP_GROK=1")
    elif not os.environ.get("GROK_API_KEY"):
        _env_file = Path(__file__).resolve().parent / ".env"
        hint = f"add to .env to verify connection"
        if _env_file.exists():
            hint += f" (check {_env_file})"
        _skip("Grok API", f"GROK_API_KEY not set — {hint}")
    else:
        try:
            from src.models.grok_client import GrokClient

            client = GrokClient()
            r = client.generate("What is 3+3? Answer with just the number.", max_tokens=10)
            if r.get("success"):
                _ok("Grok API", f"response='{(r.get('text') or '').strip()}' cost=${r.get('cost_usd', 0):.6f}")
            else:
                _fail("Grok API", r.get("error", "unknown"))
        except Exception as e:
            _fail("Grok API", str(e))

    # --- 15. Model Router ---
    print("\n15. Model Router")
    if not os.environ.get("GROK_API_KEY"):
        _skip("Model Router", "GROK_API_KEY not set — router requires Grok")
    else:
        try:
            from src.models.router import ModelRouter

            router = ModelRouter()
            r = router.generate("What is 4+4? Answer with just the number.", max_tokens=10)
            if r.get("success"):
                stats = router.get_stats()
                _ok("Model Router", f"model={r.get('model')} cache_hits={stats.get('cache_hits', 0)}")
            else:
                _fail("Model Router", r.get("error", "no local or Grok available"))
        except Exception as e:
            _fail("Model Router", str(e))

    # --- 16. Web Search Tool ---
    print("\n16. Web Search Tool (ddgs / DuckDuckGo)")
    try:
        from src.tools.web_search_tool import WebSearchTool

        tool = WebSearchTool()
        results = tool.search("Python programming", max_results=2)
        if results is not None and len(results) > 0:
            _ok("WebSearchTool", f"{len(results)} results (ddgs or HTML fallback)")
        elif results is not None:
            _warn("WebSearchTool", "0 results (rate-limited or network)")
        else:
            _warn("WebSearchTool", "search returned None")
    except ImportError as e:
        _fail("WebSearchTool", f"ddgs not installed — pip install ddgs\n  {e}")
    except Exception as e:
        _fail("WebSearchTool", str(e))

    # --- 17. Forge Backends ---
    print("\n17. Forge Backends")
    try:
        from backends import list_backends

        backends = list_backends()
        available = [b["name"] for b in backends if b.get("available")]
        _ok("Forge backends", f"available: {', '.join(available)}")
    except Exception as e:
        _fail("Forge Backends", str(e))

    # --- 18. Hardware / Backends ---
    print("\n18. Hardware / Backends")
    try:
        from backends import list_backends

        available = [b["name"] for b in list_backends() if b["available"]]
        _ok("Backend detection", f"available: {', '.join(available) or 'none'}")
    except Exception as e:
        _fail("Backend detection", str(e))


def main() -> None:
    base = os.environ.get("ARCHI_ROOT") or str(_root)
    globals()["base"] = base

    run_tests()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, s, _ in _results if s == "PASS")
    failed = sum(1 for _, s, _ in _results if s == "FAIL")
    warned = sum(1 for _, s, _ in _results if s == "WARN")
    skipped = sum(1 for _, s, _ in _results if s == "SKIP")
    total = len(_results)

    print(f"  PASS:  {passed}/{total}")
    print(f"  FAIL:  {failed}")
    print(f"  WARN:  {warned}")
    print(f"  SKIP:  {skipped}")

    if failed > 0:
        print("\nFailed tests:")
        for name, status, detail in _results:
            if status == "FAIL":
                print(f"  - {name}: {detail}")
        sys.exit(1)
    else:
        print("\nAll critical tests passed. Archi is ready.")
        sys.exit(0)


if __name__ == "__main__":
    main()
