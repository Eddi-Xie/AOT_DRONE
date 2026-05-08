"""xfail regression test for the Manual+arm:true ordering bug.

main.cpp's CMD application path applies setControlMode BEFORE setArm:

    if (to_control_mode(cmd.desired_mode, desired_mode)) {
        flight_controller.setControlMode(desired_mode);   // step 1
    }
    if (cmd.has_arm) {
        flight_controller.setArm(cmd.arm);                // step 2
    }

When a single CMD frame combines `{desired_mode=Manual, arm:true}`,
setControlMode(Manual) inside FlightController.cpp does:

    currentCommand_.throttle = is_armed(currentCommand_) ? hoverThrottle_
                                                        : rc::DRONE_MIN;

`is_armed()` reads aux1 — which step 2 hasn't updated yet — so it returns
the OLD armed-state (typically false on the first CMD), and the throttle
preset becomes `rc::DRONE_MIN` instead of `hoverThrottle_`. Subsequent
manual setpoints can override this, but if the operator's intent was
"enter Manual already armed at hover", the drone enters Manual at
DRONE_MIN until something else writes throttle.

The fix is the deferred S0.7 work (per ADR-004 the change requires HIL
replay before merge): either swap the application order in main.cpp, or
move the throttle preset out of setControlMode and recompute after the
post-update is_armed guard at the end of updateTimeStep.

This test pins the EXPECTED post-fix behaviour: throttle == hoverThrottle_
after a Manual+arm:true sequence. Marked xfail(strict=True) so:
  - With the bug present (today), the test produces an XFAIL — green.
  - When the S0.7 fix lands and the assertion starts passing, strict=True
    flips XPASS to a hard FAIL, which prompts whoever lands the fix to
    remove the xfail marker. That's the signal that the bug is closed.

Reference: docs/development_plan.md S0.4 ('Manual-mode throttle preset
reads stale aux1' deferred row) and ADR-004 in docs/decisions.md.
"""

from __future__ import annotations

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

    source_file = tmp_path / "manual_arm_test.cpp"
    binary_file = tmp_path / "manual_arm_test_bin"
    source_file.write_text(source_text, encoding="utf-8")

    compile_result = subprocess.run(
        [
            cxx,
            "-std=c++17",
            "-I",
            str(header_dir),
            str(fc_impl),
            str(source_file),
            "-o",
            str(binary_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if compile_result.returncode != 0:
        pytest.fail(
            "C++ compile failed:\n"
            f"stdout:\n{compile_result.stdout}\n"
            f"stderr:\n{compile_result.stderr}"
        )

    return subprocess.run([str(binary_file)], capture_output=True, text=True, check=False)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Manual+arm:true ordering fix deferred to S0.7 — needs HIL replay "
        "per ADR-004. When the fix lands this test will XPASS and strict=True "
        "will flip it to a hard FAIL, prompting removal of the xfail marker."
    ),
)
def test_manual_plus_arm_true_yields_hover_throttle(tmp_path: Path) -> None:
    # Reproduces main.cpp's CMD application order: setControlMode(Manual)
    # FIRST, setArm(true) SECOND. The test asserts the post-fix expected
    # behaviour: throttle == hoverThrottle_ default (1100).
    source = r"""
#include "FlightController.h"

#include <cstdint>
#include <cstdio>

int main() {
    fc::FlightController controller;

    // Mimic main.cpp's per-CMD application order on a CMD that combines
    // desired_mode=Manual with arm:true. The bug is that setControlMode
    // reads is_armed(currentCommand_) before setArm has updated aux1.
    controller.setControlMode(fc::ControlMode::Manual);
    controller.setArm(true);

    const fc::BetaFlightCommand out = controller.getCurrentCommand();

    // Post-fix expectation: Manual entry should produce hoverThrottle_
    // when armed by the same CMD. Today's behaviour: throttle stays at
    // rc::DRONE_MIN because is_armed() saw the pre-setArm aux1.
    constexpr std::uint16_t kHoverThrottleDefault = 1100;
    if (out.throttle != kHoverThrottleDefault) {
        std::fprintf(stderr,
                     "Expected throttle=%u (hoverThrottle_), got %u (likely DRONE_MIN=%u)\n",
                     kHoverThrottleDefault,
                     static_cast<unsigned>(out.throttle),
                     static_cast<unsigned>(fc::rc::DRONE_MIN));
        return 1;
    }
    return 0;
}
"""
    run_result = _compile_and_run_cpp(tmp_path, source)
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout
