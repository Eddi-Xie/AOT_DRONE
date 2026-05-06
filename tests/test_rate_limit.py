"""Per-IP token-bucket rate limit on /api/frame and /api/intent.

The bucket starts full so a 1-second burst is allowed before sustained
traffic is capped at the configured Hz. Tests use a small capacity so we
hit the 429 quickly without blowing test runtime.
"""

from __future__ import annotations

import base64
import time

from fastapi.testclient import TestClient

from src.backend.app import app
from src.backend.rate_limit import IpRateLimiter, TokenBucket
from tests.ws_test_utils import configure_backend_ws_test_env, require_udp_bind_or_skip

_VALID_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEm"
    "KzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7"
    "Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAABAAEDASIAAhEBAxEB/8QA"
    "HwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIh"
    "MUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVW"
    "V1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQF"
    "BgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAV"
    "YnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE"
    "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq"
    "8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD2aiiigD//2Q=="
)


def test_token_bucket_allows_burst_then_caps() -> None:
    bucket = TokenBucket(capacity=3.0, refill_per_s=10.0)
    # Freeze time at t=0; refill window stays empty for the burst.
    assert bucket.allow(now_s=0.0)
    assert bucket.allow(now_s=0.0)
    assert bucket.allow(now_s=0.0)
    assert not bucket.allow(now_s=0.0)
    # 0.2 s later => 2 tokens refilled (refill 10/s).
    assert bucket.allow(now_s=0.2)
    assert bucket.allow(now_s=0.2)
    assert not bucket.allow(now_s=0.2)


def test_ip_rate_limiter_buckets_per_ip() -> None:
    limiter = IpRateLimiter(capacity=1.0, refill_per_s=0.0)
    assert limiter.allow("10.0.0.1")
    assert not limiter.allow("10.0.0.1")
    # Different IP => its own fresh bucket.
    assert limiter.allow("10.0.0.2")


def test_intent_rate_limit_returns_429_after_burst(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_INTENT_RATE_LIMIT_HZ", "3")  # capacity = 3
    monkeypatch.delenv("BACKEND_API_TOKEN", raising=False)

    with TestClient(app) as client:
        # Burst the capacity within a single time slice; refill < 1 token
        # accumulates, so the 4th must 429.
        codes = [client.post("/api/intent", json={"desired_mode": 0}).status_code for _ in range(4)]
        assert codes[:3] == [200, 200, 200]
        assert codes[3] == 429


def test_frame_rate_limit_returns_429_after_burst(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_FRAME_RATE_LIMIT_HZ", "2")
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.delenv("BACKEND_API_TOKEN", raising=False)

    with TestClient(app) as client:
        # Same TestClient = same IP "testclient", so all 4 hits share one
        # bucket. We call them tight-loop so the refill barely advances.
        start = time.monotonic()
        codes = []
        for _ in range(4):
            response = client.post(
                "/api/frame",
                content=_VALID_JPEG,
                headers={"content-type": "image/jpeg"},
            )
            codes.append(response.status_code)
        elapsed = time.monotonic() - start
        # Refill 2 tokens/sec; with capacity=2 the 3rd request needs >=0.5 s
        # to refill a token. The whole loop should run faster than that.
        assert elapsed < 0.5, f"loop took {elapsed:.3f}s, refill would skew test"
        assert codes[:2] == [200, 200]
        assert 429 in codes[2:]


def test_rate_limit_disabled_when_hz_is_zero(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_INTENT_RATE_LIMIT_HZ", "0")
    monkeypatch.delenv("BACKEND_API_TOKEN", raising=False)

    with TestClient(app) as client:
        # 10 hits in a tight loop must all succeed when the limit is off.
        for _ in range(10):
            response = client.post("/api/intent", json={"desired_mode": 0})
            assert response.status_code == 200
