"""
Content creation and publishing pipeline.

Generates content in multiple formats (blog, tweet, reddit, video_script)
and publishes to platforms via their APIs. Logs all activity to
logs/content_log.jsonl.

Platforms:
  - GitHub Pages blog: commits markdown posts via GitHub API (PyGithub)
  - Twitter/X: posts tweets via Tweepy (free tier, write-only)
  - Reddit: posts via PRAW
  - YouTube: uploads videos + metadata via YouTube Data API v3 (OAuth 2.0)
  - Facebook Pages: text + photo posts via Meta Graph API (stdlib only)
  - Instagram: single image + carousel via Meta Graph API (stdlib only)

Phase 1 (session 228): generate + GitHub blog + logging.
Phase 2 (session 229): YouTube publisher + video_script format.
Phase 3 (session 230): Meta Graph API (Facebook Pages + Instagram).
"""

import json
import logging
import os
import time
from base64 import b64encode
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.utils.paths import base_path

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 15
_CONTENT_LOG = os.path.join(base_path(), "logs", "content_log.jsonl")

# ── Content Generation ──────────────────────────────────────────────────

# Format templates for the model prompt
_FORMAT_PROMPTS = {
    "blog": (
        "Write a blog post about: {topic}\n\n"
        "Requirements:\n"
        "- 500-1200 words, engaging and informative\n"
        "- Include a compelling introduction and conclusion\n"
        "- Use markdown formatting (headers, lists, emphasis)\n"
        "- Conversational but knowledgeable tone\n"
        "- {extra_context}\n\n"
        "Return ONLY the blog post content in markdown (no frontmatter — I'll add that)."
    ),
    "tweet": (
        "Write a tweet about: {topic}\n\n"
        "Requirements:\n"
        "- Maximum 270 characters (leave room for platform overhead)\n"
        "- Punchy, engaging, share-worthy\n"
        "- Include 1-2 relevant hashtags if natural\n"
        "- {extra_context}\n\n"
        "Return ONLY the tweet text, nothing else."
    ),
    "tweet_thread": (
        "Write a tweet thread (3-6 tweets) about: {topic}\n\n"
        "Requirements:\n"
        "- Each tweet ≤270 characters\n"
        "- First tweet hooks the reader\n"
        "- Last tweet has a call-to-action or takeaway\n"
        "- Number each tweet (1/, 2/, etc.)\n"
        "- {extra_context}\n\n"
        "Return ONLY the thread, one tweet per line, numbered."
    ),
    "reddit": (
        "Write a Reddit post about: {topic}\n\n"
        "Requirements:\n"
        "- Title: compelling, under 300 chars\n"
        "- Body: informative, 200-800 words\n"
        "- Reddit-appropriate tone (not corporate, not clickbait)\n"
        "- Include relevant details and sources where appropriate\n"
        "- {extra_context}\n\n"
        "Return in this exact format:\n"
        "TITLE: <your title>\n"
        "BODY:\n<your post body in markdown>"
    ),
    "video_script": (
        "Write a YouTube video script about: {topic}\n\n"
        "Requirements:\n"
        "- 3-8 minute speaking time (~450-1200 words)\n"
        "- TITLE: compelling, under 100 chars, YouTube-optimized\n"
        "- DESCRIPTION: 150-300 words with timestamps, links, keywords\n"
        "- TAGS: 5-15 comma-separated tags for discoverability\n"
        "- SCRIPT: structured with [INTRO], [MAIN], [OUTRO] sections\n"
        "- Conversational, engaging tone — written to be spoken aloud\n"
        "- Hook in the first 10 seconds\n"
        "- Clear call-to-action at the end\n"
        "- {extra_context}\n\n"
        "Return in this exact format:\n"
        "TITLE: <your title>\n"
        "DESCRIPTION:\n<video description with timestamps>\n"
        "TAGS: tag1, tag2, tag3\n"
        "SCRIPT:\n<full video script with section markers>"
    ),
}


def generate_content(
    router,
    topic: str,
    content_format: str = "blog",
    extra_context: str = "",
) -> Optional[Dict[str, Any]]:
    """Generate content using the model.

    Args:
        router: Model router for generation.
        topic: What to write about.
        content_format: One of "blog", "tweet", "tweet_thread", "reddit".
        extra_context: Additional instructions (audience, tone, etc.).

    Returns:
        Dict with keys: format, topic, content, title (for blog/reddit),
        generated_at. None on failure.
    """
    template = _FORMAT_PROMPTS.get(content_format)
    if not template:
        logger.warning("Unknown content format: %s", content_format)
        return None

    prompt = template.format(
        topic=topic,
        extra_context=extra_context or "No additional context.",
    )

    try:
        resp = router.generate(prompt=prompt, max_tokens=2000, temperature=0.7)
        text = (resp.get("text") or resp.get("content") or "").strip()
        if not text:
            logger.warning("Empty content generated for topic: %s", topic)
            return None
    except Exception as e:
        logger.error("Content generation failed: %s", e)
        return None

    result = {
        "format": content_format,
        "topic": topic,
        "content": text,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # Extract title for formats that have one
    if content_format == "blog":
        result["title"] = _extract_blog_title(text, topic)
    elif content_format == "reddit":
        title, body = _parse_reddit_post(text)
        result["title"] = title or topic
        result["content"] = body or text
    elif content_format == "video_script":
        parsed = _parse_video_script(text)
        result["title"] = parsed.get("title") or topic.title()
        result["description"] = parsed.get("description") or ""
        result["tags"] = parsed.get("tags") or []
        result["script"] = parsed.get("script") or text
        result["content"] = text  # Keep full text too

    return result


def _extract_blog_title(content: str, fallback: str) -> str:
    """Extract title from the first H1 in markdown, or use fallback."""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("##"):
            return stripped[2:].strip()
    return fallback.title()


def _parse_reddit_post(text: str) -> tuple:
    """Parse TITLE: / BODY: format from generated reddit content."""
    title, body = None, None
    lines = text.split("\n")
    body_start = None
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("TITLE:"):
            title = line.split(":", 1)[1].strip()
        elif line.strip().upper().startswith("BODY:"):
            body_start = i + 1
            break
    if body_start is not None:
        body = "\n".join(lines[body_start:]).strip()
    return (title, body)


def _parse_video_script(text: str) -> Dict[str, Any]:
    """Parse TITLE/DESCRIPTION/TAGS/SCRIPT from generated video script content."""
    result: Dict[str, Any] = {}
    lines = text.split("\n")
    current_section = None
    section_lines: List[str] = []

    def _flush():
        if current_section and section_lines:
            content = "\n".join(section_lines).strip()
            if current_section == "tags":
                result["tags"] = [t.strip() for t in content.split(",") if t.strip()]
            else:
                result[current_section] = content

    for line in lines:
        stripped = line.strip().upper()
        if stripped.startswith("TITLE:"):
            _flush()
            result["title"] = line.split(":", 1)[1].strip()
            current_section = None
            section_lines = []
        elif stripped.startswith("DESCRIPTION:"):
            _flush()
            rest = line.split(":", 1)[1].strip()
            current_section = "description"
            section_lines = [rest] if rest else []
        elif stripped.startswith("TAGS:"):
            _flush()
            rest = line.split(":", 1)[1].strip()
            current_section = "tags"
            section_lines = [rest] if rest else []
        elif stripped.startswith("SCRIPT:"):
            _flush()
            rest = line.split(":", 1)[1].strip()
            current_section = "script"
            section_lines = [rest] if rest else []
        elif current_section:
            section_lines.append(line)

    _flush()
    return result


# ── GitHub Blog Publisher ───────────────────────────────────────────────

def _get_github_config() -> tuple:
    """Return (pat, repo) from env vars, or (None, None)."""
    pat = os.environ.get("GITHUB_PAT", "").strip() or None
    repo = os.environ.get("GITHUB_BLOG_REPO", "").strip() or None
    return (pat, repo)


def _github_api(method: str, url: str, pat: str, data: Optional[dict] = None) -> dict:
    """Make a GitHub API request. Returns parsed JSON response."""
    headers = {
        "Authorization": f"token {pat}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Archi/1.0",
    }
    body = json.dumps(data).encode("utf-8") if data else None
    req = Request(url, data=body, headers=headers, method=method)
    if body:
        req.add_header("Content-Type", "application/json")
    with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    import re
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug[:60]


def publish_to_github_blog(
    title: str,
    body: str,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Publish a markdown blog post to a GitHub Pages repo.

    Creates/commits a Jekyll-format post file:
    _posts/YYYY-MM-DD-slug.md with YAML frontmatter.

    Returns dict with success, url, error keys.
    """
    pat, repo = _get_github_config()
    if not pat:
        return {"success": False, "error": "GITHUB_PAT not configured in .env"}
    if not repo:
        return {"success": False, "error": "GITHUB_BLOG_REPO not configured in .env"}

    date_str = time.strftime("%Y-%m-%d")
    slug = _slugify(title)
    filename = f"_posts/{date_str}-{slug}.md"

    # Build Jekyll frontmatter
    tags_str = "\n".join(f"  - {t}" for t in (tags or []))
    frontmatter = (
        f"---\n"
        f"layout: post\n"
        f"title: \"{title}\"\n"
        f"date: {date_str}\n"
        f"author: Archi\n"
    )
    if tags_str:
        frontmatter += f"tags:\n{tags_str}\n"
    frontmatter += f"---\n\n"

    full_content = frontmatter + body
    encoded = b64encode(full_content.encode("utf-8")).decode("ascii")

    api_url = f"https://api.github.com/repos/{repo}/contents/{filename}"

    try:
        _github_api("PUT", api_url, pat, {
            "message": f"Add post: {title}",
            "content": encoded,
        })
        # Construct the likely URL (GitHub Pages convention)
        owner = repo.split("/")[0] if "/" in repo else repo
        page_url = f"https://{owner}.github.io/{repo.split('/')[-1]}/{date_str.replace('-', '/')}/{slug}/"
        _log_content_event("publish", "github_blog", title, page_url)
        return {"success": True, "url": page_url, "filename": filename}
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        logger.error("GitHub API error %d: %s", e.code, err_body[:200])
        if e.code == 404:
            return {"success": False, "error": f"Repo '{repo}' not found. Create it first or check GITHUB_BLOG_REPO."}
        if e.code == 422:
            return {"success": False, "error": f"Post '{filename}' may already exist."}
        return {"success": False, "error": f"GitHub API error {e.code}: {err_body[:100]}"}
    except (URLError, Exception) as e:
        logger.error("GitHub publish failed: %s", e)
        return {"success": False, "error": str(e)}


def setup_github_blog(repo_name: str = "archi-blog") -> Dict[str, Any]:
    """Create a GitHub Pages repo with Jekyll config if it doesn't exist.

    Returns dict with success, repo_url, error keys.
    """
    pat, _ = _get_github_config()
    if not pat:
        return {"success": False, "error": "GITHUB_PAT not configured in .env"}

    # Check if repo exists
    try:
        _github_api("GET", f"https://api.github.com/repos/{repo_name}", pat)
        return {"success": True, "repo_url": f"https://github.com/{repo_name}", "note": "Repo already exists."}
    except HTTPError as e:
        if e.code != 404:
            return {"success": False, "error": f"GitHub API error: {e.code}"}

    # Create repo via user endpoint (repo_name should be just the name, not owner/name)
    name_only = repo_name.split("/")[-1] if "/" in repo_name else repo_name
    try:
        result = _github_api("POST", "https://api.github.com/user/repos", pat, {
            "name": name_only,
            "description": "Archi's blog — AI-generated content",
            "auto_init": True,
            "has_pages": True,
        })
        repo_full = result.get("full_name", repo_name)

        # Add minimal Jekyll _config.yml
        config_content = (
            "title: Archi's Blog\n"
            "description: AI-generated thoughts, research, and commentary\n"
            "theme: minima\n"
            "author: Archi\n"
            "plugins:\n"
            "  - jekyll-feed\n"
        )
        encoded = b64encode(config_content.encode("utf-8")).decode("ascii")
        _github_api("PUT", f"https://api.github.com/repos/{repo_full}/contents/_config.yml", pat, {
            "message": "Add Jekyll config",
            "content": encoded,
        })

        # Create _posts directory with a placeholder
        placeholder = (
            "---\n"
            "layout: post\n"
            f"title: \"Hello World\"\n"
            f"date: {time.strftime('%Y-%m-%d')}\n"
            "author: Archi\n"
            "---\n\n"
            "This is Archi's first blog post. More to come.\n"
        )
        encoded = b64encode(placeholder.encode("utf-8")).decode("ascii")
        slug = "hello-world"
        _github_api("PUT", f"https://api.github.com/repos/{repo_full}/contents/_posts/{time.strftime('%Y-%m-%d')}-{slug}.md", pat, {
            "message": "Add first post",
            "content": encoded,
        })

        _log_content_event("setup", "github_blog", repo_full, f"https://github.com/{repo_full}")
        return {"success": True, "repo_url": f"https://github.com/{repo_full}"}
    except (HTTPError, URLError, Exception) as e:
        logger.error("GitHub blog setup failed: %s", e)
        return {"success": False, "error": str(e)}


# ── Twitter Publisher ───────────────────────────────────────────────────

def _get_twitter_config() -> tuple:
    """Return (api_key, api_secret, access_token, access_secret) or Nones."""
    return (
        os.environ.get("TWITTER_API_KEY", "").strip() or None,
        os.environ.get("TWITTER_API_SECRET", "").strip() or None,
        os.environ.get("TWITTER_ACCESS_TOKEN", "").strip() or None,
        os.environ.get("TWITTER_ACCESS_SECRET", "").strip() or None,
    )


def publish_tweet(text: str) -> Dict[str, Any]:
    """Post a tweet via Twitter/X API.

    Returns dict with success, tweet_id, error keys.
    """
    keys = _get_twitter_config()
    if not all(keys):
        return {"success": False, "error": "Twitter API credentials not configured in .env"}

    if len(text) > 280:
        return {"success": False, "error": f"Tweet too long ({len(text)} chars, max 280)"}

    try:
        import tweepy
    except ImportError:
        return {"success": False, "error": "tweepy not installed (pip install tweepy)"}

    try:
        client = tweepy.Client(
            consumer_key=keys[0],
            consumer_secret=keys[1],
            access_token=keys[2],
            access_token_secret=keys[3],
        )
        response = client.create_tweet(text=text)
        tweet_id = response.data["id"]
        url = f"https://x.com/i/status/{tweet_id}"
        _log_content_event("publish", "twitter", text[:80], url)
        return {"success": True, "tweet_id": tweet_id, "url": url}
    except Exception as e:
        logger.error("Tweet publish failed: %s", e)
        return {"success": False, "error": str(e)}


def publish_tweet_thread(tweets: List[str]) -> Dict[str, Any]:
    """Post a tweet thread (list of tweet texts). Each reply chains to previous."""
    keys = _get_twitter_config()
    if not all(keys):
        return {"success": False, "error": "Twitter API credentials not configured in .env"}

    try:
        import tweepy
    except ImportError:
        return {"success": False, "error": "tweepy not installed (pip install tweepy)"}

    for i, t in enumerate(tweets):
        if len(t) > 280:
            return {"success": False, "error": f"Tweet {i+1} too long ({len(t)} chars)"}

    try:
        client = tweepy.Client(
            consumer_key=keys[0], consumer_secret=keys[1],
            access_token=keys[2], access_token_secret=keys[3],
        )
        prev_id = None
        posted = []
        for tweet_text in tweets:
            kwargs = {"text": tweet_text}
            if prev_id:
                kwargs["in_reply_to_tweet_id"] = prev_id
            resp = client.create_tweet(**kwargs)
            prev_id = resp.data["id"]
            posted.append(prev_id)

        url = f"https://x.com/i/status/{posted[0]}"
        _log_content_event("publish", "twitter_thread", tweets[0][:60], url)
        return {"success": True, "tweet_ids": posted, "url": url, "count": len(posted)}
    except Exception as e:
        logger.error("Thread publish failed: %s", e)
        return {"success": False, "error": str(e)}


# ── Reddit Publisher ────────────────────────────────────────────────────

def _get_reddit_config() -> tuple:
    """Return (client_id, client_secret, username, password) or Nones."""
    return (
        os.environ.get("REDDIT_CLIENT_ID", "").strip() or None,
        os.environ.get("REDDIT_CLIENT_SECRET", "").strip() or None,
        os.environ.get("REDDIT_USERNAME", "").strip() or None,
        os.environ.get("REDDIT_PASSWORD", "").strip() or None,
    )


def publish_reddit_post(
    subreddit: str,
    title: str,
    body: str,
) -> Dict[str, Any]:
    """Post to a subreddit via PRAW.

    Returns dict with success, url, error keys.
    """
    creds = _get_reddit_config()
    if not all(creds):
        return {"success": False, "error": "Reddit API credentials not configured in .env"}

    if len(title) > 300:
        return {"success": False, "error": f"Title too long ({len(title)} chars, max 300)"}

    try:
        import praw
    except ImportError:
        return {"success": False, "error": "praw not installed (pip install praw)"}

    try:
        reddit = praw.Reddit(
            client_id=creds[0],
            client_secret=creds[1],
            user_agent="Archi/1.0 (by /u/ArchiBot)",
            username=creds[2],
            password=creds[3],
        )
        sub = reddit.subreddit(subreddit)
        submission = sub.submit(title=title, selftext=body)
        url = f"https://www.reddit.com{submission.permalink}"
        _log_content_event("publish", "reddit", title[:80], url)
        return {"success": True, "url": url, "submission_id": submission.id}
    except Exception as e:
        logger.error("Reddit publish failed: %s", e)
        return {"success": False, "error": str(e)}


# ── YouTube Publisher ──────────────────────────────────────────────────

def _get_youtube_config() -> Dict[str, Optional[str]]:
    """Return YouTube OAuth config from env vars."""
    return {
        "client_id": os.environ.get("YOUTUBE_CLIENT_ID", "").strip() or None,
        "client_secret": os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip() or None,
        "refresh_token": os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip() or None,
    }


def _get_youtube_service():
    """Build an authenticated YouTube API service using stored refresh token.

    Returns (service, error_string). On success error is None.
    Uses google-auth to refresh tokens without interactive flow.
    """
    config = _get_youtube_config()
    if not all(config.values()):
        missing = [k for k, v in config.items() if not v]
        return None, f"YouTube credentials not configured: {', '.join(missing)}. See .env.example."

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        return None, (
            "YouTube API libraries not installed. Run: "
            "pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        )

    try:
        creds = Credentials(
            token=None,
            refresh_token=config["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            scopes=["https://www.googleapis.com/auth/youtube.upload",
                     "https://www.googleapis.com/auth/youtube"],
        )
        service = build("youtube", "v3", credentials=creds)
        return service, None
    except Exception as e:
        logger.error("YouTube service build failed: %s", e)
        return None, f"YouTube auth failed: {e}"


def publish_to_youtube(
    video_path: str,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    category_id: str = "22",  # "People & Blogs" — safe default
    privacy_status: str = "private",
) -> Dict[str, Any]:
    """Upload a video to YouTube with metadata.

    Args:
        video_path: Path to the video file on disk.
        title: Video title (max 100 chars).
        description: Video description.
        tags: List of tags for discoverability.
        category_id: YouTube category ID (default "22" = People & Blogs).
        privacy_status: "private", "unlisted", or "public".

    Returns dict with success, video_id, url, error keys.
    """
    if not os.path.isfile(video_path):
        return {"success": False, "error": f"Video file not found: {video_path}"}

    file_size = os.path.getsize(video_path)
    if file_size == 0:
        return {"success": False, "error": "Video file is empty"}
    # YouTube max is 256 GB but let's cap at 2 GB for sanity
    if file_size > 2 * 1024 * 1024 * 1024:
        return {"success": False, "error": f"Video too large ({file_size / 1e9:.1f} GB, max 2 GB)"}

    if len(title) > 100:
        return {"success": False, "error": f"Title too long ({len(title)} chars, max 100)"}

    if privacy_status not in ("private", "unlisted", "public"):
        return {"success": False, "error": f"Invalid privacy status: {privacy_status}"}

    service, err = _get_youtube_service()
    if err:
        return {"success": False, "error": err}

    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return {"success": False, "error": "google-api-python-client not installed"}

    body = {
        "snippet": {
            "title": title,
            "description": description or "",
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        media = MediaFileUpload(
            video_path,
            chunksize=10 * 1024 * 1024,  # 10 MB chunks
            resumable=True,
        )
        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        # Resumable upload with retry
        response = _resumable_upload(request)
        if response is None:
            return {"success": False, "error": "Upload failed after retries"}

        video_id = response["id"]
        url = f"https://youtu.be/{video_id}"
        _log_content_event("publish", "youtube", title[:80], url)
        logger.info("YouTube upload success: %s (%s)", video_id, title)
        return {"success": True, "video_id": video_id, "url": url}
    except Exception as e:
        logger.error("YouTube upload failed: %s", e)
        return {"success": False, "error": str(e)}


def _resumable_upload(request, max_retries: int = 5) -> Optional[dict]:
    """Execute a resumable upload with exponential backoff on transient errors."""
    import random

    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                logger.debug("YouTube upload %d%% complete", int(status.progress() * 100))
        except Exception as e:
            err_str = str(e)
            # Retry on transient HTTP errors (500, 502, 503, 504)
            if retry < max_retries and any(code in err_str for code in ("500", "502", "503", "504")):
                retry += 1
                sleep_time = random.random() * (2 ** retry)
                logger.warning("YouTube upload retry %d/%d (sleeping %.1fs): %s",
                               retry, max_retries, sleep_time, e)
                time.sleep(sleep_time)
            else:
                raise
    return response


def update_youtube_metadata(
    video_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
    privacy_status: Optional[str] = None,
) -> Dict[str, Any]:
    """Update metadata on an existing YouTube video.

    Only provided fields are updated; others are left unchanged.
    Returns dict with success, error keys.
    """
    service, err = _get_youtube_service()
    if err:
        return {"success": False, "error": err}

    try:
        # First fetch current video data
        current = service.videos().list(
            part="snippet,status",
            id=video_id,
        ).execute()

        items = current.get("items", [])
        if not items:
            return {"success": False, "error": f"Video not found: {video_id}"}

        video = items[0]
        snippet = video["snippet"]
        status = video["status"]

        # Apply updates
        if title is not None:
            snippet["title"] = title
        if description is not None:
            snippet["description"] = description
        if tags is not None:
            snippet["tags"] = tags
        if privacy_status is not None:
            status["privacyStatus"] = privacy_status

        # categoryId is required for update even if not changing
        if "categoryId" not in snippet:
            snippet["categoryId"] = "22"

        service.videos().update(
            part="snippet,status",
            body={"id": video_id, "snippet": snippet, "status": status},
        ).execute()

        _log_content_event("update", "youtube", snippet["title"][:80],
                           f"https://youtu.be/{video_id}")
        return {"success": True, "video_id": video_id}
    except Exception as e:
        logger.error("YouTube metadata update failed: %s", e)
        return {"success": False, "error": str(e)}


def youtube_authenticate(port: int = 8090) -> Dict[str, Any]:
    """Run the full OAuth flow for YouTube: opens browser, catches the redirect.

    Starts a temporary local server on the given port, opens the consent
    screen in the default browser, and automatically captures the auth code
    when Google redirects back. Returns the refresh token to store in .env.

    Args:
        port: Local port for the redirect server (default 8090).

    Returns dict with success, refresh_token, error keys.
    """
    config = _get_youtube_config()
    if not config["client_id"] or not config["client_secret"]:
        return {"success": False, "error": "YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET must be set first."}

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return {"success": False, "error": "google-auth-oauthlib not installed"}

    try:
        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [f"http://localhost:{port}"],
                }
            },
            scopes=[
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube",
            ],
        )
        # This opens the browser, runs a local server, and waits for the redirect
        creds = flow.run_local_server(
            port=port,
            prompt="consent",
            access_type="offline",
        )
        if not creds.refresh_token:
            return {"success": False, "error": "No refresh token received. Try revoking app access at https://myaccount.google.com/permissions and running again."}
        return {
            "success": True,
            "refresh_token": creds.refresh_token,
            "note": "Add this to .env as YOUTUBE_REFRESH_TOKEN",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Meta Graph API Publisher (Facebook Pages + Instagram) ──────────────
#
# Session 230: Single developer account covers both platforms.  Uses only
# stdlib urllib (no extra library).  Requires:
#   - META_PAGE_ACCESS_TOKEN — long-lived Page access token with
#     pages_manage_posts and pages_read_engagement permissions.
#   - META_PAGE_ID — numeric Facebook Page ID.
#   - META_INSTAGRAM_ACCOUNT_ID (optional) — IG Business account ID
#     linked to the same Page (for Instagram publishing).
# All three are free-tier, no credit card required.

def _get_meta_config() -> Dict[str, Optional[str]]:
    """Return Meta Graph API configuration from environment."""
    return {
        "page_access_token": os.environ.get("META_PAGE_ACCESS_TOKEN", "").strip() or None,
        "page_id": os.environ.get("META_PAGE_ID", "").strip() or None,
        "instagram_account_id": os.environ.get("META_INSTAGRAM_ACCOUNT_ID", "").strip() or None,
    }


def _meta_graph_post(endpoint: str, data: dict, token: str) -> Dict[str, Any]:
    """Make a POST request to the Meta Graph API (v22.0).

    Returns the parsed JSON response or an error dict.
    """
    import json as _json
    url = f"https://graph.facebook.com/v22.0/{endpoint}"
    data["access_token"] = token
    body = _json.dumps(data).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        logger.error("Meta Graph API error %d: %s", e.code, error_body)
        return {"error": f"HTTP {e.code}: {error_body[:300]}"}
    except URLError as e:
        logger.error("Meta Graph API connection error: %s", e.reason)
        return {"error": f"Connection error: {e.reason}"}
    except Exception as e:
        logger.error("Meta Graph API unexpected error: %s", e)
        return {"error": str(e)}


def _meta_graph_get(endpoint: str, params: dict, token: str) -> Dict[str, Any]:
    """Make a GET request to the Meta Graph API."""
    import json as _json
    from urllib.parse import urlencode
    params["access_token"] = token
    url = f"https://graph.facebook.com/v22.0/{endpoint}?{urlencode(params)}"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return {"error": f"HTTP {e.code}: {error_body[:300]}"}
    except Exception as e:
        return {"error": str(e)}


def publish_to_facebook(
    message: str,
    link: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish a post to a Facebook Page via the Graph API.

    Args:
        message: The post text.
        link: Optional URL to attach (creates a link preview).

    Returns:
        Dict with success, post_id, url, error keys.
    """
    config = _get_meta_config()
    if not config["page_access_token"] or not config["page_id"]:
        missing = []
        if not config["page_access_token"]:
            missing.append("META_PAGE_ACCESS_TOKEN")
        if not config["page_id"]:
            missing.append("META_PAGE_ID")
        return {"success": False, "error": f"Facebook Page credentials not configured: {', '.join(missing)}"}

    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    data: Dict[str, Any] = {"message": message}
    if link:
        data["link"] = link

    result = _meta_graph_post(
        f"{config['page_id']}/feed",
        data,
        config["page_access_token"],
    )

    if "error" in result:
        return {"success": False, "error": result["error"]}

    post_id = result.get("id", "")
    url = f"https://www.facebook.com/{post_id}" if post_id else ""
    _log_content_event("publish", "facebook", message[:80], url)
    logger.info("Published to Facebook Page: %s", post_id)
    return {"success": True, "post_id": post_id, "url": url}


def publish_to_facebook_photo(
    image_url: str,
    caption: str = "",
) -> Dict[str, Any]:
    """Publish a photo post to a Facebook Page.

    Args:
        image_url: Public URL of the image to post.
        caption: Optional caption text.

    Returns:
        Dict with success, post_id, error keys.
    """
    config = _get_meta_config()
    if not config["page_access_token"] or not config["page_id"]:
        return {"success": False, "error": "Facebook Page credentials not configured"}

    data: Dict[str, Any] = {"url": image_url}
    if caption:
        data["message"] = caption

    result = _meta_graph_post(
        f"{config['page_id']}/photos",
        data,
        config["page_access_token"],
    )

    if "error" in result:
        return {"success": False, "error": result["error"]}

    post_id = result.get("id", "")
    _log_content_event("publish", "facebook_photo", caption[:80], "")
    logger.info("Published photo to Facebook Page: %s", post_id)
    return {"success": True, "post_id": post_id}


def publish_to_instagram(
    image_url: str,
    caption: str = "",
) -> Dict[str, Any]:
    """Publish a single image post to Instagram via the Content Publishing API.

    Instagram publishing is a two-step process:
      1. Create a media container (POST /{ig-user-id}/media)
      2. Publish the container (POST /{ig-user-id}/media_publish)

    Args:
        image_url: Public URL of a JPEG image (required by Instagram).
        caption: Optional caption (can include hashtags).

    Returns:
        Dict with success, media_id, error keys.
    """
    config = _get_meta_config()
    if not config["page_access_token"]:
        return {"success": False, "error": "META_PAGE_ACCESS_TOKEN not configured"}
    if not config["instagram_account_id"]:
        return {"success": False, "error": "META_INSTAGRAM_ACCOUNT_ID not configured — link your IG Business account"}

    ig_id = config["instagram_account_id"]
    token = config["page_access_token"]

    # Step 1: Create container
    container_data: Dict[str, Any] = {"image_url": image_url}
    if caption:
        container_data["caption"] = caption

    container_result = _meta_graph_post(f"{ig_id}/media", container_data, token)
    if "error" in container_result:
        return {"success": False, "error": f"Container creation failed: {container_result['error']}"}

    container_id = container_result.get("id")
    if not container_id:
        return {"success": False, "error": "No container ID returned from Instagram API"}

    # Step 2: Wait for container to be ready (check status)
    # Instagram processes images asynchronously; poll up to 30s.
    _max_wait = 30
    _start = time.time()
    while time.time() - _start < _max_wait:
        status_result = _meta_graph_get(
            container_id,
            {"fields": "status_code"},
            token,
        )
        status = status_result.get("status_code", "")
        if status == "FINISHED":
            break
        if status == "ERROR":
            return {"success": False, "error": f"Instagram container processing failed: {status_result}"}
        time.sleep(2)
    else:
        return {"success": False, "error": "Instagram container processing timed out (30s)"}

    # Step 3: Publish
    publish_result = _meta_graph_post(
        f"{ig_id}/media_publish",
        {"creation_id": container_id},
        token,
    )
    if "error" in publish_result:
        return {"success": False, "error": f"Publish failed: {publish_result['error']}"}

    media_id = publish_result.get("id", "")
    _log_content_event("publish", "instagram", caption[:80], "")
    logger.info("Published to Instagram: %s", media_id)
    return {"success": True, "media_id": media_id}


def publish_to_instagram_carousel(
    image_urls: List[str],
    caption: str = "",
) -> Dict[str, Any]:
    """Publish a carousel (multiple images) to Instagram.

    Each image must be a public JPEG URL.  Instagram allows up to 10 images
    per carousel.  This counts as 1 of the 25 daily API-published posts.

    Args:
        image_urls: List of 2-10 public JPEG image URLs.
        caption: Caption for the carousel post.

    Returns:
        Dict with success, media_id, error keys.
    """
    config = _get_meta_config()
    if not config["page_access_token"]:
        return {"success": False, "error": "META_PAGE_ACCESS_TOKEN not configured"}
    if not config["instagram_account_id"]:
        return {"success": False, "error": "META_INSTAGRAM_ACCOUNT_ID not configured"}
    if len(image_urls) < 2:
        return {"success": False, "error": "Carousel requires at least 2 images"}
    if len(image_urls) > 10:
        return {"success": False, "error": "Carousel supports at most 10 images"}

    ig_id = config["instagram_account_id"]
    token = config["page_access_token"]

    # Step 1: Create child containers for each image
    child_ids = []
    for i, url in enumerate(image_urls):
        child = _meta_graph_post(
            f"{ig_id}/media",
            {"image_url": url, "is_carousel_item": True},
            token,
        )
        if "error" in child:
            return {"success": False, "error": f"Image {i+1} container failed: {child['error']}"}
        child_id = child.get("id")
        if not child_id:
            return {"success": False, "error": f"No container ID for image {i+1}"}
        child_ids.append(child_id)

    # Step 2: Create carousel container
    carousel_data: Dict[str, Any] = {
        "media_type": "CAROUSEL",
        "children": ",".join(child_ids),
    }
    if caption:
        carousel_data["caption"] = caption

    carousel = _meta_graph_post(f"{ig_id}/media", carousel_data, token)
    if "error" in carousel:
        return {"success": False, "error": f"Carousel container failed: {carousel['error']}"}

    carousel_id = carousel.get("id")
    if not carousel_id:
        return {"success": False, "error": "No carousel container ID returned"}

    # Step 3: Wait for processing
    _max_wait = 60  # Carousels take longer
    _start = time.time()
    while time.time() - _start < _max_wait:
        status_result = _meta_graph_get(carousel_id, {"fields": "status_code"}, token)
        status = status_result.get("status_code", "")
        if status == "FINISHED":
            break
        if status == "ERROR":
            return {"success": False, "error": f"Carousel processing failed: {status_result}"}
        time.sleep(3)
    else:
        return {"success": False, "error": "Carousel processing timed out (60s)"}

    # Step 4: Publish
    publish_result = _meta_graph_post(
        f"{ig_id}/media_publish",
        {"creation_id": carousel_id},
        token,
    )
    if "error" in publish_result:
        return {"success": False, "error": f"Carousel publish failed: {publish_result['error']}"}

    media_id = publish_result.get("id", "")
    _log_content_event("publish", "instagram_carousel", caption[:80], "")
    logger.info("Published carousel to Instagram: %s (%d images)", media_id, len(image_urls))
    return {"success": True, "media_id": media_id, "image_count": len(image_urls)}


# ── Content Log ─────────────────────────────────────────────────────────

def _log_content_event(action: str, platform: str, title: str, url: str = "") -> None:
    """Append an event to the content log."""
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "action": action,
        "platform": platform,
        "title": title,
        "url": url,
    }
    try:
        os.makedirs(os.path.dirname(_CONTENT_LOG), exist_ok=True)
        with open(_CONTENT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.debug("Content log write failed: %s", e)


def get_content_log(limit: int = 20) -> List[Dict[str, Any]]:
    """Read the most recent content log entries."""
    try:
        with open(_CONTENT_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries
    except (OSError, json.JSONDecodeError):
        return []


def get_content_summary() -> str:
    """Return a human-readable summary of recent content activity."""
    entries = get_content_log(20)
    if not entries:
        return "No content published yet."
    lines = []
    for e in reversed(entries):
        platform = e.get("platform", "?")
        title = e.get("title", "?")[:60]
        ts = e.get("timestamp", "?")
        url = e.get("url", "")
        line = f"- [{ts}] {platform}: {title}"
        if url:
            line += f" ({url})"
        lines.append(line)
    return "\n".join(lines)
