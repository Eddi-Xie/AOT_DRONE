from src.vision.tracker import SimpleTracker
from src.vision.types import Detection, NormalizedBBox, PixelBBox, VisState


def _sample_detection() -> Detection:
    return Detection(
        bbox=PixelBBox(x=100.0, y=40.0, w=80.0, h=60.0),
        confidence=0.9,
        track_id=11,
    )


def test_tracker_state_transitions_hold_then_search_then_no_target() -> None:
    tracker = SimpleTracker(detect_hold_n=2, search_n=1)
    detection = _sample_detection()

    states = [
        tracker.update(detections=[], img_w=640, img_h=480).state,
        tracker.update(detections=[detection], img_w=640, img_h=480).state,
        tracker.update(detections=[detection], img_w=640, img_h=480).state,
        tracker.update(detections=[detection], img_w=640, img_h=480).state,
        tracker.update(detections=[], img_w=640, img_h=480).state,
        tracker.update(detections=[], img_w=640, img_h=480).state,
    ]

    assert states == [
        VisState.NO_TARGET,
        VisState.TARGET_DETECTED,
        VisState.TARGET_DETECTED,
        VisState.TRACKING,
        VisState.SEARCHING,
        VisState.NO_TARGET,
    ]


def test_tracker_outputs_zero_bbox_when_detection_missing() -> None:
    tracker = SimpleTracker(detect_hold_n=0, search_n=0)
    detection = _sample_detection()

    tracking_msg = tracker.update(detections=[detection], img_w=640, img_h=480)
    assert tracking_msg.state == VisState.TRACKING
    assert tracking_msg.conf > 0.0

    missing_msg = tracker.update(detections=[], img_w=640, img_h=480)
    assert missing_msg.state == VisState.NO_TARGET
    assert missing_msg.bbox == NormalizedBBox.zero()
    assert missing_msg.conf == 0.0
    assert missing_msg.track_id == 0
