#include "RcSink.h"

#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <ctime>
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

namespace {

// mkdir -p equivalent. Walks the path, creating each intermediate
// directory; returns true on success or if the directory already
// exists AND is actually a directory. Errors out with a stderr
// message on failure.
//
// The S_ISDIR check on EEXIST guards against operator typos like
// FC_RC_LOG_DIR=/etc/hosts (an existing regular file) -- without the
// check, mkdir would EEXIST-succeed silently and fopen would later
// fail with a confusing ENOTDIR-style message naming the full CSV
// path instead of the offending intermediate component.
bool ensure_directory(const std::string& dir) {
    if (dir.empty()) {
        return true;
    }
    std::string accum;
    for (std::size_t i = 0; i <= dir.size(); ++i) {
        if (i == dir.size() || dir[i] == '/') {
            if (!accum.empty() && accum != "." && accum != "..") {
                if (mkdir(accum.c_str(), 0755) != 0) {
                    if (errno != EEXIST) {
                        std::cerr << "[FC] RecordingSink: mkdir('" << accum
                                  << "') failed: " << std::strerror(errno) << "\n";
                        return false;
                    }
                    struct stat st{};
                    if (stat(accum.c_str(), &st) != 0 || !S_ISDIR(st.st_mode)) {
                        std::cerr << "[FC] RecordingSink: path component '" << accum
                                  << "' exists but is not a directory; refusing to write CSV "
                                     "below it\n";
                        return false;
                    }
                }
            }
        }
        if (i < dir.size()) {
            accum.push_back(dir[i]);
        }
    }
    return true;
}

// UTC timestamp like 20260508T123456Z, suitable for embedding in a
// filename. std::gmtime is used because std::localtime would tag the
// file with the operator's wall-clock TZ — log correlation across
// machines becomes fragile.
std::string utc_filestamp_now() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t t = std::chrono::system_clock::to_time_t(now);
    std::tm tm_utc{};
    gmtime_r(&t, &tm_utc);
    char buf[32] = {0};
    // 20260508T123456Z is 16 chars + null.
    std::strftime(buf, sizeof(buf), "%Y%m%dT%H%M%SZ", &tm_utc);
    return std::string(buf);
}

} // namespace

RecordingSink::RecordingSink(const std::string& log_dir) {
    if (!ensure_directory(log_dir)) {
        return;
    }
    path_ = log_dir + "/sink_" + utc_filestamp_now() + ".csv";
    file_ = std::fopen(path_.c_str(), "w");
    if (file_ == nullptr) {
        std::cerr << "[FC] RecordingSink: fopen('" << path_ << "') failed: " << std::strerror(errno)
                  << "\n";
        return;
    }
    // Header: stable, machine-readable, matches the order in writeChannels.
    std::fprintf(file_, "timestamp_s,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4\n");
    std::fflush(file_);
    std::cout << "[FC] RecordingSink writing to " << path_ << "\n";
}

RecordingSink::~RecordingSink() {
    if (file_ != nullptr) {
        std::fclose(file_);
        file_ = nullptr;
    }
}

void RecordingSink::writeChannels(const BetaFlightCommand& cmd, double timestamp_s) {
    if (file_ == nullptr) {
        return;
    }
    // %.6f matches the TEL JSON precision so a row's timestamp_s lines
    // up with the corresponding TEL frame to the microsecond.
    const int written =
        std::fprintf(file_, "%.6f,%u,%u,%u,%u,%u,%u,%u,%u\n", timestamp_s,
                     static_cast<unsigned>(cmd.roll), static_cast<unsigned>(cmd.pitch),
                     static_cast<unsigned>(cmd.yaw), static_cast<unsigned>(cmd.throttle),
                     static_cast<unsigned>(cmd.aux1), static_cast<unsigned>(cmd.aux2),
                     static_cast<unsigned>(cmd.aux3), static_cast<unsigned>(cmd.aux4));
    // fflush after every row so a SIGINT mid-run leaves a complete
    // tail. The throughput is 50 rows/sec (~3 KB/s) so the cost is
    // negligible relative to the diagnostic value.
    const int flushed = std::fflush(file_);
    if (written < 0 || flushed != 0 || std::ferror(file_)) {
        // Disk full, read-only remount, EIO, etc. Close the file so
        // ok() flips false; the operator can see the failure via
        // rows_lost() and the (future S0.8) TEL `msp_tx_ratio` field.
        // Log only on the FIRST error so a sustained failure doesn't
        // spam stderr at 50 Hz.
        if (rows_lost_ == 0) {
            std::cerr << "[FC] RecordingSink write failure on '" << path_ << "' (errno=" << errno
                      << " " << std::strerror(errno) << "); closing log + flipping ok() to false\n";
        }
        ++rows_lost_;
        std::fclose(file_);
        file_ = nullptr;
        return;
    }
    ++rows_written_;
}

FakeBetaflightSink::FakeBetaflightSink(const std::string& host, int port)
    : host_(host), port_(port) {
    if (port <= 0 || port > 65535) {
        std::cerr << "[FC] FakeBetaflightSink: port " << port << " out of range [1, 65535]\n";
        return;
    }

    // Resolve once and cache; writeChannels reuses dest_addr_ on every
    // tick instead of re-parsing the host string at 50 Hz.
    dest_addr_.sin_family = AF_INET;
    dest_addr_.sin_port = htons(static_cast<std::uint16_t>(port_));
    if (inet_pton(AF_INET, host_.c_str(), &dest_addr_.sin_addr) != 1) {
        std::cerr << "[FC] FakeBetaflightSink: invalid host '" << host_
                  << "', expected dotted-quad IPv4 literal (e.g. 127.0.0.1)\n";
        return;
    }

    socket_fd_ = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (socket_fd_ < 0) {
        std::cerr << "[FC] FakeBetaflightSink: socket() failed: " << std::strerror(errno) << "\n";
        return;
    }
    std::cout << "[FC] FakeBetaflightSink dest=" << host_ << ":" << port_ << "\n";
}

FakeBetaflightSink::~FakeBetaflightSink() {
    if (socket_fd_ >= 0) {
        ::close(socket_fd_);
        socket_fd_ = -1;
    }
}

void FakeBetaflightSink::writeChannels(const BetaFlightCommand& cmd, double timestamp_s) {
    if (socket_fd_ < 0) {
        return;
    }

    // Self-contained JSON per-datagram. snprintf to a stack buffer keeps
    // the fast path allocation-free; the worst-case length with all
    // 6-digit channels and a wide timestamp is ~150 bytes, well under
    // the 256-byte buffer.
    char buf[256];
    const int n = std::snprintf(buf, sizeof(buf),
                                "{\"type\":\"RC\",\"seq\":%llu,\"timestamp_s\":%.6f,"
                                "\"channels\":[%u,%u,%u,%u,%u,%u,%u,%u]}",
                                static_cast<unsigned long long>(seq_), timestamp_s,
                                static_cast<unsigned>(cmd.roll), static_cast<unsigned>(cmd.pitch),
                                static_cast<unsigned>(cmd.yaw), static_cast<unsigned>(cmd.throttle),
                                static_cast<unsigned>(cmd.aux1), static_cast<unsigned>(cmd.aux2),
                                static_cast<unsigned>(cmd.aux3), static_cast<unsigned>(cmd.aux4));
    if (n <= 0 || static_cast<std::size_t>(n) >= sizeof(buf)) {
        // Should be impossible at the documented channel ranges; treat
        // as a soft drop and keep going.
        ++frames_dropped_;
        return;
    }

    const ssize_t sent =
        ::sendto(socket_fd_, buf, static_cast<std::size_t>(n),
                 /*flags=*/0, reinterpret_cast<const sockaddr*>(&dest_addr_), sizeof(dest_addr_));
    if (sent < 0) {
        const int e = errno;
        // Soft errors -- bumping frames_dropped_ keeps ok() true and the
        // FC main loop ticking; the cause is upstream backpressure /
        // signal. Hard errors (closed fd, not-a-socket, no route to host)
        // mean the sink is wedged: close the fd so ok() flips false and
        // the operator can see the failure shape.
        const bool soft = (e == EAGAIN || e == EWOULDBLOCK || e == ENOBUFS || e == EINTR);
        if (soft) {
            ++frames_dropped_;
            return;
        }
        // First hard error: log once + close the fd. Subsequent calls
        // hit the (socket_fd_ < 0) early-return at the top of this
        // function with no further log spam.
        std::cerr << "[FC] FakeBetaflightSink: sendto failed irrecoverably (errno=" << e << " "
                  << std::strerror(e) << "); closing socket + flipping ok() to false\n";
        ++frames_dropped_;
        ::close(socket_fd_);
        socket_fd_ = -1;
        return;
    }
    ++frames_sent_;
    ++seq_;
}

} // namespace fc
