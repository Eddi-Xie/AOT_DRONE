from __future__ import annotations

from fastapi.testclient import TestClient

from src.backend.app import app
from tests.ws_test_utils import configure_backend_ws_test_env, require_udp_bind_or_skip


def test_ws_connect_sends_initial_link_status(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch, ws_link_hz=4.0)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()

    assert first["ws_ver"] == 1
    assert first["event"] == "LINK_STATUS"
    assert isinstance(first["timestamp_s"], float)
    assert isinstance(first["seq"], int)

    data = first["data"]
    required_keys = {
        "fc_connected",
        "fc_last_connect_attempt_s",
        "cmd_tx_total",
        "cmd_tx_ok",
        "cmd_tx_fail",
        "cmd_last_sent_monotonic_s",
        "cmd_hz_est",
        "tracking_blocked_reason",
        "vis_age_s",
        "vis_rx_ok",
        "vis_rx_bad",
        "vis_drop_reason_oversize",
        "vis_drop_reason_json",
        "vis_drop_reason_schema",
        "vis_drop_reason_range",
        "vis_drop_reason_semantics",
        "tel_age_s",
        "tel_rx_ok",
        "tel_rx_bad",
        "video_enabled",
        "video_clients",
        "video_fps_est",
        "last_frame_age_s",
        "frames_rx_ok",
        "frames_rx_bad",
        "vis_fresh_s",
        "tel_fresh_s",
        "cmd_timeout_s",
        "cmd_hz",
        "tel_hz",
    }
    assert required_keys.issubset(set(data.keys()))

    assert data["fc_connected"] is False
    assert data["fc_last_connect_attempt_s"] is None
    assert data["cmd_tx_total"] == 0
    assert data["cmd_tx_ok"] == 0
    assert data["cmd_tx_fail"] == 0
    assert data["cmd_last_sent_monotonic_s"] is None
    assert isinstance(data["cmd_hz_est"], float)
    assert data["tracking_blocked_reason"] is None

    assert data["vis_age_s"] is None
    assert data["vis_rx_ok"] == 0
    assert data["vis_rx_bad"] == 0
    assert data["vis_drop_reason_oversize"] == 0
    assert data["vis_drop_reason_json"] == 0
    assert data["vis_drop_reason_schema"] == 0
    assert data["vis_drop_reason_range"] == 0
    assert data["vis_drop_reason_semantics"] == 0

    assert data["tel_age_s"] is None
    assert data["tel_rx_ok"] == 0
    assert data["tel_rx_bad"] == 0

    assert isinstance(data["video_enabled"], bool)
    assert isinstance(data["video_clients"], int)
    assert isinstance(data["video_fps_est"], float)
    assert data["last_frame_age_s"] is None
    assert data["frames_rx_ok"] == 0
    assert data["frames_rx_bad"] == 0

    assert isinstance(data["vis_fresh_s"], float)
    assert isinstance(data["tel_fresh_s"], float)
    assert isinstance(data["cmd_timeout_s"], float)
    assert isinstance(data["cmd_hz"], float)
    assert isinstance(data["tel_hz"], float)


def test_ws_link_status_contains_video_fields_when_video_disabled(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch, ws_link_hz=4.0)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "0")

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()

    assert first["event"] == "LINK_STATUS"
    data = first["data"]
    assert data["video_enabled"] is False
    assert isinstance(data["video_clients"], int)
    assert isinstance(data["video_fps_est"], float)
    assert "last_frame_age_s" in data
    assert isinstance(data["frames_rx_ok"], int)
    assert isinstance(data["frames_rx_bad"], int)
