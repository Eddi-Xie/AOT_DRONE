from __future__ import annotations

import math
from collections.abc import Sequence

from .types import Detection, PixelBBox, clamp01


def to_norm_center(bbox: PixelBBox, img_w: int, img_h: int) -> tuple[float, float]:
    if img_w <= 0 or img_h <= 0:
        raise ValueError("image dimensions must be positive")

    cx = (float(bbox.x) + (float(bbox.w) / 2.0)) / float(img_w)
    cy = (float(bbox.y) + (float(bbox.h) / 2.0)) / float(img_h)
    return clamp01(cx), clamp01(cy)


def score_box(
    bbox: PixelBBox,
    img_w: int,
    img_h: int,
    desired_cx: float = 0.5,
    desired_cy: float = 0.5,
) -> float:
    cx, cy = to_norm_center(bbox=bbox, img_w=img_w, img_h=img_h)
    dist_to_desired = math.hypot(cx - float(desired_cx), cy - float(desired_cy))
    box_area_norm = clamp01((float(bbox.w) * float(bbox.h)) / float(img_w * img_h))
    return dist_to_desired - (0.25 * box_area_norm)


def pick_best_box(
    detections: Sequence[Detection],
    img_w: int,
    img_h: int,
    desired_cx: float = 0.5,
    desired_cy: float = 0.5,
) -> Detection | None:
    if not detections:
        return None

    return min(
        detections,
        key=lambda item: score_box(
            bbox=item.bbox,
            img_w=img_w,
            img_h=img_h,
            desired_cx=desired_cx,
            desired_cy=desired_cy,
        ),
    )
