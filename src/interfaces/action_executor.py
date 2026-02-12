"""
Action Executor - Parse user intent and execute actions via tools.

Connects chat/API interfaces to actual tool execution (file create, etc).
Uses SafetyController for path validation before execution.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

def _is_followup_correction(msg: str) -> bool:
    """True if message is a short follow-up like 'try again', 'that's wrong'."""
    m = (msg or "").strip().lower()
    if len(m) > 60:
        return False
    return (
        "try again" in m
        or "that's wrong" in m
        or "thats wrong" in m
        or "that's not right" in m
        or "that is wrong" in m
        or "incorrect" in m
        or "wrong" in m
        or "nope" in m
        or "correct that" in m
    )


def _is_datetime_question(msg: str) -> bool:
    """True if message is asking for current date/time."""
    m = (msg or "").strip().lower()
    return (
        "what day" in m
        or "today's date" in m
        or "todays date" in m
        or "current date" in m
        or "what's the date" in m
        or "whats the date" in m
        or "what date" in m
        or "day of the week" in m
    )


def _get_datetime_response() -> str:
    """Return formatted current date/time from system."""
    now = datetime.now()
    return now.strftime("%A, %B %d, %Y")

_root = Path(__file__).resolve().parent.parent.parent


def _trace(msg: str) -> None:
    """Trace to file for debugging chat flow."""
    try:
        trace_file = _root / "logs" / "chat_trace.log"
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        with open(trace_file, "a", encoding="utf-8") as f:
            from datetime import datetime
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    except Exception:
        pass


def _log_conversation(
    source: str,
    user_message: str,
    response: str,
    action_type: str,
    cost_usd: float = 0,
) -> None:
    """Log each exchange to logs/conversations.jsonl for troubleshooting."""
    try:
        log_file = _root / "logs" / "conversations.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": __import__("datetime").datetime.now().isoformat(),
            "source": source,
            "user": (user_message or "")[:500],
            "response": (response or "")[:500],
            "action": action_type,
            "cost_usd": cost_usd,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

ARCHI_SYSTEM_PROMPT = """You are Archi, an autonomous AI agent.

Identity: You are Archi (never say you are Grok or any other AI). Only mention your name when the user asks who you are.

Capabilities: Create files in workspace, execute tasks, manage goals, work in background through dream cycles, control the computer when needed.

Communication: Be helpful, direct, and concise. Confirm actions when you take them. Do not repeat "I'm Archi" in every message."""



def _workspace_path(relative: str) -> str:
    """Resolve relative path to full workspace path."""
    rel = relative.lstrip("/").replace("\\", "/")
    if not rel.startswith("workspace/"):
        rel = "workspace/" + rel
    return str(_root / rel.replace("/", os.sep))


def _sanitize_identity(text: str) -> str:
    """Replace Grok/xAI identity with Archi in model responses (routing may use Grok)."""
    if not text or not isinstance(text, str):
        return text or ""
    rl = text.lower()
    if "grok" not in rl and "xai" not in rl:
        return text
    logger.info("Sanitizing Grok/xAI identity from response (len=%d)", len(text))
    # Handle various apostrophes: straight ', curly ', etc.
    out = re.sub(
        r"i[\u0027\u2019']?m\s+grok[^.]*\.?",
        "I'm Archi, an autonomous AI agent.",
        text,
        flags=re.IGNORECASE,
        count=1,
    )
    out = re.sub(r"\bgrok\b", "Archi", out, flags=re.IGNORECASE)
    out = re.sub(r"\bvia\s+the\s+xai\s+api\b", "via API", out, flags=re.IGNORECASE)
    out = re.sub(r"\bbuilt\s+by\s+xai\b", "built for this project", out, flags=re.IGNORECASE)
    out = re.sub(r"\ba\s+helpful\s+ai\s+built\s+by\s+xai\b", "an autonomous AI agent", out, flags=re.IGNORECASE)
    out = re.sub(r"\bxai\s+api\b", "API", out, flags=re.IGNORECASE)
    out = re.sub(r"\bxai\b", "this project", out, flags=re.IGNORECASE)
    return out


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract JSON object from model response (handles markdown wrapping)."""
    text = text.strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try first {...}
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def process_message(
    message: str,
    router: Any,
    history: Optional[List[Dict[str, Any]]] = None,
    source: str = "unknown",
    goal_manager: Optional[Any] = None,
) -> Tuple[str, List[Dict[str, Any]], float]:
    """
    Process user message: parse intent, execute actions if needed, return response.

    Args:
        message: User's message
        router: ModelRouter instance for generation
        history: Optional list of {"role": "user"|"assistant", "content": "..."} for context
        source: Where the message came from ("web", "discord", "cli") for logs/conversations.jsonl

    Returns:
        (response_text, actions_taken, cost_usd)
    """
    actions_taken: List[Dict[str, Any]] = []
    total_cost = 0.0

    history_block = ""
    if history:
        recent = history[-(10 * 2):]  # Last 10 exchanges
        lines = []
        for m in recent:
            role = m.get("role", "user")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            prefix = "User:" if role == "user" else "Archi:"
            lines.append(f"{prefix} {content}")
        if lines:
            history_block = "Previous conversation:\n" + "\n".join(lines) + "\n\n"

    # Resolve follow-up corrections: "try again", "that's wrong" -> use previous user question
    # and prefer Grok (user said previous answer was wrong)
    effective_message = message
    retry_after_correction = False
    if history and _is_followup_correction(message):
        for m in reversed(history):
            if m.get("role") == "user":
                prev = (m.get("content") or "").strip()
                if prev and len(prev) > 5:
                    effective_message = prev
                    retry_after_correction = True
                    _trace(f"Follow-up resolved: using previous question, will prefer Grok (user said wrong)")
                break

    # Date/time from system - no search, always accurate
    if _is_datetime_question(effective_message):
        out = _get_datetime_response()
        _log_conversation(source, message, out, "datetime", total_cost)
        return (out, actions_taken, total_cost)

    # Fast path: /goal <description> - create goal for dream cycles
    msg_stripped = (message or "").strip()
    if msg_stripped.lower().startswith("/goal ") and goal_manager:
        desc = msg_stripped[6:].strip()
        if desc:
            try:
                goal = goal_manager.create_goal(
                    description=desc,
                    user_intent=f"User request via {source}",
                    priority=5,
                )
                out = f"Goal created: {goal.goal_id}\n\n{desc}\n\nArchi will work on this during dream cycles (when idle 5+ min)."
                _log_conversation(source, message, out, "create_goal", total_cost)
                return (out, actions_taken, total_cost)
            except Exception as e:
                logger.exception("Goal creation failed: %s", e)
                err = f"Couldn't create goal: {e}"
                _log_conversation(source, message, err, "create_goal", total_cost)
                return (err, actions_taken, total_cost)
        return (
            "Usage: /goal <description>\nExample: /goal Create 3 text files: test1.txt, test2.txt, test3.txt",
            actions_taken,
            total_cost,
        )
    if msg_stripped.lower().startswith("/goal ") and not goal_manager:
        return (
            "Goal creation not available (goal manager not connected). Use web chat at http://127.0.0.1:5001/chat to create goals.",
            actions_taken,
            total_cost,
        )

    # ==== /goals - list all goals ====
    if msg_stripped.lower() == "/goals":
        if not goal_manager:
            return ("Goal manager not available.", actions_taken, total_cost)
        try:
            status = goal_manager.get_status()
            if status.get("total_goals", 0) == 0:
                out = "No goals yet. Create one with:\n/goal <description>"
            else:
                lines = [f"Goals ({status['total_goals']} total):"]
                for g in status.get("goals", []):
                    icon = "✓" if g.get("completion_percentage", 0) == 100 else "⏳"
                    lines.append(f"\n{icon} {g.get('goal_id', '?')}: {g.get('description', '')[:60]}")
                    lines.append(f"   Progress: {g.get('completion_percentage', 0):.0f}% ({len(g.get('tasks', []))} tasks)")
                out = "\n".join(lines)
            _log_conversation(source, message, out, "goals", total_cost)
            return (out, actions_taken, total_cost)
        except Exception as e:
            logger.exception("Error listing goals: %s", e)
            return (f"Error listing goals: {e}", actions_taken, total_cost)

    # ==== /status - system health ====
    if msg_stripped.lower() == "/status":
        try:
            from src.monitoring.health_check import health_check
            health = health_check.check_all()
            lines = [
                f"System Status: {health.get('overall_status', 'unknown').upper()}",
                f"Summary: {health.get('summary', 'Unknown')}",
                "",
                "Components:",
            ]
            for comp, check in health.get("checks", {}).items():
                st = check.get("status", "unknown")
                icon = "✓" if st == "healthy" else "⚠" if st == "degraded" else "✗"
                lines.append(f"  {icon} {comp}: {st}")
            out = "\n".join(lines)
            _log_conversation(source, message, out, "status", total_cost)
            return (out, actions_taken, total_cost)
        except Exception as e:
            logger.exception("Error getting status: %s", e)
            return (f"Error getting status: {e}", actions_taken, total_cost)

    # ==== /cost - cost summary ====
    if msg_stripped.lower() == "/cost":
        try:
            from src.monitoring.cost_tracker import get_cost_tracker
            tracker = get_cost_tracker()
            summary = tracker.get_summary("all")
            today = summary.get("today", {})
            month = summary.get("month", {})
            out = (
                f"Cost Summary:\n"
                f"Today:    ${today.get('total_cost', 0):.4f} / ${today.get('budget', 0):.2f}\n"
                f"Month:    ${month.get('total_cost', 0):.4f} / ${month.get('budget', 0):.2f}\n"
                f"All-time: ${summary.get('total_cost', 0):.4f}\n\n"
                f"Total API calls: {summary.get('total_calls', 0)}"
            )
            _log_conversation(source, message, out, "cost", total_cost)
            return (out, actions_taken, total_cost)
        except Exception as e:
            logger.exception("Error getting costs: %s", e)
            return (f"Error getting costs: {e}", actions_taken, total_cost)

    # ==== /help - show commands ====
    if msg_stripped.lower() in ("/help", "/h"):
        out = (
            "Available Commands:\n"
            "/goal <description>  - Create a goal for autonomous execution\n"
            "/goals               - List all goals and their progress\n"
            "/status              - Show system health\n"
            "/cost                - Show cost summary\n"
            "/help                - Show this help\n\n"
            "You can also:\n"
            "- Chat naturally with me\n"
            "- Ask me to create files, search, etc.\n"
            "- Ask about current information (I'll search the web)"
        )
        _log_conversation(source, message, out, "help", total_cost)
        return (out, actions_taken, total_cost)

    # ==== Unknown /command - avoid sending to model ====
    if msg_stripped.startswith("/"):
        out = f"Unknown command: {msg_stripped.split()[0]}\nType /help for available commands."
        _log_conversation(source, message, out, "unknown_command", total_cost)
        return (out, actions_taken, total_cost)

    # Step 1: Ask model to analyze intent (with Archi identity)
    intent_prompt = f"""{ARCHI_SYSTEM_PROMPT}

Analyze this user message. Respond with ONLY valid JSON, no other text.
{history_block}User: {effective_message}

If the user wants to CREATE or WRITE a file, respond:
{{"action": "create_file", "path": "workspace/filename.txt", "content": "file content here"}}
- path must be under workspace/ (e.g. workspace/hello.txt)
- content is the exact text to write

If the user wants to CLICK something on the screen (e.g. Windows Start button, a button, an icon), respond:
{{"action": "click", "target": "what to click"}}
- Examples: "target": "Windows Start button", "target": "start menu", "target": "Windows start menu button"
- Use "Windows Start button" for the taskbar Windows logo

If the user wants to OPEN a URL in the browser (e.g. "open google", "go to example.com"), respond:
{{"action": "browser_navigate", "url": "https://..."}}
- Use full URL (https://...). For "open google" use "https://www.google.com"

If the user wants to CREATE A GOAL for Archi to work on later (e.g. "create a goal: X", "add goal: X", "I want you to X when idle"), respond:
{{"action": "create_goal", "description": "clear goal description"}}
- description: what Archi should do (e.g. "Create 3 text files: test1.txt, test2.txt, test3.txt")

If the user asks for CURRENT/LIVE data (prices, weather, news, scores, date, etc.), respond:
{{"action": "search", "query": "the specific search query, not meta-phrases"}}
- Examples: "spot price of silver", "weather in Tokyo", "today's date", "current date"
- If the user said "try again" or "that's wrong" about a previous question, use the PREVIOUS user's question as the query

If the user is just asking a question or chatting (no file creation, no click, no live data), respond as Archi:
{{"action": "chat", "response": "your helpful reply here"}}

Respond with ONLY the JSON object, nothing else."""

    try:
        _trace("action_executor: start")
        _trace(f"User [{source}]: {(message or '')[:200]}")
        logger.info("Action executor: processing message (len=%d)", len(message))
        intent_resp = router.generate(
            prompt=intent_prompt,
            max_tokens=400,
            temperature=0.2,
            prefer_local=not retry_after_correction,  # Retry: prefer Grok (user said wrong)
            force_grok=retry_after_correction,
        )
        total_cost += intent_resp.get("cost_usd", 0)

        if not intent_resp.get("success", True):
            err = f"Sorry, I couldn't process that: {intent_resp.get('error', 'Unknown error')}"
            _log_conversation(source, message, err, "error", total_cost)
            return (err, actions_taken, total_cost)

        parsed = _extract_json(intent_resp.get("text", ""))
        _trace(f"intent model={intent_resp.get('model')} text_len={len(intent_resp.get('text', ''))}")

        if not parsed:
            # Fallback: treat as chat, respond as Archi
            conv_prompt = f"""{ARCHI_SYSTEM_PROMPT}

User: {effective_message}

Respond naturally as Archi."""
            conv = router.generate(
                prompt=conv_prompt,
                max_tokens=500,
                temperature=0.7,
                prefer_local=not retry_after_correction,
                force_grok=retry_after_correction,
            )
            total_cost += conv.get("cost_usd", 0)
            out = conv.get("text", "I'm not sure how to respond.").strip() or "I'm not sure how to respond."
            out = _sanitize_identity(out)
            _log_conversation(source, message, out, "chat", total_cost)
            return (out, actions_taken, total_cost)

        action_type = parsed.get("action", "chat")

        # Fallback: if intent said "chat" but message clearly needs live data, treat as search
        _SEARCH_HINTS = (
            "spot price", "price of", "current price", "weather", "today's date",
            "latest", "stock price", "bitcoin price", "exchange rate",
        )
        if action_type == "chat" and any(
            h in (effective_message or "").lower() for h in _SEARCH_HINTS
        ):
            action_type = "search"
            _trace("Re-routing chat to search (message needs live data)")

        if action_type == "search":
            query = (parsed.get("query") or effective_message).strip()
            if not query:
                err = "I'd search for that, but I couldn't determine the query."
                _log_conversation(source, message, err, "search", total_cost)
                return err, actions_taken, total_cost
            # Improve commodity/metals price queries for better results
            query_lower = query.lower()
            if any(
                x in query_lower
                for x in ("spot price", "price of", "price for", "current price", "today's price")
            ) and any(
                x in query_lower for x in ("silver", "gold", "platinum", "copper", "oil", "bitcoin")
            ):
                if "silver" in query_lower and "ounce" not in query_lower:
                    query = f"{query} per ounce USD today"
                elif "gold" in query_lower and "ounce" not in query_lower:
                    query = f"{query} per ounce USD today"
                elif "oil" in query_lower and "barrel" not in query_lower:
                    query = f"{query} per barrel USD today"
            try:
                # User said previous answer was wrong -> use Grok with web search for better accuracy
                if retry_after_correction:
                    _trace("Retry after correction: using Grok with web search")
                    answer_prompt = f"Answer concisely: {query}"
                    answer_resp = router.generate(
                        prompt=answer_prompt,
                        max_tokens=300,
                        temperature=0.2,
                        force_grok=True,  # Grok has real-time web search, more accurate for live data
                    )
                    total_cost += answer_resp.get("cost_usd", 0)
                    raw = answer_resp.get("text", "").strip() or "I couldn't find a reliable answer."
                    raw = _sanitize_identity(raw)
                    _log_conversation(source, message, raw, "search", total_cost)
                    return (raw, actions_taken, total_cost)

                from src.tools.tool_registry import ToolRegistry

                tools = ToolRegistry()
                result = tools.execute("web_search", {"query": query, "max_results": 5})
                if not result.get("success"):
                    err = result.get("error") or f"I couldn't find relevant results for '{query}'."
                    _log_conversation(source, message, err, "search", total_cost)
                    return (err, actions_taken, total_cost)
                search_context = result.get("formatted", "No search results found.")
                answer_prompt = (
                    f"Use these search results to answer the question. Be concise.\n\n"
                    f"Search Results:\n{search_context}\n\n"
                    f"Question: {query}\n\nAnswer:"
                )
                answer_resp = router.generate(
                    prompt=answer_prompt,
                    max_tokens=300,
                    temperature=0.2,
                    prefer_local=True,
                    skip_web_search=True,  # We already have search results; avoid duplicate search
                )
                total_cost += answer_resp.get("cost_usd", 0)
                raw = answer_resp.get("text", "").strip() or "No answer found in search results."
                raw = _sanitize_identity(raw)
                _log_conversation(source, message, raw, "search", total_cost)
                return (raw, actions_taken, total_cost)
            except ImportError as e:
                logger.warning("Web search not available: %s", e)
                err = "Web search is not available. Install: pip install ddgs"
                _log_conversation(source, message, err, "search", total_cost)
                return (err, actions_taken, total_cost)
            except Exception as e:
                logger.exception("Search failed: %s", e)
                err = f"I tried to search but encountered an error: {str(e)}"
                _log_conversation(source, message, err, "search", total_cost)
                return (err, actions_taken, total_cost)

        if action_type == "create_goal":
            desc = (parsed.get("description") or effective_message or "").strip()
            if not goal_manager:
                err = "Goal creation not available here. Use web chat at http://127.0.0.1:5001/chat or /goal in CLI."
                _log_conversation(source, message, err, "create_goal", total_cost)
                return (err, actions_taken, total_cost)
            if not desc:
                err = "I'd create a goal, but I couldn't determine what to do. Try: create a goal: Create 3 text files"
                _log_conversation(source, message, err, "create_goal", total_cost)
                return (err, actions_taken, total_cost)
            try:
                goal = goal_manager.create_goal(
                    description=desc,
                    user_intent=f"User request via {source}",
                    priority=5,
                )
                out = f"Goal created: {goal.goal_id}\n\n{desc}\n\nArchi will work on this during dream cycles (when idle 5+ min)."
                _log_conversation(source, message, out, "create_goal", total_cost)
                return (out, actions_taken, total_cost)
            except Exception as e:
                logger.exception("Goal creation failed: %s", e)
                err = f"Couldn't create goal: {e}"
                _log_conversation(source, message, err, "create_goal", total_cost)
                return (err, actions_taken, total_cost)

        if action_type == "chat":
            raw = parsed.get("response", "") or ""
            response = _sanitize_identity(raw)
            _trace(f"chat raw={raw[:60]!r} sanitized={response[:60]!r}")
            if not response:
                conv_prompt = f"""{ARCHI_SYSTEM_PROMPT}

User: {effective_message}

Respond naturally as Archi."""
                conv = router.generate(
                    prompt=conv_prompt,
                    max_tokens=500,
                    temperature=0.7,
                    prefer_local=not retry_after_correction,
                    force_grok=retry_after_correction,
                )
                total_cost += conv.get("cost_usd", 0)
                response = _sanitize_identity(conv.get("text", "I'm not sure how to respond.").strip())
            response = response or "I'm not sure how to respond."
            _log_conversation(source, message, response, "chat", total_cost)
            return response, actions_taken, total_cost

        if action_type == "create_file":
            path = parsed.get("path", "")
            content = parsed.get("content", "")

            if not path:
                err = "I'd be happy to create a file, but I couldn't determine the filename. Please specify it."
                _log_conversation(source, message, err, "create_file", total_cost)
                return err, actions_taken, total_cost

            full_path = _workspace_path(path)

            # Execute via SafetyController + ToolRegistry
            from src.core.safety_controller import Action, SafetyController
            from src.tools.tool_registry import ToolRegistry

            safety = SafetyController()
            tools = ToolRegistry()

            action = Action(
                type="create_file",
                parameters={"path": full_path, "content": content},
                confidence=0.8,
                reasoning="User requested file creation via chat",
            )

            if not safety.authorize(action):
                err = "I'm not allowed to write outside the workspace. Please ask for a file in the workspace (e.g. workspace/hello.txt)."
                _log_conversation(source, message, err, "create_file", total_cost)
                return (err, actions_taken, total_cost)

            result = tools.execute("create_file", {"path": full_path, "content": content})

            if result.get("success"):
                actions_taken.append({
                    "description": f"Created file: {full_path}",
                    "result": result,
                })
                out = f"Done! I created the file at {full_path}."
                _log_conversation(source, message, out, "create_file", total_cost)
                return (out, actions_taken, total_cost)
            else:
                err = f"I tried to create the file but encountered an error: {result.get('error', 'Unknown error')}"
                _log_conversation(source, message, err, "create_file", total_cost)
                return (err, actions_taken, total_cost)

        if action_type == "click":
            target = (parsed.get("target") or "").strip()
            if not target:
                err = "I'd be happy to click something, but I couldn't determine what to click. Please specify (e.g. 'Windows Start button', 'the OK button')."
                _log_conversation(source, message, err, "click", total_cost)
                return (err, actions_taken, total_cost)

            # Normalize common phrases to "Windows Start button" for ComputerUse
            target_lower = target.lower()
            if (
                "start" in target_lower and ("windows" in target_lower or "menu" in target_lower)
            ) or target_lower in ("start", "start button", "start menu"):
                target = "Windows Start button"

            try:
                from src.tools.tool_registry import ToolRegistry

                tools = ToolRegistry()
                result = tools.execute(
                    "desktop_click_element",
                    {"target": target, "app_name": "desktop", "use_vision": True},
                )
                total_cost += result.get("cost_usd", 0)

                if result.get("success"):
                    actions_taken.append({
                        "description": f"Clicked: {target}",
                        "result": result,
                    })
                    method = result.get("method", "vision")
                    out = f"Done! I clicked the {target} (using {method})."
                    _log_conversation(source, message, out, "click", total_cost)
                    return (out, actions_taken, total_cost)
                err = f"I tried to click the {target} but: {result.get('error', 'Unknown error')}"
                _log_conversation(source, message, err, "click", total_cost)
                return (err, actions_taken, total_cost)
            except ImportError as e:
                logger.warning("Computer use not available: %s", e)
                err = "Computer control (click) is not available. PyAutoGUI or dependencies may be missing."
                _log_conversation(source, message, err, "click", total_cost)
                return (err, actions_taken, total_cost)
            except Exception as e:
                logger.exception("Click failed: %s", e)
                err = f"I tried to click but encountered an error: {str(e)}"
                _log_conversation(source, message, err, "click", total_cost)
                return (err, actions_taken, total_cost)

        if action_type == "browser_navigate":
            url = (parsed.get("url") or "").strip()
            if not url:
                err = "I'd open a URL, but I couldn't determine which one. Please specify (e.g. 'open https://google.com')."
                _log_conversation(source, message, err, "browser_navigate", total_cost)
                return (err, actions_taken, total_cost)
            if not url.startswith(("http://", "https://")):
                url_lower = url.lower().replace(" ", "")
                common = {
                    "google": "https://www.google.com",
                    "youtube": "https://www.youtube.com",
                    "github": "https://github.com",
                    "duckduckgo": "https://duckduckgo.com",
                }
                url = common.get(url_lower) or ("https://" + url)
            try:
                from src.tools.tool_registry import ToolRegistry

                tools = ToolRegistry()
                result = tools.execute("browser_navigate", {"url": url})
                if result.get("success"):
                    actions_taken.append({
                        "description": f"Opened: {url}",
                        "result": result,
                    })
                    out = f"Done! I opened {url} in the browser."
                    _log_conversation(source, message, out, "browser_navigate", total_cost)
                    return (out, actions_taken, total_cost)
                err = result.get("error", "Failed to open URL")
                _log_conversation(source, message, err, "browser_navigate", total_cost)
                return (f"I couldn't open {url}: {err}", actions_taken, total_cost)
            except ImportError as e:
                logger.warning("Browser not available: %s", e)
                err = "Browser control not available. Install: pip install playwright && playwright install chromium"
                _log_conversation(source, message, err, "browser_navigate", total_cost)
                return (err, actions_taken, total_cost)
            except Exception as e:
                logger.exception("Browser navigate failed: %s", e)
                err = f"I tried to open the URL but encountered an error: {str(e)}"
                _log_conversation(source, message, err, "browser_navigate", total_cost)
                return (err, actions_taken, total_cost)

        # Unknown action - respond as Archi
        conv_prompt = f"""{ARCHI_SYSTEM_PROMPT}

User: {effective_message}

Respond naturally as Archi."""
        conv = router.generate(
            prompt=conv_prompt,
            max_tokens=500,
            temperature=0.7,
            prefer_local=not retry_after_correction,
            force_grok=retry_after_correction,
        )
        total_cost += conv.get("cost_usd", 0)
        out = conv.get("text", "I'm not sure how to respond.").strip() or "I'm not sure how to respond."
        out = _sanitize_identity(out)
        _log_conversation(source, message, out, "chat", total_cost)
        return (out, actions_taken, total_cost)

    except Exception as e:
        logger.error("Action execution error: %s", e, exc_info=True)
        err = f"Sorry, I encountered an error: {str(e)}"
        _log_conversation(source, message, err, "error", total_cost)
        return (err, actions_taken, total_cost)
