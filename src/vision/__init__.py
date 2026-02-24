"""Vision scaffold package for local development."""

from .tracker import SimpleTracker, TrackerResult
from .types import (
    Detection,
    NormalizedBBox,
    PixelBBox,
    VisMessage,
    VisState,
    normalize_bbox_xywh,
)

__all__ = [
    "Detection",
    "NormalizedBBox",
    "PixelBBox",
    "SimpleTracker",
    "TrackerResult",
    "VisMessage",
    "VisState",
    "normalize_bbox_xywh",
]
