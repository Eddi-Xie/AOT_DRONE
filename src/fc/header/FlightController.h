#pragma once

#include "RcConstants.h"

#include <array>
#include <cstdint>

namespace fc {

// Alt-hold upper ceiling (S0.9 audit #2). Used as:
//   - the upper clamp on hoverThrottle_ in FlightController::setHoverThrottle
//   - the positive-delta excursion ceiling in commandAltHoldDelta
//   - the operator-facing upper bound on FC_HOVER_THROTTLE in main.cpp
// 200 µs safety margin below rc::DRONE_MAX (2000). Lives in the header
// so main.cpp's env-var validator stays in sync with FlightController's
// clamp policy without having to duplicate the value.
inline constexpr std::uint16_t kAltHoldMaxThrottle = 1800;

enum class ControlMode : int {
    Manual = 0,
    Tracking = 1,
    LandSafely = 2,
    Takeoff = 3,
};

enum class TrackingState : int {
    NoTarget = 1,
    TargetDetected = 2,
    Tracking = 3,
    Searching = 4,
};

struct BetaFlightCommand {
    std::uint16_t roll = rc::DRONE_MID;
    std::uint16_t pitch = rc::DRONE_MID;
    std::uint16_t yaw = rc::DRONE_MID;
    std::uint16_t throttle = rc::DRONE_MIN;
    std::uint16_t aux1 = rc::DRONE_AUX_MIN;
    std::uint16_t aux2 = rc::DRONE_AUX_MAX;
    std::uint16_t aux3 = rc::DRONE_AUX_MIN;
    std::uint16_t aux4 = rc::DRONE_AUX_MIN;
};

struct UserParams {
    double followDistance_m = 2.0;
    double followAngle_deg = 0.0;
    double maxSpeed_mps = 1.0;
    double maxAltitude_m = 10.0;
};

struct TrackingMessage {
    TrackingState state = TrackingState::Searching;
    double target_x = 0.0;
    double target_y = 0.0;
    double bound_w = 0.0;
    double bound_h = 0.0;
    double confidence = 0.0;
    double timestamp_s = 0.0;
};

struct TrackingConfig {
    double trackingTimeout_s = 0.8;
    double minConfidence = 0.5;
    double desiredTargetX = 0.0;
    double desiredTargetY = 0.0;
    double desiredBoxHeight = 0.25;
    double kYawPerUnitError = 200.0;
    double kSpeedPerUnitError = 1.0;
    double minBoxHeight = 0.10;
    double maxBoxHeight = 0.45;
    double kAltPerUnitError = 0.5;
    double boxDeadband = 0.02;
    double xDeadband = 0.02;
    double yDeadband = 0.02;
};

struct TelemetryData {
    double timestamp_s = 0.0;
    ControlMode control_mode = ControlMode::LandSafely;
    TrackingState tracking_state = TrackingState::Searching;
    double distFront_m = 0.0;
    double distBack_m = 0.0;
    double distBottom_m = 0.0;
    double confidence = 0.0;
    std::array<double, 3> orientation_deg{{0.0, 0.0, 0.0}};
    std::array<double, 3> position_m{{0.0, 0.0, 0.0}};
    TrackingMessage last_tracking_msg{};
};

class FlightController {
  public:
    FlightController();

    void setControlMode(ControlMode mode);
    ControlMode getControlMode() const { return controlMode_; }

    void setUserParams(const UserParams& params) { userParams_ = params; }
    const UserParams& getUserParams() const { return userParams_; }

    void setTrackingConfig(const TrackingConfig& config) { trackingConfig_ = config; }
    const TrackingConfig& getTrackingConfig() const { return trackingConfig_; }

    void setHoverThrottle(std::uint16_t hoverThrottle);
    // Sentinel value 0 means "uncalibrated" — main.cpp's
    // apply_control_mode_safely refuses Tracking / Takeoff transitions
    // until the operator sets a real value via FC_HOVER_THROTTLE (or
    // explicit setHoverThrottle call from a calibration script). See
    // S0.9 / audit M-09.
    std::uint16_t getHoverThrottle() const { return hoverThrottle_; }

    void updateTracking(const TrackingMessage& msg);
    void setDistances(double front_m, double back_m, double bottom_m);
    void setOrientationDeg(double roll_deg, double pitch_deg, double yaw_deg);
    void setAltitudeMeters(double altitude_m);

    void setManualSetpoints(const BetaFlightCommand& cmd);
    const BetaFlightCommand& getCurrentCommand() const { return currentCommand_; }
    const TrackingMessage& getLastTrackingMessage() const { return lastTrackingMsg_; }

    BetaFlightCommand updateTimeStep(double deltaTime_s);

    const TelemetryData& getTelemetryData() const { return telemetryData_; }

    void setArm(bool arm);
    void setAngleMode(bool angleMode);
    void setAltHold(bool enabled);
    void commandForward(double speedMps);
    void commandYawRate(double yawRateDps);
    void commandAltHoldDelta(double deltaNorm);

  private:
    bool isTargetValid() const;
    void clampCommandChannels();

    void runTakeoffMode(double deltaTime_s);
    void runLandSafelyMode(double deltaTime_s);
    void runTrackingMode();
    void runManualMode();
    void runHoverSearchState();
    void runHoverStillState(TrackingState stateForTelemetry);
    void runFollowTargetState();
    void runFollowTargetLogic();

    UserParams userParams_{};
    TelemetryData telemetryData_{};
    ControlMode controlMode_ = ControlMode::LandSafely;
    TrackingMessage lastTrackingMsg_{};
    double lastTrackingUpdateTime_s_ = -1.0;

    BetaFlightCommand currentCommand_{};
    TrackingConfig trackingConfig_{};

    bool landSafelyInitialized_ = false;
    double landTimer_s_ = 0.0;
    std::uint16_t landStartThrottle_ = rc::DRONE_MIN;

    bool takeoffInitialized_ = false;
    double takeoffTimer_s_ = 0.0;

    // Default 0 is the "uncalibrated" sentinel. The audit (M-09) flagged
    // the previous 1100 µs default as unsafe — fc_app would happily fly
    // Takeoff / Tracking modes with an arbitrary hover value that no
    // operator had actually confirmed. apply_control_mode_safely refuses
    // those mode transitions while this is 0; the operator sets a real
    // value via FC_HOVER_THROTTLE env var or scripts/dev/hover_calibration.py.
    std::uint16_t hoverThrottle_ = 0;
    double searchYawRate_dps_ = 60.0;

    double takeoffSpoolTime_s_ = 0.8;
    double takeoffRampTime_s_ = 1.6;
    double takeoffHoldTime_s_ = 1.0;
    std::uint16_t takeoffSpoolThrottle_ = rc::DRONE_MIN + 20;
    std::uint16_t takeoffTargetThrottle_ = 1250;
};

} // namespace fc
