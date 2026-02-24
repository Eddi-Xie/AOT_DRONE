from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import numpy as np

from .types import Detection


@runtime_checkable
class Detector(Protocol):
    def detect(self, frame: np.ndarray, frame_id: int) -> Sequence[Detection]: ...


class DummyDetector:
    """Stub detector for PR-V1. Always reports no detections."""

    def detect(self, frame: np.ndarray, frame_id: int) -> Sequence[Detection]:
        del frame
        del frame_id
        return ()
