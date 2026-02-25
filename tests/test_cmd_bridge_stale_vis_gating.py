from src.backend.cmd_bridge import CmdBridge
from src.backend.state import SharedState
from tests.cmd_test_utils import FramedCmdCaptureServer, require_tcp_bind_or_skip, wait_until


def test_cmd_bridge_blocks_tracking_mode_when_no_vis() -> None:
    require_tcp_bind_or_skip()

    server = FramedCmdCaptureServer()
    server.start()
    assert server.bound_port is not None

    state = SharedState()
    state.update_intent({"desired_mode": 1})
    bridge = CmdBridge(
        state=state,
        fc_host="127.0.0.1",
        fc_port=server.bound_port,
        tick_hz=20.0,
        vis_fresh_s=0.25,
        connect_backoff_initial_s=0.05,
        connect_backoff_max_s=0.1,
    )
    bridge.start()
    try:
        assert wait_until(lambda: len(server.get_messages()) >= 1, timeout_s=1.0)
    finally:
        bridge.stop()
        server.stop()

    msg = server.get_messages()[0]
    assert msg["type"] == "CMD"
    assert msg["desired_mode"] == 2

    status = state.get_cmd_bridge_status()
    assert status["tracking_blocked_reason"] == "no_vis"
