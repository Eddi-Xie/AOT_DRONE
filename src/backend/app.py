import os
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .cmd_bridge import CmdBridge
from .cmd_schema import CONTROL_MODES
from .protocol_constants import (
    TCP_CMD_PORT,
    TCP_MAX_FRAME_BYTES,
    UDP_MAX_TEL_BYTES,
    UDP_MAX_VIS_BYTES,
    UDP_TEL_PORT,
    UDP_VIS_PORT,
)
from .state import SharedState
from .udp_ingest import start_udp_receiver
from .vis_ingest import VisUdpIngestor

app = FastAPI()
state = SharedState()
_vis_stop_event = threading.Event()
_vis_ingestor: VisUdpIngestor | None = None
_vis_thread: threading.Thread | None = None
_cmd_bridge: CmdBridge | None = None


class IntentRequest(BaseModel):
    desired_mode: int
    arm: bool | None = None
    setpoints: dict[str, float] | None = None


def _tel_handler(msg: dict) -> None:
    if msg.get("type") == "TEL":
        state.update_tel(msg)


@app.on_event("startup")
def startup() -> None:
    global _cmd_bridge, _vis_ingestor, _vis_thread
    threading.Thread(
        target=start_udp_receiver,
        args=("127.0.0.1", UDP_TEL_PORT, UDP_MAX_TEL_BYTES, _tel_handler),
        daemon=True,
    ).start()

    _vis_ingestor = _build_vis_ingestor_from_env()
    _vis_stop_event.clear()
    _vis_thread = threading.Thread(
        target=_vis_ingestor.serve_forever,
        args=(_vis_stop_event,),
        daemon=True,
    )
    _vis_thread.start()

    _cmd_bridge = _build_cmd_bridge_from_env()
    _cmd_bridge.start()


@app.on_event("shutdown")
def shutdown() -> None:
    if _cmd_bridge is not None:
        _cmd_bridge.stop()
    _vis_stop_event.set()
    if _vis_ingestor is not None:
        _vis_ingestor.close()
    if _vis_thread is not None:
        _vis_thread.join(timeout=1.0)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/status/vis")
def vis_status(connected_threshold_s: float = 1.0) -> dict[str, Any]:
    return state.get_vis_status(connected_threshold_s=connected_threshold_s)


@app.post("/api/intent")
def post_intent(intent_req: IntentRequest) -> dict[str, Any]:
    if intent_req.desired_mode not in CONTROL_MODES:
        raise HTTPException(status_code=422, detail="desired_mode must be one of 0,1,2,3")

    intent_payload: dict[str, Any] = {"desired_mode": int(intent_req.desired_mode)}
    if intent_req.arm is not None:
        intent_payload["arm"] = bool(intent_req.arm)
    if intent_req.setpoints is not None:
        intent_payload["setpoints"] = dict(intent_req.setpoints)

    state.update_intent(intent_payload)
    return {"ok": True, "intent": intent_payload}


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    cmd_status = state.get_cmd_bridge_status()
    vis_stats = state.get_vis_stats()
    return {
        "fc_connected": cmd_status["fc_connected"],
        "fc_last_connect_attempt_s": cmd_status["fc_last_connect_attempt_s"],
        "cmd_tx_total": cmd_status["cmd_tx_total"],
        "cmd_tx_ok": cmd_status["cmd_tx_ok"],
        "cmd_tx_fail": cmd_status["cmd_tx_fail"],
        "cmd_last_sent_monotonic_s": cmd_status["cmd_last_sent_monotonic_s"],
        "cmd_hz_est": cmd_status["cmd_hz_est"],
        "vis_age_s": vis_stats["vis_age_s"],
        "vis_rx_ok": vis_stats["vis_rx_ok"],
        "vis_rx_bad": vis_stats["vis_rx_bad"],
        "tracking_blocked_reason": cmd_status["tracking_blocked_reason"],
        "last_cmd_payload": {
            "last_cmd_seq": cmd_status["last_cmd_seq"],
            "last_cmd_desired_mode": cmd_status["last_cmd_desired_mode"],
            "last_cmd_had_tracking": cmd_status["last_cmd_had_tracking"],
            "last_cmd_bytes": cmd_status["last_cmd_bytes"],
        },
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            await ws.send_json(state.snapshot())
            await ws.receive_text()
            await ws.send_text("ok")
    except WebSocketDisconnect:
        return
    except Exception:
        return


def _build_vis_ingestor_from_env() -> VisUdpIngestor:
    host = os.environ.get("BACKEND_VIS_HOST", "127.0.0.1")
    port = _read_env_int("BACKEND_VIS_PORT", UDP_VIS_PORT)
    max_bytes = _read_env_int("BACKEND_VIS_MAX_BYTES", UDP_MAX_VIS_BYTES)
    recv_timeout_s = _read_env_float("BACKEND_VIS_RECV_TIMEOUT_S", 0.1)
    log_interval_s = _read_env_float("BACKEND_VIS_LOG_INTERVAL_S", 5.0)
    return VisUdpIngestor(
        state=state,
        bind_host=host,
        port=port,
        max_bytes=max_bytes,
        recv_timeout_s=recv_timeout_s,
        log_interval_s=log_interval_s,
    )


def _build_cmd_bridge_from_env() -> CmdBridge:
    fc_host = os.environ.get("BACKEND_FC_HOST", "127.0.0.1")
    fc_port = _read_env_int("BACKEND_FC_PORT", TCP_CMD_PORT)
    cmd_hz = _read_env_float("BACKEND_CMD_HZ", 50.0)
    vis_fresh_s = _read_env_float("BACKEND_VIS_FRESH_S", 0.25)
    max_payload_bytes = _read_env_int("BACKEND_CMD_MAX_PAYLOAD_BYTES", TCP_MAX_FRAME_BYTES)
    connect_timeout_s = _read_env_float("BACKEND_CMD_CONNECT_TIMEOUT_S", 1.0)
    backoff_initial_s = _read_env_float("BACKEND_CMD_BACKOFF_INITIAL_S", 1.0)
    backoff_max_s = _read_env_float("BACKEND_CMD_BACKOFF_MAX_S", 5.0)
    log_interval_s = _read_env_float("BACKEND_CMD_LOG_INTERVAL_S", 5.0)
    return CmdBridge(
        state=state,
        fc_host=fc_host,
        fc_port=fc_port,
        tick_hz=cmd_hz,
        vis_fresh_s=vis_fresh_s,
        max_payload_bytes=max_payload_bytes,
        connect_timeout_s=connect_timeout_s,
        connect_backoff_initial_s=backoff_initial_s,
        connect_backoff_max_s=backoff_max_s,
        log_interval_s=log_interval_s,
    )


def _read_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw)


def _read_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return float(raw)
