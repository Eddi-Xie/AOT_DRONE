#pragma once
#include "CommandReceiver.h"

#include <atomic>
#include <mutex>
#include <string>
#include <thread>

namespace fc {

class CommandServer {
  public:
    // bind_host defaults to "127.0.0.1" so the FC TCP listener is loopback-
    // only out of the box. Operators that intentionally need cross-host CMD
    // ingress (e.g. dev with backend on a different machine) pass "0.0.0.0"
    // explicitly. main.cpp drives this from the FC_BIND_HOST env var.
    explicit CommandServer(int port, std::string bind_host = "127.0.0.1");
    ~CommandServer();
    bool start();
    void stop();

    // Latest valid command JSON frame
    std::string last_cmd_json() const;

    // Latest parsed valid command.
    bool latest_command(CommandFrame& out_cmd) const;

    // Seconds since last valid command frame (monotonic time in seconds).
    double seconds_since_last_cmd() const;

  private:
    int port_;
    std::string bind_host_;
    int listen_fd_ = -1;
    int client_fd_ = -1;
    std::thread worker_;
    mutable std::mutex socket_mutex_;

    std::atomic<bool> running_{false};
    mutable std::mutex cmd_mutex_;
    std::string last_json_;
    CommandFrame last_cmd_;
    bool has_cmd_{false};
    std::atomic<double> last_cmd_time_s_{-1.0};

    void run_loop();
};

} // namespace fc
