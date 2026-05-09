// Unit tests for the IRcSink hierarchy. Each test exercises one sink
// implementation in isolation. NullSink is straightforward; RecordingSink
// drives the file system; FakeBetaflightSink (added in next commit)
// drives a UDP socket.

#include "FlightController.h"
#include "RcSink.h"
#include "test_assert.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>

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

void test_null_sink_smoke() {
    fc::NullSink sink;
    TEST_ASSERT(sink.ok());
    TEST_ASSERT(sink.name() == "null");
    TEST_ASSERT(sink.calls() == 0U);

    fc::BetaFlightCommand cmd = makeNeutralCommand();
    sink.writeChannels(cmd, 0.020);
    sink.writeChannels(cmd, 0.040);
    sink.writeChannels(cmd, 0.060);

    TEST_ASSERT(sink.calls() == 3U);
    TEST_ASSERT(sink.ok()); // ok() stays true regardless of call count
}

// Pull a per-test working directory out of the environment. ctest sets
// up CTEST_BINARY_DIRECTORY but it is not exposed to the test process
// directly; we use TMPDIR (always set on macOS, usually on Linux) with
// a fallback.
std::string make_temp_subdir(const std::string& tag) {
    const char* base = std::getenv("TMPDIR");
    std::string root = (base && *base) ? std::string(base) : std::string("/tmp/");
    if (root.empty() || root.back() != '/') {
        root.push_back('/');
    }
    return root + "aot_test_rc_sink_" + tag;
}

void test_recording_sink_writes_csv_with_header_and_rows() {
    const std::string log_dir = make_temp_subdir("rec");
    // Best-effort cleanup of any previous run.
    std::system(("rm -rf " + log_dir).c_str());

    std::string captured_path;
    {
        fc::RecordingSink sink(log_dir);
        TEST_ASSERT(sink.ok());
        TEST_ASSERT(sink.name() == "recording");
        TEST_ASSERT(sink.rows_written() == 0U);

        fc::BetaFlightCommand cmd = makeNeutralCommand();
        sink.writeChannels(cmd, 0.020);
        cmd.throttle = 1234;
        sink.writeChannels(cmd, 0.040);

        TEST_ASSERT(sink.rows_written() == 2U);
        captured_path = sink.path();
    } // sink destructor closes file_

    TEST_ASSERT(!captured_path.empty());

    // Read the file back and verify shape.
    std::ifstream in(captured_path);
    TEST_ASSERT(in.is_open());

    std::string header;
    std::getline(in, header);
    TEST_ASSERT(header == "timestamp_s,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4");

    std::string row1;
    std::getline(in, row1);
    // Layout: timestamp,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4
    TEST_ASSERT(row1 == "0.020000,1500,1500,1500,1100,1000,2000,1000,1000");

    std::string row2;
    std::getline(in, row2);
    TEST_ASSERT(row2 == "0.040000,1500,1500,1500,1234,1000,2000,1000,1000");

    std::string trailing;
    TEST_ASSERT(!std::getline(in, trailing) || trailing.empty());

    // Cleanup.
    std::system(("rm -rf " + log_dir).c_str());
}

void test_recording_sink_creates_missing_log_dir() {
    const std::string log_dir = make_temp_subdir("rec_nested") + "/a/b/c";
    std::system(("rm -rf " + make_temp_subdir("rec_nested")).c_str());

    fc::RecordingSink sink(log_dir);
    TEST_ASSERT(sink.ok());

    fc::BetaFlightCommand cmd = makeNeutralCommand();
    sink.writeChannels(cmd, 0.0);
    TEST_ASSERT(sink.rows_written() == 1U);

    std::system(("rm -rf " + make_temp_subdir("rec_nested")).c_str());
}

void test_recording_sink_refuses_when_path_component_is_a_file() {
    // Pre-fix: ensure_directory accepted EEXIST without S_ISDIR check, so
    // FC_RC_LOG_DIR=<existing-file>/sub silently passed mkdir-p and fopen
    // later failed with a confusing ENOTDIR-on-the-CSV-path message.
    // Post-fix: refuse up front with the offending intermediate named.
    const std::string base = make_temp_subdir("rec_file");
    std::system(("rm -rf " + base).c_str());
    // Create an ordinary file at the location we'll then try to mkdir
    // through.
    std::system(("mkdir -p " + base).c_str());
    const std::string blocking_file = base + "/blocking_file";
    {
        std::FILE* f = std::fopen(blocking_file.c_str(), "w");
        TEST_ASSERT(f != nullptr);
        std::fclose(f);
    }
    const std::string log_dir = blocking_file + "/sub";

    fc::RecordingSink sink(log_dir);
    // Refused: ok() false, no path stored, no row written.
    TEST_ASSERT(!sink.ok());
    TEST_ASSERT(sink.rows_written() == 0U);

    std::system(("rm -rf " + base).c_str());
}

void test_recording_sink_rows_lost_accessor_starts_zero() {
    // The rows_lost() accessor was added in round 1 alongside the disk-
    // failure ok()-flip. We don't have a portable way to inject an
    // EIO/ENOSPC mid-run from a unit test (Linux's /dev/full doesn't
    // exist on macOS; chmod-then-truncate races); the production
    // contract is exercised manually via:
    //
    //   FC_RC_LOG_DIR=/dev/full FC_RC_SINK=recording fc_app   # Linux
    //
    // and by reading the round-1 commit's RcSink.cpp diff. This test
    // pins the public-API surface so a future refactor that drops
    // rows_lost() trips CI.
    const std::string log_dir = make_temp_subdir("rec_lost");
    std::system(("rm -rf " + log_dir).c_str());

    fc::RecordingSink sink(log_dir);
    TEST_ASSERT(sink.ok());
    TEST_ASSERT(sink.rows_lost() == 0U);

    fc::BetaFlightCommand cmd = makeNeutralCommand();
    sink.writeChannels(cmd, 0.0);
    TEST_ASSERT(sink.rows_lost() == 0U);
    TEST_ASSERT(sink.rows_written() == 1U);

    std::system(("rm -rf " + log_dir).c_str());
}

// Bind a UDP socket to a kernel-assigned ephemeral port so the test
// doesn't fight with whatever else is on the box. Returns the fd and
// fills *out_port with the assigned port number.
int bind_loopback_listener(int* out_port) {
    int fd = ::socket(AF_INET, SOCK_DGRAM, 0);
    TEST_ASSERT(fd >= 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(0); // kernel picks
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    TEST_ASSERT(::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0);

    sockaddr_in bound{};
    socklen_t len = sizeof(bound);
    TEST_ASSERT(::getsockname(fd, reinterpret_cast<sockaddr*>(&bound), &len) == 0);
    *out_port = ntohs(bound.sin_port);
    return fd;
}

void test_fake_sink_sends_self_contained_json_datagram() {
    int port = 0;
    const int listener = bind_loopback_listener(&port);

    {
        fc::FakeBetaflightSink sink("127.0.0.1", port);
        TEST_ASSERT(sink.ok());
        TEST_ASSERT(sink.name() == "fake");
        TEST_ASSERT(sink.frames_sent() == 0U);

        fc::BetaFlightCommand cmd = makeNeutralCommand();
        cmd.throttle = 1234;
        sink.writeChannels(cmd, 0.020);

        TEST_ASSERT(sink.frames_sent() == 1U);
        TEST_ASSERT(sink.frames_dropped() == 0U);
    }

    // Receive the datagram. It will be in flight before the sink is
    // destroyed because UDP sendto is synchronous to the kernel buffer.
    char buf[512] = {0};
    const ssize_t got = ::recv(listener, buf, sizeof(buf) - 1, 0);
    TEST_ASSERT(got > 0);
    buf[got] = '\0';

    const std::string s(buf);
    // Strict-substring assertions for stability — full JSON parse would
    // pull in a dependency this test deliberately avoids.
    TEST_ASSERT(s.find("\"type\":\"RC\"") != std::string::npos);
    TEST_ASSERT(s.find("\"seq\":0") != std::string::npos);
    TEST_ASSERT(s.find("\"timestamp_s\":0.020000") != std::string::npos);
    TEST_ASSERT(s.find("\"channels\":[1500,1500,1500,1234,1000,2000,1000,1000]") !=
                std::string::npos);

    ::close(listener);
}

void test_fake_sink_seq_advances_per_frame() {
    int port = 0;
    const int listener = bind_loopback_listener(&port);

    fc::FakeBetaflightSink sink("127.0.0.1", port);
    TEST_ASSERT(sink.ok());

    fc::BetaFlightCommand cmd = makeNeutralCommand();
    sink.writeChannels(cmd, 0.0);
    sink.writeChannels(cmd, 0.020);
    sink.writeChannels(cmd, 0.040);

    // Drain three datagrams; assert seq=0, 1, 2.
    for (std::uint64_t expected = 0; expected < 3; ++expected) {
        char buf[512] = {0};
        const ssize_t got = ::recv(listener, buf, sizeof(buf) - 1, 0);
        TEST_ASSERT(got > 0);
        buf[got] = '\0';
        const std::string needle = "\"seq\":" + std::to_string(expected);
        TEST_ASSERT(std::string(buf).find(needle) != std::string::npos);
    }
    TEST_ASSERT(sink.frames_sent() == 3U);

    ::close(listener);
}

// The IRcSink virtuals tx_ratio() / arm_switch() / tuning_mismatch()
// landed in S0.8 alongside MspRcSink. The pre-existing sinks (NullSink,
// RecordingSink, FakeBetaflightSink) intentionally don't override them
// — they have no MSP transport — and inherit the safe defaults
// (tx_ratio=1.0 so TEL still reads as healthy, arm_switch=false, no
// rate-profile mismatch). Pin those defaults so a future override can't
// silently misreport sink health to the operator.
void test_irc_sink_defaults_for_existing_sinks() {
    fc::NullSink null_sink;
    TEST_ASSERT(null_sink.tx_ratio() == 1.0);
    TEST_ASSERT(!null_sink.arm_switch());
    TEST_ASSERT(!null_sink.tuning_mismatch());

    const std::string log_dir = make_temp_subdir("rec_defaults");
    std::system(("rm -rf " + log_dir).c_str());
    fc::RecordingSink rec(log_dir);
    TEST_ASSERT(rec.ok());
    TEST_ASSERT(rec.tx_ratio() == 1.0);
    TEST_ASSERT(!rec.arm_switch());
    TEST_ASSERT(!rec.tuning_mismatch());
    std::system(("rm -rf " + log_dir).c_str());

    int port = 0;
    const int listener = bind_loopback_listener(&port);
    fc::FakeBetaflightSink fake("127.0.0.1", port);
    TEST_ASSERT(fake.ok());
    TEST_ASSERT(fake.tx_ratio() == 1.0);
    TEST_ASSERT(!fake.arm_switch());
    TEST_ASSERT(!fake.tuning_mismatch());
    ::close(listener);
}

void test_fake_sink_rejects_invalid_host_and_port() {
    fc::FakeBetaflightSink bad_host("not-a-valid-ip", 9101);
    TEST_ASSERT(!bad_host.ok());

    fc::FakeBetaflightSink bad_port_lo("127.0.0.1", 0);
    TEST_ASSERT(!bad_port_lo.ok());

    fc::FakeBetaflightSink bad_port_hi("127.0.0.1", 65536);
    TEST_ASSERT(!bad_port_hi.ok());

    fc::FakeBetaflightSink bad_port_neg("127.0.0.1", -1);
    TEST_ASSERT(!bad_port_neg.ok());
}

} // namespace

int main() {
    test_null_sink_smoke();
    test_recording_sink_writes_csv_with_header_and_rows();
    test_recording_sink_creates_missing_log_dir();
    test_recording_sink_refuses_when_path_component_is_a_file();
    test_recording_sink_rows_lost_accessor_starts_zero();
    test_fake_sink_sends_self_contained_json_datagram();
    test_fake_sink_seq_advances_per_frame();
    test_irc_sink_defaults_for_existing_sinks();
    test_fake_sink_rejects_invalid_host_and_port();
    return 0;
}
