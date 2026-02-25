from __future__ import annotations

from typing import Any

CONTROL_MODES = {0, 1, 2, 3}
TRACKING_STATES = {1, 2, 3, 4}

_REQUIRED_FIELDS = (
    "type",
    "seq",
    "timestamp_s",
    "control_mode",
    "tracking_state",
    "distFront_m",
    "distBack_m",
    "distBottom_m",
    "target_x",
    "target_y",
    "bound_w",
    "bound_h",
    "confidence",
)


class TelValidationError(ValueError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def validate_tel_message(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TelValidationError("schema", "TEL payload must be a JSON object")

    missing = [name for name in _REQUIRED_FIELDS if name not in payload]
    if missing:
        missing_joined = ", ".join(sorted(missing))
        raise TelValidationError("schema", f"missing required TEL fields: {missing_joined}")

    msg_type = payload.get("type")
    if msg_type != "TEL":
        raise TelValidationError("schema", f"expected type='TEL', got {msg_type!r}")

    seq = _require_int(payload, "seq")
    timestamp_s = _require_float(payload, "timestamp_s")
    control_mode = _require_int(payload, "control_mode")
    tracking_state = _require_int(payload, "tracking_state")
    if control_mode not in CONTROL_MODES:
        raise TelValidationError("range", "control_mode out of allowed set {0,1,2,3}")
    if tracking_state not in TRACKING_STATES:
        raise TelValidationError("range", "tracking_state out of allowed set {1,2,3,4}")

    dist_front_m = _require_float(payload, "distFront_m")
    dist_back_m = _require_float(payload, "distBack_m")
    dist_bottom_m = _require_float(payload, "distBottom_m")
    target_x = _require_float(payload, "target_x")
    target_y = _require_float(payload, "target_y")
    bound_w = _require_float(payload, "bound_w")
    bound_h = _require_float(payload, "bound_h")
    confidence = _require_float(payload, "confidence")

    _ensure_in_range("target_x", target_x, -1.0, 1.0)
    _ensure_in_range("target_y", target_y, -1.0, 1.0)
    _ensure_in_range("bound_w", bound_w, 0.0, 1.0)
    _ensure_in_range("bound_h", bound_h, 0.0, 1.0)
    _ensure_in_range("confidence", confidence, 0.0, 1.0)

    return {
        "type": "TEL",
        "seq": seq,
        "timestamp_s": timestamp_s,
        "control_mode": control_mode,
        "tracking_state": tracking_state,
        "distFront_m": dist_front_m,
        "distBack_m": dist_back_m,
        "distBottom_m": dist_bottom_m,
        "target_x": target_x,
        "target_y": target_y,
        "bound_w": bound_w,
        "bound_h": bound_h,
        "confidence": confidence,
    }


def _require_int(payload: dict[str, Any], field_name: str) -> int:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TelValidationError("schema", f"{field_name} must be int")
    return int(value)


def _require_float(payload: dict[str, Any], field_name: str) -> float:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (int | float)):
        raise TelValidationError("schema", f"{field_name} must be float/int")
    return float(value)


def _ensure_in_range(field_name: str, value: float, lower: float, upper: float) -> None:
    if not (lower <= value <= upper):
        raise TelValidationError("range", f"{field_name} out of range [{lower}, {upper}]")
