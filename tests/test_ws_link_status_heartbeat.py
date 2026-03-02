from __future__ import annotations

from fastapi.testclient import TestClient

from src.backend.app import app
from tests.ws_test_utils import (
    configure_backend_ws_test_env,
    recv_until_event,
    require_udp_bind_or_skip,
)


def test_ws_link_status_heartbeat_emits_multiple_events(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch, ws_link_hz=10.0)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            assert first["event"] == "LINK_STATUS"

            second = recv_until_event(ws, "LINK_STATUS", max_messages=20)

    assert isinstance(first["seq"], int)
    assert isinstance(second["seq"], int)
    assert second["seq"] > first["seq"]
    assert first["ws_ver"] == 1
    assert second["ws_ver"] == 1

    data = second["data"]
    assert "fc_connected" in data
    assert "cmd_tx_total" in data
    assert "cmd_tx_ok" in data
    assert "cmd_tx_fail" in data
    assert "vis_age_s" in data
    assert "vis_rx_ok" in data
    assert "vis_rx_bad" in data
    assert "tel_age_s" in data
    assert "tel_rx_ok" in data
    assert "tel_rx_bad" in data
    assert "video_enabled" in data
    assert "video_clients" in data
    assert "video_fps_est" in data
    assert "last_frame_age_s" in data
    assert "frames_rx_ok" in data
    assert "frames_rx_bad" in data
