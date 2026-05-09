"""Subprocess-level coverage for fc_app's FC_RC_SINK env-var dispatch.

`make_rc_sink_from_env` in src/fc/implementation/main.cpp is the single
operator-facing config knob for the HIL bench. The error paths (unknown
sink kind, invalid port, msp-not-implemented) are easy to break in S0.8
when the msp branch lands; locking the contract via subprocess tests
catches a regression at the binary boundary, which is the right level
for env-driven dispatch.

Each test launches fc_app with a specific env, expects exit 1, and
asserts a substring of the stderr message identifying the failure
shape.
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
    # Bind to a non-default port so test runs don't collide with a real
    # backend listening on 9002.
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
        # The configured-correctly path hangs in the loop forever; kill
        # it and read whatever stderr it produced.
        proc.send_signal(signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
    return proc.returncode, out, err


def test_unknown_fc_rc_sink_refuses_to_start() -> None:
    rc, _out, err = _run_fc({"FC_RC_SINK": "bogus_sink_value"})
    assert rc == 1, f"expected exit 1, got {rc}; stderr={err!r}"
    assert "Unknown FC_RC_SINK='bogus_sink_value'" in err


def test_msp_sink_refuses_with_s08_pointer() -> None:
    rc, _out, err = _run_fc({"FC_RC_SINK": "msp"})
    assert rc == 1, f"expected exit 1, got {rc}; stderr={err!r}"
    assert "S0.8" in err and "USB MSP" in err


def test_invalid_fc_rc_fake_port_non_numeric() -> None:
    rc, _out, err = _run_fc({"FC_RC_SINK": "fake", "FC_RC_FAKE_PORT": "not-a-number"})
    assert rc == 1
    assert "FC_RC_FAKE_PORT" in err
    # Should reject the alpha string up-front, not crash later.
    assert "not-a-number" in err


def test_invalid_fc_rc_fake_port_zero() -> None:
    rc, _out, err = _run_fc({"FC_RC_SINK": "fake", "FC_RC_FAKE_PORT": "0"})
    assert rc == 1
    assert "FC_RC_FAKE_PORT" in err


def test_invalid_fc_rc_fake_port_too_high() -> None:
    rc, _out, err = _run_fc({"FC_RC_SINK": "fake", "FC_RC_FAKE_PORT": "70000"})
    assert rc == 1
    assert "FC_RC_FAKE_PORT" in err


def test_invalid_fc_rc_fake_host_hostname() -> None:
    # Hostnames are explicitly NOT resolved (matches FC_TEL_HOST policy).
    rc, _out, err = _run_fc({"FC_RC_SINK": "fake", "FC_RC_FAKE_HOST": "localhost"})
    assert rc == 1
    assert "invalid host" in err or "FakeBetaflightSink" in err


def test_recording_sink_refuses_when_log_dir_is_a_regular_file(tmp_path: Path) -> None:
    # Create a regular file at the path we hand FC_RC_LOG_DIR; expect
    # ensure_directory to detect it via the round-1 S_ISDIR check.
    blocking_file = tmp_path / "not_a_dir"
    blocking_file.write_text("blocking content")

    rc, _out, err = _run_fc(
        {
            "FC_RC_SINK": "recording",
            "FC_RC_LOG_DIR": str(blocking_file / "sub"),
        }
    )
    assert rc == 1, f"expected exit 1, got {rc}; stderr={err!r}"
    assert "exists but is not a directory" in err


def test_default_null_sink_runs_until_signaled(tmp_path: Path) -> None:
    # Sanity: with no env overrides, fc_app starts cleanly. We give it
    # 0.6 s, signal it, and verify it logged the NullSink banner. This
    # catches a regression where main.cpp accidentally errors on the
    # default code path.
    rc, out, err = _run_fc({}, timeout=0.6)
    # Killed by SIGTERM -> negative rc (POSIX) or 143 (some shells).
    assert rc in (
        -signal.SIGTERM,
        128 + signal.SIGTERM,
        0,
    ), f"unexpected rc={rc}; stdout={out!r} stderr={err!r}"
    combined = out + err
    assert "RC sink=null" in combined


def test_recording_sink_writes_csv_under_temp_dir(tmp_path: Path) -> None:
    # End-to-end: with FC_RC_LOG_DIR pointed at tmp_path, a short
    # fc_app run produces a sink_<UTC>.csv with a recognisable header
    # and at least a handful of channel rows.
    rc, _out, _err = _run_fc(
        {
            "FC_RC_SINK": "recording",
            "FC_RC_LOG_DIR": str(tmp_path),
        },
        timeout=0.6,
    )
    # SIGTERM kill is expected for the success path.
    assert rc in (-signal.SIGTERM, 128 + signal.SIGTERM, 0)

    csvs = list(tmp_path.glob("sink_*.csv"))
    assert len(csvs) == 1, f"expected exactly 1 CSV, found {csvs}"
    contents = csvs[0].read_text()
    lines = contents.strip().split("\n")
    assert lines[0] == "timestamp_s,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4"
    # 50 Hz over ~0.5 s of effective ticks -> at least ~10 rows.
    assert len(lines) >= 10, f"only {len(lines)} lines: {contents!r}"
