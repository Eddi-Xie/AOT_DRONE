#pragma once

#include <algorithm>
#include <cmath>

namespace fc {

// NaN-safe clamp into [-1, 1] for normalised image-plane / setpoint coords.
// Non-finite inputs are treated as 0.0 (the safest fallback for a control
// path that would otherwise propagate the non-finite into a PWM channel).
inline double clamp_target_coord(double value) {
    if (!std::isfinite(value)) {
        return 0.0;
    }
    return std::clamp(value, -1.0, 1.0);
}

// NaN-safe clamp into [0, 1] for normalised box dimensions / confidence.
inline double clamp_unit(double value) {
    if (!std::isfinite(value)) {
        return 0.0;
    }
    return std::clamp(value, 0.0, 1.0);
}

} // namespace fc
