// Pins the audit-#2 throttle ceiling fix (S0.9):
//
//   - kAltHoldMaxThrottle = 1800 (upper ceiling for hover + climb)
//   - alt-hold neutral pivot = hoverThrottle_ itself (no separate
//     constant — see FlightController.cpp's comment block; an earlier
//     draft introduced a kAltHoldNeutralThrottle constant per the
//     spec rename, then dropped it in round 2 as it was unreferenced).
//
// Pre-fix, a single 1500 µs constant served as both the alt-hold
// neutral AND the upper clamp on hoverThrottle_ AND the positive-delta
// ceiling in commandAltHoldDelta. Positive-delta climb from any
// hoverThrottle_ ≥ 1500 had zero headroom even though the FC's safe
// ceiling sits at DRONE_MAX (2000). The Python subprocess test
// test_alt_hold_positive_delta_* covers the broader scenarios; this
// file is the fast-ctest pinning of the exact API contracts so a
// future refactor that reverts the ceiling trips a sub-second test.

#include "FlightController.h"
#include "RcConstants.h"
#include "test_assert.h"

#include <cstdint>

namespace {

void test_set_hover_throttle_clamps_to_new_ceiling() {
    fc::FlightController controller;

    // Below MIN: clamped up to DRONE_MIN.
    controller.setHoverThrottle(500);
    TEST_ASSERT(controller.getHoverThrottle() == fc::rc::DRONE_MIN);

    // Exactly the new ceiling (1800): preserved.
    controller.setHoverThrottle(1800);
    TEST_ASSERT(controller.getHoverThrottle() == 1800);

    // Above the new ceiling: clamped down to 1800.
    controller.setHoverThrottle(2500);
    TEST_ASSERT(controller.getHoverThrottle() == 1800);

    // Calibrated typical value: passes through unchanged.
    controller.setHoverThrottle(1100);
    TEST_ASSERT(controller.getHoverThrottle() == 1100);

    // Exactly DRONE_MIN: accepted unchanged (boundary check —
    // a future refactor that writes `> DRONE_MIN` instead of
    // `>= DRONE_MIN` would silently break this).
    controller.setHoverThrottle(fc::rc::DRONE_MIN);
    TEST_ASSERT(controller.getHoverThrottle() == fc::rc::DRONE_MIN);

    // DRONE_MIN - 1: clamped up to DRONE_MIN.
    controller.setHoverThrottle(static_cast<std::uint16_t>(fc::rc::DRONE_MIN - 1));
    TEST_ASSERT(controller.getHoverThrottle() == fc::rc::DRONE_MIN);

    // Explicitly re-uncalibrate (sentinel): preserved as 0.
    controller.setHoverThrottle(0);
    TEST_ASSERT(controller.getHoverThrottle() == 0);
}

void test_alt_hold_full_positive_delta_reaches_new_ceiling() {
    // commandAltHoldDelta(1.0) at a realistic 1100 µs hover must climb
    // to the new 1800 ceiling. The pre-fix bug clamped this at 1500.
    fc::FlightController controller;
    controller.setHoverThrottle(1100);
    controller.commandAltHoldDelta(1.0);

    const std::uint16_t throttle = controller.getCurrentCommand().throttle;
    TEST_ASSERT(throttle == 1800);
}

void test_alt_hold_full_negative_delta_reaches_drone_min() {
    // Symmetry check on the downward range: full negative delta still
    // reaches DRONE_MIN. The S0.9 fix only widened the upward range.
    fc::FlightController controller;
    controller.setHoverThrottle(1100);
    controller.commandAltHoldDelta(-1.0);

    const std::uint16_t throttle = controller.getCurrentCommand().throttle;
    TEST_ASSERT(throttle == fc::rc::DRONE_MIN);
}

void test_alt_hold_neutral_returns_hover_throttle() {
    // deltaNorm=0 is the alt-hold neutral — must return exactly the
    // hoverThrottle_, regardless of where that sits within the ceiling.
    fc::FlightController controller;
    controller.setHoverThrottle(1100);
    controller.commandAltHoldDelta(0.0);
    TEST_ASSERT(controller.getCurrentCommand().throttle == 1100);

    controller.setHoverThrottle(1600);
    controller.commandAltHoldDelta(0.0);
    TEST_ASSERT(controller.getCurrentCommand().throttle == 1600);
}

void test_alt_hold_zero_headroom_at_ceiling() {
    // Edge case: hover already at the ceiling. Positive delta yields
    // exactly the ceiling — no overrun, no underrun.
    fc::FlightController controller;
    controller.setHoverThrottle(1800);
    controller.commandAltHoldDelta(1.0);
    TEST_ASSERT(controller.getCurrentCommand().throttle == 1800);

    // Negative delta from ceiling can still descend.
    controller.commandAltHoldDelta(-1.0);
    TEST_ASSERT(controller.getCurrentCommand().throttle == fc::rc::DRONE_MIN);
}

} // namespace

int main() {
    test_set_hover_throttle_clamps_to_new_ceiling();
    test_alt_hold_full_positive_delta_reaches_new_ceiling();
    test_alt_hold_full_negative_delta_reaches_drone_min();
    test_alt_hold_neutral_returns_hover_throttle();
    test_alt_hold_zero_headroom_at_ceiling();
    return 0;
}
