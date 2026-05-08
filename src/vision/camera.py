from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceSpec:
    kind: str
    value: str

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.value}"


def parse_source(source: str) -> SourceSpec:
    raw = source.strip()
    if not raw:
        raise ValueError("source must not be empty")

    if ":" not in raw:
        return SourceSpec(kind="file", value=raw)

    kind, value = raw.split(":", 1)
    kind = kind.strip().lower()
    value = value.strip()

    if kind == "webcam":
        if not value:
            raise ValueError("webcam source must include an integer index, for example webcam:0")
        return SourceSpec(kind="webcam", value=str(int(value)))
    if kind == "file":
        if not value:
            raise ValueError("file source must include a path, for example file:assets/test.mp4")
        return SourceSpec(kind="file", value=value)
    raise ValueError("unsupported source kind; use webcam:<index> or file:<path>")


# Backoff schedule for read-failure recovery: start short so transient
# blips clear quickly, cap at 5 s so we don't busy-loop on a permanently-
# failed source. The pattern is each failure doubles the wait until the
# cap; a successful read resets it back to the minimum.
_DEFAULT_RECONNECT_INITIAL_S = 0.25
_DEFAULT_RECONNECT_MAX_S = 5.0

# After a successful (re)open, OpenCV's first few frames from a webcam
# are sometimes black or partially-decoded as the driver settles. Tolerate
# this many opaque False/empty reads before treating the source as failed
# again — otherwise a perfectly-recovered camera would re-trigger the
# reconnect loop on its first frame.
_DEFAULT_BLACK_FRAMES_AFTER_OPEN = 5


class CameraSource:
    def __init__(
        self,
        spec: SourceSpec,
        *,
        reconnect_initial_s: float = _DEFAULT_RECONNECT_INITIAL_S,
        reconnect_max_s: float = _DEFAULT_RECONNECT_MAX_S,
        black_frames_after_open: int = _DEFAULT_BLACK_FRAMES_AFTER_OPEN,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.spec = spec
        self._reconnect_initial_s = max(0.0, float(reconnect_initial_s))
        self._reconnect_max_s = max(self._reconnect_initial_s, float(reconnect_max_s))
        self._black_frames_after_open = max(0, int(black_frames_after_open))
        self._sleep = sleep
        self._capture = self._open_capture(spec)
        # Tolerance budget consumed by the post-open warmup window — set
        # whenever we (re)open the source.
        self._post_open_grace_remaining = self._black_frames_after_open
        # Backoff state survives across read() calls so a permanently-failed
        # source escalates correctly even though each call attempts at most
        # one reconnect.
        self._next_backoff_s = self._reconnect_initial_s

    @property
    def src_label(self) -> str:
        return self.spec.label

    def _open_capture(self, spec: SourceSpec) -> Any:
        import cv2

        if spec.kind == "webcam":
            capture = cv2.VideoCapture(int(spec.value))
        else:
            capture = cv2.VideoCapture(spec.value)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"unable to open source '{spec.label}'")
        return capture

    def read(self) -> tuple[bool, np.ndarray | None]:
        ok, frame = self._capture.read()
        if ok and frame is not None:
            self._post_open_grace_remaining = self._black_frames_after_open
            self._next_backoff_s = self._reconnect_initial_s
            return True, frame

        # Empty / False inside the post-open warmup window: tolerate it as a
        # "the camera driver is still settling" signal without tearing down
        # the capture. Returns False so the caller skips this frame and
        # tries again on the next tick.
        if self._post_open_grace_remaining > 0:
            self._post_open_grace_remaining -= 1
            LOGGER.debug(
                "%s: empty frame during post-open warmup (remaining=%d)",
                self.spec.label,
                self._post_open_grace_remaining,
            )
            return False, None

        # Genuine read failure: try ONE release+reopen with the current
        # backoff. Returns False if the reopen fails or the post-open read
        # is empty; the caller is expected to call read() again on the next
        # tick, which will retry with a doubled backoff. Putting the loop
        # outside read() keeps the call non-blocking from the main loop's
        # perspective — long-tail failures don't stall the caller indefinitely.
        return self._attempt_one_reconnect()

    def _attempt_one_reconnect(self) -> tuple[bool, np.ndarray | None]:
        backoff_s = self._next_backoff_s
        LOGGER.warning(
            "%s: read failed; reconnect attempt with backoff=%.2fs", self.spec.label, backoff_s
        )

        try:
            self._capture.release()
        except Exception:  # pragma: no cover — release is best-effort
            pass

        if backoff_s > 0.0:
            self._sleep(backoff_s)

        try:
            self._capture = self._open_capture(self.spec)
        except RuntimeError as exc:
            LOGGER.warning("%s: reopen failed: %s", self.spec.label, exc)
            self._next_backoff_s = min(self._reconnect_max_s, max(backoff_s * 2.0, 0.05))
            return False, None

        self._post_open_grace_remaining = self._black_frames_after_open
        ok, frame = self._capture.read()
        if ok and frame is not None:
            LOGGER.info("%s: reconnected", self.spec.label)
            self._next_backoff_s = self._reconnect_initial_s
            return True, frame

        # Reopen succeeded but first read empty — fall through to grace on
        # the next read() call. Don't reset backoff yet (the camera may still
        # be unstable).
        if self._post_open_grace_remaining > 0:
            self._post_open_grace_remaining -= 1
        self._next_backoff_s = min(self._reconnect_max_s, max(backoff_s * 2.0, 0.05))
        return False, None

    def release(self) -> None:
        self._capture.release()

    @classmethod
    def open(cls, source: str) -> CameraSource:
        return cls(parse_source(source))
