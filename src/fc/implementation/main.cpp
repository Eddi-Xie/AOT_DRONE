#include "Clamp.h"
#include "CmdSeq.h"
#include "CommandServer.h"
#include "FlightController.h"
#include "ProtocolConstants.h"
#include "RcMath.h"
#include "TelemetryPublisher.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <thread>

namespace {

constexpr int kTelHz = 50;

using fc::clamp_target_coord;
using fc::clamp_unit;
using fc::rc::normalized_axis_to_pwm;
using fc::rc::throttle_to_pwm;
using fc::rc::yaw_rate_to_pwm;

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

    // Default to loopback so a misconfigured deployment doesn't accidentally
    // expose the FC TCP listener to the LAN. Operators that intentionally need
    // remote ingress (e.g. backend on a different machine) set FC_BIND_HOST.
    const char* fc_bind_host_env = std::getenv("FC_BIND_HOST");
    const std::string fc_bind_host =
        (fc_bind_host_env && *fc_bind_host_env) ? std::string(fc_bind_host_env) : "127.0.0.1";

    fc::CommandServer command_server(proto::TCP_CMD_PORT, fc_bind_host);
    if (!command_server.start()) {
        std::cerr << "[FC] Failed to start CommandServer on " << fc_bind_host << ":"
                  << proto::TCP_CMD_PORT << "\n";
        return 1;
    }
    std::cout << "[FC] CommandServer listening on " << fc_bind_host << ":" << proto::TCP_CMD_PORT
              << "\n";

    // TEL UDP destination is configurable so a multi-machine deployment
    // (backend on a separate host) doesn't require a recompile. Defaults
    // match the existing single-host setup. Mirrors FC_BIND_HOST shape from
    // chore/sprint0-network.
    const char* fc_tel_host_env = std::getenv("FC_TEL_HOST");
    const std::string fc_tel_host =
        (fc_tel_host_env && *fc_tel_host_env) ? std::string(fc_tel_host_env) : "127.0.0.1";

    const char* fc_tel_port_env = std::getenv("FC_TEL_PORT");
    int fc_tel_port = static_cast<int>(proto::UDP_TEL_PORT);
    if (fc_tel_port_env && *fc_tel_port_env) {
        try {
            fc_tel_port = std::stoi(fc_tel_port_env);
        } catch (const std::exception&) {
            std::cerr << "[FC] Invalid FC_TEL_PORT='" << fc_tel_port_env
                      << "', refusing to start (must be a positive integer)\n";
            command_server.stop();
            return 1;
        }
        if (fc_tel_port <= 0 || fc_tel_port > 65535) {
            std::cerr << "[FC] FC_TEL_PORT=" << fc_tel_port
                      << " out of range [1, 65535], refusing to start\n";
            command_server.stop();
            return 1;
        }
    }

    fc::TelemetryPublisher telemetry_publisher(fc_tel_host, fc_tel_port);
    if (!telemetry_publisher.ok()) {
        std::cerr << "[FC] Failed to init UDP telemetry publisher for " << fc_tel_host << ":"
                  << fc_tel_port << "\n";
        command_server.stop();
        return 1;
    }

    fc::FlightController flight_controller;
    flight_controller.setControlMode(fc::ControlMode::LandSafely);

    std::cout << "[FC] fc_app running. CMD TCP:" << proto::TCP_CMD_PORT
              << " TEL UDP:" << fc_tel_host << ":" << fc_tel_port << "\n";

    // Signed-modular int32 comparator on cmd.seq handles wrap correctly:
    // a fresh seq of 0 after CMD_SEQ_MAX is treated as "ahead by 1", not
    // "behind by 2^31 - 1". `std::nullopt` => no CMD seen yet, so the first
    // valid frame is always accepted regardless of its seq value (closes
    // the `last_cmd_seq = -1` sentinel hole that collided with seq=0 after
    // backend reseed).
    std::optional<std::int32_t> last_cmd_seq;
    uint64_t tel_seq = 0;

    // Stale-CMD failsafe: enter LandSafely on the fresh -> stale transition
    // only, not on every tick. The previous code re-stomped setControlMode
    // every iteration (50 Hz), which spammed log output and prevented any
    // observability into "did we just enter failsafe vs have we been here
    // for a while". The end state is identical to today's (LandSafely while
    // stale), but the entry is now an event, not a per-tick stomp. The
    // two-stage Healthy/StaleSoft/StaleHard state machine + auto-resume is
    // an in-flight-behaviour change deferred to S0.7 (HIL bench gate).
    bool failsafe_engaged = false;

    const auto period = std::chrono::milliseconds(1000 / kTelHz);
    auto next_tick = std::chrono::steady_clock::now();
    auto last_tick = next_tick;

    while (g_running.load()) {
        const auto now = std::chrono::steady_clock::now();
        const double dt = std::chrono::duration<double>(now - last_tick).count();
        last_tick = now;

        fc::CommandFrame cmd;
        if (command_server.latest_command(cmd) &&
            (!last_cmd_seq.has_value() ||
             fc::cmd::seq_advances(*last_cmd_seq, static_cast<std::int32_t>(cmd.seq)))) {
            last_cmd_seq = static_cast<std::int32_t>(cmd.seq);

            // Unknown desired_mode (schema-version skew, backend bug, fuzzed
            // input) does NOT trigger LandSafely. The previous behaviour was
            // to stomp into LandSafely on every parse blip, which under a
            // sustained malformed-CMD stream would oscillate the drone.
            // Keep the current mode and log the rejection — the operator
            // sees it via TEL `control_mode` not changing + log volume,
            // which is a more specific failure mode than "drone lands".
            fc::ControlMode desired_mode;
            if (to_control_mode(cmd.desired_mode, desired_mode)) {
                flight_controller.setControlMode(desired_mode);
            } else {
                std::cerr << "[FC] CMD seq=" << cmd.seq
                          << " has unknown desired_mode=" << cmd.desired_mode
                          << "; keeping current control mode\n";
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
        const bool stale_now = cmd_age_s > proto::CMD_TIMEOUT_S;
        if (stale_now && !failsafe_engaged) {
            failsafe_engaged = true;
            flight_controller.setControlMode(fc::ControlMode::LandSafely);
            std::cerr << "[FC] CMD link stale (age=" << cmd_age_s << "s > " << proto::CMD_TIMEOUT_S
                      << "s); entering LandSafely failsafe\n";
        } else if (!stale_now && failsafe_engaged) {
            failsafe_engaged = false;
            // Don't auto-resume the prior mode — operator confirms recovery
            // by sending a fresh CMD with desired_mode set explicitly. Auto-
            // resume is part of the deferred two-stage state machine (S0.7).
            std::cerr << "[FC] CMD link recovered (age=" << cmd_age_s
                      << "s); staying in LandSafely until next CMD\n";
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
