#pragma once

// MSPv1 (MultiWii Serial Protocol v1) framing helpers — pure encode/parse,
// no I/O, no globals. The serial transport lives in MspRcSink; this header
// stays I/O-free so its tests run as plain unit tests with no fd/socket
// fixtures and the framing logic can be reasoned about in isolation.
//
// Frame layout (per the MSPv1 spec used by Betaflight):
//
//   request:  $ M < <size> <cmd> <payload...>      <xor>
//   response: $ M > <size> <cmd> <payload...>      <xor>
//
//   - <size>:  1 byte — payload length only (0..255). Header, cmd, and
//              checksum bytes are NOT counted.
//   - <cmd>:   1 byte — message id.
//   - <xor>:   1 byte — XOR over [<size>, <cmd>, <payload...>]. The three
//              header bytes ($, M, < / >) are NOT included.
//
// Larger payloads (>255 bytes) require MSPv2 and are out of scope for
// S0.8 — we never send anything bigger than MSP_SET_RAW_RC's 16-byte
// payload, so v1 is sufficient.

#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace fc::msp {

// MSPv1 command IDs we care about for S0.8.
//   - MSP_API_VERSION (1):  boot probe — confirms the FC is alive and
//                            speaks MSP. Failure => MspRcSink::ok() flips
//                            false, fc_app refuses to start.
//   - MSP_RC          (105): query current RC channels (used to read
//                            the arm-switch bit for S0.14).
//   - MSP_RC_TUNING   (111): query rate profile — log + WARNING on
//                            mismatch; gates Tracking/Takeoff transitions.
//   - MSP_SET_RAW_RC  (200): per-tick channel write at 50 Hz.
inline constexpr std::uint8_t MSP_API_VERSION = 1;
inline constexpr std::uint8_t MSP_RC = 105;
inline constexpr std::uint8_t MSP_RC_TUNING = 111;
inline constexpr std::uint8_t MSP_SET_RAW_RC = 200;

// MSP_SET_RAW_RC carries 8 channels × uint16 LE = 16 bytes of payload.
inline constexpr std::size_t MSP_SET_RAW_RC_CHANNEL_COUNT = 8;
inline constexpr std::size_t MSP_SET_RAW_RC_PAYLOAD_BYTES =
    MSP_SET_RAW_RC_CHANNEL_COUNT * sizeof(std::uint16_t);

// Defence-in-depth clamp range applied by encode_set_raw_rc. Upstream
// FlightController already clamps to DRONE_MIN/DRONE_MAX (1000/2000 µs);
// we re-clamp here so a bug elsewhere can't push out-of-range values onto
// the wire and trip Betaflight's failsafe in surprising ways.
inline constexpr std::uint16_t MSP_RC_CHANNEL_MIN_US = 1000;
inline constexpr std::uint16_t MSP_RC_CHANNEL_MAX_US = 2000;

// XOR checksum over [size, cmd, payload...]. Caller MUST NOT include the
// $M< / $M> header bytes — those are excluded from the MSPv1 checksum.
std::uint8_t xor_checksum(const std::uint8_t* buf, std::size_t n);

// Encode an MSPv1 request frame ($M<). Returns the full byte sequence
// header-through-checksum, ready to write() to the serial fd.
//
// `payload` may be nullptr when `n == 0`. `n` MUST be <= 255 — the size
// field is uint8_t and MSPv1 doesn't carry larger payloads. Inputs with
// `n > 255` are silently truncated to 255 (the only callers in this
// project pass either 0 or 16 bytes, so the truncation path is
// unreachable in practice — it exists to keep the function total).
std::vector<std::uint8_t> encode_request(std::uint8_t cmd, const std::uint8_t* payload,
                                         std::size_t n);

// Convenience: encode MSP_SET_RAW_RC for the standard 8-channel RC frame.
// Channels are written little-endian. Each value is clamped to
// [MSP_RC_CHANNEL_MIN_US, MSP_RC_CHANNEL_MAX_US] before encoding.
std::vector<std::uint8_t>
encode_set_raw_rc(const std::uint16_t channels[MSP_SET_RAW_RC_CHANNEL_COUNT]);

// One parsed MSPv1 response. `payload` may be empty for zero-payload
// replies (e.g. an ack).
struct ParsedFrame {
    std::uint8_t cmd = 0;
    std::vector<std::uint8_t> payload;
};

// Result of one parse_response() call.
//
//   - frame: set iff a complete, checksum-valid response was extracted.
//   - bytes_consumed: how many bytes of `buf` the caller should drain.
//                     Includes any leading garbage skipped before the
//                     $M> marker AND the full frame on success or on
//                     checksum failure. The caller's buffer should retain
//                     `buf[bytes_consumed..n)` for the next call.
//   - checksum_error: a frame was found but its checksum did not match.
//                     `frame` is nullopt; `bytes_consumed` covers the
//                     bad frame so the caller resyncs past it.
struct ParseResult {
    std::optional<ParsedFrame> frame;
    std::size_t bytes_consumed = 0;
    bool checksum_error = false;
};

// Incremental MSPv1 response parser ($M>).
//
// Behaviour:
//   - Skips any leading bytes up to the $M> marker (treated as garbage,
//     consumed but no frame produced).
//   - On a partial frame (header found but not enough bytes for the
//     full payload yet), returns bytes_consumed pointing at the start of
//     the partial frame so the caller keeps it and waits for more bytes.
//   - On a complete, valid frame: returns the parsed cmd + payload and
//     bytes_consumed = end-of-frame.
//   - On a complete frame with bad checksum: returns checksum_error=true,
//     bytes_consumed = end-of-frame, frame = nullopt.
//   - On an empty or too-short buffer (n < 3): returns nothing and does
//     not consume — those bytes might still be the start of a header.
//
// One frame per call. Loop on the caller side to drain a backlog.
ParseResult parse_response(const std::uint8_t* buf, std::size_t n);

} // namespace fc::msp
