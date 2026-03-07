import shutil
import subprocess
from pathlib import Path

import pytest


def _compile_and_run_cpp(tmp_path: Path, source_text: str) -> subprocess.CompletedProcess[str]:
    cxx = shutil.which("c++")
    if cxx is None:
        pytest.skip("c++ compiler not available")

    repo_root = Path(__file__).resolve().parents[1]
    header_dir = repo_root / "src" / "fc" / "header"
    fc_impl = repo_root / "src" / "fc" / "implementation" / "FlightController.cpp"

    source_file = tmp_path / "flight_controller_test.cpp"
    binary_file = tmp_path / "flight_controller_test_bin"
    source_file.write_text(source_text, encoding="utf-8")

    compile_cmd = [
        cxx,
        "-std=c++17",
        "-I",
        str(header_dir),
        str(fc_impl),
        str(source_file),
        "-o",
        str(binary_file),
    ]
    compile_result = subprocess.run(compile_cmd, capture_output=True, text=True, check=False)
    if compile_result.returncode != 0:
        pytest.fail(
            "C++ compile failed:\n"
            f"stdout:\n{compile_result.stdout}\n"
            f"stderr:\n{compile_result.stderr}"
        )

    run_result = subprocess.run([str(binary_file)], capture_output=True, text=True, check=False)
    return run_result


def test_takeoff_auto_transitions_to_tracking(tmp_path: Path) -> None:
    source = r"""
#include "FlightController.h"

int main() {
    fc::FlightController controller;
    controller.setControlMode(fc::ControlMode::Takeoff);

    for (int i = 0; i < 400; ++i) {
        controller.updateTimeStep(0.02);
        if (controller.getControlMode() == fc::ControlMode::Tracking) {
            return 0;
        }
    }

    return 1;
}
"""
    run_result = _compile_and_run_cpp(tmp_path, source)
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout


def test_landsafely_ramps_and_disarms(tmp_path: Path) -> None:
    source = r"""
#include "FlightController.h"

#include <cstdint>

int main() {
    {
        fc::FlightController disarmed_controller;
        disarmed_controller.setControlMode(fc::ControlMode::LandSafely);
        const fc::BetaFlightCommand out = disarmed_controller.updateTimeStep(0.02);
        if (out.throttle != fc::rc::DRONE_MIN) {
            return 1;
        }
    }

    fc::FlightController controller;
    fc::BetaFlightCommand seeded = controller.getCurrentCommand();
    seeded.aux1 = fc::rc::DRONE_AUX_MAX;
    seeded.throttle = 1400;
    controller.setManualSetpoints(seeded);
    controller.setControlMode(fc::ControlMode::LandSafely);

    std::uint16_t prev_throttle = controller.getCurrentCommand().throttle;
    bool non_increasing = true;
    bool saw_ramp = false;
    fc::BetaFlightCommand out = controller.getCurrentCommand();

    for (int i = 0; i < 320; ++i) {
        out = controller.updateTimeStep(0.02);
        if (out.throttle > prev_throttle) {
            non_increasing = false;
        }
        if (out.throttle < prev_throttle) {
            saw_ramp = true;
        }
        prev_throttle = out.throttle;
    }

    if (!non_increasing || !saw_ramp) {
        return 2;
    }
    if (out.aux1 != fc::rc::DRONE_AUX_MIN) {
        return 3;
    }
    if (out.throttle != fc::rc::DRONE_MIN) {
        return 4;
    }

    return 0;
}
"""
    run_result = _compile_and_run_cpp(tmp_path, source)
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout


def test_manual_mode_reports_searching_state_not_tracking(tmp_path: Path) -> None:
    source = r"""
#include "FlightController.h"

int main() {
    fc::FlightController controller;
    controller.setControlMode(fc::ControlMode::Manual);

    fc::TrackingMessage msg{};
    msg.state = fc::TrackingState::Tracking;
    msg.target_x = 0.1;
    msg.target_y = 0.0;
    msg.bound_w = 0.2;
    msg.bound_h = 0.3;
    msg.confidence = 0.9;
    msg.timestamp_s = 1.0;
    controller.updateTracking(msg);

    controller.updateTimeStep(0.02);
    const fc::TelemetryData& telemetry = controller.getTelemetryData();
    if (telemetry.tracking_state != fc::TrackingState::Searching) {
        return 1;
    }
    return 0;
}
"""
    run_result = _compile_and_run_cpp(tmp_path, source)
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout


def test_alt_hold_positive_delta_never_reduces_throttle_at_high_hover_setting(
    tmp_path: Path,
) -> None:
    source = r"""
#include "FlightController.h"

int main() {
    fc::FlightController controller;
    controller.setHoverThrottle(1800);

    controller.commandAltHoldDelta(0.0);
    const std::uint16_t neutral = controller.getCurrentCommand().throttle;

    controller.commandAltHoldDelta(1.0);
    const std::uint16_t climb = controller.getCurrentCommand().throttle;

    if (climb < neutral) {
        return 1;
    }
    if (neutral > 1500 || climb > 1500) {
        return 2;
    }
    return 0;
}
"""
    run_result = _compile_and_run_cpp(tmp_path, source)
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout
