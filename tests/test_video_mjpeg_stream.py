from __future__ import annotations

from fastapi.testclient import TestClient

from src.backend.app import app
from src.backend.video_hub import make_mjpeg_part
from tests.ws_test_utils import configure_backend_ws_test_env, require_udp_bind_or_skip


def test_video_stream_returns_mjpeg_part(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.setenv("BACKEND_VIDEO_FPS", "5")

    with TestClient(app) as client:
        response = client.get("/video", params={"max_parts": 1})
        assert response.status_code == 200
        content_type = response.headers.get("content-type", "")
        assert content_type.startswith("multipart/x-mixed-replace")
        assert (
            response.headers.get("cache-control")
            == "no-store, no-cache, must-revalidate, max-age=0"
        )
        assert response.headers.get("pragma") == "no-cache"
        assert response.headers.get("expires") == "0"
        buffer = response.content

    assert b"--frame" in buffer
    assert b"Content-Type: image/jpeg" in buffer
    assert b"Content-Length:" in buffer


def test_make_mjpeg_part_format() -> None:
    frame = b"\xff\xd8jpeg\xff\xd9"
    part = make_mjpeg_part(frame, boundary="frame")

    assert part.startswith(b"--frame\r\n")
    assert b"Content-Type: image/jpeg\r\n" in part
    assert part.endswith(b"\xff\xd9\r\n")
