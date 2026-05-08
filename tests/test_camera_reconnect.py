"""Reconnect + black-frame-tolerance contract for vision/camera.py.

The previous CameraSource exited on the first `cv2.VideoCapture.read()`
False return, so a momentary USB-cam blip would crash the vision
process. Now `read()` performs ONE release+reopen attempt per call with
exponential backoff (0.25s -> 5s cap), and a post-open grace window
absorbs the first 5 black/empty frames so a fresh reconnect's settling
period doesn't immediately re-trigger the failure path.

These tests mock `cv2.VideoCapture` via a fake object stub so we can
script the read sequence deterministically. We never touch real hardware.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from src.vision.camera import CameraSource, SourceSpec


class _FakeCapture:
    """Scriptable stand-in for `cv2.VideoCapture`.

    `read_results` is a list of `(ok, frame_or_None)` tuples consumed in
    order. After the list runs out, the capture returns `(False, None)`
    indefinitely — useful when the test only cares about the first N reads.

    `is_opened_initial` lets us simulate a failed open (capture.isOpened()
    returning False), which CameraSource treats as a permanent failure
    on construction but a recoverable failure on reconnect (the latter
    exception is caught and translated into an extended backoff).
    """

    def __init__(
        self,
        read_results: list[tuple[bool, Any]],
        is_opened_initial: bool = True,
    ) -> None:
        self._read_results = list(read_results)
        self._is_opened = is_opened_initial
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 — cv2 API
        return self._is_opened

    def read(self) -> tuple[bool, Any]:
        if not self._read_results:
            return False, None
        return self._read_results.pop(0)

    def release(self) -> None:
        self.released = True
        self._is_opened = False


def _install_fake_cv2(
    monkeypatch: pytest.MonkeyPatch, factory: Callable[..., _FakeCapture]
) -> None:
    """Install a fake `cv2` module whose `VideoCapture(arg)` calls `factory(arg)`.

    Reaches into sys.modules so the `import cv2` inside CameraSource picks
    up the fake. Must be called before any CameraSource construction.
    """
    fake_cv2 = types.SimpleNamespace(VideoCapture=factory)
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)  # type: ignore[arg-type]


def _silent_sleep(_s: float) -> None:
    pass


def test_read_recovers_from_transient_read_failure(monkeypatch) -> None:
    """First read fails => reconnect attempt; second read on the (re)opened
    capture returns a real frame.
    """
    real_frame = np.zeros((4, 4, 3), dtype=np.uint8)
    captures: list[_FakeCapture] = []

    def factory(_arg: Any) -> _FakeCapture:
        # First open: returns False on first read.
        # Second open (reconnect): returns the real frame.
        if not captures:
            cap = _FakeCapture(read_results=[(False, None)])
        else:
            cap = _FakeCapture(read_results=[(True, real_frame)])
        captures.append(cap)
        return cap

    _install_fake_cv2(monkeypatch, factory)

    src = CameraSource(
        SourceSpec(kind="webcam", value="0"),
        reconnect_initial_s=0.0,
        reconnect_max_s=0.0,
        black_frames_after_open=0,  # disable grace so the test isolates the reconnect path
        sleep=_silent_sleep,
    )

    ok, frame = src.read()
    assert ok is True
    assert frame is real_frame
    assert len(captures) == 2  # one initial open + one reconnect
    assert captures[0].released is True


def test_read_tolerates_post_open_black_frames(monkeypatch) -> None:
    """First N reads after construction can be False without triggering reconnect."""
    real_frame = np.zeros((4, 4, 3), dtype=np.uint8)
    captures: list[_FakeCapture] = []

    def factory(_arg: Any) -> _FakeCapture:
        # Initial open: 3 empty frames then a real one.
        cap = _FakeCapture(
            read_results=[
                (False, None),
                (False, None),
                (False, None),
                (True, real_frame),
            ]
        )
        captures.append(cap)
        return cap

    _install_fake_cv2(monkeypatch, factory)

    src = CameraSource(
        SourceSpec(kind="webcam", value="0"),
        reconnect_initial_s=0.0,
        reconnect_max_s=0.0,
        black_frames_after_open=5,
        sleep=_silent_sleep,
    )

    # First 3 reads consume grace + return False; no reconnect triggered.
    for _ in range(3):
        ok, frame = src.read()
        assert ok is False
        assert frame is None
    assert len(captures) == 1  # still on the original capture; no reopen

    # Fourth read returns the real frame and resets grace.
    ok, frame = src.read()
    assert ok is True
    assert frame is real_frame


def test_read_returns_false_when_reopen_fails(monkeypatch) -> None:
    """A reopen that raises (capture.isOpened()=False) leaves the source in a
    state where the next read() retries — does not propagate the exception."""
    captures: list[_FakeCapture] = []

    def factory(_arg: Any) -> _FakeCapture:
        if not captures:
            cap = _FakeCapture(read_results=[(False, None)])
        else:
            # Reopen fails (isOpened returns False).
            cap = _FakeCapture(read_results=[], is_opened_initial=False)
        captures.append(cap)
        return cap

    _install_fake_cv2(monkeypatch, factory)

    src = CameraSource(
        SourceSpec(kind="webcam", value="0"),
        reconnect_initial_s=0.1,
        reconnect_max_s=2.0,
        black_frames_after_open=0,
        sleep=_silent_sleep,
    )

    initial_backoff = src._next_backoff_s
    ok, frame = src.read()
    assert ok is False
    assert frame is None
    # Backoff escalates for the next attempt without raising; the source
    # remains usable so the caller can retry on the next tick.
    assert src._next_backoff_s > initial_backoff


def test_backoff_resets_after_successful_read(monkeypatch) -> None:
    """A successful read after a reconnect resets the next-backoff to initial."""
    real_frame = np.zeros((4, 4, 3), dtype=np.uint8)
    captures: list[_FakeCapture] = []

    def factory(_arg: Any) -> _FakeCapture:
        if not captures:
            cap = _FakeCapture(read_results=[(False, None)])
        else:
            cap = _FakeCapture(read_results=[(True, real_frame)])
        captures.append(cap)
        return cap

    _install_fake_cv2(monkeypatch, factory)

    src = CameraSource(
        SourceSpec(kind="webcam", value="0"),
        reconnect_initial_s=0.25,
        reconnect_max_s=5.0,
        black_frames_after_open=0,
        sleep=_silent_sleep,
    )

    ok, _ = src.read()
    assert ok is True
    assert src._next_backoff_s == pytest.approx(0.25)
