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


def test_target_detected_grace_absorbs_single_missed_detection() -> None:
    """A 1-frame missed-detection blip in TARGET_DETECTED should NOT fall back
    to NO_TARGET. The next frame with a detection resumes hold accumulation."""
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0),),  # frame 1: detection
            (),  # frame 2: missed (the blip — should be absorbed by grace)
            (_det(101.0, 100.0, 80.0, 80.0),),  # frame 3: detection again
        )
    )
    tracker = _SequenceTracker(updates=())
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        # detect_hold_n=5 keeps us in TARGET_DETECTED across all 3 frames
        # (won't promote to TRACKING within the test window).
        config=VisionPipelineConfig(
            detect_hold_n=5, search_n=2, detect_every_n=1, target_detected_grace_frames=1
        ),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    states = [pipeline.process_frame(frame=frame, frame_id=i).state for i in range(1, 4)]

    # Without grace: states would be [TARGET_DETECTED, NO_TARGET, TARGET_DETECTED]
    # With 1-frame grace: the missed frame stays in TARGET_DETECTED.
    assert states == [
        VisState.TARGET_DETECTED,
        VisState.TARGET_DETECTED,
        VisState.TARGET_DETECTED,
    ]


def test_target_detected_falls_back_after_grace_exhausted() -> None:
    """Two consecutive missed detections (with grace=1) DO fall back to NO_TARGET."""
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0),),  # frame 1: detection
            (),  # frame 2: missed (consumes the 1-frame grace)
            (),  # frame 3: missed again (grace exhausted; fall back)
        )
    )
    tracker = _SequenceTracker(updates=())
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(
            detect_hold_n=5, search_n=2, detect_every_n=1, target_detected_grace_frames=1
        ),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    states = [pipeline.process_frame(frame=frame, frame_id=i).state for i in range(1, 4)]

    assert states == [
        VisState.TARGET_DETECTED,
        VisState.TARGET_DETECTED,  # grace absorbs first miss
        VisState.NO_TARGET,  # grace exhausted, fall back
    ]


def test_target_detected_grace_resets_on_successful_detection() -> None:
    """A successful detection while in TARGET_DETECTED resets the miss streak so
    a later isolated missed frame still gets full grace coverage."""
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0),),  # frame 1: detection
            (),  # frame 2: missed (consumes grace)
            (_det(102.0, 100.0, 80.0, 80.0),),  # frame 3: detection (resets)
            (),  # frame 4: missed (uses fresh grace)
            (_det(105.0, 100.0, 80.0, 80.0),),  # frame 5: detection
        )
    )
    tracker = _SequenceTracker(updates=())
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(
            detect_hold_n=10, search_n=2, detect_every_n=1, target_detected_grace_frames=1
        ),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    states = [pipeline.process_frame(frame=frame, frame_id=i).state for i in range(1, 6)]

    assert states == [
        VisState.TARGET_DETECTED,
        VisState.TARGET_DETECTED,  # grace absorbs miss
        VisState.TARGET_DETECTED,  # detection resets streak
        VisState.TARGET_DETECTED,  # fresh grace absorbs miss
        VisState.TARGET_DETECTED,
    ]


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
