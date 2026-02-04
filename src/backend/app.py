import threading

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .protocol_constants import UDP_MAX_TEL_BYTES, UDP_MAX_VIS_BYTES, UDP_TEL_PORT, UDP_VIS_PORT
from .state import SharedState
from .udp_ingest import start_udp_receiver

app = FastAPI()
state = SharedState()


def _tel_handler(msg):
    if msg.get("type") == "TEL":
        state.update_tel(msg)


def _vis_handler(msg):
    if msg.get("type") == "VIS":
        state.update_vis(msg)


@app.on_event("startup")
def startup():
    threading.Thread(
        target=start_udp_receiver,
        args=("127.0.0.1", UDP_TEL_PORT, UDP_MAX_TEL_BYTES, _tel_handler),
        daemon=True,
    ).start()

    threading.Thread(
        target=start_udp_receiver,
        args=("127.0.0.1", UDP_VIS_PORT, UDP_MAX_VIS_BYTES, _vis_handler),
        daemon=True,
    ).start()


@app.get("/health")
def health():
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            # Broadcast latest snapshot at ~20 Hz
            await ws.send_json(state.snapshot())
            await ws.receive_text()  # allow client pings/keepalive
            await ws.send_text("ok")
    except WebSocketDisconnect:
        return
    except Exception:
        return


def _tel_handler(msg):
    print(f"Received UDP Message: {msg}")  # <--- Add this to debug
    if msg.get("type") == "TEL":
        state.update_tel(msg)
