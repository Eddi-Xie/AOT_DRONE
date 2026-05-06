#pragma once

#include "RcConstants.h"

#include <algorithm>
#include <cmath>
#include <cstdint>

// Shared RC-channel math: helpers that map controller setpoints
// (normalised in [-1, 1] or 0..1) to Betaflight RC PWM µs values.
//
// Note: FlightController also defines local kPitchRangeUs / kYawRangeUs
// (currently 300.0). Audit task P2.9 will consolidate those into a single
// configurable RcStickRangeUs alongside kAxisRangeUs below.
namespace fc::rc {

// PWM µs deflection for a unit-magnitude (±1.0) normalised setpoint on
// pitch / yaw axes used by the CMD-frame setpoint path in main.cpp.
inline constexpr double kAxisRangeUs = 300.0;

// Saturation for yaw rate. Anything beyond is clamped before mapping to PWM.
// Must be kept consistent with the Betaflight rate profile loaded on the FC
// (see ADR-005 / docs/development_plan.md S0.4 for the boot-time rate-tuning
// query that will assert this).
inline constexpr double kMaxYawRateDps = 180.0;

// Map a normalised axis setpoint in [-1, 1] to a PWM µs value centred at
// DRONE_MID with ±kAxisRangeUs deflection. Non-finite inputs return MID.
inline std::uint16_t normalized_axis_to_pwm(double normalized) {
    if (!std::isfinite(normalized)) {
        return DRONE_MID;
    }
    const double clamped = std::clamp(normalized, -1.0, 1.0);
    const double pwm = static_cast<double>(DRONE_MID) + clamped * kAxisRangeUs;
    return static_cast<std::uint16_t>(std::lround(
        std::clamp(pwm, static_cast<double>(DRONE_MIN), static_cast<double>(DRONE_MAX))));
}

// Map a yaw-rate setpoint in degrees-per-second to a PWM µs value.
inline std::uint16_t yaw_rate_to_pwm(double yaw_rate_dps) {
    if (!std::isfinite(yaw_rate_dps)) {
        return DRONE_MID;
    }
    const double limited = std::clamp(yaw_rate_dps, -kMaxYawRateDps, kMaxYawRateDps);
    return normalized_axis_to_pwm(limited / kMaxYawRateDps);
}

// Map a throttle setpoint to a PWM µs value.
//   - throttle ∈ [0, 1] : interpreted as normalised, mapped to [DRONE_MIN, DRONE_MAX]
//   - throttle outside [0, 1] : interpreted as a raw PWM µs already, then clamped
//   - non-finite : DRONE_MIN (motors-off fallback)
inline std::uint16_t throttle_to_pwm(double throttle) {
    if (!std::isfinite(throttle)) {
        return DRONE_MIN;
    }
    if (throttle >= 0.0 && throttle <= 1.0) {
        const double pwm =
            static_cast<double>(DRONE_MIN) + throttle * static_cast<double>(DRONE_MAX - DRONE_MIN);
        return static_cast<std::uint16_t>(std::lround(
            std::clamp(pwm, static_cast<double>(DRONE_MIN), static_cast<double>(DRONE_MAX))));
    }
    return static_cast<std::uint16_t>(std::lround(
        std::clamp(throttle, static_cast<double>(DRONE_MIN), static_cast<double>(DRONE_MAX))));
}

} // namespace fc::rc
