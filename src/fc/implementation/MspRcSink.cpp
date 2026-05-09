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

std::unique_ptr<MspRcSink> MspRcSink::make_for_testing(int fd, OwnsBootProbe probe) {
    auto sink = std::unique_ptr<MspRcSink>(new MspRcSink(fd));
    if (probe == OwnsBootProbe::Yes) {
        if (sink->run_boot_probe_()) {
            sink->run_tuning_probe_();
        }
    }
    return sink;
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
    // Up to two write() syscalls total — initial attempt plus one retry
    // on EAGAIN/EINTR/short write. Anything beyond is treated as a
    // persistent transport failure (fd closed; ok() flips false; main.cpp
    // routes through the sink-degraded failsafe path in S0.8 commit 4).
    std::size_t off = 0;
    int attempts = 0;
    constexpr int kMaxAttempts = 2;

    while (off < n && attempts < kMaxAttempts) {
        ++attempts;
        const ssize_t w = ::write(fd_, buf + off, n - off);
        if (w > 0) {
            off += static_cast<std::size_t>(w);
            continue; // partial write counts toward the attempt budget
        }
        const int err = errno;
        if (w < 0 && (err == EAGAIN || err == EWOULDBLOCK || err == EINTR)) {
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
        std::cerr << "[FC] MspRcSink: write incomplete after " << attempts << " attempt(s) (" << off
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
template <typename F> void parse_drain(std::vector<std::uint8_t>& acc, F on_frame) {
    while (!acc.empty()) {
        const auto pr = fc::msp::parse_response(acc.data(), acc.size());
        if (pr.bytes_consumed > 0) {
            acc.erase(acc.begin(), acc.begin() + pr.bytes_consumed);
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
            parse_drain(rx_buffer_, [&](const fc::msp::ParsedFrame& f) {
                if (f.cmd == fc::msp::MSP_API_VERSION) {
                    std::cout << "[FC] MspRcSink: MSP_API_VERSION boot probe ok ("
                              << f.payload.size() << "-byte payload)\n";
                    found = true;
                    return true;
                }
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
            parse_drain(rx_buffer_, [&](const fc::msp::ParsedFrame& f) {
                if (f.cmd == fc::msp::MSP_RC_TUNING) {
                    std::cout << "[FC] MspRcSink: MSP_RC_TUNING reply (" << f.payload.size()
                              << "-byte payload, "
                                 "rate-profile values logged for operator review)\n";
                    found = true;
                    return true;
                }
                return false;
            });
            if (found) {
                return;
            }
        }
        elapsed += kProbePollMs;
    }

    std::cerr << "[FC] MspRcSink: MSP_RC_TUNING did not reply within " << kProbeTimeoutMs
              << "ms; setting tuning_mismatch=true (Tracking/Takeoff "
                 "transitions will be refused; deferring operator-confirm UI to S0.14)\n";
    tuning_mismatch_ = true;
}

void MspRcSink::poll_msp_rc_() {
    if (fd_ < 0) {
        return;
    }
    drain_into(fd_, rx_buffer_);

    parse_drain(rx_buffer_, [&](const fc::msp::ParsedFrame& f) {
        if (f.cmd == fc::msp::MSP_RC && f.payload.size() >= kMspRcAux1Offset + 2) {
            const std::uint16_t aux1 =
                static_cast<std::uint16_t>(f.payload[kMspRcAux1Offset]) |
                static_cast<std::uint16_t>(
                    static_cast<std::uint16_t>(f.payload[kMspRcAux1Offset + 1]) << 8);
            arm_switch_ = (aux1 >= kArmSwitchThresholdUs);
        }
        return false; // keep draining — more frames may follow
    });

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

    const std::uint16_t channels[fc::msp::MSP_SET_RAW_RC_CHANNEL_COUNT] = {
        cmd.roll, cmd.pitch, cmd.yaw, cmd.throttle, cmd.aux1, cmd.aux2, cmd.aux3, cmd.aux4,
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
