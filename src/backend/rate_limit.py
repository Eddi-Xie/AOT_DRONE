"""Per-IP token-bucket rate limiter.

Homegrown rather than `slowapi` so we don't pull in another async-middleware
framework for one feature. Each (route, client-IP) pair gets a separate
bucket; the bucket starts full so a sudden burst up to `capacity` is allowed,
after which sustained traffic is capped at `refill_per_s` requests per second.

Threadsafe within a single process. Multi-worker deployments would need a
shared store (Redis, memcached); for Sprint 0 the backend runs as a single
uvicorn process per host so a per-process limiter is sufficient.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    __slots__ = ("capacity", "refill_per_s", "_tokens", "_last_refill_s", "_lock")

    def __init__(self, capacity: float, refill_per_s: float) -> None:
        self.capacity = float(capacity)
        self.refill_per_s = float(refill_per_s)
        self._tokens = float(capacity)
        self._last_refill_s = time.monotonic()
        self._lock = threading.Lock()

    def allow(self, now_s: float | None = None) -> bool:
        with self._lock:
            now = float(now_s) if now_s is not None else time.monotonic()
            elapsed = max(0.0, now - self._last_refill_s)
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_s)
            self._last_refill_s = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


class IpRateLimiter:
    """Per-client-IP token bucket.

    Buckets are created on first hit and never evicted. Memory cost per
    distinct IP is small (~200 B) but unbounded; a follow-up may add
    LRU eviction once we have a real adversary model. For local-LAN dev the
    risk is bounded.
    """

    def __init__(self, capacity: float, refill_per_s: float) -> None:
        self._capacity = float(capacity)
        self._refill_per_s = float(refill_per_s)
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        with self._lock:
            bucket = self._buckets.get(ip)
            if bucket is None:
                bucket = TokenBucket(self._capacity, self._refill_per_s)
                self._buckets[ip] = bucket
        return bucket.allow()
