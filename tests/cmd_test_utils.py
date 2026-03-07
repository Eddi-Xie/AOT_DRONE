from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time

import pytest


def require_tcp_bind_or_skip() -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        if os.environ.get("REQUIRE_TCP_BIND_TESTS", "").strip().lower() in {"1", "true", "yes"}:
            raise AssertionError(f"TCP socket bind required but unavailable: {exc}") from exc
        pytest.skip(f"TCP socket bind unavailable in test environment: {exc}")


def wait_until(predicate, timeout_s: float = 1.0, step_s: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return False


class FramedCmdCaptureServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.bound_port: int | None = None
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._messages: list[dict] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(5)
        listener.settimeout(0.1)
        self._listener = listener
        self.bound_port = int(listener.getsockname()[1])
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._serve, name="cmd-capture-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def get_messages(self) -> list[dict]:
        with self._lock:
            return list(self._messages)

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
            with conn:
                conn.settimeout(0.1)
                self._consume_connection(conn)

    def _consume_connection(self, conn: socket.socket) -> None:
        while not self._stop_event.is_set():
            try:
                header = _recv_exact(conn, 4, self._stop_event)
            except TimeoutError:
                continue
            except (EOFError, OSError):
                break

            payload_len = struct.unpack(">I", header)[0]
            if payload_len <= 0:
                break
            try:
                payload = _recv_exact(conn, payload_len, self._stop_event)
            except TimeoutError:
                continue
            except (EOFError, OSError):
                break

            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            with self._lock:
                self._messages.append(msg)


def _recv_exact(conn: socket.socket, size: int, stop_event: threading.Event) -> bytes:
    data = bytearray()
    while len(data) < size:
        if stop_event.is_set():
            raise EOFError
        try:
            chunk = conn.recv(size - len(data))
        except TimeoutError:
            if not data:
                raise
            continue
        if not chunk:
            raise EOFError
        data.extend(chunk)
    return bytes(data)
