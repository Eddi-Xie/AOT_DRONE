from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from src.backend.app import app
from tests.ws_test_utils import configure_backend_ws_test_env, require_udp_bind_or_skip

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


def test_video_ingest_accepts_valid_jpeg(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.setenv("BACKEND_VIDEO_MAX_JPEG_BYTES", "200000")

    with TestClient(app) as client:
        response = client.post(
            "/api/frame",
            data=_VALID_JPEG,
            headers={"content-type": "image/jpeg"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["size_bytes"] == len(_VALID_JPEG)

        status = client.get("/api/status")
        assert status.status_code == 200
        payload = status.json()
        assert payload["frames_rx_ok"] >= 1
        assert payload["frames_rx_bad"] == 0


def test_video_ingest_rejects_oversize_and_invalid_magic(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.setenv("BACKEND_VIDEO_MAX_JPEG_BYTES", "64")

    oversize_payload = b"\xff\xd8" + (b"x" * 80) + b"\xff\xd9"

    with TestClient(app) as client:
        oversize_response = client.post(
            "/api/frame",
            data=oversize_payload,
            headers={"content-type": "image/jpeg"},
        )
        assert oversize_response.status_code == 400

        invalid_response = client.post(
            "/api/frame",
            data=b"not-a-jpeg",
            headers={"content-type": "image/jpeg"},
        )
        assert invalid_response.status_code == 400

        status = client.get("/api/status")
        assert status.status_code == 200
        payload = status.json()
        assert payload["frames_rx_bad"] >= 2


def test_video_ingest_decode_validation_toggle(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.setenv("BACKEND_VIDEO_VALIDATE_DECODE", "1")

    fake_jpeg = b"\xff\xd8" + (b"invalid-jpeg-data" * 4) + b"\xff\xd9"

    with TestClient(app) as client:
        response = client.post(
            "/api/frame",
            data=fake_jpeg,
            headers={"content-type": "image/jpeg"},
        )
        assert response.status_code == 400
        assert "decode validation failed" in response.text


def test_frame_ingest_rejects_chunked_header(monkeypatch) -> None:
    # Sending Transfer-Encoding: chunked must be rejected up-front. The
    # production hazard is an attacker bypassing the JPEG-byte cap by streaming
    # an unbounded body with no Content-Length advertised. We reject before any
    # body read happens.
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.setenv("BACKEND_VIDEO_MAX_JPEG_BYTES", "200000")

    with TestClient(app) as client:
        response = client.post(
            "/api/frame",
            content=_VALID_JPEG,
            headers={
                "content-type": "image/jpeg",
                "transfer-encoding": "chunked",
            },
        )
        assert response.status_code == 400
        assert "chunked" in response.text.lower()


def test_frame_ingest_rejects_chunked_streaming_body(monkeypatch) -> None:
    # When the client sends an iterable body, httpx switches to chunked
    # transfer encoding. We must reject before consuming the iterator past the
    # first chunk so the worker never buffers an unbounded amount.
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")
    monkeypatch.setenv("BACKEND_VIDEO_MAX_JPEG_BYTES", "200000")

    yielded: list[int] = []

    def chunk_generator():
        # Each chunk is well-formed JPEG-ish but cumulatively unbounded if
        # someone kept pulling. We track yields to assert the server didn't
        # consume the whole stream before responding.
        for i in range(1000):
            yielded.append(i)
            yield b"\xff\xd8" + (b"x" * 1024) + b"\xff\xd9"

    with TestClient(app) as client:
        response = client.post(
            "/api/frame",
            content=chunk_generator(),
            headers={"content-type": "image/jpeg"},
        )
        assert response.status_code == 400
        # Even if httpx eagerly drained a few chunks before the server's 400
        # propagated, it must not have consumed all 1000.
        assert len(yielded) < 1000


def test_frame_ingest_requires_content_length(monkeypatch) -> None:
    # With chunked already rejected, a request without Content-Length leaves
    # the body unbounded. We refuse it with 411 rather than falling through to
    # an unbounded body read.
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "1")

    with TestClient(app) as client:
        # Build a request that explicitly omits Content-Length. httpx normally
        # adds it from the body length, so we send empty bytes and pop the
        # header on the prepared request.
        request = client.build_request(
            "POST",
            "/api/frame",
            content=b"",
            headers={"content-type": "image/jpeg"},
        )
        request.headers.pop("content-length", None)
        response = client.send(request)
        assert response.status_code == 411


def test_video_status_fields_present_when_video_disabled(monkeypatch) -> None:
    require_udp_bind_or_skip()
    configure_backend_ws_test_env(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_ENABLED", "0")

    with TestClient(app) as client:
        status = client.get("/api/status")
        assert status.status_code == 200
        payload = status.json()

    assert payload["video_enabled"] is False
    assert isinstance(payload["video_clients"], int)
    assert isinstance(payload["video_fps_est"], float)
    assert "last_frame_age_s" in payload
    assert isinstance(payload["frames_rx_ok"], int)
    assert isinstance(payload["frames_rx_bad"], int)
