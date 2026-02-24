#pragma once

#include <string>

namespace fc {

struct CommandSetpoints {
    bool has_roll = false;
    double roll = 0.0;

    bool has_pitch = false;
    double pitch = 0.0;

    bool has_yaw_rate = false;
    double yaw_rate = 0.0;

    bool has_throttle = false;
    double throttle = 0.0;
};

struct CommandFrame {
    int seq = 0;
    double timestamp_s = 0.0;
    int desired_mode = 0;

    bool has_arm = false;
    bool arm = false;

    CommandSetpoints setpoints{};
    std::string raw_json;
};

// Parse a JSON command payload and extract the minimum required fields:
// {"type":"CMD","seq":<int>,"timestamp_s":<float>,"desired_mode":<int>}
bool parse_cmd_frame(const std::string& json, CommandFrame& out);

} // namespace fc
