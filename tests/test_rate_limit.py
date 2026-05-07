"""Per-IP token-bucket rate limit on /api/frame and /api/intent.

The bucket starts full so a 1-second burst is allowed before sustained
traffic is capped at the configured Hz. Tests use a small capacity so we
hit the 429 quickly without blowing test runtime.
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

import src.backend.rate_limit as rate_limit_module
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


def _freeze_rate_limit_clock(monkeypatch) -> list[float]:
    """Pin the rate-limit module's clock so refill never advances mid-test.

    Returns the single-element list holding the fake "now"; tests can mutate
    its value to advance time deterministically. Avoids the previous wall-clock
    elapsed-bound check, which could spuriously fail on a slow CI runner.
    """
    fake_now = [1000.0]
    monkeypatch.setattr(rate_limit_module.time, "monotonic", lambda: fake_now[0])
    return fake_now


def test_intent_rate_limit_returns_429_after_burst(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_INTENT_RATE_LIMIT_HZ", "3")  # capacity = 3
    monkeypatch.delenv("BACKEND_API_TOKEN", raising=False)
    _freeze_rate_limit_clock(monkeypatch)

    with TestClient(app) as client:
        # Time is frozen, so refill stays at zero; the 4th request must 429
        # regardless of how long the actual TestClient call takes.
        codes = [client.post("/api/intent", json={"desired_mode": 0}).status_code for _ in range(4)]
        assert codes[:3] == [200, 200, 200]
        assert codes[3] == 429


def test_frame_rate_limit_returns_429_after_burst(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_FRAME_RATE_LIMIT_HZ", "2")
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.delenv("BACKEND_API_TOKEN", raising=False)
    _freeze_rate_limit_clock(monkeypatch)

    with TestClient(app) as client:
        # Time frozen => refill never advances => 3rd and 4th must 429.
        codes = []
        for _ in range(4):
            response = client.post(
                "/api/frame",
                content=_VALID_JPEG,
                headers={"content-type": "image/jpeg"},
            )
            codes.append(response.status_code)
        assert codes[:2] == [200, 200]
        assert codes[2] == 429
        assert codes[3] == 429


def test_frame_rate_limit_refills_after_time_passes(monkeypatch) -> None:
    # Companion test: with the clock advanced past the refill interval,
    # the 3rd request gets a fresh token. Pins the refill semantics
    # without relying on wall-clock sleep.
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_FRAME_RATE_LIMIT_HZ", "2")
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.delenv("BACKEND_API_TOKEN", raising=False)
    fake_now = _freeze_rate_limit_clock(monkeypatch)

    with TestClient(app) as client:

        def post_frame() -> int:
            return client.post(
                "/api/frame",
                content=_VALID_JPEG,
                headers={"content-type": "image/jpeg"},
            ).status_code

        assert post_frame() == 200
        assert post_frame() == 200
        assert post_frame() == 429
        # Advance 0.6 s @ 2 Hz refill => 1.2 tokens accrued.
        fake_now[0] += 0.6
        assert post_frame() == 200


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
