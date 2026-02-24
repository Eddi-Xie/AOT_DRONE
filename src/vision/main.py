from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .camera import CameraSource
from .detector import Detector, DummyDetector
from .publisher import Publisher, create_publisher
from .tracker import SimpleTracker
from .types import VisMessage


@dataclass(frozen=True)
class VisionConfig:
    source: str = "webcam:0"
    vis_hz: float = 0.0
    preview: bool = False
    max_frames: int | None = None
    detect_hold_n: int = 30
    search_n: int = 0
    no_output: bool = False
    output: str | None = None


class FrameSource(Protocol):
    src_label: str

    def read(self) -> tuple[bool, Any]: ...

    def release(self) -> None: ...


def parse_args(argv: Sequence[str] | None = None) -> VisionConfig:
    parser = argparse.ArgumentParser(description="Local vision runner scaffold")
    parser.add_argument("--source", default="webcam:0", help="webcam:<index> or file:<path>")
    parser.add_argument(
        "--vis-hz",
        type=float,
        default=0.0,
        help="Monotonic time-based VIS publish cap. 0 means publish every frame.",
    )
    parser.add_argument("--preview", action="store_true", help="show local preview window")
    parser.add_argument("--max-frames", type=int, default=None, help="stop after N frames")
    parser.add_argument(
        "--detect-hold-n",
        type=int,
        default=30,
        help="frames to remain in TargetDetected before Tracking",
    )
    parser.add_argument(
        "--search-n",
        type=int,
        default=0,
        help="frames to stay in Searching after losing detections in Tracking",
    )
    parser.add_argument("--no-output", action="store_true", help="disable stdout VIS output")
    parser.add_argument(
        "--output",
        default=None,
        help="optional sink, currently supports jsonl:<path>",
    )
    args = parser.parse_args(argv)

    if args.vis_hz < 0:
        raise ValueError("--vis-hz must be >= 0")
    if args.max_frames is not None and args.max_frames <= 0:
        raise ValueError("--max-frames must be > 0")
    if args.detect_hold_n < 0:
        raise ValueError("--detect-hold-n must be >= 0")
    if args.search_n < 0:
        raise ValueError("--search-n must be >= 0")

    return VisionConfig(
        source=args.source,
        vis_hz=args.vis_hz,
        preview=args.preview,
        max_frames=args.max_frames,
        detect_hold_n=args.detect_hold_n,
        search_n=args.search_n,
        no_output=args.no_output,
        output=args.output,
    )


def run_loop(
    config: VisionConfig,
    detector: Detector | None = None,
    tracker: SimpleTracker | None = None,
    publisher: Publisher | None = None,
    frame_source: FrameSource | None = None,
    clock: Callable[[], float] | None = None,
) -> int:
    detector_impl = detector or DummyDetector()
    tracker_impl = tracker or SimpleTracker(
        detect_hold_n=config.detect_hold_n,
        search_n=config.search_n,
    )
    publisher_impl = publisher or create_publisher(
        no_output=config.no_output,
        output_spec=config.output,
    )
    source = frame_source or CameraSource.open(config.source)
    now_fn = clock or time.monotonic

    cv2_module: Any | None = None
    draw_overlay = None
    if config.preview:
        _ensure_preview_available()
        import cv2 as cv2_module_local

        from .overlay import draw_overlay as draw_overlay_local

        cv2_module = cv2_module_local
        draw_overlay = draw_overlay_local

    emit_interval_s = (1.0 / config.vis_hz) if config.vis_hz > 0 else 0.0
    next_emit_ts = 0.0
    frame_id = 0
    emitted_count = 0

    try:
        while True:
            ok, frame = source.read()
            if not ok or frame is None:
                break

            frame_id += 1
            img_h, img_w = frame.shape[:2]
            detections = list(detector_impl.detect(frame, frame_id=frame_id))
            tracker_result = tracker_impl.update(detections=detections, img_w=img_w, img_h=img_h)

            now_s = now_fn()
            message = VisMessage(
                ts=now_s,
                state=tracker_result.state,
                bbox=tracker_result.bbox,
                conf=tracker_result.conf,
                track_id=tracker_result.track_id,
                frame_id=frame_id,
                src=source.src_label,
                img_wh=(img_w, img_h),
            )

            if emit_interval_s == 0.0 or now_s >= next_emit_ts:
                publisher_impl.publish(message)
                emitted_count += 1
                if emit_interval_s > 0.0:
                    next_emit_ts = now_s + emit_interval_s

            if config.preview and cv2_module is not None and draw_overlay is not None:
                frame_with_overlay = draw_overlay(
                    frame=frame,
                    state=tracker_result.state,
                    bbox=tracker_result.bbox,
                    conf=tracker_result.conf,
                    track_id=tracker_result.track_id,
                    frame_id=frame_id,
                )
                cv2_module.imshow("vision-preview", frame_with_overlay)
                key = cv2_module.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break

            if config.max_frames is not None and frame_id >= config.max_frames:
                break
    finally:
        source.release()
        publisher_impl.close()
        if config.preview and cv2_module is not None:
            cv2_module.destroyAllWindows()

    return emitted_count


def _ensure_preview_available() -> None:
    if sys.platform.startswith("linux"):
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        if not has_display:
            raise RuntimeError(
                "--preview requested but no GUI display detected " "(DISPLAY/WAYLAND_DISPLAY unset)"
            )

    try:
        import cv2
    except Exception as exc:  # pragma: no cover - import error is environment-specific
        raise RuntimeError("--preview requested but OpenCV GUI backend is unavailable") from exc

    required_attrs = ("imshow", "waitKey", "destroyAllWindows")
    missing = [name for name in required_attrs if not hasattr(cv2, name)]
    if missing:
        missing_joined = ", ".join(missing)
        raise RuntimeError(
            "--preview requested but OpenCV GUI functions are missing: " f"{missing_joined}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        config = parse_args(argv)
        run_loop(config)
    except Exception as exc:
        print(f"vision runner failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
