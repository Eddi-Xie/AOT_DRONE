#include "MspFraming.h"

namespace fc::msp {

std::uint8_t xor_checksum(const std::uint8_t* buf, std::size_t n) {
    std::uint8_t cksum = 0;
    for (std::size_t i = 0; i < n; ++i) {
        cksum ^= buf[i];
    }
    return cksum;
}

std::vector<std::uint8_t> encode_request(std::uint8_t cmd, const std::uint8_t* payload,
                                         std::size_t n) {
    // MSPv1's size field is one byte. Anything larger needs MSPv2 (which
    // uses a different framing entirely). We don't send anything close to
    // 255 in this project, but truncating rather than UB-ing keeps the
    // function total.
    const std::uint8_t size = (n > 255U) ? 255U : static_cast<std::uint8_t>(n);

    std::vector<std::uint8_t> frame;
    // Header (3) + size (1) + cmd (1) + payload (size) + checksum (1).
    frame.reserve(static_cast<std::size_t>(6) + size);
    frame.push_back('$');
    frame.push_back('M');
    frame.push_back('<');
    frame.push_back(size);
    frame.push_back(cmd);

    std::uint8_t cksum = static_cast<std::uint8_t>(size ^ cmd);
    for (std::size_t i = 0; i < size; ++i) {
        frame.push_back(payload[i]);
        cksum ^= payload[i];
    }
    frame.push_back(cksum);
    return frame;
}

std::vector<std::uint8_t>
encode_set_raw_rc(const std::uint16_t channels[MSP_SET_RAW_RC_CHANNEL_COUNT]) {
    std::uint8_t payload[MSP_SET_RAW_RC_PAYLOAD_BYTES];
    for (std::size_t i = 0; i < MSP_SET_RAW_RC_CHANNEL_COUNT; ++i) {
        std::uint16_t v = channels[i];
        if (v < MSP_RC_CHANNEL_MIN_US) {
            v = MSP_RC_CHANNEL_MIN_US;
        } else if (v > MSP_RC_CHANNEL_MAX_US) {
            v = MSP_RC_CHANNEL_MAX_US;
        }
        payload[2 * i] = static_cast<std::uint8_t>(v & 0xFFU);
        payload[2 * i + 1] = static_cast<std::uint8_t>((v >> 8) & 0xFFU);
    }
    return encode_request(MSP_SET_RAW_RC, payload, sizeof(payload));
}

ParseResult parse_response(const std::uint8_t* buf, std::size_t n) {
    ParseResult result;

    // Need at least the 3-byte $M> marker before we can tell whether any
    // garbage in front is droppable. Anything shorter is fully retained
    // for the next call.
    if (n < 3) {
        return result;
    }

    // Scan for $M>. Stop at the last position where a 3-byte marker could
    // still fit; if no match, the trailing 2 bytes are preserved (they
    // could be the start of a future header).
    std::size_t i = 0;
    bool found_header = false;
    while (i + 3 <= n) {
        if (buf[i] == '$' && buf[i + 1] == 'M' && buf[i + 2] == '>') {
            found_header = true;
            break;
        }
        ++i;
    }
    if (!found_header) {
        // Drop the unambiguously-not-header prefix; keep up to 2 trailing
        // bytes since they could be the start of a header still arriving.
        result.bytes_consumed = i;
        return result;
    }

    // Need 2 more bytes after the marker (size, cmd) to know payload length.
    if (i + 5 > n) {
        result.bytes_consumed = i; // preserve from the $ onward
        return result;
    }

    const std::uint8_t size = buf[i + 3];
    const std::uint8_t cmd = buf[i + 4];
    // Total frame bytes: $M> (3) + size (1) + cmd (1) + payload + cksum (1).
    const std::size_t frame_total = 3U + 1U + 1U + size + 1U;
    if (i + frame_total > n) {
        result.bytes_consumed = i; // wait for the full payload + checksum
        return result;
    }

    std::uint8_t cksum = static_cast<std::uint8_t>(size ^ cmd);
    for (std::size_t j = 0; j < size; ++j) {
        cksum ^= buf[i + 5 + j];
    }
    const std::uint8_t expected = buf[i + 5 + size];
    const std::size_t end = i + frame_total;

    if (cksum != expected) {
        // Bad frame: consume past it so the caller resyncs. Surface the
        // diagnostic flag for logging without raising it to a fatal.
        result.bytes_consumed = end;
        result.checksum_error = true;
        return result;
    }

    ParsedFrame f;
    f.cmd = cmd;
    f.payload.assign(buf + i + 5, buf + i + 5 + size);
    result.frame = std::move(f);
    result.bytes_consumed = end;
    return result;
}

} // namespace fc::msp
