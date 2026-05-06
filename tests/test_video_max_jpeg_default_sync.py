"""Regression tests for the wire-contract default that backend and vision
must share for the /api/frame JPEG ingest cap.

The backend's /api/frame endpoint and the vision-side JPEG encoder both
default to BACKEND_VIDEO_MAX_JPEG_BYTES. If one side defaults to a
different value than the other, vision either produces frames the backend
rejects (oversize) or backend silently accepts frames the encoder will
never produce. We collapsed both sides onto a single source of truth in
src.backend.protocol_constants.VIDEO_MAX_JPEG_BYTES_DEFAULT; these tests
fail loudly if a future re-hardcode breaks that contract.

Important: each test exercises the *actual production code path*:
- backend: drives a TestClient(app) lifecycle so _startup() re-reads the
  env, mirroring how uvicorn boots in production. A simple
  module-global assertion would be order-dependent because earlier tests
  in the suite drive _startup() with explicit BACKEND_VIDEO_MAX_JPEG_BYTES
  overrides and _shutdown() does not restore the import-time default.
- vision: drives parse_args() (the entry point hit by
  `python -m src.vision.main`), not just the dataclass default.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.backend.protocol_constants import VIDEO_MAX_JPEG_BYTES_DEFAULT


def _delenv(monkeypatch, name: str) -> None:
    monkeypatch.delenv(name, raising=False)


def _disable_backend_network(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_TEL_INGEST_ENABLED", "0")
    monkeypatch.setenv("BACKEND_VIS_INGEST_ENABLED", "0")
    monkeypatch.setenv("BACKEND_CMD_BRIDGE_ENABLED", "0")


def test_backend_startup_uses_protocol_constant_when_env_unset(monkeypatch) -> None:
    """Drives the *actual* FastAPI startup path via TestClient(app).

    With BACKEND_VIDEO_MAX_JPEG_BYTES unset, _startup() must initialise the
    runtime cap from the shared protocol constant. Asserting on the
    module-level _runtime_video_max_jpeg_bytes alone would be brittle:
    other tests in the suite mutate it via env overrides and _shutdown()
    does not restore the import-time default.
    """
    from src.backend import app as backend_app

    _disable_backend_network(monkeypatch)
    _delenv(monkeypatch, "BACKEND_VIDEO_MAX_JPEG_BYTES")

    with TestClient(backend_app.app):
        # _startup() has run; _runtime_video_max_jpeg_bytes is freshly
        # populated from the (absent) env override and must equal the
        # protocol-constant default.
        assert backend_app._runtime_video_max_jpeg_bytes == VIDEO_MAX_JPEG_BYTES_DEFAULT


def test_backend_startup_respects_explicit_env_override(monkeypatch) -> None:
    """Counterpart sanity check: an explicit env override is honoured (so
    the previous test isn't accidentally satisfied by the protocol-constant
    value happening to equal whatever a prior test left in the global)."""
    from src.backend import app as backend_app

    _disable_backend_network(monkeypatch)
    monkeypatch.setenv("BACKEND_VIDEO_MAX_JPEG_BYTES", "12345")

    with TestClient(backend_app.app):
        assert backend_app._runtime_video_max_jpeg_bytes == 12345


def test_vision_parse_args_uses_protocol_constant_when_env_unset(monkeypatch) -> None:
    """Drives the *actual* CLI entry path: python -m src.vision.main.

    With BACKEND_VIDEO_MAX_JPEG_BYTES unset, parse_args() must produce a
    VisionConfig whose backend_video_max_jpeg_bytes equals the shared
    constant. Just calling VisionConfig() (the previous test) doesn't
    cover a future re-hardcode inside parse_args itself.
    """
    from src.vision.main import parse_args

    _delenv(monkeypatch, "BACKEND_VIDEO_MAX_JPEG_BYTES")

    config = parse_args([])
    assert config.backend_video_max_jpeg_bytes == VIDEO_MAX_JPEG_BYTES_DEFAULT


def test_vision_parse_args_respects_explicit_env_override(monkeypatch) -> None:
    """Counterpart sanity check for parse_args: an explicit env override
    propagates into the produced VisionConfig."""
    from src.vision.main import parse_args

    monkeypatch.setenv("BACKEND_VIDEO_MAX_JPEG_BYTES", "67890")

    config = parse_args([])
    assert config.backend_video_max_jpeg_bytes == 67890


def test_vision_config_default_matches_protocol_constant() -> None:
    """Static dataclass default also tracks the shared constant. This
    catches the simpler regression where someone bypasses parse_args and
    constructs VisionConfig() directly somewhere new."""
    from src.vision.main import VisionConfig

    assert VisionConfig().backend_video_max_jpeg_bytes == VIDEO_MAX_JPEG_BYTES_DEFAULT


def test_frame_pusher_default_matches_protocol_constant() -> None:
    """FramePusher() with no args must default to the shared constant so
    any caller constructing it without explicit max_jpeg_bytes still
    matches the backend cap."""
    from src.vision.frame_pusher import FramePusher

    pusher = FramePusher()
    try:
        assert pusher.max_jpeg_bytes == VIDEO_MAX_JPEG_BYTES_DEFAULT
    finally:
        pusher.close()
