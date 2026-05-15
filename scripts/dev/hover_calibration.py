#!/usr/bin/env python3
"""Hover throttle calibration sweep (S0.9).

Walks fc_app's throttle setpoint through a configurable range, holds at
each step long enough for an operator to read the FC's actual channel
value, and writes a CSV that feeds into Sprint 1 motor tests. The CSV
gives the bench engineer + Eddi a controller-output curve to compare
against motor-spin observations once props are reinstalled.

Bench prerequisites (motors physically detached, per ADR-004):

  1. Backend running:    python -m uvicorn src.backend.app:app
  2. Webapp running:     npm --prefix src/webapp run dev      (optional;
                         only needed if the operator wants the dashboard
                         open alongside the script)
  3. fc_app running with the RecordingSink so each tick's commanded
     channels land in a CSV:

         FC_RC_SINK=recording \\
         FC_RC_LOG_DIR=/tmp/hover_cal \\
           ./build/src/fc/fc_app

     The script's `--log-dir` flag points at the same directory.

How the script works:

  - For each step in [start, stop] (step `--step-us` µs):
      1. POST /api/intent with desired_mode=Manual and setpoints.throttle
         = step_us, telling fc_app to drive that channel value.
      2. Sleep `--hold-s` seconds so fc_app's setpoint propagates and the
         RecordingSink CSV captures a stable row.
      3. Prompt the operator to classify what they're seeing
         (liftoff / stable / clipping / skip). With `--unattended` the
         prompt is skipped and "n/a" is recorded (CI smoke path).
      4. Read the most recent RecordingSink CSV row to capture the
         observed throttle. NOTE: "observed" here means "what fc_app
         most recently SENT on the wire post-clamp", not "what the FC
         echoed back via MSP_RC". Until S0.14 surfaces MSP_RC channel
         values in TEL, the wire-side commanded value is the
         operator's closest signal.
  - Writes a CSV with columns:
      step_idx, requested_us, observed_us, classification, notes

Recovery from operator typos: any step that fails (HTTP error, CSV
unreadable) records the failure in the `notes` column and continues so
a 71-step run isn't lost to a single backend hiccup.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="S0.9 hover throttle calibration sweep against fc_app + backend.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--backend-url",
        default="http://127.0.0.1:8000",
        help="Backend base URL (default: %(default)s).",
    )
    parser.add_argument(
        "--api-token",
        default=None,
        help="Bearer token if BACKEND_API_TOKEN is set on the backend (default: none).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (default: hover_cal_<utc>.csv in cwd).",
    )
    parser.add_argument(
        "--start", type=int, default=1000, help="Start throttle µs (default: %(default)s)."
    )
    parser.add_argument(
        "--stop",
        type=int,
        default=1700,
        help="Stop throttle µs (inclusive; default: %(default)s).",
    )
    parser.add_argument(
        "--step-us",
        type=int,
        default=10,
        help="Step size µs (default: %(default)s).",
    )
    parser.add_argument(
        "--hold-s",
        type=float,
        default=1.0,
        help="Hold each step for this many seconds before prompting (default: %(default)s).",
    )
    parser.add_argument(
        "--unattended",
        action="store_true",
        help="Skip operator prompts; record 'n/a' in the classification column. "
        "Useful for CI smoke tests.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="RecordingSink log directory (fc_app's FC_RC_LOG_DIR). If unset, "
        "observed_us is recorded as 'n/a'.",
    )
    parser.add_argument(
        "--request-timeout-s",
        type=float,
        default=2.0,
        help="HTTP timeout per POST /api/intent (default: %(default)s).",
    )
    return parser.parse_args()


def _post_intent(
    backend_url: str,
    throttle_us: int,
    api_token: str | None,
    timeout_s: float,
) -> tuple[bool, str]:
    """POST /api/intent with desired_mode=Manual + setpoints.throttle.

    Returns (success, note) — note empty on success, error string otherwise.
    """
    payload = {
        "desired_mode": 0,  # Manual
        "setpoints": {"throttle": float(throttle_us)},
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    req = urllib.request.Request(
        f"{backend_url.rstrip('/')}/api/intent",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status not in (200, 201, 204):
                return False, f"http {resp.status}"
            return True, ""
    except urllib.error.HTTPError as exc:
        return False, f"http {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return False, f"network: {exc.reason}"
    except TimeoutError:
        return False, "timeout"


def _latest_csv(log_dir: Path) -> Path | None:
    """Return the most-recently-modified sink_*.csv in log_dir, or None."""
    if not log_dir.is_dir():
        return None
    csvs = sorted(
        log_dir.glob("sink_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return csvs[0] if csvs else None


def _read_observed_throttle(csv_path: Path) -> int | None:
    """Return the throttle µs from the LAST data row in the RecordingSink CSV.

    The CSV header is:
        timestamp_s,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4
    fflush after each row guarantees the file is readable concurrently
    with fc_app writing it.
    """
    try:
        with csv_path.open("r", newline="") as fh:
            reader = csv.DictReader(fh)
            last_row: dict | None = None
            for row in reader:
                last_row = row
            if last_row is None:
                return None
            return int(last_row["throttle"])
    except (OSError, ValueError, KeyError):
        return None


def _prompt_classification(throttle_us: int) -> str:
    valid = {"liftoff", "stable", "clipping", "skip", "l", "s", "c", "k"}
    short = {"l": "liftoff", "s": "stable", "c": "clipping", "k": "skip"}
    while True:
        ans = (
            input(
                f"  step {throttle_us} µs — classify [liftoff / stable / clipping / skip] "
                "or [l/s/c/k]: "
            )
            .strip()
            .lower()
        )
        if not ans:
            continue
        if ans in valid:
            return short.get(ans, ans)
        print(f"    unrecognised: {ans!r}. Pick one of: liftoff, stable, clipping, skip.")


def _default_output_path() -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(f"hover_cal_{stamp}.csv")


def main() -> int:
    args = parse_args()

    if args.start > args.stop:
        print(
            f"--start ({args.start}) must be <= --stop ({args.stop})",
            file=sys.stderr,
        )
        return 2
    if args.step_us <= 0:
        print(f"--step-us must be positive, got {args.step_us}", file=sys.stderr)
        return 2

    output_path = args.output or _default_output_path()
    steps = list(range(args.start, args.stop + 1, args.step_us))
    print(
        f"hover_calibration: {len(steps)} steps from {args.start} to {args.stop} µs "
        f"({args.step_us} µs step, {args.hold_s}s hold each). Output: {output_path}"
    )
    if args.unattended:
        print("hover_calibration: --unattended; skipping operator prompts.")
    if args.log_dir is None:
        print(
            "hover_calibration: --log-dir not provided; observed_us column will be 'n/a'.",
            file=sys.stderr,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["step_idx", "requested_us", "observed_us", "classification", "notes"])

        for idx, throttle_us in enumerate(steps):
            ok, note = _post_intent(
                args.backend_url, throttle_us, args.api_token, args.request_timeout_s
            )
            if not ok:
                print(
                    f"  step {throttle_us} µs — POST /api/intent failed: {note}. Continuing.",
                    file=sys.stderr,
                )

            time.sleep(args.hold_s)

            classification = "n/a"
            if not args.unattended:
                try:
                    classification = _prompt_classification(throttle_us)
                except EOFError:
                    # Pipe closed (e.g. unattended subprocess) — fall back.
                    classification = "n/a"

            observed: object = "n/a"
            if args.log_dir is not None:
                csv_path = _latest_csv(args.log_dir)
                if csv_path is None:
                    note = note or "no RecordingSink CSV in --log-dir"
                else:
                    parsed = _read_observed_throttle(csv_path)
                    if parsed is None:
                        note = note or f"could not read throttle from {csv_path.name}"
                    else:
                        observed = parsed

            writer.writerow([idx, throttle_us, observed, classification, note])
            fh.flush()

            print(
                f"  step {idx + 1}/{len(steps)}: requested={throttle_us} µs "
                f"observed={observed} classification={classification}"
            )

    print(f"hover_calibration: wrote {len(steps)} steps to {output_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
