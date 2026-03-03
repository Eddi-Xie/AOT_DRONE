from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.backend.app import app
from tests.integration.e2e_test_utils import configure_backend_e2e_env
from tests.integration.fake_fc_server import FakeFcServer, require_tcp_bind_or_skip
from tests.integration.status_poll_utils import wait_for_status


def test_e2e_cmd_gating_no_vis(monkeypatch) -> None:
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
        vis_fresh_s=0.5,
    )

    try:
        with TestClient(app) as client:
            assert fc_server.wait_for_connection(timeout_s=2.0)
            response = client.post("/api/intent", json={"desired_mode": 1})
            assert response.status_code == 200

            status = wait_for_status(
                client,
                lambda payload: (
                    payload.get("tracking_blocked_reason") == "no_vis"
                    and _last_cmd_mode(payload) == 2
                ),
                timeout_s=2.5,
            )

            target_seq = int(status["last_cmd_payload"]["last_cmd_seq"])
            matched_cmd = fc_server.wait_for_command(
                lambda payload: (
                    _cmd_seq(payload) >= target_seq and int(payload.get("desired_mode", -1)) == 2
                ),
                timeout_s=1.0,
            )

        assert matched_cmd is not None
        assert matched_cmd["desired_mode"] == 2
        assert fc_server.wait_for_commands(minimum_count=1, timeout_s=1.0)
        seqs = [
            int(msg["seq"]) for msg in fc_server.received_cmds() if isinstance(msg.get("seq"), int)
        ]
        for idx in range(1, len(seqs)):
            assert seqs[idx] > seqs[idx - 1]
        assert status["tracking_blocked_reason"] == "no_vis"
        assert fc_server.framing_errors == []
        assert fc_server.json_errors == []
    finally:
        fc_server.stop()


def _cmd_seq(payload: dict[str, object]) -> int:
    seq = payload.get("seq")
    return int(seq) if isinstance(seq, int) else -1


def _last_cmd_mode(status_payload: dict[str, object]) -> int | None:
    last_cmd_payload = status_payload.get("last_cmd_payload")
    if not isinstance(last_cmd_payload, dict):
        return None
    mode = last_cmd_payload.get("last_cmd_desired_mode")
    return int(mode) if isinstance(mode, int) else None
