#pragma once
#include <atomic>
#include <string>

namespace fc {

class CommandServer {
  public:
    explicit CommandServer(int port);
    bool start();
    void stop();

    // Latest command JSON frame
    std::string last_cmd_json() const;

    // Seconds since last valid frame (monotonic wall-time in seconds)
    double seconds_since_last_cmd() const;

  private:
    int port_;
    int listen_fd_ = -1;
    int client_fd_ = -1;

    std::atomic<bool> running_{false};
    mutable std::string last_json_;
    std::atomic<double> last_cmd_time_s_{0.0};

    void run_loop();
};

} // namespace fc
