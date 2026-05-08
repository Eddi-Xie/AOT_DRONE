from src.backend.cmd_bridge import CmdBridge
from src.backend.state import SharedState
from tests.cmd_test_utils import FramedCmdCaptureServer, require_tcp_bind_or_skip, wait_until
from tests.seq_test_utils import assert_seqs_strictly_advance


def test_cmd_bridge_sends_incrementing_cmd_messages() -> None:
    require_tcp_bind_or_skip()

    server = FramedCmdCaptureServer()
    server.start()
    assert server.bound_port is not None

    state = SharedState()
    state.update_intent({"desired_mode": 0, "arm": True})
    bridge = CmdBridge(
        state=state,
        fc_host="127.0.0.1",
        fc_port=server.bound_port,
        tick_hz=20.0,
        connect_backoff_initial_s=0.05,
        connect_backoff_max_s=0.1,
    )
    bridge.start()
    try:
        assert wait_until(lambda: len(server.get_messages()) >= 3, timeout_s=1.2)
    finally:
        bridge.stop()
        server.stop()

    messages = server.get_messages()
    assert len(messages) >= 3
    for msg in messages:
        assert msg["type"] == "CMD"

    seqs = [int(msg["seq"]) for msg in messages]
    # Wrap-aware monotonicity check: strict `>` would falsely fail at the
    # CMD_SEQ_MAX -> 0 boundary even though that's a valid forward step.
    assert_seqs_strictly_advance(seqs)

    cmd_status = state.get_cmd_bridge_status()
    assert cmd_status["cmd_tx_ok"] >= 3
    assert cmd_status["fc_connected"] in (True, False)
