"""
Skill Suggestions — Pattern detection for automatic skill creation.

Runs during dream cycles to:
1. Scan the learning system for repeated action patterns
2. Propose new skills when patterns emerge
3. Format suggestions for user notification
4. Track which suggestions were accepted/rejected
"""

import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.skill_creator import SkillProposal

logger = logging.getLogger(__name__)

# Minimum occurrences of a similar action before suggesting a skill.
MIN_PATTERN_OCCURRENCES = 3

# Minimum confidence to surface a suggestion to the user.
MIN_SUGGEST_CONFIDENCE = 0.6


class SkillSuggestions:
    """Detects patterns in experiences and suggests skill creation."""

    def __init__(self, state_path: Optional[Path] = None):
        if state_path:
            self._state_path = state_path
        else:
            try:
                from src.utils.paths import base_path
                self._state_path = Path(base_path()) / "data" / "skill_suggestions_state.json"
            except ImportError:
                self._state_path = Path("data") / "skill_suggestions_state.json"

        self._state = self._load_state()

    def scan_for_suggestions(
        self,
        learning_system: Any,
        skill_registry: Any,
    ) -> List[SkillProposal]:
        """Scan experiences for patterns that could become skills.

        Args:
            learning_system: LearningSystem with .experiences attribute.
            skill_registry: SkillRegistry to check for already-existing skills.

        Returns:
            List of SkillProposal candidates.
        """
        proposals = []

        try:
            experiences = list(learning_system.experiences)
        except Exception:
            return proposals

        if len(experiences) < MIN_PATTERN_OCCURRENCES:
            return proposals

        existing_skills = set()
        try:
            existing_skills = set(skill_registry.get_available_skills())
        except Exception:
            pass

        # Get already-suggested names to avoid re-suggesting
        already_suggested = set(self._state.get("suggested_names", []))

        # Detection 1: Repeated action types with similar contexts
        action_proposals = self._detect_repeated_actions(experiences)
        for proposal in action_proposals:
            if (proposal.name not in existing_skills
                    and proposal.name not in already_suggested
                    and proposal.confidence >= MIN_SUGGEST_CONFIDENCE):
                proposals.append(proposal)

        # Detection 2: Multi-step sequences that repeat
        sequence_proposals = self._detect_repeated_sequences(experiences)
        for proposal in sequence_proposals:
            if (proposal.name not in existing_skills
                    and proposal.name not in already_suggested
                    and proposal.confidence >= MIN_SUGGEST_CONFIDENCE):
                proposals.append(proposal)

        # Record scan
        self._state["last_scan"] = datetime.now().isoformat()
        self._state["last_scan_experience_count"] = len(experiences)
        self._save_state()

        if proposals:
            logger.info("Found %d skill creation opportunities", len(proposals))

        return proposals

    def format_suggestions_for_user(self, proposals: List[SkillProposal]) -> str:
        """Format proposals as a readable message for the user.

        Returns a string suitable for sending via Discord.
        """
        if not proposals:
            return ""

        lines = ["I noticed some patterns that could become reusable skills:\n"]
        for i, p in enumerate(proposals[:3], 1):
            conf = f"{p.confidence:.0%}"
            lines.append(f"  {i}. **{p.description}** (confidence: {conf})")
            if p.pattern_description:
                lines.append(f"     Pattern: {p.pattern_description[:100]}")

        lines.append(
            "\nSay `/skill create <description>` to create any of these, "
            "or I can create them during my next work cycle."
        )

        return "\n".join(lines)

    def record_suggestion(self, proposal: SkillProposal) -> None:
        """Record that a suggestion was made to the user."""
        suggested = self._state.setdefault("suggested_names", [])
        if proposal.name not in suggested:
            suggested.append(proposal.name)

        history = self._state.setdefault("suggestion_history", [])
        history.append({
            "name": proposal.name,
            "description": proposal.description,
            "confidence": proposal.confidence,
            "source": proposal.source,
            "suggested_at": datetime.now().isoformat(),
            "user_response": "pending",
        })

        # Cap history
        if len(history) > 50:
            self._state["suggestion_history"] = history[-50:]

        self._save_state()

    def record_user_response(self, skill_name: str, accepted: bool) -> None:
        """Record whether the user accepted or rejected a suggestion."""
        for entry in reversed(self._state.get("suggestion_history", [])):
            if entry.get("name") == skill_name and entry.get("user_response") == "pending":
                entry["user_response"] = "accepted" if accepted else "rejected"
                entry["responded_at"] = datetime.now().isoformat()
                break
        self._save_state()

    # -- Pattern detection -------------------------------------------------

    def _detect_repeated_actions(
        self, experiences: List[Any],
    ) -> List[SkillProposal]:
        """Find action types that repeat with similar context."""
        proposals = []

        # Group successful experiences by action type
        action_groups: Dict[str, List[Any]] = {}
        for exp in experiences:
            if hasattr(exp, "experience_type") and exp.experience_type == "success":
                action = getattr(exp, "action", "")
                if action:
                    action_groups.setdefault(action, []).append(exp)

        for action, exps in action_groups.items():
            if len(exps) < MIN_PATTERN_OCCURRENCES:
                continue

            # Skip actions that are already built-in tools
            builtin_actions = {
                "web_search", "fetch_webpage", "create_file", "read_file",
                "list_files", "write_source", "edit_file", "run_python",
                "run_command", "ask_user", "append_file",
            }
            if action in builtin_actions:
                continue

            # Check context similarity (are these similar tasks?)
            contexts = [getattr(e, "context", "") for e in exps]
            # Simple heuristic: if many contexts share words, it's a pattern
            common_words = self._find_common_words(contexts)
            if len(common_words) >= 2:
                pattern_desc = f"Repeated '{action}' in contexts about: {', '.join(common_words[:5])}"
                name = f"auto_{action}_{'_'.join(common_words[:3])}"

                proposals.append(SkillProposal(
                    name=name[:40],
                    description=f"Automate the repeated pattern: {pattern_desc}",
                    confidence=min(0.5 + len(exps) * 0.1, 0.95),
                    source="pattern_detection",
                    pattern_description=pattern_desc,
                ))

        return proposals

    def _detect_repeated_sequences(
        self, experiences: List[Any],
    ) -> List[SkillProposal]:
        """Find multi-step action sequences that repeat."""
        proposals = []

        # Extract action sequences (sliding window of 3)
        actions = [
            getattr(e, "action", "unknown")
            for e in experiences
            if hasattr(e, "experience_type") and e.experience_type == "success"
        ]

        if len(actions) < 6:
            return proposals

        # Count 3-action sequences
        sequences = Counter()
        for i in range(len(actions) - 2):
            seq = (actions[i], actions[i + 1], actions[i + 2])
            sequences[seq] += 1

        for seq, count in sequences.most_common(3):
            if count < MIN_PATTERN_OCCURRENCES:
                continue
            # Skip all-same-action sequences
            if len(set(seq)) == 1:
                continue

            seq_desc = " → ".join(seq)
            name = f"auto_seq_{'_'.join(seq)}"

            proposals.append(SkillProposal(
                name=name[:40],
                description=f"Automate the sequence: {seq_desc} (seen {count} times)",
                confidence=min(0.4 + count * 0.15, 0.9),
                source="pattern_detection",
                pattern_description=f"Sequence {seq_desc} repeated {count} times",
            ))

        return proposals

    # -- Helpers -----------------------------------------------------------

    def _find_common_words(self, texts: List[str], min_freq: int = 2) -> List[str]:
        """Find words that appear frequently across multiple texts."""
        word_counts: Counter = Counter()
        skip_words = {
            "the", "a", "an", "to", "for", "of", "in", "on", "and", "or",
            "is", "was", "are", "were", "be", "been", "with", "that", "this",
            "from", "at", "by", "it", "not", "but", "as",
        }
        for text in texts:
            words = set(text.lower().split()) - skip_words
            meaningful = {w for w in words if len(w) >= 3 and w.isalpha()}
            for w in meaningful:
                word_counts[w] += 1

        return [w for w, c in word_counts.most_common(10) if c >= min_freq]

    def _load_state(self) -> Dict[str, Any]:
        """Load suggestion state from disk."""
        if self._state_path.is_file():
            try:
                return json.loads(self._state_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.debug("Failed to load suggestion state: %s", e)
        return {}

    def _save_state(self) -> None:
        """Save suggestion state to disk."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(self._state, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("Failed to save suggestion state: %s", e)
