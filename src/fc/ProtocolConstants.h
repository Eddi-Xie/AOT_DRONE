#pragma once

namespace proto {

// Ports
constexpr int UDP_TEL_PORT = 9001;
constexpr int UDP_VIS_PORT = 9003;
constexpr int TCP_CMD_PORT = 9002;

// Limits
constexpr int UDP_MAX_TEL_BYTES = 1024;
constexpr int UDP_MAX_VIS_BYTES = 512;
constexpr int TCP_MAX_FRAME_BYTES = 4096;

// Rates / timeouts
constexpr double CMD_TIMEOUT_S = 0.5;

} // namespace proto
