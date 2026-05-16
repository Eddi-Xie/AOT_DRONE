#include "Clamp.h"
#include "CmdSeq.h"
#include "CommandServer.h"
#include "FlightController.h"
#include "MspRcSink.h"
#include "ProtocolConstants.h"
#include "RcMath.h"
#include "RcSink.h"
#include "TelemetryPublisher.h"

#include <arpa/inet.h>
#include <sys/stat.h>

#include <algorithm>
#include <atomic>
#include <charconv>
#include <chrono>
#include <clocale>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <locale>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <system_error>
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

// Apply a control-mode transition through a single chokepoint. Tracking
// and Takeoff both depend on the FC channel-write path being healthy
// (boot probe ok, recent writes succeeding, rate profile confirmed) —
// refusing those transitions when the RC sink can't honour them keeps
// the drone in its current mode (typically LandSafely or Manual) rather
// than committing to a mode that needs MSP-level guarantees we don't
// have. LandSafely and Manual are always allowed: the operator must be
// able to land regardless of sink state.
//
// The dev plan (S0.8 line 272-273) calls for refusing Tracking/Takeoff
// on boot-probe failure AND on MSP_RC_TUNING mismatch. Both conditions
// gate identically here; operator-confirm UI for the mismatch path is
// out of scope for S0.8 and lands with S0.14's arm-authority work.
bool apply_control_mode_safely(fc::ControlMode desired, fc::FlightController& flight_controller,
                               const fc::IRcSink& rc_sink, std::int32_t cmd_seq) {
    const bool needs_msp =
        (desired == fc::ControlMode::Tracking || desired == fc::ControlMode::Takeoff);
    if (needs_msp) {
        if (!rc_sink.ok()) {
            std::cerr << "[FC] CMD seq=" << cmd_seq << " refused mode transition to "
                      << static_cast<int>(desired)
                      << "; RC sink not healthy (boot probe failed or write loop degraded)\n";
            return false;
        }
        if (rc_sink.tuning_mismatch()) {
            std::cerr << "[FC] CMD seq=" << cmd_seq << " refused mode transition to "
                      << static_cast<int>(desired)
                      << "; MSP_RC_TUNING did not confirm the FC's rate profile (operator-"
                         "confirm UI lands with S0.14)\n";
            return false;
        }
        // Uncalibrated-hover gate (S0.9 / audit M-09): refuse any mode that
        // depends on a known hover throttle until the operator has explicitly
        // calibrated. hoverThrottle_=0 is the sentinel; set via
        // FC_HOVER_THROTTLE env var or scripts/dev/hover_calibration.py.
        // Manual is intentionally NOT gated here — it's operator-direct, and
        // clampCommandChannels handles a 0 throttle by clamping up to
        // DRONE_MIN, which is safer than refusing operator control entirely.
        if (flight_controller.getHoverThrottle() == 0) {
            std::cerr << "[FC] CMD seq=" << cmd_seq << " refused mode transition to "
                      << static_cast<int>(desired)
                      << "; hoverThrottle uncalibrated (set FC_HOVER_THROTTLE or run "
                         "scripts/dev/hover_calibration.py; operator-confirm UI lands with "
                         "S0.14)\n";
            return false;
        }
    }
    flight_controller.setControlMode(desired);
    return true;
}

std::string build_tel_json(uint64_t seq, const fc::TelemetryData& telemetry,
                           const fc::TrackingMessage& tracking_message, double cmd_age_s,
                           double msp_tx_ratio) {
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
    // Pin the C locale on this stream so float formatting always uses '.' as
    // the decimal separator regardless of the process locale (e.g.
    // de_DE.UTF-8 would otherwise produce "1,234567" — invalid JSON).
    oss.imbue(std::locale::classic());
    oss << std::fixed << std::setprecision(6);
    oss << "{"
        << "\"type\":\"TEL\","
        << "\"seq\":" << seq << ","
        << "\"timestamp_s\":" << telemetry.timestamp_s << ","
        << "\"control_mode\":" << static_cast<int>(telemetry.control_mode) << ","
        << "\"cmd_age_s\":" << cmd_age_s << ","
        << "\"msp_tx_ratio\":" << msp_tx_ratio << ","
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

// Construct the IRcSink selected by FC_RC_SINK. Defaults to NullSink so an
// unset env mirrors the pre-S0.7 discard behaviour. Returns nullptr on a
// fatal config error (caller is responsible for reporting + exiting).
std::unique_ptr<fc::IRcSink> make_rc_sink_from_env() {
    const char* sink_env = std::getenv("FC_RC_SINK");
    const std::string sink_kind = (sink_env && *sink_env) ? std::string(sink_env) : "null";

    if (sink_kind == "null") {
        return std::make_unique<fc::NullSink>();
    }
    if (sink_kind == "recording") {
        const char* dir_env = std::getenv("FC_RC_LOG_DIR");
        const std::string log_dir =
            (dir_env && *dir_env) ? std::string(dir_env) : std::string("logs/hil");
        auto sink = std::make_unique<fc::RecordingSink>(log_dir);
        if (!sink->ok()) {
            std::cerr << "[FC] RecordingSink failed to open log file under '" << log_dir
                      << "'. Refusing to start.\n";
            return nullptr;
        }
        return sink;
    }
    if (sink_kind == "fake") {
        const char* host_env = std::getenv("FC_RC_FAKE_HOST");
        const std::string host =
            (host_env && *host_env) ? std::string(host_env) : std::string("127.0.0.1");
        const char* port_env = std::getenv("FC_RC_FAKE_PORT");
        int port = 9101;
        if (port_env && *port_env) {
            const char* end = port_env + std::strlen(port_env);
            auto [ptr, ec] = std::from_chars(port_env, end, port);
            if (ec != std::errc{} || ptr != end || port <= 0 || port > 65535) {
                std::cerr << "[FC] Invalid FC_RC_FAKE_PORT='" << port_env
                          << "', refusing to start (must be a base-10 integer in [1, 65535])\n";
                return nullptr;
            }
        }
        auto sink = std::make_unique<fc::FakeBetaflightSink>(host, port);
        if (!sink->ok()) {
            std::cerr << "[FC] FakeBetaflightSink failed to open UDP socket. Refusing to start.\n";
            return nullptr;
        }
        return sink;
    }
    if (sink_kind == "msp") {
        const char* dev_env = std::getenv("FC_RC_DEVICE");
        if (dev_env == nullptr || *dev_env == '\0') {
            std::cerr << "[FC] FC_RC_SINK=msp requires FC_RC_DEVICE to point at the FC's USB "
                         "serial node (e.g. /dev/cu.usbmodem... on macOS, /dev/ttyACM... on "
                         "Linux). Refusing to start.\n";
            return nullptr;
        }
        // Validate the device exists AND is a character device. This catches
        // operator typos like FC_RC_DEVICE=/etc/hosts or a stale path from a
        // previous USB session up-front, with the offending path named in
        // the message — rather than later when open() / tcgetattr would
        // produce a confusing "Inappropriate ioctl for device".
        struct stat st{};
        if (::stat(dev_env, &st) != 0 || !S_ISCHR(st.st_mode)) {
            std::cerr << "[FC] FC_RC_DEVICE='" << dev_env
                      << "' is not an accessible character device (open the FC over USB and "
                         "confirm the path with `ls /dev/cu.usbmodem* /dev/ttyACM*`). "
                         "Refusing to start.\n";
            return nullptr;
        }
        const char* baud_env = std::getenv("FC_RC_BAUD");
        int baud = 115200;
        if (baud_env != nullptr && *baud_env != '\0') {
            // Strict parse mirroring the FC_TEL_PORT / FC_RC_FAKE_PORT
            // pattern: from_chars rejects trailing junk (e.g. "115200abc")
            // and partial parses, unlike std::stoi which silently accepts
            // the leading digits.
            const char* const begin = baud_env;
            const char* const end = begin + std::strlen(begin);
            auto [ptr, ec] = std::from_chars(begin, end, baud);
            if (ec != std::errc{} || ptr != end || baud <= 0) {
                std::cerr << "[FC] Invalid FC_RC_BAUD='" << baud_env
                          << "', refusing to start (must be a positive base-10 integer; "
                             "MspRcSink supports 9600, 19200, 38400, 57600, 115200, 230400, "
                             "460800, 921600)\n";
                return nullptr;
            }
        }
        auto sink = fc::MspRcSink::from_device(dev_env, baud);
        if (sink == nullptr || !sink->ok()) {
            std::cerr << "[FC] MspRcSink failed to initialize (open / termios / boot probe). "
                         "Refusing to start.\n";
            return nullptr;
        }
        return sink;
    }
    std::cerr << "[FC] Unknown FC_RC_SINK='" << sink_kind
              << "'. Valid: null|recording|fake|msp. Falling back to 'null' is unsafe; refusing "
                 "to start.\n";
    return nullptr;
}

} // namespace

int main() {
    // Pin LC_NUMERIC=C process-wide so any future float formatter (sprintf,
    // strtod, etc.) is locale-safe by default. The TEL JSON ostringstream
    // additionally imbues std::locale::classic() in build_tel_json — defence
    // in depth against a third-party library that flips the locale at
    // runtime.
    std::setlocale(LC_NUMERIC, "C");

    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);
    // Ignore SIGPIPE process-wide. MspRcSink::write_frame_ writes to a
    // USB serial fd; a USB unplug or FC reboot can deliver SIGPIPE which
    // would otherwise terminate fc_app outright, bypassing the carefully
    // designed sink-degraded -> LandSafely failsafe path. With SIGPIPE
    // ignored, write() returns EPIPE and the sink's hard-error branch
    // closes the fd + flips ok() so main loop's parallel latch enters
    // LandSafely on the next tick.
    std::signal(SIGPIPE, SIG_IGN);

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
    {
        // TelemetryPublisher's UDP send path uses inet_pton internally and
        // will silently fail on a hostname like "localhost". Validate up-
        // front with the same rule so the error message points at the
        // operator-facing config knob instead of "Failed to init UDP
        // telemetry publisher" with no further explanation.
        sockaddr_in probe{};
        if (inet_pton(AF_INET, fc_tel_host.c_str(), &probe.sin_addr) != 1) {
            std::cerr << "[FC] Invalid FC_TEL_HOST='" << fc_tel_host
                      << "', refusing to start (use a dotted-quad IPv4 literal "
                         "such as 127.0.0.1, or 0.0.0.0; hostnames are not resolved)\n";
            command_server.stop();
            return 1;
        }
    }

    const char* fc_tel_port_env = std::getenv("FC_TEL_PORT");
    int fc_tel_port = static_cast<int>(proto::UDP_TEL_PORT);
    if (fc_tel_port_env && *fc_tel_port_env) {
        // Strict parse: from_chars rejects trailing junk (e.g. "9001abc"),
        // unlike std::stoi which silently accepts the leading digits.
        const char* const begin = fc_tel_port_env;
        const char* const end = begin + std::strlen(begin);
        auto [ptr, ec] = std::from_chars(begin, end, fc_tel_port);
        if (ec != std::errc{} || ptr != end) {
            std::cerr << "[FC] Invalid FC_TEL_PORT='" << fc_tel_port_env
                      << "', refusing to start (must be a base-10 integer with no trailing junk)\n";
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

    // FC_HOVER_THROTTLE: operator-calibrated hover throttle in µs (S0.9).
    // Default 0 = "uncalibrated"; apply_control_mode_safely refuses
    // Tracking/Takeoff transitions until this is set to a real value
    // measured against the operator's specific airframe (battery, mass,
    // props, ESC tune). Calibrated via scripts/dev/hover_calibration.py.
    // Range: 0 (sentinel) or [DRONE_MIN, kAltHoldMaxThrottle] = 0 or
    // [1000, 1800]; values outside this band are refused at parse.
    const char* fc_hover_throttle_env = std::getenv("FC_HOVER_THROTTLE");
    if (fc_hover_throttle_env != nullptr && *fc_hover_throttle_env != '\0') {
        int hover_us = 0;
        const char* const begin = fc_hover_throttle_env;
        const char* const end = begin + std::strlen(begin);
        auto [ptr, ec] = std::from_chars(begin, end, hover_us);
        if (ec != std::errc{} || ptr != end) {
            std::cerr << "[FC] Invalid FC_HOVER_THROTTLE='" << fc_hover_throttle_env
                      << "', refusing to start (must be a base-10 integer with no trailing junk)\n";
            command_server.stop();
            return 1;
        }
        // Permitted values: 0 (explicit uncalibrate) OR a calibrated
        // value in [DRONE_MIN, kAltHoldMaxThrottle]. Upper bound is
        // sourced from FlightController.h so this validator stays in
        // sync with setHoverThrottle's clamp policy automatically — a
        // hardcoded number here would silently drift if the ceiling
        // ever changes (review #5 callout in S0.9 Copilot pass).
        if (hover_us != 0 && (hover_us < static_cast<int>(fc::rc::DRONE_MIN) ||
                              hover_us > static_cast<int>(fc::kAltHoldMaxThrottle))) {
            std::cerr << "[FC] FC_HOVER_THROTTLE=" << hover_us
                      << " out of range; valid values are 0 (uncalibrated) or ["
                      << fc::rc::DRONE_MIN << ", " << fc::kAltHoldMaxThrottle
                      << "] (calibrated). Refusing to start.\n";
            command_server.stop();
            return 1;
        }
        flight_controller.setHoverThrottle(static_cast<std::uint16_t>(hover_us));
        std::cout << "[FC] FC_HOVER_THROTTLE=" << hover_us
                  << " (operator-calibrated); Tracking/Takeoff transitions allowed\n";
    }

    std::unique_ptr<fc::IRcSink> rc_sink = make_rc_sink_from_env();
    if (rc_sink == nullptr) {
        command_server.stop();
        return 1;
    }

    std::cout << "[FC] fc_app running. CMD TCP:" << proto::TCP_CMD_PORT
              << " TEL UDP:" << fc_tel_host << ":" << fc_tel_port << " RC sink=" << rc_sink->name()
              << "\n";

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

    // Parallel latch for RC-sink degradation (boot-probe failure or
    // persistent write failure flipped MspRcSink::ok() to false). Kept
    // SEPARATE from failsafe_engaged so the recovery branches don't
    // cross-clear: a CMD-link recovery must not spuriously re-enable
    // mode transitions while MSP is still wedged, and an MSP recovery
    // must not silently leave LandSafely while CMD is also stale. Each
    // condition has its own entry/exit log so the operator can see
    // which one tripped and which one cleared.
    bool sink_degraded_engaged = false;

    const auto period = std::chrono::milliseconds(1000 / kTelHz);
    auto next_tick = std::chrono::steady_clock::now();
    auto last_tick = next_tick;

    while (g_running.load()) {
        const auto now = std::chrono::steady_clock::now();
        const double dt = std::chrono::duration<double>(now - last_tick).count();
        last_tick = now;

        fc::CommandFrame cmd;
        // latest_command and seconds_since_last_cmd are read separately, not
        // from a single coherent snapshot. That's intentional: a writer commit
        // between the two reads strictly improves staleness accuracy (cmd_age
        // below reflects the freshest network frame, even if a newer CMD
        // raced in after this seq read). The atomic-publish fix in
        // CommandServer just guarantees each call returns a self-consistent
        // value; main.cpp doesn't need cross-call atomicity here.
        constexpr std::int32_t kCmdSeqMaxFc = 0x7FFFFFFF; // mirrors backend CMD_SEQ_MAX
        const bool have_cmd = command_server.latest_command(cmd);
        const bool seq_in_range = have_cmd && cmd.seq >= 0 && cmd.seq <= kCmdSeqMaxFc;
        if (have_cmd && !seq_in_range) {
            // Out-of-range seqs (negative or > CMD_SEQ_MAX) violate the wire
            // contract. seq_advances would still produce a deterministic
            // boolean, but the modular comparison is undefined outside
            // [0, CMD_SEQ_MAX] — drop with a log instead of letting a
            // malformed CMD poison last_cmd_seq.
            std::cerr << "[FC] Dropped CMD with out-of-range seq=" << cmd.seq << " (must be in [0, "
                      << kCmdSeqMaxFc << "])\n";
        }
        if (seq_in_range &&
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
                apply_control_mode_safely(desired_mode, flight_controller, *rc_sink,
                                          static_cast<std::int32_t>(cmd.seq));
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
            // Clear the latch only — don't auto-resume any prior mode. A
            // fresh CMD earlier in this same tick may have already updated
            // the control mode (the CMD-application block above runs
            // before this stale check); otherwise the controller stays in
            // whatever mode it's currently in (typically LandSafely from
            // the entry-side branch). Auto-resume of a saved pre-failsafe
            // mode is the deferred two-stage state machine in S0.7.
            std::cerr << "[FC] CMD link recovered (age=" << cmd_age_s
                      << "s); cleared failsafe latch (sink_degraded still engaged: "
                      << (sink_degraded_engaged ? "yes" : "no")
                      << "); mode unchanged unless a CMD already applied this tick\n";
        }

        // Parallel sink-degraded latch (S0.8). MspRcSink flips ok()=false
        // when its USB write loop persists in failure or its boot probe
        // didn't reply; that's distinct from CMD-link staleness above and
        // tracked separately so the two recovery paths don't interfere.
        // Both paths converge on LandSafely as the safe target.
        const bool sink_degraded_now = !rc_sink->ok();
        if (sink_degraded_now && !sink_degraded_engaged) {
            sink_degraded_engaged = true;
            flight_controller.setControlMode(fc::ControlMode::LandSafely);
            std::cerr << "[FC] RC sink degraded (ok=false, tx_ratio=" << rc_sink->tx_ratio()
                      << "); entering LandSafely failsafe\n";
        } else if (!sink_degraded_now && sink_degraded_engaged) {
            sink_degraded_engaged = false;
            std::cerr << "[FC] RC sink recovered; cleared sink-degraded latch (CMD-stale "
                         "failsafe still engaged: "
                      << (failsafe_engaged ? "yes" : "no")
                      << "); mode unchanged unless a CMD already applied this tick\n";
        }

        const fc::BetaFlightCommand rc_cmd = flight_controller.updateTimeStep(dt);

        const fc::TelemetryData& telemetry = flight_controller.getTelemetryData();
        rc_sink->writeChannels(rc_cmd, telemetry.timestamp_s);
        const fc::TrackingMessage& tracking_message = flight_controller.getLastTrackingMessage();

        const std::string tel_json =
            build_tel_json(tel_seq, telemetry, tracking_message, cmd_age_s, rc_sink->tx_ratio());
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
