"""Tests for ask_user() helpers extracted in session 129."""
import threading
import time
from unittest.mock import patch, MagicMock

import src.interfaces.discord_bot as bot


class TestCheckQuietHours:
    """Tests for _check_quiet_hours()."""

    def test_returns_true_when_quiet(self):
        with patch("src.interfaces.discord_bot.is_quiet_hours", return_value=True, create=True):
            with patch.dict("sys.modules", {"src.utils.time_awareness": MagicMock(is_quiet_hours=MagicMock(return_value=True))}):
                assert bot._check_quiet_hours() is True

    def test_returns_false_when_not_quiet(self):
        with patch.dict("sys.modules", {"src.utils.time_awareness": MagicMock(is_quiet_hours=MagicMock(return_value=False))}):
            assert bot._check_quiet_hours() is False

    def test_returns_false_on_import_error(self):
        """If time_awareness module is unavailable, default to not quiet."""
        with patch.dict("sys.modules", {"src.utils.time_awareness": None}):
            assert bot._check_quiet_hours() is False


class TestTryPiggybackQuestion:
    """Tests for _try_piggyback_question()."""

    def setup_method(self):
        bot._pending_question = None
        bot._question_response = None

    def test_no_pending_question(self):
        """When no question is pending, returns (False, None)."""
        handled, result = bot._try_piggyback_question(timeout=1.0)
        assert handled is False
        assert result is None

    def test_piggybacks_on_pending_question(self):
        """When a question is pending, waits for the answer."""
        evt = threading.Event()
        bot._pending_question = evt
        bot._question_response = None

        # Simulate another thread answering after a short delay
        def _answer():
            time.sleep(0.05)
            with bot._question_lock:
                bot._question_response = "yes"
            evt.set()

        t = threading.Thread(target=_answer)
        t.start()

        handled, result = bot._try_piggyback_question(timeout=2.0)
        t.join()
        assert handled is True
        assert result == "yes"

    def test_piggyback_timeout(self):
        """When the pending question times out, returns (True, None)."""
        evt = threading.Event()
        bot._pending_question = evt
        bot._question_response = None

        handled, result = bot._try_piggyback_question(timeout=0.05)
        assert handled is True
        assert result is None

    def teardown_method(self):
        bot._pending_question = None
        bot._question_response = None


class TestMarkQuestionAnswered:
    """Tests for _mark_question_answered()."""

    def setup_method(self):
        bot._recent_questions.clear()

    def test_marks_matching_question(self):
        bot._recent_questions.append((time.time(), "What color?", False))
        bot._mark_question_answered("What color?")
        assert bot._recent_questions[0][2] is True

    def test_no_match_is_safe(self):
        """No crash when question isn't in the list."""
        bot._recent_questions.append((time.time(), "Other question", False))
        bot._mark_question_answered("Not in list")
        assert bot._recent_questions[0][2] is False  # unchanged

    def test_marks_most_recent_match(self):
        """When multiple matches exist, marks the last one."""
        now = time.time()
        bot._recent_questions.append((now - 5, "Same Q", True))
        bot._recent_questions.append((now, "Same Q", False))
        bot._mark_question_answered("Same Q")
        assert bot._recent_questions[0][2] is True  # already True
        assert bot._recent_questions[1][2] is True  # newly marked

    def teardown_method(self):
        bot._recent_questions.clear()
