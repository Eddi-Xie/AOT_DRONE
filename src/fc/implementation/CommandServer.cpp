#include "CommandServer.h"

#include "FrameCodec.h"
#include "ProtocolConstants.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstdint>
#include <iostream>
#include <thread>

namespace {
double now_s() {
    using clock = std::chrono::steady_clock;
    return std::chrono::duration<double>(clock::now().time_since_epoch()).count();
}
} // namespace

namespace fc {

CommandServer::CommandServer(int port) : port_(port) {}

bool CommandServer::start() {
    listen_fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd_ < 0) {
        return false;
    }

    int yes = 1;
    ::setsockopt(listen_fd_, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(static_cast<uint16_t>(port_));

    if (::bind(listen_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        ::close(listen_fd_);
        listen_fd_ = -1;
        return false;
    }

    if (::listen(listen_fd_, 1) < 0) {
        ::close(listen_fd_);
        listen_fd_ = -1;
        return false;
    }

    running_.store(true);
    std::thread(&CommandServer::run_loop, this).detach();
    return true;
}

void CommandServer::stop() {
    running_.store(false);
    if (client_fd_ >= 0) {
        ::close(client_fd_);
    }
    if (listen_fd_ >= 0) {
        ::close(listen_fd_);
    }
    client_fd_ = -1;
    listen_fd_ = -1;
}

void CommandServer::run_loop() {
    while (running_.load()) {
        std::cout << "[FC] Waiting for TCP command client on :" << port_ << "\n";
        client_fd_ = ::accept(listen_fd_, nullptr, nullptr);
        if (client_fd_ < 0) {
            if (running_.load()) {
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
            }
            continue;
        }

        std::cout << "[FC] Backend connected\n";
        while (running_.load()) {
            std::string json;
            if (!read_frame(client_fd_, json, static_cast<uint32_t>(proto::TCP_MAX_FRAME_BYTES))) {
                std::cout << "[FC] Command link lost / invalid frame\n";
                ::close(client_fd_);
                client_fd_ = -1;
                break;
            }

            CommandFrame parsed;
            if (!parse_cmd_frame(json, parsed)) {
                std::cout << "[FC] Dropped invalid CMD payload\n";
                continue;
            }

            {
                std::lock_guard<std::mutex> lock(cmd_mutex_);
                has_cmd_ = true;
                last_json_ = json;
                last_cmd_ = parsed;
            }
            last_cmd_time_s_.store(now_s());

            std::cout << "[FC] CMD seq=" << parsed.seq << " desired_mode=" << parsed.desired_mode
                      << "\n";
        }
    }
}

std::string CommandServer::last_cmd_json() const {
    std::lock_guard<std::mutex> lock(cmd_mutex_);
    return last_json_;
}

bool CommandServer::latest_command(CommandFrame& out_cmd) const {
    std::lock_guard<std::mutex> lock(cmd_mutex_);
    if (!has_cmd_) {
        return false;
    }
    out_cmd = last_cmd_;
    return true;
}

double CommandServer::seconds_since_last_cmd() const {
    const double t = last_cmd_time_s_.load();
    if (t <= 0.0) {
        return 1e9;
    }
    return now_s() - t;
}

} // namespace fc
