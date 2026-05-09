#!/usr/bin/env python3
"""Read a RecordingSink CSV and report basic statistics.

Stub for the future "replay a recorded mission against the FC" tool.
The full version (Sprint 1) injects the recorded CMD timeline into the
FC's TCP listener and asserts the resulting RC channels match the
recorded RC trace within tolerance. This first cut is offline-only:

* loads the CSV
* asserts the header matches the contract (S0.7)
* prints per-channel min/max/mean and the observed Hz so a developer
  eyeballing a HIL bench session can confirm shape without a notebook

Usage::

    python -m scripts.dev.replay_mission logs/hil/sink_20260508T120000Z.csv

Exit code 0 on success, 2 on schema mismatch / missing file.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

EXPECTED_HEADER = (
    "timestamp_s",
    "roll",
    "pitch",
    "yaw",
    "throttle",
    "aux1",
    "aux2",
    "aux3",
    "aux4",
)
RC_MIN_US = 1000
RC_MAX_US = 2000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarise a RecordingSink CSV (S0.7 stub)")
    parser.add_argument("path", type=Path, help="CSV file produced by FC RecordingSink")
    parser.add_argument(
        "--check-band",
        action="store_true",
        help="Fail if any channel sample is outside [1000, 2000] microseconds",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.path.exists():
        print(f"[replay] FAIL: file not found: {args.path}", file=sys.stderr)
        return 2

    with args.path.open(newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = tuple(next(reader))
        except StopIteration:
            print("[replay] FAIL: empty CSV", file=sys.stderr)
            return 2

        if header != EXPECTED_HEADER:
            print(
                f"[replay] FAIL: header mismatch.\n  got:  {header}\n  want: {EXPECTED_HEADER}",
                file=sys.stderr,
            )
            return 2

        timestamps: list[float] = []
        per_channel: dict[str, list[int]] = {ch: [] for ch in EXPECTED_HEADER[1:]}

        for row in reader:
            if len(row) != len(EXPECTED_HEADER):
                print(f"[replay] FAIL: malformed row: {row}", file=sys.stderr)
                return 2
            try:
                t = float(row[0])
                channels = [int(x) for x in row[1:]]
            except ValueError as e:
                print(f"[replay] FAIL: parse error in row {row}: {e}", file=sys.stderr)
                return 2
            timestamps.append(t)
            for name, value in zip(EXPECTED_HEADER[1:], channels, strict=False):
                per_channel[name].append(value)

    n = len(timestamps)
    if n == 0:
        print("[replay] FAIL: header but zero data rows", file=sys.stderr)
        return 2

    out_of_band: list[tuple[str, int, int]] = []  # (channel_name, row_index, value)
    if args.check_band:
        for name, samples in per_channel.items():
            for i, v in enumerate(samples):
                if not (RC_MIN_US <= v <= RC_MAX_US):
                    out_of_band.append((name, i, v))

    duration_s = (timestamps[-1] - timestamps[0]) if n > 1 else 0.0
    hz = (n - 1) / duration_s if duration_s > 0 else 0.0

    print(f"[replay] file={args.path}")
    print(f"[replay] rows={n} duration_s={duration_s:.3f} hz={hz:.2f}")
    for name, samples in per_channel.items():
        lo, hi = min(samples), max(samples)
        mean = statistics.fmean(samples)
        print(f"[replay]   {name}: min={lo} max={hi} mean={mean:.1f}")

    if args.check_band and out_of_band:
        print(f"[replay] FAIL: {len(out_of_band)} out-of-band samples", file=sys.stderr)
        for name, i, v in out_of_band[:10]:
            print(f"[replay]   {name}[{i}]={v}", file=sys.stderr)
        return 2

    print("[replay] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
