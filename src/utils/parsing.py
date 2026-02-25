"""
Shared parsing helpers for Archi.

Centralises ``extract_json_array()`` and ``extract_json()`` which were
previously duplicated across multiple core modules.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from src.utils.text_cleaning import strip_thinking

logger = logging.getLogger(__name__)


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract a JSON object from an LLM response.

    Strips ``<think>`` reasoning blocks, then tries in order:
    1. Direct ``json.loads``.
    2. Content inside a markdown code fence.
    3. First ``{…}`` substring.

    Returns ``None`` if no valid JSON object is found.
    """
    text = (text or "").strip()
    if not text:
        return None

    # Strip reasoning model output (may wrap JSON)
    text = strip_thinking(text).strip()
    if not text:
        return None

    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Markdown code block
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. Bare {...} substring
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


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
    text = strip_thinking(text).strip()
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


def read_file_contents(
    file_paths: List[str],
    *,
    max_files: int = 5,
    max_chars: int = 1500,
    total_budget: int = 6000,
    note_missing: bool = False,
    empty_label: str = "(no files)",
) -> str:
    """Read and truncate file contents for model prompts.

    Returns a formatted block of file contents respecting size budgets.
    Used by QA evaluator, integrator, and anywhere else that needs to
    include file snippets in prompts.

    Args:
        file_paths: Files to read.
        max_files: Maximum number of files to include.
        max_chars: Per-file character read limit.
        total_budget: Total character budget across all files.
        note_missing: If True, include "MISSING: <name>" for absent files.
        empty_label: Text returned when no files are readable.
    """
    blocks: List[str] = []
    total = 0
    for fpath in file_paths[:max_files]:
        if total > total_budget:
            break
        try:
            if not os.path.isfile(fpath):
                if note_missing:
                    blocks.append(f"MISSING: {os.path.basename(fpath)}")
                continue
            size = os.path.getsize(fpath)
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_chars)
            block = f"--- {os.path.basename(fpath)} ({size} bytes) ---\n{content}"
            blocks.append(block)
            total += len(block)
        except Exception as e:
            logger.debug("read_file_contents: couldn't read %s: %s", fpath, e)
    return "\n\n".join(blocks) if blocks else empty_label
