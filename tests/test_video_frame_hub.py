from __future__ import annotations

import pytest

from src.backend.video_hub import VideoFrameHub


def test_video_frame_hub_set_get_and_age() -> None:
    hub = VideoFrameHub()
    frame = b"\xff\xd8abc\xff\xd9"

    assert hub.get_jpeg() is None
    assert hub.get_age_s(now_monotonic_s=10.0) is None

    hub.set_jpeg(frame, now_monotonic_s=10.0)
    assert hub.get_jpeg() == frame
    assert hub.get_age_s(now_monotonic_s=11.5) == 1.5


def test_video_frame_hub_counters_and_clients() -> None:
    hub = VideoFrameHub()

    hub.record_bad_frame()
    hub.set_jpeg(b"\xff\xd8ok\xff\xd9", now_monotonic_s=3.0)

    hub.register_client()
    hub.register_client()
    hub.record_frame_served(now_monotonic_s=4.0)
    hub.record_frame_served(now_monotonic_s=4.1)
    hub.unregister_client()

    stats = hub.get_stats(now_monotonic_s=4.2)
    assert stats["frames_rx_ok"] == 1
    assert stats["frames_rx_bad"] == 1
    assert stats["video_clients"] == 1
    assert stats["last_frame_age_s"] == pytest.approx(1.2)
    assert stats["video_fps_est"] > 0.0
