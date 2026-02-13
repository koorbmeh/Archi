"""
Interesting Findings Queue — Surface noteworthy discoveries to Jesse.

When the dream cycle completes research tasks and creates files,
this module evaluates whether the findings contain something genuinely
surprising or useful.  If so, it queues a short conversational message
for delivery through Discord (via morning reports, hourly summaries,
or appended to the next chat response).

Design constraints (from prime directive):
- Avoid unnecessary messaging spam
- Lead with actionable information
- Never send communications that could endanger privacy
- Communication style: clear, concise, technically competent
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# Singleton instance
_instance: Optional["InterestingFindingsQueue"] = None

# Queue limits
_MAX_PENDING = 10       # Max undelivered findings in queue
_EXPIRE_DAYS = 7        # Auto-expire undelivered findings after this many days
_DELIVERY_COOLDOWN = 14400  # 4 hours between finding deliveries in chat (seconds)

# Track last delivery time (module-level, survives across calls within same process)
_last_chat_delivery: float = 0.0


def get_findings_queue() -> "InterestingFindingsQueue":
    """Return the singleton InterestingFindingsQueue (lazy-load)."""
    global _instance
    if _instance is None:
        _instance = InterestingFindingsQueue()
    return _instance


class InterestingFindingsQueue:
    """Queue for interesting findings to surface to Jesse."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else _base_path() / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.data_dir / "interesting_findings_queue.json"

        self.findings: List[Dict[str, Any]] = []
        self._load()

        # Prune expired findings on load
        self._prune_expired()

        logger.info(
            "InterestingFindingsQueue initialized (%d pending)",
            self.pending_count(),
        )

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load findings from disk."""
        if not self._file.exists():
            return
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.findings = data
            elif isinstance(data, dict):
                self.findings = data.get("findings", [])
        except Exception as e:
            logger.warning("Could not load findings queue: %s", e)

    def save(self) -> None:
        """Save findings to disk (atomic write)."""
        tmp = self._file.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.findings, f, indent=2, ensure_ascii=False)
            tmp.replace(self._file)
        except Exception as e:
            logger.warning("Could not save findings queue: %s", e)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # ── Queue management ─────────────────────────────────────────────

    def pending_count(self) -> int:
        """Number of undelivered findings."""
        return sum(1 for f in self.findings if not f.get("delivered"))

    def get_next_undelivered(self) -> Optional[Dict[str, Any]]:
        """Get the oldest undelivered finding, or None."""
        for f in self.findings:
            if not f.get("delivered"):
                return f
        return None

    def get_next_for_chat(self) -> Optional[Dict[str, Any]]:
        """Get next finding for chat delivery, respecting cooldown.

        Returns None if cooldown hasn't elapsed since last chat delivery.
        """
        global _last_chat_delivery
        now = time.monotonic()
        if now - _last_chat_delivery < _DELIVERY_COOLDOWN:
            return None
        return self.get_next_undelivered()

    def mark_delivered(self, finding_id: str) -> None:
        """Mark a finding as delivered."""
        global _last_chat_delivery
        for f in self.findings:
            if f.get("id") == finding_id:
                f["delivered"] = True
                f["delivered_at"] = datetime.now().isoformat()
                _last_chat_delivery = time.monotonic()
                self.save()
                logger.info("Finding delivered: %s", finding_id)
                return

    def queue_finding(self, finding: Dict[str, Any]) -> Optional[str]:
        """Add a finding to the queue.

        Args:
            finding: Dict with at least 'summary' and 'topic' keys.

        Returns:
            Finding ID if queued, None if rejected (queue full, etc.).
        """
        if self.pending_count() >= _MAX_PENDING:
            logger.info("Finding queue full (%d), skipping", _MAX_PENDING)
            return None

        summary = (finding.get("summary") or "").strip()
        if not summary:
            return None

        finding_id = f"find_{uuid.uuid4().hex[:8]}"
        entry = {
            "id": finding_id,
            "summary": summary,
            "topic": (finding.get("topic") or "").strip(),
            "goal": (finding.get("goal") or "").strip(),
            "task": (finding.get("task") or "").strip(),
            "queued_at": datetime.now().isoformat(),
            "delivered": False,
            "delivered_at": None,
        }
        self.findings.append(entry)
        self.save()
        logger.info("Queued interesting finding [%s]: %s", finding_id, summary[:60])
        return finding_id

    def _prune_expired(self) -> None:
        """Remove undelivered findings older than _EXPIRE_DAYS."""
        cutoff = datetime.now() - timedelta(days=_EXPIRE_DAYS)
        before = len(self.findings)
        self.findings = [
            f for f in self.findings
            if f.get("delivered") or _parse_ts(f.get("queued_at", "")) > cutoff
        ]
        pruned = before - len(self.findings)
        if pruned > 0:
            self.save()
            logger.info("Pruned %d expired findings", pruned)

    # ── Finding evaluation ───────────────────────────────────────────

    def evaluate_and_queue(
        self,
        task_result: Dict[str, Any],
        files_created: List[str],
        goal_desc: str,
        task_desc: str,
        router: Any,
    ) -> Optional[str]:
        """Evaluate task output for interesting findings and queue if worthy.

        Reads created files, asks the model to judge whether anything
        is genuinely surprising or useful to Jesse, and if so, formats
        a short conversational message for the queue.

        Args:
            task_result: Result dict from PlanExecutor
            files_created: List of file paths created by the task
            goal_desc: Description of the parent goal
            task_desc: Description of the task
            router: ModelRouter for LLM calls

        Returns:
            Finding ID if something was queued, None otherwise.
        """
        if not files_created or not router:
            return None

        if self.pending_count() >= _MAX_PENDING:
            return None

        # Read created files (truncated)
        file_contents = []
        for fpath in files_created[:3]:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(1500)
                file_contents.append((os.path.basename(fpath), content))
            except Exception:
                continue

        if not file_contents:
            return None

        findings_text = "\n\n".join(
            f"--- {name} ---\n{content}" for name, content in file_contents
        )

        prompt = f"""You completed this research task for Jesse:

Goal: {goal_desc}
Task: {task_desc}

Research output:
{findings_text}

Is there something genuinely surprising, useful, or actionable here that Jesse would want to know about?  Not routine summaries — only things that are:
- Unexpected or counter-intuitive
- Directly actionable for his health/wealth/goals
- A risk or warning he should be aware of
- A specific opportunity worth considering

If YES, write a brief conversational message (1-2 sentences) as if telling a friend about something interesting you found.  Use natural language, not report-speak.  Example: "I was looking into magnesium forms and found that glycinate absorbs significantly better than oxide — might be worth switching if you're using oxide."

Return ONLY a JSON object:
{{"interesting": true/false, "summary": "conversational message or empty", "topic": "1-3 word topic"}}
JSON only:"""

        try:
            resp = router.generate(
                prompt=prompt, max_tokens=200, temperature=0.3, prefer_local=True,
            )
            text = resp.get("text", "")

            # Parse JSON response
            parsed = _extract_json(text)
            if not parsed:
                return None

            if not parsed.get("interesting"):
                logger.debug(
                    "Finding evaluated as not interesting: %s", task_desc[:60],
                )
                return None

            summary = (parsed.get("summary") or "").strip()
            topic = (parsed.get("topic") or "").strip()

            if not summary or len(summary) < 15:
                return None

            return self.queue_finding({
                "summary": summary,
                "topic": topic,
                "goal": goal_desc[:80],
                "task": task_desc[:80],
            })

        except Exception as e:
            logger.debug("Finding evaluation failed: %s", e)
            return None


# ── Utilities ────────────────────────────────────────────────────────

def _parse_ts(ts_str: str) -> datetime:
    """Parse an ISO timestamp string, returning epoch on failure."""
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return datetime.min


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract a JSON object from model output."""
    import re
    text = text.strip()
    # Direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    # Try ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, TypeError):
            pass
    # Try first {...}
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass
    return None
