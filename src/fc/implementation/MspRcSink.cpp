#include "MspRcSink.h"

#include "MspFraming.h"

#include <fcntl.h>
#include <sys/select.h>
#include <termios.h>
#include <unistd.h>

#include <cerrno>
#include <cstdint>
#include <cstring>
#include <iostream>

namespace fc {

namespace {

// EWMA smoothing constant for tx_ratio. α=0.05 → effective window ≈20
// samples; at 50 Hz that's ~0.4 s — fast enough that operator-visible
// degradation surfaces within half a second of sustained failures.
constexpr double kTxRatioAlpha = 0.05;

// MSP_RC reply payload layout: 8 channels × uint16 LE = 16 bytes. Default
// Betaflight channel map is AETR1234 — aux1 is channel index 4, occupying
// bytes [8..9]. The S0.14 arm-authority gate consumes arm_switch();
// matching the Betaflight default ARM-box threshold (channels >= 1700 µs
// are "switch on") keeps our latch consistent with what the operator sees
// in Configurator.
constexpr std::size_t kMspRcAux1Offset = 8;
constexpr std::uint16_t kArmSwitchThresholdUs = 1700;
// Force arm_switch_=false if no MSP_RC reply has been seen for this long.
// At the 5 Hz poll cadence this covers ~5 missed replies — enough to
// distinguish a momentary blip from a real disconnect, while staying
// well under S0.14's "switch held >=1 s" arm-gate window so a stale
// latch can't be the deciding bit.
constexpr long kArmSwitchStaleMs = 1000;

// Boot probe pacing. 500 ms total is generous for a USB-CDC FC (replies
// typically land in <100 ms); 50 ms select() polls keep the busy-loop
// cost negligible.
constexpr int kProbeTimeoutMs = 500;
constexpr int kProbePollMs = 50;

// MSP_RC poll cadence: every Nth writeChannels() triggers poll_msp_rc_.
// At 50 Hz this lands at 5 Hz — fast enough for S0.14's "arm switch
// held ≥1 s" check, slow enough not to drown the SET_RAW_RC heartbeat.
constexpr std::uint64_t kMspRcPollEvery = 10;

// Map a numeric baud rate to the platform's termios speed_t macro.
// Values outside this list are rejected at the env-var stage in
// make_rc_sink_from_env (S0.8 commit 3) so the operator gets a clear
// "unsupported baud" message rather than a confusing tcsetattr failure.
bool baud_to_speed(int baud, speed_t& out) {
    switch (baud) {
    case 9600:
        out = B9600;
        return true;
    case 19200:
        out = B19200;
        return true;
    case 38400:
        out = B38400;
        return true;
    case 57600:
        out = B57600;
        return true;
    case 115200:
        out = B115200;
        return true;
    case 230400:
        out = B230400;
        return true;
#ifdef B460800
    case 460800:
        out = B460800;
        return true;
#endif
#ifdef B921600
    case 921600:
        out = B921600;
        return true;
#endif
    default:
        return false;
    }
}

} // namespace

MspRcSink::MspRcSink(int fd) : fd_(fd) {}

MspRcSink::~MspRcSink() {
    if (fd_ >= 0) {
        ::close(fd_);
        fd_ = -1;
    }
}

std::unique_ptr<MspRcSink> MspRcSink::make_for_testing(int fd) {
    return std::unique_ptr<MspRcSink>(new MspRcSink(fd));
}

std::unique_ptr<MspRcSink> MspRcSink::from_device(const std::string& path, int baud) {
    speed_t speed;
    if (!baud_to_speed(baud, speed)) {
        std::cerr << "[FC] MspRcSink: unsupported baud " << baud
                  << " (allowed: 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600). "
                     "Refusing to start.\n";
        return nullptr;
    }

    int fd = ::open(path.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) {
        std::cerr << "[FC] MspRcSink: open('" << path << "') failed: " << std::strerror(errno)
                  << "\n";
        return nullptr;
    }

    struct termios tio{};
    if (tcgetattr(fd, &tio) != 0) {
        std::cerr << "[FC] MspRcSink: tcgetattr('" << path << "') failed: " << std::strerror(errno)
                  << "\n";
        ::close(fd);
        return nullptr;
    }
    cfmakeraw(&tio);
    if (cfsetispeed(&tio, speed) != 0 || cfsetospeed(&tio, speed) != 0) {
        std::cerr << "[FC] MspRcSink: cfsetispeed/cfsetospeed failed: " << std::strerror(errno)
                  << "\n";
        ::close(fd);
        return nullptr;
    }
    // 8N1, no flow control, ignore modem control lines, enable receiver.
    // cfmakeraw normalises most of these but USB-CDC stacks can be picky;
    // re-asserting is defence in depth.
    tio.c_cflag &= ~(PARENB | CSTOPB | CSIZE | CRTSCTS);
    tio.c_cflag |= (CS8 | CLOCAL | CREAD);
    tio.c_iflag &= ~(IXON | IXOFF | IXANY);
    tio.c_cc[VMIN] = 0;
    tio.c_cc[VTIME] = 0;

    if (tcsetattr(fd, TCSANOW, &tio) != 0) {
        std::cerr << "[FC] MspRcSink: tcsetattr('" << path << "') failed: " << std::strerror(errno)
                  << "\n";
        ::close(fd);
        return nullptr;
    }

    // Drop any boot chatter the FC sent before we configured the line —
    // Betaflight prints diagnostic banner text on serial open that would
    // otherwise show up as "garbage before $M>" in the boot probe parser.
    tcflush(fd, TCIOFLUSH);

    std::cout << "[FC] MspRcSink: opened '" << path << "' at " << baud
              << " baud; running MSP_API_VERSION boot probe...\n";

    auto sink = std::unique_ptr<MspRcSink>(new MspRcSink(fd));
    if (sink->run_boot_probe_()) {
        sink->run_tuning_probe_();
    } else {
        // boot_probe_failed_ already set; ok() now reads false. Keep the
        // sink alive so the caller's ok() check produces the operator-
        // facing "Refusing to start" log with full context (baud, path).
        std::cerr << "[FC] MspRcSink: boot probe failed; sink will refuse fc_app startup\n";
    }
    return sink;
}

bool MspRcSink::write_frame_(const std::uint8_t* buf, std::size_t n) {
    if (fd_ < 0) {
        return false;
    }
    // Retry policy: count CONSECUTIVE no-progress attempts (EAGAIN /
    // EWOULDBLOCK / EINTR with no bytes advanced). Any successful write
    // — even a 1-byte short write — resets the counter. Hitting the
    // budget is a persistent transport failure: close the fd, flip ok()
    // to false, main loop's sink-degraded latch routes to LandSafely.
    //
    // Counting progress and no-progress separately matters under sustained
    // kernel-buffer pressure where w=1, EAGAIN, w=21 is a recoverable
    // sequence — but the previous attempts-only cap would have classified
    // it as a failsafe trigger after just two syscalls.
    std::size_t off = 0;
    int no_progress = 0;
    // Hard cap on total syscalls: catches the pathological 1-byte-then-
    // EAGAIN cycle that would otherwise loop indefinitely. 32 is generous
    // for the 22-byte SET_RAW_RC frame (one syscall per byte plus 10
    // EAGAINs) and unreachable in practice for a healthy USB-CDC FC.
    int total_attempts = 0;
    constexpr int kMaxNoProgress = 2; // initial try + one retry
    constexpr int kMaxTotal = 32;

    while (off < n && total_attempts < kMaxTotal) {
        ++total_attempts;
        const ssize_t w = ::write(fd_, buf + off, n - off);
        if (w > 0) {
            off += static_cast<std::size_t>(w);
            no_progress = 0; // any progress resets the budget
            continue;
        }
        const int err = errno;
        if (w < 0 && (err == EAGAIN || err == EWOULDBLOCK || err == EINTR)) {
            ++no_progress;
            if (no_progress >= kMaxNoProgress) {
                std::cerr << "[FC] MspRcSink: write blocked for " << no_progress
                          << " consecutive no-progress attempts (" << off << "/" << n
                          << " bytes); closing fd, ok() flipping to false\n";
                ::close(fd_);
                fd_ = -1;
                return false;
            }
            continue; // transient — retry
        }
        // Hard error or write()==0: persistent failure.
        std::cerr << "[FC] MspRcSink: write() failed (errno=" << err << "): " << std::strerror(err)
                  << "; closing fd, ok() flipping to false\n";
        ::close(fd_);
        fd_ = -1;
        return false;
    }
    if (off < n) {
        std::cerr << "[FC] MspRcSink: write hit total-attempt cap " << kMaxTotal << " (" << off
                  << "/" << n << " bytes); closing fd, ok() flipping to false\n";
        ::close(fd_);
        fd_ = -1;
        return false;
    }
    return true;
}

namespace {

// Drain whatever's already in `fd`'s kernel buffer into `acc` non-
// blocking. Returns once read() reports no more data (EAGAIN / 0 / -1).
// Used by the probe waits and by poll_msp_rc_; behaviour is identical so
// the helper lives in the anonymous namespace.
void drain_into(int fd, std::vector<std::uint8_t>& acc) {
    while (true) {
        std::uint8_t buf[256];
        const ssize_t r = ::read(fd, buf, sizeof(buf));
        if (r > 0) {
            acc.insert(acc.end(), buf, buf + r);
            if (static_cast<std::size_t>(r) == sizeof(buf)) {
                continue; // more available
            }
        }
        break;
    }
}

// Pull every complete frame out of `acc`, invoking `on_frame` for each.
// Stops when the parser reports no progress (partial frame waiting for
// more bytes). `on_frame` returns true to stop early (e.g. boot probe
// found the cmd it was waiting for); false to keep draining.
//
// Bad-checksum frames are logged with a running count via `parse_errors_ref`
// (bumped each time). They never reach `on_frame` — the parser already
// resyncs past them; this helper just makes the silent drop visible so
// the operator can correlate USB issues with TEL-side symptoms.
template <typename F>
void parse_drain(std::vector<std::uint8_t>& acc, std::uint64_t& parse_errors_ref, F on_frame) {
    while (!acc.empty()) {
        const auto pr = fc::msp::parse_response(acc.data(), acc.size());
        if (pr.bytes_consumed > 0) {
            acc.erase(acc.begin(), acc.begin() + pr.bytes_consumed);
        }
        if (pr.checksum_error) {
            ++parse_errors_ref;
            std::cerr << "[FC] MspRcSink: dropped MSP frame with bad checksum (count="
                      << parse_errors_ref
                      << "); a small non-zero count is normal USB-CDC framing recovery, "
                         "sustained growth points at a flaky cable\n";
        }
        if (pr.frame.has_value()) {
            if (on_frame(*pr.frame)) {
                return;
            }
        }
        if (pr.bytes_consumed == 0) {
            return; // partial frame; need more bytes
        }
    }
}

} // namespace

bool MspRcSink::run_boot_probe_() {
    auto frame = fc::msp::encode_request(fc::msp::MSP_API_VERSION, nullptr, 0);
    if (!write_frame_(frame.data(), frame.size())) {
        boot_probe_failed_ = true;
        return false;
    }

    int elapsed = 0;
    while (elapsed < kProbeTimeoutMs && fd_ >= 0) {
        struct timeval tv;
        tv.tv_sec = 0;
        tv.tv_usec = kProbePollMs * 1000;
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(fd_, &rfds);

        const int sel = ::select(fd_ + 1, &rfds, nullptr, nullptr, &tv);
        if (sel > 0 && FD_ISSET(fd_, &rfds)) {
            drain_into(fd_, rx_buffer_);

            bool found = false;
            parse_drain(rx_buffer_, parse_errors_, [&](const fc::msp::ParsedFrame& f) {
                if (f.cmd == fc::msp::MSP_API_VERSION) {
                    std::cout << "[FC] MspRcSink: MSP_API_VERSION boot probe ok ("
                              << f.payload.size() << "-byte payload)\n";
                    found = true;
                    return true;
                }
                // Non-target frames during the boot-probe window are
                // unusual but possible (residual replies from a previous
                // session, out-of-order CDC delivery). Log so the
                // operator can correlate "tuning probe failed" later
                // with "an MSP_RC_TUNING reply got eaten here".
                std::cerr << "[FC] MspRcSink: boot probe ignored unexpected MSP cmd="
                          << static_cast<int>(f.cmd) << " (" << f.payload.size()
                          << "-byte payload)\n";
                return false;
            });
            if (found) {
                return true;
            }
        }
        elapsed += kProbePollMs;
    }

    std::cerr << "[FC] MspRcSink: MSP_API_VERSION boot probe timed out after " << kProbeTimeoutMs
              << "ms\n";
    boot_probe_failed_ = true;
    return false;
}

void MspRcSink::run_tuning_probe_() {
    auto frame = fc::msp::encode_request(fc::msp::MSP_RC_TUNING, nullptr, 0);
    if (!write_frame_(frame.data(), frame.size())) {
        // fd already closed by write_frame_; ok() now false. tuning_
        // mismatch_ is moot in the failsafe path so leave it alone.
        return;
    }

    int elapsed = 0;
    while (elapsed < kProbeTimeoutMs && fd_ >= 0) {
        struct timeval tv;
        tv.tv_sec = 0;
        tv.tv_usec = kProbePollMs * 1000;
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(fd_, &rfds);

        const int sel = ::select(fd_ + 1, &rfds, nullptr, nullptr, &tv);
        if (sel > 0 && FD_ISSET(fd_, &rfds)) {
            drain_into(fd_, rx_buffer_);

            bool found = false;
            parse_drain(rx_buffer_, parse_errors_, [&](const fc::msp::ParsedFrame& f) {
                if (f.cmd == fc::msp::MSP_RC_TUNING) {
                    std::cout << "[FC] MspRcSink: MSP_RC_TUNING reply (" << f.payload.size()
                              << "-byte payload, "
                                 "rate-profile values logged for operator review)\n";
                    found = true;
                    return true;
                }
                std::cerr << "[FC] MspRcSink: tuning probe ignored unexpected MSP cmd="
                          << static_cast<int>(f.cmd) << " (" << f.payload.size()
                          << "-byte payload)\n";
                return false;
            });
            if (found) {
                return;
            }
        }
        elapsed += kProbePollMs;
    }

    std::cerr << "[FC] MspRcSink: MSP_RC_TUNING did not reply within " << kProbeTimeoutMs
              << "ms; setting tuning_mismatch=true. Tracking/Takeoff transitions will be "
                 "refused for the rest of this fc_app process — there is no in-flight "
                 "re-probe path in S0.8. Recovery: stop fc_app, reseat USB / power-cycle "
                 "the FC, restart fc_app. Periodic re-probe + operator-confirm UI lands "
                 "with S0.14 / Sprint 1 H2 UART pivot.\n";
    tuning_mismatch_ = true;
}

void MspRcSink::poll_msp_rc_() {
    if (fd_ < 0) {
        return;
    }
    drain_into(fd_, rx_buffer_);

    parse_drain(rx_buffer_, parse_errors_, [&](const fc::msp::ParsedFrame& f) {
        if (f.cmd == fc::msp::MSP_RC && f.payload.size() >= kMspRcAux1Offset + 2) {
            const std::uint16_t aux1 =
                static_cast<std::uint16_t>(f.payload[kMspRcAux1Offset]) |
                static_cast<std::uint16_t>(
                    static_cast<std::uint16_t>(f.payload[kMspRcAux1Offset + 1]) << 8);
            arm_switch_ = (aux1 >= kArmSwitchThresholdUs);
            last_msp_rc_reply_time_ = std::chrono::steady_clock::now();
        }
        return false; // keep draining — more frames may follow
    });

    // Force-clear arm_switch_ if no MSP_RC reply has landed within the
    // staleness window. Without this, a USB hiccup or FC reboot loop
    // would freeze the latch at its last value — and S0.14's three-
    // condition arm gate would then act on stale "switch on" state.
    if (arm_switch_ && last_msp_rc_reply_time_.has_value()) {
        const auto elapsed = std::chrono::steady_clock::now() - *last_msp_rc_reply_time_;
        const auto elapsed_ms =
            std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
        if (elapsed_ms > kArmSwitchStaleMs) {
            arm_switch_ = false;
            std::cerr << "[FC] MspRcSink: forcing arm_switch=false (no MSP_RC reply for "
                      << elapsed_ms << "ms, threshold=" << kArmSwitchStaleMs << "ms)\n";
        }
    }

    // Send a fresh MSP_RC query so the *next* poll has a current reply
    // to drain. write_frame_ failure here closes the fd and flips ok();
    // that's the same path SET_RAW_RC failure takes, so no extra
    // handling is needed.
    auto query = fc::msp::encode_request(fc::msp::MSP_RC, nullptr, 0);
    write_frame_(query.data(), query.size());
}

void MspRcSink::writeChannels(const BetaFlightCommand& cmd, double /*timestamp_s*/) {
    if (fd_ < 0) {
        // Already in degraded state — main.cpp's failsafe latch saw ok()=false
        // and routed to LandSafely. Stay silent; tx_ratio_ is frozen at its
        // last value so TEL still surfaces the deviation.
        return;
    }

    // MSPv1 channel-order asymmetry — read carefully before touching:
    //
    //   MSP_SET_RAW_RC (write, this function): wire order is AETR1234
    //     under the default rcmap — Aileron (Roll), Elevator (Pitch),
    //     Throttle, Rudder (Yaw), then four aux channels. Betaflight's
    //     rxMspFrameReceive applies rcmap[] to remap from this wire
    //     order to its internal channel layout. With the default rcmap
    //     "AETR1234", wire position 2 maps to internal THROTTLE and
    //     wire position 3 maps to internal YAW. We MUST send AETR
    //     regardless of the FC's rcmap config — the rcmap reverses the
    //     remap, so AETR on the wire is the contract.
    //
    //   MSP_RC (read, see poll_msp_rc_): reply order is RPYT1234 — the
    //     FC's INTERNAL channel constants from rc.h (ROLL=0, PITCH=1,
    //     YAW=2, THROTTLE=3, AUX1=4...). No rcmap is applied on the
    //     way back. So a writeChannels-then-readback round-trip sees
    //     fields at *different positions* on the two ends.
    //
    // Pre-fix this array sent {roll, pitch, yaw, throttle, ...} (RPYT).
    // The bench MSP_RC log surfaced it: in LandSafely (throttle=1000,
    // yaw=1500) the FC reported Throttle=1500, Yaw=1000 — fc_app was
    // driving ~50% throttle on the yaw channel. With motors attached
    // this would have been catastrophic. CI missed it because the
    // unit test pinned the wrong order.
    const std::uint16_t channels[fc::msp::MSP_SET_RAW_RC_CHANNEL_COUNT] = {
        cmd.roll, cmd.pitch, cmd.throttle, cmd.yaw, cmd.aux1, cmd.aux2, cmd.aux3, cmd.aux4,
    };
    auto frame = fc::msp::encode_set_raw_rc(channels);

    ++writes_attempted_;
    const bool success = write_frame_(frame.data(), frame.size());
    if (success) {
        ++writes_succeeded_;
    }
    tx_ratio_ = (1.0 - kTxRatioAlpha) * tx_ratio_ + kTxRatioAlpha * (success ? 1.0 : 0.0);

    // Poll on tick 0, 10, 20, ... — first poll sends the initial MSP_RC
    // query so subsequent polls have something to drain. Done after the
    // SET_RAW_RC write so a poll-side write failure can't mask the
    // primary heartbeat's success.
    if ((tick_counter_ % kMspRcPollEvery) == 0) {
        poll_msp_rc_();
    }
    ++tick_counter_;
}

} // namespace fc
