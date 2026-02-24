#include "TelemetryPublisher.h"
#include "ProtocolConstants.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstdint>
#include <iostream>

namespace {

double monotonic_now_s() {
    using clock = std::chrono::steady_clock;
    return std::chrono::duration<double>(clock::now().time_since_epoch()).count();
}

bool should_log(double now_s, double& last_log_s, double interval_s) {
    if (last_log_s < 0.0 || (now_s - last_log_s) >= interval_s) {
        last_log_s = now_s;
        return true;
    }
    return false;
}

} // namespace

namespace fc {

TelemetryPublisher::TelemetryPublisher(const std::string& ip, int port) {
    sock_ = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (sock_ < 0) {
        return;
    }

    auto* addr = new sockaddr_in{};
    addr->sin_family = AF_INET;
    addr->sin_port = htons(static_cast<uint16_t>(port));
    if (::inet_pton(AF_INET, ip.c_str(), &addr->sin_addr) != 1) {
        ::close(sock_);
        sock_ = -1;
        delete addr;
        return;
    }
    addr_ = addr;
}

TelemetryPublisher::~TelemetryPublisher() {
    if (sock_ >= 0) {
        ::close(sock_);
    }
    delete static_cast<sockaddr_in*>(addr_);
    addr_ = nullptr;
}

bool TelemetryPublisher::send_json(const std::string& json) {
    if (sock_ < 0 || addr_ == nullptr) {
        return false;
    }
    if (json.empty() || json.size() > static_cast<size_t>(proto::UDP_MAX_TEL_BYTES)) {
        const double now_s = monotonic_now_s();
        if (should_log(now_s, last_oversize_log_s_, 1.0)) {
            std::cerr << "[FC] Dropped TEL payload size=" << json.size()
                      << " (max=" << proto::UDP_MAX_TEL_BYTES << ")\n";
        }
        return false;
    }

    auto* addr = static_cast<sockaddr_in*>(addr_);
    const ssize_t sent = ::sendto(sock_, json.data(), json.size(), 0,
                                  reinterpret_cast<sockaddr*>(addr), sizeof(*addr));
    if (sent != static_cast<ssize_t>(json.size())) {
        const double now_s = monotonic_now_s();
        if (should_log(now_s, last_send_fail_log_s_, 1.0)) {
            std::cerr << "[FC] UDP telemetry send failed\n";
        }
        return false;
    }
    return true;
}

} // namespace fc
