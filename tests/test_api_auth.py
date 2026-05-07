"""Bearer-token auth on /api/intent, /api/frame, and the /ws upgrade.

When `BACKEND_API_TOKEN` is unset, all three remain unauthenticated for local
dev (matched by the existing test suite). When it is set, requests without a
matching Authorization header (REST) or `aot.bearer.<token>` Sec-WebSocket-
Protocol subprotocol (WS) must be rejected before any work is done.
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.backend.app import app
from tests.ws_test_utils import configure_backend_ws_test_env, require_udp_bind_or_skip

_TOKEN = "test-bearer-token-abc123"
_WRONG_TOKEN = "test-bearer-token-wrong"

# Minimal valid JPEG (same fixture as test_video_ingest_endpoint.py).
_VALID_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEm"
    "KzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7"
    "Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAABAAEDASIAAhEBAxEB/8QA"
    "HwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIh"
    "MUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVW"
    "V1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQF"
    "BgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAV"
    "YnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE"
    "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq"
    "8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD2aiiigD//2Q=="
)


def _enable_auth(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_API_TOKEN", _TOKEN)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")


def test_intent_rejects_request_without_token(monkeypatch) -> None:
    _enable_auth(monkeypatch)
    with TestClient(app) as client:
        response = client.post("/api/intent", json={"desired_mode": 0})
        assert response.status_code == 401


def test_intent_rejects_request_with_wrong_token(monkeypatch) -> None:
    _enable_auth(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/intent",
            json={"desired_mode": 0},
            headers={"Authorization": f"Bearer {_WRONG_TOKEN}"},
        )
        assert response.status_code == 401


def test_intent_accepts_request_with_correct_token(monkeypatch) -> None:
    _enable_auth(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/intent",
            json={"desired_mode": 0},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == 200


def test_frame_rejects_request_without_token(monkeypatch) -> None:
    _enable_auth(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/frame",
            content=_VALID_JPEG,
            headers={"content-type": "image/jpeg"},
        )
        assert response.status_code == 401


def test_frame_accepts_request_with_correct_token(monkeypatch) -> None:
    _enable_auth(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/frame",
            content=_VALID_JPEG,
            headers={
                "content-type": "image/jpeg",
                "Authorization": f"Bearer {_TOKEN}",
            },
        )
        assert response.status_code == 200


def test_ws_rejects_upgrade_without_subprotocol(monkeypatch) -> None:
    # Auth on, client offers no subprotocol => reject pre-accept.
    _enable_auth(monkeypatch)
    with TestClient(app) as client:
        try:
            with client.websocket_connect("/ws"):
                raise AssertionError("expected ws upgrade to be rejected")
        except WebSocketDisconnect as exc:
            # Pre-accept close => 4401 surfaces as the disconnect code.
            assert exc.code == 4401


def test_ws_rejects_upgrade_with_wrong_token(monkeypatch) -> None:
    _enable_auth(monkeypatch)
    with TestClient(app) as client:
        try:
            with client.websocket_connect("/ws", subprotocols=[f"aot.bearer.{_WRONG_TOKEN}"]):
                raise AssertionError("expected ws upgrade to be rejected")
        except WebSocketDisconnect as exc:
            assert exc.code == 4401


def test_ws_rejects_upgrade_with_unrelated_subprotocol(monkeypatch) -> None:
    # Auth on, client offers a non-bearer subprotocol => still reject.
    _enable_auth(monkeypatch)
    with TestClient(app) as client:
        try:
            with client.websocket_connect("/ws", subprotocols=["chat.v1"]):
                raise AssertionError("expected ws upgrade to be rejected")
        except WebSocketDisconnect as exc:
            assert exc.code == 4401


def test_ws_accepts_upgrade_with_correct_token(monkeypatch) -> None:
    _enable_auth(monkeypatch)
    with TestClient(app) as client:
        with client.websocket_connect("/ws", subprotocols=[f"aot.bearer.{_TOKEN}"]) as ws:
            # First envelope is LINK_STATUS sent immediately on accept.
            envelope = ws.receive_json()
            assert envelope["event"] == "LINK_STATUS"


def test_auth_disabled_when_token_unset(monkeypatch) -> None:
    # Default behaviour: env unset => no token required (preserves local dev
    # ergonomics; operator is expected to bind 127.0.0.1).
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.delenv("BACKEND_API_TOKEN", raising=False)
    with TestClient(app) as client:
        response = client.post("/api/intent", json={"desired_mode": 0})
        assert response.status_code == 200
        with client.websocket_connect("/ws") as ws:
            envelope = ws.receive_json()
            assert envelope["event"] == "LINK_STATUS"
