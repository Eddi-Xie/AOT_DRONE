"""Subprocess-level coverage for the S0.9 hoverThrottle uncalibrated gate.

`apply_control_mode_safely` in `src/fc/implementation/main.cpp` refuses
Tracking and Takeoff transitions when the operator has not calibrated
hover throttle (i.e. `hoverThrottle_` is still the default 0 sentinel).
The gate sits at the CMD-apply boundary alongside the existing S0.8
`!rc_sink->ok()` and `rc_sink->tuning_mismatch()` checks.

This test starts a real fc_app with no operator calibration applied,
sends a framed CMD asking for Tracking, and asserts the gate-refusal
log appears on stderr. The matching positive-control case
(`FC_HOVER_THROTTLE=N` → accepted, Tracking transition allowed) is
covered by the companion env-var test
`test_fc_hover_throttle_env.py::test_fc_hover_throttle_calibrated_unblocks_tracking_gate`.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import struct
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FC_APP = REPO_ROOT / "build" / "src" / "fc" / "fc_app"

# fc_app's TCP_CMD_PORT is hardcoded in proto::ProtocolConstants.h. The
# bind host can be overridden via FC_BIND_HOST but the port can't, so
# tests serialise on the same port (pytest's default sequential runner
# is fine).
FC_CMD_PORT = 9002
DESIRED_MODE_TRACKING = 1


def _have_fc_app() -> bool:
    return FC_APP.exists() and os.access(FC_APP, os.X_OK)


pytestmark = pytest.mark.skipif(
    not _have_fc_app(),
    reason="fc_app binary missing; run `cmake --build build -j` first",
)


def _build_cmd_frame(seq: int, desired_mode: int) -> bytes:
    """Build a length-prefixed CMD frame matching scripts/dev/send_cmd.py."""
    payload = {
        "type": "CMD",
        "seq": seq,
        "timestamp_s": time.monotonic(),
        "desired_mode": desired_mode,
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def _start_fc_app(env_overrides: dict) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(env_overrides)
    env.setdefault("FC_BIND_HOST", "127.0.0.1")
    return subprocess.Popen(
        [str(FC_APP)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )


def _wait_for_cmd_listener(timeout_s: float = 1.5) -> None:
    """Block until fc_app's TCP CMD listener is accepting, then return."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", FC_CMD_PORT), timeout=0.1):
                return
        except (ConnectionRefusedError, TimeoutError, OSError):
            time.sleep(0.02)
    raise AssertionError(f"fc_app TCP CMD listener did not bind within {timeout_s}s")


def _terminate_and_capture(proc: subprocess.Popen) -> tuple[int, str, str]:
    proc.send_signal(signal.SIGTERM)
    try:
        out, err = proc.communicate(timeout=1.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    return proc.returncode, out, err


# Time to wait after sending a CMD frame before SIGTERM. fc_app's main
# loop ticks at 50 Hz (~20ms), so 800ms covers ~40 ticks — comfortable
# even on a contended GitHub Actions runner. Previously this was 200ms,
# which review #7 flagged as flake-risk under noisy CI.
_CMD_APPLY_WAIT_S = 0.8


def test_uncalibrated_hover_refuses_tracking_mode() -> None:
    # FC_HOVER_THROTTLE unset → default 0 → gate must refuse Tracking.
    proc = _start_fc_app({})
    try:
        _wait_for_cmd_listener()
        frame = _build_cmd_frame(seq=1, desired_mode=DESIRED_MODE_TRACKING)
        with socket.create_connection(("127.0.0.1", FC_CMD_PORT), timeout=0.5) as sock:
            sock.sendall(frame)
        # Give fc_app enough ticks to apply the CMD and log the refusal.
        # See _CMD_APPLY_WAIT_S comment for the timing rationale.
        time.sleep(_CMD_APPLY_WAIT_S)
    finally:
        rc, _out, err = _terminate_and_capture(proc)

    assert rc in (-signal.SIGTERM, 128 + signal.SIGTERM, 0), f"unexpected rc={rc}; stderr={err!r}"
    assert "refused mode transition" in err, f"gate refusal log missing; stderr={err!r}"
    assert "hoverThrottle uncalibrated" in err, f"gate reason missing from stderr; got: {err!r}"
    # FC must not have entered Tracking — confirms the gate actually
    # prevented the setControlMode call.
    assert "control_mode=1" not in err, f"Tracking mode logged despite refusal: {err!r}"
