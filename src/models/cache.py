"""
Query cache with TTL for model responses. Reduces API cost and latency for repeated prompts.
Gate B Phase 3 – cache Grok (and local) responses.
"""

import hashlib
import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class QueryCache:
    """Thread-safe cache for model responses with configurable TTL and hit/miss metrics."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        """
        Initialize query cache.

        Args:
            ttl_seconds: Time-to-live for cached entries in seconds (default 1 hour).
        """
        self._ttl_seconds = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        logger.info("Query cache initialized (TTL: %ds)", ttl_seconds)

    def get(self, prompt: str) -> Optional[Dict[str, Any]]:
        """
        Return cached response for the prompt if present and not expired.

        Returns:
            Cached response dict, or None on miss or expiry.
        """
        key = self._hash_prompt(prompt)
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                logger.debug("Cache miss for: %s...", prompt[:50] if len(prompt) > 50 else prompt)
                return None
            entry = self._cache[key]
            if time.time() - entry["cached_at"] >= self._ttl_seconds:
                del self._cache[key]
                self._misses += 1
                logger.debug("Cache expired for: %s...", prompt[:50] if len(prompt) > 50 else prompt)
                return None
            self._hits += 1
            logger.debug("Cache hit for: %s...", prompt[:50] if len(prompt) > 50 else prompt)
            return entry["response"]

    def set(self, prompt: str, response: Dict[str, Any]) -> None:
        """Store a response for the prompt."""
        key = self._hash_prompt(prompt)
        with self._lock:
            self._cache[key] = {
                "response": response,
                "cached_at": time.time(),
            }
        logger.debug("Cached response for: %s...", prompt[:50] if len(prompt) > 50 else prompt)

    def clear(self) -> None:
        """Remove all cached entries."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
        logger.info("Cache cleared (%d entries removed)", count)

    def get_stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100.0) if total > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "total_queries": total,
                "hit_rate_percent": hit_rate,
                "cached_entries": len(self._cache),
            }

    def _hash_prompt(self, prompt: str) -> str:
        """Return a stable hash key for the prompt."""
        return hashlib.md5(prompt.encode("utf-8")).hexdigest()
