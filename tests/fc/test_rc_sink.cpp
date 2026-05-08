// Unit tests for the IRcSink hierarchy. Each test exercises one sink
// implementation in isolation. NullSink is straightforward; RecordingSink
// and FakeBetaflightSink (added in follow-up commits) drive the file
// system and a UDP socket respectively.

#include "FlightController.h"
#include "RcSink.h"
#include "test_assert.h"

#include <cstdint>

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

} // namespace

int main() {
    test_null_sink_smoke();
    return 0;
}
