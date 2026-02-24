#!/usr/bin/env python3
"""Send framed CMD messages to fc_app over TCP."""

import argparse
import json
import socket
import struct
import time


def build_frame(seq: int, desired_mode: int) -> bytes:
    payload_dict = {
        "type": "CMD",
        "seq": seq,
        "timestamp_s": time.monotonic(),
        "desired_mode": desired_mode,
    }
    payload = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(payload)) + payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send framed CMD to 127.0.0.1:9002")
    parser.add_argument("desired_mode", type=int, help="Control mode to command (e.g. 1)")
    parser.add_argument(
        "--seq",
        type=int,
        default=None,
        help="Initial sequence number (default: based on monotonic clock)",
    )
    parser.add_argument(
        "--repeat-hz",
        type=float,
        default=0.0,
        help="If >0, send a short burst at this rate instead of one frame",
    )
    parser.add_argument(
        "--burst-seconds",
        type=float,
        default=0.6,
        help="Burst duration when --repeat-hz is set (default: 0.6s)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9002)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    seq = args.seq if args.seq is not None else int(time.monotonic() * 1000.0)

    try:
        with socket.create_connection((args.host, args.port), timeout=2.0) as sock:
            if args.repeat_hz > 0.0:
                period_s = 1.0 / args.repeat_hz
                deadline = time.monotonic() + max(0.0, args.burst_seconds)
                sent = 0
                while time.monotonic() < deadline:
                    sock.sendall(build_frame(seq=seq, desired_mode=args.desired_mode))
                    seq += 1
                    sent += 1
                    time.sleep(period_s)
                print(f"Sent {sent} CMD frames at ~{args.repeat_hz:.2f} Hz")
            else:
                sock.sendall(build_frame(seq=seq, desired_mode=args.desired_mode))
                print(f"Sent single CMD frame: desired_mode={args.desired_mode}, seq={seq}")
    except OSError as exc:
        print(f"Failed to send CMD: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
