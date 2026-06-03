"""Tests for scripts/dev/hover_calibration.py.

The script's wire contract is HTTP POST to backend `/api/intent`. The
backend is mocked here via a tiny in-process HTTPServer that records
each POST body — no FastAPI dependency, no live backend. The script
runs under `--unattended` (skips operator prompts) and a tiny
`--hold-s` so the full sweep finishes in well under a second.

Coverage:
  - Happy path: walks the configured range, writes a CSV with one row
    per step, posts the right body shape per step.
  - HTTP error path: a 500-returning backend leaves a `note` column
    rather than crashing.
  - RecordingSink CSV lookup: with `--log-dir` pointing at a stub
    sink CSV, `observed_us` is parsed from the CSV's last row.
  - Without `--log-dir`: `observed_us` is "n/a".
"""

from __future__ import annotations

import csv
import json
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "dev" / "hover_calibration.py"


class _IntentRecorder:
    """ThreadingHTTPServer harness that records POST /api/intent bodies."""

    def __init__(self, *, status: int = 200, require_token: str | None = None) -> None:
        self._status = status
        self._require_token = require_token
        self.posts: list[dict] = []
        self.authorizations: list[str | None] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = 0

    def __enter__(self) -> _IntentRecorder:
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
                if self.path != "/api/intent":
                    self.send_response(404)
                    self.end_headers()
                    return
                auth = self.headers.get("Authorization")
                if (
                    recorder._require_token is not None
                    and auth != f"Bearer {recorder._require_token}"
                ):
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"detail":"unauthorized"}')
                    with recorder._lock:
                        recorder.authorizations.append(auth)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                try:
                    parsed = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    parsed = {"_raw": body.hex()}
                with recorder._lock:
                    recorder.posts.append(parsed)
                    recorder.authorizations.append(auth)
                self.send_response(recorder._status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *args: object) -> None:  # silence default stderr
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _run_script(
    *extra_args: str,
    timeout_s: float = 10.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra_args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_s,
        env=env,
    )


def test_happy_path_walks_range_and_writes_csv(tmp_path: Path) -> None:
    with _IntentRecorder() as recorder:
        output = tmp_path / "session.csv"
        result = _run_script(
            "--backend-url",
            recorder.base_url,
            "--output",
            str(output),
            "--start",
            "1000",
            "--stop",
            "1030",
            "--step-us",
            "10",
            "--hold-s",
            "0.01",
            "--unattended",
        )

    assert result.returncode == 0, result.stderr
    assert output.exists()

    with output.open() as fh:
        rows = list(csv.DictReader(fh))
    # 1000, 1010, 1020, 1030 → 4 steps.
    assert len(rows) == 4, rows
    assert [int(r["requested_us"]) for r in rows] == [1000, 1010, 1020, 1030]
    assert all(r["classification"] == "n/a" for r in rows)
    # No --log-dir → observed is n/a.
    assert all(r["observed_us"] == "n/a" for r in rows)

    # All 4 intents arrived at the mock backend with the right shape.
    assert len(recorder.posts) == 4
    for posted, expected_us in zip(recorder.posts, [1000, 1010, 1020, 1030], strict=True):
        assert posted["desired_mode"] == 0  # Manual
        assert posted["setpoints"] == {"throttle": float(expected_us)}


def test_backend_500_recorded_in_notes_column(tmp_path: Path) -> None:
    with _IntentRecorder(status=500) as recorder:
        output = tmp_path / "session.csv"
        result = _run_script(
            "--backend-url",
            recorder.base_url,
            "--output",
            str(output),
            "--start",
            "1000",
            "--stop",
            "1010",
            "--step-us",
            "10",
            "--hold-s",
            "0.01",
            "--unattended",
        )

    # Script must not crash — it logs the error per step and continues.
    assert result.returncode == 0, result.stderr
    with output.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    # Every step recorded an HTTP error in the notes column.
    assert all("http 500" in r["notes"] for r in rows), rows


def test_observed_us_from_recording_sink_csv(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    stub_csv = log_dir / "sink_20260514T000000Z.csv"
    stub_csv.write_text(
        "timestamp_s,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4\n"
        "0.020000,1500,1500,1500,1100,1000,2000,1000,1000\n"
        "0.040000,1500,1500,1500,1234,1000,2000,1000,1000\n"
    )

    with _IntentRecorder() as recorder:
        output = tmp_path / "session.csv"
        result = _run_script(
            "--backend-url",
            recorder.base_url,
            "--output",
            str(output),
            "--start",
            "1100",
            "--stop",
            "1100",
            "--step-us",
            "10",
            "--hold-s",
            "0.01",
            "--unattended",
            "--log-dir",
            str(log_dir),
        )

    assert result.returncode == 0, result.stderr
    with output.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    # Last data row of the RecordingSink CSV has throttle=1234.
    assert rows[0]["observed_us"] == "1234"


def test_latest_csv_picked_when_multiple_sink_csvs_present(tmp_path: Path) -> None:
    # Operator restart of fc_app leaves multiple sink_*.csv files in
    # FC_RC_LOG_DIR. The script's _latest_csv must pick the
    # most-recently-modified one — a regression that drops `reverse=True`
    # or picks csvs[-1] would silently read a stale older file.
    import os

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Older session: throttle=999.
    older = log_dir / "sink_20260514T010000Z.csv"
    older.write_text(
        "timestamp_s,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4\n"
        "0.020000,1500,1500,1500,999,1000,2000,1000,1000\n"
    )
    # Force an older mtime so the sort is unambiguous regardless of
    # filesystem timestamp granularity.
    older_mtime = older.stat().st_mtime - 10.0
    os.utime(older, (older_mtime, older_mtime))

    # Newer session: throttle=1357 (what the script should pick up).
    newer = log_dir / "sink_20260514T020000Z.csv"
    newer.write_text(
        "timestamp_s,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4\n"
        "0.020000,1500,1500,1500,1357,1000,2000,1000,1000\n"
    )

    with _IntentRecorder() as recorder:
        output = tmp_path / "session.csv"
        result = _run_script(
            "--backend-url",
            recorder.base_url,
            "--output",
            str(output),
            "--start",
            "1100",
            "--stop",
            "1100",
            "--step-us",
            "10",
            "--hold-s",
            "0.01",
            "--unattended",
            "--log-dir",
            str(log_dir),
        )

    assert result.returncode == 0, result.stderr
    with output.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    # The newer CSV's throttle (1357), not the older one's (999).
    assert rows[0]["observed_us"] == "1357", rows[0]


def test_invalid_step_range_rejects_before_posting(tmp_path: Path) -> None:
    # start > stop is operator error. The script should refuse without
    # firing any HTTP requests.
    with _IntentRecorder() as recorder:
        result = _run_script(
            "--backend-url",
            recorder.base_url,
            "--output",
            str(tmp_path / "wont_be_written.csv"),
            "--start",
            "1500",
            "--stop",
            "1000",
            "--step-us",
            "10",
            "--hold-s",
            "0.01",
            "--unattended",
        )

    assert result.returncode == 2, result.stderr
    assert "must be <=" in result.stderr
    assert recorder.posts == []
    assert not (tmp_path / "wont_be_written.csv").exists()


def test_unattended_mode_skips_operator_prompt(tmp_path: Path) -> None:
    # Sanity: in --unattended mode the prompt never fires, so all rows
    # land with 'n/a'. This guards against a future change that calls
    # input() even in unattended mode (which would block on a closed
    # stdin in CI subprocess). The actual short-code mapping (l/s/c/k)
    # inside _prompt_classification is not exercised here — the
    # interactive prompt is hard to drive through subprocess stdin and
    # the mapping is straightforward enough to inspect by eye.
    with _IntentRecorder() as recorder:
        output = tmp_path / "session.csv"
        result = _run_script(
            "--backend-url",
            recorder.base_url,
            "--output",
            str(output),
            "--start",
            "1000",
            "--stop",
            "1000",
            "--step-us",
            "10",
            "--hold-s",
            "0.01",
            "--unattended",
        )

    assert result.returncode == 0, result.stderr
    with output.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["classification"] == "n/a"


def test_backend_api_token_env_var_picked_up_by_default(tmp_path: Path) -> None:
    # If the operator already exported BACKEND_API_TOKEN for the backend
    # process, the script should pick it up without --api-token. Otherwise
    # every POST would silently 401 and the operator gets a 71-row CSV of
    # failed requests masquerading as a successful sweep (Copilot review
    # round 3, comment #1).
    import os as _os

    expected_token = "operator-test-token"
    with _IntentRecorder(require_token=expected_token) as recorder:
        output = tmp_path / "session.csv"
        env = _os.environ.copy()
        env["BACKEND_API_TOKEN"] = expected_token
        result = _run_script(
            "--backend-url",
            recorder.base_url,
            "--output",
            str(output),
            "--start",
            "1000",
            "--stop",
            "1010",
            "--step-us",
            "10",
            "--hold-s",
            "0.01",
            "--unattended",
            env=env,
        )

    assert result.returncode == 0, result.stderr
    assert len(recorder.posts) == 2
    # Every recorded request carried the bearer header from the env var.
    assert recorder.authorizations == [f"Bearer {expected_token}"] * 2


def test_unauthorized_response_aborts_before_walking_full_range(tmp_path: Path) -> None:
    # 401 on the first step means auth is misconfigured — every subsequent
    # step will also 401. Continuing produces a misleading "complete" CSV
    # full of n/a rows. Script must abort fast so the operator notices
    # immediately (Copilot review round 3, comment #1).
    import os as _os

    with _IntentRecorder(require_token="some-other-token") as recorder:
        output = tmp_path / "session.csv"
        env = _os.environ.copy()
        env.pop("BACKEND_API_TOKEN", None)
        result = _run_script(
            "--backend-url",
            recorder.base_url,
            "--output",
            str(output),
            "--start",
            "1000",
            "--stop",
            "1100",
            "--step-us",
            "10",
            "--hold-s",
            "0.01",
            "--unattended",
            env=env,
        )

    # Non-zero exit, only one POST attempted before abort.
    assert result.returncode == 3, result.stderr
    assert "401 Unauthorized" in result.stderr
    assert len(recorder.authorizations) == 1, recorder.authorizations
    # CSV exists but is just the header — no data rows written before abort.
    with output.open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows == []


def test_keyboard_interrupt_posts_safety_reset_before_exit(tmp_path: Path) -> None:
    # Operator Ctrl-Cs mid-sweep — without a handler, the last requested
    # throttle stays in effect on the FC until something else overrides
    # it. Script must POST throttle=0 to leave the FC in a known-safe
    # state on exit. The 0→DRONE_MIN clamp lands in fc_app, not the
    # backend — RcMath::throttle_to_pwm treats 0 as a normalized
    # fraction (= 0%) and emits DRONE_MIN. (Copilot review round 4 #4,
    # wording fix round 5 #2.)
    with _IntentRecorder() as recorder:
        output = tmp_path / "session.csv"
        # --hold-s=2.0 so the script is reliably mid-sleep when we SIGINT.
        # --stop=2000 so the run can't possibly complete before our signal.
        proc = subprocess.Popen(
            [
                sys.executable,
                str(SCRIPT),
                "--backend-url",
                recorder.base_url,
                "--output",
                str(output),
                "--start",
                "1000",
                "--stop",
                "2000",
                "--step-us",
                "10",
                "--hold-s",
                "2.0",
                "--unattended",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait for the first POST so we know the loop has started.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if len(recorder.posts) >= 1:
                break
            time.sleep(0.05)
        assert len(recorder.posts) >= 1, "script never made first POST"
        before_sigint = len(recorder.posts)

        proc.send_signal(signal.SIGINT)
        try:
            _out, err = proc.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            _out, err = proc.communicate()
            raise

    assert proc.returncode == 130, f"expected SIGINT exit code 130; got {proc.returncode!r}"
    assert "safety reset" in err, f"expected safety-reset log on stderr; got {err!r}"
    # At least one additional POST landed after SIGINT — the safety
    # reset. It must carry throttle=0.
    assert len(recorder.posts) > before_sigint, (
        f"no safety-reset POST after SIGINT; "
        f"posts before={before_sigint} after={len(recorder.posts)}"
    )
    final = recorder.posts[-1]
    assert final["setpoints"] == {
        "throttle": 0.0
    }, f"final POST not the throttle=0 safety reset; got {final!r}"
    assert final["desired_mode"] == 0  # Manual


@pytest.mark.skipif(not SCRIPT.exists(), reason="hover_calibration.py missing")
def test_help_text_runs_clean() -> None:
    # Cheap smoke: --help must exit 0 and mention the key flags.
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5.0,
    )
    assert result.returncode == 0
    for flag in ("--backend-url", "--output", "--start", "--stop", "--unattended"):
        assert flag in result.stdout
