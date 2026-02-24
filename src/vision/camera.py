from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SourceSpec:
    kind: str
    value: str

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.value}"


def parse_source(source: str) -> SourceSpec:
    raw = source.strip()
    if not raw:
        raise ValueError("source must not be empty")

    if ":" not in raw:
        return SourceSpec(kind="file", value=raw)

    kind, value = raw.split(":", 1)
    kind = kind.strip().lower()
    value = value.strip()

    if kind == "webcam":
        if not value:
            raise ValueError("webcam source must include an integer index, for example webcam:0")
        return SourceSpec(kind="webcam", value=str(int(value)))
    if kind == "file":
        if not value:
            raise ValueError("file source must include a path, for example file:assets/test.mp4")
        return SourceSpec(kind="file", value=value)
    raise ValueError("unsupported source kind; use webcam:<index> or file:<path>")


class CameraSource:
    def __init__(self, spec: SourceSpec) -> None:
        self.spec = spec
        self._capture = self._open_capture(spec)

    @property
    def src_label(self) -> str:
        return self.spec.label

    def _open_capture(self, spec: SourceSpec) -> Any:
        import cv2

        if spec.kind == "webcam":
            capture = cv2.VideoCapture(int(spec.value))
        else:
            capture = cv2.VideoCapture(spec.value)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"unable to open source '{spec.label}'")
        return capture

    def read(self) -> tuple[bool, np.ndarray | None]:
        ok, frame = self._capture.read()
        if not ok:
            return False, None
        return True, frame

    def release(self) -> None:
        self._capture.release()

    @classmethod
    def open(cls, source: str) -> CameraSource:
        return cls(parse_source(source))
