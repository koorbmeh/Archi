"""
Learning System - Self-improvement through experience.

Archi learns from successes, failures, and feedback to improve
its performance over time.
"""

import json
import logging
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.utils.parsing import extract_json_array as _extract_json_array

logger = logging.getLogger(__name__)

class Experience:
    """A recorded experience (success or failure)."""

    def __init__(
        self,
        experience_type: str,  # 'success', 'failure', 'feedback'
        context: str,
        action: str,
        outcome: str,
        lesson: Optional[str] = None,
    ):
        self.experience_type = experience_type
        self.context = context
        self.action = action
        self.outcome = outcome
        self.lesson = lesson
        self.timestamp = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.experience_type,
            "context": self.context,
            "action": self.action,
            "outcome": self.outcome,
            "lesson": self.lesson,
            "timestamp": self.timestamp.isoformat(),
        }

class LearningSystem:
    """
    Manages Archi's learning and self-improvement.

    Tracks experiences, extracts patterns, and adapts behavior
    based on what works and what doesn't.

    Saves are batched: records are marked dirty and flushed every
    _FLUSH_INTERVAL experiences or on explicit flush() call.
    """

    _FLUSH_INTERVAL = 10  # Save to disk every N new experiences
    _MAX_EXPERIENCES = 500  # Cap in-memory list; older entries saved on disk

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else Path("data")
        self.data_dir.mkdir(exist_ok=True)

        self._lock = threading.Lock()  # Protects all mutable state below
        self.experiences: List[Experience] = []
        self.patterns: Dict[str, Any] = {}
        self.performance_metrics: Dict[str, List[float]] = defaultdict(list)
        self.action_stats: Dict[str, Dict[str, int]] = {}  # e.g. {"web_search": {"success": 5, "fail": 2}}
        self._dirty_count = 0  # Unsaved experiences since last flush

        self._load_experiences()

        logger.info("Learning System initialized")

    def record_success(
        self,
        context: str,
        action: str,
        outcome: str,
        lesson: Optional[str] = None,
    ) -> None:
        """
        Record a successful action.

        Args:
            context: Situation/task description
            action: What was done
            outcome: Positive result
            lesson: Optional insight learned
        """
        exp = Experience("success", context, action, outcome, lesson)
        with self._lock:
            self.experiences.append(exp)
            logger.info("Recorded success: %s", action)
            self._maybe_flush()

    def record_failure(
        self,
        context: str,
        action: str,
        outcome: str,
        lesson: Optional[str] = None,
    ) -> None:
        """
        Record a failed action.

        Args:
            context: Situation/task description
            action: What was attempted
            outcome: Negative result/error
            lesson: What was learned from failure
        """
        exp = Experience("failure", context, action, outcome, lesson)
        with self._lock:
            self.experiences.append(exp)
            logger.warning("Recorded failure: %s -> %s", action, outcome)
            self._maybe_flush()

    def record_feedback(
        self,
        context: str,
        action: str,
        feedback: str,
    ) -> None:
        """
        Record user feedback on an action.

        Args:
            context: What was being done
            action: What Archi did
            feedback: User's response/correction
        """
        exp = Experience("feedback", context, action, feedback, None)
        with self._lock:
            self.experiences.append(exp)
            logger.info("Recorded feedback: %s", feedback)
            self._maybe_flush()

    def track_metric(self, metric_name: str, value: float) -> None:
        """
        Track a performance metric over time.

        Args:
            metric_name: Name of metric (e.g., 'task_completion_rate')
            value: Numeric value
        """
        with self._lock:
            self.performance_metrics[metric_name].append(value)
        logger.debug("Tracked metric: %s = %s", metric_name, value)

    def get_metric_trend(
        self, metric_name: str, window: int = 10
    ) -> Optional[str]:
        """
        Get trend for a metric (improving, declining, stable).

        Args:
            metric_name: Metric to analyze
            window: Number of recent values to consider

        Returns:
            'improving', 'declining', 'stable', or None
        """
        values = self.performance_metrics.get(metric_name, [])

        if len(values) < 4:
            return None

        recent = values[-min(window, len(values)):]
        half = len(recent) // 2
        first_half = sum(recent[:half]) / half if half > 0 else 0
        second_half = sum(recent[half:]) / (len(recent) - half) if (len(recent) - half) > 0 else 0

        diff_percent = (
            ((second_half - first_half) / first_half) * 100 if first_half > 0 else 0
        )

        if diff_percent > 5:
            return "improving"
        elif diff_percent < -5:
            return "declining"
        else:
            return "stable"

    def extract_patterns(self, model: Any) -> List[str]:
        """
        Analyze experiences to extract patterns and insights.

        Args:
            model: AI model with generate(prompt, max_tokens, temperature) -> {text}

        Returns:
            List of extracted patterns/insights
        """
        # --- Lock: snapshot experiences for the prompt ---
        with self._lock:
            if len(self.experiences) < 5:
                logger.info("Not enough experiences to extract patterns")
                return []

            recent = list(self.experiences[-20:])

        summary = "\n".join(
            [
                f"{exp.experience_type.upper()}: {exp.context} -> {exp.action} -> {exp.outcome}"
                for exp in recent
            ]
        )

        prompt = f"""Analyze these recent experiences and extract actionable patterns:

{summary}

Identify:
1. What strategies work well (repeated successes)
2. What to avoid (repeated failures)
3. Patterns in successful approaches
4. Areas for improvement

Return a JSON array of insights:
[
  "pattern or insight 1",
  "pattern or insight 2",
  ...
]

Focus on specific, actionable insights."""

        try:
            # --- No lock during API call ---
            response = model.generate(
                prompt, max_tokens=500, temperature=0.5
            )
            text = response.get("text", "").strip()
            if not text:
                return []

            patterns = _extract_json_array(text, allow_prose_fallback=True)
            if not isinstance(patterns, list):
                return []

            # --- Lock: write patterns back ---
            with self._lock:
                self.patterns["last_analysis"] = datetime.now().isoformat()
                self.patterns["insights"] = patterns
                self._save_experiences()

            logger.info("Extracted %d patterns from experiences", len(patterns))
            return patterns

        except Exception as e:
            logger.error("Pattern extraction failed: %s", e)
            return []

    def get_improvement_suggestions(self, model: Any) -> List[str]:
        """
        Get specific suggestions for self-improvement.

        Args:
            model: AI model for analysis

        Returns:
            List of improvement suggestions
        """
        # --- Lock: snapshot data for the prompt ---
        with self._lock:
            metrics_summary = []
            for metric, values in self.performance_metrics.items():
                if values:
                    trend = self.get_metric_trend(metric)
                    avg = sum(values[-10:]) / min(len(values), 10)
                    metrics_summary.append(
                        f"{metric}: {avg:.2f} ({trend or 'N/A'})"
                    )

            recent_failures = [
                exp for exp in self.experiences[-20:]
                if exp.experience_type == "failure"
            ]

        prompt = f"""Based on performance data, suggest specific improvements.

Performance Metrics:
{chr(10).join(metrics_summary) if metrics_summary else 'No metrics yet'}

Recent Failures:
{chr(10).join([f"- {f.context}: {f.outcome}" for f in recent_failures]) if recent_failures else 'None'}

Provide 3-5 specific, actionable improvements I can make.

Return a JSON array:
[
  "suggestion 1",
  "suggestion 2",
  ...
]"""

        try:
            response = model.generate(
                prompt, max_tokens=400, temperature=0.6
            )
            text = response.get("text", "").strip()
            if not text:
                return []

            suggestions = _extract_json_array(text, allow_prose_fallback=True)
            if not isinstance(suggestions, list):
                return []

            logger.info("Generated %d improvement suggestions", len(suggestions))
            return suggestions

        except Exception as e:
            logger.error("Suggestion generation failed: %s", e)
            return []

    # -- Proactive error prevention -------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> set:
        """Extract meaningful lowercase word tokens (len >= 3) from text."""
        return {
            w for w in text.lower().split()
            if len(w) >= 3 and w.isalpha()
        }

    def get_failure_warnings(
        self,
        task_description: str,
        goal_description: str = "",
        limit: int = 3,
        min_overlap: int = 2,
    ) -> List[str]:
        """Return task-specific warnings derived from recent failure experiences.

        Scans the last 100 failure experiences for keyword overlap with the
        current task description (and optional goal description).  Groups
        similar outcomes to avoid duplicate warnings.

        Args:
            task_description: The task about to be executed.
            goal_description: Parent goal context (improves matching).
            limit: Maximum number of warnings to return.
            min_overlap: Minimum keyword overlap to consider a failure relevant.

        Returns:
            List of concise warning strings (0 to *limit*).
        """
        with self._lock:
            failures = [
                e for e in self.experiences
                if e.experience_type == "failure"
            ]
        if not failures:
            return []

        task_words = self._tokenize(f"{task_description} {goal_description}")
        if len(task_words) < 2:
            return []

        # Score each failure by keyword overlap with the current task
        scored = []
        for f in failures[-100:]:
            ctx_words = self._tokenize(f"{f.context} {f.action}")
            overlap = len(task_words & ctx_words)
            if overlap >= min_overlap:
                scored.append((overlap, f))

        if not scored:
            return []

        scored.sort(key=lambda x: -x[0])

        # Deduplicate by outcome similarity (first 60 chars of outcome)
        seen_outcomes: set = set()
        warnings: List[str] = []
        for _, failure in scored:
            outcome_key = failure.outcome[:60].lower().strip()
            if outcome_key in seen_outcomes:
                continue
            seen_outcomes.add(outcome_key)

            action_brief = failure.action[:80]
            outcome_brief = failure.outcome[:150]
            warnings.append(
                f"CAUTION (past failure on similar task): "
                f"'{action_brief}' failed with: {outcome_brief}. "
                f"Avoid this pattern."
            )
            if len(warnings) >= limit:
                break

        if warnings:
            logger.info(
                "Injected %d failure warnings for task: %s",
                len(warnings), task_description[:60],
            )
        return warnings

    # -- Feedback loop helpers ------------------------------------------------

    def get_active_insights(self, limit: int = 3) -> List[str]:
        """
        Return the most recent extracted patterns for injection into prompts.

        These are the actual "lessons learned" that should influence future
        brainstorming, goal decomposition, and task execution.

        Args:
            limit: Max number of insights to return.

        Returns:
            List of short insight strings (deduplicated).
        """
        with self._lock:
            raw = list(self.patterns.get("insights", []))
        # Deduplicate while preserving order
        seen: set = set()
        unique: List[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(item.strip())

        insights = unique[:limit]

        # Append skill inventory if available
        try:
            from src.core.skill_system import get_shared_skill_registry
            inventory = get_shared_skill_registry().get_skill_inventory(limit=3)
            if inventory:
                insights.append(inventory)
        except Exception:
            pass

        return insights

    # -- Skill tracking ----------------------------------------------------

    def record_skill_created(self, skill_name: str, from_pattern: str) -> None:
        """Record that a skill was successfully created."""
        with self._lock:
            self.patterns[f"created_skill:{skill_name}"] = from_pattern
            self._maybe_flush()
        self.record_success(
            context=f"Skill creation: {skill_name}",
            action="create_skill",
            outcome=f"Created skill '{skill_name}'",
            lesson=f"Pattern '{from_pattern}' was common enough to generalize",
        )

    def record_skill_suggested(self, skill_name: str) -> None:
        """Record that a skill creation was suggested to the user."""
        with self._lock:
            suggested = self.patterns.setdefault("suggested_skills", [])
            if skill_name not in suggested:
                suggested.append(skill_name)
                self._maybe_flush()
        logger.info("Recorded skill suggestion: %s", skill_name)

    def record_action_outcome(self, action_type: str, success: bool) -> None:
        """
        Track success/failure rate per PlanExecutor action type.

        Called after every step in PlanExecutor so we know which tools
        are reliable and which tend to fail.

        Args:
            action_type: e.g. "web_search", "create_file", "fetch_webpage"
            success: Whether the step succeeded.
        """
        with self._lock:
            if action_type not in self.action_stats:
                self.action_stats[action_type] = {"success": 0, "fail": 0}
            if success:
                self.action_stats[action_type]["success"] += 1
            else:
                self.action_stats[action_type]["fail"] += 1

    def get_action_summary(self) -> str:
        """
        One-line summary of action-type success rates for prompt injection.

        Returns something like:
            "Reliable: web_search (87%), create_file (95%). Weak: fetch_webpage (40%)."
        Or empty string if not enough data.
        """
        with self._lock:
            rates: List[tuple] = []  # (action, rate)
            for action, stats in self.action_stats.items():
                total = stats.get("success", 0) + stats.get("fail", 0)
                if total >= 3:  # Need at least 3 data points
                    rate = stats["success"] / total
                    rates.append((action, rate))

        if not rates:
            return ""

        rates.sort(key=lambda x: x[1], reverse=True)
        best = [f"{a} ({r:.0%})" for a, r in rates if r >= 0.6]
        weak = [f"{a} ({r:.0%})" for a, r in rates if r < 0.6]

        parts = []
        if best:
            parts.append("Reliable: " + ", ".join(best[:3]))
        if weak:
            parts.append("Weak: " + ", ".join(weak[:2]))
        return ". ".join(parts) + "." if parts else ""

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of learning progress."""
        with self._lock:
            total = len(self.experiences)
            successes = sum(
                1 for e in self.experiences if e.experience_type == "success"
            )
            failures = sum(
                1 for e in self.experiences if e.experience_type == "failure"
            )

            return {
                "total_experiences": total,
                "successes": successes,
                "failures": failures,
                "success_rate": (successes / total * 100) if total > 0 else 0,
                "tracked_metrics": list(self.performance_metrics.keys()),
                "patterns_extracted": len(self.patterns.get("insights", [])),
                "last_pattern_analysis": self.patterns.get("last_analysis"),
            }

    def _maybe_flush(self) -> None:
        """Increment dirty counter and flush to disk if threshold reached.

        MUST be called with self._lock already held (called from record_*).
        Also trims experiences to _MAX_EXPERIENCES to prevent unbounded growth.
        """
        self._dirty_count += 1
        if self._dirty_count >= self._FLUSH_INTERVAL:
            self._save_experiences()
            self._dirty_count = 0
        if len(self.experiences) > self._MAX_EXPERIENCES:
            self.experiences = self.experiences[-self._MAX_EXPERIENCES:]

    def flush(self) -> None:
        """Force save any unsaved experiences to disk (call on shutdown)."""
        with self._lock:
            if self._dirty_count > 0:
                self._save_experiences()
                self._dirty_count = 0

    def _save_experiences(self) -> None:
        """Save experiences to disk."""
        exp_file = self.data_dir / "experiences.json"

        data = {
            "experiences": [e.to_dict() for e in self.experiences],
            "patterns": self.patterns,
            "metrics": {k: list(v) for k, v in self.performance_metrics.items()},
            "action_stats": self.action_stats,
        }

        with open(exp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _load_experiences(self) -> None:
        """Load experiences from disk."""
        exp_file = self.data_dir / "experiences.json"

        if not exp_file.exists():
            return

        try:
            with open(exp_file, encoding="utf-8") as f:
                data = json.load(f)

            for exp_data in data.get("experiences", []):
                exp = Experience(
                    exp_data["type"],
                    exp_data["context"],
                    exp_data["action"],
                    exp_data["outcome"],
                    exp_data.get("lesson"),
                )
                if "timestamp" in exp_data:
                    exp.timestamp = datetime.fromisoformat(
                        exp_data["timestamp"]
                    )
                self.experiences.append(exp)

            self.patterns = data.get("patterns", {})

            for metric, values in data.get("metrics", {}).items():
                self.performance_metrics[metric] = values

            self.action_stats = data.get("action_stats", {})

            # Trim to cap if persisted file grew large from older versions
            if len(self.experiences) > self._MAX_EXPERIENCES:
                self.experiences = self.experiences[-self._MAX_EXPERIENCES:]

            logger.info("Loaded %d experiences from disk", len(self.experiences))

        except Exception as e:
            logger.error("Failed to load experiences: %s", e)
