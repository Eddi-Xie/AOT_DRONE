import json

import pytest

from src.vision.types import NormalizedBBox, VisMessage, VisState, normalize_bbox_xywh


def _assert_vis_payload_contract(payload: dict[str, object]) -> None:
    required = {"type", "proto_ver", "ts", "state", "bbox", "conf", "track_id"}
    assert required.issubset(payload.keys())

    assert payload["type"] == "VIS"
    assert isinstance(payload["type"], str)

    assert payload["proto_ver"] == 1
    assert isinstance(payload["proto_ver"], int)

    assert isinstance(payload["ts"], float)

    allowed_states = {state.value for state in VisState}
    assert payload["state"] in allowed_states
    assert isinstance(payload["state"], str)

    bbox = payload["bbox"]
    assert isinstance(bbox, dict)
    assert set(bbox.keys()) == {"cx", "cy", "w", "h"}
    for key in ("cx", "cy", "w", "h"):
        value = bbox[key]
        assert isinstance(value, float)
        assert 0.0 <= value <= 1.0

    conf = payload["conf"]
    assert isinstance(conf, float)
    assert 0.0 <= conf <= 1.0

    track_id = payload["track_id"]
    assert isinstance(track_id, int)
    assert track_id >= 0


def test_vis_message_schema_defaults() -> None:
    message = VisMessage(
        ts=12.5,
        state=VisState.NO_TARGET,
        bbox=NormalizedBBox.zero(),
        conf=0.0,
        track_id=0,
        frame_id=7,
        src="webcam:0",
        img_wh=(640, 480),
    )

    payload = message.to_dict()
    _assert_vis_payload_contract(payload)
    assert payload["state"] == "NoTarget"
    assert payload["bbox"] == {"cx": 0.0, "cy": 0.0, "w": 0.0, "h": 0.0}
    assert payload["conf"] == 0.0
    assert payload["track_id"] == 0

    round_trip = json.loads(message.to_json())
    assert round_trip["type"] == "VIS"
    assert round_trip["bbox"]["cx"] == 0.0


def test_vis_message_schema_contract_clamps_types_and_ranges() -> None:
    message = VisMessage(
        ts=3.0,
        state=VisState.TRACKING,
        bbox=NormalizedBBox(cx=-0.2, cy=0.3, w=1.4, h=0.5),
        conf=1.8,
        track_id=-7,
    )
    payload = message.to_dict()
    _assert_vis_payload_contract(payload)


def test_normalize_bbox_clamps_to_unit_range() -> None:
    bbox = normalize_bbox_xywh(x=-20, y=50, w=500, h=900, img_w=320, img_h=240)
    assert bbox.cx == pytest.approx(0.71875)
    assert bbox.cy == 1.0
    assert bbox.w == 1.0
    assert bbox.h == 1.0

    for value in (bbox.cx, bbox.cy, bbox.w, bbox.h):
        assert 0.0 <= value <= 1.0
