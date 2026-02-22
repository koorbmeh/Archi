"""
Network safety utilities — SSRF guards for URL fetching.

Extracted from plan_executor.py (session 71) so any module that fetches
arbitrary URLs can reuse the same guard.
"""

import ipaddress
import logging
import socket
import urllib.parse

logger = logging.getLogger(__name__)


def is_private_url(url: str) -> bool:
    """Return True if the URL targets a private/internal/loopback address (SSRF guard).

    Resolves the hostname via DNS and checks whether the resulting IP is
    private, loopback, link-local, or reserved.  Blocks ``localhost`` and
    empty hostnames outright.

    On DNS failure the URL is *allowed* — the fetch itself will fail
    naturally and produce a more useful error message.
    """
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        if host in ("localhost", ""):
            return True
        # Try parsing as IP directly (covers 127.x.x.x, 10.x.x.x, etc.)
        try:
            addr = ipaddress.ip_address(host)
            return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
        except ValueError:
            pass
        # DNS-resolve hostname and check the resulting IP
        resolved = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _, _, _, _, sockaddr in resolved:
            addr = ipaddress.ip_address(sockaddr[0])
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return True
    except Exception:
        pass  # DNS failure etc. — let the fetch itself fail naturally
    return False
