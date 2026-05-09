"""Unit tests for the HIL Python harness scripts (S0.7).

Covers the pure-function pieces of fake_betaflight_listener.py and
replay_mission.py — frame decoding, header validation, in-band check,
exit code on missing file. The end-to-end UDP roundtrip with the FC
binary is exercised manually via docs/hil.md and the CI-friendly
``runall.sh`` smoke (added later).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "dev"


def _import_script(name: str):
    """Import a script-style module without executing its argparse / main().

    Registers the module in sys.modules BEFORE exec — `@dataclass` reads
    `sys.modules.get(cls.__module__).__dict__` during ClassVar resolution,
    which crashes if the module isn't there yet.
    """
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_decode_frame_accepts_well_formed_payload() -> None:
    listener = _import_script("fake_betaflight_listener")
    payload = json.dumps(
        {
            "type": "RC",
            "seq": 7,
            "timestamp_s": 0.020,
            "channels": [1500, 1500, 1500, 1100, 1000, 2000, 1000, 1000],
        }
    ).encode("utf-8")
    out = listener.decode_frame(payload)
    assert out is not None
    assert out["seq"] == 7
    assert out["channels"] == [1500, 1500, 1500, 1100, 1000, 2000, 1000, 1000]


def test_decode_frame_rejects_wrong_type() -> None:
    listener = _import_script("fake_betaflight_listener")
    bad = json.dumps({"type": "TEL", "seq": 1, "timestamp_s": 0.0, "channels": [0] * 8}).encode()
    assert listener.decode_frame(bad) is None


def test_decode_frame_rejects_short_channels() -> None:
    listener = _import_script("fake_betaflight_listener")
    bad = json.dumps({"type": "RC", "seq": 1, "timestamp_s": 0.0, "channels": [1500] * 7}).encode()
    assert listener.decode_frame(bad) is None


def test_decode_frame_rejects_non_integer_channel() -> None:
    listener = _import_script("fake_betaflight_listener")
    bad = json.dumps(
        {"type": "RC", "seq": 1, "timestamp_s": 0.0, "channels": [1500.5] * 8}
    ).encode()
    assert listener.decode_frame(bad) is None


def test_decode_frame_rejects_malformed_json() -> None:
    listener = _import_script("fake_betaflight_listener")
    assert listener.decode_frame(b"{not valid json") is None
    assert listener.decode_frame(b"\xff\xfe") is None  # invalid UTF-8


def test_is_in_band_boundaries() -> None:
    listener = _import_script("fake_betaflight_listener")
    assert listener.is_in_band(1000)
    assert listener.is_in_band(2000)
    assert not listener.is_in_band(999)
    assert not listener.is_in_band(2001)
    assert not listener.is_in_band(0)


def test_replay_mission_succeeds_on_valid_csv(tmp_path: Path) -> None:
    csv = tmp_path / "sink.csv"
    csv.write_text(
        "timestamp_s,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4\n"
        "0.020000,1500,1500,1500,1100,1000,2000,1000,1000\n"
        "0.040000,1500,1500,1500,1234,1000,2000,1000,1000\n"
    )
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "replay_mission.py"), str(csv)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "rows=2" in proc.stdout
    assert "throttle: min=1100 max=1234" in proc.stdout


def test_replay_mission_fails_on_missing_file(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "replay_mission.py"), str(tmp_path / "nope.csv")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2


def test_replay_mission_fails_on_header_mismatch(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    csv.write_text("ts,a,b\n1,2,3\n")
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "replay_mission.py"), str(csv)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "header mismatch" in proc.stderr


def test_replay_mission_check_band_flags_out_of_range(tmp_path: Path) -> None:
    csv = tmp_path / "oob.csv"
    csv.write_text(
        "timestamp_s,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4\n"
        "0.0,1500,1500,1500,2500,1000,2000,1000,1000\n"
    )
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "replay_mission.py"), "--check-band", str(csv)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "out-of-band samples" in proc.stderr
