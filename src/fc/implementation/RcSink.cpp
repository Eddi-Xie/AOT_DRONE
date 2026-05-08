#include "RcSink.h"

#include <iostream>

namespace fc {

void NullSink::writeChannels(const BetaFlightCommand& cmd, double timestamp_s) {
    ++calls_;
    if (!first_logged_) {
        first_logged_ = true;
        std::cout << "[FC] NullSink received first channel write at t=" << timestamp_s
                  << " roll=" << cmd.roll << " pitch=" << cmd.pitch << " yaw=" << cmd.yaw
                  << " throttle=" << cmd.throttle << " (further writes will be silent)\n";
    }
}

} // namespace fc
