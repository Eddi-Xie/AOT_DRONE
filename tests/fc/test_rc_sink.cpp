// Unit tests for the IRcSink hierarchy. Each test exercises one sink
// implementation in isolation. NullSink is straightforward; RecordingSink
// drives the file system; FakeBetaflightSink (added in next commit)
// drives a UDP socket.

#include "FlightController.h"
#include "RcSink.h"
#include "test_assert.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
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

} // namespace

int main() {
    test_null_sink_smoke();
    test_recording_sink_writes_csv_with_header_and_rows();
    test_recording_sink_creates_missing_log_dir();
    return 0;
}
