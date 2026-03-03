from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .detector import Detector
from .types import Detection, PixelBBox


class YoloDetector(Detector):
    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        target_class: int = 0,
        conf_threshold: float = 0.5,
        infer_width: int = 640,
        infer_size: int | None = None,
    ) -> None:
        if not model_path:
            raise ValueError("model_path must not be empty")
        if target_class < 0:
            raise ValueError("target_class must be >= 0")
        if not (0.0 <= conf_threshold <= 1.0):
            raise ValueError("conf_threshold must be in [0, 1]")
        if infer_width <= 0:
            raise ValueError("infer_width must be > 0")
        if infer_size is not None and infer_size <= 0:
            raise ValueError("infer_size must be > 0 when provided")

        try:
            from ultralytics import YOLO
        except Exception as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError(
                "detect mode requires 'ultralytics'; install src/vision/requirements.txt"
            ) from exc

        resolved_model_spec, model_source = _resolve_model_spec(model_path)
        print(f"vision detect: YOLO model source={model_source}", file=sys.stderr)

        self.target_class = int(target_class)
        self.conf_threshold = float(conf_threshold)
        self.infer_width = int(infer_width)
        self.infer_size = int(infer_size) if infer_size is not None else None
        self.model_spec = resolved_model_spec
        try:
            self._model = YOLO(self.model_spec)
        except Exception as exc:
            raise RuntimeError(
                "failed to load YOLO model "
                f"from --model-path={model_path!r} (resolved={self.model_spec!r})"
            ) from exc

    def detect(self, frame: np.ndarray, frame_id: int) -> Sequence[Detection]:
        del frame_id

        imgsz = self._resolve_imgsz(frame)
        results = self._model.predict(
            source=frame,
            verbose=False,
            conf=self.conf_threshold,
            classes=[self.target_class],
            imgsz=imgsz,
        )
        if not results:
            return ()

        boxes_obj = getattr(results[0], "boxes", None)
        if boxes_obj is None:
            return ()

        xyxy = _to_list(getattr(boxes_obj, "xyxy", None))
        confs = _to_list(getattr(boxes_obj, "conf", None))
        classes = _to_list(getattr(boxes_obj, "cls", None))
        if xyxy is None or confs is None or classes is None:
            return ()

        detections: list[Detection] = []
        for coords, conf, class_id in zip(xyxy, confs, classes, strict=False):
            if int(class_id) != self.target_class:
                continue

            score = float(conf)
            if score < self.conf_threshold:
                continue

            x1 = float(coords[0])
            y1 = float(coords[1])
            x2 = float(coords[2])
            y2 = float(coords[3])
            width = max(0.0, x2 - x1)
            height = max(0.0, y2 - y1)
            if width <= 0.0 or height <= 0.0:
                continue

            detections.append(
                Detection(
                    bbox=PixelBBox(x=x1, y=y1, w=width, h=height),
                    confidence=score,
                    track_id=1,
                )
            )

        return tuple(detections)

    def _resolve_imgsz(self, frame: np.ndarray) -> int | tuple[int, int]:
        if self.infer_size is not None:
            return self.infer_size

        frame_h, frame_w = frame.shape[:2]
        if frame_w <= 0 or frame_h <= 0:
            return self.infer_width

        scaled_h = max(1, int(round((float(frame_h) * float(self.infer_width)) / float(frame_w))))
        return (scaled_h, self.infer_width)


def _to_list(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, list):
        return None
    return value


def _resolve_model_spec(
    model_path: str,
    *,
    cwd: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[str, str]:
    raw_path = Path(model_path).expanduser()
    cwd_path = cwd or Path.cwd()
    repo_root_path = repo_root or _repo_root()

    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.append(cwd_path / raw_path)
        repo_candidate = repo_root_path / raw_path
        if repo_candidate not in candidates:
            candidates.append(repo_candidate)

    for candidate in candidates:
        if candidate.is_file():
            resolved = candidate.resolve()
            return str(resolved), str(resolved)

    if raw_path.is_absolute() or raw_path.parent != Path("."):
        attempted = ", ".join(str(path.resolve()) for path in candidates)
        raise FileNotFoundError(
            f"YOLO model file not found for --model-path={model_path!r}. Looked in: {attempted}"
        )

    return (
        model_path,
        f"{model_path} (no local file in cwd/repo-root, delegating to ultralytics)",
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
