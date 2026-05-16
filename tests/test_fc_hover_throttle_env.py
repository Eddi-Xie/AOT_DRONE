"""Subprocess-level coverage for FC_HOVER_THROTTLE env-var parsing.

`FC_HOVER_THROTTLE` (S0.9) is the operator-facing calibration knob that
sets fc_app's `hoverThrottle_` at startup. Without it, the FC defaults
to 0 (uncalibrated) and `apply_control_mode_safely` refuses
Tracking/Takeoff transitions. With it set to a valid microsecond value
in [DRONE_MIN, 1800], the gate clears.

This file exercises the parser's strict-`from_chars` policy (mirrors
the FC_TEL_PORT / FC_RC_BAUD pattern) and the range validation:

  - Unset / empty → default 0 retained (covered by
    test_apply_control_mode_safely_hover_gate.py).
  - Numeric in-band → accepted; fc_app starts.
  - Non-numeric / trailing junk / out-of-range → exit 1 with
    "[FC] Invalid FC_HOVER_THROTTLE" or "out of range" on stderr.

The matching positive-control test for the gate (with calibration
applied → Tracking accepted) lives in the gate test file; this file
only exercises the env-var parser surface.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FC_APP = REPO_ROOT / "build" / "src" / "fc" / "fc_app"


def _have_fc_app() -> bool:
    return FC_APP.exists() and os.access(FC_APP, os.X_OK)


pytestmark = pytest.mark.skipif(
    not _have_fc_app(),
    reason="fc_app binary missing; run `cmake --build build -j` first",
)


def _run_fc(env_overrides: dict, *, timeout: float = 1.5) -> tuple[int, str, str]:
    """Run fc_app with the given env additions; return (rc, stdout, stderr)."""
    env = os.environ.copy()
    env.update(env_overrides)
    env.setdefault("FC_BIND_HOST", "127.0.0.1")
    proc = subprocess.Popen(
        [str(FC_APP)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.send_signal(signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
    return proc.returncode, out, err


def test_fc_hover_throttle_unset_starts_clean() -> None:
    # No env override → hoverThrottle_ stays at the uncalibrated default.
    # fc_app must boot fine; the gate only fires on Tracking/Takeoff CMDs.
    rc, _out, err = _run_fc({}, timeout=0.5)
    assert rc in (-signal.SIGTERM, 128 + signal.SIGTERM, 0), f"unexpected rc={rc}; stderr={err!r}"
    # No FC_HOVER_THROTTLE log line should appear when unset.
    assert (
        "FC_HOVER_THROTTLE" not in err
    ), f"unexpected FC_HOVER_THROTTLE log when env unset; stderr={err!r}"


def test_fc_hover_throttle_valid_value_accepted() -> None:
    # A realistic calibrated hover (1100 µs) should be accepted and
    # logged on stdout. The acceptance log is written from main on the
    # same stdout where the CommandServer worker thread also writes a
    # "Waiting for TCP command client on :9002" line at startup; the
    # two can interleave and split "FC_HOVER_THROTTLE=1100" mid-string.
    # Assert on the trailing "(operator-calibrated)" parenthetical which
    # is emitted as a single string-literal operator<< and survives the
    # race intact. Mirrors the de-flake of test_default_null_sink in
    # tests/test_fc_rc_sink_env.py.
    rc, out, err = _run_fc({"FC_HOVER_THROTTLE": "1100"}, timeout=0.5)
    assert rc in (-signal.SIGTERM, 128 + signal.SIGTERM, 0), f"unexpected rc={rc}; stderr={err!r}"
    combined = out + err
    assert (
        "(operator-calibrated)" in combined
    ), f"expected hover-throttle acceptance log; stdout={out!r} stderr={err!r}"


def test_fc_hover_throttle_zero_explicit_accepted() -> None:
    # FC_HOVER_THROTTLE=0 is explicit "uncalibrated" — must NOT be
    # treated as an error, but the gate is still active.
    # Same stdout-race caveat as the valid-value case above: assert on
    # the trailing "(operator-calibrated)" parenthetical, not the
    # interleavable "FC_HOVER_THROTTLE=" prefix.
    rc, out, err = _run_fc({"FC_HOVER_THROTTLE": "0"}, timeout=0.5)
    assert rc in (-signal.SIGTERM, 128 + signal.SIGTERM, 0), f"unexpected rc={rc}; stderr={err!r}"
    combined = out + err
    assert "(operator-calibrated)" in combined, (
        f"expected hover-throttle acceptance log for explicit 0; " f"stdout={out!r} stderr={err!r}"
    )


def test_fc_hover_throttle_non_numeric_refuses_to_start() -> None:
    rc, _out, err = _run_fc({"FC_HOVER_THROTTLE": "not-a-number"})
    assert rc == 1, f"expected exit 1, got {rc}; stderr={err!r}"
    assert "Invalid FC_HOVER_THROTTLE" in err
    assert "not-a-number" in err


def test_fc_hover_throttle_trailing_junk_refuses_to_start() -> None:
    # Trailing junk after a valid integer (e.g. "1100abc") would be
    # silently truncated by std::stoi. We use std::from_chars and assert
    # ptr == end, matching the FC_TEL_PORT / FC_RC_BAUD policy.
    rc, _out, err = _run_fc({"FC_HOVER_THROTTLE": "1100abc"})
    assert rc == 1, f"expected exit 1, got {rc}; stderr={err!r}"
    assert "Invalid FC_HOVER_THROTTLE" in err


def test_fc_hover_throttle_below_drone_min_refuses_to_start() -> None:
    # 500 µs is below DRONE_MIN. Allowed values are 0 (uncalibrated)
    # or [DRONE_MIN, 1800]. 500 falls in neither — refuse up-front
    # rather than silently clamping (which would mask operator typos
    # like missing leading 1).
    rc, _out, err = _run_fc({"FC_HOVER_THROTTLE": "500"})
    assert rc == 1, f"expected exit 1, got {rc}; stderr={err!r}"
    assert "FC_HOVER_THROTTLE=500" in err
    assert "out of range" in err


def test_fc_hover_throttle_negative_value_refuses_to_start() -> None:
    # Negative microseconds are nonsensical. from_chars parses the sign
    # then the range guard catches it via `< DRONE_MIN`.
    rc, _out, err = _run_fc({"FC_HOVER_THROTTLE": "-100"})
    assert rc == 1, f"expected exit 1, got {rc}; stderr={err!r}"
    assert "FC_HOVER_THROTTLE=-100" in err
    assert "out of range" in err


def test_fc_hover_throttle_above_ceiling_refuses_to_start() -> None:
    # 1900 µs is above kAltHoldMaxThrottle (1800). Same up-front refusal
    # as the below-MIN case — operator likely meant 1800 (the ceiling)
    # or a lower value; silent clamping would hide the typo.
    rc, _out, err = _run_fc({"FC_HOVER_THROTTLE": "1900"})
    assert rc == 1, f"expected exit 1, got {rc}; stderr={err!r}"
    assert "FC_HOVER_THROTTLE=1900" in err
    assert "out of range" in err


def test_fc_hover_throttle_calibrated_unblocks_tracking_gate() -> None:
    # End-to-end gate behaviour: with FC_HOVER_THROTTLE=1100 set, the
    # apply_control_mode_safely gate from S0.9 commit 1 must NOT refuse
    # a Tracking transition on account of uncalibrated hover. Companion
    # to test_apply_control_mode_safely_hover_gate.py's negative case.
    import json
    import socket
    import struct
    import time

    proc = subprocess.Popen(
        [str(FC_APP)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "FC_BIND_HOST": "127.0.0.1", "FC_HOVER_THROTTLE": "1100"},
        text=True,
    )
    try:
        # Wait for the CMD listener.
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", 9002), timeout=0.1):
                    break
            except (ConnectionRefusedError, TimeoutError, OSError):
                time.sleep(0.02)
        # Send a Tracking-mode CMD.
        payload = {
            "type": "CMD",
            "seq": 1,
            "timestamp_s": time.monotonic(),
            "desired_mode": 1,
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        frame = struct.pack(">I", len(body)) + body
        with socket.create_connection(("127.0.0.1", 9002), timeout=0.5) as sock:
            sock.sendall(frame)
        # 0.8s covers ~40 fc_app ticks; comfortable margin over noisy
        # CI runners that can stall a single tick by 100+ ms. See
        # review #7 in the S0.9 round-1 fixes for the rationale.
        time.sleep(0.8)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            _out, err = proc.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            _out, err = proc.communicate()

    # Calibrated path: no "hoverThrottle uncalibrated" refusal.
    assert (
        "hoverThrottle uncalibrated" not in err
    ), f"gate refused Tracking despite calibration; stderr={err!r}"
