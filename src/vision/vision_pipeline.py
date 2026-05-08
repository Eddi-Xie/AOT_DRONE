from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from .box_select import pick_best_box
from .tracker import TrackerResult
from .types import Detection, NormalizedBBox, PixelBBox, VisState, normalize_bbox_xywh


class DetectorLike(Protocol):
    def detect(self, frame: np.ndarray, frame_id: int) -> Sequence[Detection]: ...


class TrackerLike(Protocol):
    def initialize(self, frame: Any, bbox: PixelBBox) -> bool: ...

    def update(self, frame: Any) -> tuple[bool, PixelBBox | None]: ...

    def reset(self) -> None: ...


@dataclass(frozen=True)
class VisionPipelineConfig:
    detect_hold_n: int = 30
    search_n: int = 30
    detect_every_n: int = 1
    desired_cx: float = 0.5
    desired_cy: float = 0.5
    # While in TARGET_DETECTED, tolerate this many consecutive frames with
    # no detection before falling back to NO_TARGET. The previous behaviour
    # was an instant fall-back on any missed frame, which made a single
    # missed detection (e.g. one bad YOLO inference) reset the entire
    # detect-hold accumulation. 1-frame grace covers the common single-
    # frame blip without delaying the genuine target-lost case beyond ~1
    # frame at the pipeline's detection rate.
    target_detected_grace_frames: int = 1

    def __post_init__(self) -> None:
        if self.detect_hold_n < 0:
            raise ValueError("detect_hold_n must be >= 0")
        if self.search_n < 0:
            raise ValueError("search_n must be >= 0")
        if self.detect_every_n <= 0:
            raise ValueError("detect_every_n must be >= 1")
        if not (0.0 <= self.desired_cx <= 1.0):
            raise ValueError("desired_cx must be in [0, 1]")
        if not (0.0 <= self.desired_cy <= 1.0):
            raise ValueError("desired_cy must be in [0, 1]")
        if self.target_detected_grace_frames < 0:
            raise ValueError("target_detected_grace_frames must be >= 0")


class VisionPipeline:
    def __init__(
        self,
        detector: DetectorLike,
        tracker: TrackerLike,
        config: VisionPipelineConfig,
    ) -> None:
        self.detector = detector
        self.tracker = tracker
        self.config = config

        self._state = VisState.NO_TARGET
        self._detected_hold_counter = 0
        self._search_counter = 0
        self._detected_box: Detection | None = None

        self._last_detection: Detection | None = None
        self._last_detection_frame: int | None = None

        self._tracker_active = False
        # Counter for consecutive missed-detection frames in TARGET_DETECTED.
        # Reset on (a) entry to TARGET_DETECTED from another state and
        # (b) any successful detection while in TARGET_DETECTED. Compared
        # against config.target_detected_grace_frames before the fallback
        # to NO_TARGET fires.
        self._target_detected_miss_streak = 0

    @property
    def state(self) -> VisState:
        return self._state

    def reset(self) -> None:
        self._state = VisState.NO_TARGET
        self._detected_hold_counter = 0
        self._search_counter = 0
        self._detected_box = None
        self._last_detection = None
        self._last_detection_frame = None
        self._tracker_active = False
        self._target_detected_miss_streak = 0
        self.tracker.reset()

    def process_frame(self, frame: np.ndarray, frame_id: int) -> TrackerResult:
        img_h, img_w = frame.shape[:2]
        should_detect = self._should_detect(frame_id)
        detected = self._detect_best(frame=frame, frame_id=frame_id, img_w=img_w, img_h=img_h)
        if self._state == VisState.NO_TARGET:
            return self._update_no_target(detected=detected)
        if self._state == VisState.TARGET_DETECTED:
            return self._update_target_detected(
                frame=frame,
                frame_id=frame_id,
                img_w=img_w,
                img_h=img_h,
                detected=detected,
                should_detect=should_detect,
            )
        if self._state == VisState.TRACKING:
            return self._update_tracking(
                frame=frame,
                img_w=img_w,
                img_h=img_h,
                detected=detected,
            )
        return self._update_searching(detected=detected)

    def _update_no_target(self, detected: Detection | None) -> TrackerResult:
        if detected is None:
            return _zero_result(VisState.NO_TARGET)

        self._state = VisState.TARGET_DETECTED
        self._detected_box = detected
        self._detected_hold_counter = 0
        self._search_counter = 0
        self._target_detected_miss_streak = 0
        return _zero_result(VisState.TARGET_DETECTED)

    def _update_target_detected(
        self,
        frame: np.ndarray,
        frame_id: int,
        img_w: int,
        img_h: int,
        detected: Detection | None,
        should_detect: bool,
    ) -> TrackerResult:
        current = detected
        if current is None and not should_detect:
            current = self._reuse_last_detection(frame_id)

        if current is None:
            # Grace window: a single missed detection in TARGET_DETECTED is
            # treated as a transient blip rather than "target gone". Hold
            # the state, increment the miss streak, and return zero+state.
            # Falls through to _transition_no_target only after the streak
            # exceeds the configured grace.
            self._target_detected_miss_streak += 1
            if self._target_detected_miss_streak <= self.config.target_detected_grace_frames:
                return _zero_result(VisState.TARGET_DETECTED)
            self._transition_no_target()
            return _zero_result(VisState.NO_TARGET)

        # Successful detection (or reuse) — reset the miss streak.
        self._target_detected_miss_streak = 0
        self._detected_box = current
        self._detected_hold_counter += 1

        if self._detected_hold_counter >= self.config.detect_hold_n:
            self._state = VisState.TRACKING
            self._search_counter = 0
            self._tracker_active = bool(self.tracker.initialize(frame=frame, bbox=current.bbox))
            return _tracking_result(bbox=current.bbox, img_w=img_w, img_h=img_h)

        return _zero_result(VisState.TARGET_DETECTED)

    def _update_tracking(
        self,
        frame: np.ndarray,
        img_w: int,
        img_h: int,
        detected: Detection | None,
    ) -> TrackerResult:
        tracker_success = False
        tracker_bbox: PixelBBox | None = None

        if self._tracker_active:
            tracker_success, tracker_bbox = self.tracker.update(frame)
            if not tracker_success:
                self._tracker_active = False

        if detected is not None:
            self._tracker_active = bool(self.tracker.initialize(frame=frame, bbox=detected.bbox))
            return _tracking_result(bbox=detected.bbox, img_w=img_w, img_h=img_h)

        if tracker_success and tracker_bbox is not None:
            return _tracking_result(bbox=tracker_bbox, img_w=img_w, img_h=img_h)

        self._state = VisState.SEARCHING
        self._search_counter = 0
        self._detected_hold_counter = 0
        self._detected_box = None
        self.tracker.reset()
        self._tracker_active = False
        return _zero_result(VisState.SEARCHING)

    def _update_searching(self, detected: Detection | None) -> TrackerResult:
        if detected is not None:
            self._state = VisState.TARGET_DETECTED
            self._detected_box = detected
            self._detected_hold_counter = 0
            self._search_counter = 0
            self._target_detected_miss_streak = 0
            return _zero_result(VisState.TARGET_DETECTED)

        self._search_counter += 1
        if self._search_counter >= self.config.search_n:
            self._transition_no_target()
            return _zero_result(VisState.NO_TARGET)

        return _zero_result(VisState.SEARCHING)

    def _transition_no_target(self) -> None:
        self._state = VisState.NO_TARGET
        self._detected_hold_counter = 0
        self._search_counter = 0
        self._detected_box = None
        self._target_detected_miss_streak = 0
        self.tracker.reset()
        self._tracker_active = False

    def _should_detect(self, frame_id: int) -> bool:
        return ((max(1, frame_id) - 1) % self.config.detect_every_n) == 0

    def _detect_best(
        self,
        frame: np.ndarray,
        frame_id: int,
        img_w: int,
        img_h: int,
    ) -> Detection | None:
        if not self._should_detect(frame_id):
            return None

        detections = self.detector.detect(frame=frame, frame_id=frame_id)
        best = pick_best_box(
            detections=detections,
            img_w=img_w,
            img_h=img_h,
            desired_cx=self.config.desired_cx,
            desired_cy=self.config.desired_cy,
        )

        self._last_detection = best
        self._last_detection_frame = frame_id if best is not None else None
        return best

    def _reuse_last_detection(self, frame_id: int) -> Detection | None:
        if self._last_detection is None or self._last_detection_frame is None:
            return None

        if frame_id - self._last_detection_frame < self.config.detect_every_n:
            return self._last_detection
        return None


def _tracking_result(bbox: PixelBBox, img_w: int, img_h: int) -> TrackerResult:
    normalized_bbox = normalize_bbox_xywh(
        x=bbox.x,
        y=bbox.y,
        w=bbox.w,
        h=bbox.h,
        img_w=img_w,
        img_h=img_h,
    )
    return TrackerResult(
        state=VisState.TRACKING,
        bbox=normalized_bbox,
        conf=1.0,
        track_id=1,
    )


def _zero_result(state: VisState) -> TrackerResult:
    return TrackerResult(
        state=state,
        bbox=NormalizedBBox.zero(),
        conf=0.0,
        track_id=0,
    )
