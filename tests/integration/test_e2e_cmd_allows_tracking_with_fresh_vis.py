from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from src.backend.app import app
from tests.integration.e2e_test_utils import (
    configure_backend_e2e_env,
    encode_datagram,
    make_vis_ingestor_for_client,
    make_vis_payload,
)
from tests.integration.fake_fc_server import FakeFcServer, require_tcp_bind_or_skip
from tests.integration.status_poll_utils import wait_for_status


def test_e2e_cmd_allows_tracking_with_fresh_vis(monkeypatch) -> None:
    require_tcp_bind_or_skip()

    fc_server = FakeFcServer()
    try:
        fc_server.start()
    except OSError as exc:
        pytest.skip(f"SKIP_NET: TCP socket bind unavailable in test environment: {exc}")

    configure_backend_e2e_env(
        monkeypatch,
        cmd_bridge_enabled=True,
        fc_host="127.0.0.1",
        fc_port=fc_server.listening_port,
        vis_fresh_s=2.0,
    )

    try:
        with TestClient(app) as client:
            assert fc_server.wait_for_connection(timeout_s=2.0)
            vis_ingestor = make_vis_ingestor_for_client(client)

            vis_payload = make_vis_payload(seq=2001)
            assert vis_ingestor.process_datagram(
                encode_datagram(vis_payload),
                rx_monotonic_s=time.monotonic(),
            )
            wait_for_status(
                client,
                lambda payload: int(payload.get("vis_rx_ok", 0)) >= 1,
                timeout_s=1.5,
            )

            response = client.post("/api/intent", json={"desired_mode": 1})
            assert response.status_code == 200

            status = wait_for_status(
                client,
                lambda payload: (
                    payload.get("tracking_blocked_reason") is None
                    and int(payload["last_cmd_payload"]["last_cmd_desired_mode"]) == 1
                ),
                timeout_s=2.5,
            )
            target_seq = int(status["last_cmd_payload"]["last_cmd_seq"])
            matched_cmd = fc_server.wait_for_command(
                lambda payload: (
                    _cmd_seq(payload) >= target_seq
                    and int(payload.get("desired_mode", -1)) == 1
                    and isinstance(payload.get("tracking"), dict)
                ),
                timeout_s=1.0,
            )

        assert fc_server.wait_for_commands(minimum_count=2, timeout_s=1.0)
        seqs = [
            int(msg["seq"]) for msg in fc_server.received_cmds() if isinstance(msg.get("seq"), int)
        ]
        for idx in range(1, len(seqs)):
            assert seqs[idx] > seqs[idx - 1]
        assert matched_cmd is not None
        assert matched_cmd["desired_mode"] == 1
        tracking = matched_cmd["tracking"]
        assert isinstance(tracking, dict)
        assert tracking["tracking_state"] == 3
        assert "loc_x" in tracking
        assert "loc_y" in tracking
        assert "bound_w" in tracking
        assert "bound_h" in tracking
        assert "confidence" in tracking
        assert status["tracking_blocked_reason"] is None
        assert fc_server.framing_errors == []
        assert fc_server.json_errors == []
    finally:
        fc_server.stop()


def _cmd_seq(payload: dict[str, object]) -> int:
    seq = payload.get("seq")
    return int(seq) if isinstance(seq, int) else -1
