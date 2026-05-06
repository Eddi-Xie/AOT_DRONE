// Unit tests for the inline PWM-mapping helpers in src/fc/header/RcMath.h.
//
// These helpers convert controller setpoints to RC PWM us values that flow
// directly into the FC's BetaFlightCommand. A regression in any of them
// would change manual RC outputs in main.cpp and the upcoming MspRcSink
// driver (Sprint 0 S0.8) without breaking any of the existing FC mode tests
// in tests/test_flight_controller_modes.py — those tests check the
// FlightController state machine, not the wire-level PWM mapping.
//
// Kept dependency-free on purpose: no test framework. Uses the always-on
// TEST_ASSERT macro from tests/fc/test_assert.h instead of <cassert>'s
// assert(), because assert() is a no-op under -DNDEBUG (typical Release
// builds) and would let a broken helper silently report ctest success.
// Run via `ctest --test-dir build --output-on-failure` after a normal
// cmake build, or directly: `./build/tests/fc/test_rc_math`.

#include "RcConstants.h"
#include "RcMath.h"
#include "test_assert.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>

namespace {

constexpr std::uint16_t kMid = fc::rc::DRONE_MID;
constexpr std::uint16_t kMin = fc::rc::DRONE_MIN;
constexpr std::uint16_t kMax = fc::rc::DRONE_MAX;
constexpr double kAxisRange = fc::rc::kAxisRangeUs; // 300.0 us
constexpr double kMaxYaw = fc::rc::kMaxYawRateDps;  // 180.0 dps

void test_normalized_axis_to_pwm_centre_and_endpoints() {
    using fc::rc::normalized_axis_to_pwm;

    // Zero -> exact centre.
    TEST_ASSERT(normalized_axis_to_pwm(0.0) == kMid);

    // +1.0 -> centre + axis-range, *clamped* against [DRONE_MIN, DRONE_MAX].
    // The expected value below applies the same clamp the helper does, so
    // this test pins the documented contract regardless of how
    // kAxisRangeUs is tuned in future. With today's tuning
    // (kAxisRangeUs=300, mid=1500, max=2000) the clamp is a no-op
    // (1800 < 2000), but a future kAxisRangeUs > 500 would correctly
    // saturate at DRONE_MAX, and this test must still pass.
    const double pos_pwm = static_cast<double>(kMid) + kAxisRange;
    const std::uint16_t expected_pos = static_cast<std::uint16_t>(
        std::lround(std::clamp(pos_pwm, static_cast<double>(kMin), static_cast<double>(kMax))));
    TEST_ASSERT(normalized_axis_to_pwm(1.0) == expected_pos);

    // -1.0 -> symmetric, clamped against [DRONE_MIN, DRONE_MAX].
    const double neg_pwm = static_cast<double>(kMid) - kAxisRange;
    const std::uint16_t expected_neg = static_cast<std::uint16_t>(
        std::lround(std::clamp(neg_pwm, static_cast<double>(kMin), static_cast<double>(kMax))));
    TEST_ASSERT(normalized_axis_to_pwm(-1.0) == expected_neg);
}

void test_normalized_axis_to_pwm_saturates_outside_unit() {
    using fc::rc::normalized_axis_to_pwm;

    // Anything beyond [-1, 1] saturates to the same value as the boundary.
    TEST_ASSERT(normalized_axis_to_pwm(2.5) == normalized_axis_to_pwm(1.0));
    TEST_ASSERT(normalized_axis_to_pwm(-9999.0) == normalized_axis_to_pwm(-1.0));
}

void test_normalized_axis_to_pwm_non_finite_returns_mid() {
    using fc::rc::normalized_axis_to_pwm;
    const double nan_v = std::numeric_limits<double>::quiet_NaN();
    const double inf_v = std::numeric_limits<double>::infinity();

    TEST_ASSERT(normalized_axis_to_pwm(nan_v) == kMid);
    TEST_ASSERT(normalized_axis_to_pwm(inf_v) == kMid);
    TEST_ASSERT(normalized_axis_to_pwm(-inf_v) == kMid);
}

void test_yaw_rate_to_pwm_zero_and_saturation() {
    using fc::rc::normalized_axis_to_pwm;
    using fc::rc::yaw_rate_to_pwm;

    // 0 dps -> centre.
    TEST_ASSERT(yaw_rate_to_pwm(0.0) == kMid);

    // +max -> equivalent to normalized_axis_to_pwm(1.0).
    TEST_ASSERT(yaw_rate_to_pwm(kMaxYaw) == normalized_axis_to_pwm(1.0));
    TEST_ASSERT(yaw_rate_to_pwm(kMaxYaw + 1000.0) == normalized_axis_to_pwm(1.0));

    // -max -> equivalent to normalized_axis_to_pwm(-1.0).
    TEST_ASSERT(yaw_rate_to_pwm(-kMaxYaw) == normalized_axis_to_pwm(-1.0));
}

void test_yaw_rate_to_pwm_non_finite_returns_mid() {
    using fc::rc::yaw_rate_to_pwm;
    const double nan_v = std::numeric_limits<double>::quiet_NaN();

    TEST_ASSERT(yaw_rate_to_pwm(nan_v) == kMid);
    TEST_ASSERT(yaw_rate_to_pwm(std::numeric_limits<double>::infinity()) == kMid);
}

void test_throttle_to_pwm_normalised_range() {
    using fc::rc::throttle_to_pwm;

    // 0.0 -> DRONE_MIN.
    TEST_ASSERT(throttle_to_pwm(0.0) == kMin);
    // 1.0 -> DRONE_MAX.
    TEST_ASSERT(throttle_to_pwm(1.0) == kMax);
    // 0.5 -> midpoint of MIN..MAX.
    const std::uint16_t expected_mid =
        static_cast<std::uint16_t>(std::lround(static_cast<double>(kMin) + 0.5 * (kMax - kMin)));
    TEST_ASSERT(throttle_to_pwm(0.5) == expected_mid);
}

void test_throttle_to_pwm_raw_pwm_passthrough() {
    using fc::rc::throttle_to_pwm;

    // Values outside [0,1] are interpreted as raw PWM us already and
    // clamped into [DRONE_MIN, DRONE_MAX].
    TEST_ASSERT(throttle_to_pwm(1500.0) == kMid);
    TEST_ASSERT(throttle_to_pwm(static_cast<double>(kMin) - 100.0) == kMin);
    TEST_ASSERT(throttle_to_pwm(static_cast<double>(kMax) + 100.0) == kMax);
}

void test_throttle_to_pwm_non_finite_returns_min() {
    using fc::rc::throttle_to_pwm;
    const double nan_v = std::numeric_limits<double>::quiet_NaN();

    // Motors-off fallback for non-finite throttle is DRONE_MIN.
    TEST_ASSERT(throttle_to_pwm(nan_v) == kMin);
    TEST_ASSERT(throttle_to_pwm(std::numeric_limits<double>::infinity()) == kMin);
    TEST_ASSERT(throttle_to_pwm(-std::numeric_limits<double>::infinity()) == kMin);
}

} // namespace

int main() {
    test_normalized_axis_to_pwm_centre_and_endpoints();
    test_normalized_axis_to_pwm_saturates_outside_unit();
    test_normalized_axis_to_pwm_non_finite_returns_mid();
    test_yaw_rate_to_pwm_zero_and_saturation();
    test_yaw_rate_to_pwm_non_finite_returns_mid();
    test_throttle_to_pwm_normalised_range();
    test_throttle_to_pwm_raw_pwm_passthrough();
    test_throttle_to_pwm_non_finite_returns_min();
    std::printf("test_rc_math: 8 cases passed\n");
    return 0;
}
