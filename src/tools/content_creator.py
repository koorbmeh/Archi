"""
Content creation and publishing pipeline.

Generates content in multiple formats (blog, tweet, reddit) and publishes
to platforms via their APIs. Logs all activity to logs/content_log.jsonl.

Platforms:
  - GitHub Pages blog: commits markdown posts via GitHub API (PyGithub)
  - Twitter/X: posts tweets via Tweepy (free tier, write-only)
  - Reddit: posts via PRAW

Phase 1 (session 228): generate + GitHub blog + logging.
Twitter and Reddit publishers are functional stubs awaiting credentials.
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
