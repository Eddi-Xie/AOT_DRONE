from __future__ import annotations

import json
from typing import Any

from src.backend.tel_ingest import TelUdpIngestor
from src.backend.vis_ingest import VisUdpIngestor


def configure_backend_e2e_env(
    monkeypatch,
    *,
    cmd_bridge_enabled: bool,
    fc_host: str = "127.0.0.1",
    fc_port: int = 9002,
    vis_fresh_s: float = 0.25,
) -> None:
    monkeypatch.setenv("BACKEND_TEL_INGEST_ENABLED", "0")
    monkeypatch.setenv("BACKEND_VIS_INGEST_ENABLED", "0")
    monkeypatch.setenv("BACKEND_CMD_BRIDGE_ENABLED", "1" if cmd_bridge_enabled else "0")
    monkeypatch.setenv("BACKEND_FC_HOST", fc_host)
    monkeypatch.setenv("BACKEND_FC_PORT", str(fc_port))
    monkeypatch.setenv("BACKEND_CMD_HZ", "30.0")
    monkeypatch.setenv("BACKEND_CMD_CONNECT_TIMEOUT_S", "0.2")
    monkeypatch.setenv("BACKEND_CMD_BACKOFF_INITIAL_S", "0.05")
    monkeypatch.setenv("BACKEND_CMD_BACKOFF_MAX_S", "0.2")
    monkeypatch.setenv("BACKEND_CMD_LOG_INTERVAL_S", "0.0")
    monkeypatch.setenv("BACKEND_VIS_FRESH_S", str(vis_fresh_s))
    monkeypatch.setenv("BACKEND_WS_LINK_HZ", "20.0")
    monkeypatch.setenv("BACKEND_WS_TEL_HZ", "40.0")
    monkeypatch.setenv("BACKEND_WS_VIS_HZ", "40.0")
    monkeypatch.setenv("BACKEND_WS_CLIENT_QUEUE_MAX", "32")
    monkeypatch.setenv("BACKEND_WS_WARNING_INTERVAL_S", "0.05")
    monkeypatch.setenv("BACKEND_TEL_LOG_INTERVAL_S", "0.0")
    monkeypatch.setenv("BACKEND_VIS_LOG_INTERVAL_S", "0.0")
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "0")


def make_vis_payload(seq: int = 1) -> dict[str, Any]:
    return {
        "type": "VIS",
        "seq": seq,
        "timestamp_s": 10.0 + seq,
        "tracking_state": 3,
        "loc_x": 0.12,
        "loc_y": -0.08,
        "bound_w": 0.28,
        "bound_h": 0.34,
        "confidence": 0.91,
    }


def make_tel_payload(seq: int = 1) -> dict[str, Any]:
    return {
        "type": "TEL",
        "seq": seq,
        "timestamp_s": 10.0 + seq,
        "control_mode": 1,
        "tracking_state": 3,
        "distFront_m": 0.0,
        "distBack_m": 0.0,
        "distBottom_m": 1.2,
        "target_x": 0.1,
        "target_y": -0.1,
        "bound_w": 0.2,
        "bound_h": 0.3,
        "confidence": 0.8,
    }


def encode_datagram(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def make_vis_ingestor_for_client(client: Any) -> VisUdpIngestor:
    shared_state = client.app.state.shared_state
    broadcaster = client.app.state.broadcaster
    assert shared_state is not None
    assert broadcaster is not None
    return VisUdpIngestor(
        state=shared_state,
        max_bytes=512,
        log_interval_s=0.0,
        on_valid=broadcaster.on_vis_update,
        on_drop=broadcaster.on_vis_drop,
    )


def make_tel_ingestor_for_client(client: Any) -> TelUdpIngestor:
    shared_state = client.app.state.shared_state
    broadcaster = client.app.state.broadcaster
    assert shared_state is not None
    assert broadcaster is not None
    return TelUdpIngestor(
        state=shared_state,
        max_bytes=1024,
        log_interval_s=0.0,
        on_valid=broadcaster.on_tel_update,
        on_drop=broadcaster.on_tel_drop,
    )
