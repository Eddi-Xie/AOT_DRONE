"""Regression tests for the wire-contract default that backend and vision
must share for the /api/frame JPEG ingest cap.

The backend's /api/frame endpoint and the vision-side JPEG encoder both
default to BACKEND_VIDEO_MAX_JPEG_BYTES. If one side defaults to a
different value than the other, vision either produces frames the backend
rejects (oversize) or backend silently accepts frames the encoder will
never produce. We collapsed both sides onto a single source of truth in
src.backend.protocol_constants.VIDEO_MAX_JPEG_BYTES_DEFAULT; this test
fails loudly if a future re-hardcode breaks that contract.
"""

from __future__ import annotations

from src.backend.protocol_constants import VIDEO_MAX_JPEG_BYTES_DEFAULT


def test_backend_runtime_default_matches_protocol_constant() -> None:
    """src.backend.app initialises its runtime cap from the same constant.

    Imports the module-level _runtime_video_max_jpeg_bytes (set before
    _startup() reads the env override) and asserts it equals the constant.
    """
    from src.backend import app as backend_app

    assert backend_app._runtime_video_max_jpeg_bytes == VIDEO_MAX_JPEG_BYTES_DEFAULT


def test_vision_config_default_matches_protocol_constant() -> None:
    """src.vision.main.VisionConfig.backend_video_max_jpeg_bytes default
    pulls from the same constant — drift between vision and backend would
    cause the backend to reject frames the encoder produces.
    """
    from src.vision.main import VisionConfig

    assert VisionConfig().backend_video_max_jpeg_bytes == VIDEO_MAX_JPEG_BYTES_DEFAULT


def test_frame_pusher_default_matches_protocol_constant() -> None:
    """FramePusher() with no args must default to the shared constant so
    any caller that constructs it without explicit max_jpeg_bytes still
    matches the backend cap.
    """
    from src.vision.frame_pusher import FramePusher

    pusher = FramePusher()
    try:
        assert pusher.max_jpeg_bytes == VIDEO_MAX_JPEG_BYTES_DEFAULT
    finally:
        pusher.close()
