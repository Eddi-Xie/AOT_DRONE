#pragma once

#include <string>

namespace fc {

class TelemetryPublisher {
  public:
    TelemetryPublisher(const std::string& ip, int port);
    ~TelemetryPublisher();

    TelemetryPublisher(const TelemetryPublisher&) = delete;
    TelemetryPublisher& operator=(const TelemetryPublisher&) = delete;

    bool ok() const { return sock_ >= 0; }
    bool send_json(const std::string& json);

  private:
    int sock_ = -1;
    void* addr_ = nullptr; // sockaddr_in owned by this object
    double last_oversize_log_s_ = -1.0;
    double last_send_fail_log_s_ = -1.0;
};

} // namespace fc
