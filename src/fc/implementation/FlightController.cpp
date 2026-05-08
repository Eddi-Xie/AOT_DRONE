#include "FlightController.h"

#include "Clamp.h"
#include "RcMath.h"

#include <algorithm>
#include <cmath>

namespace {

constexpr double kDisarmTimeS = 5.0;
constexpr double kLandRampRateUsPerS = 50.0;
constexpr std::uint16_t kMinLandThrottle = fc::rc::DRONE_MIN + 30;
constexpr double kMaxControlDtS = 0.2;
constexpr double kPitchRangeUs = 300.0;
constexpr double kYawRangeUs = 300.0;
constexpr std::uint16_t kAltHoldMaxThrottle = 1500;

using fc::clamp_target_coord;
using fc::clamp_unit;
using fc::rc::kMaxYawRateDps;

// aux1 is the BetaFlight ARM channel; >midpoint means armed, ≤midpoint means
// disarmed. Treating mid as disarmed is intentional (matches BetaFlight
// CLI defaults) so the ambiguous edge resolves to the safer state.
// Only setArm() should write aux1; ad-hoc writes elsewhere will silently
// flip armed-ness behind setControlMode()'s back. Audit C-13.
bool is_armed(const fc::BetaFlightCommand& cmd) {
    const std::uint16_t threshold =
        static_cast<std::uint16_t>((static_cast<unsigned>(fc::rc::DRONE_AUX_MIN) +
                                    static_cast<unsigned>(fc::rc::DRONE_AUX_MAX)) /
                                   2u);
    return cmd.aux1 > threshold;
}

} // namespace

namespace fc {

FlightController::FlightController()
    : lastTrackingMsg_{TrackingState::Searching, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0} {
    telemetryData_.control_mode = controlMode_;
    telemetryData_.tracking_state = TrackingState::Searching;
    telemetryData_.last_tracking_msg = lastTrackingMsg_;

    currentCommand_.roll = rc::DRONE_MID;
    currentCommand_.pitch = rc::DRONE_MID;
    currentCommand_.yaw = rc::DRONE_MID;
    currentCommand_.throttle = rc::DRONE_MIN;
    currentCommand_.aux1 = rc::DRONE_AUX_MIN;
    currentCommand_.aux2 = rc::DRONE_AUX_MAX;
    currentCommand_.aux3 = rc::DRONE_AUX_MIN;
    currentCommand_.aux4 = rc::DRONE_AUX_MIN;

    clampCommandChannels();
}

void FlightController::setControlMode(ControlMode mode) {
    if (mode == controlMode_) {
        telemetryData_.control_mode = mode;
        return;
    }

    controlMode_ = mode;
    telemetryData_.control_mode = mode;

    if (mode == ControlMode::Manual) {
        // Prevent stale tracking outputs from carrying into manual mode when no setpoints are
        // provided.
        currentCommand_.roll = rc::DRONE_MID;
        currentCommand_.pitch = rc::DRONE_MID;
        currentCommand_.yaw = rc::DRONE_MID;
        currentCommand_.throttle = is_armed(currentCommand_) ? hoverThrottle_ : rc::DRONE_MIN;
        setAltHold(false);
    }

    landSafelyInitialized_ = false;
    landTimer_s_ = 0.0;
    takeoffInitialized_ = false;
    takeoffTimer_s_ = 0.0;
}

void FlightController::setHoverThrottle(std::uint16_t hoverThrottle) {
    hoverThrottle_ = std::clamp(hoverThrottle, rc::DRONE_MIN, kAltHoldMaxThrottle);
}

void FlightController::updateTracking(const TrackingMessage& msg) {
    lastTrackingMsg_.state = msg.state;
    lastTrackingMsg_.target_x = clamp_target_coord(msg.target_x);
    lastTrackingMsg_.target_y = clamp_target_coord(msg.target_y);
    lastTrackingMsg_.bound_w = clamp_unit(msg.bound_w);
    lastTrackingMsg_.bound_h = clamp_unit(msg.bound_h);
    lastTrackingMsg_.confidence = clamp_unit(msg.confidence);
    lastTrackingMsg_.timestamp_s = msg.timestamp_s;

    telemetryData_.last_tracking_msg = lastTrackingMsg_;
    lastTrackingUpdateTime_s_ = telemetryData_.timestamp_s;
}

void FlightController::setDistances(double front_m, double back_m, double bottom_m) {
    telemetryData_.distFront_m = std::isfinite(front_m) ? front_m : 0.0;
    telemetryData_.distBack_m = std::isfinite(back_m) ? back_m : 0.0;
    telemetryData_.distBottom_m = std::isfinite(bottom_m) ? bottom_m : 0.0;
}

void FlightController::setOrientationDeg(double roll_deg, double pitch_deg, double yaw_deg) {
    telemetryData_.orientation_deg = {
        std::isfinite(roll_deg) ? roll_deg : 0.0,
        std::isfinite(pitch_deg) ? pitch_deg : 0.0,
        std::isfinite(yaw_deg) ? yaw_deg : 0.0,
    };
}

void FlightController::setAltitudeMeters(double altitude_m) {
    telemetryData_.position_m[2] = std::isfinite(altitude_m) ? altitude_m : 0.0;
}

void FlightController::setManualSetpoints(const BetaFlightCommand& cmd) {
    currentCommand_ = cmd;
    clampCommandChannels();
}

bool FlightController::isTargetValid() const {
    const bool stale = lastTrackingUpdateTime_s_ < 0.0 ||
                       (telemetryData_.timestamp_s - lastTrackingUpdateTime_s_) >
                           trackingConfig_.trackingTimeout_s;

    const bool not_tracking = (lastTrackingMsg_.state != TrackingState::Tracking);
    const bool low_confidence = (lastTrackingMsg_.confidence < trackingConfig_.minConfidence);

    return !(stale || not_tracking || low_confidence);
}

void FlightController::runTakeoffMode(double deltaTime_s) {
    if (!takeoffInitialized_) {
        takeoffInitialized_ = true;
        takeoffTimer_s_ = 0.0;
    }

    takeoffTimer_s_ += deltaTime_s;

    telemetryData_.tracking_state = TrackingState::Searching;

    setArm(true);
    setAngleMode(true);
    setAltHold(true);

    currentCommand_.roll = rc::DRONE_MID;
    currentCommand_.pitch = rc::DRONE_MID;
    currentCommand_.yaw = rc::DRONE_MID;

    if (takeoffTimer_s_ < takeoffSpoolTime_s_) {
        currentCommand_.throttle = takeoffSpoolThrottle_;
        return;
    }

    const double ramp_t = takeoffTimer_s_ - takeoffSpoolTime_s_;
    if (ramp_t < takeoffRampTime_s_) {
        const double alpha = ramp_t / takeoffRampTime_s_;
        const double cmd = (1.0 - alpha) * static_cast<double>(takeoffSpoolThrottle_) +
                           alpha * static_cast<double>(takeoffTargetThrottle_);
        currentCommand_.throttle = static_cast<std::uint16_t>(cmd);
        return;
    }

    currentCommand_.throttle = hoverThrottle_;

    if (ramp_t > (takeoffRampTime_s_ + takeoffHoldTime_s_)) {
        setControlMode(ControlMode::Tracking);
    }
}

void FlightController::runHoverSearchState() {
    telemetryData_.tracking_state = TrackingState::Searching;

    setAngleMode(true);
    setAltHold(true);

    currentCommand_.roll = rc::DRONE_MID;
    currentCommand_.pitch = rc::DRONE_MID;
    // No `currentCommand_.yaw = rc::DRONE_MID;` here: commandYawRate() below
    // overwrites yaw unconditionally, so a pre-write is a dead store. Roll
    // and pitch above are NOT dead — nothing else writes them in this path.

    commandAltHoldDelta(0.0);
    commandYawRate(searchYawRate_dps_);
}

void FlightController::runHoverStillState(TrackingState stateForTelemetry) {
    telemetryData_.tracking_state = stateForTelemetry;

    setAngleMode(true);
    setAltHold(true);

    currentCommand_.roll = rc::DRONE_MID;
    currentCommand_.pitch = rc::DRONE_MID;
    currentCommand_.yaw = rc::DRONE_MID;

    commandAltHoldDelta(0.0);
}

void FlightController::runFollowTargetState() {
    setAngleMode(true);
    setAltHold(true);
    runFollowTargetLogic();
}

void FlightController::runLandSafelyMode(double deltaTime_s) {
    if (!landSafelyInitialized_) {
        landSafelyInitialized_ = true;
        landTimer_s_ = 0.0;
        landStartThrottle_ = std::max<std::uint16_t>(
            kMinLandThrottle, std::min(currentCommand_.throttle, hoverThrottle_));
    }

    if (!is_armed(currentCommand_)) {
        telemetryData_.tracking_state = TrackingState::Searching;
        currentCommand_.throttle = rc::DRONE_MIN;
        return;
    }

    telemetryData_.tracking_state = TrackingState::Searching;
    landTimer_s_ += deltaTime_s;

    setAltHold(false);
    setAngleMode(true);

    currentCommand_.roll = rc::DRONE_MID;
    currentCommand_.pitch = rc::DRONE_MID;
    currentCommand_.yaw = rc::DRONE_MID;

    if (landTimer_s_ >= kDisarmTimeS) {
        currentCommand_.throttle = rc::DRONE_MIN;
        setArm(false);
        return;
    }

    const double throttle_cmd =
        static_cast<double>(landStartThrottle_) - kLandRampRateUsPerS * landTimer_s_;
    const double clamped = std::clamp(throttle_cmd, static_cast<double>(kMinLandThrottle),
                                      static_cast<double>(rc::DRONE_MAX));

    currentCommand_.throttle = static_cast<std::uint16_t>(clamped);
}

void FlightController::runManualMode() {
    telemetryData_.tracking_state = TrackingState::Searching;
}

void FlightController::runTrackingMode() {
    const bool stale = lastTrackingUpdateTime_s_ < 0.0 ||
                       (telemetryData_.timestamp_s - lastTrackingUpdateTime_s_) >
                           trackingConfig_.trackingTimeout_s;

    if (stale) {
        runHoverSearchState();
        return;
    }

    switch (lastTrackingMsg_.state) {
    case TrackingState::Tracking:
        if (isTargetValid()) {
            runFollowTargetState();
        } else {
            runHoverSearchState();
        }
        break;
    case TrackingState::Searching:
        runHoverSearchState();
        break;
    case TrackingState::TargetDetected:
        runHoverStillState(TrackingState::TargetDetected);
        break;
    case TrackingState::NoTarget:
    default:
        runHoverStillState(TrackingState::NoTarget);
        break;
    }
}

void FlightController::runFollowTargetLogic() {
    commandAltHoldDelta(0.0);

    const bool stale = lastTrackingUpdateTime_s_ < 0.0 ||
                       (telemetryData_.timestamp_s - lastTrackingUpdateTime_s_) >
                           trackingConfig_.trackingTimeout_s;
    const bool bad_state = (lastTrackingMsg_.state != TrackingState::Tracking);
    const bool low_confidence = (lastTrackingMsg_.confidence < trackingConfig_.minConfidence);

    if (stale || bad_state || low_confidence) {
        telemetryData_.tracking_state = TrackingState::Searching;
        telemetryData_.confidence = 0.0;

        currentCommand_.roll = rc::DRONE_MID;
        currentCommand_.pitch = rc::DRONE_MID;
        currentCommand_.yaw = rc::DRONE_MID;
        currentCommand_.throttle = hoverThrottle_;
        return;
    }

    telemetryData_.tracking_state = TrackingState::Tracking;
    telemetryData_.confidence = lastTrackingMsg_.confidence;

    double xError = lastTrackingMsg_.target_x - trackingConfig_.desiredTargetX;
    double yError = lastTrackingMsg_.target_y - trackingConfig_.desiredTargetY;
    const double bound_h = lastTrackingMsg_.bound_h;
    double sizeError = trackingConfig_.desiredBoxHeight - bound_h;

    if (std::abs(xError) < trackingConfig_.xDeadband) {
        xError = 0.0;
    }
    if (std::abs(yError) < trackingConfig_.yDeadband) {
        yError = 0.0;
    }
    if (std::abs(sizeError) < trackingConfig_.boxDeadband) {
        sizeError = 0.0;
    }

    const double yawRateCmd = trackingConfig_.kYawPerUnitError * xError;
    commandYawRate(yawRateCmd);

    double forwardCmd = trackingConfig_.kSpeedPerUnitError * sizeError;
    if (bound_h >= trackingConfig_.maxBoxHeight) {
        forwardCmd = -userParams_.maxSpeed_mps;
    } else if (bound_h <= trackingConfig_.minBoxHeight) {
        forwardCmd = userParams_.maxSpeed_mps;
    }

    forwardCmd = std::clamp(forwardCmd, -userParams_.maxSpeed_mps, userParams_.maxSpeed_mps);
    commandForward(forwardCmd);

    double deltaNorm = -trackingConfig_.kAltPerUnitError * yError;
    deltaNorm = std::clamp(deltaNorm, -1.0, 1.0);
    commandAltHoldDelta(deltaNorm);

    currentCommand_.roll = rc::DRONE_MID;
}

void FlightController::clampCommandChannels() {
    currentCommand_.roll = std::clamp(currentCommand_.roll, rc::DRONE_MIN, rc::DRONE_MAX);
    currentCommand_.pitch = std::clamp(currentCommand_.pitch, rc::DRONE_MIN, rc::DRONE_MAX);
    currentCommand_.yaw = std::clamp(currentCommand_.yaw, rc::DRONE_MIN, rc::DRONE_MAX);
    currentCommand_.throttle = std::clamp(currentCommand_.throttle, rc::DRONE_MIN, rc::DRONE_MAX);
    currentCommand_.aux1 = std::clamp(currentCommand_.aux1, rc::DRONE_AUX_MIN, rc::DRONE_AUX_MAX);
    currentCommand_.aux2 = std::clamp(currentCommand_.aux2, rc::DRONE_AUX_MIN, rc::DRONE_AUX_MAX);
    currentCommand_.aux3 = std::clamp(currentCommand_.aux3, rc::DRONE_AUX_MIN, rc::DRONE_AUX_MAX);
    currentCommand_.aux4 = std::clamp(currentCommand_.aux4, rc::DRONE_AUX_MIN, rc::DRONE_AUX_MAX);
}

BetaFlightCommand FlightController::updateTimeStep(double deltaTime_s) {
    if (!std::isfinite(deltaTime_s) || deltaTime_s < 0.0) {
        deltaTime_s = 0.0;
    } else if (deltaTime_s > kMaxControlDtS) {
        deltaTime_s = kMaxControlDtS;
    }

    telemetryData_.timestamp_s += deltaTime_s;

    telemetryData_.control_mode = controlMode_;
    telemetryData_.last_tracking_msg = lastTrackingMsg_;

    switch (controlMode_) {
    case ControlMode::Manual:
        runManualMode();
        break;
    case ControlMode::Tracking:
        runTrackingMode();
        break;
    case ControlMode::LandSafely:
        runLandSafelyMode(deltaTime_s);
        break;
    case ControlMode::Takeoff:
        runTakeoffMode(deltaTime_s);
        break;
    }

    if (!is_armed(currentCommand_)) {
        currentCommand_.throttle = rc::DRONE_MIN;
    }

    if (telemetryData_.tracking_state != TrackingState::Tracking) {
        telemetryData_.confidence = 0.0;
    }

    clampCommandChannels();
    return currentCommand_;
}

void FlightController::setArm(bool arm) {
    currentCommand_.aux1 = arm ? rc::DRONE_AUX_MAX : rc::DRONE_AUX_MIN;
}

void FlightController::setAngleMode(bool angleMode) {
    currentCommand_.aux2 = angleMode ? rc::DRONE_AUX_MAX : rc::DRONE_AUX_MIN;
}

void FlightController::setAltHold(bool enabled) {
    currentCommand_.aux3 = enabled ? rc::DRONE_AUX_MAX : rc::DRONE_AUX_MIN;
}

void FlightController::commandForward(double speedMps) {
    const double maxSpeed = userParams_.maxSpeed_mps;
    if (maxSpeed <= 0.0 || !std::isfinite(speedMps)) {
        currentCommand_.pitch = rc::DRONE_MID;
        return;
    }

    const double limitedSpeed = std::clamp(speedMps, -maxSpeed, maxSpeed);
    const double normalized = limitedSpeed / maxSpeed;
    const double pitchCmd = static_cast<double>(rc::DRONE_MID) + normalized * kPitchRangeUs;

    currentCommand_.pitch = static_cast<std::uint16_t>(std::clamp(
        pitchCmd, static_cast<double>(rc::DRONE_MIN), static_cast<double>(rc::DRONE_MAX)));
}

void FlightController::commandYawRate(double yawRateDps) {
    if (!std::isfinite(yawRateDps)) {
        currentCommand_.yaw = rc::DRONE_MID;
        return;
    }

    const double limitedYawRate = std::clamp(yawRateDps, -kMaxYawRateDps, kMaxYawRateDps);
    const double normalized = limitedYawRate / kMaxYawRateDps;
    const double yawCmd = static_cast<double>(rc::DRONE_MID) + normalized * kYawRangeUs;

    currentCommand_.yaw = static_cast<std::uint16_t>(
        std::clamp(yawCmd, static_cast<double>(rc::DRONE_MIN), static_cast<double>(rc::DRONE_MAX)));
}

void FlightController::commandAltHoldDelta(double deltaNorm) {
    if (!std::isfinite(deltaNorm)) {
        deltaNorm = 0.0;
    }

    deltaNorm = std::clamp(deltaNorm, -1.0, 1.0);

    const double hover = static_cast<double>(hoverThrottle_);
    double cmd = hover;

    if (deltaNorm < 0.0) {
        const double downRange = std::max(0.0, hover - static_cast<double>(rc::DRONE_MIN));
        cmd = hover + deltaNorm * downRange;
    } else {
        const double upRange = std::max(0.0, static_cast<double>(kAltHoldMaxThrottle) - hover);
        cmd = hover + deltaNorm * upRange;
    }

    currentCommand_.throttle = static_cast<std::uint16_t>(
        std::clamp(cmd, static_cast<double>(rc::DRONE_MIN), static_cast<double>(rc::DRONE_MAX)));
}

} // namespace fc
