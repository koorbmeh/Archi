"""
Tavily API wrapper for AI-agent-optimized web search and content extraction.

Provides both sync and async interfaces. Falls back gracefully to DuckDuckGo
if no Tavily API key is configured.

Env: TAVILY_API_KEY
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Rate limiter — shared across threads, separate from DDG rate limiter
_tavily_lock = threading.Lock()
_last_tavily_time: float = 0.0
_MIN_TAVILY_INTERVAL = 0.5  # seconds between Tavily calls


@dataclass
class TavilyResult:
    """Single search result from Tavily."""
    title: str = ""
    url: str = ""
    content: str = ""  # Extracted text snippet
    score: float = 0.0
    raw_content: str = ""  # Full page content (when include_raw_content=True)


@dataclass
class TavilySearchResponse:
    """Response from a Tavily search."""
    query: str = ""
    results: List[TavilyResult] = field(default_factory=list)
    answer: str = ""  # AI-generated answer (when include_answer=True)
    response_time: float = 0.0


@dataclass
class ExtractedPage:
    """Content extracted from a URL via Tavily Extract."""
    url: str = ""
    raw_content: str = ""
    success: bool = True
    error: str = ""


def _get_api_key() -> Optional[str]:
    """Get Tavily API key from environment."""
    return os.environ.get("TAVILY_API_KEY", "").strip() or None


def _throttle() -> None:
    """Rate-limit Tavily API calls."""
    global _last_tavily_time
    with _tavily_lock:
        elapsed = time.monotonic() - _last_tavily_time
        wait = max(0.0, _MIN_TAVILY_INTERVAL - elapsed)
    if wait > 0:
        time.sleep(wait)
    with _tavily_lock:
        _last_tavily_time = time.monotonic()


class TavilySearch:
    """Tavily API wrapper — search + extract with graceful degradation."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or _get_api_key()
        self._client: Any = None

    @property
    def available(self) -> bool:
        """Check if Tavily is configured and usable."""
        return bool(self._api_key)

    def _get_client(self) -> Any:
        """Lazy-init Tavily client."""
        if self._client is None:
            if not self._api_key:
                raise RuntimeError("TAVILY_API_KEY not configured")
            from tavily import TavilyClient
            self._client = TavilyClient(api_key=self._api_key)
        return self._client

    def search(
        self,
        query: str,
        search_depth: str = "basic",
        max_results: int = 5,
        include_answer: bool = False,
        include_raw_content: bool = False,
        topic: str = "general",
    ) -> TavilySearchResponse:
        """Search with Tavily. Returns structured results with content extracts.

        Args:
            query: Search query string.
            search_depth: "basic" (fast) or "advanced" (thorough, 2x cost).
            max_results: Number of results (1-20).
            include_answer: Include AI-generated answer summary.
            include_raw_content: Include full page content per result.
            topic: "general" or "news".

        Returns:
            TavilySearchResponse with results.
        """
        _throttle()
        client = self._get_client()
        try:
            raw = client.search(
                query=query,
                search_depth=search_depth,
                max_results=max_results,
                include_answer=include_answer,
                include_raw_content=include_raw_content,
                topic=topic,
            )
            results = []
            for r in raw.get("results", []):
                results.append(TavilyResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    content=r.get("content", ""),
                    score=r.get("score", 0.0),
                    raw_content=r.get("raw_content", "") or "",
                ))
            response = TavilySearchResponse(
                query=query,
                results=results,
                answer=raw.get("answer", "") or "",
                response_time=raw.get("response_time", 0.0),
            )
            logger.info(
                "Tavily search %r: %d results (%.1fs, depth=%s)",
                query[:60], len(results), response.response_time, search_depth,
            )
            return response
        except Exception as e:
            logger.error("Tavily search failed for %r: %s", query[:60], e)
            raise

    def extract(self, urls: List[str]) -> List[ExtractedPage]:
        """Extract clean content from URLs via Tavily Extract API.

        Args:
            urls: List of URLs to extract content from (max 20).

        Returns:
            List of ExtractedPage with extracted text.
        """
        if not urls:
            return []
        _throttle()
        client = self._get_client()
        try:
            raw = client.extract(urls=urls[:20])
            pages = []
            for r in raw.get("results", []):
                pages.append(ExtractedPage(
                    url=r.get("url", ""),
                    raw_content=r.get("raw_content", ""),
                    success=True,
                ))
            for r in raw.get("failed_results", []):
                pages.append(ExtractedPage(
                    url=r.get("url", ""),
                    raw_content="",
                    success=False,
                    error=r.get("error", "extraction failed"),
                ))
            logger.info(
                "Tavily extract: %d/%d URLs succeeded",
                sum(1 for p in pages if p.success), len(urls),
            )
            return pages
        except Exception as e:
            logger.error("Tavily extract failed: %s", e)
            raise

    def format_results(self, response: TavilySearchResponse) -> str:
        """Format search results as text for the model."""
        if not response.results:
            return "No search results found."
        lines = [f"Search Results for: {response.query}\n"]
        if response.answer:
            lines.append(f"Summary: {response.answer}\n")
        for i, r in enumerate(response.results, 1):
            lines.append(f"{i}. {r.title}")
            # Truncate content to keep prompts manageable
            content = r.content[:500] if r.content else ""
            if content:
                lines.append(f"   {content}")
            lines.append(f"   Source: {r.url}\n")
        return "\n".join(lines)


# Module-level singleton
_instance: Optional[TavilySearch] = None
_instance_lock = threading.Lock()


def get_tavily_search() -> TavilySearch:
    """Get or create the TavilySearch singleton."""
    global _instance
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is not None:
            return _instance
        _instance = TavilySearch()
        return _instance


def _reset_for_testing() -> None:
    """Reset singleton for test isolation."""
    global _instance
    with _instance_lock:
        _instance = None
