from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .types import Detection, NormalizedBBox, VisState, clamp01, normalize_bbox_xywh


@dataclass(frozen=True)
class TrackerResult:
    state: VisState
    bbox: NormalizedBBox
    conf: float
    track_id: int


class SimpleTracker:
    """Deterministic tracker/state-machine scaffold for PR-V1."""

    def __init__(self, detect_hold_n: int = 30, search_n: int = 0) -> None:
        if detect_hold_n < 0:
            raise ValueError("detect_hold_n must be >= 0")
        if search_n < 0:
            raise ValueError("search_n must be >= 0")
        self.detect_hold_n = detect_hold_n
        self.search_n = search_n
        self._state = VisState.NO_TARGET
        self._detect_streak = 0
        self._missing_streak = 0
        self._last_bbox = NormalizedBBox.zero()
        self._last_conf = 0.0
        self._last_track_id = 0

    @property
    def state(self) -> VisState:
        return self._state

    def reset(self) -> None:
        self._state = VisState.NO_TARGET
        self._detect_streak = 0
        self._missing_streak = 0
        self._last_bbox = NormalizedBBox.zero()
        self._last_conf = 0.0
        self._last_track_id = 0

    def update(self, detections: Sequence[Detection], img_w: int, img_h: int) -> TrackerResult:
        if detections:
            return self._update_with_detection(detections[0], img_w=img_w, img_h=img_h)
        return self._update_without_detection()

    def _update_with_detection(self, detection: Detection, img_w: int, img_h: int) -> TrackerResult:
        self._missing_streak = 0
        if self._state != VisState.TRACKING:
            self._detect_streak += 1
            if self._detect_streak <= self.detect_hold_n:
                self._state = VisState.TARGET_DETECTED
            else:
                self._state = VisState.TRACKING

        self._last_bbox = normalize_bbox_xywh(
            x=detection.bbox.x,
            y=detection.bbox.y,
            w=detection.bbox.w,
            h=detection.bbox.h,
            img_w=img_w,
            img_h=img_h,
        )
        self._last_conf = clamp01(detection.confidence)
        self._last_track_id = max(0, int(detection.track_id))
        return TrackerResult(
            state=self._state,
            bbox=self._last_bbox,
            conf=self._last_conf,
            track_id=self._last_track_id,
        )

    def _update_without_detection(self) -> TrackerResult:
        self._detect_streak = 0

        if self._state in (VisState.TRACKING, VisState.SEARCHING) and self.search_n > 0:
            self._missing_streak += 1
            if self._missing_streak <= self.search_n:
                self._state = VisState.SEARCHING
                return TrackerResult(
                    state=self._state,
                    bbox=NormalizedBBox.zero(),
                    conf=0.0,
                    track_id=0,
                )

        self._state = VisState.NO_TARGET
        self._missing_streak = 0
        self._last_bbox = NormalizedBBox.zero()
        self._last_conf = 0.0
        self._last_track_id = 0
        return TrackerResult(
            state=self._state,
            bbox=self._last_bbox,
            conf=self._last_conf,
            track_id=self._last_track_id,
        )
