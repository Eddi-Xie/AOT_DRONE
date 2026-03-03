from __future__ import annotations

import math
from dataclasses import dataclass

from .tracker import TrackerResult
from .types import NormalizedBBox, VisState, clamp01

PATTERN_CHOICES = ("none", "sweep", "lose", "reacquire")


@dataclass(frozen=True)
class PatternGenerator:
    mode: str

    def __post_init__(self) -> None:
        if self.mode not in PATTERN_CHOICES:
            raise ValueError(f"unsupported pattern '{self.mode}'")

    def update(self, frame_id: int) -> TrackerResult:
        if self.mode == "sweep":
            return self._tracking(frame_id=frame_id, confidence=0.92)
        if self.mode == "lose":
            return self._lose(frame_id=frame_id)
        if self.mode == "reacquire":
            return self._reacquire(frame_id=frame_id)
        return _zero_result(VisState.NO_TARGET)

    def _lose(self, frame_id: int) -> TrackerResult:
        idx = (max(1, frame_id) - 1) % 160
        if idx < 70:
            return self._tracking(frame_id=frame_id, confidence=0.9)
        if idx < 100:
            return _zero_result(VisState.SEARCHING)
        if idx < 130:
            return _zero_result(VisState.NO_TARGET)
        return self._detected(frame_id=frame_id, confidence=0.7)

    def _reacquire(self, frame_id: int) -> TrackerResult:
        idx = (max(1, frame_id) - 1) % 180
        if idx < 50:
            return _zero_result(VisState.NO_TARGET)
        if idx < 80:
            return self._detected(frame_id=frame_id, confidence=0.65)
        if idx < 140:
            return self._tracking(frame_id=frame_id, confidence=0.93)
        if idx < 165:
            return _zero_result(VisState.SEARCHING)
        return self._detected(frame_id=frame_id, confidence=0.72)

    def _tracking(self, frame_id: int, confidence: float) -> TrackerResult:
        return TrackerResult(
            state=VisState.TRACKING,
            bbox=_sweep_bbox(frame_id=frame_id),
            conf=clamp01(confidence),
            track_id=1,
        )

    def _detected(self, frame_id: int, confidence: float) -> TrackerResult:
        return TrackerResult(
            state=VisState.TARGET_DETECTED,
            bbox=_sweep_bbox(frame_id=frame_id),
            conf=clamp01(confidence),
            track_id=1,
        )


def _zero_result(state: VisState) -> TrackerResult:
    return TrackerResult(
        state=state,
        bbox=NormalizedBBox.zero(),
        conf=0.0,
        track_id=0,
    )


def _sweep_bbox(frame_id: int) -> NormalizedBBox:
    idx = (max(1, frame_id) - 1) % 120
    phase = idx / 119.0
    cx = 0.15 + (0.70 * phase)
    cy = 0.5 + (0.18 * math.sin(2.0 * math.pi * phase))
    return NormalizedBBox(
        cx=clamp01(cx),
        cy=clamp01(cy),
        w=0.24,
        h=0.3,
    )
