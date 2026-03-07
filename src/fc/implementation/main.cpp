#include "CommandServer.h"
#include "FlightController.h"
#include "ProtocolConstants.h"
#include "TelemetryPublisher.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <thread>

namespace {

constexpr int kTelHz = 50;
constexpr double kAxisRangeUs = 300.0;
constexpr double kMaxYawRateDps = 180.0;

std::atomic<bool> g_running{true};

void handle_signal(int) {
    g_running.store(false);
}

bool to_control_mode(int raw_mode, fc::ControlMode& out_mode) {
    switch (raw_mode) {
    case 0:
        out_mode = fc::ControlMode::Manual;
        return true;
    case 1:
        out_mode = fc::ControlMode::Tracking;
        return true;
    case 2:
        out_mode = fc::ControlMode::LandSafely;
        return true;
    case 3:
        out_mode = fc::ControlMode::Takeoff;
        return true;
    default:
        return false;
    }
}

bool to_tracking_state(int raw_state, fc::TrackingState& out_state) {
    switch (raw_state) {
    case 1:
        out_state = fc::TrackingState::NoTarget;
        return true;
    case 2:
        out_state = fc::TrackingState::TargetDetected;
        return true;
    case 3:
        out_state = fc::TrackingState::Tracking;
        return true;
    case 4:
        out_state = fc::TrackingState::Searching;
        return true;
    default:
        return false;
    }
}

double clamp_target_coord(double value) {
    if (!std::isfinite(value)) {
        return 0.0;
    }
    return std::clamp(value, -1.0, 1.0);
}

double clamp_unit(double value) {
    if (!std::isfinite(value)) {
        return 0.0;
    }
    return std::clamp(value, 0.0, 1.0);
}

std::uint16_t normalized_axis_to_pwm(double normalized) {
    if (!std::isfinite(normalized)) {
        return fc::rc::DRONE_MID;
    }

    const double clamped = std::clamp(normalized, -1.0, 1.0);
    const double pwm = static_cast<double>(fc::rc::DRONE_MID) + clamped * kAxisRangeUs;
    return static_cast<std::uint16_t>(std::lround(std::clamp(
        pwm, static_cast<double>(fc::rc::DRONE_MIN), static_cast<double>(fc::rc::DRONE_MAX))));
}

std::uint16_t yaw_rate_to_pwm(double yaw_rate_dps) {
    if (!std::isfinite(yaw_rate_dps)) {
        return fc::rc::DRONE_MID;
    }

    const double limited = std::clamp(yaw_rate_dps, -kMaxYawRateDps, kMaxYawRateDps);
    const double normalized = limited / kMaxYawRateDps;
    return normalized_axis_to_pwm(normalized);
}

std::uint16_t throttle_to_pwm(double throttle) {
    if (!std::isfinite(throttle)) {
        return fc::rc::DRONE_MIN;
    }

    if (throttle >= 0.0 && throttle <= 1.0) {
        const double pwm = static_cast<double>(fc::rc::DRONE_MIN) +
                           throttle * static_cast<double>(fc::rc::DRONE_MAX - fc::rc::DRONE_MIN);
        return static_cast<std::uint16_t>(std::lround(std::clamp(
            pwm, static_cast<double>(fc::rc::DRONE_MIN), static_cast<double>(fc::rc::DRONE_MAX))));
    }

    return static_cast<std::uint16_t>(std::lround(std::clamp(
        throttle, static_cast<double>(fc::rc::DRONE_MIN), static_cast<double>(fc::rc::DRONE_MAX))));
}

void apply_setpoint_overrides(const fc::CommandFrame& cmd, fc::FlightController& controller) {
    const fc::CommandSetpoints& setpoints = cmd.setpoints;
    if (!setpoints.has_roll && !setpoints.has_pitch && !setpoints.has_yaw_rate &&
        !setpoints.has_throttle) {
        return;
    }

    fc::BetaFlightCommand command = controller.getCurrentCommand();
    if (setpoints.has_roll) {
        command.roll = normalized_axis_to_pwm(setpoints.roll);
    }
    if (setpoints.has_pitch) {
        command.pitch = normalized_axis_to_pwm(setpoints.pitch);
    }
    if (setpoints.has_yaw_rate) {
        command.yaw = yaw_rate_to_pwm(setpoints.yaw_rate);
    }
    if (setpoints.has_throttle) {
        command.throttle = throttle_to_pwm(setpoints.throttle);
    }

    controller.setManualSetpoints(command);
}

void apply_tracking_update(const fc::CommandFrame& cmd, fc::FlightController& controller) {
    if (!cmd.tracking.has_tracking) {
        return;
    }

    fc::TrackingState tracking_state = fc::TrackingState::Searching;
    if (!to_tracking_state(cmd.tracking.tracking_state, tracking_state)) {
        return;
    }

    fc::TrackingMessage tracking_message;
    tracking_message.state = tracking_state;
    tracking_message.target_x = cmd.tracking.loc_x;
    tracking_message.target_y = cmd.tracking.loc_y;
    tracking_message.bound_w = cmd.tracking.bound_w;
    tracking_message.bound_h = cmd.tracking.bound_h;
    tracking_message.confidence = cmd.tracking.confidence;
    tracking_message.timestamp_s = cmd.tracking.vis_timestamp_s;
    controller.updateTracking(tracking_message);
}

std::string build_tel_json(uint64_t seq, const fc::TelemetryData& telemetry,
                           const fc::TrackingMessage& tracking_message, double cmd_age_s) {
    double target_x = 0.0;
    double target_y = 0.0;
    double bound_w = 0.0;
    double bound_h = 0.0;
    double confidence = 0.0;

    if (telemetry.tracking_state == fc::TrackingState::Tracking) {
        target_x = clamp_target_coord(tracking_message.target_x);
        target_y = clamp_target_coord(tracking_message.target_y);
        bound_w = clamp_unit(tracking_message.bound_w);
        bound_h = clamp_unit(tracking_message.bound_h);
        confidence = clamp_unit(tracking_message.confidence);
    }

    std::ostringstream oss;
    oss << std::fixed << std::setprecision(6);
    oss << "{"
        << "\"type\":\"TEL\","
        << "\"seq\":" << seq << ","
        << "\"timestamp_s\":" << telemetry.timestamp_s << ","
        << "\"control_mode\":" << static_cast<int>(telemetry.control_mode) << ","
        << "\"cmd_age_s\":" << cmd_age_s << ","
        << "\"tracking_state\":" << static_cast<int>(telemetry.tracking_state) << ","
        << "\"distFront_m\":" << telemetry.distFront_m << ","
        << "\"distBack_m\":" << telemetry.distBack_m << ","
        << "\"distBottom_m\":" << telemetry.distBottom_m << ","
        << "\"target_x\":" << target_x << ","
        << "\"target_y\":" << target_y << ","
        << "\"bound_w\":" << bound_w << ","
        << "\"bound_h\":" << bound_h << ","
        << "\"confidence\":" << confidence << "}";
    return oss.str();
}

} // namespace

int main() {
    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    fc::CommandServer command_server(proto::TCP_CMD_PORT);
    if (!command_server.start()) {
        std::cerr << "[FC] Failed to start CommandServer on port " << proto::TCP_CMD_PORT << "\n";
        return 1;
    }

    fc::TelemetryPublisher telemetry_publisher("127.0.0.1", proto::UDP_TEL_PORT);
    if (!telemetry_publisher.ok()) {
        std::cerr << "[FC] Failed to init UDP telemetry publisher for 127.0.0.1:"
                  << proto::UDP_TEL_PORT << "\n";
        command_server.stop();
        return 1;
    }

    fc::FlightController flight_controller;
    flight_controller.setControlMode(fc::ControlMode::LandSafely);

    std::cout << "[FC] fc_app running. CMD TCP:" << proto::TCP_CMD_PORT
              << " TEL UDP:127.0.0.1:" << proto::UDP_TEL_PORT << "\n";

    int last_cmd_seq = -1;
    uint64_t tel_seq = 0;

    const auto period = std::chrono::milliseconds(1000 / kTelHz);
    auto next_tick = std::chrono::steady_clock::now();
    auto last_tick = next_tick;

    while (g_running.load()) {
        const auto now = std::chrono::steady_clock::now();
        const double dt = std::chrono::duration<double>(now - last_tick).count();
        last_tick = now;

        fc::CommandFrame cmd;
        if (command_server.latest_command(cmd) && cmd.seq != last_cmd_seq) {
            last_cmd_seq = cmd.seq;

            fc::ControlMode desired_mode = fc::ControlMode::LandSafely;
            if (to_control_mode(cmd.desired_mode, desired_mode)) {
                flight_controller.setControlMode(desired_mode);
            } else {
                flight_controller.setControlMode(fc::ControlMode::LandSafely);
            }

            if (cmd.has_arm) {
                flight_controller.setArm(cmd.arm);
            }
            apply_setpoint_overrides(cmd, flight_controller);
            apply_tracking_update(cmd, flight_controller);

            std::cout << "[FC] Applied CMD seq=" << cmd.seq << " desired_mode=" << cmd.desired_mode
                      << "\n";
        }

        const double cmd_age_s = command_server.seconds_since_last_cmd();
        if (cmd_age_s > proto::CMD_TIMEOUT_S) {
            flight_controller.setControlMode(fc::ControlMode::LandSafely);
        }

        (void)flight_controller.updateTimeStep(dt);

        const fc::TelemetryData& telemetry = flight_controller.getTelemetryData();
        const fc::TrackingMessage& tracking_message = flight_controller.getLastTrackingMessage();

        const std::string tel_json =
            build_tel_json(tel_seq, telemetry, tracking_message, cmd_age_s);
        telemetry_publisher.send_json(tel_json);

        if ((tel_seq % 25U) == 0U) {
            std::cout << "[FC] TEL seq=" << tel_seq
                      << " mode=" << static_cast<int>(telemetry.control_mode)
                      << " tracking_state=" << static_cast<int>(telemetry.tracking_state) << "\n";
        }
        ++tel_seq;

        next_tick += period;
        const auto post_send = std::chrono::steady_clock::now();
        if (next_tick < post_send) {
            next_tick = post_send;
        }
        std::this_thread::sleep_until(next_tick);
    }

    command_server.stop();
    std::cout << "[FC] fc_app stopped\n";
    return 0;
}
