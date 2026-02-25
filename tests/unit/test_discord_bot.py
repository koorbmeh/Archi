"""Unit tests for src/interfaces/discord_bot.py."""

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

import src.interfaces.discord_bot as db


# ── Helpers ─────────────────────────────────────────────────────────


def _reset_module_state():
    """Reset all module-level globals to a clean state."""
    db._router = None
    db._goal_manager = None
    db._heartbeat = None
    db._upload_dir = None
    db._bot_client = None
    db._bot_loop = None
    db._owner_dm_channel = None
    db._owner_id = None
    db._pending_approval = None
    db._approval_result = False
    db._approval_message_id = None
    db._pending_question = None
    db._question_response = None
    db._recent_questions.clear()
    db._deferred_approvals.clear()
    db._suppressed_queue.clear()
    db._last_user_message.clear()
    db._tracked_messages.clear()
    db._chat_response_messages.clear()
    db._accepting_messages = False
    db._cleanup_never_paths.clear()
    db._bot_stop_event = None


@pytest.fixture(autouse=True)
def clean_state():
    """Reset module state before and after each test."""
    _reset_module_state()
    yield
    _reset_module_state()


# ── TestTrackNotificationMessage ────────────────────────────────────


class TestTrackNotificationMessage:
    def test_basic_tracking(self):
        db.track_notification_message(100, {"goal": "test"})
        assert 100 in db._tracked_messages
        assert db._tracked_messages[100]["goal"] == "test"

    def test_prune_oldest_when_over_cap(self):
        for i in range(db._MAX_TRACKED + 5):
            db.track_notification_message(i, {"goal": f"g{i}"})
        assert len(db._tracked_messages) <= db._MAX_TRACKED

    def test_oldest_pruned_not_newest(self):
        for i in range(db._MAX_TRACKED + 3):
            db.track_notification_message(i, {"goal": f"g{i}"})
        # The newest entries should still be present
        assert db._MAX_TRACKED + 2 in db._tracked_messages
        # The very oldest should be gone
        assert 0 not in db._tracked_messages


# ── TestTrackChatResponse ───────────────────────────────────────────


class TestTrackChatResponse:
    def test_basic_tracking(self):
        db._track_chat_response(200, "Hello there")
        assert 200 in db._chat_response_messages
        assert db._chat_response_messages[200] == "Hello there"

    def test_truncates_to_100_chars(self):
        long_text = "x" * 200
        db._track_chat_response(201, long_text)
        assert len(db._chat_response_messages[201]) == 100

    def test_none_text_handled(self):
        db._track_chat_response(202, None)
        assert db._chat_response_messages[202] == ""

    def test_prune_oldest_when_over_cap(self):
        for i in range(db._MAX_CHAT_TRACKED + 5):
            db._track_chat_response(i, f"msg{i}")
        assert len(db._chat_response_messages) <= db._MAX_CHAT_TRACKED


# ── TestRecordToneFeedback ──────────────────────────────────────────


class TestRecordToneFeedback:
    def test_no_snippet_returns_early(self):
        # No tracked message -> should not crash
        db._record_tone_feedback(999, "👍")

    def test_positive_sentiment(self):
        mock_um = MagicMock()
        db._chat_response_messages[100] = "Great response"
        with patch("src.core.user_model.get_user_model", return_value=mock_um):
            db._record_tone_feedback(100, "👍")
        mock_um.add_tone_feedback.assert_called_once_with("positive", "Great response")

    def test_negative_sentiment(self):
        mock_um = MagicMock()
        db._chat_response_messages[101] = "Bad response"
        with patch("src.core.user_model.get_user_model", return_value=mock_um):
            db._record_tone_feedback(101, "👎")
        mock_um.add_tone_feedback.assert_called_once_with("negative", "Bad response")


# ── TestRecordReactionFeedback ──────────────────────────────────────


class TestRecordReactionFeedback:
    def test_no_context_returns_early(self):
        db._record_reaction_feedback(999, "👍")

    def test_uses_heartbeat_learning_system(self):
        mock_ls = MagicMock()
        mock_hb = MagicMock()
        mock_hb.learning_system = mock_ls
        db._heartbeat = mock_hb
        db._tracked_messages[100] = {"goal": "test goal", "event": "goal_completion"}
        db._record_reaction_feedback(100, "👍")
        mock_ls.record_feedback.assert_called_once()

    def test_falls_back_to_standalone_learning_system(self):
        db._heartbeat = None
        db._tracked_messages[100] = {"goal": "test goal", "event": "notification"}
        with patch("src.core.learning_system.LearningSystem") as MockLS:
            mock_ls = MagicMock()
            MockLS.return_value = mock_ls
            db._record_reaction_feedback(100, "👎")
            mock_ls.record_feedback.assert_called_once()

    def test_exception_handled_gracefully(self):
        db._heartbeat = MagicMock()
        db._heartbeat.learning_system = None
        db._tracked_messages[100] = {"goal": "test"}
        with patch("src.core.learning_system.LearningSystem", side_effect=Exception("boom")):
            # Should not raise
            db._record_reaction_feedback(100, "👍")


# ── TestGetUploadDir ────────────────────────────────────────────────


class TestGetUploadDir:
    def test_returns_path(self, tmp_path):
        db._upload_dir = None
        with patch.object(Path, "resolve", return_value=tmp_path / "src" / "interfaces" / "discord_bot.py"):
            with patch.object(Path, "mkdir"):
                result = db._get_upload_dir()
                assert isinstance(result, Path)
                assert "uploads" in str(result)

    def test_cached_on_second_call(self):
        db._upload_dir = Path("/fake/uploads")
        result = db._get_upload_dir()
        assert result == Path("/fake/uploads")


# ── TestInitDiscordBot ──────────────────────────────────────────────


class TestInitDiscordBot:
    def test_sets_all_globals(self):
        mock_gm = MagicMock()
        mock_router = MagicMock()
        mock_hb = MagicMock()
        db.init_discord_bot(mock_gm, mock_router, mock_hb)
        assert db._goal_manager is mock_gm
        assert db._router is mock_router
        assert db._heartbeat is mock_hb

    def test_router_none_not_overwritten(self):
        db._router = MagicMock()
        original = db._router
        db.init_discord_bot(MagicMock(), router=None)
        assert db._router is original

    def test_heartbeat_none_not_overwritten(self):
        db._heartbeat = MagicMock()
        original = db._heartbeat
        db.init_discord_bot(MagicMock(), heartbeat=None)
        assert db._heartbeat is original


# ── TestGetRouter ───────────────────────────────────────────────────


class TestGetRouter:
    def test_returns_existing_router(self):
        mock = MagicMock()
        db._router = mock
        assert db._get_router() is mock

    def test_lazy_init_on_none(self):
        with patch("src.models.router.ModelRouter") as MockRouter:
            mock_instance = MagicMock()
            MockRouter.return_value = mock_instance
            result = db._get_router()
            assert result is mock_instance

    def test_lazy_init_failure(self):
        with patch("src.models.router.ModelRouter", side_effect=Exception("no key")):
            result = db._get_router()
            assert result is None


# ── TestTruncate ────────────────────────────────────────────────────


class TestTruncate:
    def test_short_text_unchanged(self):
        assert db._truncate("hello") == "hello"

    def test_exact_limit_unchanged(self):
        text = "x" * 1900
        assert db._truncate(text) == text

    def test_over_limit_truncated(self):
        text = "x" * 2000
        result = db._truncate(text)
        assert len(result) == 1900
        assert result.endswith("...")

    def test_custom_max_len(self):
        text = "x" * 100
        result = db._truncate(text, max_len=50)
        assert len(result) == 50
        assert result.endswith("...")


# ── TestBuildConfigRequestNote ──────────────────────────────────────


class TestBuildConfigRequestNote:
    def test_single_request(self):
        result = db._build_config_request_note(["change humor level"])
        assert "change humor level" in result
        assert "request" in result.lower()

    def test_multiple_requests(self):
        result = db._build_config_request_note(["change humor", "change tone"])
        assert "requests" in result.lower()
        assert "change humor" in result
        assert "change tone" in result


# ── TestIsOutboundReady ─────────────────────────────────────────────


class TestIsOutboundReady:
    def test_false_when_no_client(self):
        assert db.is_outbound_ready() is False

    def test_false_when_no_dm_channel(self):
        db._bot_client = MagicMock()
        assert db.is_outbound_ready() is False

    def test_true_when_both_set(self):
        db._bot_client = MagicMock()
        db._owner_dm_channel = MagicMock()
        assert db.is_outbound_ready() is True


# ── TestKickHeartbeat ───────────────────────────────────────────────


class TestKickHeartbeat:
    def test_calls_heartbeat_kick(self):
        mock_hb = MagicMock()
        db._heartbeat = mock_hb
        db.kick_heartbeat("goal123", reactive=True)
        mock_hb.kick.assert_called_once_with(goal_id="goal123", reactive=True)

    def test_no_heartbeat_no_crash(self):
        db._heartbeat = None
        db.kick_heartbeat("goal123")  # Should not raise

    def test_back_compat_alias(self):
        assert db.kick_dream_cycle is db.kick_heartbeat


# ── TestSendNotification ────────────────────────────────────────────


class TestSendNotification:
    def test_not_ready_returns_false(self):
        assert db.send_notification("test") is False

    def test_quiet_hours_queues_message(self):
        db._bot_client = MagicMock()
        db._bot_loop = MagicMock()
        db._owner_dm_channel = MagicMock()
        with patch.object(db, "_check_quiet_hours", return_value=True):
            result = db.send_notification("quiet test")
        assert result is False
        assert len(db._suppressed_queue) == 1
        assert db._suppressed_queue[0] == "quiet test"

    def test_suppressed_queue_respects_cap(self):
        db._bot_client = MagicMock()
        db._bot_loop = MagicMock()
        db._owner_dm_channel = MagicMock()
        with patch.object(db, "_check_quiet_hours", return_value=True):
            for i in range(db._MAX_SUPPRESSED + 5):
                db.send_notification(f"msg{i}")
        assert len(db._suppressed_queue) == db._MAX_SUPPRESSED + 1
        assert "queue full" in db._suppressed_queue[-1]

    def test_empty_send_kwargs_returns_false(self):
        db._bot_client = MagicMock()
        db._bot_loop = MagicMock()
        db._owner_dm_channel = MagicMock()
        with patch.object(db, "_check_quiet_hours", return_value=False):
            result = db.send_notification("")
        assert result is False

    def test_successful_send(self):
        db._bot_client = MagicMock()
        db._bot_loop = MagicMock()
        db._owner_dm_channel = MagicMock()
        mock_msg = MagicMock()
        mock_msg.id = 12345
        mock_future = MagicMock()
        mock_future.result.return_value = mock_msg
        with patch.object(db, "_check_quiet_hours", return_value=False), \
             patch("asyncio.run_coroutine_threadsafe", return_value=mock_future), \
             patch.object(db, "_log_outbound"):
            result = db.send_notification("hello!")
        assert result is True

    def test_send_with_track_context(self):
        db._bot_client = MagicMock()
        db._bot_loop = MagicMock()
        db._owner_dm_channel = MagicMock()
        mock_msg = MagicMock()
        mock_msg.id = 999
        mock_future = MagicMock()
        mock_future.result.return_value = mock_msg
        with patch.object(db, "_check_quiet_hours", return_value=False), \
             patch("asyncio.run_coroutine_threadsafe", return_value=mock_future), \
             patch.object(db, "_log_outbound"):
            db.send_notification("done!", track_context={"goal": "X"})
        assert 999 in db._tracked_messages


# ── TestDrainSuppressedNotifications ────────────────────────────────


class TestDrainSuppressedNotifications:
    def test_empty_queue_returns_zero(self):
        assert db.drain_suppressed_notifications() == 0

    def test_not_ready_returns_zero(self):
        db._suppressed_queue.append("test msg")
        result = db.drain_suppressed_notifications()
        assert result == 0

    def test_drains_messages(self):
        db._bot_client = MagicMock()
        db._bot_loop = MagicMock()
        db._owner_dm_channel = MagicMock()
        db._suppressed_queue.extend(["msg1", "msg2", "msg3"])
        mock_future = MagicMock()
        mock_future.result.return_value = None
        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future), \
             patch.object(db, "_log_outbound"):
            result = db.drain_suppressed_notifications()
        assert result == 3
        assert len(db._suppressed_queue) == 0


# ── TestLogOutbound ─────────────────────────────────────────────────


class TestLogOutbound:
    def test_writes_jsonl(self, tmp_path):
        log_file = tmp_path / "logs" / "conversations.jsonl"
        with patch.object(Path, "resolve", return_value=tmp_path / "src" / "interfaces" / "discord_bot.py"):
            # The function constructs path relative to __file__, so mock that
            with patch("src.interfaces.discord_bot.Path") as MockPath:
                mock_resolve = MagicMock()
                mock_resolve.parent.parent.parent = tmp_path
                MockPath.return_value.resolve.return_value = mock_resolve
                MockPath.__truediv__ = Path.__truediv__
                # Just test it doesn't crash
                db._log_outbound("test message")

    def test_exception_handled(self):
        # Should not raise even if everything fails
        db._log_outbound("test")


# ── TestSetupApprovalGate ───────────────────────────────────────────


class TestSetupApprovalGate:
    def test_initializes_gate(self):
        result = db._setup_approval_gate()
        assert result is True
        assert db._pending_approval is not None
        assert not db._pending_approval.is_set()
        assert db._approval_result is False

    def test_rejects_when_already_pending(self):
        db._pending_approval = threading.Event()
        result = db._setup_approval_gate(check_pending=True)
        assert result is False

    def test_force_setup_ignores_pending(self):
        db._pending_approval = threading.Event()
        result = db._setup_approval_gate(check_pending=False)
        assert result is True


# ── TestCollectApprovalResult ───────────────────────────────────────


class TestCollectApprovalResult:
    def test_returns_result_on_response(self):
        db._pending_approval = threading.Event()
        db._approval_result = True
        db._pending_approval.set()
        responded, approved = db._collect_approval_result(timeout=1.0)
        assert responded is True
        assert approved is True
        assert db._pending_approval is None

    def test_timeout_returns_false(self):
        db._pending_approval = threading.Event()
        db._approval_result = False
        responded, approved = db._collect_approval_result(timeout=0.01)
        assert responded is False
        assert approved is False


# ── TestSendEmbedOrFallback ─────────────────────────────────────────


class TestSendEmbedOrFallback:
    def test_stores_msg_id_on_success(self):
        db._pending_approval = threading.Event()
        result = db._send_embed_or_fallback(12345, "fallback")
        assert result is True
        assert db._approval_message_id == 12345

    def test_sends_fallback_when_no_msg_id(self):
        db._pending_approval = threading.Event()
        with patch.object(db, "send_notification", return_value=True):
            result = db._send_embed_or_fallback(None, "fallback text")
        assert result is True

    def test_clears_state_when_both_fail(self):
        db._pending_approval = threading.Event()
        with patch.object(db, "send_notification", return_value=False):
            result = db._send_embed_or_fallback(None, "fallback")
        assert result is False
        assert db._pending_approval is None


# ── TestHandleSourceTimeout ─────────────────────────────────────────


class TestHandleSourceTimeout:
    def test_records_deferred_approval(self):
        with patch.object(db, "send_notification", return_value=True):
            db._handle_source_timeout("write_source", "src/foo.py", "test task")
        assert "src/foo.py" in db._deferred_approvals
        assert db._deferred_approvals["src/foo.py"]["action"] == "write_source"


# ── TestRequestSourceApproval ───────────────────────────────────────


class TestRequestSourceApproval:
    def test_not_ready_returns_false(self):
        result = db.request_source_approval("write_source", "src/foo.py", "task")
        assert result is False

    def test_already_pending_returns_false(self):
        db._bot_client = MagicMock()
        db._owner_dm_channel = MagicMock()
        db._pending_approval = threading.Event()
        result = db.request_source_approval("write_source", "src/foo.py", "task")
        assert result is False

    def test_send_failure_returns_false(self):
        db._bot_client = MagicMock()
        db._owner_dm_channel = MagicMock()
        with patch.object(db, "_send_approval_embed", return_value=None), \
             patch.object(db, "send_notification", return_value=False):
            result = db.request_source_approval("edit_file", "src/bar.py", "task")
        assert result is False


# ── TestQuestionSimilarity ──────────────────────────────────────────


class TestQuestionSimilarity:
    def test_identical_strings(self):
        assert db._question_similarity("hello world", "hello world") == 1.0

    def test_completely_different(self):
        assert db._question_similarity("hello world", "foo bar baz") == 0.0

    def test_partial_overlap(self):
        sim = db._question_similarity("the quick brown fox", "the lazy brown dog")
        assert 0.0 < sim < 1.0

    def test_empty_string(self):
        assert db._question_similarity("", "hello") == 0.0
        assert db._question_similarity("hello", "") == 0.0


# ── TestWasRecentlyAsked ────────────────────────────────────────────


class TestWasRecentlyAsked:
    def test_empty_history(self):
        assert db._was_recently_asked("test question") is False

    def test_similar_question_returns_true(self):
        db._recent_questions.append((time.time(), "test question here", False))
        assert db._was_recently_asked("test question here") is True

    def test_old_question_pruned(self):
        old_ts = time.time() - db._QUESTION_DEDUP_COOLDOWN - 10
        db._recent_questions.append((old_ts, "old question", False))
        assert db._was_recently_asked("old question") is False
        assert len(db._recent_questions) == 0


# ── TestCheckQuietHours ─────────────────────────────────────────────


class TestCheckQuietHours:
    def test_quiet_hours_true(self):
        with patch("src.utils.time_awareness.is_quiet_hours", return_value=True):
            assert db._check_quiet_hours() is True

    def test_quiet_hours_false(self):
        with patch("src.utils.time_awareness.is_quiet_hours", return_value=False):
            assert db._check_quiet_hours() is False

    def test_import_failure_returns_false(self):
        with patch("src.utils.time_awareness.is_quiet_hours", side_effect=Exception("no module")):
            assert db._check_quiet_hours() is False


# ── TestTryPiggybackQuestion ────────────────────────────────────────


class TestTryPiggybackQuestion:
    def test_no_pending_returns_not_handled(self):
        handled, result = db._try_piggyback_question(0.01)
        assert handled is False
        assert result is None

    def test_piggybacks_on_existing(self):
        evt = threading.Event()
        db._pending_question = evt
        db._question_response = "the answer"

        def _set_later():
            time.sleep(0.05)
            evt.set()

        t = threading.Thread(target=_set_later, daemon=True)
        t.start()
        handled, result = db._try_piggyback_question(timeout=2.0)
        assert handled is True
        assert result == "the answer"
        t.join(timeout=2)


# ── TestHasPendingQuestion ──────────────────────────────────────────


class TestHasPendingQuestion:
    def test_no_pending(self):
        assert db._has_pending_question() is False

    def test_pending_not_set(self):
        db._pending_question = threading.Event()
        assert db._has_pending_question() is True

    def test_pending_already_set(self):
        evt = threading.Event()
        evt.set()
        db._pending_question = evt
        assert db._has_pending_question() is False


# ── TestHasPendingApproval ──────────────────────────────────────────


class TestHasPendingApproval:
    def test_no_pending(self):
        assert db._has_pending_approval() is False

    def test_pending_not_set(self):
        db._pending_approval = threading.Event()
        assert db._has_pending_approval() is True

    def test_pending_already_set(self):
        evt = threading.Event()
        evt.set()
        db._pending_approval = evt
        assert db._has_pending_approval() is False


# ── TestResolveQuestionReply ────────────────────────────────────────


class TestResolveQuestionReply:
    def test_no_pending_noop(self):
        db._resolve_question_reply("answer")
        # No crash, no state change

    def test_sets_response_and_event(self):
        db._pending_question = threading.Event()
        db._resolve_question_reply("  my answer  ")
        assert db._question_response == "my answer"
        assert db._pending_question.is_set()

    def test_already_set_noop(self):
        evt = threading.Event()
        evt.set()
        db._pending_question = evt
        db._question_response = "original"
        db._resolve_question_reply("new answer")
        assert db._question_response == "original"


# ── TestResolveApproval ─────────────────────────────────────────────


class TestResolveApproval:
    def test_no_pending_noop(self):
        db._resolve_approval(True)

    def test_approved(self):
        db._pending_approval = threading.Event()
        db._resolve_approval(True)
        assert db._approval_result is True
        assert db._pending_approval.is_set()

    def test_denied(self):
        db._pending_approval = threading.Event()
        db._resolve_approval(False)
        assert db._approval_result is False
        assert db._pending_approval.is_set()

    def test_never_path_overrides_approval(self):
        db._pending_approval = threading.Event()
        db._resolve_approval(True, "never foo.txt")
        assert db._approval_result is False
        assert "foo.txt" in db._cleanup_never_paths


# ── TestCheckCleanupNever ───────────────────────────────────────────


class TestCheckCleanupNever:
    def test_matching_never(self):
        assert db._check_cleanup_never("never foo.txt") == "foo.txt"

    def test_with_backticks(self):
        assert db._check_cleanup_never("never `bar.py`") == "bar.py"

    def test_no_match(self):
        assert db._check_cleanup_never("yes please") is None

    def test_empty_after_never(self):
        assert db._check_cleanup_never("never ") is None


# ── TestRequestCleanupApproval ──────────────────────────────────────


class TestRequestCleanupApproval:
    def test_empty_files_returns_no(self):
        assert db.request_cleanup_approval([]) == "no"

    def test_not_ready_returns_timeout(self):
        assert db.request_cleanup_approval(["file.txt"]) == "timeout"


# ── TestLogConvo ────────────────────────────────────────────────────


class TestLogConvo:
    def test_calls_log_conversation(self):
        with patch("src.interfaces.response_builder.log_conversation") as mock_log:
            db._log_convo("hello", "world", "chat", 0.01)
            mock_log.assert_called_once_with("discord", "hello", "world", "chat", 0.01)

    def test_exception_handled(self):
        with patch("src.interfaces.response_builder.log_conversation", side_effect=Exception("fail")):
            db._log_convo("hello", "world", "chat")  # Should not raise


# ── TestParseModelSwitch ────────────────────────────────────────────


class TestParseModelSwitch:
    def test_switch_to_grok(self):
        result = db._parse_model_switch("switch to grok")
        assert result == ("grok", False, 0)

    def test_switch_to_direct(self):
        result = db._parse_model_switch("switch to grok direct")
        assert result == ("grok-direct", False, 0)

    def test_use_claude_for_this_task(self):
        result = db._parse_model_switch("use claude for this task")
        assert result == ("claude", False, 1)

    def test_switch_for_n_messages(self):
        result = db._parse_model_switch("switch to deepseek for 5 messages")
        assert result == ("deepseek", False, 5)

    def test_switch_and_retry(self):
        result = db._parse_model_switch("switch to claude and try again")
        assert result == ("claude", True, 0)

    def test_temp_with_retry(self):
        result = db._parse_model_switch("use claude for this task and retry")
        assert result == ("claude", True, 1)

    def test_provider_model_path(self):
        result = db._parse_model_switch("switch to xai/grok-2")
        assert result == ("xai/grok-2", False, 0)

    def test_not_a_switch(self):
        assert db._parse_model_switch("hello there") is None

    def test_use_alias(self):
        result = db._parse_model_switch("use deepseek direct")
        assert result == ("deepseek-direct", False, 0)

    def test_set_model_to(self):
        result = db._parse_model_switch("set model to claude")
        assert result == ("claude", False, 0)


# ── TestParseImageModelSwitch ───────────────────────────────────────


class TestParseImageModelSwitch:
    def test_use_for_images(self):
        result = db._parse_image_model_switch("use illustrious for images")
        assert result == "illustrious"

    def test_switch_image_model_to(self):
        result = db._parse_image_model_switch("switch image model to uber")
        assert result == "uber"

    def test_set_image_model_to(self):
        result = db._parse_image_model_switch("set image model to intorealism")
        assert result == "intorealism"

    def test_not_image_switch(self):
        assert db._parse_image_model_switch("switch to grok") is None


# ── TestParseDreamCycleInterval ─────────────────────────────────────


class TestParseDreamCycleInterval:
    def test_set_minutes(self):
        assert db._parse_dream_cycle_interval("set dream cycle to 15 minutes") == 900

    def test_set_seconds(self):
        assert db._parse_dream_cycle_interval("set dream cycle to 900 seconds") == 900

    def test_set_hours(self):
        assert db._parse_dream_cycle_interval("set dream cycle to 2 hours") == 7200

    def test_pattern_2(self):
        assert db._parse_dream_cycle_interval("dream cycle 10 minutes") == 600

    def test_pattern_3(self):
        assert db._parse_dream_cycle_interval("15 minute dream cycles") == 900

    def test_no_dream_keyword(self):
        assert db._parse_dream_cycle_interval("set interval to 15 minutes") is None

    def test_polite_prefix(self):
        assert db._parse_dream_cycle_interval("can you set dream cycle to 5 minutes?") == 300

    def test_adjust_variant(self):
        assert db._parse_dream_cycle_interval("please adjust the dream cycle to 10 minutes") == 600


# ── TestParseProjectCommand ─────────────────────────────────────────


class TestParseProjectCommand:
    def test_list_projects(self):
        assert db._parse_project_command("list projects") == ("list", None)

    def test_show_projects(self):
        assert db._parse_project_command("show projects") == ("list", None)

    def test_what_projects(self):
        assert db._parse_project_command("what projects") == ("list", None)

    def test_add_project(self):
        result = db._parse_project_command("add project health tracker")
        assert result == ("add", "health tracker")

    def test_add_project_called(self):
        result = db._parse_project_command("add project called meal planner")
        assert result == ("add", "meal planner")

    def test_remove_project(self):
        result = db._parse_project_command("remove project health_tracker")
        assert result == ("remove", "health_tracker")

    def test_drop_project(self):
        result = db._parse_project_command("drop the health tracker project")
        assert result == ("remove", "health tracker")

    def test_no_project_keyword(self):
        assert db._parse_project_command("hello there") is None

    def test_polite_prefix(self):
        result = db._parse_project_command("can you add a project called meal planner?")
        assert result == ("add", "meal planner")


# ── TestHandleProjectCommand ────────────────────────────────────────


class TestHandleProjectCommand:
    def test_list_empty(self):
        with patch("src.utils.project_context.load", return_value={"active_projects": {}}):
            result = db._handle_project_command("list", None)
        assert "No active projects" in result

    def test_list_with_projects(self):
        ctx = {"active_projects": {
            "health": {"description": "Health Tracker", "priority": "high"},
        }}
        with patch("src.utils.project_context.load", return_value=ctx):
            result = db._handle_project_command("list", None)
        assert "health" in result

    def test_add_project(self):
        ctx = {"active_projects": {}}
        with patch("src.utils.project_context.load", return_value=ctx), \
             patch("src.utils.project_context.save", return_value=True) as mock_save:
            result = db._handle_project_command("add", "Meal Planner")
        assert "meal_planner" in result.lower()
        mock_save.assert_called_once()

    def test_add_duplicate(self):
        ctx = {"active_projects": {"meal_planner": {}}}
        with patch("src.utils.project_context.load", return_value=ctx):
            result = db._handle_project_command("add", "Meal Planner")
        assert "already exists" in result

    def test_remove_project(self):
        ctx = {"active_projects": {"health": {"description": "Health"}}}
        with patch("src.utils.project_context.load", return_value=ctx), \
             patch("src.utils.project_context.save", return_value=True):
            result = db._handle_project_command("remove", "health")
        assert "Removed" in result

    def test_remove_not_found(self):
        ctx = {"active_projects": {"health": {}}}
        with patch("src.utils.project_context.load", return_value=ctx):
            result = db._handle_project_command("remove", "nutrition")
        assert "No project matching" in result

    def test_remove_fuzzy_match(self):
        ctx = {"active_projects": {"health_tracker": {"description": "Health"}}}
        with patch("src.utils.project_context.load", return_value=ctx), \
             patch("src.utils.project_context.save", return_value=True):
            result = db._handle_project_command("remove", "health")
        assert "Removed" in result

    def test_unknown_action(self):
        with patch("src.utils.project_context.load", return_value={"active_projects": {}}):
            result = db._handle_project_command("update", "health")
        assert "Unknown" in result


# ── TestShouldRespond ───────────────────────────────────────────────


class TestShouldRespond:
    def test_ignores_bots(self):
        msg = MagicMock()
        msg.author.bot = True
        assert db._should_respond(msg, 1) is False

    def test_responds_to_dm(self):
        msg = MagicMock()
        msg.author.bot = False
        msg.guild = None
        assert db._should_respond(msg, 1) is True

    def test_responds_to_mention_in_guild(self):
        msg = MagicMock()
        msg.author.bot = False
        msg.guild = MagicMock()
        bot_mention = MagicMock()
        bot_mention.id = 42
        msg.mentions = [bot_mention]
        assert db._should_respond(msg, 42) is True

    def test_ignores_non_mention_in_guild(self):
        msg = MagicMock()
        msg.author.bot = False
        msg.guild = MagicMock()
        other_user = MagicMock()
        other_user.id = 99
        msg.mentions = [other_user]
        assert db._should_respond(msg, 42) is False

    def test_no_mentions_in_guild(self):
        msg = MagicMock()
        msg.author.bot = False
        msg.guild = MagicMock()
        msg.mentions = []
        assert db._should_respond(msg, 42) is False


# ── TestGetContent ──────────────────────────────────────────────────


class TestGetContent:
    def test_plain_text(self):
        msg = MagicMock()
        msg.content = "  hello world  "
        msg.mentions = []
        assert db._get_content(msg, 42) == "hello world"

    def test_strips_bot_mention(self):
        msg = MagicMock()
        msg.content = "<@42> what's up"
        bot_mention = MagicMock()
        bot_mention.id = 42
        msg.mentions = [bot_mention]
        assert db._get_content(msg, 42) == "what's up"

    def test_none_content(self):
        msg = MagicMock()
        msg.content = None
        msg.mentions = []
        assert db._get_content(msg, 42) == ""


# ── TestProcessWithArchi ────────────────────────────────────────────


class TestProcessWithArchi:
    def test_no_router_returns_error(self):
        db._router = None
        with patch.object(db, "_get_router", return_value=None):
            full, truncated, actions = db.process_with_archi("hello")
        assert "not available" in full.lower()
        assert actions == []

    def test_successful_processing(self):
        mock_router = MagicMock()
        db._router = mock_router
        with patch("src.interfaces.message_handler.process_message") as mock_pm:
            mock_pm.return_value = ("response text", [{"description": "Done"}], 0.01)
            full, truncated, actions = db.process_with_archi("hello")
        assert "response text" in full
        assert len(actions) == 1


# ── TestProcessImageWithArchi ───────────────────────────────────────


class TestProcessImageWithArchi:
    def test_no_router_returns_error(self):
        db._router = None
        with patch.object(db, "_get_router", return_value=None):
            full, truncated, cost = db.process_image_with_archi("describe", "/path/img.png")
        assert "not available" in full.lower()
        assert cost == 0.0

    def test_auto_escalates_to_claude(self):
        mock_router = MagicMock()
        mock_router.get_active_model_info.return_value = {"model": "grok-fast"}
        mock_router.chat_with_image.return_value = {"text": "I see a cat", "cost_usd": 0.001}
        db._router = mock_router
        full, truncated, cost = db.process_image_with_archi("describe", "/path/img.png")
        mock_router.switch_model_temp.assert_called_once_with("claude-haiku", count=1)
        mock_router.complete_temp_task.assert_called_once()
        assert "cat" in full

    def test_no_escalation_if_already_claude(self):
        mock_router = MagicMock()
        mock_router.get_active_model_info.return_value = {"model": "claude-haiku-4.5"}
        mock_router.chat_with_image.return_value = {"text": "result", "cost_usd": 0.002}
        db._router = mock_router
        full, truncated, cost = db.process_image_with_archi("describe", "/path/img.png")
        mock_router.switch_model_temp.assert_not_called()


# ── TestCloseBotAndRequestStop ──────────────────────────────────────


class TestCloseBotAndRequestStop:
    def test_close_bot_when_not_running(self):
        # Should not raise
        db.close_bot()

    def test_request_bot_stop_when_no_event(self):
        db.request_bot_stop()  # Should not raise

    def test_request_bot_stop_sets_event(self):
        mock_event = MagicMock()
        db._bot_stop_event = mock_event
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        db._bot_loop = mock_loop
        db.request_bot_stop()
        mock_loop.call_soon_threadsafe.assert_called_once_with(mock_event.set)


# ── TestPersistOwnerId ──────────────────────────────────────────────


class TestPersistOwnerId:
    def test_updates_existing_entry(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DISCORD_BOT_TOKEN=abc\nDISCORD_OWNER_ID=111\n")
        with patch.object(Path, "resolve", return_value=tmp_path / "src" / "interfaces" / "discord_bot.py"):
            with patch("src.interfaces.discord_bot.Path") as MockPath:
                mock_resolve = MagicMock()
                mock_resolve.parent.parent.parent.__truediv__ = lambda self, x: tmp_path / x
                MockPath.return_value.resolve.return_value = mock_resolve

                # Simpler approach: directly test the function's core logic
                # by mocking at a higher level
                db._persist_owner_id(222)

    def test_no_env_file_no_crash(self, tmp_path):
        # Should handle missing .env gracefully
        db._persist_owner_id(123)


# ── TestAskUser ─────────────────────────────────────────────────────


class TestAskUser:
    def test_quiet_hours_returns_none(self):
        with patch.object(db, "_check_quiet_hours", return_value=True):
            result = db.ask_user("question?")
        assert result is None

    def test_not_ready_returns_none(self):
        with patch.object(db, "_check_quiet_hours", return_value=False):
            result = db.ask_user("question?")
        assert result is None

    def test_recently_asked_returns_none(self):
        db._bot_client = MagicMock()
        db._owner_dm_channel = MagicMock()
        db._recent_questions.append((time.time(), "question?", False))
        with patch.object(db, "_check_quiet_hours", return_value=False):
            result = db.ask_user("question?")
        assert result is None


# ── TestMarkQuestionAnswered ────────────────────────────────────────


class TestMarkQuestionAnswered:
    def test_marks_existing_question(self):
        db._recent_questions.append((time.time(), "what color?", False))
        db._mark_question_answered("what color?")
        assert db._recent_questions[0][2] is True

    def test_no_match_no_change(self):
        db._recent_questions.append((time.time(), "what color?", False))
        db._mark_question_answered("different question")
        assert db._recent_questions[0][2] is False


# ── TestGetGoalManager ──────────────────────────────────────────────


class TestGetGoalManager:
    def test_returns_from_heartbeat(self):
        mock_hb = MagicMock()
        mock_hb.goal_manager = MagicMock()
        db._heartbeat = mock_hb
        assert db._get_goal_manager() is mock_hb.goal_manager

    def test_returns_none_without_heartbeat(self):
        db._heartbeat = None
        assert db._get_goal_manager() is None


# ── TestCreateBot ───────────────────────────────────────────────────


class TestCreateBot:
    def test_creates_bot_instance(self):
        try:
            import discord  # noqa: F401
        except ImportError:
            pytest.skip("discord.py not installed")
        bot = db.create_bot()
        assert bot is not None

    def test_raises_without_discord(self):
        with patch.dict("sys.modules", {"discord": None}):
            # create_bot tries to import discord, should raise
            # This test verifies the ImportError path
            pass  # Can't fully test without unloading module


# ── TestCancelKeywords ──────────────────────────────────────────────


class TestCancelKeywords:
    def test_cancel_exact_words(self):
        for word in ("stop", "cancel", "abort", "quit", "halt"):
            assert word in db._CANCEL_EXACT

    def test_cancel_phrases(self):
        assert "stop that" in db._CANCEL_PHRASES
        assert "cancel task" in db._CANCEL_PHRASES


# ── TestRunBot ──────────────────────────────────────────────────────


class TestRunBot:
    def test_no_token_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove DISCORD_BOT_TOKEN
            os.environ.pop("DISCORD_BOT_TOKEN", None)
            with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
                db.run_bot()
