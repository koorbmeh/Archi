"""Action dispatch registry.

Each action handler takes (params, context) and returns a result string.
Context is a dict with: router, goal_manager, source, effective_message,
system_prompt, history_messages, progress_callback, etc.
"""

import logging
import os
import re
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
        from src.tools.tool_registry import get_shared_registry
        tools = get_shared_registry()
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
        from src.tools.tool_registry import get_shared_registry
        tools = get_shared_registry()
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
        goal = goal_manager.create_goal(description=desc, user_intent=f"User request via {source}", priority=5)
        if goal is None:
            return ("Already working on that — I'll let you know when it's done.", [], 0.0)
        # Submit directly to worker pool for zero-latency start
        try:
            from src.interfaces.discord_bot import kick_heartbeat
            kick_heartbeat(goal.goal_id, reactive=True)
        except Exception:
            pass
        # Keep the response short and human. Truncate the description to
        # a brief label — the user already knows what they asked for.
        _label = desc.split(".")[0].split(":")[0].strip()
        if len(_label) > 80:
            _label = _label[:77] + "…"
        response = f"On it — I'll work on that in the background."
        return (response, [], 0.0)
    except Exception as e:
        logger.exception("Goal creation failed: %s", e)
        return (f"Couldn't create goal: {e}", [], 0.0)


def _handle_create_skill(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Create a new skill via the skill creator pipeline."""
    router = ctx.get("router")
    desc = (params.get("description") or "").strip()
    actions: list = []

    if not desc:
        return ("I'd create a skill, but I need a description. Try: /skill create <description>", actions, 0.0)
    if not router:
        return ("Skill creation requires a model connection.", actions, 0.0)

    try:
        from src.core.skill_creator import SkillCreator
        creator = SkillCreator()
        proposal = creator.create_skill_from_request(desc, router)
        if not proposal:
            return (f"Couldn't generate a valid skill for '{desc}'. The code failed validation.", actions, 0.0)

        success = creator.finalize_skill(proposal)
        if not success:
            return (f"Skill code was generated but failed final validation. Try a simpler description.", actions, 0.0)

        # Record in learning system
        try:
            from src.core.learning_system import LearningSystem
            ls = LearningSystem()
            ls.record_skill_created(proposal.name, desc)
        except Exception:
            pass

        actions.append({"description": f"Created skill: skill_{proposal.name}", "result": {"success": True}})
        return (
            f"Created skill `skill_{proposal.name}` — it's ready to use! "
            f"Try `/skill list` to see it, or I can invoke it automatically during tasks.",
            actions, 0.0,
        )
    except Exception as e:
        logger.exception("Skill creation failed: %s", e)
        return (f"Skill creation failed: {e}", actions, 0.0)


def _handle_generate_image(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Generate one or more images using SDXL."""
    router = ctx["router"]
    prompt = (params.get("prompt") or ctx.get("effective_message", "")).strip()[:500]
    count = min(int(params.get("count", 1)), 10)  # cap at 10
    actions = []

    if not prompt:
        return ("I'd generate an image, but I need a description.", actions, 0.0)

    # Model selection: explicit request → "auto" means use configured default
    model = params.get("model")
    if model == "auto" or not model:
        model = None  # let image_gen resolve from default/auto-discovery

    # Progress callback for multi-image generation
    progress_cb = ctx.get("progress_callback")

    results = []
    batch_mode = count > 1  # keep pipeline loaded for multi-image requests
    try:
        for i in range(count):
            if progress_cb and count > 1:
                progress_cb(i + 1, count, f"Generating image {i + 1}/{count}...")
            result = router.generate_image(
                prompt, model=model,
                keep_loaded=batch_mode and (i < count - 1),  # keep loaded until last image
            )
            results.append(result)
            if not result.get("success"):
                # Stop on first failure — no point continuing if pipeline is broken
                break
    finally:
        # Always ensure pipeline is unloaded after batch
        if batch_mode:
            router.finish_image_batch()

    successes = [r for r in results if r.get("success")]
    failures = [r for r in results if not r.get("success")]

    if not successes:
        error = failures[0].get("error", "unknown") if failures else "unknown"
        return (f"Image generation failed: {error}", actions, 0.0)

    paths = [r.get("image_path", "?") for r in successes]
    for r in successes:
        actions.append({"description": f"Generated image: {prompt[:40]}", "result": r})

    model_name = successes[0].get("model_used", "")
    model_label = f" [{model_name}]" if model_name else ""

    if len(paths) == 1:
        gen_ms = successes[0].get("duration_ms", 0)
        return (f"Image generated{model_label}: `{paths[0]}` ({gen_ms / 1000:.1f}s)", actions, 0.0)

    lines = [f"Generated {len(paths)} images{model_label}:"]
    for p in paths:
        lines.append(f"  `{p}`")
    if failures:
        lines.append(f"({len(failures)} failed)")
    return ("\n".join(lines), actions, 0.0)


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
        from src.tools.tool_registry import get_shared_registry
        tools = get_shared_registry()
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
        from src.tools.tool_registry import get_shared_registry

        tools = get_shared_registry()
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
        from src.tools.tool_registry import get_shared_registry
        tools = get_shared_registry()
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


def _extract_file_path_from_context(effective_message: str) -> Optional[str]:
    """Extract a file path from reply context in the effective message.

    Looks for file paths in patterns like:
    - "Files created: filename.ext"
    - "workspace/projects/filename.ext"
    - Any word with a file extension (e.g. "report.md", "data.json")
    """
    if not effective_message:
        return None

    # Extract the reply context portion
    match = re.search(r'\[Replying to Archi\'s message: "(.+?)"\]', effective_message, re.DOTALL)
    if not match:
        return None
    reply_text = match.group(1)

    return _find_file_path_in_text(reply_text)


def _find_file_path_in_text(text: str) -> Optional[str]:
    """Find a file path in arbitrary text. Shared by reply-context and history search."""
    if not text:
        return None

    path_patterns = [
        r'(workspace/\S+\.\w{1,5})',       # workspace/projects/file.ext
        r'(\S+/\S+\.\w{1,5})',             # any/path/file.ext
        r'(?:Files? created:?\s*)(\S+\.\w{1,5})',  # "Files created: file.ext"
        r'(?:created|produced|wrote|saved)\s+`?(\S+\.\w{1,5})`?',  # "created file.ext"
        r'`(\S+\.\w{1,5})`',               # `filename.ext` in backticks
        r'\b(\w[\w\-. ]*\.\w{1,5})\b',     # standalone filename.ext
    ]

    for pattern in path_patterns:
        found = re.search(pattern, text, re.IGNORECASE)
        if found:
            path = found.group(1).strip('`"\',.')
            if path.lower() in ("e.g", "i.e", "etc", "vs"):
                continue
            return path
    return None


def _extract_file_path_from_history(history_messages: List[dict]) -> Optional[str]:
    """Search recent conversation history for file paths mentioned by Archi.

    Walks backward through Archi's recent messages looking for file paths,
    so "send me the file" works even without a Discord reply context.
    """
    if not history_messages:
        return None

    # Search Archi's messages in reverse (most recent first), limit to last 6
    for msg in reversed(history_messages[-6:]):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content") or ""
        path = _find_file_path_in_text(content)
        if path:
            logger.info("send_file: found path '%s' in conversation history", path)
            return path
    return None


def _filetracker_fuzzy_lookup(search_text: str) -> Optional[str]:
    """Try to resolve a file path via FileTracker fuzzy keyword matching.

    Session 230: FileTracker knows about every file Archi has created and
    can match against the actual manifest on disk, avoiding hallucinated
    filenames from regex extraction or LLM params.
    """
    if not search_text:
        return None
    try:
        from src.core.file_tracker import FileTracker
        tracker = FileTracker()
        candidates = tracker.get_files_by_keywords(search_text)
        if candidates:
            from src.core.plan_executor import _resolve_project_path
            for c in candidates:
                resolved = _resolve_project_path(c)
                if os.path.isfile(resolved):
                    logger.info("send_file: FileTracker matched '%s'", c)
                    return c
    except Exception as ft_err:
        logger.debug("FileTracker lookup failed: %s", ft_err)
    return None


def _handle_send_file(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Send a file as a Discord attachment."""
    rel_path = (params.get("path") or "").strip()
    actions = []

    # If no path in params, try to extract from reply context
    if not rel_path:
        effective = ctx.get("effective_message", "")
        extracted = _extract_file_path_from_context(effective)
        if extracted:
            rel_path = extracted
            logger.info("send_file: extracted path '%s' from reply context", rel_path)

    # Fallback: search recent conversation history for file paths
    if not rel_path:
        extracted = _extract_file_path_from_history(ctx.get("history_messages", []))
        if extracted:
            rel_path = extracted

    # Session 230: FileTracker fallback — if we still have no path, or the
    # path doesn't resolve, try fuzzy matching against the file manifest
    # using the user's original message + recent context as search text.
    if not rel_path:
        _search = (params.get("path") or "") + " " + ctx.get("effective_message", "")
        rel_path = _filetracker_fuzzy_lookup(_search) or ""

    if not rel_path:
        return ("I'd send a file, but I need a path. Which file?", actions, 0.0)

    try:
        from src.core.plan_executor import _resolve_project_path
        full_path = _resolve_project_path(rel_path)
        if not os.path.isfile(full_path):
            # Path from regex/LLM didn't resolve — try FileTracker as last resort
            _search = rel_path + " " + ctx.get("effective_message", "")
            ft_path = _filetracker_fuzzy_lookup(_search)
            if ft_path:
                rel_path = ft_path
                full_path = _resolve_project_path(rel_path)
            if not os.path.isfile(full_path):
                return (f"File not found: '{rel_path}'", actions, 0.0)

        from src.interfaces.discord_bot import send_notification
        success = send_notification(
            text=f"Here's `{os.path.basename(rel_path)}`:",
            file_path=rel_path,
        )
        if success:
            actions.append({"description": f"Sent file: {rel_path}", "result": {"success": True}})
            return (f"Sent `{os.path.basename(rel_path)}` as an attachment.", actions, 0.0)
        else:
            return (f"Discord isn't ready to send files right now. Try again in a moment.", actions, 0.0)
    except Exception as e:
        logger.exception("Send file failed: %s", e)
        return (f"Couldn't send '{rel_path}': {e}", actions, 0.0)


# ---- Schedule handlers (session 196) ----

def _handle_create_schedule(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Create a new scheduled task from conversational input."""
    from src.core.scheduler import create_task, slugify, validate_cron
    actions = []

    desc = (params.get("description") or "").strip()
    cron_expr = (params.get("cron") or "").strip()
    action = (params.get("action") or "notify").strip()
    payload = params.get("payload", desc)

    if not desc:
        return ("I'd set up a reminder, but I need to know what for.", actions, 0.0)
    if not cron_expr or not validate_cron(cron_expr):
        return ("I couldn't parse a valid schedule from that. Try something like "
                "'every day at 4:15' or 'every Monday at 9'.", actions, 0.0)

    task_id = params.get("task_id") or slugify(desc)
    task = create_task(
        task_id=task_id, description=desc, cron_expr=cron_expr,
        action=action, payload=payload, created_by="user",
    )
    if not task:
        return ("Couldn't create that schedule — it might already exist or the schedule is full.", actions, 0.0)

    from src.core.scheduler import format_friendly_time
    actions.append({"description": f"Created schedule: {task_id}", "result": {"success": True}})
    try:
        next_str = format_friendly_time(task.next_run_at)
    except Exception:
        next_str = task.next_run_at
    return (f"Got it — I'll {desc.lower()} on schedule. Next: {next_str}.", actions, 0.0)


def _handle_modify_schedule(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Modify an existing scheduled task."""
    from src.core.scheduler import modify_task
    actions = []

    task_id = (params.get("task_id") or "").strip()
    if not task_id:
        return ("Which scheduled task should I modify?", actions, 0.0)

    updates = {}
    if params.get("cron"):
        updates["cron"] = params["cron"]
    if params.get("description"):
        updates["description"] = params["description"]
    if params.get("payload"):
        updates["payload"] = params["payload"]
    if "enabled" in params:
        updates["enabled"] = params["enabled"]

    if not updates:
        return ("What would you like to change about that schedule?", actions, 0.0)

    task = modify_task(task_id, **updates)
    if not task:
        return (f"Couldn't find a schedule called '{task_id}'.", actions, 0.0)

    from src.core.scheduler import format_friendly_time
    actions.append({"description": f"Modified schedule: {task_id}", "result": {"success": True}})
    change_summary = ", ".join(updates.keys())
    try:
        next_str = format_friendly_time(task.next_run_at)
    except Exception:
        next_str = task.next_run_at
    return (f"Updated {task_id} ({change_summary}). Next: {next_str}.", actions, 0.0)


def _handle_remove_schedule(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Remove a scheduled task."""
    from src.core.scheduler import remove_task
    actions = []

    task_id = (params.get("task_id") or "").strip()
    if not task_id:
        return ("Which scheduled task should I remove?", actions, 0.0)

    if remove_task(task_id):
        actions.append({"description": f"Removed schedule: {task_id}", "result": {"success": True}})
        return (f"Done — removed the '{task_id}' schedule.", actions, 0.0)
    return (f"Couldn't find a schedule called '{task_id}'.", actions, 0.0)


def _handle_list_schedule(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """List all scheduled tasks."""
    from src.core.scheduler import list_tasks, format_task_list
    tasks = list_tasks()
    return (format_task_list(tasks), [], 0.0)


# ---- Email handlers ----

def _handle_send_email(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Send an email via Archi's email account.

    In dream mode (source=dream_cycle_queue), emails require Jesse's approval
    via Discord before sending. In chat mode, emails send immediately.
    """
    to = (params.get("to") or "").strip()
    subject = (params.get("subject") or "").strip()
    body = (params.get("body") or "").strip()
    if not to or not subject or not body:
        return ("I need a recipient (to), subject, and body to send an email.", [], 0.0)

    source = ctx.get("source", "unknown")

    # Dream mode: require approval before sending
    if source == "dream_cycle_queue":
        try:
            from src.interfaces.discord_bot import request_email_approval
            approved = request_email_approval(to, subject, body)
        except ImportError:
            approved = False
        if not approved:
            actions = [{"description": f"send_email to {to} (dream — denied)", "result": {"success": False, "error": "Approval denied or timed out."}}]
            return (f"Email to {to} was not sent — approval denied or timed out.", actions, 0.0)

    from src.tools.email_tool import send_email
    result = send_email(to, subject, body)
    actions = [{"description": f"send_email to {to}", "result": result}]
    if result.get("success"):
        return (f"Email sent to {to}: \"{subject}\"", actions, 0.0)
    return (result.get("error", "Failed to send email."), actions, 0.0)


def _handle_check_email(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Check Archi's inbox for recent/unread messages."""
    max_count = int(params.get("max_count", 5))
    unread_only = params.get("unread_only", True)

    from src.tools.email_tool import check_inbox
    result = check_inbox(max_count=max_count, unread_only=unread_only)
    actions = [{"description": "check_email", "result": {"success": result.get("success"), "count": result.get("count", 0)}}]

    if not result.get("success"):
        return (result.get("error", "Failed to check email."), actions, 0.0)

    messages = result.get("messages", [])
    if not messages:
        label = "unread" if unread_only else ""
        return (f"No {label} emails in the inbox.".strip(), actions, 0.0)

    # Format summary
    lines = [f"Found {len(messages)} email{'s' if len(messages) != 1 else ''}:"]
    for i, m in enumerate(messages, 1):
        lines.append(f"{i}. From: {m.get('from', '?')} — \"{m.get('subject', '(no subject)')}\" ({m.get('date', '')})")
        preview = m.get("preview", "")
        if preview:
            lines.append(f"   {preview[:150]}{'…' if len(preview) > 150 else ''}")
    return ("\n".join(lines), actions, 0.0)


def _handle_search_email(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Search Archi's inbox."""
    query = (params.get("query") or "").strip()
    if not query:
        return ("I need a search query to search emails.", [], 0.0)

    max_count = int(params.get("max_count", 5))

    from src.tools.email_tool import search_inbox
    result = search_inbox(query, max_count=max_count)
    actions = [{"description": f"search_email: {query}", "result": {"success": result.get("success"), "count": result.get("count", 0)}}]

    if not result.get("success"):
        return (result.get("error", "Failed to search email."), actions, 0.0)

    messages = result.get("messages", [])
    if not messages:
        return (f"No emails found matching '{query}'.", actions, 0.0)

    lines = [f"Found {len(messages)} email{'s' if len(messages) != 1 else ''} matching '{query}':"]
    for i, m in enumerate(messages, 1):
        lines.append(f"{i}. From: {m.get('from', '?')} — \"{m.get('subject', '(no subject)')}\" ({m.get('date', '')})")
    return ("\n".join(lines), actions, 0.0)


def _handle_morning_digest(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """On-demand daily briefing: weather + calendar + supplements + finance + email + news."""
    from src.core.morning_digest import gather_digest
    digest = gather_digest()
    actions = [{"description": "morning_digest", "result": {"success": True}}]

    parts = []
    if digest.get("weather"):
        parts.append(f"**Weather:** {digest['weather']}")
    if digest.get("calendar"):
        parts.append(f"**Calendar:**\n{digest['calendar']}")
    if digest.get("supplements"):
        parts.append(f"**Supplements:**\n{digest['supplements']}")
    if digest.get("finance"):
        parts.append(f"**Finances:**\n{digest['finance']}")
    if digest.get("email"):
        parts.append(f"**Inbox:**\n{digest['email']}")
    if digest.get("news"):
        parts.append(f"**Headlines:**\n{digest['news']}")

    if not parts:
        return ("Couldn't fetch any digest data right now. Try again in a bit.", actions, 0.0)

    return ("\n\n".join(parts), actions, 0.0)


def _handle_check_calendar(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """On-demand calendar check: upcoming events for today and tomorrow."""
    from src.utils.calendar_client import get_upcoming_events
    data = get_upcoming_events(days_ahead=2)
    actions = [{"description": "check_calendar", "result": {"success": True}}]

    summary = data.get("summary", "")
    events = data.get("events", [])

    if not events and not summary:
        return ("No calendar URLs configured. Set ARCHI_CALENDAR_URLS in .env or "
                "calendar_urls in archi_identity.yaml (ICS feed URLs).", actions, 0.0)
    if not events:
        return ("No upcoming events in the next 2 days.", actions, 0.0)

    return (f"**Calendar:**\n{summary}", actions, 0.0)


# ---- Content handlers (session 228) ----

def _handle_create_content(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Generate content (blog post, tweet, reddit post, video script) from a topic."""
    from src.tools.content_creator import generate_content
    router = ctx["router"]
    actions = []

    topic = (params.get("topic") or ctx.get("effective_message", "")).strip()
    fmt = (params.get("format") or "blog").strip().lower()
    extra = (params.get("extra_context") or "").strip()

    if not topic:
        return ("What should I write about?", actions, 0.0)

    result = generate_content(router, topic, content_format=fmt, extra_context=extra)
    if not result:
        return (f"Couldn't generate {fmt} content about '{topic}'. Try again?", actions, 0.0)

    cost = getattr(router, '_last_cost', 0.0) or 0.0
    actions.append({"description": f"Generated {fmt}: {topic[:60]}", "result": {"success": True}})

    # Format response based on content type
    content = result["content"]
    title = result.get("title", "")
    if fmt == "blog":
        resp = f"**Draft blog post: {title}**\n\n{content}\n\n_Use \"publish that to the blog\" to post it._"
    elif fmt in ("tweet", "tweet_thread"):
        resp = f"**Draft tweet:**\n\n{content}\n\n_Use \"publish that on Twitter\" to post it._"
    elif fmt == "reddit":
        resp = f"**Draft Reddit post: {title}**\n\n{content}\n\n_Use \"post that on Reddit in r/<subreddit>\" to publish._"
    elif fmt == "video_script":
        script_title = result.get("title", title)
        tags = result.get("tags", [])
        tags_str = ", ".join(tags[:10]) if tags else "none"
        resp = (f"**Video script: {script_title}**\n\n{content}\n\n"
                f"**Tags:** {tags_str}\n\n"
                f"_When you have a video file, use \"upload <path> to YouTube\" to publish._")
    else:
        resp = content

    # Store draft in context for follow-up publish
    ctx["_last_content_draft"] = result
    return (resp, actions, cost)


def _handle_publish_content(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Publish previously generated content to a platform."""
    from src.tools.content_creator import (
        publish_to_github_blog, publish_tweet, publish_tweet_thread,
        publish_reddit_post, publish_to_youtube, update_youtube_metadata,
        publish_to_facebook, publish_to_facebook_photo,
        publish_to_instagram, publish_to_instagram_carousel,
    )
    actions = []
    platform = (params.get("platform") or "").strip().lower()

    if not platform:
        return ("Which platform? I can publish to: github_blog, twitter, reddit, youtube, facebook, instagram.", actions, 0.0)

    # Check for dream-mode approval (same pattern as email)
    source = ctx.get("source", "")
    if source == "dream_cycle_queue":
        from src.interfaces.discord_bot import send_notification
        send_notification(text="I drafted some content but publishing from dream mode requires your approval. "
                              "Please tell me to publish it when you're ready.")
        return ("Content ready but awaiting user approval (dream mode).", actions, 0.0)

    # For now, content draft needs to come from params or recent context
    title = (params.get("title") or "").strip()
    body = (params.get("body") or "").strip()
    subreddit = (params.get("subreddit") or "").strip()

    if platform == "youtube":
        # YouTube needs a video file path + metadata
        video_path = (params.get("video_path") or "").strip()
        if not video_path:
            return ("I need a video file path to upload to YouTube. "
                    "Use: \"upload <path> to YouTube\" or provide video_path.", actions, 0.0)
        if not title:
            return ("I need a title for the YouTube video.", actions, 0.0)
        tags = params.get("tags", [])
        privacy = (params.get("privacy") or "private").strip().lower()
        result = publish_to_youtube(
            video_path=video_path, title=title, description=body,
            tags=tags, privacy_status=privacy,
        )
    elif not body:
        return ("No content to publish. Generate something first with \"write a blog post about X\".", actions, 0.0)
    elif platform == "github_blog":
        if not title:
            return ("I need a title for the blog post.", actions, 0.0)
        tags = params.get("tags", [])
        result = publish_to_github_blog(title, body, tags)
    elif platform == "twitter":
        # Check if it's a thread
        tweets = body.split("\n")
        tweets = [t.strip() for t in tweets if t.strip()]
        if len(tweets) > 1 and all(len(t) <= 280 for t in tweets):
            result = publish_tweet_thread(tweets)
        else:
            result = publish_tweet(body)
    elif platform == "reddit":
        if not subreddit:
            return ("Which subreddit? Use: \"post that on Reddit in r/<subreddit>\".", actions, 0.0)
        if not title:
            return ("I need a title for the Reddit post.", actions, 0.0)
        result = publish_reddit_post(subreddit, title, body)
    elif platform == "facebook":
        image_url = (params.get("image_url") or "").strip()
        link = (params.get("link") or "").strip()
        if image_url:
            result = publish_to_facebook_photo(image_url, caption=body)
        else:
            result = publish_to_facebook(message=body, link=link or None)
    elif platform == "instagram":
        image_url = (params.get("image_url") or "").strip()
        image_urls = params.get("image_urls", [])
        if image_urls and len(image_urls) >= 2:
            result = publish_to_instagram_carousel(image_urls, caption=body)
        elif image_url:
            result = publish_to_instagram(image_url, caption=body)
        else:
            return ("Instagram requires an image URL. Use: \"post that to Instagram\" "
                    "with an image_url parameter.", actions, 0.0)
    else:
        return (f"Unknown platform '{platform}'. Options: github_blog, twitter, reddit, youtube, facebook, instagram.", actions, 0.0)

    if result.get("success"):
        url = result.get("url", "")
        actions.append({"description": f"Published to {platform}", "result": result})
        return (f"Published to {platform}! {url}", actions, 0.0)
    else:
        error = result.get("error", "Unknown error")
        return (f"Publishing to {platform} failed: {error}", actions, 0.0)


def _handle_list_content(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Show recent content activity."""
    from src.tools.content_creator import get_content_summary
    summary = get_content_summary()
    actions = [{"description": "list_content", "result": {"success": True}}]
    return (f"**Content log:**\n{summary}", actions, 0.0)


def _handle_deep_research(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Execute a deep research session on a topic."""
    router = ctx["router"]
    question = (params.get("query") or params.get("topic") or ctx.get("effective_message", "")).strip()
    if not question:
        return ("What would you like me to research?", [], 0.0)

    # Strip common prefixes
    for prefix in ("research ", "deep research ", "look into ", "investigate "):
        if question.lower().startswith(prefix):
            question = question[len(prefix):].strip()
            break

    context = params.get("context", "")
    from src.core.research_agent import ResearchAgent
    agent = ResearchAgent(router)
    try:
        result = agent.research(question=question, context=context)
        response = result.format_for_user()
        actions = [{"description": "deep_research", "result": result.to_dict()}]
        return (response, actions, result.total_cost)
    except Exception as e:
        logger.error("Deep research failed: %s", e)
        return (f"Research failed: {e}", [], 0.0)


def _handle_content_plan(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Plan a week of content across platforms."""
    from src.tools.content_calendar import ContentCalendar, format_week_plan
    cal = ContentCalendar()
    slots = cal.plan_week()
    response = format_week_plan(slots)
    return (response, [{"description": "content_plan", "slots": len(slots)}], 0.0)


def _handle_content_upcoming(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Show upcoming scheduled content."""
    from src.tools.content_calendar import ContentCalendar, format_upcoming
    cal = ContentCalendar()
    days = int(params.get("days", 7))
    slots = cal.get_upcoming(days=days)
    response = format_upcoming(slots)
    return (response, [], 0.0)


def _handle_content_schedule(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Schedule a single content item."""
    from src.tools.content_calendar import ContentCalendar
    cal = ContentCalendar()
    topic = (params.get("topic") or params.get("query") or "").strip()
    platform = (params.get("platform") or "twitter").strip().lower()

    if not topic:
        return ("What topic should I schedule? e.g., 'schedule a post about AI trends on twitter'", [], 0.0)

    slot = cal.queue_content(topic=topic, platform=platform)
    if not slot:
        return ("Failed to queue content. Check the logs.", [], 0.0)
    return (
        f"\u2705 Queued: **{topic[:60]}** on {platform}, "
        f"scheduled for {slot.scheduled_at[:16]}.",
        [{"description": "content_schedule", "slot_id": slot.slot_id}],
        0.0,
    )


def _handle_content_image(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Generate a content image for a topic/platform via SDXL (session 243)."""
    from src.tools.image_generator import generate_content_image, is_available

    if not is_available():
        return ("Image generation isn't available right now — no SDXL model found. "
                "Place an SDXL .safetensors file in models/ or set IMAGE_MODEL_PATH.", [], 0.0)

    topic = (params.get("topic") or params.get("query")
             or ctx.get("effective_message", "")).strip()
    platform = (params.get("platform") or "default").strip().lower()
    pillar = (params.get("pillar") or "").strip()
    overlay = (params.get("overlay_text") or "").strip()

    if not topic:
        return ("What should the image be about?", [], 0.0)

    result = generate_content_image(
        topic=topic, platform=platform, pillar=pillar, overlay_text=overlay,
    )
    if not result.get("success"):
        return (f"Image generation failed: {result.get('error', 'unknown')}", [], 0.0)

    path = result["image_path"]
    dur = result.get("duration_ms", 0)
    resp = (f"**Image generated** for *{topic[:60]}* ({platform})\n"
            f"Path: `{path}`\nTook {dur}ms")

    # Auto-upload for public URL if hosting is configured
    try:
        from src.tools.image_host import upload_for_platform, is_configured
        if is_configured():
            upload = upload_for_platform(path, platform=platform, topic=topic)
            if upload.get("success"):
                resp += f"\n**Public URL:** {upload['url']}"
    except Exception:
        pass  # Hosting is optional

    return (resp, [{"description": "content_image", "path": path}], 0.0)


def _handle_content_adapt(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Adapt content across platforms (Content Strategy Phase 5, session 241)."""
    from src.tools.content_creator import adapt_content, format_adaptation_summary
    router = ctx.get("router")
    if not router:
        return ("No model router available.", [], 0.0)

    source = (params.get("content") or params.get("source") or "").strip()
    source_format = (params.get("source_format") or params.get("format") or "blog").strip().lower()
    topic = (params.get("topic") or params.get("query") or "").strip()
    targets = params.get("target_platforms")

    if not source:
        return ("I need the source content to adapt. "
                "Try: 'adapt my latest blog post for twitter and instagram'", [], 0.0)

    results = adapt_content(
        router, source_content=source, source_format=source_format,
        target_platforms=targets, topic=topic,
    )
    response = format_adaptation_summary(results)
    successful = sum(1 for v in results.values() if v)
    return (response, [{"description": "content_adapt", "platforms": successful}], 0.0)


# ---- Supplement handlers (session 245) ----

def _handle_add_supplement(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Add a new supplement to the tracker."""
    from src.tools.supplement_tracker import get_tracker
    tracker = get_tracker()
    actions = []

    name = (params.get("name") or "").strip()
    if not name:
        return ("I need a supplement name. Try: \"add supplement creatine 5g daily\"",
                actions, 0.0)

    dose = (params.get("dose") or "").strip()
    frequency = (params.get("frequency") or "daily").strip().lower()
    time_of_day = (params.get("time_of_day") or "").strip()
    notes = (params.get("notes") or "").strip()
    stock_days = int(params.get("stock_days") or 0)

    supp = tracker.add_supplement(
        name=name, dose=dose, frequency=frequency,
        time_of_day=time_of_day, notes=notes, stock_days=stock_days,
    )
    actions.append({"description": "add_supplement", "result": {"name": supp.name}})
    return (f"Added **{supp.display_name()}** ({frequency}). "
            f"Now tracking {len(tracker.get_active())} supplements.",
            actions, 0.0)


def _handle_remove_supplement(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Remove (deactivate) a supplement from the tracker."""
    from src.tools.supplement_tracker import get_tracker
    tracker = get_tracker()

    name = (params.get("name") or "").strip()
    if not name:
        return ("Which supplement should I remove?", [], 0.0)

    if tracker.remove_supplement(name):
        return (f"Removed **{name}** from your supplement list.", [], 0.0)
    return (f"Couldn't find a supplement called \"{name}\".", [], 0.0)


def _handle_log_supplement(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Log supplement intake (taken/skipped)."""
    from src.tools.supplement_tracker import get_tracker
    tracker = get_tracker()

    name = (params.get("name") or "").strip()
    status = (params.get("status") or "taken").strip().lower()

    if not name or name.lower() == "all":
        entries = tracker.log_all_taken()
        if not entries:
            return ("No active supplements to log.", [], 0.0)
        names = ", ".join(e.supplement_name for e in entries)
        return (f"Logged {len(entries)} supplements as taken: {names}",
                [{"description": "log_supplement", "count": len(entries)}], 0.0)

    entry = tracker.log_intake(name, status=status)
    return (f"Logged **{entry.supplement_name}** as {entry.status}.",
            [{"description": "log_supplement"}], 0.0)


def _handle_supplement_status(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Show current supplement status — list, daily progress, or report."""
    from src.tools.supplement_tracker import get_tracker
    tracker = get_tracker()

    view = (params.get("view") or "status").strip().lower()

    if view == "list":
        return (tracker.format_supplement_list(), [], 0.0)
    elif view == "report":
        days = int(params.get("days") or 7)
        return (tracker.format_report(days), [], 0.0)
    else:  # status (default)
        return (tracker.format_daily_status(), [], 0.0)


def _handle_log_expense(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Log an expense."""
    from src.tools.finance_tracker import get_tracker
    tracker = get_tracker()

    amount = params.get("amount")
    if not amount:
        return ("I need an amount. Try: \"spent $50 on groceries\"", [], 0.0)
    try:
        amount = float(str(amount).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return (f"Couldn't parse amount: {amount}", [], 0.0)

    category = (params.get("category") or "other").strip()
    description = (params.get("description") or "").strip()

    expense = tracker.log_expense(amount, category, description)
    return (f"Logged **${expense.amount:.2f}** ({expense.category})"
            + (f" — {expense.description}" if expense.description else ""),
            [{"description": "log_expense"}], 0.0)


def _handle_add_subscription(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Add a recurring subscription."""
    from src.tools.finance_tracker import get_tracker
    tracker = get_tracker()

    name = (params.get("name") or "").strip()
    if not name:
        return ("I need a subscription name. Try: \"add subscription Netflix $15.99/month\"",
                [], 0.0)

    amount = params.get("amount")
    if not amount:
        return ("I need an amount for this subscription.", [], 0.0)
    try:
        amount = float(str(amount).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return (f"Couldn't parse amount: {amount}", [], 0.0)

    frequency = (params.get("frequency") or "monthly").strip()
    category = (params.get("category") or "subscriptions").strip()
    notes = (params.get("notes") or "").strip()

    sub = tracker.add_subscription(name, amount, frequency, category, notes)
    freq_label = {"monthly": "/mo", "yearly": "/yr", "weekly": "/wk"}
    freq = freq_label.get(sub.frequency, f"/{sub.frequency}")
    return (f"Added subscription: **{sub.name}** ${sub.amount:.2f}{freq}",
            [], 0.0)


def _handle_cancel_subscription(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Cancel a subscription."""
    from src.tools.finance_tracker import get_tracker
    tracker = get_tracker()

    name = (params.get("name") or "").strip()
    if not name:
        return ("Which subscription should I cancel?", [], 0.0)

    if tracker.cancel_subscription(name):
        return (f"Cancelled subscription: **{name}**", [], 0.0)
    return (f"Couldn't find a subscription called \"{name}\".", [], 0.0)


def _handle_set_budget(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Set a monthly spending budget."""
    from src.tools.finance_tracker import get_tracker
    tracker = get_tracker()

    category = (params.get("category") or "total").strip()
    limit = params.get("limit") or params.get("amount")
    if not limit:
        return ("I need a budget amount. Try: \"set budget groceries $500/month\"", [], 0.0)
    try:
        limit = float(str(limit).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return (f"Couldn't parse amount: {limit}", [], 0.0)

    budget = tracker.set_budget(category, limit)
    return (f"Budget set: **{budget.category}** — ${budget.monthly_limit:.2f}/month", [], 0.0)


def _handle_finance_status(params: dict, ctx: dict) -> Tuple[str, list, float]:
    """Show financial status — spending, subscriptions, budgets, or report."""
    from src.tools.finance_tracker import get_tracker
    tracker = get_tracker()

    view = (params.get("view") or "spending").strip().lower()

    if view == "subscriptions":
        return (tracker.format_subscription_list(), [], 0.0)
    elif view == "budget":
        return (tracker.format_budget_report(), [], 0.0)
    elif view == "report":
        return (tracker.format_monthly_report(), [], 0.0)
    else:  # spending (default)
        days = int(params.get("days") or 7)
        return (tracker.format_spending_summary(days), [], 0.0)


# ---- Handler registry ----

ACTION_HANDLERS = {
    "chat": _handle_chat,
    "search": _handle_search,
    "create_file": _handle_create_file,
    "list_files": _handle_list_files,
    "read_file": _handle_read_file,
    "send_file": _handle_send_file,
    "create_goal": _handle_create_goal,
    "create_skill": _handle_create_skill,
    "generate_image": _handle_generate_image,
    "screenshot": _handle_screenshot,
    "click": _handle_click,
    "browser_navigate": _handle_browser_navigate,
    "fetch_webpage": _handle_fetch_webpage,
    "create_schedule": _handle_create_schedule,
    "modify_schedule": _handle_modify_schedule,
    "remove_schedule": _handle_remove_schedule,
    "list_schedule": _handle_list_schedule,
    "send_email": _handle_send_email,
    "check_email": _handle_check_email,
    "search_email": _handle_search_email,
    "morning_digest": _handle_morning_digest,
    "check_calendar": _handle_check_calendar,
    "create_content": _handle_create_content,
    "publish_content": _handle_publish_content,
    "list_content": _handle_list_content,
    "deep_research": _handle_deep_research,
    "content_plan": _handle_content_plan,
    "content_upcoming": _handle_content_upcoming,
    "content_schedule": _handle_content_schedule,
    "content_adapt": _handle_content_adapt,
    "content_image": _handle_content_image,
    "add_supplement": _handle_add_supplement,
    "remove_supplement": _handle_remove_supplement,
    "log_supplement": _handle_log_supplement,
    "supplement_status": _handle_supplement_status,
    "log_expense": _handle_log_expense,
    "add_subscription": _handle_add_subscription,
    "cancel_subscription": _handle_cancel_subscription,
    "set_budget": _handle_set_budget,
    "finance_status": _handle_finance_status,
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
        # Image generation hallucinations
        "i've generated", "i generated", "here are your images",
        "here are the images", "images are saved", "images have been saved",
        "saved to workspace/images", "saved them to", "placed them in",
        "i've placed", "here are your pictures", "pictures are ready",
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
