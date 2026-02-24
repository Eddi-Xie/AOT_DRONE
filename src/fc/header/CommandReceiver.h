#pragma once

#include <string>

namespace fc {

struct CommandFrame {
    int seq = 0;
    double timestamp_s = 0.0;
    int desired_mode = 0;
    std::string raw_json;
};

// Parse a JSON command payload and extract the minimum required fields:
// {"type":"CMD","seq":<int>,"timestamp_s":<float>,"desired_mode":<int>}
bool parse_cmd_frame(const std::string& json, CommandFrame& out);

} // namespace fc
