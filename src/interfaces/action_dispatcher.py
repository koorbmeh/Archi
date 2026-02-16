"""Action dispatch registry.

Each action handler takes (params, context) and returns a result string.
Context is a dict with: router, goal_manager, source, effective_message,
system_prompt, history_messages, progress_callback, etc.
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from src.utils.text_cleaning import sanitize_identity
from src.interfaces.response_builder import trace

logger = logging.getLogger(__name__)


def dispatch(action_type: str, params: dict, context: dict) -> Tuple[str, List[Dict], float]:
    """Execute an action and return (response_text, actions_taken, cost).

    Looks up the handler in ACTION_HANDLERS. Falls back to chat if unknown.
    """
    handler = ACTION_HANDLERS.get(action_type, _handle_unknown)
    return handler(params, context)


# ---- Individual action handlers ----

def _handle_chat(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Return the model's chat response (already in params)."""
    raw = params.get("response", "")
    response = sanitize_identity(raw)
    trace(f"chat raw={raw[:60]!r} sanitized={response[:60]!r}")

    # Reject chat that falsely claims work was done
    if _is_chat_claiming_action_done(response):
        logger.warning("Chat claims work done without execution: %s", (response or "")[:80])
        response = ("I apologize — I didn't actually execute that. I can create files, "
                     "click, or open URLs when you ask explicitly; would you like me to do that now?")

    return (response or "I'm not sure how to respond.", [], 0.0)


def _handle_search(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Execute a web search and summarize results."""
    router = ctx["router"]
    query = (params.get("query") or ctx.get("effective_message", "")).strip()
    cost = 0.0

    if not query:
        return ("I'd search for that, but I couldn't determine the query.", [], 0.0)

    # Improve commodity/metals price queries
    query_lower = query.lower()
    if any(x in query_lower for x in ("spot price", "price of", "price for", "current price")):
        if "silver" in query_lower and "ounce" not in query_lower:
            query = f"{query} per ounce USD today"
        elif "gold" in query_lower and "ounce" not in query_lower:
            query = f"{query} per ounce USD today"
        elif "oil" in query_lower and "barrel" not in query_lower:
            query = f"{query} per barrel USD today"

    try:
        from src.tools.tool_registry import ToolRegistry
        tools = ToolRegistry()
        result = tools.execute("web_search", {"query": query, "max_results": 5})
        if not result.get("success"):
            return (result.get("error") or f"No results for '{query}'.", [], 0.0)

        search_context = result.get("formatted", "No search results found.")
        answer_prompt = (
            f"Use these search results to answer the question. Be concise.\n\n"
            f"Search Results:\n{search_context}\n\n"
            f"Question: {query}\n\nAnswer:"
        )
        answer_resp = router.generate(
            prompt=answer_prompt, max_tokens=300, temperature=0.2,
            skip_web_search=True,
        )
        cost += answer_resp.get("cost_usd", 0)
        raw = sanitize_identity(answer_resp.get("text", "").strip())
        return (raw or "No answer found in search results.", [], cost)

    except ImportError:
        return ("Web search is not available. Install: pip install ddgs", [], 0.0)
    except Exception as e:
        logger.exception("Search failed: %s", e)
        return (f"Search error: {e}", [], 0.0)


def _handle_create_file(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Create a file in workspace."""
    rel_path = (params.get("path") or "").strip()
    content = params.get("content", "")
    actions = []

    if not rel_path:
        return ("I'd create a file, but no path was specified.", actions, 0.0)

    try:
        full_path = _workspace_path(rel_path)
    except ValueError as e:
        return (str(e), actions, 0.0)

    try:
        from src.core.safety_controller import SafetyController
        sc = SafetyController()
        auth = sc.authorize_action("create_file", {"path": full_path, "content": content})
        if not auth.get("allowed", True):
            return (f"Safety blocked: {auth.get('reason', 'unknown')}", actions, 0.0)
    except Exception:
        pass

    try:
        from src.tools.tool_registry import ToolRegistry
        tools = ToolRegistry()
        result = tools.execute("file_write", {"path": full_path, "content": content})
        if result.get("success"):
            # Verify file actually exists
            if os.path.isfile(full_path):
                actions.append({"description": f"Created file: {rel_path}", "result": result})
                return (f"Created `{rel_path}` ({len(content)} chars).", actions, 0.0)
            else:
                return (f"Tool reported success but file not found at {rel_path}.", actions, 0.0)
        return (f"File creation failed: {result.get('error', 'unknown')}", actions, 0.0)
    except Exception as e:
        logger.exception("Create file failed: %s", e)
        return (f"File creation error: {e}", actions, 0.0)


def _handle_list_files(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """List directory contents."""
    rel_path = (params.get("path") or "").strip().rstrip("/")
    if not rel_path:
        rel_path = "."
    actions = []

    try:
        from src.core.plan_executor import _resolve_project_path
        full_path = _resolve_project_path(rel_path)
        if not os.path.isdir(full_path):
            return (f"'{rel_path}' is not a directory or doesn't exist.", actions, 0.0)
        entries = sorted(os.listdir(full_path))
        if not entries:
            return (f"The directory '{rel_path}' is empty.", actions, 0.0)

        lines = [f"Contents of {rel_path}/ ({len(entries)} items):"]
        for e in entries[:50]:
            ep = os.path.join(full_path, e)
            marker = "📁" if os.path.isdir(ep) else "📄"
            lines.append(f"  {marker} {e}")
        if len(entries) > 50:
            lines.append(f"  ... and {len(entries) - 50} more")
        out = "\n".join(lines)
        actions.append({"description": f"Listed files in: {rel_path}/", "result": {"success": True}})
        return (out, actions, 0.0)
    except Exception as e:
        logger.exception("List files failed: %s", e)
        return (f"Couldn't list '{rel_path}': {e}", actions, 0.0)


def _handle_read_file(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Read a file's contents."""
    rel_path = (params.get("path") or "").strip()
    actions = []

    if not rel_path:
        return ("I'd read a file, but I need a path. Which file?", actions, 0.0)

    try:
        from src.core.plan_executor import _resolve_project_path
        full_path = _resolve_project_path(rel_path)
        if not os.path.isfile(full_path):
            return (f"File not found: '{rel_path}'", actions, 0.0)
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(5000)
        if not content:
            out = f"File '{rel_path}' is empty."
        else:
            truncated = " (truncated)" if len(content) >= 5000 else ""
            out = f"Contents of {rel_path}{truncated}:\n```\n{content}\n```"
        actions.append({"description": f"Read file: {rel_path}", "result": {"success": True}})
        return (out, actions, 0.0)
    except Exception as e:
        logger.exception("Read file failed: %s", e)
        return (f"Couldn't read '{rel_path}': {e}", actions, 0.0)


def _handle_create_goal(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Create a goal."""
    goal_manager = ctx.get("goal_manager")
    source = ctx.get("source", "unknown")
    desc = (params.get("description") or ctx.get("effective_message", "")).strip()

    if not goal_manager:
        return ("Goal creation not available here. Use /goal in Discord.", [], 0.0)
    if not desc:
        return ("I'd create a goal, but couldn't determine what to do. Try: /goal <description>", [], 0.0)

    try:
        goal_manager.create_goal(description=desc, user_intent=f"User request via {source}", priority=5)
        return (f'Got it. Goal added: "{desc}"\n\nI\'ll work on this during my next dream cycle (when idle 5+ min).', [], 0.0)
    except Exception as e:
        logger.exception("Goal creation failed: %s", e)
        return (f"Couldn't create goal: {e}", [], 0.0)


def _handle_generate_image(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Generate an image using SDXL."""
    router = ctx["router"]
    prompt = (params.get("prompt") or ctx.get("effective_message", "")).strip()[:500]
    actions = []

    if not prompt:
        return ("I'd generate an image, but I need a description.", actions, 0.0)

    result = router.generate_image(prompt)
    if result.get("success"):
        path = result.get("image_path", "unknown")
        gen_time = result.get("generation_time", 0)
        actions.append({"description": f"Generated image: {prompt[:40]}", "result": result})
        return (f"Image generated: `{path}` ({gen_time:.1f}s)", actions, 0.0)
    return (f"Image generation failed: {result.get('error', 'unknown')}", actions, 0.0)


def _handle_click(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Click a UI element."""
    target = (params.get("target") or "").strip()
    actions = []

    if not target:
        return ("I'd click something, but what should I click?", actions, 0.0)

    # Normalize common targets
    target_lower = target.lower()
    if "start" in target_lower and any(w in target_lower for w in ("windows", "menu")):
        target = "Windows Start button"

    try:
        from src.tools.tool_registry import ToolRegistry
        tools = ToolRegistry()
        result = tools.execute("desktop_click_element", {"target": target})
        if result.get("success"):
            method = result.get("method", "click")
            actions.append({"description": f"Clicked: {target}", "result": result})
            return (f"Clicked '{target}' (method: {method}).", actions, 0.0)
        return (f"Couldn't click '{target}': {result.get('error', 'unknown')}", actions, 0.0)
    except ImportError:
        return ("Desktop automation is not available.", actions, 0.0)
    except Exception as e:
        logger.exception("Click failed: %s", e)
        return (f"Click error: {e}", actions, 0.0)


def _handle_screenshot(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Take a screenshot and return the file path for Discord to send."""
    actions = []
    try:
        from src.tools.tool_registry import ToolRegistry

        tools = ToolRegistry()
        # Save to workspace so it persists
        import time

        fname = f"screenshot_{int(time.time())}.png"
        workspace = os.environ.get("ARCHI_ROOT", os.getcwd())
        save_dir = os.path.join(workspace, "workspace", "screenshots")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, fname)

        result = tools.execute("desktop_screenshot", {"filepath": save_path})
        if result.get("success"):
            actions.append({"description": "Screenshot taken", "result": {"image_path": save_path}})
            return (f"Here's a screenshot of the current screen.", actions, 0.0)
        return (f"Screenshot failed: {result.get('error', 'unknown')}", actions, 0.0)
    except ImportError:
        return ("Desktop automation is not available — pyautogui may not be installed.", actions, 0.0)
    except Exception as e:
        logger.exception("Screenshot failed: %s", e)
        return (f"Screenshot error: {e}", actions, 0.0)


def _handle_browser_navigate(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Navigate to a URL."""
    url = (params.get("url") or "").strip()
    actions = []

    if not url:
        return ("I'd open a URL, but none was specified.", actions, 0.0)

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        shortcuts = {"google": "https://www.google.com", "youtube": "https://www.youtube.com",
                      "github": "https://github.com", "reddit": "https://www.reddit.com"}
        url = shortcuts.get(url.lower(), f"https://{url}")

    try:
        from src.tools.tool_registry import ToolRegistry
        tools = ToolRegistry()
        result = tools.execute("browser_navigate", {"url": url})
        if result.get("success"):
            actions.append({"description": f"Navigated to: {url}", "result": result})
            return (f"Opened {url}", actions, 0.0)
        return (f"Couldn't open {url}: {result.get('error', 'unknown')}", actions, 0.0)
    except ImportError:
        return ("Browser automation is not available.", actions, 0.0)
    except Exception as e:
        logger.exception("Browser navigate failed: %s", e)
        return (f"Navigation error: {e}", actions, 0.0)


def _handle_fetch_webpage(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Fetch and summarize a webpage."""
    router = ctx["router"]
    url = (params.get("url") or "").strip()
    cost = 0.0
    actions = []

    if not url:
        return ("I'd fetch a webpage, but no URL was specified.", actions, 0.0)
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        text = _fetch_url_text(url)
        if not text:
            return (f"Fetched {url} but couldn't extract meaningful text.", actions, 0.0)

        summary_prompt = f"Summarize concisely:\n\n{text[:3000]}\n\nSummary:"
        resp = router.generate(prompt=summary_prompt, max_tokens=300, temperature=0.3,
                               skip_web_search=True)
        cost += resp.get("cost_usd", 0)
        summary = sanitize_identity(resp.get("text", "").strip())
        actions.append({"description": f"Fetched webpage: {url}", "result": {"success": True}})
        return (summary or f"Fetched {url} but couldn't summarize.", actions, cost)
    except Exception as e:
        logger.exception("Fetch webpage failed: %s", e)
        return (f"Couldn't fetch {url}: {e}", actions, 0.0)


def _handle_unknown(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Fallback: respond conversationally using the model."""
    router = ctx["router"]
    system_prompt = ctx.get("system_prompt", "")
    history_messages = ctx.get("history_messages", [])
    effective_message = ctx.get("effective_message", "")
    cost = 0.0

    instruction = ("Respond naturally as Archi. Use conversation history for context. "
                    "NEVER claim you created files, clicked, or opened URLs unless you actually executed those actions.")
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history_messages)
    messages.append({"role": "user", "content": f"{effective_message}\n\n{instruction}"})

    resp = router.generate(max_tokens=500, temperature=0.7, messages=messages)
    cost += resp.get("cost_usd", 0)
    out = sanitize_identity(resp.get("text", "").strip())
    return (out or "I'm not sure how to respond.", [], cost)


# ---- Handler registry ----

ACTION_HANDLERS = {
    "chat": _handle_chat,
    "search": _handle_search,
    "create_file": _handle_create_file,
    "list_files": _handle_list_files,
    "read_file": _handle_read_file,
    "create_goal": _handle_create_goal,
    "generate_image": _handle_generate_image,
    "screenshot": _handle_screenshot,
    "click": _handle_click,
    "browser_navigate": _handle_browser_navigate,
    "fetch_webpage": _handle_fetch_webpage,
}


# ---- Shared utilities ----

def _is_chat_claiming_action_done(response: str) -> bool:
    """Detect false claims of completed work without execution."""
    if not response:
        return False
    lower = response.lower()
    _CLAIM_PHRASES = (
        "i created the file", "i've created", "i have created the file",
        "files are all in workspace", "draft outline ready", "done! i clicked",
        "i've navigated", "i have navigated", "i opened", "i've opened the",
        "i clicked on", "i've clicked", "here's the file i created",
        "i successfully created", "the file has been created",
        "i've written the file", "i wrote the file",
    )
    return any(phrase in lower for phrase in _CLAIM_PHRASES)


def _workspace_path(rel_path: str) -> str:
    """Resolve relative path to full workspace path; prevent directory traversal."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    workspace = root / "workspace"
    if not rel_path.startswith("workspace"):
        rel_path = f"workspace/{rel_path.lstrip('/')}"
    full = (root / rel_path).resolve()
    if not str(full).startswith(str(workspace)):
        raise ValueError(f"Path escapes workspace: {rel_path}")
    return str(full)


def _fetch_url_text(url: str, max_chars: int = 3000) -> str:
    """Fetch and extract text from a URL."""
    try:
        from src.tools.tool_registry import ToolRegistry
        tools = ToolRegistry()
        result = tools.execute("web_search", {"query": f"site:{url}", "max_results": 1})
        # Direct fetch fallback
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Archi/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # Crude HTML stripping
        import re
        text = re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        logger.debug("URL fetch failed: %s", e)
        return ""
