from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


def wait_for_status(
    client: Any,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout_s: float = 2.0,
    step_s: float = 0.02,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get("/api/status")
        assert response.status_code == 200
        payload = response.json()
        assert isinstance(payload, dict)
        last_payload = payload
        if predicate(payload):
            return payload
        time.sleep(step_s)
    raise AssertionError(f"status condition not met within {timeout_s:.2f}s; last={last_payload}")
