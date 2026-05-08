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
    # In TRACKING state, re-initialise the KCF tracker only when the new
    # detection's IoU against the most recent tracker estimate falls below
    # this threshold. Above the threshold we trust the tracker's appearance
    # model and skip the re-init. 0.5 strikes a balance: it accepts modest
    # box drift (the detector is noisy too) but flips on a real identity
    # swap or large position jump.
    tracker_reinit_iou: float = 0.5
    # Confidence decay applied to the published TRACKING confidence on every
    # tracker-only frame (no detection arrived). Multiplicative: each
    # tracker-only frame multiplies the current conf by this factor. A
    # detection frame resets conf to the detection's own confidence. Default
    # 0.95 means after ~14 tracker-only frames a detection conf=1.0
    # decays to ~0.5; after ~50 frames to ~0.08 (clamped at the floor).
    tracker_only_conf_decay: float = 0.95
    # Lower bound for the decayed confidence so a long tracker-only stretch
    # doesn't underrun past zero. UI consumers compare against this floor
    # to decide how to render "tracker prediction stale". Configurable via
    # the env var VISION_TRACKER_ONLY_CONF_FLOOR (read in main.py).
    tracker_only_conf_floor: float = 0.3

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
        if not (0.0 <= self.tracker_reinit_iou <= 1.0):
            raise ValueError("tracker_reinit_iou must be in [0, 1]")
        if not (0.0 < self.tracker_only_conf_decay <= 1.0):
            raise ValueError("tracker_only_conf_decay must be in (0, 1]")
        if not (0.0 <= self.tracker_only_conf_floor <= 1.0):
            raise ValueError("tracker_only_conf_floor must be in [0, 1]")


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
        # Cached most-recent tracker output (only valid while _tracker_active
        # is True). Used for the IoU sanity check that gates re-init when a
        # detection arrives while tracking — see _update_tracking.
        self._last_tracker_bbox: PixelBBox | None = None
        # Most recent published TRACKING confidence — seeded from a detection
        # frame, then multiplicatively decayed on tracker-only frames toward
        # the configured floor. Reset to 0.0 on transition out of TRACKING.
        self._last_tracking_conf: float = 0.0
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
        self._last_tracker_bbox = None
        self._last_tracking_conf = 0.0
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
            # Seed the tracking confidence from the detection that just got
            # us here. Subsequent tracker-only frames will decay this value;
            # the next detection will re-seed it.
            self._last_tracking_conf = float(current.confidence)
            return _tracking_result(
                bbox=current.bbox, img_w=img_w, img_h=img_h, conf=self._last_tracking_conf
            )

        return _zero_result(VisState.TARGET_DETECTED)

    def _update_tracking(
        self,
        frame: np.ndarray,
        img_w: int,
        img_h: int,
        detected: Detection | None,
    ) -> TrackerResult:
        # When a detection arrives this frame we trust it as the authoritative
        # signal and SKIP `tracker.update` (audit A12) — KCF's update is
        # expensive and, when the tracker has drifted from the true target,
        # can produce a misleading bbox we'd then have to discard. Use the
        # most recent tracker bbox cached from a previous frame for the IoU
        # sanity check below (audit A3).
        tracker_success = False
        tracker_bbox: PixelBBox | None = None
        if self._tracker_active and detected is None:
            tracker_success, tracker_bbox = self.tracker.update(frame)
            if tracker_success and tracker_bbox is not None:
                self._last_tracker_bbox = tracker_bbox
            else:
                self._tracker_active = False

        if detected is not None:
            # Audit A3: re-init the tracker only when its prior estimate has
            # diverged from the detection (low IoU, or tracker not active /
            # no prior estimate). Re-init on every detection — the previous
            # behaviour — destroys the appearance model the tracker has
            # built up across frames and trades long-term stability for a
            # one-frame correction.
            should_reinit = (
                not self._tracker_active
                or self._last_tracker_bbox is None
                or _iou(self._last_tracker_bbox, detected.bbox) < self.config.tracker_reinit_iou
            )
            if should_reinit:
                self._tracker_active = bool(
                    self.tracker.initialize(frame=frame, bbox=detected.bbox)
                )
                self._last_tracker_bbox = detected.bbox if self._tracker_active else None
            # Detection frame: re-seed the published confidence from the
            # detector's own confidence (audit A9). The previous behaviour
            # was hardcoded conf=1.0 regardless of detector output, which
            # masked uncertain detections from the UI sparkline.
            self._last_tracking_conf = float(detected.confidence)
            return _tracking_result(
                bbox=detected.bbox, img_w=img_w, img_h=img_h, conf=self._last_tracking_conf
            )

        if tracker_success and tracker_bbox is not None:
            # Tracker-only frame: decay the cached conf toward the floor.
            # The decay rate (0.95/frame default) means a high-conf detection
            # remains "trustworthy" for ~14 tracker-only frames before
            # dropping below 0.5 — long enough to ride out a typical
            # detector miss-streak, short enough that a long tracker-only
            # stretch surfaces in the UI as "stale".
            decayed = self._last_tracking_conf * self.config.tracker_only_conf_decay
            self._last_tracking_conf = max(decayed, self.config.tracker_only_conf_floor)
            return _tracking_result(
                bbox=tracker_bbox, img_w=img_w, img_h=img_h, conf=self._last_tracking_conf
            )

        self._state = VisState.SEARCHING
        self._search_counter = 0
        self._detected_hold_counter = 0
        self._detected_box = None
        self._last_tracker_bbox = None
        self._last_tracking_conf = 0.0
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
        self._last_tracker_bbox = None
        self._last_tracking_conf = 0.0

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


def _tracking_result(bbox: PixelBBox, img_w: int, img_h: int, conf: float = 1.0) -> TrackerResult:
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
        conf=conf,
        track_id=1,
    )


def _zero_result(state: VisState) -> TrackerResult:
    return TrackerResult(
        state=state,
        bbox=NormalizedBBox.zero(),
        conf=0.0,
        track_id=0,
    )


def _iou(a: PixelBBox, b: PixelBBox) -> float:
    """Intersection-over-Union for two pixel-space (x, y, w, h) boxes.

    Returns 0.0 for non-overlapping boxes or zero-area inputs (rather than
    NaN), so callers can do plain `<` comparisons against a threshold.
    """
    if a.w <= 0.0 or a.h <= 0.0 or b.w <= 0.0 or b.h <= 0.0:
        return 0.0
    a_x2 = a.x + a.w
    a_y2 = a.y + a.h
    b_x2 = b.x + b.w
    b_y2 = b.y + b.h
    inter_x1 = max(a.x, b.x)
    inter_y1 = max(a.y, b.y)
    inter_x2 = min(a_x2, b_x2)
    inter_y2 = min(a_y2, b_y2)
    inter_w = inter_x2 - inter_x1
    inter_h = inter_y2 - inter_y1
    if inter_w <= 0.0 or inter_h <= 0.0:
        return 0.0
    inter_area = inter_w * inter_h
    union_area = (a.w * a.h) + (b.w * b.h) - inter_area
    if union_area <= 0.0:
        return 0.0
    return inter_area / union_area
