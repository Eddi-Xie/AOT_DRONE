from __future__ import annotations

import numpy as np

from src.vision.opencv_tracker import _to_cv_bbox
from src.vision.types import PixelBBox


def test_to_cv_bbox_rounds_and_clamps_to_image_bounds() -> None:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    bbox = PixelBBox(x=1919.7, y=1079.3, w=100.9, h=50.2)

    converted = _to_cv_bbox(bbox=bbox, frame=frame)

    assert converted == (1919, 1079, 1, 1)


def test_to_cv_bbox_rejects_non_positive_boxes() -> None:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    assert _to_cv_bbox(bbox=PixelBBox(x=10.0, y=10.0, w=0.0, h=4.0), frame=frame) is None
    assert _to_cv_bbox(bbox=PixelBBox(x=10.0, y=10.0, w=4.0, h=-2.0), frame=frame) is None


def test_to_cv_bbox_rejects_invalid_frame_shape() -> None:
    assert _to_cv_bbox(bbox=PixelBBox(x=1.0, y=1.0, w=2.0, h=2.0), frame=object()) is None
