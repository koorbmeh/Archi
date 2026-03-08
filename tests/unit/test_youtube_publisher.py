"""Tests for YouTube publisher and video_script format (session 229)."""

import os
import tempfile
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.tools.content_creator import (
    _parse_video_script,
    _get_youtube_config,
    _get_youtube_service,
    publish_to_youtube,
    update_youtube_metadata,
    youtube_authenticate,
    generate_content,
    _FORMAT_PROMPTS,
    _resumable_upload,
)


# ── Video Script Parser ────────────────────────────────────────────────


class TestParseVideoScript:
    def test_full_parse(self):
        text = (
            "TITLE: 5 Python Tips You Need to Know\n"
            "DESCRIPTION:\n"
            "In this video, we cover 5 essential Python tips.\n"
            "00:00 Intro\n"
            "01:00 Tip 1\n"
            "TAGS: python, programming, tips, coding, tutorial\n"
            "SCRIPT:\n"
            "[INTRO]\n"
            "Hey everyone, welcome back to the channel.\n"
            "[MAIN]\n"
            "Let's dive into tip number one."
        )
        result = _parse_video_script(text)
        assert result["title"] == "5 Python Tips You Need to Know"
        assert "5 essential Python tips" in result["description"]
        assert "00:00 Intro" in result["description"]
        assert result["tags"] == ["python", "programming", "tips", "coding", "tutorial"]
        assert "[INTRO]" in result["script"]
        assert "tip number one" in result["script"]

    def test_title_only(self):
        result = _parse_video_script("TITLE: My Video\nSome other text")
        assert result["title"] == "My Video"

    def test_empty_text(self):
        result = _parse_video_script("")
        assert result == {}

    def test_no_markers(self):
        result = _parse_video_script("Just plain text with no markers")
        assert result == {}

    def test_tags_inline(self):
        text = "TAGS: ai, machine learning, deep learning"
        result = _parse_video_script(text)
        assert result["tags"] == ["ai", "machine learning", "deep learning"]

    def test_multiline_description(self):
        text = (
            "TITLE: Test\n"
            "DESCRIPTION:\n"
            "Line one\n"
            "Line two\n"
            "Line three\n"
            "TAGS: a, b\n"
        )
        result = _parse_video_script(text)
        assert "Line one" in result["description"]
        assert "Line three" in result["description"]


# ── Video Script Generation ────────────────────────────────────────────


class TestGenerateVideoScript:
    def test_generate_video_script_success(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": (
                "TITLE: AI Agents Explained\n"
                "DESCRIPTION:\nLearn about AI agents.\n"
                "TAGS: ai, agents, tutorial\n"
                "SCRIPT:\n[INTRO]\nHello world!"
            )
        }
        result = generate_content(router, "AI agents", "video_script")
        assert result is not None
        assert result["format"] == "video_script"
        assert result["title"] == "AI Agents Explained"
        assert result["tags"] == ["ai", "agents", "tutorial"]
        assert "[INTRO]" in result["script"]
        assert result["description"] == "Learn about AI agents."

    def test_video_script_fallback_title(self):
        router = MagicMock()
        router.generate.return_value = {"text": "Just a plain script without markers"}
        result = generate_content(router, "cool topic", "video_script")
        assert result is not None
        assert result["title"] == "Cool Topic"  # fallback to topic.title()

    def test_video_script_format_prompt_exists(self):
        assert "video_script" in _FORMAT_PROMPTS
        template = _FORMAT_PROMPTS["video_script"]
        assert "{topic}" in template
        assert "{extra_context}" in template
        assert "YouTube" in template


# ── YouTube Config ─────────────────────────────────────────────────────


class TestYouTubeConfig:
    def test_config_all_set(self):
        with patch.dict(os.environ, {
            "YOUTUBE_CLIENT_ID": "cid",
            "YOUTUBE_CLIENT_SECRET": "csec",
            "YOUTUBE_REFRESH_TOKEN": "rtok",
        }, clear=False):
            config = _get_youtube_config()
            assert config["client_id"] == "cid"
            assert config["client_secret"] == "csec"
            assert config["refresh_token"] == "rtok"

    def test_config_empty(self):
        with patch.dict(os.environ, {
            "YOUTUBE_CLIENT_ID": "",
            "YOUTUBE_CLIENT_SECRET": "",
            "YOUTUBE_REFRESH_TOKEN": "",
        }, clear=False):
            config = _get_youtube_config()
            assert config["client_id"] is None
            assert config["client_secret"] is None
            assert config["refresh_token"] is None


# ── YouTube Service ────────────────────────────────────────────────────


class TestYouTubeService:
    def test_missing_credentials(self):
        with patch.dict(os.environ, {
            "YOUTUBE_CLIENT_ID": "",
            "YOUTUBE_CLIENT_SECRET": "",
            "YOUTUBE_REFRESH_TOKEN": "",
        }, clear=False):
            svc, err = _get_youtube_service()
            assert svc is None
            assert "not configured" in err

    def test_import_error(self):
        with patch.dict(os.environ, {
            "YOUTUBE_CLIENT_ID": "cid",
            "YOUTUBE_CLIENT_SECRET": "csec",
            "YOUTUBE_REFRESH_TOKEN": "rtok",
        }, clear=False):
            with patch.dict("sys.modules", {"google.oauth2.credentials": None, "google": None}):
                svc, err = _get_youtube_service()
                assert svc is None
                assert "not installed" in err


# ── Publish to YouTube ─────────────────────────────────────────────────


class TestPublishToYouTube:
    def test_file_not_found(self):
        result = publish_to_youtube("/nonexistent/video.mp4", "Title")
        assert not result["success"]
        assert "not found" in result["error"]

    def test_empty_file(self, tmp_path):
        empty_file = tmp_path / "empty.mp4"
        empty_file.write_bytes(b"")
        result = publish_to_youtube(str(empty_file), "Title")
        assert not result["success"]
        assert "empty" in result["error"]

    def test_title_too_long(self, tmp_path):
        vid = tmp_path / "test.mp4"
        vid.write_bytes(b"\x00" * 1024)
        result = publish_to_youtube(str(vid), "x" * 101)
        assert not result["success"]
        assert "too long" in result["error"].lower()

    def test_invalid_privacy(self, tmp_path):
        vid = tmp_path / "test.mp4"
        vid.write_bytes(b"\x00" * 1024)
        result = publish_to_youtube(str(vid), "Title", privacy_status="invalid")
        assert not result["success"]
        assert "Invalid privacy" in result["error"]

    def test_no_credentials(self, tmp_path):
        vid = tmp_path / "test.mp4"
        vid.write_bytes(b"\x00" * 1024)
        with patch.dict(os.environ, {
            "YOUTUBE_CLIENT_ID": "",
            "YOUTUBE_CLIENT_SECRET": "",
            "YOUTUBE_REFRESH_TOKEN": "",
        }, clear=False):
            result = publish_to_youtube(str(vid), "Title")
            assert not result["success"]
            assert "not configured" in result["error"]

    @patch("src.tools.content_creator._resumable_upload")
    @patch("src.tools.content_creator._get_youtube_service")
    def test_upload_success(self, mock_svc, mock_upload, tmp_path):
        vid = tmp_path / "test.mp4"
        vid.write_bytes(b"\x00" * 1024)

        mock_service = MagicMock()
        mock_svc.return_value = (mock_service, None)
        mock_upload.return_value = {"id": "abc123xyz"}

        with patch("src.tools.content_creator.MediaFileUpload", create=True):
            # Need to mock the import inside the function
            with patch.dict("sys.modules", {
                "googleapiclient.http": MagicMock(),
                "googleapiclient": MagicMock(),
            }):
                result = publish_to_youtube(
                    str(vid), "Test Video", "A description",
                    tags=["test", "video"], privacy_status="private",
                )
                assert result["success"]
                assert result["video_id"] == "abc123xyz"
                assert "youtu.be" in result["url"]

    @patch("src.tools.content_creator._get_youtube_service")
    def test_upload_api_error(self, mock_svc, tmp_path):
        vid = tmp_path / "test.mp4"
        vid.write_bytes(b"\x00" * 1024)

        mock_service = MagicMock()
        mock_svc.return_value = (mock_service, None)
        mock_service.videos.return_value.insert.side_effect = Exception("API error")

        with patch.dict("sys.modules", {
            "googleapiclient.http": MagicMock(),
            "googleapiclient": MagicMock(),
        }):
            result = publish_to_youtube(str(vid), "Title")
            assert not result["success"]
            assert "API error" in result["error"]


# ── Update YouTube Metadata ────────────────────────────────────────────


class TestUpdateYouTubeMetadata:
    def test_no_credentials(self):
        with patch.dict(os.environ, {
            "YOUTUBE_CLIENT_ID": "",
            "YOUTUBE_CLIENT_SECRET": "",
            "YOUTUBE_REFRESH_TOKEN": "",
        }, clear=False):
            result = update_youtube_metadata("vid123", title="New Title")
            assert not result["success"]
            assert "not configured" in result["error"]

    @patch("src.tools.content_creator._get_youtube_service")
    def test_video_not_found(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = (mock_service, None)
        mock_service.videos.return_value.list.return_value.execute.return_value = {"items": []}

        result = update_youtube_metadata("vid123", title="New Title")
        assert not result["success"]
        assert "not found" in result["error"].lower()

    @patch("src.tools.content_creator._get_youtube_service")
    def test_update_success(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = (mock_service, None)
        mock_service.videos.return_value.list.return_value.execute.return_value = {
            "items": [{
                "snippet": {"title": "Old Title", "description": "Old desc", "categoryId": "22"},
                "status": {"privacyStatus": "private"},
            }]
        }
        mock_service.videos.return_value.update.return_value.execute.return_value = {}

        result = update_youtube_metadata("vid123", title="New Title", tags=["new"])
        assert result["success"]
        assert result["video_id"] == "vid123"


# ── YouTube Auth Helpers ───────────────────────────────────────────────


class TestYouTubeAuth:
    def test_authenticate_no_credentials(self):
        with patch.dict(os.environ, {
            "YOUTUBE_CLIENT_ID": "",
            "YOUTUBE_CLIENT_SECRET": "",
        }, clear=False):
            result = youtube_authenticate()
            assert not result["success"]
            assert "must be set" in result["error"]


# ── Resumable Upload ──────────────────────────────────────────────────


class TestResumableUpload:
    def test_success_first_try(self):
        mock_req = MagicMock()
        mock_req.next_chunk.return_value = (None, {"id": "video123"})
        result = _resumable_upload(mock_req)
        assert result == {"id": "video123"}

    def test_progress_reporting(self):
        mock_req = MagicMock()
        status_mock = MagicMock()
        status_mock.progress.return_value = 0.5
        mock_req.next_chunk.side_effect = [
            (status_mock, None),
            (None, {"id": "video123"}),
        ]
        result = _resumable_upload(mock_req)
        assert result == {"id": "video123"}

    def test_non_retryable_error_raises(self):
        mock_req = MagicMock()
        mock_req.next_chunk.side_effect = Exception("400 Bad Request")
        with pytest.raises(Exception, match="400"):
            _resumable_upload(mock_req, max_retries=2)
