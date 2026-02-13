"""
Shared parsing helpers for Archi.

Centralises ``extract_json_array()`` which was previously duplicated in
``src.core.goal_manager`` and ``src.core.learning_system``.
"""

import json
import re
from typing import Any, List


def _strip_thinking_blocks(text: str) -> str:
    """Remove <think>...</think> blocks that reasoning models may emit."""
    if not text or "<think>" not in text:
        return text or ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return cleaned.replace("</think>", "").strip() if "</think>" in cleaned else cleaned


def _parse_numbered_list(text: str) -> List[str]:
    """Extract a list from prose like '1. X\\n2. Y' or '- X\\n- Y'."""
    items: List[str] = []
    # Numbered: 1. X, 2. Y (or 1) X, 2) Y)
    for m in re.finditer(r"^\s*(?:\d+[.)]\s*)(.+)$", text, re.MULTILINE):
        items.append(m.group(1).strip())
    if items:
        return items
    # Bullet: - X, * X
    for m in re.finditer(r"^\s*[-*]\s+(.+)$", text, re.MULTILINE):
        items.append(m.group(1).strip())
    return items


def extract_json_array(text: str, *, allow_prose_fallback: bool = False) -> List[Any]:
    """Extract a JSON array from an LLM response.

    Tries, in order:
    1. Direct ``json.loads``.
    2. Content inside a markdown ````` ``` ````` code fence.
    3. First ``[…]`` substring.
    4. Strip <think> blocks and retry 1–3.
    5. If allow_prose_fallback: parse numbered/bullet list (1. X, 2. Y or - X).

    Returns ``[]`` if none succeed (avoids raising for prose responses).
    """
    text = (text or "").strip()
    if not text:
        return []

    # 0. Strip reasoning model output (may wrap JSON)
    text = _strip_thinking_blocks(text).strip()
    if not text:
        return []

    # 1. Direct parse
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        pass

    # 2. Markdown code block
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            result = json.loads(match.group(1).strip())
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            pass

    # 3. Bare [...] substring
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            result = json.loads(match.group(0))
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            pass

    # 4. Numbered/bullet list fallback (for string arrays only, e.g. suggestions)
    if allow_prose_fallback:
        items = _parse_numbered_list(text)
        if items:
            return items

    return []
