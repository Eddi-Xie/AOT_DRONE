from __future__ import annotations

from typing import Any

from src.backend.broadcast import BackendBroadcaster
from src.backend.state import SharedState
from tests.ws_test_utils import make_valid_tel


class _CaptureWsManager:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def broadcast(
        self,
        event: str,
        data: dict[str, Any],
        timestamp_s: float | None = None,
    ) -> dict[str, Any]:
        payload = {
            "event": event,
            "data": data,
            "timestamp_s": timestamp_s,
        }
        self.events.append(payload)
        return payload


def test_tel_update_throttle_caps_emission_rate() -> None:
    state = SharedState()
    ws_manager = _CaptureWsManager()
    broadcaster = BackendBroadcaster(
        state=state,
        ws_manager=ws_manager,  # type: ignore[arg-type]
        tel_hz=5.0,
        vis_hz=20.0,
        link_hz=2.0,
    )

    base_s = 100.0
    for idx in range(20):
        now_s = base_s + (0.01 * idx)
        msg = make_valid_tel(seq=idx + 1)
        state.record_tel_ok(msg, rx_monotonic_s=now_s)
        broadcaster.on_tel_update(msg, rx_monotonic_s=now_s)

    tel_events = [event for event in ws_manager.events if event["event"] == "TEL_UPDATE"]
    assert len(tel_events) == 1

    late_s = base_s + 0.25
    msg = make_valid_tel(seq=999)
    state.record_tel_ok(msg, rx_monotonic_s=late_s)
    broadcaster.on_tel_update(msg, rx_monotonic_s=late_s)

    tel_events = [event for event in ws_manager.events if event["event"] == "TEL_UPDATE"]
    assert len(tel_events) == 2


def test_oversize_warning_includes_drop_counter() -> None:
    state = SharedState()
    ws_manager = _CaptureWsManager()
    broadcaster = BackendBroadcaster(
        state=state,
        ws_manager=ws_manager,  # type: ignore[arg-type]
        tel_hz=5.0,
        vis_hz=20.0,
        link_hz=2.0,
        warning_min_interval_s=0.0,
    )

    state.record_vis_drop("oversize")
    broadcaster.on_vis_drop("oversize", "datagram length=700 exceeds max_bytes=512")
    warning_events = [event for event in ws_manager.events if event["event"] == "WARNING"]
    assert len(warning_events) == 1
    assert "count=1" in warning_events[0]["data"]["detail"]

    state.record_vis_drop("oversize")
    broadcaster.on_vis_drop("oversize", "datagram length=700 exceeds max_bytes=512")
    warning_events = [event for event in ws_manager.events if event["event"] == "WARNING"]
    assert len(warning_events) == 2
    assert "count=2" in warning_events[1]["data"]["detail"]
