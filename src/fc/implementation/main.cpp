#include "CommandServer.h"
#include "ProtocolConstants.h"
#include "TelemetryPublisher.h"

#include <atomic>
#include <chrono>
#include <csignal>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <thread>

namespace {

constexpr int kControlModeLandSafely = 2;
constexpr int kTrackingStateTracking = 3;
constexpr int kTrackingStateSearching = 4;
constexpr int kTelHz = 50;

std::atomic<bool> g_running{true};

double monotonic_now_s() {
    using clock = std::chrono::steady_clock;
    // timestamp_s is monotonic for ordering/diagnostics, not wall-clock Unix time.
    return std::chrono::duration<double>(clock::now().time_since_epoch()).count();
}

void handle_signal(int) {
    g_running.store(false);
}

std::string build_tel_json(uint64_t seq, int control_mode, int tracking_state) {
    double target_x = 0.0;
    double target_y = 0.0;
    double bound_w = 0.0;
    double bound_h = 0.0;
    double confidence = 0.0;

    if (tracking_state == kTrackingStateTracking) {
        // Tracking values will be wired from vision in a later PR.
        target_x = 0.0;
        target_y = 0.0;
        bound_w = 0.0;
        bound_h = 0.0;
        confidence = 0.0;
    }

    std::ostringstream oss;
    oss << std::fixed << std::setprecision(6);
    oss << "{"
        << "\"type\":\"TEL\","
        << "\"seq\":" << seq << ","
        << "\"timestamp_s\":" << monotonic_now_s() << ","
        << "\"control_mode\":" << control_mode << ","
        << "\"tracking_state\":" << tracking_state << ","
        << "\"distFront_m\":0.0,"
        << "\"distBack_m\":0.0,"
        << "\"distBottom_m\":0.0,"
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

    std::cout << "[FC] fc_app running. CMD TCP:" << proto::TCP_CMD_PORT
              << " TEL UDP:127.0.0.1:" << proto::UDP_TEL_PORT << "\n";

    int control_mode = kControlModeLandSafely;
    int tracking_state = kTrackingStateSearching;
    int last_cmd_seq = -1;
    uint64_t tel_seq = 0;

    const auto period = std::chrono::milliseconds(1000 / kTelHz);
    auto next_tick = std::chrono::steady_clock::now();

    while (g_running.load()) {
        fc::CommandFrame cmd;
        if (command_server.latest_command(cmd) && cmd.seq != last_cmd_seq) {
            last_cmd_seq = cmd.seq;
            control_mode = cmd.desired_mode;
            std::cout << "[FC] Applied desired_mode=" << control_mode << " from CMD seq=" << cmd.seq
                      << "\n";
        }

        if (command_server.seconds_since_last_cmd() > proto::CMD_TIMEOUT_S) {
            control_mode = kControlModeLandSafely;
        }

        const std::string tel_json = build_tel_json(tel_seq, control_mode, tracking_state);
        telemetry_publisher.send_json(tel_json);

        if ((tel_seq % 25U) == 0U) {
            std::cout << "[FC] TEL seq=" << tel_seq << " mode=" << control_mode
                      << " tracking_state=" << tracking_state << "\n";
        }
        ++tel_seq;

        next_tick += period;
        std::this_thread::sleep_until(next_tick);
    }

    command_server.stop();
    std::cout << "[FC] fc_app stopped\n";
    return 0;
}
