from __future__ import annotations

import os
import socket
from typing import Any

import pytest
from starlette.testclient import WebSocketTestSession


def require_udp_bind_or_skip() -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        if os.environ.get("REQUIRE_UDP_BIND_TESTS", "").strip().lower() in {"1", "true", "yes"}:
            raise AssertionError(f"UDP socket bind required but unavailable: {exc}") from exc
        pytest.skip(f"UDP socket bind unavailable in test environment: {exc}")


def configure_backend_ws_test_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ws_link_hz: float = 2.0,
    ws_tel_hz: float = 20.0,
    ws_vis_hz: float = 20.0,
) -> None:
    monkeypatch.setenv("BACKEND_TEL_PORT", "0")
    monkeypatch.setenv("BACKEND_VIS_PORT", "0")
    monkeypatch.setenv("BACKEND_CMD_BRIDGE_ENABLED", "0")
    monkeypatch.setenv("BACKEND_WS_LINK_HZ", str(ws_link_hz))
    monkeypatch.setenv("BACKEND_WS_TEL_HZ", str(ws_tel_hz))
    monkeypatch.setenv("BACKEND_WS_VIS_HZ", str(ws_vis_hz))
    monkeypatch.setenv("BACKEND_WS_CLIENT_QUEUE_MAX", "10")
    monkeypatch.setenv("BACKEND_TEL_LOG_INTERVAL_S", "0.0")
    monkeypatch.setenv("BACKEND_VIS_LOG_INTERVAL_S", "0.0")
    monkeypatch.setenv("BACKEND_WS_WARNING_INTERVAL_S", "0.1")


def make_valid_vis(seq: int = 1) -> dict[str, Any]:
    return {
        "type": "VIS",
        "seq": seq,
        "timestamp_s": 10.0 + seq,
        "tracking_state": 3,
        "loc_x": 0.2,
        "loc_y": -0.1,
        "bound_w": 0.3,
        "bound_h": 0.4,
        "confidence": 0.8,
    }


def make_valid_tel(seq: int = 1) -> dict[str, Any]:
    return {
        "type": "TEL",
        "seq": seq,
        "timestamp_s": 10.0 + seq,
        "control_mode": 1,
        "tracking_state": 3,
        "distFront_m": 0.0,
        "distBack_m": 0.0,
        "distBottom_m": 1.1,
        "target_x": 0.1,
        "target_y": -0.05,
        "bound_w": 0.2,
        "bound_h": 0.3,
        "confidence": 0.9,
    }


def recv_until_event(
    ws: WebSocketTestSession,
    event: str,
    *,
    max_messages: int = 30,
) -> dict[str, Any]:
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("event") == event:
            return msg
    raise AssertionError(f"did not receive event={event!r} within {max_messages} messages")
