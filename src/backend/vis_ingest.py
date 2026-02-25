from __future__ import annotations

import json
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .state import SharedState
from .vis_schema import VisValidationError, validate_vis_message

LOGGER = logging.getLogger(__name__)


@dataclass
class _DropLogBucket:
    seen: int = 0
    suppressed: int = 0
    last_log_monotonic_s: float = 0.0


class _DropReasonLogger:
    def __init__(self, min_interval_s: float = 5.0) -> None:
        self._min_interval_s = min_interval_s
        self._buckets: dict[str, _DropLogBucket] = {}

    def log(self, reason: str, detail: str) -> None:
        bucket = self._buckets.setdefault(reason, _DropLogBucket())
        bucket.seen += 1

        now_s = time.monotonic()
        should_log = (
            bucket.seen == 1
            or self._min_interval_s <= 0.0
            or (now_s - bucket.last_log_monotonic_s) >= self._min_interval_s
        )
        if should_log:
            suffix = ""
            if bucket.suppressed:
                suffix = f" (suppressed={bucket.suppressed})"
                bucket.suppressed = 0
            LOGGER.warning("Dropped VIS packet [%s]: %s%s", reason, detail, suffix)
            bucket.last_log_monotonic_s = now_s
        else:
            bucket.suppressed += 1


class VisUdpIngestor:
    def __init__(
        self,
        state: SharedState,
        bind_host: str = "127.0.0.1",
        port: int = 9003,
        max_bytes: int = 512,
        recv_timeout_s: float = 0.1,
        log_interval_s: float = 5.0,
        on_valid: Callable[[dict[str, Any], float], None] | None = None,
        on_drop: Callable[[str, str], None] | None = None,
    ) -> None:
        self.state = state
        self.bind_host = bind_host
        self.port = port
        self.max_bytes = max_bytes
        self.recv_timeout_s = recv_timeout_s
        self.on_valid = on_valid
        self.on_drop = on_drop
        self._sock: socket.socket | None = None
        self._sock_lock = threading.Lock()
        self._drop_logger = _DropReasonLogger(min_interval_s=log_interval_s)
        self.bound_port: int | None = None

    def serve_forever(self, stop_event: threading.Event | None = None) -> None:
        local_stop_event = stop_event or threading.Event()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.bind_host, self.port))
        sock.settimeout(self.recv_timeout_s)

        with self._sock_lock:
            self._sock = sock
            self.bound_port = int(sock.getsockname()[1])

        try:
            while not local_stop_event.is_set():
                try:
                    data, _ = sock.recvfrom(self.max_bytes + 1)
                except TimeoutError:
                    continue
                except OSError:
                    if local_stop_event.is_set():
                        break
                    continue
                self.process_datagram(data=data, rx_monotonic_s=time.monotonic())
        finally:
            with self._sock_lock:
                if self._sock is not None:
                    self._sock.close()
                self._sock = None

    def close(self) -> None:
        with self._sock_lock:
            if self._sock is not None:
                self._sock.close()
                self._sock = None

    def process_datagram(self, data: bytes, rx_monotonic_s: float | None = None) -> bool:
        self.state.record_vis_rx_total()

        if len(data) > self.max_bytes:
            detail = f"datagram length={len(data)} exceeds max_bytes={self.max_bytes}"
            self.state.record_vis_drop("oversize")
            self._drop_logger.log("oversize", detail)
            if self.on_drop is not None:
                self.on_drop("oversize", detail)
            return False

        try:
            decoded = data.decode("utf-8")
            payload = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            detail = str(exc)
            self.state.record_vis_drop("json")
            self._drop_logger.log("json", detail)
            if self.on_drop is not None:
                self.on_drop("json", detail)
            return False

        try:
            validated = validate_vis_message(payload)
        except VisValidationError as exc:
            self.state.record_vis_drop(exc.reason)
            self._drop_logger.log(exc.reason, exc.detail)
            if self.on_drop is not None:
                self.on_drop(exc.reason, exc.detail)
            return False

        rx_s = rx_monotonic_s if rx_monotonic_s is not None else time.monotonic()
        self.state.record_vis_ok(validated, rx_monotonic_s=rx_s)
        if self.on_valid is not None:
            self.on_valid(validated, rx_s)
        return True
