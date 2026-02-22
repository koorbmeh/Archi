"""
Web content helpers — SSL context, URL fetching, SSRF guard.

Extracted from plan_executor.py (session 73) for SRP compliance.
"""

import logging
import re
import ssl
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# Build a reusable SSL context from certifi's CA bundle.
# This fixes CERTIFICATE_VERIFY_FAILED on Windows (e.g. arxiv.org).
try:
    import certifi
    _ssl_context = ssl.create_default_context(cafile=certifi.where())
    _ssl_source = f"certifi ({certifi.where()})"
except ImportError:
    _ssl_context = ssl.create_default_context()
    _ssl_source = "system default (certifi not installed)"

# Reusable opener with HTTPS handler — gives HTTP keep-alive (connection
# pooling) across sequential _fetch_url_text() calls to the same host.
_url_opener = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_ssl_context),
)

logger.debug("SSL context: %s", _ssl_source)


def _is_private_url(url: str) -> bool:
    """SSRF guard — delegates to shared utility in src/utils/net_safety.py."""
    from src.utils.net_safety import is_private_url
    return is_private_url(url)


def _fetch_url_text(url: str, max_chars: int = 5000) -> str:
    """Fetch a URL and extract readable text from the HTML.

    Strips scripts, styles, and HTML tags. Returns plain text limited
    to max_chars. This gives Archi the ability to actually read web
    pages — not just search snippets.
    """
    if _is_private_url(url):
        return f"Blocked: {url} resolves to a private/internal address"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,*/*",
            },
        )
        with _url_opener.open(req, timeout=15) as resp:
            raw = resp.read()
        # Try to decode
        html = raw.decode("utf-8", errors="replace")
        # Strip <script> and <style> blocks
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Strip HTML tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Decode common HTML entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        return f"Error fetching {url}: {e}"
