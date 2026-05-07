from __future__ import annotations

import asyncio
import hmac
import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .broadcast import BackendBroadcaster
from .cmd_bridge import CmdBridge
from .cmd_schema import CONTROL_MODES
from .protocol_constants import (
    CMD_TIMEOUT_S,
    TCP_CMD_PORT,
    TCP_MAX_FRAME_BYTES,
    UDP_MAX_TEL_BYTES,
    UDP_MAX_VIS_BYTES,
    UDP_TEL_PORT,
    UDP_VIS_PORT,
    VIDEO_MAX_JPEG_BYTES_DEFAULT,
)
from .rate_limit import IpRateLimiter
from .state import SharedState
from .tel_ingest import TelUdpIngestor
from .video_hub import (
    SyntheticJpegGenerator,
    VideoConfig,
    VideoFrameHub,
    can_decode_jpeg,
    make_mjpeg_part,
)
from .vis_ingest import VisUdpIngestor
from .ws_manager import WsManager

LOGGER = logging.getLogger(__name__)

state = SharedState()

_tel_stop_event = threading.Event()
_vis_stop_event = threading.Event()

_tel_ingestor: TelUdpIngestor | None = None
_vis_ingestor: VisUdpIngestor | None = None
_tel_thread: threading.Thread | None = None
_vis_thread: threading.Thread | None = None
_cmd_bridge: CmdBridge | None = None
_ws_manager: WsManager | None = None
_broadcaster: BackendBroadcaster | None = None
_video_hub: VideoFrameHub | None = None
_synthetic_jpeg_generator: SyntheticJpegGenerator | None = None

_runtime_vis_fresh_s = 0.25
_runtime_tel_fresh_s = 0.5
_runtime_cmd_hz = 50.0
_runtime_tel_hz = 50.0
_runtime_video_enabled = True
_runtime_video_fps = 10.0
_runtime_video_max_jpeg_bytes = VIDEO_MAX_JPEG_BYTES_DEFAULT
_runtime_video_frame_fresh_s = 1.0
_runtime_video_validate_decode = False

# When set, all of /api/intent, /api/frame, and the /ws upgrade require the
# token. When None (default / env unset), auth is disabled and the operator is
# expected to bind the FastAPI server to 127.0.0.1.
_runtime_api_token: str | None = None

# Per-IP rate limiters. None => limit disabled (env var set to 0). Capacity
# equals the configured Hz so a 1-second burst is allowed before the steady
# refill cap kicks in.
_frame_rate_limiter: IpRateLimiter | None = None
_intent_rate_limiter: IpRateLimiter | None = None


def _read_env_token(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _require_api_token(request: Request) -> None:
    """Enforce bearer auth on REST routes when BACKEND_API_TOKEN is set.

    No-op when auth is disabled. On mismatch raises 401. Constant-time compare
    prevents trivial timing leaks of the secret.
    """
    expected = _runtime_api_token
    if expected is None:
        return
    authorization = request.headers.get("authorization", "")
    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise HTTPException(status_code=401, detail="missing bearer token")
    if not hmac.compare_digest(presented.strip(), expected):
        raise HTTPException(status_code=401, detail="invalid bearer token")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limit_frame(request: Request) -> None:
    limiter = _frame_rate_limiter
    if limiter is None:
        return
    if not limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="frame rate limit exceeded")


def _rate_limit_intent(request: Request) -> None:
    limiter = _intent_rate_limiter
    if limiter is None:
        return
    if not limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="intent rate limit exceeded")


def _check_ws_token(presented: str | None) -> bool:
    """Return True iff the token is acceptable for a WS upgrade.

    Browsers cannot set Authorization on `new WebSocket(...)`, so the token is
    carried as a `?token=` query param on the upgrade URL. Auth disabled =>
    always accept.
    """
    expected = _runtime_api_token
    if expected is None:
        return True
    if not presented:
        return False
    return hmac.compare_digest(presented.strip(), expected)


class IntentRequest(BaseModel):
    desired_mode: int
    arm: bool | None = None
    setpoints: dict[str, float] | None = None


def _startup() -> None:
    global _broadcaster, _cmd_bridge, _tel_ingestor, _tel_thread, _vis_ingestor, _vis_thread
    global _ws_manager, _runtime_cmd_hz, _runtime_tel_hz, _runtime_vis_fresh_s
    global _runtime_tel_fresh_s
    global _runtime_video_enabled, _runtime_video_fps, _runtime_video_max_jpeg_bytes
    global _runtime_video_frame_fresh_s, _runtime_video_validate_decode
    global _runtime_api_token
    global _frame_rate_limiter, _intent_rate_limiter
    global _video_hub, _synthetic_jpeg_generator

    state.reset()
    _runtime_cmd_hz = _read_env_float("BACKEND_CMD_HZ", 50.0)
    _runtime_tel_hz = _read_env_float("BACKEND_TEL_HZ_NOMINAL", 50.0)
    _runtime_vis_fresh_s = _read_env_float("BACKEND_VIS_FRESH_S", 0.25)
    _runtime_tel_fresh_s = max(0.001, _read_env_float("BACKEND_TEL_FRESH_S", 0.5))
    _runtime_video_enabled = _read_env_bool("BACKEND_VIDEO_ENABLED", True)
    _runtime_video_fps = max(0.5, _read_env_float("BACKEND_VIDEO_FPS", 10.0))
    _runtime_video_max_jpeg_bytes = max(
        1, _read_env_int("BACKEND_VIDEO_MAX_JPEG_BYTES", VIDEO_MAX_JPEG_BYTES_DEFAULT)
    )
    _runtime_video_frame_fresh_s = max(0.0, _read_env_float("BACKEND_VIDEO_FRAME_FRESH_S", 1.0))
    _runtime_video_validate_decode = _read_env_bool("BACKEND_VIDEO_VALIDATE_DECODE", False)
    frame_rate_hz = max(0.0, _read_env_float("BACKEND_FRAME_RATE_LIMIT_HZ", 30.0))
    intent_rate_hz = max(0.0, _read_env_float("BACKEND_INTENT_RATE_LIMIT_HZ", 5.0))
    _frame_rate_limiter = (
        IpRateLimiter(capacity=frame_rate_hz, refill_per_s=frame_rate_hz)
        if frame_rate_hz > 0
        else None
    )
    _intent_rate_limiter = (
        IpRateLimiter(capacity=intent_rate_hz, refill_per_s=intent_rate_hz)
        if intent_rate_hz > 0
        else None
    )
    _runtime_api_token = _read_env_token("BACKEND_API_TOKEN")
    if _runtime_api_token is None:
        LOGGER.warning(
            "BACKEND_API_TOKEN unset: /api/intent, /api/frame, and /ws are unauthenticated. "
            "Bind FastAPI to 127.0.0.1 in this configuration."
        )
    else:
        LOGGER.info("BACKEND_API_TOKEN set: bearer auth required on /api/intent, /api/frame, /ws.")
    _video_hub = VideoFrameHub()
    _synthetic_jpeg_generator = SyntheticJpegGenerator(width=640, height=360)

    _ws_manager = WsManager(queue_max=_read_env_int("BACKEND_WS_CLIENT_QUEUE_MAX", 10))
    _broadcaster = BackendBroadcaster(
        state=state,
        ws_manager=_ws_manager,
        tel_hz=_read_env_float("BACKEND_WS_TEL_HZ", 20.0),
        vis_hz=_read_env_float("BACKEND_WS_VIS_HZ", 20.0),
        link_hz=_read_env_float("BACKEND_WS_LINK_HZ", 2.0),
        vis_fresh_s=_runtime_vis_fresh_s,
        tel_fresh_s=_runtime_tel_fresh_s,
        cmd_timeout_s=CMD_TIMEOUT_S,
        cmd_hz_nominal=_runtime_cmd_hz,
        tel_hz_nominal=_runtime_tel_hz,
        warning_min_interval_s=_read_env_float("BACKEND_WS_WARNING_INTERVAL_S", 2.0),
        link_status_extra_provider=_build_video_status,
    )
    _broadcaster.start()

    _tel_ingestor = None
    _tel_thread = None
    if _read_env_bool("BACKEND_TEL_INGEST_ENABLED", True):
        _tel_ingestor = _build_tel_ingestor_from_env(
            on_valid=_broadcaster.on_tel_update,
            on_drop=_broadcaster.on_tel_drop,
        )
        _tel_stop_event.clear()
        _tel_thread = threading.Thread(
            target=_tel_ingestor.serve_forever,
            args=(_tel_stop_event,),
            daemon=True,
        )
        _tel_thread.start()

    _vis_ingestor = None
    _vis_thread = None
    if _read_env_bool("BACKEND_VIS_INGEST_ENABLED", True):
        _vis_ingestor = _build_vis_ingestor_from_env(
            on_valid=_broadcaster.on_vis_update,
            on_drop=_broadcaster.on_vis_drop,
        )
        _vis_stop_event.clear()
        _vis_thread = threading.Thread(
            target=_vis_ingestor.serve_forever,
            args=(_vis_stop_event,),
            daemon=True,
        )
        _vis_thread.start()

    _cmd_bridge = None
    if _read_env_bool("BACKEND_CMD_BRIDGE_ENABLED", True):
        _cmd_bridge = _build_cmd_bridge_from_env(
            cmd_hz=_runtime_cmd_hz,
            vis_fresh_s=_runtime_vis_fresh_s,
            on_connection_change=_broadcaster.on_fc_connection_changed,
            on_tracking_blocked=_broadcaster.on_tracking_blocked,
        )
        _cmd_bridge.start()

    app.state.shared_state = state
    app.state.ws_manager = _ws_manager
    app.state.broadcaster = _broadcaster
    app.state.tel_ingestor = _tel_ingestor
    app.state.vis_ingestor = _vis_ingestor
    app.state.cmd_bridge = _cmd_bridge
    app.state.video_hub = _video_hub
    app.state.video_config = VideoConfig(
        enabled=_runtime_video_enabled,
        fps=_runtime_video_fps,
        max_jpeg_bytes=_runtime_video_max_jpeg_bytes,
        frame_fresh_s=_runtime_video_frame_fresh_s,
    )


def _shutdown() -> None:
    global _broadcaster, _cmd_bridge, _tel_ingestor, _tel_thread, _vis_ingestor, _vis_thread
    global _ws_manager, _video_hub, _synthetic_jpeg_generator

    if _cmd_bridge is not None:
        _cmd_bridge.stop()
        _cmd_bridge = None

    _tel_stop_event.set()
    if _tel_ingestor is not None:
        _tel_ingestor.close()
        _tel_ingestor = None
    if _tel_thread is not None:
        _tel_thread.join(timeout=1.0)
        _tel_thread = None

    _vis_stop_event.set()
    if _vis_ingestor is not None:
        _vis_ingestor.close()
        _vis_ingestor = None
    if _vis_thread is not None:
        _vis_thread.join(timeout=1.0)
        _vis_thread = None

    if _broadcaster is not None:
        _broadcaster.stop(join_timeout_s=1.0)
        _broadcaster = None
    _ws_manager = None
    _video_hub = None
    _synthetic_jpeg_generator = None

    app.state.ws_manager = None
    app.state.broadcaster = None
    app.state.tel_ingestor = None
    app.state.vis_ingestor = None
    app.state.cmd_bridge = None
    app.state.video_hub = None
    app.state.video_config = None


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _startup()
    try:
        yield
    finally:
        _shutdown()


app = FastAPI(lifespan=_lifespan)


def _build_cors_origins() -> list[str]:
    """Parse comma-separated origin list from BACKEND_CORS_ALLOW_ORIGINS.

    Empty / unset => no CORS middleware (default-deny cross-origin). Operators
    that serve the webapp from a different origin (e.g. a non-Vite static host)
    set this to that origin explicitly.
    """
    raw = os.environ.get("BACKEND_CORS_ALLOW_ORIGINS", "").strip()
    if not raw:
        return []
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


_cors_origins = _build_cors_origins()
if _cors_origins:
    # `allow_credentials=False` keeps the model token-only (no cookies). The
    # `Authorization` header is the only custom request header we need to
    # whitelist; everything else is simple-CORS-safe.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/status/vis")
def vis_status(connected_threshold_s: float = Query(default=1.0, ge=0.0)) -> dict[str, Any]:
    return state.get_vis_status(connected_threshold_s=connected_threshold_s)


@app.post(
    "/api/intent",
    dependencies=[Depends(_require_api_token), Depends(_rate_limit_intent)],
)
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
    now_s = time.monotonic()
    status = state.get_link_status(
        vis_fresh_s=_runtime_vis_fresh_s,
        tel_fresh_s=_runtime_tel_fresh_s,
        cmd_timeout_s=CMD_TIMEOUT_S,
        cmd_hz=_runtime_cmd_hz,
        tel_hz=_runtime_tel_hz,
        now_monotonic_s=now_s,
    )
    status.update(_build_video_status(now_monotonic_s=now_s))
    cmd_status = state.get_cmd_bridge_status()
    status["last_cmd_payload"] = {
        "last_cmd_seq": cmd_status["last_cmd_seq"],
        "last_cmd_desired_mode": cmd_status["last_cmd_desired_mode"],
        "last_cmd_had_tracking": cmd_status["last_cmd_had_tracking"],
        "last_cmd_bytes": cmd_status["last_cmd_bytes"],
    }
    return status


@app.post(
    "/api/frame",
    dependencies=[Depends(_require_api_token), Depends(_rate_limit_frame)],
)
async def post_frame(request: Request) -> dict[str, Any]:
    if not _runtime_video_enabled:
        raise HTTPException(status_code=503, detail="video streaming is disabled")

    video_hub = _video_hub
    if video_hub is None:
        raise HTTPException(status_code=503, detail="video subsystem not ready")

    content_type = request.headers.get("content-type", "").lower()
    if "image/jpeg" not in content_type:
        video_hub.record_bad_frame()
        raise HTTPException(status_code=400, detail="content-type must be image/jpeg")

    # Reject Transfer-Encoding: chunked outright. Vision (the only intended
    # producer) always sends Content-Length; a chunked POST has no advertised
    # size, so accepting it would let an attacker bypass the JPEG-byte cap by
    # streaming an unbounded body and force the worker to buffer it before any
    # size check could fire.
    transfer_encoding = request.headers.get("transfer-encoding", "")
    encodings = {token.strip().lower() for token in transfer_encoding.split(",") if token.strip()}
    if "chunked" in encodings:
        video_hub.record_bad_frame()
        raise HTTPException(
            status_code=400,
            detail="transfer-encoding: chunked is not supported on /api/frame",
        )

    max_bytes = _runtime_video_max_jpeg_bytes
    content_length_header = request.headers.get("content-length")
    if content_length_header is None:
        # With chunked already rejected above, no Content-Length means we have
        # no advertised body size. Refuse rather than fall through to an
        # unbounded body read.
        video_hub.record_bad_frame()
        raise HTTPException(status_code=411, detail="content-length header is required")

    content_length_text = content_length_header.strip()
    if not content_length_text:
        # Present-but-empty Content-Length must not silently fall through
        # to await request.body(); treat as a malformed header.
        video_hub.record_bad_frame()
        raise HTTPException(
            status_code=400,
            detail="invalid content-length header",
        )
    try:
        content_length = int(content_length_text)
    except ValueError:
        video_hub.record_bad_frame()
        raise HTTPException(
            status_code=400,
            detail="invalid content-length header",
        ) from None
    if content_length < 0:
        video_hub.record_bad_frame()
        raise HTTPException(status_code=400, detail="invalid content-length header")
    if content_length > max_bytes:
        video_hub.record_bad_frame()
        raise HTTPException(
            status_code=400,
            detail=f"content-length exceeds max size ({max_bytes} bytes)",
        )

    # Stream-read the body so a misreported Content-Length cannot trick us
    # into buffering more than max_bytes before the size check fires.
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        received += len(chunk)
        if received > max_bytes:
            video_hub.record_bad_frame()
            raise HTTPException(
                status_code=400,
                detail=f"jpeg payload exceeds max size ({max_bytes} bytes)",
            )
        chunks.append(chunk)
    payload = b"".join(chunks)

    if len(payload) == 0:
        video_hub.record_bad_frame()
        raise HTTPException(status_code=400, detail="empty request body")

    if not _looks_like_jpeg(payload):
        video_hub.record_bad_frame()
        raise HTTPException(status_code=400, detail="invalid jpeg payload")

    if _runtime_video_validate_decode and not can_decode_jpeg(payload):
        video_hub.record_bad_frame()
        raise HTTPException(status_code=400, detail="jpeg decode validation failed")

    video_hub.set_jpeg(payload, now_monotonic_s=time.monotonic())
    return {"ok": True, "size_bytes": len(payload)}


@app.get("/video")
async def video_stream(
    max_parts: int | None = Query(default=None, ge=1, le=1000),
) -> StreamingResponse:
    if not _runtime_video_enabled:
        raise HTTPException(status_code=503, detail="video streaming is disabled")

    video_hub = _video_hub
    if video_hub is None:
        raise HTTPException(status_code=503, detail="video subsystem not ready")

    boundary = "frame"
    frame_period_s = max(0.001, 1.0 / _runtime_video_fps)

    async def stream_generator() -> AsyncIterator[bytes]:
        video_hub.register_client()
        try:
            emitted_parts = 0
            while True:
                now_s = time.monotonic()
                frame = video_hub.get_fresh_jpeg(
                    max_age_s=_runtime_video_frame_fresh_s,
                    now_monotonic_s=now_s,
                )
                if frame is None:
                    frame = _render_synthetic_frame(now_monotonic_s=now_s)

                video_hub.record_frame_served(now_monotonic_s=now_s)
                yield make_mjpeg_part(frame, boundary=boundary)
                emitted_parts += 1
                if max_parts is not None and emitted_parts >= max_parts:
                    break
                await asyncio.sleep(frame_period_s)
        finally:
            video_hub.unregister_client()

    return StreamingResponse(
        stream_generator(),
        media_type=f"multipart/x-mixed-replace; boundary={boundary}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str | None = Query(default=None)) -> None:
    if not _check_ws_token(token):
        # Close before accept so unauthorized clients see a clean handshake
        # rejection instead of a brief "connected" flash. 4401 = app-defined
        # "unauthorized" close code.
        await ws.close(code=4401)
        return

    await ws.accept()

    ws_manager = _ws_manager
    broadcaster = _broadcaster
    if ws_manager is None or broadcaster is None:
        await ws.close(code=1011)
        return

    client_id = ws_manager.register(ws)
    try:
        now_s = time.monotonic()
        link_payload = broadcaster.build_link_status(now_monotonic_s=now_s)
        await ws.send_json(
            ws_manager.build_envelope("LINK_STATUS", link_payload, timestamp_s=now_s)
        )

        tel_payload = broadcaster.build_tel_update_data(now_monotonic_s=now_s)
        if tel_payload is not None:
            await ws.send_json(
                ws_manager.build_envelope("TEL_UPDATE", tel_payload, timestamp_s=now_s)
            )

        vis_payload = broadcaster.build_vis_update_data(now_monotonic_s=now_s)
        if vis_payload is not None:
            await ws.send_json(
                ws_manager.build_envelope("VIS_UPDATE", vis_payload, timestamp_s=now_s)
            )

        while True:
            envelope = ws_manager.pop_next(client_id)
            if envelope is None:
                await asyncio.sleep(0.02)
                continue
            await ws.send_json(envelope)
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.unregister(client_id)


def _build_video_status(now_monotonic_s: float | None = None) -> dict[str, Any]:
    now_s = float(now_monotonic_s) if now_monotonic_s is not None else time.monotonic()
    video_hub = _video_hub
    if video_hub is None:
        return {
            "video_enabled": _runtime_video_enabled,
            "video_clients": 0,
            "video_fps_est": 0.0,
            "last_frame_age_s": None,
            "frames_rx_ok": 0,
            "frames_rx_bad": 0,
        }

    stats = video_hub.get_stats(now_monotonic_s=now_s)
    return {
        "video_enabled": _runtime_video_enabled,
        "video_clients": int(stats["video_clients"]),
        "video_fps_est": float(stats["video_fps_est"]),
        "last_frame_age_s": stats["last_frame_age_s"],
        "frames_rx_ok": int(stats["frames_rx_ok"]),
        "frames_rx_bad": int(stats["frames_rx_bad"]),
    }


def _render_synthetic_frame(now_monotonic_s: float) -> bytes:
    generator = _synthetic_jpeg_generator
    if generator is None:
        generator = SyntheticJpegGenerator(width=640, height=360)

    link_status = state.get_link_status(
        vis_fresh_s=_runtime_vis_fresh_s,
        tel_fresh_s=_runtime_tel_fresh_s,
        cmd_timeout_s=CMD_TIMEOUT_S,
        cmd_hz=_runtime_cmd_hz,
        tel_hz=_runtime_tel_hz,
        now_monotonic_s=now_monotonic_s,
    )
    fc_connected = bool(link_status.get("fc_connected"))
    vis_age_s = link_status.get("vis_age_s")
    vis_age_label = f"{float(vis_age_s):.2f}s" if isinstance(vis_age_s, int | float) else "n/a"

    return generator.render(
        now_monotonic_s=now_monotonic_s,
        lines=[
            f"fc_connected={fc_connected}",
            f"vis_age_s={vis_age_label}",
        ],
    )


def _looks_like_jpeg(payload: bytes) -> bool:
    return len(payload) >= 4 and payload.startswith(b"\xff\xd8") and payload.endswith(b"\xff\xd9")


def _build_tel_ingestor_from_env(
    on_valid: Callable[[dict[str, Any], float], None] | None = None,
    on_drop: Callable[[str, str], None] | None = None,
) -> TelUdpIngestor:
    host = os.environ.get("BACKEND_TEL_HOST", "127.0.0.1")
    port = _read_env_int("BACKEND_TEL_PORT", UDP_TEL_PORT)
    max_bytes = _read_env_int("BACKEND_TEL_MAX_BYTES", UDP_MAX_TEL_BYTES)
    recv_timeout_s = _read_env_float("BACKEND_TEL_RECV_TIMEOUT_S", 0.1)
    log_interval_s = _read_env_float("BACKEND_TEL_LOG_INTERVAL_S", 5.0)
    return TelUdpIngestor(
        state=state,
        bind_host=host,
        port=port,
        max_bytes=max_bytes,
        recv_timeout_s=recv_timeout_s,
        log_interval_s=log_interval_s,
        on_valid=on_valid,
        on_drop=on_drop,
    )


def _build_vis_ingestor_from_env(
    on_valid: Callable[[dict[str, Any], float], None] | None = None,
    on_drop: Callable[[str, str], None] | None = None,
) -> VisUdpIngestor:
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
        on_valid=on_valid,
        on_drop=on_drop,
    )


def _build_cmd_bridge_from_env(
    cmd_hz: float,
    vis_fresh_s: float,
    on_connection_change: Callable[[bool], None] | None = None,
    on_tracking_blocked: Callable[[str], None] | None = None,
) -> CmdBridge:
    fc_host = os.environ.get("BACKEND_FC_HOST", "127.0.0.1")
    fc_port = _read_env_int("BACKEND_FC_PORT", TCP_CMD_PORT)
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
        on_connection_change=on_connection_change,
        on_tracking_blocked=on_tracking_blocked,
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


def _read_env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean env var for {name}: {raw!r}")
