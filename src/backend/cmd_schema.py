from __future__ import annotations

import json
import struct
from typing import Any

CONTROL_MODES = {0, 1, 2, 3}
MODE_TRACKING = 1
MODE_LAND_SAFELY = 2
DEFAULT_DESIRED_MODE = MODE_LAND_SAFELY


def build_cmd_payload(
    seq: int,
    timestamp_s: float,
    intent: dict[str, Any] | None,
    vis_snapshot: dict[str, Any] | None,
    vis_age_s: float | None,
    vis_fresh_s: float,
) -> tuple[dict[str, Any], str | None]:
    if seq < 0:
        raise ValueError("seq must be >= 0")
    if vis_fresh_s < 0:
        raise ValueError("vis_fresh_s must be >= 0")

    desired_mode = DEFAULT_DESIRED_MODE
    arm: bool | None = None
    setpoints: dict[str, Any] | None = None
    if intent is not None:
        desired_mode = int(intent["desired_mode"])
        if desired_mode not in CONTROL_MODES:
            raise ValueError("desired_mode is outside ControlMode enum")
        if "arm" in intent and intent["arm"] is not None:
            arm = bool(intent["arm"])
        if "setpoints" in intent and intent["setpoints"] is not None:
            if not isinstance(intent["setpoints"], dict):
                raise ValueError("setpoints must be an object")
            setpoints = dict(intent["setpoints"])

    is_vis_fresh = vis_snapshot is not None and vis_age_s is not None and vis_age_s <= vis_fresh_s
    blocked_reason: str | None = None
    if desired_mode == MODE_TRACKING and not is_vis_fresh:
        desired_mode = MODE_LAND_SAFELY
        blocked_reason = "no_vis" if vis_snapshot is None else "stale_vis"

    payload: dict[str, Any] = {
        "type": "CMD",
        "seq": int(seq),
        "timestamp_s": float(timestamp_s),
        "desired_mode": desired_mode,
    }
    if arm is not None:
        payload["arm"] = arm
    if setpoints is not None:
        payload["setpoints"] = setpoints
    if is_vis_fresh and vis_snapshot is not None:
        payload["tracking"] = _build_tracking_object(vis_snapshot)

    return payload, blocked_reason


def frame_cmd_payload(payload: dict[str, Any], max_payload_bytes: int = 4096) -> bytes:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if not (1 <= len(raw) <= max_payload_bytes):
        raise ValueError(f"CMD payload length out of range [1, {max_payload_bytes}]")
    return struct.pack(">I", len(raw)) + raw


def _build_tracking_object(vis_snapshot: dict[str, Any]) -> dict[str, Any]:
    required = (
        "tracking_state",
        "loc_x",
        "loc_y",
        "bound_w",
        "bound_h",
        "confidence",
        "seq",
        "timestamp_s",
    )
    missing = [name for name in required if name not in vis_snapshot]
    if missing:
        missing_joined = ", ".join(sorted(missing))
        raise ValueError(f"VIS snapshot missing tracking fields: {missing_joined}")

    return {
        "tracking_state": int(vis_snapshot["tracking_state"]),
        "loc_x": float(vis_snapshot["loc_x"]),
        "loc_y": float(vis_snapshot["loc_y"]),
        "bound_w": float(vis_snapshot["bound_w"]),
        "bound_h": float(vis_snapshot["bound_h"]),
        "confidence": float(vis_snapshot["confidence"]),
        "vis_seq": int(vis_snapshot["seq"]),
        "vis_timestamp_s": float(vis_snapshot["timestamp_s"]),
    }
