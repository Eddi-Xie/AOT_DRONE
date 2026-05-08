from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from src.vision.types import Detection, PixelBBox, VisState
from src.vision.vision_pipeline import (
    VisionPipeline,
    VisionPipelineConfig,
    _iou,
)


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


class _CountingTracker:
    """Tracker that tallies update() and initialize() calls.

    Returns the configured `update_result` from every update() call so a
    test can pin "the tracker stayed locked on its prediction" without
    depending on real KCF behaviour.
    """

    def __init__(
        self,
        initial_bbox: PixelBBox | None = None,
        update_result: tuple[bool, PixelBBox | None] = (True, None),
    ) -> None:
        self._initial_bbox = initial_bbox
        self._update_result = update_result
        self.init_calls = 0
        self.update_calls = 0

    def initialize(self, frame: np.ndarray, bbox: PixelBBox) -> bool:
        del frame
        del bbox
        self.init_calls += 1
        return True

    def update(self, frame: np.ndarray) -> tuple[bool, PixelBBox | None]:
        del frame
        self.update_calls += 1
        return self._update_result

    def reset(self) -> None:
        return None


def test_tracking_skips_tracker_update_when_detection_arrives_same_frame() -> None:
    """Audit A12: when a detection lands on the same frame as a tracker
    update would, the detection wins and tracker.update is skipped to save
    cycles + avoid feeding stale tracker output into the IoU sanity check."""
    detection = _det(100.0, 100.0, 80.0, 80.0)
    detector = _SequenceDetector(
        by_frame=(
            (detection,),  # frame 1: enter TARGET_DETECTED
            (detection,),  # frame 2: hold met (n=1) => TRACKING (init #1)
            (detection,),  # frame 3: TRACKING, detection arrives — skip update
            (detection,),  # frame 4: TRACKING, detection arrives — skip update
        )
    )
    tracker = _CountingTracker(update_result=(True, PixelBBox(x=99.0, y=99.0, w=80.0, h=80.0)))
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(detect_hold_n=1, search_n=2, detect_every_n=1),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    for i in range(1, 5):
        pipeline.process_frame(frame=frame, frame_id=i)

    # tracker.update must NOT have been called on any frame: every frame had
    # a detection (frames 1-2 are pre-TRACKING; frames 3-4 are in TRACKING
    # with detection arriving so skip). Pre-fix, frames 3-4 would each call
    # update (2 calls) and then re-init.
    assert tracker.update_calls == 0


def test_tracking_does_not_reinit_on_high_iou_detection() -> None:
    """Audit A3: a detection that overlaps the tracker's prior estimate
    above the IoU threshold leaves the tracker undisturbed — no re-init,
    appearance model preserved."""
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0),),  # frame 1: TARGET_DETECTED
            (_det(100.0, 100.0, 80.0, 80.0),),  # frame 2: TRACKING (init #1)
            # frame 3: NO detection — runs tracker.update; tracker reports
            # bbox at (99,99,80,80) very close to last detection.
            (),
            # frame 4: detection at (101,99,80,80) — IoU with (99,99,80,80)
            # is well above 0.5, so the comparator must NOT re-init.
            (_det(101.0, 99.0, 80.0, 80.0),),
        )
    )
    tracker = _CountingTracker(update_result=(True, PixelBBox(x=99.0, y=99.0, w=80.0, h=80.0)))
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(detect_hold_n=1, search_n=2, detect_every_n=1),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    for i in range(1, 5):
        pipeline.process_frame(frame=frame, frame_id=i)

    # init_calls == 1: the entry from TARGET_DETECTED -> TRACKING at frame 2.
    # The high-IoU detection at frame 4 must not have triggered a second init.
    assert tracker.init_calls == 1


def test_tracking_reinits_on_low_iou_detection() -> None:
    """Audit A3: a detection that does NOT overlap the tracker's prior
    estimate above the IoU threshold (i.e. the tracker has drifted or a
    new target appeared) DOES trigger a re-init."""
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0),),  # frame 1: TARGET_DETECTED
            (_det(100.0, 100.0, 80.0, 80.0),),  # frame 2: TRACKING (init #1)
            (),  # frame 3: tracker.update => bbox at (99,99,80,80)
            # frame 4: detection at (300, 300, 80, 80) — far away; IoU=0
            (_det(300.0, 300.0, 80.0, 80.0),),
        )
    )
    tracker = _CountingTracker(update_result=(True, PixelBBox(x=99.0, y=99.0, w=80.0, h=80.0)))
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(detect_hold_n=1, search_n=2, detect_every_n=1),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    for i in range(1, 5):
        pipeline.process_frame(frame=frame, frame_id=i)

    # init_calls == 2: entry into TRACKING (frame 2) plus the IoU-failed
    # detection at frame 4 forced a re-init.
    assert tracker.init_calls == 2


def test_tracking_publishes_real_detector_confidence() -> None:
    """Audit A9: TRACKING-state confidence used to be hard-coded 1.0
    regardless of detector confidence. Now it's the detector's own
    confidence on detection frames."""
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0, conf=0.6),),  # frame 1: TARGET_DETECTED
            (_det(100.0, 100.0, 80.0, 80.0, conf=0.8),),  # frame 2: TRACKING (init)
        )
    )
    tracker = _CountingTracker()
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(detect_hold_n=1, search_n=2, detect_every_n=1),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    pipeline.process_frame(frame=frame, frame_id=1)
    result_2 = pipeline.process_frame(frame=frame, frame_id=2)

    # Detection at frame 2 had conf=0.8 — must surface in the published result.
    assert result_2.state == VisState.TRACKING
    assert result_2.conf == 0.8


def test_tracker_only_frames_decay_confidence_toward_floor() -> None:
    """Audit A9: tracker-only frames (no detection) multiply the published
    confidence by a decay factor each frame, bounded below by the configured
    floor. Detection frames re-seed it."""
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0, conf=0.9),),  # frame 1: TARGET_DETECTED
            (_det(100.0, 100.0, 80.0, 80.0, conf=0.9),),  # frame 2: TRACKING (init), conf=0.9
            (),  # frame 3: tracker-only => conf decays
            (),  # frame 4: tracker-only => conf decays again
            (),  # frame 5: tracker-only => conf decays again
        )
    )
    # Tracker stays locked on a stable bbox so we exercise tracker-only frames.
    tracker = _CountingTracker(update_result=(True, PixelBBox(x=99.0, y=99.0, w=80.0, h=80.0)))
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(
            detect_hold_n=1,
            search_n=10,  # avoid SEARCHING fall-through during the test window
            detect_every_n=1,
            tracker_only_conf_decay=0.5,
            tracker_only_conf_floor=0.1,
        ),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    pipeline.process_frame(frame=frame, frame_id=1)
    pipeline.process_frame(frame=frame, frame_id=2)  # TRACKING entry, conf=0.9
    r3 = pipeline.process_frame(frame=frame, frame_id=3)
    r4 = pipeline.process_frame(frame=frame, frame_id=4)
    r5 = pipeline.process_frame(frame=frame, frame_id=5)

    # 0.9 * 0.5 = 0.45, then 0.225, then 0.1125 -> floor would clamp at 0.1.
    assert r3.conf == 0.45
    assert r4.conf == 0.225
    # 0.225 * 0.5 = 0.1125 — above floor 0.1, so no clamp yet.
    assert r5.conf == pytest.approx(0.1125)


def test_tracker_only_confidence_clamps_at_floor() -> None:
    """A long tracker-only stretch must never publish a confidence below the
    configured floor — the floor IS the contract for "stale tracker output"
    in the UI."""
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0, conf=0.5),),
            (_det(100.0, 100.0, 80.0, 80.0, conf=0.5),),
        )
        + tuple([()] * 50)  # 50 tracker-only frames
    )
    tracker = _CountingTracker(update_result=(True, PixelBBox(x=99.0, y=99.0, w=80.0, h=80.0)))
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(
            detect_hold_n=1,
            search_n=100,
            detect_every_n=1,
            tracker_only_conf_decay=0.5,  # aggressive decay
            tracker_only_conf_floor=0.3,
        ),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    last_conf = 1.0
    for i in range(1, 53):
        result = pipeline.process_frame(frame=frame, frame_id=i)
        last_conf = result.conf

    # After many decays, conf must rest at the floor exactly — never below.
    assert last_conf == 0.3


def test_iou_handles_boundary_cases() -> None:
    """Direct test of _iou helper for the cases that previously only had
    indirect coverage via the re-init tests."""
    # Identical boxes => IoU 1.0
    a = PixelBBox(x=0.0, y=0.0, w=10.0, h=10.0)
    assert _iou(a, a) == 1.0

    # Fully disjoint
    b = PixelBBox(x=100.0, y=100.0, w=10.0, h=10.0)
    assert _iou(a, b) == 0.0

    # Touching edges (zero intersection area)
    c = PixelBBox(x=10.0, y=0.0, w=10.0, h=10.0)
    assert _iou(a, c) == 0.0

    # Half overlap horizontally => intersection 50, union 150 => 1/3
    d = PixelBBox(x=5.0, y=0.0, w=10.0, h=10.0)
    assert _iou(a, d) == pytest.approx(50.0 / 150.0)

    # Zero-area inputs
    zero_w = PixelBBox(x=0.0, y=0.0, w=0.0, h=10.0)
    assert _iou(zero_w, a) == 0.0
    zero_h = PixelBBox(x=0.0, y=0.0, w=10.0, h=0.0)
    assert _iou(zero_h, a) == 0.0

    # NaN coordinate => 0.0 (so the should_reinit check evaluates
    # 0.0 < threshold = True, forcing re-init on a poisoned input)
    nan_box = PixelBBox(x=float("nan"), y=0.0, w=10.0, h=10.0)
    assert _iou(nan_box, a) == 0.0
    assert _iou(a, nan_box) == 0.0


def test_low_seed_conf_decays_freely_below_floor() -> None:
    """Floor must not RAISE conf above the seed when the seed itself was
    below the floor. Operator scenario: YOLO conf_threshold lowered below
    the configured floor so a low-conf detection seeds TRACKING. The floor
    only applies as a clamp on stretches that started healthy."""
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0, conf=0.4),),  # frame 1: TARGET_DETECTED
            (
                _det(100.0, 100.0, 80.0, 80.0, conf=0.4),
            ),  # frame 2: TRACKING (seed=0.4, below floor)
            (),  # frame 3: tracker-only — must continue decaying, not clamp up to floor=0.5
            (),  # frame 4: same
        )
    )
    tracker = _CountingTracker(update_result=(True, PixelBBox(x=99.0, y=99.0, w=80.0, h=80.0)))
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(
            detect_hold_n=1,
            search_n=10,
            detect_every_n=1,
            tracker_only_conf_decay=0.5,
            tracker_only_conf_floor=0.5,  # higher than the seed (0.4)
        ),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    pipeline.process_frame(frame=frame, frame_id=1)
    pipeline.process_frame(frame=frame, frame_id=2)  # TRACKING entry, conf=0.4
    r3 = pipeline.process_frame(frame=frame, frame_id=3)
    r4 = pipeline.process_frame(frame=frame, frame_id=4)

    # Pre-fix bug: floor would clamp 0.2 up to 0.5, RAISING conf above seed.
    # Post-fix: seed (0.4) < floor (0.5), so decay continues unbounded.
    assert r3.conf == 0.2  # 0.4 * 0.5
    assert r4.conf == 0.1  # 0.2 * 0.5


def test_out_of_contract_detector_conf_is_clamped() -> None:
    """A buggy YOLO wrapper returning conf > 1.0 must not poison the decay
    state. clamp01 at the seed sites caps the cached _last_tracking_conf
    in [0, 1]."""
    detector = _SequenceDetector(
        by_frame=(
            (_det(100.0, 100.0, 80.0, 80.0, conf=1.5),),  # frame 1: TARGET_DETECTED
            (_det(100.0, 100.0, 80.0, 80.0, conf=1.5),),  # frame 2: TRACKING (clamps to 1.0)
        )
    )
    tracker = _CountingTracker()
    pipeline = VisionPipeline(
        detector=detector,
        tracker=tracker,
        config=VisionPipelineConfig(detect_hold_n=1, search_n=2, detect_every_n=1),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    pipeline.process_frame(frame=frame, frame_id=1)
    result = pipeline.process_frame(frame=frame, frame_id=2)

    # Detector said 1.5 — clamped to 1.0 in the published result and in
    # the cached state.
    assert result.conf == 1.0


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
