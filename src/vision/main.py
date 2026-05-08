from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

# Wire-contract constants are owned by the backend's protocol_constants module
# (single source of truth). Vision imports the JPEG size cap from there so the
# default can never drift between sender (vision) and receiver (backend).
from src.backend.protocol_constants import VIDEO_MAX_JPEG_BYTES_DEFAULT

from .camera import CameraSource
from .detector import Detector
from .frame_pusher import FramePusher
from .opencv_tracker import TRACKER_CHOICES, OpenCvTracker
from .patterns import PATTERN_CHOICES, PatternGenerator
from .publisher import Publisher, create_publisher
from .types import VisMessage, VisState, clamp01
from .udp_vis_sender import VisUdpSender
from .vision_pipeline import TrackerLike, VisionPipeline, VisionPipelineConfig
from .yolo_detector import YoloDetector

MODE_CHOICES = ("pattern", "detect")

_TRACKING_STATE_TO_CODE = {
    VisState.NO_TARGET: 1,
    VisState.TARGET_DETECTED: 2,
    VisState.TRACKING: 3,
    VisState.SEARCHING: 4,
}
_TRACKING_STATE_TRACKING = 3


@dataclass(frozen=True)
class VisionConfig:
    source: str = "webcam:0"
    mode: str = "detect"
    vis_hz: float = 20.0
    frame_fps: float = 10.0
    jpeg_quality: int = 80
    backend_http: str = "http://127.0.0.1:8000"
    backend_frame_endpoint: str = "/api/frame"
    backend_video_max_jpeg_bytes: int = VIDEO_MAX_JPEG_BYTES_DEFAULT
    vis_udp_host: str = "127.0.0.1"
    vis_udp_port: int = 9003
    pattern: str = "none"
    no_frame_push: bool = False
    no_vis_udp: bool = False
    preview: bool = False
    max_frames: int | None = None
    model_path: str = "yolov8n.pt"
    target_class: int = 0
    conf_threshold: float = 0.5
    tracker: str = "kcf"
    infer_width: int = 640
    infer_size: int | None = None
    detect_every_n: int = 1
    detect_hold_n: int = 30
    search_n: int = 30
    desired_cx: float = 0.5
    desired_cy: float = 0.5
    no_output: bool = False
    output: str | None = None


class FrameSource(Protocol):
    src_label: str

    def read(self) -> tuple[bool, Any]: ...

    def release(self) -> None: ...


class VisSender(Protocol):
    def send(self, vis_dict: dict[str, Any]) -> None: ...

    def close(self) -> None: ...


class FramePushSink(Protocol):
    def push_frame(self, frame: Any, quality: int) -> bool: ...

    def close(self) -> None: ...


def parse_args(argv: Sequence[str] | None = None) -> VisionConfig:
    parser = argparse.ArgumentParser(description="Local vision runner scaffold")
    parser.add_argument("--source", default="webcam:0", help="webcam:<index> or file:<path>")
    parser.add_argument(
        "--mode",
        choices=MODE_CHOICES,
        default="detect",
        help="vision mode: synthetic pattern or YOLO+tracker detect",
    )
    parser.add_argument(
        "--backend-http",
        default=_read_env_str("VISION_BACKEND_HTTP", "http://127.0.0.1:8000"),
        help="backend base URL for frame push",
    )
    parser.add_argument(
        "--backend-frame-endpoint",
        default="/api/frame",
        help="backend frame ingest endpoint path",
    )
    parser.add_argument(
        "--vis-udp-host",
        default=_read_env_str("VISION_VIS_UDP_HOST", "127.0.0.1"),
        help="VIS UDP destination host",
    )
    parser.add_argument(
        "--vis-udp-port",
        type=int,
        default=_read_env_int("VISION_VIS_UDP_PORT", 9003),
        help="VIS UDP destination port",
    )
    parser.add_argument(
        "--vis-hz",
        type=float,
        default=_read_env_float("VISION_VIS_HZ", 20.0),
        help="Monotonic time-based VIS publish cap. 0 means publish every frame.",
    )
    parser.add_argument(
        "--frame-fps",
        type=float,
        default=_read_env_float("VISION_FRAME_FPS", 10.0),
        help="Monotonic frame push cap. 0 means push every frame.",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=_read_env_int("VISION_JPEG_QUALITY", 80),
        help="JPEG quality for backend frame ingest (1..100).",
    )
    parser.add_argument(
        "--pattern",
        choices=PATTERN_CHOICES,
        default="none",
        help="synthetic VIS pattern mode for local validation",
    )
    parser.add_argument(
        "--model-path",
        default="yolov8n.pt",
        help="YOLO model path used in detect mode",
    )
    parser.add_argument(
        "--target-class",
        type=int,
        default=0,
        help="COCO class id to keep (default 0=person)",
    )
    parser.add_argument(
        "--conf-threshold",
        type=float,
        default=0.5,
        help="minimum detector confidence in [0,1]",
    )
    parser.add_argument(
        "--tracker",
        choices=TRACKER_CHOICES,
        default="kcf",
        help="tracking backend for detect mode",
    )
    parser.add_argument(
        "--infer-width",
        type=int,
        default=640,
        help="YOLO inference width while preserving aspect ratio",
    )
    parser.add_argument(
        "--infer-size",
        type=int,
        default=None,
        help="optional square inference size overriding --infer-width",
    )
    parser.add_argument(
        "--detect-every-n",
        type=int,
        default=1,
        help="run detector every N frames",
    )
    parser.add_argument(
        "--detect-hold-n",
        type=int,
        default=30,
        help="frames to remain in TargetDetected before Tracking",
    )
    parser.add_argument(
        "--search-n",
        type=int,
        default=30,
        help="frames to stay in Searching before NoTarget",
    )
    parser.add_argument(
        "--desired-cx",
        type=float,
        default=0.5,
        help="desired normalized X point used for best-box selection",
    )
    parser.add_argument(
        "--desired-cy",
        type=float,
        default=0.5,
        help="desired normalized Y point used for best-box selection",
    )
    parser.add_argument("--no-frame-push", action="store_true", help="disable HTTP frame push")
    parser.add_argument("--no-vis-udp", action="store_true", help="disable VIS UDP send")
    parser.add_argument("--preview", action="store_true", help="show local preview window")
    parser.add_argument("--max-frames", type=int, default=None, help="stop after N frames")
    parser.add_argument("--no-output", action="store_true", help="disable stdout VIS output")
    parser.add_argument(
        "--output",
        default=None,
        help="optional sink, currently supports jsonl:<path>",
    )
    args = parser.parse_args(argv)

    if args.vis_hz < 0:
        raise ValueError("--vis-hz must be >= 0")
    if args.frame_fps < 0:
        raise ValueError("--frame-fps must be >= 0")
    if args.jpeg_quality < 1 or args.jpeg_quality > 100:
        raise ValueError("--jpeg-quality must be in [1, 100]")
    if args.vis_udp_port <= 0 or args.vis_udp_port > 65535:
        raise ValueError("--vis-udp-port must be in [1, 65535]")
    if args.max_frames is not None and args.max_frames <= 0:
        raise ValueError("--max-frames must be > 0")
    if args.target_class < 0:
        raise ValueError("--target-class must be >= 0")
    if not (0.0 <= args.conf_threshold <= 1.0):
        raise ValueError("--conf-threshold must be in [0, 1]")
    if args.infer_width <= 0:
        raise ValueError("--infer-width must be > 0")
    if args.infer_size is not None and args.infer_size <= 0:
        raise ValueError("--infer-size must be > 0 when provided")
    if args.detect_every_n <= 0:
        raise ValueError("--detect-every-n must be >= 1")
    if args.detect_hold_n < 0:
        raise ValueError("--detect-hold-n must be >= 0")
    if args.search_n < 0:
        raise ValueError("--search-n must be >= 0")
    if not (0.0 <= args.desired_cx <= 1.0):
        raise ValueError("--desired-cx must be in [0, 1]")
    if not (0.0 <= args.desired_cy <= 1.0):
        raise ValueError("--desired-cy must be in [0, 1]")

    return VisionConfig(
        source=args.source,
        mode=args.mode,
        backend_http=args.backend_http,
        backend_frame_endpoint=args.backend_frame_endpoint,
        vis_udp_host=args.vis_udp_host,
        vis_udp_port=args.vis_udp_port,
        vis_hz=args.vis_hz,
        frame_fps=args.frame_fps,
        jpeg_quality=args.jpeg_quality,
        pattern=args.pattern,
        model_path=args.model_path,
        target_class=args.target_class,
        conf_threshold=args.conf_threshold,
        tracker=args.tracker,
        infer_width=args.infer_width,
        infer_size=args.infer_size,
        detect_every_n=args.detect_every_n,
        detect_hold_n=args.detect_hold_n,
        search_n=args.search_n,
        desired_cx=args.desired_cx,
        desired_cy=args.desired_cy,
        no_frame_push=args.no_frame_push,
        no_vis_udp=args.no_vis_udp,
        backend_video_max_jpeg_bytes=_read_env_int(
            "BACKEND_VIDEO_MAX_JPEG_BYTES", VIDEO_MAX_JPEG_BYTES_DEFAULT
        ),
        preview=args.preview,
        max_frames=args.max_frames,
        no_output=args.no_output,
        output=args.output,
    )


def run_loop(
    config: VisionConfig,
    detector: Detector | None = None,
    tracker: TrackerLike | None = None,
    vision_pipeline: VisionPipeline | None = None,
    publisher: Publisher | None = None,
    frame_source: FrameSource | None = None,
    clock: Callable[[], float] | None = None,
    vis_sender: VisSender | None = None,
    frame_pusher: FramePushSink | None = None,
) -> int:
    publisher_impl = publisher or create_publisher(
        no_output=config.no_output,
        output_spec=config.output,
    )
    source = frame_source or CameraSource.open(config.source)
    now_fn = clock or time.monotonic

    pattern_generator = PatternGenerator(config.pattern) if config.mode == "pattern" else None
    detect_pipeline = vision_pipeline if config.mode == "detect" else None
    if config.mode == "detect" and detect_pipeline is None:
        detector_impl = detector or YoloDetector(
            model_path=config.model_path,
            target_class=config.target_class,
            conf_threshold=config.conf_threshold,
            infer_width=config.infer_width,
            infer_size=config.infer_size,
        )
        tracker_impl = tracker or OpenCvTracker(config.tracker)
        # Tracker-only confidence decay knobs are env-driven so operators can
        # tune them without a code change. Defaults match VisionPipelineConfig
        # (0.95 decay rate, 0.5 floor) — the floor is intentionally aligned
        # with the FC's `trackingConfig_.minConfidence` (0.5) so a normal
        # tracker-only stretch never drops the FC out of TRACKING. Lowering
        # the floor below 0.5 changes in-flight behaviour and must be done
        # in lock-step with FC config — see VisionPipelineConfig docstring
        # and src/fc/header/FlightController.h:54.
        decay_rate = float(os.environ.get("VISION_TRACKER_ONLY_CONF_DECAY", "0.95"))
        decay_floor = float(os.environ.get("VISION_TRACKER_ONLY_CONF_FLOOR", "0.5"))
        detect_pipeline = VisionPipeline(
            detector=detector_impl,
            tracker=tracker_impl,
            config=VisionPipelineConfig(
                detect_hold_n=config.detect_hold_n,
                search_n=config.search_n,
                detect_every_n=config.detect_every_n,
                desired_cx=config.desired_cx,
                desired_cy=config.desired_cy,
                tracker_only_conf_decay=decay_rate,
                tracker_only_conf_floor=decay_floor,
            ),
        )

    vis_sender_impl: VisSender | None
    if config.no_vis_udp:
        vis_sender_impl = None
    else:
        vis_sender_impl = vis_sender or VisUdpSender(
            host=config.vis_udp_host,
            port=config.vis_udp_port,
        )

    frame_pusher_impl: FramePushSink | None
    if config.no_frame_push:
        frame_pusher_impl = None
    else:
        frame_pusher_impl = frame_pusher or FramePusher(
            backend_http=config.backend_http,
            frame_endpoint=config.backend_frame_endpoint,
            max_jpeg_bytes=config.backend_video_max_jpeg_bytes,
            api_token=os.environ.get("BACKEND_API_TOKEN") or None,
        )

    cv2_module: Any | None = None
    draw_overlay = None
    if config.preview:
        _ensure_preview_available()
        import cv2 as cv2_module_local

        from .overlay import draw_overlay as draw_overlay_local

        cv2_module = cv2_module_local
        draw_overlay = draw_overlay_local

    vis_interval_s = (1.0 / config.vis_hz) if config.vis_hz > 0 else 0.0
    frame_interval_s = (1.0 / config.frame_fps) if config.frame_fps > 0 else 0.0
    next_vis_emit_ts = 0.0
    next_frame_push_ts = 0.0
    frame_id = 0
    emitted_count = 0
    vis_seq = 1

    # Black-frame detection state: warn after N consecutive frames with
    # variance below the threshold. Reset on the first non-low-var frame.
    # The variance threshold (1.0) is well below any natural-image variance
    # but above pure-zero or single-uniform-colour frames, which is the
    # signature of a webcam producing all-black or stuck frames.
    _BLACK_FRAME_VAR_THRESHOLD = 1.0
    _BLACK_FRAME_WARN_AFTER = 30
    consecutive_black_frames = 0
    black_frame_warn_emitted = False

    try:
        while True:
            ok, frame = source.read()
            if not ok or frame is None:
                # Live sources (webcam/RTSP): trust the source's reconnect
                # logic — a False here is either a transient failure that's
                # already in the backoff window or a post-open black-frame
                # grace tick. Sleep briefly to avoid tight-looping during
                # the grace window (during reconnect, the source itself
                # sleeps).
                # Recorded sources (file): EOF is terminal; the loop ends.
                # Use getattr so test fakes that don't expose `is_live`
                # default to terminal-on-False semantics, preserving the
                # legacy break behaviour.
                if getattr(source, "is_live", False):
                    time.sleep(0.01)
                    continue
                break

            frame_id += 1
            img_h, img_w = frame.shape[:2]

            # Black-frame detection: a stuck or all-black source produces
            # near-zero variance frames. Warn once per low-var stretch (not
            # per frame) so the log doesn't drown in repeats.
            try:
                frame_variance = float(frame.var())
            except Exception:  # pragma: no cover — defensive against odd dtypes
                frame_variance = float("nan")
            if frame_variance == frame_variance and frame_variance < _BLACK_FRAME_VAR_THRESHOLD:
                consecutive_black_frames += 1
                if (
                    consecutive_black_frames >= _BLACK_FRAME_WARN_AFTER
                    and not black_frame_warn_emitted
                ):
                    print(
                        f"vision: {source.src_label} produced "
                        f"{consecutive_black_frames} consecutive low-variance frames "
                        f"(var<{_BLACK_FRAME_VAR_THRESHOLD}); camera may be stuck or covered",
                        file=sys.stderr,
                    )
                    black_frame_warn_emitted = True
            else:
                consecutive_black_frames = 0
                black_frame_warn_emitted = False
            if detect_pipeline is not None:
                tracker_result = detect_pipeline.process_frame(frame=frame, frame_id=frame_id)
            elif pattern_generator is not None:
                tracker_result = pattern_generator.update(frame_id=frame_id)
            else:
                raise RuntimeError("invalid vision mode configuration")

            now_s = now_fn()
            if frame_pusher_impl is not None and (
                frame_interval_s == 0.0 or now_s >= next_frame_push_ts
            ):
                frame_pusher_impl.push_frame(frame=frame, quality=config.jpeg_quality)
                if frame_interval_s > 0.0:
                    next_frame_push_ts = now_s + frame_interval_s

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

            if vis_interval_s == 0.0 or now_s >= next_vis_emit_ts:
                publisher_impl.publish(message)
                emitted_count += 1

                if vis_sender_impl is not None:
                    vis_payload = _build_vis_payload(
                        seq=vis_seq,
                        timestamp_s=now_s,
                        state=tracker_result.state,
                        bbox=tracker_result.bbox,
                        confidence=tracker_result.conf,
                    )
                    vis_sender_impl.send(vis_payload)
                    vis_seq += 1

                if vis_interval_s > 0.0:
                    next_vis_emit_ts = now_s + vis_interval_s

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
        _emit_shutdown_summary(vis_sender=vis_sender_impl, frame_pusher=frame_pusher_impl)
        source.release()
        publisher_impl.close()
        if frame_pusher_impl is not None:
            frame_pusher_impl.close()
        if vis_sender_impl is not None:
            vis_sender_impl.close()
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


def _build_vis_payload(
    seq: int,
    timestamp_s: float,
    state: VisState,
    bbox: Any,
    confidence: float,
) -> dict[str, Any]:
    tracking_state = _TRACKING_STATE_TO_CODE[state]
    if tracking_state != _TRACKING_STATE_TRACKING:
        return {
            "type": "VIS",
            "seq": int(seq),
            "timestamp_s": float(timestamp_s),
            "tracking_state": tracking_state,
            "loc_x": 0.0,
            "loc_y": 0.0,
            "bound_w": 0.0,
            "bound_h": 0.0,
            "confidence": 0.0,
        }

    loc_x = max(-1.0, min(1.0, ((float(bbox.cx) - 0.5) * 2.0)))
    loc_y = max(-1.0, min(1.0, ((0.5 - float(bbox.cy)) * 2.0)))
    return {
        "type": "VIS",
        "seq": int(seq),
        "timestamp_s": float(timestamp_s),
        "tracking_state": tracking_state,
        "loc_x": loc_x,
        "loc_y": loc_y,
        "bound_w": clamp01(bbox.w),
        "bound_h": clamp01(bbox.h),
        "confidence": clamp01(confidence),
    }


def _read_env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    value_stripped = value.strip()
    return value_stripped if value_stripped else default


def _read_env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return int(default)
    value_stripped = value.strip()
    if not value_stripped:
        return int(default)
    return int(value_stripped)


def _read_env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return float(default)
    value_stripped = value.strip()
    if not value_stripped:
        return float(default)
    return float(value_stripped)


def _emit_shutdown_summary(
    vis_sender: VisSender | None, frame_pusher: FramePushSink | None
) -> None:
    if vis_sender is None and frame_pusher is None:
        return

    vis_ok = _counter_or_zero(vis_sender, "sent_ok")
    vis_fail = _counter_or_zero(vis_sender, "sent_fail")
    vis_drop = _counter_or_zero(vis_sender, "dropped_oversize")
    frame_ok = _counter_or_zero(frame_pusher, "push_ok")
    frame_fail = _counter_or_zero(frame_pusher, "push_fail")
    frame_drop = _counter_or_zero(frame_pusher, "dropped_oversize")
    print(
        "vision summary: "
        f"vis_udp_ok={vis_ok} vis_udp_fail={vis_fail} vis_udp_drop_oversize={vis_drop} "
        f"frame_push_ok={frame_ok} frame_push_fail={frame_fail} "
        f"frame_drop_oversize={frame_drop}",
        file=sys.stderr,
    )


def _counter_or_zero(instance: object | None, attr: str) -> int:
    if instance is None:
        return 0
    value = getattr(instance, attr, 0)
    try:
        return int(value)
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
