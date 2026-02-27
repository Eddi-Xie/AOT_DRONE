from __future__ import annotations

import base64
import io
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - Pillow is optional fallback path
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]


_STATIC_JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIf"
    "IiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7"
    "Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAABAAEDASIAAhEBAxEB"
    "/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQR"
    "BRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpT"
    "VFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLD"
    "xMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAEC"
    "AwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMz"
    "UvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6"
    "goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn"
    "6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD2aiiigD//2Q=="
)


@dataclass(frozen=True)
class VideoConfig:
    enabled: bool
    fps: float
    max_jpeg_bytes: int
    frame_fresh_s: float


class VideoFrameHub:
    """Thread-safe storage for latest JPEG frame and stream counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._last_rx_monotonic_s: float | None = None
        self._frames_rx_ok = 0
        self._frames_rx_bad = 0
        self._video_clients = 0
        self._video_fps_est = 0.0
        self._last_served_monotonic_s: float | None = None
        self._frames_served_total = 0

    def set_jpeg(self, jpeg_bytes: bytes, now_monotonic_s: float | None = None) -> None:
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else time.monotonic()
        with self._lock:
            self._latest_jpeg = bytes(jpeg_bytes)
            self._last_rx_monotonic_s = now_s
            self._frames_rx_ok += 1

    def record_bad_frame(self) -> None:
        with self._lock:
            self._frames_rx_bad += 1

    def get_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def get_fresh_jpeg(
        self,
        max_age_s: float,
        now_monotonic_s: float | None = None,
    ) -> bytes | None:
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else time.monotonic()
        with self._lock:
            if self._latest_jpeg is None or self._last_rx_monotonic_s is None:
                return None
            age_s = max(0.0, now_s - self._last_rx_monotonic_s)
            if age_s > max_age_s:
                return None
            return self._latest_jpeg

    def get_age_s(self, now_monotonic_s: float | None = None) -> float | None:
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else time.monotonic()
        with self._lock:
            if self._last_rx_monotonic_s is None:
                return None
            return max(0.0, now_s - self._last_rx_monotonic_s)

    def register_client(self) -> None:
        with self._lock:
            self._video_clients += 1

    def unregister_client(self) -> None:
        with self._lock:
            self._video_clients = max(0, self._video_clients - 1)

    def record_frame_served(self, now_monotonic_s: float | None = None) -> None:
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else time.monotonic()
        with self._lock:
            prev_s = self._last_served_monotonic_s
            self._last_served_monotonic_s = now_s
            self._frames_served_total += 1
            if prev_s is None:
                return
            delta_s = now_s - prev_s
            if delta_s <= 0.0:
                return
            inst_fps = 1.0 / delta_s
            if self._video_fps_est <= 0.0:
                self._video_fps_est = inst_fps
            else:
                self._video_fps_est = (0.85 * self._video_fps_est) + (0.15 * inst_fps)

    def get_stats(self, now_monotonic_s: float | None = None) -> dict[str, Any]:
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else time.monotonic()
        with self._lock:
            age_s = None
            if self._last_rx_monotonic_s is not None:
                age_s = max(0.0, now_s - self._last_rx_monotonic_s)
            return {
                "video_clients": self._video_clients,
                "video_fps_est": self._video_fps_est,
                "last_frame_age_s": age_s,
                "frames_rx_ok": self._frames_rx_ok,
                "frames_rx_bad": self._frames_rx_bad,
            }


class SyntheticJpegGenerator:
    """Creates synthetic JPEG frames for local development and fallback streaming."""

    def __init__(self, width: int = 640, height: int = 360, quality: int = 75) -> None:
        self.width = width
        self.height = height
        self.quality = quality

    def render(self, now_monotonic_s: float, lines: list[str] | None = None) -> bytes:
        if Image is None or ImageDraw is None or ImageFont is None:
            return _STATIC_JPEG_BYTES

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        text_lines = [
            "AOT_DRONE",
            f"synthetic feed {timestamp}",
            f"mono {now_monotonic_s:.3f}",
        ]
        if lines:
            text_lines.extend(lines[:3])

        image = Image.new("RGB", (self.width, self.height), (15, 34, 52))
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()

        draw.rectangle((0, 0, self.width - 1, self.height - 1), outline=(110, 167, 209), width=3)
        draw.rectangle((10, 10, self.width - 10, self.height - 10), outline=(46, 93, 121), width=1)

        y = 28
        for line in text_lines:
            draw.text((24, y), line, fill=(227, 247, 255), font=font)
            y += 24

        output = io.BytesIO()
        image.save(output, format="JPEG", quality=self.quality, optimize=True)
        return output.getvalue()


def make_mjpeg_part(frame_bytes: bytes, boundary: str = "frame") -> bytes:
    header = (
        f"--{boundary}\r\n"
        "Content-Type: image/jpeg\r\n"
        f"Content-Length: {len(frame_bytes)}\r\n"
        "\r\n"
    ).encode("ascii")
    return header + frame_bytes + b"\r\n"


def can_decode_jpeg(frame_bytes: bytes) -> bool:
    """Best-effort decode check for optional strict validation."""
    if Image is None:
        return False

    try:
        with Image.open(io.BytesIO(frame_bytes)) as image:
            image.verify()
    except Exception:
        return False
    return True
