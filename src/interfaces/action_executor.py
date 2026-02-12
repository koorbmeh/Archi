"""
Action Executor - Parse user intent and execute actions via tools.

Connects chat/API interfaces to actual tool execution (file create, etc).
Uses SafetyController for path validation before execution.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

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

ARCHI_SYSTEM_PROMPT = """You are Archi, an autonomous AI agent.

Core Identity:
- You are Archi, NOT Grok or any other AI. Never say you are Grok.
- You are an autonomous agent with the ability to: create files in the workspace, execute tasks, manage goals, work in the background through dream cycles, and control the computer when needed.

Capabilities:
- File operations (create, read, edit in workspace)
- Goal management (break down complex goals into tasks)
- Autonomous execution during idle time
- Cost optimization (use local AI when possible, Grok only when needed)

Communication: Be helpful, direct, and concise. When asked who you are, say you are Archi. Confirm actions when you take them."""



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
) -> Tuple[str, List[Dict[str, Any]], float]:
    """
    Process user message: parse intent, execute actions if needed, return response.

    Args:
        message: User's message
        router: ModelRouter instance for generation

    Returns:
        (response_text, actions_taken, cost_usd)
    """
    actions_taken: List[Dict[str, Any]] = []
    total_cost = 0.0

    # Step 1: Ask model to analyze intent (with Archi identity)
    intent_prompt = f"""{ARCHI_SYSTEM_PROMPT}

Analyze this user message. Respond with ONLY valid JSON, no other text.

User: {message}

If the user wants to CREATE or WRITE a file, respond:
{{"action": "create_file", "path": "workspace/filename.txt", "content": "file content here"}}
- path must be under workspace/ (e.g. workspace/hello.txt)
- content is the exact text to write

If the user is just asking a question or chatting (no file creation/file write request), respond as Archi:
{{"action": "chat", "response": "your helpful reply as Archi here"}}

Remember: You are Archi. Respond with ONLY the JSON object, nothing else."""

    try:
        _trace("action_executor: start")
        logger.info("Action executor: processing message (len=%d)", len(message))
        intent_resp = router.generate(
            prompt=intent_prompt,
            max_tokens=400,
            temperature=0.2,
            prefer_local=True,  # Chat: try local first even for long prompts
        )
        total_cost += intent_resp.get("cost_usd", 0)

        if not intent_resp.get("success", True):
            return (
                f"Sorry, I couldn't process that: {intent_resp.get('error', 'Unknown error')}",
                actions_taken,
                total_cost,
            )

        parsed = _extract_json(intent_resp.get("text", ""))
        _trace(f"intent model={intent_resp.get('model')} text_len={len(intent_resp.get('text', ''))}")

        if not parsed:
            # Fallback: treat as chat, respond as Archi
            conv_prompt = f"""{ARCHI_SYSTEM_PROMPT}

User: {message}

Respond naturally as Archi."""
            conv = router.generate(
                prompt=conv_prompt,
                max_tokens=500,
                temperature=0.7,
                prefer_local=True,
            )
            total_cost += conv.get("cost_usd", 0)
            out = conv.get("text", "I'm not sure how to respond.").strip() or "I'm not sure how to respond."
            return (_sanitize_identity(out), actions_taken, total_cost)

        action_type = parsed.get("action", "chat")

        if action_type == "chat":
            raw = parsed.get("response", "") or ""
            response = _sanitize_identity(raw)
            _trace(f"chat raw={raw[:60]!r} sanitized={response[:60]!r}")
            if not response:
                conv_prompt = f"""{ARCHI_SYSTEM_PROMPT}

User: {message}

Respond naturally as Archi."""
                conv = router.generate(
                    prompt=conv_prompt,
                    max_tokens=500,
                    temperature=0.7,
                    prefer_local=True,
                )
                total_cost += conv.get("cost_usd", 0)
                response = _sanitize_identity(conv.get("text", "I'm not sure how to respond.").strip())
            return response or "I'm not sure how to respond.", actions_taken, total_cost

        if action_type == "create_file":
            path = parsed.get("path", "")
            content = parsed.get("content", "")

            if not path:
                return "I'd be happy to create a file, but I couldn't determine the filename. Please specify it.", actions_taken, total_cost

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
                return (
                    f"I'm not allowed to write outside the workspace. Please ask for a file in the workspace (e.g. workspace/hello.txt).",
                    actions_taken,
                    total_cost,
                )

            result = tools.execute("create_file", {"path": full_path, "content": content})

            if result.get("success"):
                actions_taken.append({
                    "description": f"Created file: {full_path}",
                    "result": result,
                })
                return (
                    f"Done! I created the file at {full_path}.",
                    actions_taken,
                    total_cost,
                )
            else:
                return (
                    f"I tried to create the file but encountered an error: {result.get('error', 'Unknown error')}",
                    actions_taken,
                    total_cost,
                )

        # Unknown action - respond as Archi
        conv_prompt = f"""{ARCHI_SYSTEM_PROMPT}

User: {message}

Respond naturally as Archi."""
        conv = router.generate(
            prompt=conv_prompt,
            max_tokens=500,
            temperature=0.7,
            prefer_local=True,
        )
        total_cost += conv.get("cost_usd", 0)
        out = conv.get("text", "I'm not sure how to respond.").strip() or "I'm not sure how to respond."
        return (_sanitize_identity(out), actions_taken, total_cost)

    except Exception as e:
        logger.error("Action execution error: %s", e, exc_info=True)
        return f"Sorry, I encountered an error: {str(e)}", actions_taken, total_cost
