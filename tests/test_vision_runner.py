import numpy as np
import pytest

from src.vision.main import VisionConfig, run_loop
from src.vision.types import VisMessage


class _FakeSource:
    def __init__(self, num_frames: int, width: int = 320, height: int = 240) -> None:
        self.src_label = "file:fake.mp4"
        self._remaining = num_frames
        self._frame = np.zeros((height, width, 3), dtype=np.uint8)

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._remaining <= 0:
            return False, None
        self._remaining -= 1
        return True, self._frame

    def release(self) -> None:
        return None


class _CollectPublisher:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def publish(self, message: VisMessage) -> None:
        self.messages.append(message.to_dict())

    def close(self) -> None:
        return None


class _StepClock:
    def __init__(self, start_s: float, step_s: float) -> None:
        self._value = start_s
        self._step = step_s

    def __call__(self) -> float:
        current = self._value
        self._value += self._step
        return current


def test_vis_hz_monotonic_throttle_emits_expected_count() -> None:
    publisher = _CollectPublisher()
    emitted = run_loop(
        config=VisionConfig(
            source="webcam:0",
            vis_hz=2.0,
            max_frames=10,
            no_output=True,
            no_frame_push=True,
            no_vis_udp=True,
        ),
        publisher=publisher,
        frame_source=_FakeSource(num_frames=10),
        clock=_StepClock(start_s=0.0, step_s=0.1),
    )
    assert emitted == 2
    assert len(publisher.messages) == 2
    assert [item["frame_id"] for item in publisher.messages] == [1, 6]


def test_preview_guard_raises_on_headless_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.vision.main.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with pytest.raises(RuntimeError, match="no GUI display"):
        run_loop(
            config=VisionConfig(
                source="webcam:0",
                preview=True,
                max_frames=1,
                no_output=True,
                no_frame_push=True,
                no_vis_udp=True,
            ),
            publisher=_CollectPublisher(),
            frame_source=_FakeSource(num_frames=1),
            clock=_StepClock(start_s=0.0, step_s=0.1),
        )
