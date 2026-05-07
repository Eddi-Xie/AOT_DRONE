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

bool should_log(double now_s_value, double& last_log_s, double interval_s) {
    if (last_log_s < 0.0 || (now_s_value - last_log_s) >= interval_s) {
        last_log_s = now_s_value;
        return true;
    }
    return false;
}

void shutdown_and_close_fd(int& fd) {
    if (fd < 0) {
        return;
    }
    ::shutdown(fd, SHUT_RDWR);
    ::close(fd);
    fd = -1;
}
} // namespace

namespace fc {

CommandServer::CommandServer(int port, std::string bind_host)
    : port_(port), bind_host_(std::move(bind_host)) {}

CommandServer::~CommandServer() {
    stop();
}

bool CommandServer::start() {
    if (running_.load()) {
        return true;
    }

    const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return false;
    }

    int yes = 1;
    ::setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<uint16_t>(port_));
    if (bind_host_.empty() || bind_host_ == "0.0.0.0") {
        addr.sin_addr.s_addr = INADDR_ANY;
    } else if (inet_pton(AF_INET, bind_host_.c_str(), &addr.sin_addr) != 1) {
        std::cerr << "[FC] CommandServer: invalid bind host '" << bind_host_
                  << "', refusing to start (use 127.0.0.1 or 0.0.0.0)\n";
        ::close(fd);
        return false;
    }

    if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        ::close(fd);
        return false;
    }

    if (::listen(fd, 1) < 0) {
        ::close(fd);
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(socket_mutex_);
        listen_fd_ = fd;
    }

    running_.store(true);
    worker_ = std::thread(&CommandServer::run_loop, this);
    return true;
}

void CommandServer::stop() {
    running_.store(false);
    {
        std::lock_guard<std::mutex> lock(socket_mutex_);
        shutdown_and_close_fd(client_fd_);
        shutdown_and_close_fd(listen_fd_);
    }

    if (worker_.joinable()) {
        worker_.join();
    }
}

void CommandServer::run_loop() {
    double last_link_lost_log_s = -1.0;
    while (running_.load()) {
        std::cout << "[FC] Waiting for TCP command client on :" << port_ << "\n";
        int listen_fd_snapshot = -1;
        {
            std::lock_guard<std::mutex> lock(socket_mutex_);
            listen_fd_snapshot = listen_fd_;
        }

        if (listen_fd_snapshot < 0) {
            break;
        }

        const int accepted_fd = ::accept(listen_fd_snapshot, nullptr, nullptr);
        if (accepted_fd < 0) {
            if (running_.load()) {
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
            }
            continue;
        }

        {
            std::lock_guard<std::mutex> lock(socket_mutex_);
            if (!running_.load()) {
                ::close(accepted_fd);
                break;
            }
            shutdown_and_close_fd(client_fd_);
            client_fd_ = accepted_fd;
        }

        std::cout << "[FC] Backend connected\n";
        while (running_.load()) {
            int client_fd_snapshot = -1;
            {
                std::lock_guard<std::mutex> lock(socket_mutex_);
                client_fd_snapshot = client_fd_;
            }
            if (client_fd_snapshot < 0) {
                break;
            }

            std::string json;
            if (!read_frame(client_fd_snapshot, json,
                            static_cast<uint32_t>(proto::TCP_MAX_FRAME_BYTES))) {
                const double t_s = now_s();
                if (should_log(t_s, last_link_lost_log_s, 1.0)) {
                    std::cout << "[FC] Command link lost / invalid frame\n";
                }
                {
                    std::lock_guard<std::mutex> lock(socket_mutex_);
                    if (client_fd_ == client_fd_snapshot) {
                        shutdown_and_close_fd(client_fd_);
                    }
                }
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

    std::lock_guard<std::mutex> lock(socket_mutex_);
    shutdown_and_close_fd(client_fd_);
    shutdown_and_close_fd(listen_fd_);
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
