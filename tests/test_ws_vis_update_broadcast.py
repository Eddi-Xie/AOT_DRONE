from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.backend.app import app
from tests.ws_test_utils import (
    configure_backend_ws_test_env,
    make_valid_vis,
    require_udp_bind_or_skip,
)


def test_ws_receives_vis_update_after_vis_ingest(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch, ws_link_hz=1.0, ws_vis_hz=20.0)

    with TestClient(app) as client:
        vis_ingestor = client.app.state.vis_ingestor
        assert vis_ingestor is not None

        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            assert first["event"] == "LINK_STATUS"

            vis_payload = make_valid_vis(seq=42)
            raw = json.dumps(vis_payload, separators=(",", ":")).encode("utf-8")
            assert vis_ingestor.process_datagram(raw, rx_monotonic_s=123.0)

            event = ws.receive_json()

    assert event["event"] == "VIS_UPDATE"
    assert event["ws_ver"] == 1
    assert isinstance(event["timestamp_s"], float)
    assert isinstance(event["seq"], int)

    data = event["data"]
    assert data["type"] == "VIS"
    assert data["seq"] == 42
    assert isinstance(data["timestamp_s"], float)
    assert isinstance(data["tracking_state"], int)
    assert isinstance(data["loc_x"], float)
    assert isinstance(data["loc_y"], float)
    assert isinstance(data["bound_w"], float)
    assert isinstance(data["bound_h"], float)
    assert isinstance(data["confidence"], float)
    assert data["rx_monotonic_s"] == 123.0
    assert data["vis_age_s"] == 0.0
