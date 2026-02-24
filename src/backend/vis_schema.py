from __future__ import annotations

from typing import Any

TRACKING_STATES = {1, 2, 3, 4}
TRACKING_STATE_TRACKING = 3
NON_TRACKING_ZERO_EPS = 1e-6

_REQUIRED_FIELDS = (
    "type",
    "seq",
    "timestamp_s",
    "tracking_state",
    "loc_x",
    "loc_y",
    "bound_w",
    "bound_h",
    "confidence",
)
_NON_TRACKING_ZERO_FIELDS = ("loc_x", "loc_y", "bound_w", "bound_h", "confidence")


class VisValidationError(ValueError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def validate_vis_message(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise VisValidationError("schema", "VIS payload must be a JSON object")

    missing = [name for name in _REQUIRED_FIELDS if name not in payload]
    if missing:
        missing_joined = ", ".join(sorted(missing))
        raise VisValidationError("schema", f"missing required VIS fields: {missing_joined}")

    msg_type = payload.get("type")
    if msg_type != "VIS":
        raise VisValidationError("schema", f"expected type='VIS', got {msg_type!r}")

    seq = _require_int(payload, "seq")
    timestamp_s = _require_float(payload, "timestamp_s")
    tracking_state = _require_int(payload, "tracking_state")
    if tracking_state not in TRACKING_STATES:
        raise VisValidationError("range", "tracking_state out of allowed set {1,2,3,4}")

    loc_x = _require_float(payload, "loc_x")
    loc_y = _require_float(payload, "loc_y")
    bound_w = _require_float(payload, "bound_w")
    bound_h = _require_float(payload, "bound_h")
    confidence = _require_float(payload, "confidence")

    _ensure_in_range("loc_x", loc_x, -1.0, 1.0)
    _ensure_in_range("loc_y", loc_y, -1.0, 1.0)
    _ensure_in_range("bound_w", bound_w, 0.0, 1.0)
    _ensure_in_range("bound_h", bound_h, 0.0, 1.0)
    _ensure_in_range("confidence", confidence, 0.0, 1.0)

    if tracking_state != TRACKING_STATE_TRACKING:
        values = {
            "loc_x": loc_x,
            "loc_y": loc_y,
            "bound_w": bound_w,
            "bound_h": bound_h,
            "confidence": confidence,
        }
        nonzero = [
            name for name in _NON_TRACKING_ZERO_FIELDS if abs(values[name]) > NON_TRACKING_ZERO_EPS
        ]
        if nonzero:
            nonzero_joined = ", ".join(nonzero)
            raise VisValidationError(
                "semantics",
                "non-tracking VIS must be near-zero for "
                f"{nonzero_joined} within ±{NON_TRACKING_ZERO_EPS} "
                f"(tracking_state={tracking_state})",
            )
        loc_x = 0.0
        loc_y = 0.0
        bound_w = 0.0
        bound_h = 0.0
        confidence = 0.0

    return {
        "type": "VIS",
        "seq": seq,
        "timestamp_s": timestamp_s,
        "tracking_state": tracking_state,
        "loc_x": loc_x,
        "loc_y": loc_y,
        "bound_w": bound_w,
        "bound_h": bound_h,
        "confidence": confidence,
    }


def _require_int(payload: dict[str, Any], field_name: str) -> int:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise VisValidationError("schema", f"{field_name} must be int")
    return int(value)


def _require_float(payload: dict[str, Any], field_name: str) -> float:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (int | float)):
        raise VisValidationError("schema", f"{field_name} must be float/int")
    return float(value)


def _ensure_in_range(field_name: str, value: float, lower: float, upper: float) -> None:
    if not (lower <= value <= upper):
        raise VisValidationError("range", f"{field_name} out of range [{lower}, {upper}]")
