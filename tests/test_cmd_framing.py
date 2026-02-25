import json
import struct

from src.backend.cmd_schema import build_cmd_payload, frame_cmd_payload


def test_cmd_framing_includes_length_prefix_and_valid_json() -> None:
    payload, blocked_reason = build_cmd_payload(
        seq=101,
        timestamp_s=50.5,
        intent={"desired_mode": 0, "arm": True},
        vis_snapshot=None,
        vis_age_s=None,
        vis_fresh_s=0.25,
    )
    assert blocked_reason is None

    framed = frame_cmd_payload(payload, max_payload_bytes=4096)
    assert len(framed) > 4

    payload_len = struct.unpack(">I", framed[:4])[0]
    body = framed[4:]
    assert payload_len == len(body)

    decoded = json.loads(body.decode("utf-8"))
    assert decoded["type"] == "CMD"
    assert decoded["seq"] == 101
    assert isinstance(decoded["timestamp_s"], float)
    assert decoded["desired_mode"] == 0


def test_cmd_payload_includes_tracking_when_vis_is_fresh() -> None:
    vis = {
        "type": "VIS",
        "seq": 55,
        "timestamp_s": 10.2,
        "tracking_state": 3,
        "loc_x": 0.25,
        "loc_y": -0.12,
        "bound_w": 0.2,
        "bound_h": 0.3,
        "confidence": 0.88,
    }
    payload, blocked_reason = build_cmd_payload(
        seq=12,
        timestamp_s=99.0,
        intent={"desired_mode": 1},
        vis_snapshot=vis,
        vis_age_s=0.1,
        vis_fresh_s=0.25,
    )
    assert blocked_reason is None
    assert payload["desired_mode"] == 1
    assert payload["tracking"]["tracking_state"] == 3
    assert payload["tracking"]["vis_seq"] == 55
    assert payload["tracking"]["vis_timestamp_s"] == 10.2


def test_cmd_payload_blocks_tracking_when_vis_is_stale() -> None:
    vis = {
        "type": "VIS",
        "seq": 2,
        "timestamp_s": 1.0,
        "tracking_state": 3,
        "loc_x": 0.0,
        "loc_y": 0.0,
        "bound_w": 0.2,
        "bound_h": 0.2,
        "confidence": 0.5,
    }
    payload, blocked_reason = build_cmd_payload(
        seq=13,
        timestamp_s=100.0,
        intent={"desired_mode": 1},
        vis_snapshot=vis,
        vis_age_s=0.5,
        vis_fresh_s=0.25,
    )
    assert payload["desired_mode"] == 2
    assert blocked_reason == "stale_vis"
