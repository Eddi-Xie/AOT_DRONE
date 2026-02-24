import pytest

from src.backend.vis_schema import VisValidationError, validate_vis_message


def _valid_vis() -> dict[str, float | int | str]:
    return {
        "type": "VIS",
        "seq": 10,
        "timestamp_s": 123.45,
        "tracking_state": 3,
        "loc_x": 0.1,
        "loc_y": -0.1,
        "bound_w": 0.2,
        "bound_h": 0.3,
        "confidence": 0.9,
    }


def test_validate_vis_message_accepts_valid_tracking_message() -> None:
    validated = validate_vis_message(_valid_vis())
    assert validated["type"] == "VIS"
    assert validated["tracking_state"] == 3
    assert validated["loc_x"] == pytest.approx(0.1)


def test_validate_vis_message_rejects_missing_fields() -> None:
    msg = _valid_vis()
    del msg["seq"]
    with pytest.raises(VisValidationError) as exc_info:
        validate_vis_message(msg)
    assert exc_info.value.reason == "schema"


def test_validate_vis_message_rejects_out_of_range_values() -> None:
    msg = _valid_vis()
    msg["loc_x"] = 1.2
    with pytest.raises(VisValidationError) as exc_info:
        validate_vis_message(msg)
    assert exc_info.value.reason == "range"


def test_validate_vis_message_enforces_non_tracking_zero_semantics() -> None:
    msg = _valid_vis()
    msg["tracking_state"] = 1
    msg["loc_x"] = 0.1
    with pytest.raises(VisValidationError) as exc_info:
        validate_vis_message(msg)
    assert exc_info.value.reason == "semantics"


def test_validate_vis_message_accepts_small_non_tracking_epsilon_and_normalizes() -> None:
    msg = _valid_vis()
    msg["tracking_state"] = 2
    msg["loc_x"] = 1e-9
    msg["loc_y"] = -1e-9
    msg["bound_w"] = 0.0
    msg["bound_h"] = 1e-12
    msg["confidence"] = 1e-12

    validated = validate_vis_message(msg)
    assert validated["tracking_state"] == 2
    assert validated["loc_x"] == 0.0
    assert validated["loc_y"] == 0.0
    assert validated["bound_w"] == 0.0
    assert validated["bound_h"] == 0.0
    assert validated["confidence"] == 0.0
