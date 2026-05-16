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
    # S0.9 added per-tick uncalibrated-hover guards to runTakeoffMode and
    # runFollowTargetLogic; without an explicit setHoverThrottle the
    # default-0 sentinel routes both to LandSafely. This test exercises
    # the calibrated-hover happy path, so set a realistic value first.
    source = r"""
#include "FlightController.h"

int main() {
    fc::FlightController controller;
    controller.setHoverThrottle(1100);
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
    # Pre-S0.9 the alt-hold ceiling was 1500 µs — the SAME value used as
    # both the alt-hold neutral and the upper clamp on setHoverThrottle.
    # Positive delta at any hoverThrottle_ >= 1500 had zero headroom even
    # though the FC could safely accept up to DRONE_MAX. The old version
    # of this test PINNED that broken behavior with `neutral > 1500 ||
    # climb > 1500` returning failure.
    #
    # Post-S0.9 (audit #2 fix): kAltHoldMaxThrottle = 1800 is the upper
    # ceiling, and the alt-hold neutral pivot is hoverThrottle_ itself
    # (no separate kAltHoldNeutralThrottle constant — see
    # FlightController.cpp's anonymous-namespace comment block for why).
    # setHoverThrottle clamps to [DRONE_MIN, 1800] so calibrated hovers
    # above 1500 are now accepted, and commandAltHoldDelta(1.0) actually
    # climbs from there.
    #
    # New assertions:
    #   (a) climb >= neutral — positive delta never reduces throttle
    #   (b) climb <= DRONE_MAX — safety bound
    #   (c) with a realistic 1100 µs hover, commandAltHoldDelta(1.0)
    #       must reach the new ceiling (1800), proving the fix.
    source = r"""
#include "FlightController.h"

#include <cstdio>

int main() {
    // Case 1: hover set above old broken ceiling (1500 → clamped to new
    // ceiling 1800). climb must not reduce throttle.
    {
        fc::FlightController controller;
        controller.setHoverThrottle(1800);

        controller.commandAltHoldDelta(0.0);
        const std::uint16_t neutral = controller.getCurrentCommand().throttle;
        controller.commandAltHoldDelta(1.0);
        const std::uint16_t climb = controller.getCurrentCommand().throttle;

        if (climb < neutral) {
            std::fprintf(stderr, "case 1: climb=%u < neutral=%u\n",
                         static_cast<unsigned>(climb), static_cast<unsigned>(neutral));
            return 1;
        }
        if (climb > fc::rc::DRONE_MAX) {
            std::fprintf(stderr, "case 1: climb=%u > DRONE_MAX=%u\n",
                         static_cast<unsigned>(climb),
                         static_cast<unsigned>(fc::rc::DRONE_MAX));
            return 2;
        }
    }

    // Case 2: realistic calibrated hover at 1100 µs. commandAltHoldDelta(1.0)
    // should drive throttle to the new ceiling (1800). Pre-S0.9 this was
    // clamped at 1500, leaving the FC unable to climb past the old
    // neutral.
    {
        fc::FlightController controller;
        controller.setHoverThrottle(1100);

        controller.commandAltHoldDelta(1.0);
        const std::uint16_t climb_from_1100 = controller.getCurrentCommand().throttle;

        if (climb_from_1100 != 1800) {
            std::fprintf(stderr,
                         "case 2: expected climb from 1100 to reach 1800 (new ceiling), got %u\n",
                         static_cast<unsigned>(climb_from_1100));
            return 3;
        }
    }

    return 0;
}
"""
    run_result = _compile_and_run_cpp(tmp_path, source)
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout


def test_entering_manual_mode_clears_stale_tracking_axes(tmp_path: Path) -> None:
    # Per-tick uncalibrated-hover guard (S0.9) routes Tracking → LandSafely
    # when hoverThrottle_ == 0. This test exercises the Tracking → Manual
    # transition path; set a realistic hover so Tracking actually runs.
    source = r"""
#include "FlightController.h"

int main() {
    fc::FlightController controller;
    controller.setHoverThrottle(1100);
    controller.setArm(true);
    controller.setControlMode(fc::ControlMode::Tracking);

    fc::TrackingMessage msg{};
    msg.state = fc::TrackingState::Tracking;
    msg.target_x = 1.0;
    msg.target_y = 0.0;
    msg.bound_w = 0.1;
    msg.bound_h = 0.05;
    msg.confidence = 1.0;
    msg.timestamp_s = 1.0;
    controller.updateTracking(msg);
    controller.updateTimeStep(0.02);

    const fc::BetaFlightCommand tracking_cmd = controller.getCurrentCommand();
    if (tracking_cmd.pitch == fc::rc::DRONE_MID || tracking_cmd.yaw == fc::rc::DRONE_MID) {
        return 1;
    }

    controller.setControlMode(fc::ControlMode::Manual);
    const fc::BetaFlightCommand manual_cmd = controller.updateTimeStep(0.02);
    if (manual_cmd.pitch != fc::rc::DRONE_MID) {
        return 2;
    }
    if (manual_cmd.yaw != fc::rc::DRONE_MID) {
        return 3;
    }

    return 0;
}
"""
    run_result = _compile_and_run_cpp(tmp_path, source)
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout
