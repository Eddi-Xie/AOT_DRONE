import os
import threading
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .protocol_constants import UDP_MAX_TEL_BYTES, UDP_MAX_VIS_BYTES, UDP_TEL_PORT, UDP_VIS_PORT
from .state import SharedState
from .udp_ingest import start_udp_receiver
from .vis_ingest import VisUdpIngestor

app = FastAPI()
state = SharedState()
_vis_stop_event = threading.Event()
_vis_ingestor: VisUdpIngestor | None = None
_vis_thread: threading.Thread | None = None


def _tel_handler(msg: dict) -> None:
    if msg.get("type") == "TEL":
        state.update_tel(msg)


@app.on_event("startup")
def startup() -> None:
    global _vis_ingestor, _vis_thread
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


@app.on_event("shutdown")
def shutdown() -> None:
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
