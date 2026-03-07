from __future__ import annotations

import time

from fastapi.testclient import TestClient

from src.backend.app import app
from tests.integration.e2e_test_utils import (
    configure_backend_e2e_env,
    encode_datagram,
    make_vis_ingestor_for_client,
    make_vis_payload,
)
from tests.integration.status_poll_utils import wait_for_status
from tests.integration.ws_client_utils import wait_for_ws_event


def test_e2e_vis_to_ws_and_status(monkeypatch) -> None:
    configure_backend_e2e_env(monkeypatch, cmd_bridge_enabled=False)

    with TestClient(app) as client:
        vis_ingestor = make_vis_ingestor_for_client(client)
        with client.websocket_connect("/ws") as ws:
            wait_for_ws_event(ws, "LINK_STATUS", timeout_s=1.5)

            vis_payload = make_vis_payload(seq=1001)
            assert vis_ingestor.process_datagram(
                encode_datagram(vis_payload),
                rx_monotonic_s=time.monotonic(),
            )

            vis_event = wait_for_ws_event(ws, "VIS_UPDATE", timeout_s=2.0)
            link_event = wait_for_ws_event(ws, "LINK_STATUS", timeout_s=2.0)

        status = wait_for_status(
            client,
            lambda payload: int(payload.get("vis_rx_ok", 0)) >= 1,
            timeout_s=2.0,
        )

    assert vis_event["event"] == "VIS_UPDATE"
    assert vis_event["data"]["type"] == "VIS"
    assert vis_event["data"]["seq"] == 1001
    assert "vis_age_s" in vis_event["data"]

    link_data = link_event["data"]
    assert "vis_age_s" in link_data
    assert "vis_drop_reason_oversize" in link_data
    assert "vis_drop_reason_json" in link_data
    assert "vis_drop_reason_schema" in link_data
    assert "vis_drop_reason_range" in link_data
    assert "vis_drop_reason_semantics" in link_data

    assert int(status["vis_rx_ok"]) >= 1
