"""
Query cache with TTL for model responses. Reduces API cost and latency for repeated prompts.
Cache Grok (and local) responses.
LRU eviction, optional disk persistence.
"""

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class QueryCache:
    """Thread-safe cache for model responses with TTL, LRU eviction, and optional disk persistence."""

    def __init__(
        self,
        ttl_seconds: int = 3600,
        max_size: int = 0,
        use_disk_cache: bool = False,
        disk_cache_dir: Optional[Path] = None,
    ) -> None:
        """
        Initialize query cache.

        Args:
            ttl_seconds: Time-to-live for cached entries in seconds (default 1 hour).
            max_size: Max in-memory entries; 0 = unbounded (no LRU eviction).
            use_disk_cache: Whether to persist to disk for survival across restarts.
            disk_cache_dir: Directory for disk cache; default data/cache/query_cache.
        """
        self._ttl_seconds = ttl_seconds
        self._max_size = max_size
        self._use_disk_cache = use_disk_cache
        self._disk_cache_dir = Path(disk_cache_dir) if disk_cache_dir else Path("data/cache/query_cache")
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._access_order: List[str] = []  # LRU tracking
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

        if self._use_disk_cache:
            self._disk_cache_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Query cache initialized (TTL: %ds, max_size: %s, disk: %s)",
            ttl_seconds,
            max_size if max_size > 0 else "unbounded",
            use_disk_cache,
        )

    def get(self, prompt: str) -> Optional[Dict[str, Any]]:
        """
        Return cached response for the prompt if present and not expired.

        Checks memory first, then disk (if enabled).

        Returns:
            Cached response dict, or None on miss or expiry.
        """
        key = self._hash_prompt(prompt)

        # Check memory first
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if time.time() - entry["cached_at"] >= self._ttl_seconds:
                    del self._cache[key]
                    self._mark_accessed(key, remove_only=True)
                    self._misses += 1
                    logger.debug(
                        "Cache expired for: %s...",
                        prompt[:50] if len(prompt) > 50 else prompt,
                    )
                else:
                    self._mark_accessed(key)
                    self._hits += 1
                    logger.debug(
                        "Cache hit for: %s...",
                        prompt[:50] if len(prompt) > 50 else prompt,
                    )
                    return entry["response"]

            self._misses += 1
            logger.debug(
                "Cache miss for: %s...",
                prompt[:50] if len(prompt) > 50 else prompt,
            )

        # Check disk if enabled
        if self._use_disk_cache:
            cached = self._get_from_disk(key)
            if cached is not None:
                self.set(prompt, cached)
                return cached

        return None

    def set(self, prompt: str, response: Dict[str, Any]) -> None:
        """Store a response for the prompt."""
        key = self._hash_prompt(prompt)
        with self._lock:
            # Evict oldest if at capacity
            if self._max_size > 0 and len(self._cache) >= self._max_size and key not in self._cache:
                while self._access_order and len(self._cache) >= self._max_size:
                    oldest_key = self._access_order.pop(0)
                    if oldest_key in self._cache:
                        del self._cache[oldest_key]
                        logger.debug("Evicted cache entry: %s", oldest_key[:8])
                        break

            self._cache[key] = {
                "response": response,
                "cached_at": time.time(),
            }
            self._mark_accessed(key)

        if self._use_disk_cache:
            self._save_to_disk(key, response)

        logger.debug(
            "Cached response for: %s...",
            prompt[:50] if len(prompt) > 50 else prompt,
        )

    def clear(self) -> None:
        """Remove all in-memory cached entries."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._access_order.clear()
        logger.info("Cache cleared (%d entries removed)", count)

    def clear_all(self) -> None:
        """Clear all entries and reset hit/miss stats."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._access_order.clear()
            self._hits = 0
            self._misses = 0
        logger.info("Cache cleared (%d entries removed, stats reset)", count)

    def get_stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100.0) if total > 0 else 0.0
            stats: Dict[str, Any] = {
                "hits": self._hits,
                "misses": self._misses,
                "total_queries": total,
                "hit_rate_percent": hit_rate,
                "cached_entries": len(self._cache),
            }
            if self._use_disk_cache and self._disk_cache_dir.exists():
                stats["disk_entries"] = len(list(self._disk_cache_dir.glob("*.json")))
            return stats

    def _mark_accessed(self, key: str, remove_only: bool = False) -> None:
        """Update LRU access order."""
        if key in self._access_order:
            self._access_order.remove(key)
        if not remove_only:
            self._access_order.append(key)

    def _hash_prompt(self, prompt: str) -> str:
        """Return a stable hash key for the prompt."""
        return hashlib.md5(prompt.encode("utf-8")).hexdigest()

    def _get_from_disk(self, key: str) -> Optional[Dict[str, Any]]:
        """Get response from disk cache."""
        cache_file = self._disk_cache_dir / f"{key}.json"
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)

            if time.time() - data["cached_at"] >= self._ttl_seconds:
                cache_file.unlink()
                return None

            logger.debug("Disk cache HIT: %s", key[:8])
            return data["response"]

        except Exception as e:
            logger.warning("Disk cache read error: %s", e)
            try:
                cache_file.unlink()
            except OSError:
                pass
        return None

    def _save_to_disk(self, key: str, response: Dict[str, Any]) -> None:
        """Save response to disk cache."""
        if not self._use_disk_cache:
            return

        cache_file = self._disk_cache_dir / f"{key}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"cached_at": time.time(), "response": response}, f)
        except Exception as e:
            logger.warning("Disk cache write error: %s", e)
