#pragma once
#include "CommandReceiver.h"

#include <atomic>
#include <mutex>
#include <string>

namespace fc {

class CommandServer {
  public:
    explicit CommandServer(int port);
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
    int listen_fd_ = -1;
    int client_fd_ = -1;

    std::atomic<bool> running_{false};
    mutable std::mutex cmd_mutex_;
    std::string last_json_;
    CommandFrame last_cmd_;
    bool has_cmd_{false};
    std::atomic<double> last_cmd_time_s_{-1.0};

    void run_loop();
};

} // namespace fc
