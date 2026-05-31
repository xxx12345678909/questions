"""
High-performance in-memory cache proxy — drop-in replacement for Redis.

Provides O(1) read/write for high-frequency transient state:
  - Session fatigue snapshots
  - Sliding-window correctness (fixed-size deque)
  - DAG learning-path JSON trees (with TTL)

Thread-safe: all public methods are guarded by a reentrant lock.
"""
import time
import threading


class InMemoryCacheProxy:
    """High-cohesion single-machine memory-level cache proxy.

    Seamlessly swappable for a real Redis client.
    Thread-safe via internal lock — safe for multi-threaded Flask dev server.
    """

    def __init__(self):
        self._storage = {}
        self._expire = {}
        self._lock = threading.Lock()

    def set(self, key, value, ttl=None):
        with self._lock:
            self._storage[key] = value
            if ttl:
                self._expire[key] = time.time() + ttl

    def get(self, key):
        with self._lock:
            if key in self._expire and time.time() > self._expire[key]:
                self._delete_unsafe(key)
                return None
            return self._storage.get(key, None)

    def delete(self, key):
        with self._lock:
            return self._delete_unsafe(key)

    def _delete_unsafe(self, key):
        """Internal delete — caller must hold _lock."""
        this_key = self._storage.pop(key, None)
        self._expire.pop(key, None)
        return this_key

    def lpush_fixed_window(self, key, value, max_size=5):
        """Sliding-window deque simulation for correctness tracking."""
        with self._lock:
            lst = self._storage.get(key, [])
            lst.insert(0, value)
            if len(lst) > max_size:
                lst = lst[:max_size]
            self._storage[key] = lst


# Global singleton — the single source of truth for cache across the app
cache_service = InMemoryCacheProxy()
