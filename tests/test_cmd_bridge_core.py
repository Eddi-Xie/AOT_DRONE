import json
import struct

from src.backend.cmd_bridge import CmdBridge
from src.backend.state import CMD_SEQ_MAX, SharedState


class _FakeSocket:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def sendall(self, frame: bytes) -> None:
        self.frames.append(frame)


def _decode_frame(frame: bytes) -> dict:
    payload_len = struct.unpack(">I", frame[:4])[0]
    payload = frame[4 : 4 + payload_len]
    return json.loads(payload.decode("utf-8"))


def test_cmd_seq_is_monotonic_across_bridge_instances_within_process(monkeypatch) -> None:
    state = SharedState()
    state.update_intent({"desired_mode": 0})
    fake_sock = _FakeSocket()

    bridge_one = CmdBridge(state=state, tick_hz=20.0)
    bridge_two = CmdBridge(state=state, tick_hz=20.0)

    monkeypatch.setattr(bridge_one, "_get_socket", lambda: fake_sock)
    monkeypatch.setattr(bridge_two, "_get_socket", lambda: fake_sock)

    bridge_one._send_tick(now_s=10.0)
    bridge_one._send_tick(now_s=10.1)
    bridge_two._send_tick(now_s=10.2)

    seqs = [_decode_frame(frame)["seq"] for frame in fake_sock.frames]
    assert len(seqs) == 3
    assert seqs[1] == seqs[0] + 1
    assert seqs[2] == seqs[1] + 1


def test_last_cmd_payload_debug_snapshot_updates() -> None:
    state = SharedState()
    state.update_intent({"desired_mode": 1})
    vis = {
        "type": "VIS",
        "seq": 77,
        "timestamp_s": 5.5,
        "tracking_state": 3,
        "loc_x": 0.1,
        "loc_y": -0.1,
        "bound_w": 0.3,
        "bound_h": 0.4,
        "confidence": 0.9,
    }
    state.record_vis_ok(vis, rx_monotonic_s=10.0)

    fake_sock = _FakeSocket()
    bridge = CmdBridge(state=state, tick_hz=20.0, vis_fresh_s=0.25)
    bridge._get_socket = lambda: fake_sock  # type: ignore[method-assign]
    bridge._send_tick(now_s=10.1)

    status = state.get_cmd_bridge_status()
    assert status["last_cmd_seq"] is not None
    assert status["last_cmd_desired_mode"] == 1
    assert status["last_cmd_had_tracking"] is True
    assert isinstance(status["last_cmd_bytes"], int)
    assert status["last_cmd_bytes"] > 0


def test_reserve_cmd_seq_wraps_after_int32_maximum() -> None:
    state = SharedState()
    state.cmd_next_seq = CMD_SEQ_MAX - 1

    assert state.reserve_cmd_seq() == CMD_SEQ_MAX - 1
    assert state.reserve_cmd_seq() == CMD_SEQ_MAX
    assert state.reserve_cmd_seq() == 0


def test_ensure_cmd_seq_minimum_handles_post_wrap_state() -> None:
    """After cmd_next_seq has wrapped to a low value, a stale-high `minimum`
    from a CmdBridge restart (e.g. seq_start = CMD_SEQ_MAX - 10) MUST NOT
    silently leave cmd_next_seq pointed at the post-wrap range, because that
    would re-issue old sequence numbers the FC has already seen.

    Pre-fix behaviour (linear `<`): `cmd_next_seq=5 < clamped_minimum=high`
    is True, so cmd_next_seq jumps backward to the high value — but that
    high value is *behind* the FC's last-seen-seq in wrap-space, so the FC
    rejects the next 2^31 - 10 frames silently.

    Post-fix behaviour (signed-modular): `_seq_advances(5, high)` returns
    False because `high` is "behind" the post-wrap `5` in the half-space
    sense, so cmd_next_seq stays at 5 and continues from there. Operator
    intervention (e.g. an explicit reseed) is required to escape, which is
    the correct posture — we don't silently re-issue.
    """
    state = SharedState()
    # Simulate a state that has just wrapped past CMD_SEQ_MAX.
    state.cmd_next_seq = 5

    # A bridge restart asks for a minimum that's "behind" in wrap-space.
    state.ensure_cmd_seq_minimum(CMD_SEQ_MAX - 10)
    assert state.cmd_next_seq == 5  # unchanged: ahead in wrap-space wins

    # By contrast, a minimum that's genuinely "ahead" in wrap-space is honoured.
    state.ensure_cmd_seq_minimum(50)
    assert state.cmd_next_seq == 50


def test_reserve_cmd_seq_clamps_negative_minimum_to_zero() -> None:
    """Negative minimum must not pull the sequence backwards or raise."""
    state = SharedState()
    state.cmd_next_seq = 100

    # Negative minimum clamps to 0; cmd_next_seq is already > 0, so no bump.
    assert state.reserve_cmd_seq(minimum=-1) == 100
    assert state.reserve_cmd_seq(minimum=-1_000_000) == 101

    # If cmd_next_seq sits low and minimum is negative, we still don't go
    # backwards — we just advance normally from current.
    state.cmd_next_seq = 5
    assert state.reserve_cmd_seq(minimum=-50) == 5
    assert state.cmd_next_seq == 6


def test_reserve_cmd_seq_clamps_over_max_minimum_does_not_jump_backward() -> None:
    """Over-max minimum must clamp to CMD_SEQ_MAX, but cmd_next_seq must NOT
    jump to it from a low post-wrap state.

    Pre-S0.4 behaviour (linear `<`) jumped cmd_next_seq from 0 to CMD_SEQ_MAX,
    which against a wrap-aware FC would re-issue sequences the FC has already
    seen as "behind" and dropped. Post-S0.4 the comparison is signed-modular:
    CMD_SEQ_MAX is "behind" 0 in the wrap space (forward distance == 2^31 - 1
    > half-space), so the bump is rejected and cmd_next_seq advances normally
    from its current value.

    The legitimate use case (a bridge restart with `seq_start=high` after a
    fresh process start) is a residual: if the operator needs to force a
    high resume seq, they assign `state.cmd_next_seq` directly. No silent
    foot-gun via the public API.
    """
    state = SharedState()
    state.cmd_next_seq = 0

    seq = state.reserve_cmd_seq(minimum=CMD_SEQ_MAX + 1000)
    # No jump — clamping happens, but the modular comparator says "behind".
    assert seq == 0
    # Subsequent reserves continue from 1 onward, unchanged by the rejected bump.
    assert state.reserve_cmd_seq() == 1


def test_ensure_cmd_seq_minimum_clamps_out_of_range_inputs() -> None:
    """Out-of-range minimums must not crash or move cmd_next_seq backwards.

    The over-max case under signed-modular semantics is "behind" any low
    cmd_next_seq, so it is rejected (mirrors the rationale in
    test_ensure_cmd_seq_minimum_handles_post_wrap_state above). Negative
    minimum still clamps to 0 and is then trivially "behind" any positive
    cmd_next_seq, so no bump.
    """
    state = SharedState()
    state.cmd_next_seq = 50

    # Negative minimum clamps to 0; cmd_next_seq stays at 50 (>0, ahead in mod-space).
    state.ensure_cmd_seq_minimum(-100)
    assert state.cmd_next_seq == 50

    # Over-max minimum clamps to CMD_SEQ_MAX, but signed-modular says "behind"
    # (forward distance is 2^31 - 51 > half-space). Reject the bump.
    state.ensure_cmd_seq_minimum(CMD_SEQ_MAX + 1000)
    assert state.cmd_next_seq == 50

    # Reserve continues from 50 onward.
    assert state.reserve_cmd_seq() == 50
    assert state.reserve_cmd_seq() == 51
