from __future__ import annotations

import numpy as np

from .types import NormalizedBBox, VisState


def draw_overlay(
    frame: np.ndarray,
    state: VisState,
    bbox: NormalizedBBox,
    conf: float,
    track_id: int,
    frame_id: int | None = None,
) -> np.ndarray:
    import cv2

    out = frame.copy()
    img_h, img_w = out.shape[:2]

    label = f"state={state.value} conf={conf:.2f} id={track_id}"
    if frame_id is not None:
        label = f"frame={frame_id} {label}"
    cv2.putText(out, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

    if bbox.w <= 0.0 or bbox.h <= 0.0:
        return out

    x1 = int(round((bbox.cx - (bbox.w / 2.0)) * img_w))
    y1 = int(round((bbox.cy - (bbox.h / 2.0)) * img_h))
    x2 = int(round((bbox.cx + (bbox.w / 2.0)) * img_w))
    y2 = int(round((bbox.cy + (bbox.h / 2.0)) * img_h))

    x1 = _clamp(x1, 0, img_w - 1)
    y1 = _clamp(y1, 0, img_h - 1)
    x2 = _clamp(x2, 0, img_w - 1)
    y2 = _clamp(y2, 0, img_h - 1)

    if x2 <= x1:
        x2 = min(img_w - 1, x1 + 1)
    if y2 <= y1:
        y2 = min(img_h - 1, y1 + 1)

    cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 0), 2)
    return out


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))
