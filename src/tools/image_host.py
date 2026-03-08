"""Image hosting — uploads local images to GitHub for public URLs.

Bridges the gap between locally generated SDXL images and social media
publishers (Instagram, Facebook) that require publicly accessible URLs.

Uses the same GitHub PAT and blog repo already configured for the content
pipeline. Images are committed to an `images/` directory in the repo and
served via raw.githubusercontent.com URLs.

Public API:
    upload_image(local_path) -> dict  (success, url, error)
    get_public_url(repo_path) -> str
    is_configured() -> bool
"""

import logging
import os
import time
from base64 import b64encode
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 20
_IMAGES_DIR = "images/content"


def _get_config() -> Dict[str, Optional[str]]:
    """Return GitHub config from env vars."""
    return {
        "pat": os.environ.get("GITHUB_PAT", "").strip() or None,
        "repo": os.environ.get("GITHUB_BLOG_REPO", "").strip() or None,
    }


def is_configured() -> bool:
    """Check if image hosting credentials are available."""
    cfg = _get_config()
    return bool(cfg["pat"] and cfg["repo"])


def get_public_url(repo: str, repo_path: str, branch: str = "main") -> str:
    """Build a raw.githubusercontent.com URL for a file in a GitHub repo."""
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{repo_path}"


def upload_image(
    local_path: str,
    filename: Optional[str] = None,
    subfolder: str = "",
) -> Dict[str, Any]:
    """Upload a local image to GitHub and return its public URL.

    Args:
        local_path: Path to the local image file.
        filename: Override filename (default: use original name with timestamp).
        subfolder: Optional subfolder within images/content/.

    Returns:
        Dict with: success, url, repo_path, error.
    """
    cfg = _get_config()
    if not cfg["pat"]:
        return {"success": False, "error": "GITHUB_PAT not configured"}
    if not cfg["repo"]:
        return {"success": False, "error": "GITHUB_BLOG_REPO not configured"}

    pat = cfg["pat"]
    repo = cfg["repo"]

    # Read and encode the image
    src = Path(local_path)
    if not src.exists():
        return {"success": False, "error": f"Image not found: {local_path}"}

    if not filename:
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{src.name}"

    # Build repo path
    parts = [_IMAGES_DIR]
    if subfolder:
        parts.append(subfolder.strip("/"))
    parts.append(filename)
    repo_path = "/".join(parts)

    try:
        with open(local_path, "rb") as f:
            content_bytes = f.read()
    except IOError as e:
        return {"success": False, "error": f"Failed to read image: {e}"}

    encoded = b64encode(content_bytes).decode("ascii")

    # Commit via GitHub Contents API
    api_url = f"https://api.github.com/repos/{repo}/contents/{repo_path}"

    try:
        import json

        headers = {
            "Authorization": f"token {pat}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Archi/1.0",
            "Content-Type": "application/json",
        }
        body = json.dumps({
            "message": f"Add content image: {filename}",
            "content": encoded,
        }).encode("utf-8")

        req = Request(api_url, data=body, headers=headers, method="PUT")
        with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        public_url = get_public_url(repo, repo_path)

        logger.info("Image uploaded to GitHub: %s → %s", filename, public_url)
        return {
            "success": True,
            "url": public_url,
            "repo_path": repo_path,
            "sha": result.get("content", {}).get("sha", ""),
            "size_bytes": len(content_bytes),
        }

    except HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        logger.error("GitHub image upload failed (%d): %s", e.code, err_body)
        if e.code == 422:
            # File already exists — return the URL anyway
            public_url = get_public_url(repo, repo_path)
            return {
                "success": True,
                "url": public_url,
                "repo_path": repo_path,
                "note": "Image already exists at this path",
            }
        return {"success": False, "error": f"GitHub API error {e.code}: {err_body}"}
    except (URLError, Exception) as e:
        logger.error("Image upload failed: %s", e)
        return {"success": False, "error": str(e)}


def upload_for_platform(
    local_path: str,
    platform: str = "",
    topic: str = "",
) -> Dict[str, Any]:
    """Upload an image with platform-organized subfolder and descriptive name.

    Convenience wrapper around upload_image() that organizes uploads by
    platform (e.g., images/content/instagram/...).

    Args:
        local_path: Path to the local image file.
        platform: Target platform (instagram_post, facebook_post, etc.).
        topic: Content topic for the filename.

    Returns:
        Dict with: success, url, repo_path, error.
    """
    # Map content formats to clean subfolder names
    platform_folders = {
        "instagram_post": "instagram",
        "instagram_story": "instagram",
        "facebook_post": "facebook",
        "tweet": "twitter",
        "twitter": "twitter",
        "blog": "blog",
        "youtube": "youtube",
        "reddit": "reddit",
    }
    subfolder = platform_folders.get(platform, platform or "misc")

    # Build descriptive filename
    ts = time.strftime("%Y%m%d_%H%M%S")
    slug = "".join(c if c.isalnum() else "_" for c in topic[:30]).strip("_").lower()
    ext = Path(local_path).suffix or ".png"
    filename = f"{ts}_{slug}{ext}" if slug else f"{ts}{ext}"

    return upload_image(local_path, filename=filename, subfolder=subfolder)
