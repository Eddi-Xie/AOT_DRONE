#!/usr/bin/env python3
"""HIL bench harness — listen for FakeBetaflightSink RC datagrams.

Decodes the UDP frames produced by FakeBetaflightSink (S0.7) and asserts
the operational invariants we care about for HIL replay:

  * monotonically advancing per-sender ``seq`` (no gaps over short
    horizons; bounded gaps tolerated for transient packet loss).
  * received Hz within tolerance of the expected publish rate (50 Hz
    default).
  * channel values inside the RC band [DRONE_MIN, DRONE_MAX] = [1000, 2000]
    so a future MspRcSink swap can never write out-of-band micro-seconds
    to a real flight controller.

Use this as the receiving end of an HIL session::

    # Terminal A:
    python -m scripts.dev.fake_betaflight_listener --duration-s 5

    # Terminal B:
    FC_RC_SINK=fake ./build/src/fc/fc_app

The listener exits 0 on a clean run that meets all asserted invariants
and exits 2 on any failure (asserts surface as the script's exit code so
``runall.sh`` and CI can gate on it).
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from dataclasses import dataclass

# RC channel band — matches src/fc/header/RcConstants.h. We hard-code
# them rather than importing because this script must run under the
# default interpreter without project deps.
RC_MIN_US = 1000
RC_MAX_US = 2000

EXPECTED_FIELDS = ("type", "seq", "timestamp_s", "channels")


@dataclass
class RunStats:
    received: int = 0
    decode_errors: int = 0
    out_of_band: int = 0
    seq_first: int | None = None
    seq_last: int | None = None
    seq_gaps: int = 0  # number of non-+1 advances (excluding the very first frame)
    t_first_recv: float | None = None
    t_last_recv: float | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="HIL: receive + assert FakeBetaflightSink RC datagrams"
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9101, help="Bind UDP port (default: 9101)")
    parser.add_argument(
        "--duration-s",
        type=float,
        default=5.0,
        help="Receive window in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--expect-hz",
        type=float,
        default=50.0,
        help="Expected publish rate; the run must come within --hz-tolerance",
    )
    parser.add_argument(
        "--hz-tolerance",
        type=float,
        default=0.30,
        help="Fractional tolerance on expected Hz (default: 0.30 = +/-30 percent)",
    )
    parser.add_argument(
        "--min-frames",
        type=int,
        default=10,
        help="Minimum frames required before stats are evaluated (default: 10)",
    )
    parser.add_argument(
        "--max-seq-gap",
        type=int,
        default=2,
        help="Tolerated upper bound on (curr_seq - prev_seq); >1 indicates loss "
        "(default: 2 = up to 1 dropped frame between observed seqs)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="If set, any decode error or out-of-band channel = fail. Otherwise "
        "decode errors are warned-once and out-of-band failures abort.",
    )
    return parser.parse_args()


def is_in_band(channel_us: int) -> bool:
    return RC_MIN_US <= channel_us <= RC_MAX_US


def decode_frame(payload: bytes) -> dict | None:
    """Return the parsed frame on success or None on schema/JSON failure."""
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") != "RC":
        return None
    for field in EXPECTED_FIELDS:
        if field not in obj:
            return None
    if not isinstance(obj["seq"], int) or obj["seq"] < 0:
        return None
    if not isinstance(obj["timestamp_s"], int | float):
        return None
    if not isinstance(obj["channels"], list) or len(obj["channels"]) != 8:
        return None
    if not all(isinstance(c, int) for c in obj["channels"]):
        return None
    return obj


def main() -> int:
    args = parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((args.host, args.port))
    except OSError as exc:
        # Most common case: a previous listener is still running on the
        # same port. Without the guard this raised an unfriendly
        # traceback + exit 1, contradicting the documented "exit 0/2"
        # contract that runall.sh and CI gate on.
        print(
            f"[harness] FAIL: bind {args.host}:{args.port}: {exc}",
            file=sys.stderr,
        )
        sock.close()
        return 2
    sock.settimeout(0.25)

    print(
        f"[harness] listening on {args.host}:{args.port} for {args.duration_s:.1f}s "
        f"(expect {args.expect_hz:.1f} Hz +/- {args.hz_tolerance*100:.0f}%)",
        file=sys.stderr,
    )

    stats = RunStats()
    deadline = time.monotonic() + args.duration_s

    try:
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(2048)
            except TimeoutError:
                continue

            now = time.monotonic()
            if stats.t_first_recv is None:
                stats.t_first_recv = now
            stats.t_last_recv = now

            frame = decode_frame(data)
            if frame is None:
                stats.decode_errors += 1
                if args.strict:
                    print(f"[harness] strict: decode error on payload {data!r}", file=sys.stderr)
                    return 2
                continue

            stats.received += 1
            if stats.seq_first is None:
                stats.seq_first = frame["seq"]
            else:
                gap = frame["seq"] - (stats.seq_last or 0)
                if gap != 1:
                    stats.seq_gaps += 1
                    if gap < 0 or gap > args.max_seq_gap:
                        # Out-of-tolerance gap: a roll-back or a multi-frame
                        # drop is a hard failure even outside strict mode.
                        print(
                            f"[harness] seq gap out of tolerance: {stats.seq_last} -> "
                            f"{frame['seq']} (max allowed {args.max_seq_gap})",
                            file=sys.stderr,
                        )
                        return 2
            stats.seq_last = frame["seq"]

            for ch in frame["channels"]:
                if not is_in_band(ch):
                    stats.out_of_band += 1
                    print(
                        f"[harness] OUT-OF-BAND channel: seq={frame['seq']} "
                        f"channels={frame['channels']} (must be in [{RC_MIN_US}, {RC_MAX_US}])",
                        file=sys.stderr,
                    )
                    return 2
    finally:
        sock.close()

    return _evaluate_and_report(stats, args)


def _evaluate_and_report(stats: RunStats, args: argparse.Namespace) -> int:
    if stats.received < args.min_frames:
        print(
            f"[harness] FAIL: only received {stats.received} frames; "
            f"min required {args.min_frames}. Decode errors: {stats.decode_errors}",
            file=sys.stderr,
        )
        return 2

    assert stats.t_first_recv is not None and stats.t_last_recv is not None
    elapsed = max(stats.t_last_recv - stats.t_first_recv, 1e-6)
    observed_hz = (stats.received - 1) / elapsed if stats.received > 1 else 0.0
    lo = args.expect_hz * (1 - args.hz_tolerance)
    hi = args.expect_hz * (1 + args.hz_tolerance)

    tol_pct = args.hz_tolerance * 100
    print(
        f"[harness] received={stats.received} "
        f"seq={stats.seq_first}..{stats.seq_last} gaps={stats.seq_gaps} "
        f"observed_hz={observed_hz:.2f} (target {args.expect_hz:.2f} +/-{tol_pct:.0f}%) "
        f"decode_errors={stats.decode_errors}",
        file=sys.stderr,
    )

    if not (lo <= observed_hz <= hi):
        print(
            f"[harness] FAIL: observed_hz={observed_hz:.2f} outside [{lo:.2f}, {hi:.2f}]",
            file=sys.stderr,
        )
        return 2

    if args.strict and stats.decode_errors > 0:
        print(
            f"[harness] FAIL (strict): decode_errors={stats.decode_errors}",
            file=sys.stderr,
        )
        return 2

    print("[harness] OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
