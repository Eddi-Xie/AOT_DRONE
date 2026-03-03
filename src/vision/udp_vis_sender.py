from __future__ import annotations

import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass
class _LogBucket:
    last_log_s: float = 0.0
    suppressed: int = 0


class VisUdpSender:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9003,
        max_payload_bytes: int = 512,
        log_interval_s: float = 60.0,
        sock: socket.socket | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.max_payload_bytes = max(1, int(max_payload_bytes))
        self.log_interval_s = max(0.0, float(log_interval_s))
        self._sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._own_socket = sock is None

        self.sent_ok = 0
        self.sent_fail = 0
        self.dropped_oversize = 0

        self._log_buckets: dict[str, _LogBucket] = {}

    def send(self, vis_dict: dict[str, Any]) -> None:
        payload = self._encode_compact_json(vis_dict)
        if len(payload) > self.max_payload_bytes:
            self.dropped_oversize += 1
            self._log_rate_limited(
                "oversize",
                logging.WARNING,
                "Dropping oversize VIS payload: %d bytes (max=%d, dropped=%d)",
                len(payload),
                self.max_payload_bytes,
                self.dropped_oversize,
            )
            return

        try:
            self._sock.sendto(payload, (self.host, self.port))
            self.sent_ok += 1
        except OSError as exc:
            self.sent_fail += 1
            self._log_rate_limited(
                "send",
                logging.WARNING,
                "VIS UDP send failed to %s:%d: %s (failures=%d)",
                self.host,
                self.port,
                exc,
                self.sent_fail,
            )

    def close(self) -> None:
        if self._own_socket:
            self._sock.close()

    @staticmethod
    def _encode_compact_json(payload: dict[str, Any]) -> bytes:
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

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
