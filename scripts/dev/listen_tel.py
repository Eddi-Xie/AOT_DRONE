#!/usr/bin/env python3
"""Listen for FC telemetry UDP datagrams on :9001."""

import argparse
import json
import socket
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Listen for TEL packets on UDP :9001")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9001, help="Bind UDP port (default: 9001)")
    parser.add_argument(
        "--max-print-hz",
        type=float,
        default=10.0,
        help="Maximum summary print rate (default: 10 Hz)",
    )
    parser.add_argument(
        "--show-json",
        action="store_true",
        help="Also print full JSON payload for each displayed update",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print_period_s = 0.0 if args.max_print_hz <= 0 else (1.0 / args.max_print_hz)
    next_print_s = 0.0

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(1.0)
    print(f"Listening for TEL on udp://{args.host}:{args.port} ... Ctrl+C to stop")

    try:
        while True:
            try:
                data, src = sock.recvfrom(4096)
            except TimeoutError:
                continue

            now_s = time.monotonic()
            if print_period_s > 0.0 and now_s < next_print_s:
                continue

            try:
                msg = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                print(f"Invalid TEL datagram ({len(data)} bytes) from {src}")
                continue

            if msg.get("type") != "TEL":
                print(f"Ignoring non-TEL message from {src}: type={msg.get('type')}")
                continue

            print(
                "TEL",
                f"seq={msg.get('seq')}",
                f"control_mode={msg.get('control_mode')}",
                f"tracking_state={msg.get('tracking_state')}",
            )
            if args.show_json:
                print(json.dumps(msg, separators=(",", ":"), sort_keys=True))

            if print_period_s > 0.0:
                next_print_s = now_s + print_period_s
    except KeyboardInterrupt:
        print("\nStopped telemetry listener.")
        return 0
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
