#!/usr/bin/env python3
"""Replay or synthesize VIS UDP datagrams for backend ingest testing."""

from __future__ import annotations

import argparse
import json
import math
import random
import socket
import time
from pathlib import Path
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9003
DEFAULT_HZ = 20.0
DEFAULT_MAX_BYTES = 512


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay/generate VIS datagrams over UDP")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="Replay VIS messages from JSONL",
    )
    source_group.add_argument(
        "--pattern",
        choices=("sweep", "lose", "reacquire"),
        default="sweep",
        help="Synthetic VIS pattern to generate (default: sweep)",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Destination host (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Destination port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=DEFAULT_HZ,
        help=f"Send rate in Hz (default: {DEFAULT_HZ})",
    )
    parser.add_argument("--duration-s", type=float, default=None, help="Send duration in seconds")
    parser.add_argument("--count", type=int, default=None, help="Number of messages to send")
    parser.add_argument("--seed", type=int, default=1, help="Seed for deterministic patterns")
    parser.add_argument("--start-seq", type=int, default=None, help="Initial sequence number")
    parser.add_argument(
        "--send-oversize-once",
        action="store_true",
        help="Send one deliberately oversize UDP datagram before normal traffic",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"Maximum payload size to enforce (default: {DEFAULT_MAX_BYTES})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.hz <= 0:
        raise ValueError("--hz must be > 0")
    if args.count is not None and args.count <= 0:
        raise ValueError("--count must be > 0")
    if args.duration_s is not None and args.duration_s <= 0:
        raise ValueError("--duration-s must be > 0")
    if args.max_bytes <= 0:
        raise ValueError("--max-bytes must be > 0")

    total_count = _resolve_total_count(args.count, args.duration_s, args.hz)
    start_seq = args.start_seq if args.start_seq is not None else int(time.monotonic() * 1000.0)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        if args.send_oversize_once:
            oversize = b"x" * (args.max_bytes + 1)
            sock.sendto(oversize, (args.host, args.port))

        if args.jsonl is not None:
            sent = _replay_jsonl(
                sock=sock,
                path=args.jsonl,
                host=args.host,
                port=args.port,
                hz=args.hz,
                max_bytes=args.max_bytes,
                max_count=total_count,
            )
        else:
            rng = random.Random(args.seed)
            sent = _send_pattern(
                sock=sock,
                pattern=args.pattern,
                host=args.host,
                port=args.port,
                hz=args.hz,
                max_bytes=args.max_bytes,
                count=total_count,
                start_seq=start_seq,
                rng=rng,
            )

    print(f"Sent {sent} VIS datagrams to udp://{args.host}:{args.port}")
    return 0


def _resolve_total_count(count: int | None, duration_s: float | None, hz: float) -> int:
    if count is not None:
        return count
    if duration_s is not None:
        return max(1, int(round(duration_s * hz)))
    return 100


def _replay_jsonl(
    sock: socket.socket,
    path: Path,
    host: str,
    port: int,
    hz: float,
    max_bytes: int,
    max_count: int,
) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return 0

    period_s = 1.0 / hz
    sent = 0
    next_send_s = time.monotonic()
    for line in lines:
        if sent >= max_count:
            break
        if not line.strip():
            continue
        payload_obj = json.loads(line)
        payload_bytes = _encode_payload(payload_obj, max_bytes=max_bytes)
        _sleep_until(next_send_s)
        sock.sendto(payload_bytes, (host, port))
        sent += 1
        next_send_s += period_s
    return sent


def _send_pattern(
    sock: socket.socket,
    pattern: str,
    host: str,
    port: int,
    hz: float,
    max_bytes: int,
    count: int,
    start_seq: int,
    rng: random.Random,
) -> int:
    period_s = 1.0 / hz
    next_send_s = time.monotonic()
    sent = 0
    for index in range(count):
        msg = _build_pattern_msg(
            pattern=pattern,
            index=index,
            total=count,
            seq=start_seq + index,
            timestamp_s=time.monotonic(),
            rng=rng,
        )
        payload = _encode_payload(msg, max_bytes=max_bytes)
        _sleep_until(next_send_s)
        sock.sendto(payload, (host, port))
        sent += 1
        next_send_s += period_s
    return sent


def _build_pattern_msg(
    pattern: str,
    index: int,
    total: int,
    seq: int,
    timestamp_s: float,
    rng: random.Random,
) -> dict[str, Any]:
    if pattern == "sweep":
        denom = max(1, total - 1)
        loc_x = -1.0 + (2.0 * (index / denom))
        return {
            "type": "VIS",
            "seq": seq,
            "timestamp_s": float(timestamp_s),
            "tracking_state": 3,
            "loc_x": float(loc_x),
            "loc_y": float(0.2 * math.sin(index / 8.0)),
            "bound_w": 0.22,
            "bound_h": 0.30,
            "confidence": 0.9,
        }
    if pattern == "lose":
        phase = index / max(1, total)
        if phase < 0.5:
            return {
                "type": "VIS",
                "seq": seq,
                "timestamp_s": float(timestamp_s),
                "tracking_state": 3,
                "loc_x": float(max(-1.0, min(1.0, 0.5 - phase))),
                "loc_y": -0.1,
                "bound_w": 0.24,
                "bound_h": 0.28,
                "confidence": 0.85,
            }
        if phase < 0.75:
            return _zero_vis(seq=seq, timestamp_s=timestamp_s, tracking_state=4)
        return _zero_vis(seq=seq, timestamp_s=timestamp_s, tracking_state=1)
    if pattern == "reacquire":
        phase = index / max(1, total)
        if phase < 0.33:
            return _zero_vis(seq=seq, timestamp_s=timestamp_s, tracking_state=1)
        if phase < 0.66:
            return _zero_vis(seq=seq, timestamp_s=timestamp_s, tracking_state=2)
        return {
            "type": "VIS",
            "seq": seq,
            "timestamp_s": float(timestamp_s),
            "tracking_state": 3,
            "loc_x": rng.uniform(-0.2, 0.2),
            "loc_y": rng.uniform(-0.2, 0.2),
            "bound_w": 0.20,
            "bound_h": 0.24,
            "confidence": 0.92,
        }
    raise ValueError(f"unsupported pattern: {pattern}")


def _zero_vis(seq: int, timestamp_s: float, tracking_state: int) -> dict[str, Any]:
    return {
        "type": "VIS",
        "seq": seq,
        "timestamp_s": float(timestamp_s),
        "tracking_state": tracking_state,
        "loc_x": 0.0,
        "loc_y": 0.0,
        "bound_w": 0.0,
        "bound_h": 0.0,
        "confidence": 0.0,
    }


def _encode_payload(payload: dict[str, Any], max_bytes: int) -> bytes:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(
            f"VIS payload exceeds {max_bytes} bytes (len={len(encoded)}): "
            f"type={payload.get('type')}"
        )
    return encoded


def _sleep_until(deadline_s: float) -> None:
    remaining = deadline_s - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
