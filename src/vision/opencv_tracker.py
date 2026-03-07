from __future__ import annotations

from typing import Any

from .types import PixelBBox

TRACKER_CHOICES = ("none", "kcf", "csrt")


class OpenCvTracker:
    def __init__(self, tracker_type: str = "kcf") -> None:
        tracker_type_normalized = tracker_type.strip().lower()
        if tracker_type_normalized not in TRACKER_CHOICES:
            raise ValueError(f"unsupported tracker type '{tracker_type}'")

        self.tracker_type = tracker_type_normalized
        self._factory: Any | None = None
        self._tracker: Any | None = None

        if self.tracker_type == "none":
            return

        try:
            import cv2
        except Exception as exc:  # pragma: no cover - import depends on local runtime
            raise RuntimeError(
                "tracker mode requires OpenCV; install opencv-contrib-python for KCF/CSRT support"
            ) from exc

        factory = _resolve_tracker_factory(cv2_module=cv2, tracker_type=self.tracker_type)
        if factory is None:
            raise RuntimeError(
                f"tracker '{self.tracker_type}' is unavailable in this OpenCV build; "
                "install opencv-contrib-python"
            )
        self._factory = factory

    @property
    def active(self) -> bool:
        return self._tracker is not None

    def initialize(self, frame: Any, bbox: PixelBBox) -> bool:
        if self.tracker_type == "none":
            self._tracker = None
            return False

        if self._factory is None:
            return False

        cv_bbox = _to_cv_bbox(bbox=bbox, frame=frame)
        if cv_bbox is None:
            self._tracker = None
            return False

        tracker = self._factory()
        ok = bool(tracker.init(frame, cv_bbox))
        if not ok:
            self._tracker = None
            return False

        self._tracker = tracker
        return True

    def update(self, frame: Any) -> tuple[bool, PixelBBox | None]:
        if self._tracker is None:
            return False, None

        ok, bbox = self._tracker.update(frame)
        if not ok:
            self._tracker = None
            return False, None

        x, y, w, h = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        if w <= 0.0 or h <= 0.0:
            self._tracker = None
            return False, None

        return True, PixelBBox(x=x, y=y, w=w, h=h)

    def reset(self) -> None:
        self._tracker = None


def _to_cv_bbox(bbox: PixelBBox, frame: Any) -> tuple[int, int, int, int] | None:
    if not hasattr(frame, "shape"):
        return None

    shape = frame.shape
    if not isinstance(shape, tuple) or len(shape) < 2:
        return None
    img_h = int(shape[0])
    img_w = int(shape[1])
    if img_w <= 0 or img_h <= 0:
        return None

    x = int(round(float(bbox.x)))
    y = int(round(float(bbox.y)))
    w = int(round(float(bbox.w)))
    h = int(round(float(bbox.h)))
    if w <= 0 or h <= 0:
        return None

    x = max(0, min(x, img_w - 1))
    y = max(0, min(y, img_h - 1))
    w = min(w, img_w - x)
    h = min(h, img_h - y)
    if w <= 0 or h <= 0:
        return None

    return (x, y, w, h)


def _resolve_tracker_factory(cv2_module: Any, tracker_type: str) -> Any | None:
    candidates = {
        "kcf": ("TrackerKCF_create", "legacy.TrackerKCF_create"),
        "csrt": ("TrackerCSRT_create", "legacy.TrackerCSRT_create"),
    }[tracker_type]

    for attr_path in candidates:
        current: Any = cv2_module
        found = True
        for part in attr_path.split("."):
            if not hasattr(current, part):
                found = False
                break
            current = getattr(current, part)
        if found and callable(current):
            return current
    return None
