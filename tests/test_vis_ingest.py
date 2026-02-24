import json
import socket
import threading
import time

import pytest

from src.backend.state import SharedState
from src.backend.vis_ingest import VisUdpIngestor


def _valid_vis(seq: int = 1) -> dict[str, float | int | str]:
    return {
        "type": "VIS",
        "seq": seq,
        "timestamp_s": 10.0 + seq,
        "tracking_state": 3,
        "loc_x": 0.2,
        "loc_y": -0.1,
        "bound_w": 0.3,
        "bound_h": 0.4,
        "confidence": 0.8,
    }


def _wait_until(predicate, timeout_s: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_vis_ingest_process_datagram_counts_reasons() -> None:
    state = SharedState()
    ingestor = VisUdpIngestor(state=state, max_bytes=512, log_interval_s=0.0)
    valid = json.dumps(_valid_vis(), separators=(",", ":")).encode("utf-8")
    assert ingestor.process_datagram(valid)
    assert not ingestor.process_datagram(b"{not-json")
    assert not ingestor.process_datagram(b"x" * 513)

    stats = state.get_vis_stats()
    assert stats["vis_rx_total"] == 3
    assert stats["vis_rx_ok"] == 1
    assert stats["vis_rx_bad"] == 2
    assert stats["vis_drop_reason_json"] == 1
    assert stats["vis_drop_reason_oversize"] == 1
    assert stats["vis_last_seq"] == 1
    assert stats["vis_last_timestamp_s"] == 11.0


def test_vis_udp_ingest_loop_accepts_datagram_when_udp_allowed() -> None:
    _require_udp_bind_or_skip()

    state = SharedState()
    ingestor = VisUdpIngestor(
        state=state,
        bind_host="127.0.0.1",
        port=0,
        max_bytes=512,
        recv_timeout_s=0.02,
        log_interval_s=0.0,
    )
    stop_event = threading.Event()
    thread = threading.Thread(target=ingestor.serve_forever, args=(stop_event,), daemon=True)
    thread.start()

    assert _wait_until(lambda: ingestor.bound_port is not None)
    assert ingestor.bound_port is not None
    target = ("127.0.0.1", ingestor.bound_port)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            valid = json.dumps(_valid_vis(seq=2), separators=(",", ":")).encode("utf-8")
            sock.sendto(valid, target)
            assert _wait_until(lambda: state.get_vis_stats()["vis_rx_ok"] == 1)
    finally:
        stop_event.set()
        ingestor.close()
        thread.join(timeout=1.0)

    assert not thread.is_alive()
    latest = state.get_latest_vis()
    assert latest is not None
    assert latest["seq"] == 2


def test_vis_status_connected_threshold() -> None:
    state = SharedState()
    status_initial = state.get_vis_status(connected_threshold_s=1.0, now_monotonic_s=10.0)
    assert status_initial["vis_connected"] is False
    assert status_initial["vis_age_s"] is None
    assert status_initial["vis_rx_ok"] == 0
    assert status_initial["vis_rx_bad"] == 0

    state.record_vis_ok(_valid_vis(seq=5), rx_monotonic_s=20.0)
    status_connected = state.get_vis_status(connected_threshold_s=1.0, now_monotonic_s=20.5)
    assert status_connected["vis_connected"] is True
    assert status_connected["vis_age_s"] == pytest.approx(0.5)
    assert status_connected["vis_last_seq"] == 5

    status_stale = state.get_vis_status(connected_threshold_s=1.0, now_monotonic_s=21.5)
    assert status_stale["vis_connected"] is False
    assert status_stale["vis_age_s"] == pytest.approx(1.5)


def _require_udp_bind_or_skip() -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"UDP socket bind unavailable in test environment: {exc}")
