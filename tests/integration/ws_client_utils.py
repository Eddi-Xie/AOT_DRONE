from __future__ import annotations

import queue
import threading
import time
from typing import Any


def receive_json_with_timeout(ws: Any, timeout_s: float) -> dict[str, Any]:
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def _receive_once() -> None:
        try:
            result_queue.put((True, ws.receive_json()))
        except Exception as exc:  # pragma: no cover - defensive.
            result_queue.put((False, exc))

    threading.Thread(target=_receive_once, daemon=True).start()
    try:
        ok, payload = result_queue.get(timeout=timeout_s)
    except queue.Empty as exc:
        raise AssertionError(f"websocket receive timed out after {timeout_s:.2f}s") from exc

    if not ok:
        raise payload
    if not isinstance(payload, dict):
        raise AssertionError(f"unexpected websocket payload type: {type(payload).__name__}")
    return payload


def wait_for_ws_event(
    ws: Any,
    event: str,
    *,
    timeout_s: float = 2.0,
    max_messages: int = 100,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    seen = 0
    while seen < max_messages:
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0:
            break
        message = receive_json_with_timeout(ws, timeout_s=min(0.5, remaining_s))
        seen += 1
        if message.get("event") == event:
            return message
    raise AssertionError(
        f"did not receive websocket event={event!r} within timeout={timeout_s:.2f}s "
        f"and max_messages={max_messages}"
    )
