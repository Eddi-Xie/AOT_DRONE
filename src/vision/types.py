from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class VisState(str, Enum):
    NO_TARGET = "NoTarget"
    TARGET_DETECTED = "TargetDetected"
    TRACKING = "Tracking"
    SEARCHING = "Searching"


@dataclass(frozen=True)
class PixelBBox:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class NormalizedBBox:
    cx: float = 0.0
    cy: float = 0.0
    w: float = 0.0
    h: float = 0.0

    @staticmethod
    def zero() -> NormalizedBBox:
        return NormalizedBBox()

    def to_dict(self) -> dict[str, float]:
        return {
            "cx": clamp01(self.cx),
            "cy": clamp01(self.cy),
            "w": clamp01(self.w),
            "h": clamp01(self.h),
        }


def normalize_bbox_xywh(
    x: float,
    y: float,
    w: float,
    h: float,
    img_w: int,
    img_h: int,
) -> NormalizedBBox:
    if img_w <= 0 or img_h <= 0:
        raise ValueError("image dimensions must be positive")

    img_w_f = float(img_w)
    img_h_f = float(img_h)
    cx = (float(x) + (float(w) / 2.0)) / img_w_f
    cy = (float(y) + (float(h) / 2.0)) / img_h_f
    nw = float(w) / img_w_f
    nh = float(h) / img_h_f
    return NormalizedBBox(cx=clamp01(cx), cy=clamp01(cy), w=clamp01(nw), h=clamp01(nh))


@dataclass(frozen=True)
class Detection:
    bbox: PixelBBox
    confidence: float = 0.0
    track_id: int = 1


@dataclass
class VisMessage:
    ts: float
    state: VisState = VisState.NO_TARGET
    bbox: NormalizedBBox = field(default_factory=NormalizedBBox.zero)
    conf: float = 0.0
    track_id: int = 0
    frame_id: int | None = None
    src: str | None = None
    img_wh: tuple[int, int] | None = None
    type: str = field(default="VIS", init=False)
    proto_ver: int = field(default=1, init=False)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "proto_ver": self.proto_ver,
            "ts": float(self.ts),
            "state": self.state.value,
            "bbox": self.bbox.to_dict(),
            "conf": clamp01(self.conf),
            "track_id": max(0, int(self.track_id)),
        }
        if self.frame_id is not None:
            payload["frame_id"] = int(self.frame_id)
        if self.src is not None:
            payload["src"] = self.src
        if self.img_wh is not None:
            payload["img_wh"] = [int(self.img_wh[0]), int(self.img_wh[1])]
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))
