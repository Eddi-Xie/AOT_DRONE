from src.vision.main import _build_vis_payload
from src.vision.types import NormalizedBBox, VisState


def test_vis_payload_non_tracking_states_force_exact_zero_fields() -> None:
    bbox = NormalizedBBox(cx=0.75, cy=0.25, w=0.4, h=0.2)

    for state in (VisState.NO_TARGET, VisState.TARGET_DETECTED, VisState.SEARCHING):
        payload = _build_vis_payload(
            seq=1,
            timestamp_s=1.0,
            state=state,
            bbox=bbox,
            confidence=0.87,
        )

        assert payload["tracking_state"] != 3
        assert payload["loc_x"] == 0.0
        assert payload["loc_y"] == 0.0
        assert payload["bound_w"] == 0.0
        assert payload["bound_h"] == 0.0
        assert payload["confidence"] == 0.0


def test_vis_payload_tracking_state_uses_normalized_values() -> None:
    bbox = NormalizedBBox(cx=0.75, cy=0.25, w=0.4, h=0.2)

    payload = _build_vis_payload(
        seq=10,
        timestamp_s=3.5,
        state=VisState.TRACKING,
        bbox=bbox,
        confidence=0.9,
    )

    assert payload["tracking_state"] == 3
    assert payload["loc_x"] == 0.5
    assert payload["loc_y"] == 0.5
    assert payload["bound_w"] == 0.4
    assert payload["bound_h"] == 0.2
    assert payload["confidence"] == 0.9
