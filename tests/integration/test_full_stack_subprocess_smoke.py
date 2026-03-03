from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, request

import pytest


@pytest.mark.integration
def test_full_stack_subprocess_smoke() -> None:
    if not _env_flag("RUN_FULL_E2E"):
        pytest.skip("set RUN_FULL_E2E=1 to run subprocess full-stack smoke")

    repo_root = Path(__file__).resolve().parents[2]
    _require_uvicorn_or_skip()
    backend_port = _reserve_port_or_skip(socket.SOCK_STREAM, "backend HTTP")
    vis_port = _reserve_port_or_skip(socket.SOCK_DGRAM, "backend VIS UDP")
    run_with_fc = _env_flag("RUN_FULL_E2E_WITH_FC")

    env = os.environ.copy()
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "BACKEND_VIS_HOST": "127.0.0.1",
            "BACKEND_VIS_PORT": str(vis_port),
            "BACKEND_VIS_LOG_INTERVAL_S": "0.0",
            "BACKEND_TEL_LOG_INTERVAL_S": "0.0",
            "BACKEND_WS_LINK_HZ": "5.0",
            "BACKEND_VIDEO_ENABLED": "0",
        }
    )

    fc_proc: subprocess.Popen[str] | None = None
    if run_with_fc:
        fc_proc = _start_fc_app_or_skip(repo_root)
        env.update(
            {
                "BACKEND_CMD_BRIDGE_ENABLED": "1",
                "BACKEND_TEL_INGEST_ENABLED": "1",
                "BACKEND_TEL_HOST": "127.0.0.1",
                "BACKEND_TEL_PORT": "9001",
                "BACKEND_FC_HOST": "127.0.0.1",
                "BACKEND_FC_PORT": "9002",
            }
        )
    else:
        env.update(
            {
                "BACKEND_CMD_BRIDGE_ENABLED": "0",
                "BACKEND_TEL_INGEST_ENABLED": "0",
            }
        )

    backend_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "src.backend.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(backend_port),
        "--log-level",
        "warning",
    ]
    backend_proc = subprocess.Popen(
        backend_cmd,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        _wait_for_health(f"http://127.0.0.1:{backend_port}/health", timeout_s=5.0)

        replay_cmd = [
            sys.executable,
            "scripts/dev/vision_replay.py",
            "--pattern",
            "sweep",
            "--host",
            "127.0.0.1",
            "--port",
            str(vis_port),
            "--hz",
            "20",
            "--count",
            "20",
            "--seed",
            "7",
        ]
        replay_result = subprocess.run(
            replay_cmd,
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=6.0,
            check=False,
        )
        assert replay_result.returncode == 0, (
            f"vision replay failed with code {replay_result.returncode}\n"
            f"stdout:\n{replay_result.stdout}\n"
            f"stderr:\n{replay_result.stderr}"
        )

        if run_with_fc:
            _http_post_json(
                f"http://127.0.0.1:{backend_port}/api/intent",
                {"desired_mode": 1},
                timeout_s=2.0,
            )

        status = _wait_for_status(
            f"http://127.0.0.1:{backend_port}/api/status",
            predicate=lambda payload: int(payload.get("vis_rx_ok", 0)) >= 1,
            timeout_s=5.0,
        )
        assert int(status["vis_rx_ok"]) >= 1

        if run_with_fc:
            status_fc = _wait_for_status(
                f"http://127.0.0.1:{backend_port}/api/status",
                predicate=lambda payload: (
                    int(payload.get("tel_rx_ok", 0)) >= 1 and int(payload.get("cmd_tx_ok", 0)) >= 1
                ),
                timeout_s=5.0,
            )
            assert int(status_fc["tel_rx_ok"]) >= 1
            assert int(status_fc["cmd_tx_ok"]) >= 1
    finally:
        _terminate_process(backend_proc)
        if fc_proc is not None:
            _terminate_process(fc_proc)


def _start_fc_app_or_skip(repo_root: Path) -> subprocess.Popen[str]:
    for port, sock_type, label in (
        (9002, socket.SOCK_STREAM, "FC CMD TCP (9002)"),
        (9001, socket.SOCK_DGRAM, "FC TEL UDP (9001)"),
    ):
        if not _port_available("127.0.0.1", port, sock_type):
            pytest.skip(f"SKIP_NET: full-stack FC mode unavailable: cannot bind required {label}")

    fc_path = repo_root / "build" / "src" / "fc" / "fc_app"
    if not fc_path.exists():
        pytest.skip("SKIP_BIN: RUN_FULL_E2E_WITH_FC=1 set but build/src/fc/fc_app is not available")

    return subprocess.Popen(
        [str(fc_path)],
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _port_available(host: str, port: int, sock_type: int) -> bool:
    with socket.socket(socket.AF_INET, sock_type) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _reserve_port_or_skip(sock_type: int, label: str) -> int:
    try:
        with socket.socket(socket.AF_INET, sock_type) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])
    except OSError as exc:
        pytest.skip(f"SKIP_NET: {label} bind unavailable in test environment: {exc}")
    raise AssertionError("unreachable")


def _require_uvicorn_or_skip() -> None:
    if importlib.util.find_spec("uvicorn") is None:
        pytest.skip("SKIP_BIN: uvicorn is not installed for subprocess backend smoke")


def _wait_for_health(url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            payload = _http_get_json(url, timeout_s=0.5)
        except Exception:
            time.sleep(0.05)
            continue
        if payload.get("ok") is True:
            return
        time.sleep(0.05)
    raise AssertionError(f"backend health endpoint not ready within {timeout_s:.2f}s")


def _wait_for_status(
    url: str,
    predicate,
    timeout_s: float,
    step_s: float = 0.05,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        payload = _http_get_json(url, timeout_s=1.0)
        last_payload = payload
        if predicate(payload):
            return payload
        time.sleep(step_s)
    raise AssertionError(f"status condition not met within {timeout_s:.2f}s; last={last_payload}")


def _http_get_json(url: str, timeout_s: float) -> dict[str, Any]:
    req = request.Request(url, method="GET")
    with request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read()
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"expected JSON object from {url}, got {type(payload).__name__}")
    return payload


def _http_post_json(url: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = request.Request(
        url,
        data=raw,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"POST {url} failed with HTTP {exc.code}: {detail}") from exc
    result = json.loads(body.decode("utf-8"))
    if not isinstance(result, dict):
        raise AssertionError(f"expected JSON object from POST {url}, got {type(result).__name__}")
    return result


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)


def _env_flag(name: str) -> bool:
    raw = os.environ.get(name, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}
