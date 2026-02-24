#!/usr/bin/env python3
"""PR4 protocol smoke test: CMD apply + timeout fallback via telemetry."""

import argparse
import json
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TelSnapshot:
    recv_monotonic_s: float
    seq: int
    control_mode: int
    tracking_state: int


class TelState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.latest: TelSnapshot | None = None

    def update(self, snapshot: TelSnapshot) -> None:
        with self._lock:
            self.latest = snapshot

    def get(self) -> TelSnapshot | None:
        with self._lock:
            return self.latest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test FC CMD/TEL protocol behavior")
    parser.add_argument("--tcp-host", default="127.0.0.1")
    parser.add_argument("--tcp-port", type=int, default=9002)
    parser.add_argument("--udp-host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, default=9001)
    parser.add_argument("--cmd-mode", type=int, default=1)
    parser.add_argument("--cmd-hz", type=float, default=50.0)
    parser.add_argument("--cmd-burst-seconds", type=float, default=0.3)
    parser.add_argument("--apply-timeout-seconds", type=float, default=0.2)
    parser.add_argument("--fallback-timeout-seconds", type=float, default=0.8)
    parser.add_argument("--spawn-fc-app", action="store_true")
    parser.add_argument("--fc-app-path", default="build/src/fc/fc_app")
    return parser.parse_args()


def build_cmd_frame(seq: int, desired_mode: int) -> bytes:
    payload_dict = {
        "type": "CMD",
        "seq": seq,
        "timestamp_s": time.monotonic(),
        "desired_mode": desired_mode,
    }
    payload = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(payload)) + payload


def start_tel_listener(
    host: str, port: int, state: TelState, stop_event: threading.Event
) -> threading.Thread:
    def _run() -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((host, port))
        sock.settimeout(0.1)
        try:
            while not stop_event.is_set():
                try:
                    data, _ = sock.recvfrom(4096)
                except TimeoutError:
                    continue
                try:
                    msg = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if msg.get("type") != "TEL":
                    continue
                try:
                    snapshot = TelSnapshot(
                        recv_monotonic_s=time.monotonic(),
                        seq=int(msg["seq"]),
                        control_mode=int(msg["control_mode"]),
                        tracking_state=int(msg["tracking_state"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                state.update(snapshot)
        finally:
            sock.close()

    thread = threading.Thread(target=_run, name="tel-listener", daemon=True)
    thread.start()
    return thread


def wait_for_mode(
    state: TelState,
    mode: int,
    timeout_s: float,
    not_before_s: float | None = None,
) -> TelSnapshot | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snap = state.get()
        if snap is not None:
            if (
                not_before_s is None or snap.recv_monotonic_s >= not_before_s
            ) and snap.control_mode == mode:
                return snap
        time.sleep(0.01)
    return None


def wait_for_any_telemetry(state: TelState, timeout_s: float) -> TelSnapshot | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snap = state.get()
        if snap is not None:
            return snap
        time.sleep(0.01)
    return None


def main() -> int:
    args = parse_args()

    fc_proc: subprocess.Popen[str] | None = None
    if args.spawn_fc_app:
        fc_path = Path(args.fc_app_path)
        if not fc_path.exists():
            print(f"fc_app binary not found: {fc_path}")
            return 1
        fc_proc = subprocess.Popen(
            [str(fc_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(0.5)

    tel_state = TelState()
    stop_event = threading.Event()
    listener = start_tel_listener(args.udp_host, args.udp_port, tel_state, stop_event)

    try:
        if wait_for_any_telemetry(tel_state, timeout_s=2.0) is None:
            print("No telemetry received on UDP listener. Is fc_app running?")
            return 1

        start_send_s = time.monotonic()
        seq = int(start_send_s * 1000.0)
        send_period_s = 1.0 / args.cmd_hz
        send_deadline_s = start_send_s + max(0.0, args.cmd_burst_seconds)
        apply_deadline_s = start_send_s + max(0.0, args.apply_timeout_seconds)

        apply_snap: TelSnapshot | None = None
        try:
            with socket.create_connection((args.tcp_host, args.tcp_port), timeout=2.0) as sock:
                while time.monotonic() < send_deadline_s:
                    sock.sendall(build_cmd_frame(seq=seq, desired_mode=args.cmd_mode))
                    seq += 1
                    snap = tel_state.get()
                    if (
                        snap is not None
                        and snap.recv_monotonic_s >= start_send_s
                        and snap.control_mode == args.cmd_mode
                    ):
                        apply_snap = snap
                        break
                    time.sleep(send_period_s)
        except OSError as exc:
            print(f"Failed to connect/send CMD to {args.tcp_host}:{args.tcp_port}: {exc}")
            return 1

        if apply_snap is None:
            remaining_apply_s = max(0.0, apply_deadline_s - time.monotonic())
            apply_snap = wait_for_mode(
                tel_state,
                mode=args.cmd_mode,
                timeout_s=remaining_apply_s,
                not_before_s=start_send_s,
            )
        if apply_snap is None:
            print(
                f"FAIL: telemetry did not reach control_mode={args.cmd_mode} "
                f"within {args.apply_timeout_seconds:.3f}s"
            )
            return 1
        print(
            f"PASS: telemetry reached control_mode={args.cmd_mode} "
            f"(seq={apply_snap.seq}, tracking_state={apply_snap.tracking_state})"
        )

        stop_send_s = time.monotonic()
        fallback_snap = wait_for_mode(
            tel_state,
            mode=2,
            timeout_s=args.fallback_timeout_seconds,
            not_before_s=stop_send_s,
        )
        if fallback_snap is None:
            print(
                f"FAIL: telemetry did not return to control_mode=2 "
                f"within {args.fallback_timeout_seconds:.3f}s after CMD stop"
            )
            return 1
        print(
            f"PASS: telemetry returned to control_mode=2 "
            f"(seq={fallback_snap.seq}, tracking_state={fallback_snap.tracking_state})"
        )
        print("Protocol smoke test passed.")
        return 0
    finally:
        stop_event.set()
        listener.join(timeout=1.0)
        if fc_proc is not None:
            fc_proc.terminate()
            try:
                fc_proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                fc_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
