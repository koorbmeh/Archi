"""
Discovery Phase — Project context scanning before goal decomposition.

For complex goals that reference existing projects, runs BEFORE the Architect
to produce a structured project brief. The brief grounds task specs in reality:
what files exist, what patterns to follow, what NOT to duplicate.

One model call per discovery. Feeds into decompose_goal() prompt.

Created session 53 (Phase 5: Planning + Scheduling).
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# Max files to enumerate per project
_MAX_FILES = 100
# Max bytes to read per file for structure extraction
_MAX_READ_BYTES = 4000
# Max total chars of file content to feed into the model prompt
_MAX_CONTENT_CHARS = 8000


def discover_project(
    goal_description: str,
    project_context: Dict[str, Any],
    router: Any,
) -> Optional[Dict[str, Any]]:
    """Run discovery for a goal that references a known project.

    Steps:
    1. Match goal keywords against project_context to find the right project.
    2. Enumerate files in the project directory.
    3. Rank by relevance (entry points, READMEs, keyword matches, recency).
    4. Read selectively (signatures for code, full for docs).
    5. Compress into a structured brief via one model call.

    Returns:
        dict with keys: project_name, project_path, brief, files_found, cost
        or None if no matching project found.
    """
    # Step 1: Find matching project
    match = _match_project(goal_description, project_context)
    if not match:
        logger.debug("Discovery: no project match for goal '%s'", goal_description[:80])
        return None

    project_key, project_info = match
    project_path = project_info.get("path", "")
    abs_path = _base_path() / project_path

    if not abs_path.exists():
        logger.warning("Discovery: project path does not exist: %s", abs_path)
        return None

    logger.info("Discovery: scanning project '%s' at %s", project_key, project_path)

    # Step 2: Enumerate files
    all_files = _enumerate_files(abs_path)
    if not all_files:
        logger.info("Discovery: no files found in %s", project_path)
        return None

    # Step 3: Rank by relevance (with user model personalization, session 58)
    user_prefs_context = ""
    try:
        from src.core.user_model import get_user_model
        user_prefs_context = get_user_model().get_context_for_discovery()
    except Exception:
        pass
    ranked = _rank_files(all_files, goal_description, abs_path, user_prefs_context)

    # Step 4: Read selectively
    file_contents = _read_selectively(ranked, abs_path)

    # Step 5: Compress into brief via model call
    brief, cost = _generate_brief(
        goal_description=goal_description,
        project_name=project_key,
        project_desc=project_info.get("description", project_key),
        file_contents=file_contents,
        all_files=[str(f.relative_to(abs_path)) for f in all_files],
        router=router,
    )

    result = {
        "project_name": project_key,
        "project_path": project_path,
        "brief": brief,
        "files_found": len(all_files),
        "files_read": len(file_contents),
        "cost": cost,
    }
    logger.info(
        "Discovery: brief generated for '%s' (%d files found, %d read, $%.4f)",
        project_key, len(all_files), len(file_contents), cost,
    )
    return result


def _match_project(
    goal_description: str,
    project_context: Dict[str, Any],
) -> Optional[tuple]:
    """Match a goal description to a project in project_context.

    Uses word-level matching against project keys, descriptions, paths,
    and focus areas. Returns (key, info_dict) or None.
    """
    active = project_context.get("active_projects", {})
    if not active:
        return None

    goal_lower = goal_description.lower()
    goal_words = set(re.findall(r'\w+', goal_lower))
    # Remove very common words
    _STOP = {"a", "an", "the", "and", "or", "to", "for", "in", "of", "on",
             "with", "is", "by", "it", "my", "this", "that", "do", "be",
             "create", "build", "make", "write", "add", "update", "fix",
             "new", "work", "project", "file", "files"}
    goal_words -= _STOP
    # Normalize: add stemmed forms (strip trailing 's', 'ing', 'er')
    goal_stems = set()
    for w in goal_words:
        goal_stems.add(w)
        if w.endswith("s") and len(w) > 3:
            goal_stems.add(w[:-1])
        if w.endswith("ing") and len(w) > 5:
            goal_stems.add(w[:-3])
        if w.endswith("er") and len(w) > 4:
            goal_stems.add(w[:-2])

    best_score = 0
    best_match = None

    for key, info in active.items():
        if not isinstance(info, dict):
            continue

        # Build match corpus from project metadata
        corpus_parts = [
            key.replace("_", " "),
            info.get("description", ""),
            info.get("path", ""),
        ]
        for fa in info.get("focus_areas", []):
            corpus_parts.append(fa)
        corpus = " ".join(corpus_parts).lower()
        corpus_words = set(re.findall(r'\w+', corpus)) - _STOP
        # Normalize corpus similarly
        corpus_stems = set()
        for w in corpus_words:
            corpus_stems.add(w)
            if w.endswith("s") and len(w) > 3:
                corpus_stems.add(w[:-1])
            if w.endswith("ing") and len(w) > 5:
                corpus_stems.add(w[:-3])
            if w.endswith("er") and len(w) > 4:
                corpus_stems.add(w[:-2])

        # Score: word overlap (using stemmed forms)
        if not goal_stems or not corpus_stems:
            continue
        overlap = len(goal_stems & corpus_stems)
        if overlap > best_score:
            best_score = overlap
            best_match = (key, info)

    # Require at least 1 meaningful word match
    if best_score >= 1 and best_match:
        return best_match
    return None


def _enumerate_files(root: Path) -> List[Path]:
    """Recursively list files in a project directory.

    Skips hidden dirs, __pycache__, node_modules, .git, etc.
    Returns up to _MAX_FILES paths sorted by modification time (newest first).
    """
    _SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  ".mypy_cache", ".pytest_cache", "dist", "build", ".tox"}
    files: List[Path] = []

    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune skip dirs
            dirnames[:] = [
                d for d in dirnames
                if d not in _SKIP_DIRS and not d.startswith(".")
            ]
            for fname in filenames:
                if fname.startswith("."):
                    continue
                files.append(Path(dirpath) / fname)
                if len(files) >= _MAX_FILES:
                    break
            if len(files) >= _MAX_FILES:
                break
    except Exception as e:
        logger.debug("Discovery: error enumerating %s: %s", root, e)

    # Sort by modification time (newest first)
    try:
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    except Exception:
        pass

    return files


def _rank_files(
    files: List[Path],
    goal_description: str,
    root: Path,
    user_prefs: str = "",
) -> List[Path]:
    """Rank files by relevance to the goal.

    Priority order:
    1. Entry points (main.py, __init__.py, app.py)
    2. READMEs and overview docs
    3. Files matching goal keywords
    4. Files matching user model preferences (session 58)
    5. Recently modified files
    6. Everything else

    Returns top ~15 files.
    """
    goal_words = set(re.findall(r'\w+', goal_description.lower()))
    # Extract keywords from user preferences for relevance boosting
    pref_words = set(re.findall(r'\w+', user_prefs.lower())) - {
        "jesse", "prefers", "project", "preferences", "style", "communication",
    } if user_prefs else set()

    scored: List[tuple] = []
    for f in files:
        score = 0
        name_lower = f.name.lower()
        rel = str(f.relative_to(root)).lower()

        # Entry points
        if name_lower in ("main.py", "app.py", "__init__.py", "index.py", "setup.py"):
            score += 10
        # READMEs / overviews
        if any(kw in name_lower for kw in ("readme", "overview", "vision", "todo")):
            score += 8
        # Config files
        if name_lower in ("requirements.txt", "pyproject.toml", "package.json", "config.yaml"):
            score += 6
        # Goal keyword match
        rel_words = set(re.findall(r'\w+', rel))
        keyword_hits = len(goal_words & rel_words)
        score += keyword_hits * 4
        # User preference keyword match (session 58)
        if pref_words:
            pref_hits = len(pref_words & rel_words)
            score += pref_hits * 2
        # Python/markdown files get a small bonus (more likely to be relevant)
        if f.suffix in (".py", ".md", ".json", ".yaml", ".yml"):
            score += 2
        # Recency bonus (already sorted newest first, index gives bonus)
        idx = files.index(f)
        if idx < 5:
            score += 3

        scored.append((score, f))

    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:15]]


def _read_selectively(
    ranked_files: List[Path],
    root: Path,
) -> List[Dict[str, str]]:
    """Read file contents selectively.

    For code files: read first ~50 lines + extract function/class signatures.
    For docs/config: read full content (up to _MAX_READ_BYTES).
    """
    results: List[Dict[str, str]] = []
    total_chars = 0

    for fpath in ranked_files:
        if total_chars >= _MAX_CONTENT_CHARS:
            break

        rel = str(fpath.relative_to(root))
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read(_MAX_READ_BYTES)
        except Exception:
            continue

        if not raw.strip():
            continue

        suffix = fpath.suffix.lower()
        if suffix in (".py",):
            # For Python: extract structure (imports, class/function defs, first docstring)
            content = _extract_python_structure(raw)
        elif suffix in (".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini"):
            # Docs/config: include full content (truncated)
            content = raw[:2000]
        else:
            # Other files: first 500 chars
            content = raw[:500]

        if content.strip():
            results.append({"path": rel, "content": content})
            total_chars += len(content)

    return results


def _extract_python_structure(source: str) -> str:
    """Extract the structural skeleton of a Python file.

    Returns imports, class definitions, function signatures, and top-level
    docstrings. Skips function bodies.
    """
    lines = source.split("\n")
    output_lines: List[str] = []
    in_docstring = False
    docstring_count = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Always include imports
        if stripped.startswith(("import ", "from ")):
            output_lines.append(line)
            continue

        # Always include class/function definitions
        if stripped.startswith(("class ", "def ")):
            output_lines.append(line)
            continue

        # Include decorators
        if stripped.startswith("@"):
            output_lines.append(line)
            continue

        # Include docstrings (first 2 only)
        if '"""' in stripped or "'''" in stripped:
            if not in_docstring:
                in_docstring = True
                docstring_count += 1
                if docstring_count <= 2:
                    output_lines.append(line)
            else:
                in_docstring = False
                if docstring_count <= 2:
                    output_lines.append(line)
            continue

        if in_docstring and docstring_count <= 2:
            output_lines.append(line)
            continue

        # Include module-level constants / assignments (no indentation)
        if not line.startswith((" ", "\t")) and "=" in stripped and not stripped.startswith("#"):
            output_lines.append(line)
            continue

        # Include first 3 lines of the file (shebang, encoding, module docstring start)
        if i < 3:
            output_lines.append(line)

    # Cap at ~80 lines
    return "\n".join(output_lines[:80])


def _generate_brief(
    goal_description: str,
    project_name: str,
    project_desc: str,
    file_contents: List[Dict[str, str]],
    all_files: List[str],
    router: Any,
) -> tuple:
    """Generate a structured project brief via one model call.

    Returns (brief_text, cost_usd).
    """
    # Build file content blocks
    content_blocks = []
    for fc in file_contents:
        content_blocks.append(f"--- {fc['path']} ---\n{fc['content']}")
    content_text = "\n\n".join(content_blocks)

    # Truncate all_files list for prompt
    files_list = "\n".join(f"  {f}" for f in all_files[:40])

    prompt = f"""You are Archi's Architect. Produce a concise project brief that will guide task planning.

PROJECT: {project_name} — {project_desc}

GOAL TO ACCOMPLISH: {goal_description}

ALL FILES IN PROJECT ({len(all_files)} total):
{files_list}

KEY FILE CONTENTS:
{content_text}

Produce a brief covering:
1. WHAT EXISTS: Key files and their purpose (2-4 sentences).
2. PATTERNS: Coding conventions, file organization, naming patterns observed.
3. RELEVANT TO GOAL: Which existing files/patterns relate to this goal.
4. GAPS: What's missing that needs to be built for this goal.
5. CAUTIONS: Things to NOT duplicate, dependencies to be aware of, patterns to follow.

Keep it under 500 words. Be specific — name actual files, actual patterns, actual gaps.
No preamble, just the brief."""

    try:
        resp = router.generate(prompt=prompt, max_tokens=800, temperature=0.3)
        brief = resp.get("text", "").strip()
        cost = resp.get("cost_usd", 0) or 0
        if not brief:
            brief = _fallback_brief(project_name, all_files, file_contents)
            cost = 0
        return brief, cost
    except Exception as e:
        logger.warning("Discovery: model call failed: %s", e)
        return _fallback_brief(project_name, all_files, file_contents), 0


def _fallback_brief(
    project_name: str,
    all_files: List[str],
    file_contents: List[Dict[str, str]],
) -> str:
    """Deterministic fallback brief when model call fails."""
    lines = [f"Project: {project_name}"]
    lines.append(f"Files: {len(all_files)} total")
    if all_files:
        lines.append("Key files: " + ", ".join(all_files[:10]))
    if file_contents:
        lines.append("Read: " + ", ".join(fc["path"] for fc in file_contents[:5]))
    return "\n".join(lines)
