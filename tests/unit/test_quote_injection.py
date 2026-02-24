"""Tests for context-aware quote injection from personality.yaml."""

import sys
from pathlib import Path
from unittest.mock import patch

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.utils.config import get_relevant_quote, _QUOTE_KEYWORDS


class TestGetRelevantQuote:
    """Test get_relevant_quote() keyword matching and probability gate."""

    FAKE_QUOTES = [
        {"source": "Marcus Aurelius", "text": "The impediment...", "use": "obstacles"},
        {"source": "Epictetus", "text": "It's not what happens...", "use": "frustration"},
        {"source": "Seneca", "text": "We suffer more...", "use": "anxiety"},
        {"source": "Sun Tzu", "text": "Every battle is won...", "use": "preparation"},
        {"source": "Franklin", "text": "An investment in knowledge...", "use": "learning"},
        {"source": "Rohn", "text": "Don't wish it were easier...", "use": "shortcuts"},
        {"source": "Carlin", "text": "Think about how stupid...", "use": "never"},
        {"source": "Nietzsche", "text": "He who has a why...", "use": "purpose"},
        {"source": "Diogenes", "text": "It is the privilege...", "use": "simplicity"},
        {"source": "Ruiz", "text": "Be impeccable with your word.", "use": "always"},
        {"source": "Orwell", "text": "In a time of deceit...", "use": "honesty"},
        {"source": "Dobelli", "text": "If 50 million people...", "use": "consensus"},
        {"source": "Aristotle", "text": "We are what we repeatedly do...", "use": "habits"},
        {"source": "Greene", "text": "The future belongs...", "use": "skills"},
        {"source": "Gaarder", "text": "The only thing we require...", "use": "curiosity"},
    ]

    @patch("src.utils.config._personality")
    def test_returns_none_when_no_match(self, mock_p):
        mock_p.return_value = {"guiding_quotes": self.FAKE_QUOTES}
        assert get_relevant_quote("hello there how are you") is None

    @patch("src.utils.config._personality")
    def test_returns_none_when_no_quotes(self, mock_p):
        mock_p.return_value = {}
        assert get_relevant_quote("I'm stuck on this obstacle") is None

    @patch("src.utils.config._personality")
    @patch("src.utils.config.random.random", return_value=0.01)
    def test_returns_quote_when_match_and_probability_passes(self, mock_rand, mock_p):
        mock_p.return_value = {"guiding_quotes": self.FAKE_QUOTES}
        result = get_relevant_quote("I'm stuck and hit a wall")
        assert result is not None
        assert result["source"] == "Marcus Aurelius"
        assert "impediment" in result["text"].lower() or result["text"]

    @patch("src.utils.config._personality")
    @patch("src.utils.config.random.random", return_value=0.99)
    def test_returns_none_when_probability_fails(self, mock_rand, mock_p):
        mock_p.return_value = {"guiding_quotes": self.FAKE_QUOTES}
        assert get_relevant_quote("I'm stuck and hit a wall") is None

    @patch("src.utils.config._personality")
    @patch("src.utils.config.random.random", return_value=0.01)
    def test_matches_anxiety_keywords(self, mock_rand, mock_p):
        mock_p.return_value = {"guiding_quotes": self.FAKE_QUOTES}
        result = get_relevant_quote("I'm worried about this deadline")
        assert result is not None
        assert result["source"] == "Seneca"

    @patch("src.utils.config._personality")
    @patch("src.utils.config.random.random", return_value=0.01)
    def test_matches_habit_keywords(self, mock_rand, mock_p):
        mock_p.return_value = {"guiding_quotes": self.FAKE_QUOTES}
        result = get_relevant_quote("I need to build a consistent routine")
        assert result is not None
        assert result["source"] == "Aristotle"

    @patch("src.utils.config._personality")
    @patch("src.utils.config.random.random", return_value=0.01)
    @patch("src.utils.config.random.choice")
    def test_multiple_matches_picks_randomly(self, mock_choice, mock_rand, mock_p):
        """When multiple keywords match, a random quote is picked."""
        mock_p.return_value = {"guiding_quotes": self.FAKE_QUOTES}
        mock_choice.side_effect = lambda lst: lst[0]
        # "research" → Franklin, "curiosity" → Gaarder
        result = get_relevant_quote("my curiosity led me to research this")
        assert result is not None
        assert result["source"] in ("Franklin", "Gaarder")

    def test_keyword_indices_in_range(self):
        """All keyword indices reference valid positions in a 15-quote list."""
        for keywords, idx in _QUOTE_KEYWORDS:
            assert idx < 15, f"Index {idx} out of range for keywords {keywords}"
            assert idx != 6, "Index 6 (Carlin) should be skipped"

    @patch("src.utils.config._personality")
    @patch("src.utils.config.random.random", return_value=0.01)
    def test_skips_carlin(self, mock_rand, mock_p):
        """Carlin quote (index 6) has no keyword mapping — never surfaces."""
        mock_p.return_value = {"guiding_quotes": self.FAKE_QUOTES}
        # Run many messages — none should return Carlin
        messages = [
            "think about how stupid", "average person", "stupid people",
            "comfortable illusions", "cut through the noise",
        ]
        for msg in messages:
            result = get_relevant_quote(msg)
            if result:
                assert result["source"] != "Carlin"
