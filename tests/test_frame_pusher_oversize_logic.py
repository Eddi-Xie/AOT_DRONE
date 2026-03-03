from __future__ import annotations

import numpy as np

from src.vision.frame_pusher import FramePusher


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _FakeHttpClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, bytes, dict[str, str]]] = []

    def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> _FakeResponse:
        self.posts.append((url, content, headers))
        return _FakeResponse(status_code=200)

    def close(self) -> None:
        return None


def test_frame_pusher_drops_oversize_jpeg_before_http() -> None:
    client = _FakeHttpClient()
    pusher = FramePusher(
        backend_http="http://127.0.0.1:8000",
        frame_endpoint="/api/frame",
        max_jpeg_bytes=5,
        http_client=client,
    )

    ok = pusher.push(b"0123456789")

    assert ok is False
    assert pusher.dropped_oversize == 1
    assert pusher.push_ok == 0
    assert pusher.push_fail == 0
    assert client.posts == []


def test_frame_pusher_retries_quality_once_before_post() -> None:
    client = _FakeHttpClient()
    encode_calls: list[int] = []

    def fake_encoder(frame: np.ndarray, quality: int) -> bytes:
        del frame
        encode_calls.append(quality)
        if len(encode_calls) == 1:
            return b"x" * 12
        return b"x" * 6

    pusher = FramePusher(
        backend_http="http://127.0.0.1:8000",
        frame_endpoint="/api/frame",
        max_jpeg_bytes=8,
        http_client=client,
        jpeg_encoder=fake_encoder,
    )
    frame = np.zeros((2, 2, 3), dtype=np.uint8)

    ok = pusher.push_frame(frame=frame, quality=80)

    assert ok is True
    assert encode_calls == [80, 60]
    assert pusher.push_ok == 1
    assert pusher.push_fail == 0
    assert pusher.dropped_oversize == 0
    assert len(client.posts) == 1
    _, content, headers = client.posts[0]
    assert content == (b"x" * 6)
    assert headers["Content-Type"] == "image/jpeg"
