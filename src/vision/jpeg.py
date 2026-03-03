from __future__ import annotations

from typing import Any

import numpy as np


def clamp_quality(quality: int) -> int:
    return max(1, min(100, int(quality)))


def encode_jpeg(frame: np.ndarray, quality: int) -> bytes:
    import cv2

    quality_clamped = clamp_quality(quality)
    ok, encoded = cv2.imencode(
        ".jpg",
        frame,
        [int(cv2.IMWRITE_JPEG_QUALITY), quality_clamped],
    )
    if not ok:
        raise RuntimeError("OpenCV failed to encode JPEG frame")

    if not hasattr(encoded, "tobytes"):
        raise RuntimeError("OpenCV returned an unexpected JPEG buffer type")

    return _to_bytes(encoded)


def _to_bytes(value: Any) -> bytes:
    return bytes(value.tobytes())
