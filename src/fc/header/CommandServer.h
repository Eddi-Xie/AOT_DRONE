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

    // Latest parsed command + the monotonic timestamp at which it landed,
    // returned together under a single mutex acquisition. Use this when a
    // caller needs a coherent snapshot of both values in the same decision
    // (for example, a metrics/log builder that pairs cmd.seq with the
    // recv-time on the same line). Calling latest_command() and
    // seconds_since_last_cmd() separately races against the writer and can
    // observe a mismatched (cmd, time) pair.
    //
    // main.cpp deliberately uses the separate-call pattern: a writer commit
    // between the two reads strictly improves staleness accuracy (cmd_age
    // reflects the freshest network frame, even if a newer CMD raced in
    // after the seq read), and main.cpp doesn't make a single decision that
    // depends on both values being from the same snapshot.
    bool latest_command_with_time(CommandFrame& out_cmd, double& out_time_s) const;

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
    // last_cmd_time_s_ used to be std::atomic<double> stored OUTSIDE
    // cmd_mutex_, which let a reader observe `last_cmd_` from one CMD and
    // `last_cmd_time_s_` from another (small window between the lock release
    // and the atomic store). Now lives under the same mutex as everything
    // else for a coherent snapshot.
    double last_cmd_time_s_{-1.0};

    void run_loop();
};

} // namespace fc
