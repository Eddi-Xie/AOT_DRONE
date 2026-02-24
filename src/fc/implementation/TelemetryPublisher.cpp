#include "TelemetryPublisher.h"
#include "ProtocolConstants.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstdint>

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
        return false;
    }

    auto* addr = static_cast<sockaddr_in*>(addr_);
    const ssize_t sent = ::sendto(sock_, json.data(), json.size(), 0,
                                  reinterpret_cast<sockaddr*>(addr), sizeof(*addr));
    return sent == static_cast<ssize_t>(json.size());
}

} // namespace fc
