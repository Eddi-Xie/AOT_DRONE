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
            mode="pattern",
            pattern="sweep",
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


def test_black_frame_warning_after_30_consecutive_low_var_frames(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A stuck/covered camera produces near-zero-variance frames. After 30
    consecutive such frames we emit one stderr warning per low-var stretch
    so logs don't drown in repeats."""
    publisher = _CollectPublisher()
    # _FakeSource emits np.zeros frames (variance=0) — exactly the "stuck
    # camera" signature.
    run_loop(
        config=VisionConfig(
            source="webcam:0",
            mode="pattern",
            pattern="sweep",
            vis_hz=0.0,  # publish every frame so the loop runs all 35 iterations
            max_frames=35,
            no_output=True,
            no_frame_push=True,
            no_vis_udp=True,
        ),
        publisher=publisher,
        frame_source=_FakeSource(num_frames=35),
        clock=_StepClock(start_s=0.0, step_s=0.04),
    )
    captured = capsys.readouterr()
    # Warning message references the threshold + source label + frame_id
    # (for log correlation) and fires exactly once across the 35-frame run
    # (warn_emitted flag prevents repeats).
    assert "consecutive low-variance frames" in captured.err
    assert "file:fake.mp4" in captured.err
    assert "frame_id=" in captured.err
    assert captured.err.count("consecutive low-variance frames") == 1


def test_no_black_frame_warning_for_short_low_var_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """29 consecutive low-var frames is below the threshold — must not warn."""
    publisher = _CollectPublisher()
    run_loop(
        config=VisionConfig(
            source="webcam:0",
            mode="pattern",
            pattern="sweep",
            vis_hz=0.0,
            max_frames=29,
            no_output=True,
            no_frame_push=True,
            no_vis_udp=True,
        ),
        publisher=publisher,
        frame_source=_FakeSource(num_frames=29),
        clock=_StepClock(start_s=0.0, step_s=0.04),
    )
    captured = capsys.readouterr()
    assert "consecutive low-variance frames" not in captured.err


class _LiveFakeSource:
    """Fake source that exposes `is_live=True`. read() yields a scripted
    sequence: each entry is either a real frame (success) or None (False).

    Mirrors the cv2 contract: `(False, None)` means "transient failure";
    main.py's loop should `continue` on these (with a 10ms sleep) rather
    than break, since `is_live=True` says the source is recoverable.
    """

    def __init__(
        self, sequence: list[np.ndarray | None], width: int = 320, height: int = 240
    ) -> None:
        self.src_label = "webcam:0"
        self.is_live = True
        self._sequence = list(sequence)
        self._real_frame = np.zeros((height, width, 3), dtype=np.uint8)

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self._sequence:
            return False, None
        item = self._sequence.pop(0)
        if item is None:
            return False, None
        return True, item

    def release(self) -> None:
        return None


def test_live_source_false_triggers_continue_not_break(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live source returning (False, None) must not terminate the run
    loop — main.py is expected to sleep briefly and continue, trusting the
    source's own reconnect logic. The legacy break-on-False behaviour
    only applies to non-live (file) sources.

    Pre-fix: main.py broke on the first False unconditionally and the
    vision process exited on a single momentary cv2 blip."""
    real_frame = np.zeros((240, 320, 3), dtype=np.uint8)
    sequence: list[np.ndarray | None] = [None, real_frame, real_frame, real_frame]
    source = _LiveFakeSource(sequence=sequence)
    publisher = _CollectPublisher()

    # Stub time.sleep so the 10ms sleep in main.py's continue path doesn't
    # actually delay the test.
    monkeypatch.setattr("src.vision.main.time.sleep", lambda _s: None)

    emitted = run_loop(
        config=VisionConfig(
            source="webcam:0",
            mode="pattern",
            pattern="sweep",
            vis_hz=0.0,  # publish every frame
            max_frames=3,  # cap so the loop terminates after 3 successes
            no_output=True,
            no_frame_push=True,
            no_vis_udp=True,
        ),
        publisher=publisher,
        frame_source=source,
        clock=_StepClock(start_s=0.0, step_s=0.04),
    )

    # 3 successful frames after the initial False (which was tolerated and
    # continue'd past). Pre-fix this would emit 0 (loop broke on the False).
    assert emitted == 3
    # Confirm the False was consumed (sequence is now empty).
    assert source._sequence == []


def test_preview_guard_raises_on_headless_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.vision.main.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with pytest.raises(RuntimeError, match="no GUI display"):
        run_loop(
            config=VisionConfig(
                source="webcam:0",
                mode="pattern",
                pattern="sweep",
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
