// Unit tests for the inline NaN-safe clamp helpers in src/fc/header/Clamp.h.
//
// clamp_target_coord and clamp_unit are now used on:
//  - the tracking-ingest path in FlightController.cpp (every CMD-frame
//    tracking update is filtered through them), and
//  - the telemetry-serialization path in main.cpp (every TEL JSON
//    emission uses them to bound the published values).
//
// A regression in either function would propagate non-finite or
// out-of-range values into the controller's state machine or out onto the
// wire, with no other test in the suite catching it.
//
// Kept dependency-free on purpose: no test framework, just <cassert>.
// Run via `ctest --test-dir build --output-on-failure` after a normal
// cmake build, or directly: `./build/tests/fc/test_clamp`.

#include "Clamp.h"

#include <cassert>
#include <cstdio>
#include <limits>

namespace {

void test_clamp_target_coord_inside_range_passthrough() {
    using fc::clamp_target_coord;

    // Values inside [-1, 1] pass through unchanged.
    assert(clamp_target_coord(0.0) == 0.0);
    assert(clamp_target_coord(0.5) == 0.5);
    assert(clamp_target_coord(-0.5) == -0.5);
    assert(clamp_target_coord(1.0) == 1.0);
    assert(clamp_target_coord(-1.0) == -1.0);
}

void test_clamp_target_coord_saturates_outside_range() {
    using fc::clamp_target_coord;

    // Values outside [-1, 1] saturate to the boundary.
    assert(clamp_target_coord(1.5) == 1.0);
    assert(clamp_target_coord(-1.5) == -1.0);
    assert(clamp_target_coord(1e9) == 1.0);
    assert(clamp_target_coord(-1e9) == -1.0);
}

void test_clamp_target_coord_non_finite_returns_zero() {
    using fc::clamp_target_coord;
    const double nan_v = std::numeric_limits<double>::quiet_NaN();
    const double inf_v = std::numeric_limits<double>::infinity();

    // NaN and ±Inf must collapse to 0.0 (safe fallback for the control
    // path that would otherwise propagate them into a PWM channel).
    assert(clamp_target_coord(nan_v) == 0.0);
    assert(clamp_target_coord(inf_v) == 0.0);
    assert(clamp_target_coord(-inf_v) == 0.0);
}

void test_clamp_unit_inside_range_passthrough() {
    using fc::clamp_unit;

    // Values inside [0, 1] pass through unchanged.
    assert(clamp_unit(0.0) == 0.0);
    assert(clamp_unit(0.25) == 0.25);
    assert(clamp_unit(0.5) == 0.5);
    assert(clamp_unit(0.75) == 0.75);
    assert(clamp_unit(1.0) == 1.0);
}

void test_clamp_unit_saturates_outside_range() {
    using fc::clamp_unit;

    // Values below 0 saturate to 0; above 1 saturate to 1.
    assert(clamp_unit(-0.1) == 0.0);
    assert(clamp_unit(-1e9) == 0.0);
    assert(clamp_unit(1.5) == 1.0);
    assert(clamp_unit(1e9) == 1.0);
}

void test_clamp_unit_non_finite_returns_zero() {
    using fc::clamp_unit;
    const double nan_v = std::numeric_limits<double>::quiet_NaN();
    const double inf_v = std::numeric_limits<double>::infinity();

    // NaN and ±Inf must collapse to 0.0.
    assert(clamp_unit(nan_v) == 0.0);
    assert(clamp_unit(inf_v) == 0.0);
    assert(clamp_unit(-inf_v) == 0.0);
}

} // namespace

int main() {
    test_clamp_target_coord_inside_range_passthrough();
    test_clamp_target_coord_saturates_outside_range();
    test_clamp_target_coord_non_finite_returns_zero();
    test_clamp_unit_inside_range_passthrough();
    test_clamp_unit_saturates_outside_range();
    test_clamp_unit_non_finite_returns_zero();
    std::printf("test_clamp: 6 cases passed\n");
    return 0;
}
