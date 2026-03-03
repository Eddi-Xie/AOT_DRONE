from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .jpeg import clamp_quality, encode_jpeg

LOGGER = logging.getLogger(__name__)


class _HttpClientLike(Protocol):
    def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> object: ...

    def close(self) -> None: ...


@dataclass
class _LogBucket:
    last_log_s: float = 0.0
    suppressed: int = 0


class FramePusher:
    def __init__(
        self,
        backend_http: str = "http://127.0.0.1:8000",
        frame_endpoint: str = "/api/frame",
        max_jpeg_bytes: int = 200_000,
        timeout_s: float = 1.0,
        retry_quality_step: int = 20,
        log_interval_s: float = 60.0,
        http_client: _HttpClientLike | None = None,
        jpeg_encoder: Callable[[np.ndarray, int], bytes] | None = None,
    ) -> None:
        self.url = self._join_url(backend_http, frame_endpoint)
        self.max_jpeg_bytes = max(1, int(max_jpeg_bytes))
        self.retry_quality_step = max(1, int(retry_quality_step))
        self.log_interval_s = max(0.0, float(log_interval_s))

        self._jpeg_encoder = jpeg_encoder or encode_jpeg
        self._client = http_client or self._build_default_http_client(timeout_s=timeout_s)
        self._own_client = http_client is None

        self.push_ok = 0
        self.push_fail = 0
        self.dropped_oversize = 0

        self._log_buckets: dict[str, _LogBucket] = {}

    def push(self, jpeg_bytes: bytes) -> bool:
        if len(jpeg_bytes) > self.max_jpeg_bytes:
            self.dropped_oversize += 1
            self._log_rate_limited(
                "oversize",
                logging.WARNING,
                "Dropping oversize JPEG frame: %d bytes (max=%d, dropped=%d)",
                len(jpeg_bytes),
                self.max_jpeg_bytes,
                self.dropped_oversize,
            )
            return False

        try:
            response = self._client.post(
                self.url,
                content=jpeg_bytes,
                headers={"Content-Type": "image/jpeg"},
            )
        except Exception as exc:
            self.push_fail += 1
            self._log_rate_limited(
                "push_exception",
                logging.WARNING,
                "Frame push failed for %s: %s (failures=%d)",
                self.url,
                exc,
                self.push_fail,
            )
            return False

        status_code = int(getattr(response, "status_code", 0))
        if 200 <= status_code < 300:
            self.push_ok += 1
            return True

        self.push_fail += 1
        self._log_rate_limited(
            "push_non_2xx",
            logging.WARNING,
            "Frame push rejected by backend: status=%d url=%s (failures=%d)",
            status_code,
            self.url,
            self.push_fail,
        )
        return False

    def push_frame(self, frame: np.ndarray, quality: int) -> bool:
        quality_primary = clamp_quality(quality)
        try:
            encoded = self._jpeg_encoder(frame, quality_primary)
        except Exception as exc:
            self.push_fail += 1
            self._log_rate_limited(
                "encode_primary",
                logging.WARNING,
                "JPEG encode failed at quality=%d: %s (failures=%d)",
                quality_primary,
                exc,
                self.push_fail,
            )
            return False

        if len(encoded) <= self.max_jpeg_bytes:
            return self.push(encoded)

        retry_quality = clamp_quality(quality_primary - self.retry_quality_step)
        if retry_quality == quality_primary:
            return self.push(encoded)

        try:
            encoded_retry = self._jpeg_encoder(frame, retry_quality)
        except Exception as exc:
            self.push_fail += 1
            self._log_rate_limited(
                "encode_retry",
                logging.WARNING,
                "JPEG retry encode failed at quality=%d: %s (failures=%d)",
                retry_quality,
                exc,
                self.push_fail,
            )
            return False

        return self.push(encoded_retry)

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    @staticmethod
    def _join_url(base: str, endpoint: str) -> str:
        base_clean = base.strip().rstrip("/")
        endpoint_clean = endpoint.strip()
        if not endpoint_clean:
            endpoint_clean = "/api/frame"
        if not endpoint_clean.startswith("/"):
            endpoint_clean = f"/{endpoint_clean}"
        return f"{base_clean}{endpoint_clean}"

    @staticmethod
    def _build_default_http_client(timeout_s: float) -> _HttpClientLike:
        import httpx

        return httpx.Client(timeout=max(0.1, float(timeout_s)))

    def _log_rate_limited(self, key: str, level: int, message: str, *args: object) -> None:
        bucket = self._log_buckets.setdefault(key, _LogBucket())
        now_s = time.monotonic()
        should_log = (
            bucket.last_log_s == 0.0
            or self.log_interval_s <= 0.0
            or (now_s - bucket.last_log_s) >= self.log_interval_s
        )
        if should_log:
            suffix = ""
            if bucket.suppressed:
                suffix = f" (suppressed={bucket.suppressed})"
                bucket.suppressed = 0
            LOGGER.log(level, message + suffix, *args)
            bucket.last_log_s = now_s
        else:
            bucket.suppressed += 1
