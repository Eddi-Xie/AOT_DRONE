from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .cmd_schema import build_cmd_payload, frame_cmd_payload
from .state import SharedState

LOGGER = logging.getLogger(__name__)


@dataclass
class _LogBucket:
    seen: int = 0
    suppressed: int = 0
    last_log_monotonic_s: float = 0.0


class _BridgeReasonLogger:
    def __init__(self, min_interval_s: float, now_fn: Callable[[], float]) -> None:
        self._min_interval_s = min_interval_s
        self._now_fn = now_fn
        self._buckets: dict[str, _LogBucket] = {}

    def log(self, reason: str, detail: str) -> None:
        bucket = self._buckets.setdefault(reason, _LogBucket())
        bucket.seen += 1

        now_s = self._now_fn()
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
            LOGGER.warning("CMD bridge [%s]: %s%s", reason, detail, suffix)
            bucket.last_log_monotonic_s = now_s
        else:
            bucket.suppressed += 1


class CmdBridge:
    def __init__(
        self,
        state: SharedState,
        fc_host: str = "127.0.0.1",
        fc_port: int = 9002,
        tick_hz: float = 50.0,
        vis_fresh_s: float = 0.25,
        max_payload_bytes: int = 4096,
        connect_timeout_s: float = 1.0,
        connect_backoff_initial_s: float = 1.0,
        connect_backoff_max_s: float = 5.0,
        log_interval_s: float = 5.0,
        seq_start: int | None = None,
        clock: Callable[[], float] | None = None,
        wait_fn: Callable[[threading.Event, float], None] | None = None,
        on_connection_change: Callable[[bool], None] | None = None,
        on_tracking_blocked: Callable[[str], None] | None = None,
    ) -> None:
        if tick_hz <= 0:
            raise ValueError("tick_hz must be > 0")
        if vis_fresh_s < 0:
            raise ValueError("vis_fresh_s must be >= 0")
        if max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes must be > 0")
        if connect_timeout_s <= 0:
            raise ValueError("connect_timeout_s must be > 0")
        if connect_backoff_initial_s <= 0:
            raise ValueError("connect_backoff_initial_s must be > 0")
        if connect_backoff_max_s < connect_backoff_initial_s:
            raise ValueError("connect_backoff_max_s must be >= connect_backoff_initial_s")
        if log_interval_s < 0:
            raise ValueError("log_interval_s must be >= 0")

        self._state = state
        self.fc_host = fc_host
        self.fc_port = fc_port
        self.tick_hz = tick_hz
        self.vis_fresh_s = vis_fresh_s
        self.max_payload_bytes = max_payload_bytes
        self.connect_timeout_s = connect_timeout_s
        self.connect_backoff_initial_s = connect_backoff_initial_s
        self.connect_backoff_max_s = connect_backoff_max_s
        self.log_interval_s = log_interval_s

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock_lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._clock = clock or time.monotonic
        self._wait_fn = wait_fn or self._default_wait
        self._log_limiter = _BridgeReasonLogger(min_interval_s=log_interval_s, now_fn=self._clock)
        self._on_connection_change = on_connection_change
        self._on_tracking_blocked = on_tracking_blocked
        self._next_connect_attempt_s = 0.0
        self._connect_backoff_s = self.connect_backoff_initial_s
        self._last_fc_connected = False
        self._last_tracking_blocked_reason: str | None = None
        if seq_start is not None:
            self._state.ensure_cmd_seq_minimum(seq_start)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run_forever, name="cmd-bridge", daemon=True)
        self._thread.start()

    def stop(self, join_timeout_s: float = 1.0) -> None:
        self._stop_event.set()
        self._close_socket()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_s)
        self._set_fc_connected(False)

    def run_forever(self) -> None:
        period_s = 1.0 / self.tick_hz
        self._set_fc_connected(False)
        while not self._stop_event.is_set():
            loop_start_s = self._clock()
            if self._get_socket() is None:
                self._attempt_connect(loop_start_s)
            else:
                self._send_tick(loop_start_s)

            elapsed_s = self._clock() - loop_start_s
            remaining_s = max(0.0, period_s - elapsed_s)
            self._wait_fn(self._stop_event, remaining_s)

        self._close_socket()
        self._set_fc_connected(False)

    def _attempt_connect(self, now_s: float) -> None:
        if now_s < self._next_connect_attempt_s:
            return

        self._state.record_fc_connect_attempt(now_s)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.connect_timeout_s)
        try:
            sock.connect((self.fc_host, self.fc_port))
        except OSError as exc:
            sock.close()
            self._set_fc_connected(False)
            self._next_connect_attempt_s = now_s + self._connect_backoff_s
            self._connect_backoff_s = min(self._connect_backoff_s * 2.0, self.connect_backoff_max_s)
            self._log_limiter.log(
                "connect_fail",
                f"host={self.fc_host} port={self.fc_port} error={exc}",
            )
            return

        sock.settimeout(None)
        with self._sock_lock:
            self._sock = sock
        self._set_fc_connected(True)
        self._connect_backoff_s = self.connect_backoff_initial_s
        self._next_connect_attempt_s = now_s

    def _send_tick(self, now_s: float) -> None:
        intent = self._state.get_latest_intent()
        vis_snapshot = self._state.get_latest_vis()
        vis_age_s = self._state.get_vis_age_s(now_monotonic_s=now_s)

        seq = self._state.reserve_cmd_seq()
        payload, blocked_reason = build_cmd_payload(
            seq=seq,
            timestamp_s=now_s,
            intent=intent,
            vis_snapshot=vis_snapshot,
            vis_age_s=vis_age_s,
            vis_fresh_s=self.vis_fresh_s,
        )
        self._state.set_tracking_blocked_reason(blocked_reason)
        self._emit_tracking_blocked_callback(blocked_reason)
        self._state.record_cmd_tx_attempt()

        try:
            frame = frame_cmd_payload(payload, max_payload_bytes=self.max_payload_bytes)
        except ValueError:
            self._state.record_cmd_tx_fail()
            return
        self._state.record_last_cmd_payload(payload=payload, payload_bytes=max(0, len(frame) - 4))

        sock = self._get_socket()
        if sock is None:
            self._state.record_cmd_tx_fail()
            self._set_fc_connected(False)
            self._next_connect_attempt_s = now_s
            return

        try:
            sock.sendall(frame)
        except OSError as exc:
            self._state.record_cmd_tx_fail()
            self._set_fc_connected(False)
            self._close_socket()
            self._next_connect_attempt_s = now_s + self._connect_backoff_s
            self._connect_backoff_s = min(self._connect_backoff_s * 2.0, self.connect_backoff_max_s)
            self._log_limiter.log("send_fail", str(exc))
            return

        self._state.record_cmd_tx_ok(sent_monotonic_s=now_s)

    def _set_fc_connected(self, connected: bool) -> None:
        self._state.set_fc_connected(connected)
        if connected == self._last_fc_connected:
            return
        self._last_fc_connected = connected
        if self._on_connection_change is None:
            return
        try:
            self._on_connection_change(connected)
        except Exception:
            LOGGER.exception("CMD bridge connection callback failed")

    def _emit_tracking_blocked_callback(self, blocked_reason: str | None) -> None:
        if blocked_reason is None:
            self._last_tracking_blocked_reason = None
            return
        if blocked_reason == self._last_tracking_blocked_reason:
            return
        self._last_tracking_blocked_reason = blocked_reason
        if self._on_tracking_blocked is None:
            return
        try:
            self._on_tracking_blocked(blocked_reason)
        except Exception:
            LOGGER.exception("CMD bridge tracking-blocked callback failed")

    def _get_socket(self) -> socket.socket | None:
        with self._sock_lock:
            return self._sock

    def _close_socket(self) -> None:
        with self._sock_lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                finally:
                    self._sock = None

    @staticmethod
    def _default_wait(stop_event: threading.Event, timeout_s: float) -> None:
        stop_event.wait(timeout=timeout_s)
