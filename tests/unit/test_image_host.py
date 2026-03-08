"""Tests for src/tools/image_host.py — GitHub-based image hosting."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Config / availability ─────────────────────────────────────────────

def test_is_configured_missing_pat():
    with patch.dict(os.environ, {"GITHUB_PAT": "", "GITHUB_BLOG_REPO": "user/repo"}, clear=False):
        from src.tools.image_host import is_configured
        assert not is_configured()


def test_is_configured_missing_repo():
    with patch.dict(os.environ, {"GITHUB_PAT": "ghp_test", "GITHUB_BLOG_REPO": ""}, clear=False):
        from src.tools.image_host import is_configured
        assert not is_configured()


def test_is_configured_both_present():
    with patch.dict(os.environ, {"GITHUB_PAT": "ghp_test", "GITHUB_BLOG_REPO": "user/repo"}, clear=False):
        from src.tools.image_host import is_configured
        assert is_configured()


# ── URL generation ─────────────────────────────────────────────────────

def test_get_public_url():
    from src.tools.image_host import get_public_url
    url = get_public_url("user/repo", "images/content/test.png")
    assert url == "https://raw.githubusercontent.com/user/repo/main/images/content/test.png"


def test_get_public_url_custom_branch():
    from src.tools.image_host import get_public_url
    url = get_public_url("user/repo", "img/test.png", branch="gh-pages")
    assert url == "https://raw.githubusercontent.com/user/repo/gh-pages/img/test.png"


# ── upload_image ───────────────────────────────────────────────────────

def test_upload_image_no_pat():
    with patch.dict(os.environ, {"GITHUB_PAT": "", "GITHUB_BLOG_REPO": "u/r"}, clear=False):
        from src.tools.image_host import upload_image
        result = upload_image("/fake/path.png")
        assert not result["success"]
        assert "GITHUB_PAT" in result["error"]


def test_upload_image_no_repo():
    with patch.dict(os.environ, {"GITHUB_PAT": "ghp_x", "GITHUB_BLOG_REPO": ""}, clear=False):
        from src.tools.image_host import upload_image
        result = upload_image("/fake/path.png")
        assert not result["success"]
        assert "GITHUB_BLOG_REPO" in result["error"]


def test_upload_image_file_not_found():
    with patch.dict(os.environ, {"GITHUB_PAT": "ghp_x", "GITHUB_BLOG_REPO": "u/r"}, clear=False):
        from src.tools.image_host import upload_image
        result = upload_image("/nonexistent/image.png")
        assert not result["success"]
        assert "not found" in result["error"]


def test_upload_image_success():
    """Mock GitHub API and verify upload_image returns correct URL."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"fake_png_data")
        tmp_path = f.name

    try:
        env = {"GITHUB_PAT": "ghp_test", "GITHUB_BLOG_REPO": "user/repo"}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "content": {"sha": "abc123"}
        }).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, env, clear=False), \
             patch("src.tools.image_host.urlopen", return_value=mock_response):
            from src.tools.image_host import upload_image
            result = upload_image(tmp_path, filename="test_img.png")

        assert result["success"]
        assert "raw.githubusercontent.com" in result["url"]
        assert "user/repo" in result["url"]
        assert "test_img.png" in result["url"]
        assert result["sha"] == "abc123"
        assert result["size_bytes"] == len(b"fake_png_data")
    finally:
        os.unlink(tmp_path)


def test_upload_image_422_already_exists():
    """HTTP 422 (already exists) should return success with the URL."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"data")
        tmp_path = f.name

    try:
        from urllib.error import HTTPError
        env = {"GITHUB_PAT": "ghp_test", "GITHUB_BLOG_REPO": "user/repo"}

        mock_err = HTTPError(
            url="https://api.github.com/...",
            code=422,
            msg="Unprocessable",
            hdrs=None,
            fp=MagicMock(read=MagicMock(return_value=b"already exists")),
        )

        with patch.dict(os.environ, env, clear=False), \
             patch("src.tools.image_host.urlopen", side_effect=mock_err):
            from src.tools.image_host import upload_image
            result = upload_image(tmp_path, filename="dup.png")

        assert result["success"]
        assert "raw.githubusercontent.com" in result["url"]
        assert "already exists" in result.get("note", "")
    finally:
        os.unlink(tmp_path)


# ── upload_for_platform ──────────────────────────────────────────────

def test_upload_for_platform_maps_subfolder():
    """Verify platform → subfolder mapping and filename generation."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"img")
        tmp_path = f.name

    try:
        env = {"GITHUB_PAT": "ghp_test", "GITHUB_BLOG_REPO": "user/repo"}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"content": {"sha": "x"}}).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, env, clear=False), \
             patch("src.tools.image_host.urlopen", return_value=mock_response):
            from src.tools.image_host import upload_for_platform
            result = upload_for_platform(tmp_path, platform="instagram_post", topic="AI trends")

        assert result["success"]
        assert "/instagram/" in result["repo_path"]
        assert "ai_trends" in result["repo_path"]
    finally:
        os.unlink(tmp_path)


def test_upload_for_platform_unknown_platform():
    """Unknown platform uses the platform name as subfolder."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"img")
        tmp_path = f.name

    try:
        env = {"GITHUB_PAT": "ghp_test", "GITHUB_BLOG_REPO": "user/repo"}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"content": {"sha": "x"}}).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, env, clear=False), \
             patch("src.tools.image_host.urlopen", return_value=mock_response):
            from src.tools.image_host import upload_for_platform
            result = upload_for_platform(tmp_path, platform="tiktok", topic="test")

        assert result["success"]
        assert "/tiktok/" in result["repo_path"]
    finally:
        os.unlink(tmp_path)


# ── Heartbeat integration ────────────────────────────────────────────

def test_publish_with_image_not_configured():
    """_publish_with_image returns None when hosting isn't configured."""
    from unittest.mock import MagicMock as MM
    # Create a minimal mock heartbeat with the method
    from types import SimpleNamespace
    slot = SimpleNamespace(
        slot_id="s1", content_format="instagram_post",
        image_path="/fake.png", generated_content="caption", topic="test",
    )

    with patch.dict(os.environ, {"GITHUB_PAT": "", "GITHUB_BLOG_REPO": ""}, clear=False):
        from src.core.heartbeat import Heartbeat
        hb = Heartbeat.__new__(Heartbeat)
        result = hb._publish_with_image(slot, MM(), MM())
        assert result is None


def test_publish_with_image_no_image_path():
    """_publish_with_image returns None when slot has no image."""
    from types import SimpleNamespace
    slot = SimpleNamespace(
        slot_id="s2", content_format="instagram_post",
        image_path="", generated_content="caption", topic="test",
    )

    with patch.dict(os.environ, {"GITHUB_PAT": "ghp_x", "GITHUB_BLOG_REPO": "u/r"}, clear=False):
        from src.core.heartbeat import Heartbeat
        hb = Heartbeat.__new__(Heartbeat)
        result = hb._publish_with_image(slot, MagicMock(), MagicMock())
        assert result is None


def test_publish_with_image_instagram_flow():
    """Full flow: upload → publish_to_instagram with public URL."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"img_data")
        tmp_path = f.name

    try:
        from types import SimpleNamespace
        slot = SimpleNamespace(
            slot_id="s3", content_format="instagram_post",
            image_path=tmp_path, generated_content="My caption", topic="AI",
        )

        mock_ig = MagicMock(return_value={"success": True, "media_id": "123"})
        mock_fb = MagicMock()

        upload_result = {
            "success": True,
            "url": "https://raw.githubusercontent.com/u/r/main/images/content/instagram/test.jpg",
        }

        env = {"GITHUB_PAT": "ghp_x", "GITHUB_BLOG_REPO": "u/r"}
        with patch.dict(os.environ, env, clear=False), \
             patch("src.tools.image_host.upload_for_platform", return_value=upload_result):
            from src.core.heartbeat import Heartbeat
            hb = Heartbeat.__new__(Heartbeat)
            result = hb._publish_with_image(slot, mock_ig, mock_fb)

        assert result == {"success": True, "media_id": "123"}
        mock_ig.assert_called_once_with(upload_result["url"], caption="My caption")
        mock_fb.assert_not_called()
    finally:
        os.unlink(tmp_path)


def test_publish_with_image_facebook_flow():
    """Upload → publish_to_facebook_photo with public URL."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"img_data")
        tmp_path = f.name

    try:
        from types import SimpleNamespace
        slot = SimpleNamespace(
            slot_id="s4", content_format="facebook_post",
            image_path=tmp_path, generated_content="FB caption", topic="Tech",
        )

        mock_ig = MagicMock()
        mock_fb = MagicMock(return_value={"success": True, "post_id": "456"})

        upload_result = {
            "success": True,
            "url": "https://raw.githubusercontent.com/u/r/main/images/content/facebook/test.jpg",
        }

        env = {"GITHUB_PAT": "ghp_x", "GITHUB_BLOG_REPO": "u/r"}
        with patch.dict(os.environ, env, clear=False), \
             patch("src.tools.image_host.upload_for_platform", return_value=upload_result):
            from src.core.heartbeat import Heartbeat
            hb = Heartbeat.__new__(Heartbeat)
            result = hb._publish_with_image(slot, mock_ig, mock_fb)

        assert result == {"success": True, "post_id": "456"}
        mock_fb.assert_called_once_with(upload_result["url"], caption="FB caption")
        mock_ig.assert_not_called()
    finally:
        os.unlink(tmp_path)


def test_publish_with_image_upload_fails():
    """When image upload fails, returns None (falls through to text-only publish)."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"img_data")
        tmp_path = f.name

    try:
        from types import SimpleNamespace
        slot = SimpleNamespace(
            slot_id="s5", content_format="instagram_post",
            image_path=tmp_path, generated_content="caption", topic="test",
        )

        upload_result = {"success": False, "error": "API error"}

        env = {"GITHUB_PAT": "ghp_x", "GITHUB_BLOG_REPO": "u/r"}
        with patch.dict(os.environ, env, clear=False), \
             patch("src.tools.image_host.upload_for_platform", return_value=upload_result):
            from src.core.heartbeat import Heartbeat
            hb = Heartbeat.__new__(Heartbeat)
            result = hb._publish_with_image(slot, MagicMock(), MagicMock())

        assert result is None
    finally:
        os.unlink(tmp_path)
