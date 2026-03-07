from __future__ import annotations

import time

from fastapi.testclient import TestClient

from src.backend.app import app
from tests.integration.e2e_test_utils import (
    configure_backend_e2e_env,
    encode_datagram,
    make_tel_ingestor_for_client,
    make_tel_payload,
)
from tests.integration.status_poll_utils import wait_for_status
from tests.integration.ws_client_utils import wait_for_ws_event


def test_e2e_tel_to_ws_and_status(monkeypatch) -> None:
    configure_backend_e2e_env(monkeypatch, cmd_bridge_enabled=False)

    with TestClient(app) as client:
        tel_ingestor = make_tel_ingestor_for_client(client)
        with client.websocket_connect("/ws") as ws:
            wait_for_ws_event(ws, "LINK_STATUS", timeout_s=1.5)

            tel_payload = make_tel_payload(seq=3001)
            assert tel_ingestor.process_datagram(
                encode_datagram(tel_payload),
                rx_monotonic_s=time.monotonic(),
            )

            tel_event = wait_for_ws_event(ws, "TEL_UPDATE", timeout_s=2.0)
            link_event = wait_for_ws_event(ws, "LINK_STATUS", timeout_s=2.0)

        status = wait_for_status(
            client,
            lambda payload: int(payload.get("tel_rx_ok", 0)) >= 1,
            timeout_s=2.0,
        )

    assert tel_event["event"] == "TEL_UPDATE"
    assert tel_event["data"]["type"] == "TEL"
    assert tel_event["data"]["seq"] == 3001
    assert "tel_age_s" in tel_event["data"]

    link_data = link_event["data"]
    assert "tel_age_s" in link_data
    assert "tel_rx_ok" in link_data
    assert "tel_rx_bad" in link_data

    assert int(status["tel_rx_ok"]) >= 1
