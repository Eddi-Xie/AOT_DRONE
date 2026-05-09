// Unit tests for MspRcSink. The fixture is a Unix socketpair: the sink
// is constructed with one end via make_for_testing(); the test drives the
// other end as a fake Betaflight FC. This avoids touching real serial
// devices in CI but exercises the same write()/read()/non-blocking I/O
// paths the production driver uses against /dev/cu.usbmodem*.
//
// Out of explicit scope (covered by the bench-acceptance procedure in
// docs/hil.md S3.4): real-device open/termios behaviour, USB-CDC quirks,
// MSP_RC_TUNING semantic validation. Open() / cfsetispeed() / tcsetattr()
// touch /dev nodes and can't be exercised meaningfully without hardware.

#include "FlightController.h"
#include "MspFraming.h"
#include "MspRcSink.h"
#include "RcSink.h"
#include "test_assert.h"

#include <fcntl.h>
#include <signal.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstdint>
#include <cstring>
#include <vector>

namespace {

fc::BetaFlightCommand makeNeutralCommand() {
    fc::BetaFlightCommand cmd{};
    cmd.roll = 1500;
    cmd.pitch = 1500;
    cmd.yaw = 1500;
    cmd.throttle = 1100;
    cmd.aux1 = 1000;
    cmd.aux2 = 2000;
    cmd.aux3 = 1000;
    cmd.aux4 = 1000;
    return cmd;
}

// Set both ends of a socketpair to non-blocking. Production opens the
// serial device with O_NONBLOCK; mirroring it here keeps the EAGAIN /
// short-write code paths in MspRcSink::write_frame_ exercised the same
// way the real driver hits them.
void make_socketpair_nonblocking(int sv[2]) {
    for (int i = 0; i < 2; ++i) {
        const int flags = ::fcntl(sv[i], F_GETFL, 0);
        TEST_ASSERT(flags >= 0);
        TEST_ASSERT(::fcntl(sv[i], F_SETFL, flags | O_NONBLOCK) == 0);
    }
}

// Build a $M> response frame inline. Mirrors test_msp_framing.cpp's
// helper but kept private here so the two test executables don't need to
// share a header.
std::vector<std::uint8_t> build_msp_response(std::uint8_t cmd,
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

// Compose an 8-channel MSP_RC reply payload (16 bytes, uint16 LE).
std::vector<std::uint8_t> encode_msp_rc_payload(const std::uint16_t channels[8]) {
    std::vector<std::uint8_t> p;
    p.reserve(16);
    for (std::size_t i = 0; i < 8; ++i) {
        p.push_back(static_cast<std::uint8_t>(channels[i] & 0xFFU));
        p.push_back(static_cast<std::uint8_t>((channels[i] >> 8) & 0xFFU));
    }
    return p;
}

void test_make_for_testing_constructs_healthy_sink() {
    int sv[2];
    TEST_ASSERT(::socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    make_socketpair_nonblocking(sv);

    auto sink = fc::MspRcSink::make_for_testing(sv[0]);
    TEST_ASSERT(sink != nullptr);
    TEST_ASSERT(sink->ok());
    TEST_ASSERT(sink->name() == "msp");
    // EWMA init = 1.0 (optimistic), so fresh sink reads as healthy in TEL.
    TEST_ASSERT(sink->tx_ratio() == 1.0);
    TEST_ASSERT(!sink->arm_switch());
    TEST_ASSERT(!sink->tuning_mismatch());
    TEST_ASSERT(sink->writes_attempted() == 0U);
    TEST_ASSERT(sink->writes_succeeded() == 0U);

    // sink owns sv[0]; sv[1] is the test's responsibility.
    ::close(sv[1]);
}

void test_writechannels_emits_msp_set_raw_rc_frame() {
    int sv[2];
    TEST_ASSERT(::socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    make_socketpair_nonblocking(sv);

    auto sink = fc::MspRcSink::make_for_testing(sv[0]);
    TEST_ASSERT(sink->ok());

    // Distinguishable per-channel values so a future bug that swaps two
    // channel positions in writeChannels is caught. With every channel
    // at 1500 (the previous test's choice) a roll/pitch/yaw permutation
    // would still produce identical bytes 5..10.
    fc::BetaFlightCommand cmd;
    cmd.roll = 1100;
    cmd.pitch = 1200;
    cmd.yaw = 1300;
    cmd.throttle = 1400;
    cmd.aux1 = 1500;
    cmd.aux2 = 1600;
    cmd.aux3 = 1700;
    cmd.aux4 = 1800;
    sink->writeChannels(cmd, 0.020);

    // After the SET_RAW_RC heartbeat, the first writeChannels also runs
    // poll_msp_rc_ (tick_counter_ % 10 == 0 on the first call) which
    // sends an MSP_RC query. Read everything that arrived at the peer.
    std::uint8_t rx[128] = {0};
    const ssize_t n = ::read(sv[1], rx, sizeof(rx));
    TEST_ASSERT(n == 22 + 6); // SET_RAW_RC frame + trailing MSP_RC query

    // Header.
    TEST_ASSERT(rx[0] == 0x24); // $
    TEST_ASSERT(rx[1] == 0x4D); // M
    TEST_ASSERT(rx[2] == 0x3C); // <
    TEST_ASSERT(rx[3] == 0x10); // size = 16
    TEST_ASSERT(rx[4] == 0xC8); // cmd = MSP_SET_RAW_RC

    // Verify each channel encodes at the right offset, little-endian.
    // Channel order is roll, pitch, yaw, throttle, aux1, aux2, aux3, aux4
    // matching the order written into BetaFlightCommand by FlightController.
    const std::uint16_t expected[8] = {1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800};
    for (std::size_t i = 0; i < 8; ++i) {
        const std::uint16_t got =
            static_cast<std::uint16_t>(rx[5 + 2 * i]) |
            static_cast<std::uint16_t>(static_cast<std::uint16_t>(rx[5 + 2 * i + 1]) << 8);
        TEST_ASSERT(got == expected[i]);
    }

    // Verify checksum: xor over [size, cmd, payload].
    std::uint8_t cksum_expected = 0x10 ^ 0xC8;
    for (std::size_t i = 0; i < 16; ++i) {
        cksum_expected ^= rx[5 + i];
    }
    TEST_ASSERT(rx[21] == cksum_expected);

    TEST_ASSERT(sink->writes_attempted() == 1U);
    TEST_ASSERT(sink->writes_succeeded() == 1U);
    // EWMA stays at 1.0 after a single success: 0.95*1.0 + 0.05*1.0 = 1.0.
    TEST_ASSERT(sink->tx_ratio() == 1.0);

    ::close(sv[1]);
}

void test_writechannels_persistent_failure_closes_fd_and_drops_tx_ratio() {
    int sv[2];
    TEST_ASSERT(::socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    make_socketpair_nonblocking(sv);

    auto sink = fc::MspRcSink::make_for_testing(sv[0]);
    TEST_ASSERT(sink->ok());

    // Close the peer; sink writes will fail with EPIPE / ECONNRESET.
    // SIGPIPE is ignored process-wide in main() so the failed write
    // returns -1 with errno=EPIPE rather than killing the process.
    ::close(sv[1]);

    fc::BetaFlightCommand cmd = makeNeutralCommand();
    sink->writeChannels(cmd, 0.020);

    // Persistent failure path: fd closed, ok() flips false.
    TEST_ASSERT(!sink->ok());
    // EWMA after one failure on init=1.0: 0.95*1.0 + 0.05*0.0 = 0.95.
    // Strict-equality check is safe — this is the first arithmetic op
    // applied to the field, no accumulated rounding.
    TEST_ASSERT(sink->tx_ratio() == 0.95);
    TEST_ASSERT(sink->writes_attempted() == 1U);
    TEST_ASSERT(sink->writes_succeeded() == 0U);

    // Subsequent writes are no-ops (fd already < 0). Counters and
    // tx_ratio must NOT advance further — the first failure latched us
    // out of the heartbeat loop.
    sink->writeChannels(cmd, 0.040);
    TEST_ASSERT(sink->writes_attempted() == 1U);
    TEST_ASSERT(sink->tx_ratio() == 0.95);
}

void test_arm_switch_latches_when_aux1_above_threshold() {
    int sv[2];
    TEST_ASSERT(::socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    make_socketpair_nonblocking(sv);

    // Pre-stage a synthetic MSP_RC reply on the peer side. Channel 4
    // (aux1) = 1800 µs, well above the 1700 µs arm threshold.
    std::uint16_t channels_in[8] = {1500, 1500, 1500, 1500, 1800, 1500, 1500, 1500};
    const auto payload = encode_msp_rc_payload(channels_in);
    const auto reply = build_msp_response(fc::msp::MSP_RC, payload);
    const ssize_t w = ::write(sv[1], reply.data(), reply.size());
    TEST_ASSERT(w == static_cast<ssize_t>(reply.size()));

    auto sink = fc::MspRcSink::make_for_testing(sv[0]);
    TEST_ASSERT(!sink->arm_switch()); // initial state before any poll

    // First writeChannels triggers poll_msp_rc_ on tick 0, which drains
    // the pending reply and latches the aux1 state.
    fc::BetaFlightCommand cmd = makeNeutralCommand();
    sink->writeChannels(cmd, 0.020);

    TEST_ASSERT(sink->arm_switch() == true);
    TEST_ASSERT(sink->ok());

    ::close(sv[1]);
}

void test_arm_switch_latch_transitions_back_to_false_on_below_threshold_reply() {
    // Sequence: arm_switch flips true after a 1800 µs reply, then must
    // flip back to false on a subsequent 1500 µs reply. The latch is
    // not sticky — a future "require N consecutive samples" change would
    // need to update this test, which is the point.
    int sv[2];
    TEST_ASSERT(::socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    make_socketpair_nonblocking(sv);

    auto sink = fc::MspRcSink::make_for_testing(sv[0]);
    TEST_ASSERT(!sink->arm_switch());

    fc::BetaFlightCommand cmd = makeNeutralCommand();

    // Stage 1: aux1=1800 (above threshold). Trigger poll on tick 0.
    {
        std::uint16_t channels_high[8] = {1500, 1500, 1500, 1500, 1800, 1500, 1500, 1500};
        const auto payload = encode_msp_rc_payload(channels_high);
        const auto reply = build_msp_response(fc::msp::MSP_RC, payload);
        const ssize_t w = ::write(sv[1], reply.data(), reply.size());
        TEST_ASSERT(w == static_cast<ssize_t>(reply.size()));
    }
    sink->writeChannels(cmd, 0.020); // tick 0 → poll runs
    TEST_ASSERT(sink->arm_switch() == true);

    // Stage 2: drive the next 9 writeChannels (no poll) while keeping the
    // peer-side fed with a below-threshold reply. The 10th call (tick 10)
    // polls again and should flip the latch back to false.
    for (int i = 1; i <= 9; ++i) {
        sink->writeChannels(cmd, 0.020 + 0.020 * i);
    }
    TEST_ASSERT(sink->arm_switch() == true); // unchanged across non-poll ticks

    {
        std::uint16_t channels_low[8] = {1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500};
        const auto payload = encode_msp_rc_payload(channels_low);
        const auto reply = build_msp_response(fc::msp::MSP_RC, payload);
        const ssize_t w = ::write(sv[1], reply.data(), reply.size());
        TEST_ASSERT(w == static_cast<ssize_t>(reply.size()));
    }
    sink->writeChannels(cmd, 0.020 + 0.020 * 10); // tick 10 → poll runs
    TEST_ASSERT(sink->arm_switch() == false);

    ::close(sv[1]);
}

void test_arm_switch_stays_false_when_aux1_below_threshold() {
    int sv[2];
    TEST_ASSERT(::socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    make_socketpair_nonblocking(sv);

    // aux1 = 1500 (mid-stick) — below the 1700 µs ARM-box threshold.
    std::uint16_t channels_in[8] = {1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500};
    const auto payload = encode_msp_rc_payload(channels_in);
    const auto reply = build_msp_response(fc::msp::MSP_RC, payload);
    const ssize_t w = ::write(sv[1], reply.data(), reply.size());
    TEST_ASSERT(w == static_cast<ssize_t>(reply.size()));

    auto sink = fc::MspRcSink::make_for_testing(sv[0]);

    fc::BetaFlightCommand cmd = makeNeutralCommand();
    sink->writeChannels(cmd, 0.020);

    TEST_ASSERT(sink->arm_switch() == false);

    ::close(sv[1]);
}

void test_irc_sink_defaults_for_non_msp_sinks() {
    // The IRcSink virtuals tx_ratio() / arm_switch() landed in this
    // commit alongside MspRcSink. NullSink, RecordingSink, and
    // FakeBetaflightSink intentionally do not override them — they have
    // no flaky transport (NullSink, RecordingSink) or absorb hiccups
    // internally (FakeBetaflightSink). Pin the defaults here so a future
    // override can't silently misreport sink health in TEL.
    fc::NullSink null_sink;
    TEST_ASSERT(null_sink.tx_ratio() == 1.0);
    TEST_ASSERT(!null_sink.arm_switch());
}

} // namespace

int main() {
    // Tests in this file deliberately close the peer end of a socketpair
    // to drive write() into EPIPE. Default SIGPIPE behaviour terminates
    // the process; production fc_app sets the same disposition (will land
    // in S0.8 commit 4's main.cpp). For the test executable, ignoring
    // SIGPIPE keeps the failure visible as an errno value the sink
    // observes and reacts to.
    ::signal(SIGPIPE, SIG_IGN);

    test_make_for_testing_constructs_healthy_sink();
    test_writechannels_emits_msp_set_raw_rc_frame();
    test_writechannels_persistent_failure_closes_fd_and_drops_tx_ratio();
    test_arm_switch_latches_when_aux1_above_threshold();
    test_arm_switch_latch_transitions_back_to_false_on_below_threshold_reply();
    test_arm_switch_stays_false_when_aux1_below_threshold();
    test_irc_sink_defaults_for_non_msp_sinks();
    return 0;
}
