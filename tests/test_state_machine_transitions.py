from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from src.vision.types import Detection, PixelBBox, VisState
from src.vision.vision_pipeline import VisionPipeline, VisionPipelineConfig


def _det(x: float, y: float, w: float, h: float, conf: float = 0.9) -> Detection:
    return Detection(
        bbox=PixelBBox(x=x, y=y, w=w, h=h),
        confidence=conf,
        track_id=1,
    )


class _SequenceDetector:
    def __init__(self, by_frame: Sequence[Sequence[Detection]]) -> None:
        self._by_frame = list(by_frame)

    def detect(self, frame: np.ndarray, frame_id: int) -> Sequence[Detection]:
        del frame
        index = frame_id - 1
        if index < 0 or index >= len(self._by_frame):
            return ()
        return self._by_frame[index]


class _SequenceTracker:
    def __init__(self, updates: Sequence[tuple[bool, PixelBBox | None]]) -> None:
        self._updates = list(updates)
        self._idx = 0
        self.init_calls = 0

    def initialize(self, frame: np.ndarray, bbox: PixelBBox) -> bool:
        del frame
        del bbox
        self.init_calls += 1
        return True

    def update(self, frame: np.ndarray) -> tuple[bool, PixelBBox | None]:
        del frame
        if self._idx >= len(self._updates):
            return False, None
        item = self._updates[self._idx]
        self._idx += 1
        return item

    def reset(self) -> None:
        return None


def test_pipeline_state_machine_core_transitions() -> None:
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0),),
            (_det(101.0, 100.0, 80.0, 80.0),),
            (),
            (),
            (_det(140.0, 120.0, 85.0, 85.0),),
            (_det(142.0, 120.0, 85.0, 85.0),),
            (),
            (),
            (),
        )
    )
    tracker = _SequenceTracker(
        updates=(
            (True, PixelBBox(x=102.0, y=100.0, w=80.0, h=80.0)),
            (False, None),
            (False, None),
        )
    )
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(detect_hold_n=1, search_n=2, detect_every_n=1),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    states = [pipeline.process_frame(frame=frame, frame_id=i).state for i in range(1, 10)]

    assert states == [
        VisState.TARGET_DETECTED,
        VisState.TRACKING,
        VisState.TRACKING,
        VisState.SEARCHING,
        VisState.TARGET_DETECTED,
        VisState.TRACKING,
        VisState.SEARCHING,
        VisState.SEARCHING,
        VisState.NO_TARGET,
    ]
    assert tracker.init_calls == 2


def test_tracking_prefers_detector_when_tracker_fails_same_frame() -> None:
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0),),
            (_det(102.0, 102.0, 80.0, 80.0),),
            (_det(120.0, 120.0, 80.0, 80.0),),
        )
    )
    tracker = _SequenceTracker(updates=((False, None),))
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(detect_hold_n=1, search_n=2, detect_every_n=1),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    states = [pipeline.process_frame(frame=frame, frame_id=i).state for i in range(1, 4)]

    assert states == [
        VisState.TARGET_DETECTED,
        VisState.TRACKING,
        VisState.TRACKING,
    ]
    assert tracker.init_calls == 2
