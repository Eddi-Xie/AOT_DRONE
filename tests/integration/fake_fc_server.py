from __future__ import annotations

import json
import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest


def require_tcp_bind_or_skip() -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"SKIP_NET: TCP socket bind unavailable in test environment: {exc}")


class FakeFcServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        max_payload_bytes: int = 4096,
    ) -> None:
        self.host = host
        self.port = port
        self.max_payload_bytes = max_payload_bytes

        self.bound_port: int | None = None
        self.connect_event = threading.Event()

        self._listener: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._lock = threading.Lock()
        self._received_cmds: list[dict[str, Any]] = []
        self.framing_errors: list[str] = []
        self.json_errors: list[str] = []

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(4)
        listener.settimeout(0.1)

        self._listener = listener
        self.bound_port = int(listener.getsockname()[1])
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._serve, name="fake-fc-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._listener is not None:
            try:
                self._listener.close()
            finally:
                self._listener = None
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    @property
    def connected(self) -> bool:
        return self.connect_event.is_set()

    @property
    def listening_port(self) -> int:
        if self.bound_port is None:
            raise RuntimeError("server has not started")
        return self.bound_port

    def received_cmds(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._received_cmds)

    def last_cmd(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._received_cmds:
                return None
            return dict(self._received_cmds[-1])

    def wait_for_connection(self, timeout_s: float = 2.0) -> bool:
        return self.connect_event.wait(timeout_s)

    def wait_for_commands(self, minimum_count: int, timeout_s: float = 2.0) -> bool:
        return _wait_until(lambda: len(self.received_cmds()) >= minimum_count, timeout_s=timeout_s)

    def wait_for_command(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        timeout_s: float = 2.0,
        step_s: float = 0.01,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            snapshot = self.received_cmds()
            for payload in reversed(snapshot):
                if predicate(payload):
                    return payload
            time.sleep(step_s)
        return None

    def _serve(self) -> None:
        listener = self._listener
        if listener is None:
            return

        while not self._stop_event.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break

            self.connect_event.set()
            self._conn = conn
            try:
                conn.settimeout(0.1)
                self._consume_connection(conn)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
                self._conn = None

    def _consume_connection(self, conn: socket.socket) -> None:
        while not self._stop_event.is_set():
            try:
                header = _recv_exact(conn, 4, self._stop_event)
            except TimeoutError:
                continue
            except (OSError, EOFError):
                return

            payload_len = struct.unpack(">I", header)[0]
            if payload_len <= 0 or payload_len > self.max_payload_bytes:
                self.framing_errors.append(
                    f"invalid CMD frame length={payload_len} max={self.max_payload_bytes}"
                )
                return

            try:
                payload_bytes = _recv_exact(conn, payload_len, self._stop_event)
            except TimeoutError:
                continue
            except (OSError, EOFError):
                return
            if not (1 <= len(payload_bytes) <= self.max_payload_bytes):
                self.framing_errors.append(
                    "invalid CMD payload length after read: "
                    f"len={len(payload_bytes)} max={self.max_payload_bytes}"
                )
                return

            try:
                payload = json.loads(payload_bytes.decode("utf-8"))
            except Exception as exc:  # pragma: no cover - defensive.
                self.json_errors.append(str(exc))
                continue

            if not isinstance(payload, dict):
                self.json_errors.append(f"payload is not a JSON object: {type(payload).__name__}")
                continue

            with self._lock:
                self._received_cmds.append(payload)


def _wait_until(predicate: Callable[[], bool], timeout_s: float, step_s: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return False


def _recv_exact(conn: socket.socket, size: int, stop_event: threading.Event) -> bytes:
    data = bytearray()
    while len(data) < size:
        if stop_event.is_set():
            raise EOFError("server stop requested")
        try:
            chunk = conn.recv(size - len(data))
        except TimeoutError:
            if not data:
                raise
            continue
        if not chunk:
            raise EOFError("socket closed")
        data.extend(chunk)
    return bytes(data)
