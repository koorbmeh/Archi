"""
News aggregation from free public sources (no API keys required).

Sources:
  - Hacker News top stories (Firebase API, no auth)
  - RSS feeds from major outlets (feedparser)

Designed for daily digest: call get_headlines() once per morning.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError
import json

logger = logging.getLogger(__name__)

# Timeout for HTTP requests (seconds)
_HTTP_TIMEOUT = 10

# ── Hacker News ──────────────────────────────────────────────────────────

_HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
_HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"


def _fetch_json(url: str) -> Any:
    """Fetch JSON from a URL using stdlib only."""
    req = Request(url, headers={"User-Agent": "Archi/1.0"})
    with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_hn_story(story_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single HN story by ID."""
    try:
        item = _fetch_json(_HN_ITEM.format(story_id))
        if not item or item.get("type") != "story" or item.get("dead"):
            return None
        return {
            "title": item.get("title", ""),
            "url": item.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
            "source": "Hacker News",
            "score": item.get("score", 0),
        }
    except Exception as e:
        logger.debug("HN story %d fetch failed: %s", story_id, e)
        return None


def get_hn_top(count: int = 8) -> List[Dict[str, str]]:
    """Fetch top Hacker News stories. Returns list of {title, url, source, score}."""
    try:
        ids = _fetch_json(_HN_TOP)[:count * 2]  # fetch extra in case some fail
    except Exception as e:
        logger.warning("HN top stories fetch failed: %s", e)
        return []

    stories = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_fetch_hn_story, sid): sid for sid in ids}
        for fut in as_completed(futures):
            story = fut.result()
            if story and story["title"]:
                stories.append(story)
            if len(stories) >= count:
                break

    # Sort by score descending
    stories.sort(key=lambda s: s.get("score", 0), reverse=True)
    return stories[:count]


# ── RSS Feeds ────────────────────────────────────────────────────────────

# Default feeds — tech + general news. Can be extended via config.
_DEFAULT_FEEDS: List[Dict[str, str]] = [
    {"url": "https://feeds.bbci.co.uk/news/technology/rss.xml", "source": "BBC Tech"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "source": "NYT Tech"},
    {"url": "https://www.reddit.com/r/technology/.rss", "source": "r/technology"},
]


def _parse_rss(feed_url: str, source: str, count: int = 5) -> List[Dict[str, str]]:
    """Parse an RSS feed and return top entries."""
    try:
        import feedparser
    except ImportError:
        logger.debug("feedparser not installed — RSS feeds unavailable")
        return []

    try:
        feed = feedparser.parse(feed_url)
        entries = []
        for entry in feed.entries[:count]:
            entries.append({
                "title": entry.get("title", "").strip(),
                "url": entry.get("link", ""),
                "source": source,
            })
        return entries
    except Exception as e:
        logger.debug("RSS parse failed for %s: %s", source, e)
        return []


def get_rss_headlines(
    feeds: Optional[List[Dict[str, str]]] = None,
    per_feed: int = 3,
) -> List[Dict[str, str]]:
    """Fetch headlines from RSS feeds. Returns list of {title, url, source}."""
    feeds = feeds or _DEFAULT_FEEDS
    all_entries: List[Dict[str, str]] = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_parse_rss, f["url"], f["source"], per_feed): f["source"]
            for f in feeds
        }
        for fut in as_completed(futures):
            try:
                all_entries.extend(fut.result())
            except Exception as e:
                logger.debug("RSS feed fetch failed: %s", e)

    return all_entries


# ── Combined Headlines ───────────────────────────────────────────────────

def get_headlines(hn_count: int = 5, rss_per_feed: int = 2) -> Dict[str, Any]:
    """Get a combined news digest from all sources.

    Returns:
        {
            "hn": [...],       # Hacker News stories
            "rss": [...],      # RSS headlines
            "fetched_at": ..., # ISO timestamp
            "summary": "...",  # One-line summary for the morning digest prompt
        }
    """
    hn = get_hn_top(count=hn_count)
    rss = get_rss_headlines(per_feed=rss_per_feed)

    # Build compact summary for injection into morning report prompt
    lines = []
    for story in hn[:5]:
        lines.append(f"- {story['title']} ({story['source']}, score {story.get('score', '?')})")
    for entry in rss[:6]:
        lines.append(f"- {entry['title']} ({entry['source']})")

    summary = "\n".join(lines) if lines else "No headlines available."

    return {
        "hn": hn,
        "rss": rss,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "summary": summary,
    }
