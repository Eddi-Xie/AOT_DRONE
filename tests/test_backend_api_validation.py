from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.requests import Request

from src.backend.app import app


def _configure_no_network_runtime(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_TEL_INGEST_ENABLED", "0")
    monkeypatch.setenv("BACKEND_VIS_INGEST_ENABLED", "0")
    monkeypatch.setenv("BACKEND_CMD_BRIDGE_ENABLED", "0")


def test_status_vis_rejects_negative_threshold(monkeypatch) -> None:
    _configure_no_network_runtime(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/status/vis", params={"connected_threshold_s": -0.01})

    assert response.status_code == 422


def test_frame_ingest_rejects_oversize_content_length_before_body_read(monkeypatch) -> None:
    _configure_no_network_runtime(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.setenv("BACKEND_VIDEO_MAX_JPEG_BYTES", "64")

    async def _fail_if_body_read(
        self,
    ) -> bytes:  # pragma: no cover - enforced by assertion behavior
        raise AssertionError("request body must not be read for oversize content-length")

    monkeypatch.setattr(Request, "body", _fail_if_body_read, raising=True)

    oversize_payload = b"\xff\xd8" + (b"x" * 80) + b"\xff\xd9"
    with TestClient(app) as client:
        response = client.post(
            "/api/frame",
            content=oversize_payload,
            headers={"content-type": "image/jpeg"},
        )

    assert response.status_code == 400
    assert "content-length exceeds max size" in response.text


def test_frame_ingest_rejects_malformed_content_length(monkeypatch) -> None:
    """Non-numeric Content-Length must 400 with the malformed-header message
    and must not consume the body."""
    _configure_no_network_runtime(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.setenv("BACKEND_VIDEO_MAX_JPEG_BYTES", "1024")

    async def _fail_if_body_read(
        self,
    ) -> bytes:  # pragma: no cover - enforced by assertion behavior
        raise AssertionError("request body must not be read for malformed content-length")

    monkeypatch.setattr(Request, "body", _fail_if_body_read, raising=True)

    payload = b"\xff\xd8\xff\xd9"
    with TestClient(app) as client:
        response = client.post(
            "/api/frame",
            content=payload,
            headers={
                "content-type": "image/jpeg",
                "content-length": "not-a-number",
            },
        )

    assert response.status_code == 400
    assert "invalid content-length header" in response.text


def test_frame_ingest_rejects_negative_content_length(monkeypatch) -> None:
    """Negative Content-Length must 400 with the malformed-header message
    and must not consume the body."""
    _configure_no_network_runtime(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.setenv("BACKEND_VIDEO_MAX_JPEG_BYTES", "1024")

    async def _fail_if_body_read(
        self,
    ) -> bytes:  # pragma: no cover - enforced by assertion behavior
        raise AssertionError("request body must not be read for negative content-length")

    monkeypatch.setattr(Request, "body", _fail_if_body_read, raising=True)

    payload = b"\xff\xd8\xff\xd9"
    with TestClient(app) as client:
        response = client.post(
            "/api/frame",
            content=payload,
            headers={
                "content-type": "image/jpeg",
                "content-length": "-5",
            },
        )

    assert response.status_code == 400
    assert "invalid content-length header" in response.text


def test_frame_ingest_rejects_empty_content_length(monkeypatch) -> None:
    """Present-but-empty Content-Length (e.g. the literal value "" or pure
    whitespace) must 400 with the malformed-header message and must not
    consume the body. Without this guard, the empty header silently
    falls through to await request.body() and bypasses the early reject."""
    _configure_no_network_runtime(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.setenv("BACKEND_VIDEO_MAX_JPEG_BYTES", "1024")

    async def _fail_if_body_read(
        self,
    ) -> bytes:  # pragma: no cover - enforced by assertion behavior
        raise AssertionError("request body must not be read for empty content-length")

    monkeypatch.setattr(Request, "body", _fail_if_body_read, raising=True)

    payload = b"\xff\xd8\xff\xd9"
    with TestClient(app) as client:
        response = client.post(
            "/api/frame",
            content=payload,
            headers={
                "content-type": "image/jpeg",
                "content-length": "   ",
            },
        )

    assert response.status_code == 400
    assert "invalid content-length header" in response.text
