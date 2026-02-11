"""
Free web search tool for the local model. No API key required.
Uses DuckDuckGo (ddgs or duckduckgo-search package), with HTML fallback when needed.
"""

import logging
import urllib.parse
import urllib.request
import warnings
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _search_ddg_html(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Fallback: scrape DuckDuckGo HTML (no package deps beyond stdlib)."""
    try:
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({"q": query}).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # Minimal parse: look for result links and snippets (class result__a, result__snippet)
        import re
        results = []
        # Pattern: result block often has <a class="result__a" href="...">title</a> and snippet
        for block in re.split(r"<div class=\"result ", html):
            if len(results) >= max_results:
                break
            href_m = re.search(r'href="(https?://[^"]+)"', block)
            title_m = re.search(r'class="result__a"[^>]*>([^<]+)</a>', block)
            snippet_m = re.search(r'class="result__snippet"[^>]*>([^<]+)</', block)
            if href_m and title_m:
                url_str = urllib.parse.unquote(href_m.group(1).replace("&amp;", "&"))
                if url_str.startswith("https://duckduckgo.com"):
                    continue
                title = re.sub(r"\s+", " ", title_m.group(1).strip())
                snippet = re.sub(r"\s+", " ", snippet_m.group(1).strip()) if snippet_m else ""
                results.append({"title": title, "snippet": snippet, "url": url_str})
        return results
    except Exception as e:
        logger.debug("DuckDuckGo HTML fallback failed: %s", e)
        return []


class WebSearchTool:
    """Free web search using DuckDuckGo (no API key)."""

    def __init__(self) -> None:
        self._ddgs: Any = None

    def _get_ddgs(self) -> Any:
        if self._ddgs is None:
            try:
                from ddgs import DDGS
                self._ddgs = DDGS()
            except ImportError:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    try:
                        from duckduckgo_search import DDGS
                        self._ddgs = DDGS()
                    except ImportError as e:
                        raise ImportError(
                            "ddgs or duckduckgo-search required for local web search. pip install ddgs"
                        ) from e
        return self._ddgs

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """Search the web; use package first, then HTML fallback if 0 results."""
        results: List[Dict[str, str]] = []
        try:
            ddgs = self._get_ddgs()
            raw = ddgs.text(query, max_results=max_results)
            if raw is not None:
                raw_list = list(raw) if hasattr(raw, "__iter__") and not isinstance(raw, list) else raw
                for r in raw_list:
                    if isinstance(r, dict):
                        results.append({
                            "title": (r.get("title") or "").strip(),
                            "snippet": (r.get("body") or r.get("snippet") or "").strip(),
                            "url": (r.get("href") or r.get("url") or "").strip(),
                        })
        except Exception as e:
            logger.debug("DDGS search failed: %s", e)
        if not results:
            results = _search_ddg_html(query, max_results=max_results)
            if results:
                logger.info("Web search for %r: %d results (HTML fallback)", query[:50], len(results))
        else:
            logger.info("Web search for %r: %d results", query[:50], len(results))
        return results

    def format_results(self, results: List[Dict[str, str]]) -> str:
        """Format search results as text for the model."""
        if not results:
            return "No search results found."
        lines = ["Search Results:\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            url = r.get("url", "")
            lines.append(f"{i}. {title}\n   {snippet}\n   Source: {url}\n")
        return "\n".join(lines)
