// Unit tests for the MspFraming MSPv1 encode/parse helpers. Pure tests —
// no fds, no sockets, no fixtures. The goal is to pin the exact byte
// sequence the FC will put on the USB wire so a future refactor that
// shuffles checksum logic or endianness trips CI before it reaches a
// real Betaflight.

#include "MspFraming.h"
#include "test_assert.h"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace {

// Build a $M> response frame inline — used as an input to parse_response
// since the parser only handles inbound (response-direction) frames.
std::vector<std::uint8_t> build_response(std::uint8_t cmd,
                                         const std::vector<std::uint8_t>& payload) {
    std::vector<std::uint8_t> f;
    f.reserve(6 + payload.size());
    f.push_back('$');
    f.push_back('M');
    f.push_back('>');
    const std::uint8_t size = static_cast<std::uint8_t>(payload.size());
    f.push_back(size);
    f.push_back(cmd);
    std::uint8_t cksum = static_cast<std::uint8_t>(size ^ cmd);
    for (std::uint8_t b : payload) {
        f.push_back(b);
        cksum ^= b;
    }
    f.push_back(cksum);
    return f;
}

void test_xor_checksum_basic() {
    // Empty input is the identity (0x00) so a zero-payload frame's
    // checksum is just size ^ cmd.
    TEST_ASSERT(fc::msp::xor_checksum(nullptr, 0) == 0x00);

    const std::uint8_t a[] = {0x10, 0xC8};
    TEST_ASSERT(fc::msp::xor_checksum(a, sizeof(a)) == 0xD8);

    // Pairs of equal bytes XOR to zero — sanity-check the property the
    // all-1500 frame test below relies on.
    const std::uint8_t b[] = {0xDC, 0x05, 0xDC, 0x05};
    TEST_ASSERT(fc::msp::xor_checksum(b, sizeof(b)) == 0x00);
}

void test_encode_request_msp_api_version() {
    // MSP_API_VERSION (cmd=1) with no payload. Spec-defined byte sequence
    // we can hand-verify: $M< 00 01 01 (size=0, cmd=1, xor = size^cmd = 1).
    const auto frame = fc::msp::encode_request(fc::msp::MSP_API_VERSION, nullptr, 0);
    TEST_ASSERT(frame.size() == 6U);
    TEST_ASSERT(frame[0] == 0x24); // $
    TEST_ASSERT(frame[1] == 0x4D); // M
    TEST_ASSERT(frame[2] == 0x3C); // <
    TEST_ASSERT(frame[3] == 0x00); // size
    TEST_ASSERT(frame[4] == 0x01); // cmd = MSP_API_VERSION
    TEST_ASSERT(frame[5] == 0x01); // xor(0, 1) = 1
}

void test_encode_request_msp_rc_query_no_payload() {
    // MSP_RC (cmd=105=0x69) with no payload. Used by MspRcSink's 5 Hz
    // arm-switch poll.
    const auto frame = fc::msp::encode_request(fc::msp::MSP_RC, nullptr, 0);
    TEST_ASSERT(frame.size() == 6U);
    TEST_ASSERT(frame[3] == 0x00); // size
    TEST_ASSERT(frame[4] == 0x69); // cmd
    TEST_ASSERT(frame[5] == 0x69); // xor(0, 0x69) = 0x69
}

void test_encode_set_raw_rc_all_neutral() {
    // All 8 channels at 1500 µs (mid-stick). Total length is 22 bytes and
    // the checksum has a clean derivation: xor of [size=0x10, cmd=0xC8]
    // is 0xD8; eight repetitions of (0xDC ^ 0x05) cancel pairwise to 0.
    std::uint16_t channels[8] = {1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500};
    const auto frame = fc::msp::encode_set_raw_rc(channels);

    TEST_ASSERT(frame.size() == 22U);
    TEST_ASSERT(frame[0] == 0x24);
    TEST_ASSERT(frame[1] == 0x4D);
    TEST_ASSERT(frame[2] == 0x3C);
    TEST_ASSERT(frame[3] == 0x10); // size = 16
    TEST_ASSERT(frame[4] == 0xC8); // cmd = MSP_SET_RAW_RC = 200

    // Every channel encodes as DC 05 (1500 = 0x05DC, little-endian).
    for (std::size_t i = 0; i < 8; ++i) {
        TEST_ASSERT(frame[5 + 2 * i] == 0xDC);
        TEST_ASSERT(frame[5 + 2 * i + 1] == 0x05);
    }
    TEST_ASSERT(frame[21] == 0xD8);
}

void test_encode_set_raw_rc_endianness_and_clamp() {
    // ch[0]=1000 (min, encoded E8 03), ch[7]=2000 (max, encoded D0 07).
    // ch[1]=500 should clamp UP to 1000; ch[6]=3000 should clamp DOWN to 2000.
    // Defence in depth — upstream FlightController already clamps, but a
    // bug there must not put OOR values on the USB wire.
    std::uint16_t channels[8] = {1000, 500, 1500, 1500, 1500, 1500, 3000, 2000};
    const auto frame = fc::msp::encode_set_raw_rc(channels);

    TEST_ASSERT(frame.size() == 22U);

    // ch[0] = 1000 -> 0xE8 0x03
    TEST_ASSERT(frame[5] == 0xE8);
    TEST_ASSERT(frame[6] == 0x03);
    // ch[1] = 500 (below min) -> clamped to 1000 -> 0xE8 0x03
    TEST_ASSERT(frame[7] == 0xE8);
    TEST_ASSERT(frame[8] == 0x03);
    // ch[6] = 3000 (above max) -> clamped to 2000 -> 0xD0 0x07
    TEST_ASSERT(frame[5 + 2 * 6] == 0xD0);
    TEST_ASSERT(frame[5 + 2 * 6 + 1] == 0x07);
    // ch[7] = 2000 -> 0xD0 0x07
    TEST_ASSERT(frame[5 + 2 * 7] == 0xD0);
    TEST_ASSERT(frame[5 + 2 * 7 + 1] == 0x07);

    // Verify the trailing checksum is consistent with a fresh xor over
    // [size, cmd, payload]. If endianness or clamping silently changes,
    // this assertion catches the divergence even if the per-byte checks
    // above were updated incorrectly.
    std::uint8_t expected = 0x10 ^ 0xC8;
    for (std::size_t i = 0; i < 16; ++i) {
        expected ^= frame[5 + i];
    }
    TEST_ASSERT(frame[21] == expected);
}

void test_parse_response_zero_payload() {
    // A bare ack — size=0, cmd=1. Frame: 24 4D 3E 00 01 01.
    const std::vector<std::uint8_t> frame = build_response(0x01, {});
    TEST_ASSERT(frame.size() == 6U);

    const auto r = fc::msp::parse_response(frame.data(), frame.size());
    TEST_ASSERT(r.frame.has_value());
    TEST_ASSERT(!r.checksum_error);
    TEST_ASSERT(r.bytes_consumed == 6U);
    TEST_ASSERT(r.frame->cmd == 0x01);
    TEST_ASSERT(r.frame->payload.empty());
}

void test_parse_response_with_payload() {
    // Synthetic MSP_API_VERSION reply: protocol version 0, API 1.42.
    const std::vector<std::uint8_t> payload = {0x00, 0x01, 0x2A};
    const auto frame = build_response(fc::msp::MSP_API_VERSION, payload);

    const auto r = fc::msp::parse_response(frame.data(), frame.size());
    TEST_ASSERT(r.frame.has_value());
    TEST_ASSERT(!r.checksum_error);
    TEST_ASSERT(r.bytes_consumed == frame.size());
    TEST_ASSERT(r.frame->cmd == fc::msp::MSP_API_VERSION);
    TEST_ASSERT(r.frame->payload == payload);
}

void test_parse_response_rejects_bad_checksum() {
    auto frame = build_response(0x01, {0xAA, 0xBB});
    // Flip a payload bit so the trailing checksum no longer matches.
    frame[5] ^= 0x01;

    const auto r = fc::msp::parse_response(frame.data(), frame.size());
    TEST_ASSERT(!r.frame.has_value());
    TEST_ASSERT(r.checksum_error);
    // Still consumed past the bad frame so the caller resyncs instead of
    // reparsing the same garbage.
    TEST_ASSERT(r.bytes_consumed == frame.size());
}

void test_parse_response_skips_leading_garbage() {
    // Garbage prefix + valid frame. The parser should drop the prefix
    // and return the frame, with bytes_consumed covering the full buffer.
    std::vector<std::uint8_t> buf = {0x99, 0x99, 0x99};
    const auto frame = build_response(0x01, {0x42});
    buf.insert(buf.end(), frame.begin(), frame.end());

    const auto r = fc::msp::parse_response(buf.data(), buf.size());
    TEST_ASSERT(r.frame.has_value());
    TEST_ASSERT(r.frame->cmd == 0x01);
    TEST_ASSERT(r.frame->payload.size() == 1U);
    TEST_ASSERT(r.frame->payload[0] == 0x42);
    TEST_ASSERT(r.bytes_consumed == buf.size());
}

void test_parse_response_partial_frame_preserves_buffer() {
    // Header + size byte but no cmd/payload yet. The parser must NOT
    // consume the partial frame — caller will append more bytes and try
    // again.
    const std::vector<std::uint8_t> buf = {0x24, 0x4D, 0x3E, 0x03};

    const auto r = fc::msp::parse_response(buf.data(), buf.size());
    TEST_ASSERT(!r.frame.has_value());
    TEST_ASSERT(!r.checksum_error);
    TEST_ASSERT(r.bytes_consumed == 0U);
}

void test_parse_response_partial_payload_preserves_buffer() {
    // Full header + size + cmd but only part of the declared payload.
    // Frame says size=3, but we supply only 1 payload byte before EOF.
    const std::vector<std::uint8_t> buf = {0x24, 0x4D, 0x3E, 0x03, 0x01, 0xAA};

    const auto r = fc::msp::parse_response(buf.data(), buf.size());
    TEST_ASSERT(!r.frame.has_value());
    TEST_ASSERT(r.bytes_consumed == 0U);
}

void test_parse_response_drops_garbage_keeps_potential_header_tail() {
    // No '$' anywhere but enough bytes that some leading garbage can be
    // dropped. Parser keeps the trailing 2 bytes since they could be the
    // start of a future $M> arriving in the next read.
    const std::vector<std::uint8_t> buf = {0x99, 0x99, 0x99, 0x99, 0x99};

    const auto r = fc::msp::parse_response(buf.data(), buf.size());
    TEST_ASSERT(!r.frame.has_value());
    // Last 2 bytes preserved (could be $M waiting on >).
    TEST_ASSERT(r.bytes_consumed == buf.size() - 2);
}

void test_parse_response_too_short_keeps_everything() {
    // Buffers shorter than 3 bytes can't be ruled out as a partial header
    // — must be retained in full for the next call.
    const std::vector<std::uint8_t> empty = {};
    const auto r0 = fc::msp::parse_response(empty.data(), 0);
    TEST_ASSERT(!r0.frame.has_value());
    TEST_ASSERT(r0.bytes_consumed == 0U);

    const std::vector<std::uint8_t> dollar_only = {0x24};
    const auto r1 = fc::msp::parse_response(dollar_only.data(), dollar_only.size());
    TEST_ASSERT(!r1.frame.has_value());
    TEST_ASSERT(r1.bytes_consumed == 0U);

    const std::vector<std::uint8_t> dollar_m = {0x24, 0x4D};
    const auto r2 = fc::msp::parse_response(dollar_m.data(), dollar_m.size());
    TEST_ASSERT(!r2.frame.has_value());
    TEST_ASSERT(r2.bytes_consumed == 0U);
}

void test_parse_response_finds_second_dollar_after_false_start() {
    // Buffer starts with a '$' that is NOT followed by 'M>' — the parser
    // must keep scanning rather than getting stuck on the first dollar.
    std::vector<std::uint8_t> buf = {0x24, 0x4D, 0x4D}; // $MM, not $M>
    const auto frame = build_response(0x69, {0x11});
    buf.insert(buf.end(), frame.begin(), frame.end());

    const auto r = fc::msp::parse_response(buf.data(), buf.size());
    TEST_ASSERT(r.frame.has_value());
    TEST_ASSERT(r.frame->cmd == 0x69);
    TEST_ASSERT(r.bytes_consumed == buf.size());
}

} // namespace

int main() {
    test_xor_checksum_basic();
    test_encode_request_msp_api_version();
    test_encode_request_msp_rc_query_no_payload();
    test_encode_set_raw_rc_all_neutral();
    test_encode_set_raw_rc_endianness_and_clamp();
    test_parse_response_zero_payload();
    test_parse_response_with_payload();
    test_parse_response_rejects_bad_checksum();
    test_parse_response_skips_leading_garbage();
    test_parse_response_partial_frame_preserves_buffer();
    test_parse_response_partial_payload_preserves_buffer();
    test_parse_response_drops_garbage_keeps_potential_header_tail();
    test_parse_response_too_short_keeps_everything();
    test_parse_response_finds_second_dollar_after_false_start();
    return 0;
}
