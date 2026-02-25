from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from fastapi import WebSocket

WS_ENVELOPE_VERSION = 1


@dataclass
class _ClientQueue:
    websocket: WebSocket
    queue: deque[dict[str, Any]] = field(default_factory=deque)
    lock: Lock = field(default_factory=Lock)


class WsManager:
    def __init__(
        self,
        queue_max: int = 10,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if queue_max <= 0:
            raise ValueError("queue_max must be > 0")
        self.queue_max = queue_max
        self._clock = clock or time.monotonic
        self._lock = Lock()
        self._clients: dict[int, _ClientQueue] = {}
        self._next_client_id = 1
        self._next_event_seq = 1

    def register(self, websocket: WebSocket) -> int:
        with self._lock:
            client_id = self._next_client_id
            self._next_client_id += 1
            self._clients[client_id] = _ClientQueue(websocket=websocket)
            return client_id

    def unregister(self, client_id: int) -> None:
        with self._lock:
            self._clients.pop(client_id, None)

    def build_envelope(
        self,
        event: str,
        data: dict[str, Any],
        timestamp_s: float | None = None,
    ) -> dict[str, Any]:
        now_s = float(timestamp_s) if timestamp_s is not None else float(self._clock())
        with self._lock:
            seq = self._next_event_seq
            self._next_event_seq += 1
        return {
            "ws_ver": WS_ENVELOPE_VERSION,
            "event": event,
            "data": data,
            "timestamp_s": now_s,
            "seq": seq,
        }

    def broadcast(
        self,
        event: str,
        data: dict[str, Any],
        timestamp_s: float | None = None,
    ) -> dict[str, Any]:
        envelope = self.build_envelope(event=event, data=data, timestamp_s=timestamp_s)
        self.broadcast_envelope(envelope)
        return envelope

    def broadcast_envelope(self, envelope: dict[str, Any]) -> None:
        with self._lock:
            clients = list(self._clients.values())

        for client in clients:
            with client.lock:
                if len(client.queue) >= self.queue_max:
                    client.queue.popleft()
                client.queue.append(envelope)

    def pop_next(self, client_id: int) -> dict[str, Any] | None:
        with self._lock:
            client = self._clients.get(client_id)
        if client is None:
            return None

        with client.lock:
            if not client.queue:
                return None
            return client.queue.popleft()

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)
