from __future__ import annotations

from src.vision.main import _build_vis_payload
from src.vision.patterns import PatternGenerator
from src.vision.types import VisState


def test_sweep_pattern_stays_in_expected_ranges() -> None:
    generator = PatternGenerator(mode="sweep")

    for frame_id in range(1, 241):
        result = generator.update(frame_id=frame_id)
        assert result.state == VisState.TRACKING
        assert 0.0 <= result.bbox.cx <= 1.0
        assert 0.0 <= result.bbox.cy <= 1.0
        assert 0.0 <= result.bbox.w <= 1.0
        assert 0.0 <= result.bbox.h <= 1.0

        vis_payload = _build_vis_payload(
            seq=frame_id,
            timestamp_s=float(frame_id),
            state=result.state,
            bbox=result.bbox,
            confidence=result.conf,
        )
        assert -1.0 <= vis_payload["loc_x"] <= 1.0
        assert -1.0 <= vis_payload["loc_y"] <= 1.0
        assert 0.0 <= vis_payload["bound_w"] <= 1.0
        assert 0.0 <= vis_payload["bound_h"] <= 1.0


def test_lose_pattern_transitions_states() -> None:
    generator = PatternGenerator(mode="lose")

    assert generator.update(frame_id=1).state == VisState.TRACKING
    assert generator.update(frame_id=75).state == VisState.SEARCHING
    assert generator.update(frame_id=115).state == VisState.NO_TARGET
    assert generator.update(frame_id=145).state == VisState.TARGET_DETECTED


def test_reacquire_pattern_transitions_states() -> None:
    generator = PatternGenerator(mode="reacquire")

    assert generator.update(frame_id=1).state == VisState.NO_TARGET
    assert generator.update(frame_id=60).state == VisState.TARGET_DETECTED
    assert generator.update(frame_id=100).state == VisState.TRACKING
    assert generator.update(frame_id=150).state == VisState.SEARCHING
    assert generator.update(frame_id=175).state == VisState.TARGET_DETECTED


def test_non_tracking_vis_payload_fields_are_exact_zero() -> None:
    for mode in ("lose", "reacquire"):
        generator = PatternGenerator(mode=mode)
        for frame_id in range(1, 241):
            result = generator.update(frame_id=frame_id)
            payload = _build_vis_payload(
                seq=frame_id,
                timestamp_s=float(frame_id),
                state=result.state,
                bbox=result.bbox,
                confidence=result.conf,
            )
            if result.state != VisState.TRACKING:
                assert payload["loc_x"] == 0.0
                assert payload["loc_y"] == 0.0
                assert payload["bound_w"] == 0.0
                assert payload["bound_h"] == 0.0
                assert payload["confidence"] == 0.0
