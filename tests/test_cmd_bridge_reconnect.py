import socket

from src.backend.cmd_bridge import CmdBridge
from src.backend.state import SharedState
from tests.cmd_test_utils import FramedCmdCaptureServer, require_tcp_bind_or_skip, wait_until


def test_cmd_bridge_reconnects_after_server_appears() -> None:
    require_tcp_bind_or_skip()

    target_port = _reserve_unused_port()
    state = SharedState()
    state.update_intent({"desired_mode": 0})
    bridge = CmdBridge(
        state=state,
        fc_host="127.0.0.1",
        fc_port=target_port,
        tick_hz=20.0,
        connect_backoff_initial_s=0.1,
        connect_backoff_max_s=0.2,
    )
    bridge.start()

    server: FramedCmdCaptureServer | None = None
    try:
        assert wait_until(
            lambda: state.get_cmd_bridge_status()["fc_last_connect_attempt_s"] is not None,
            timeout_s=0.6,
        )
        assert state.get_cmd_bridge_status()["fc_connected"] is False

        server = FramedCmdCaptureServer(port=target_port)
        server.start()

        assert wait_until(lambda: len(server.get_messages()) >= 2, timeout_s=1.2)
        status = state.get_cmd_bridge_status()
        assert status["cmd_tx_ok"] >= 2
    finally:
        bridge.stop()
        if server is not None:
            server.stop()


def _reserve_unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
