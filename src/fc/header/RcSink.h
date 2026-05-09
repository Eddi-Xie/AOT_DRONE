#pragma once

// IRcSink — abstraction over the destination for the FC's per-tick
// BetaFlightCommand RC channel writes.
//
// Why this exists: until S0.7, src/fc/implementation/main.cpp computed
// the BetaFlightCommand each tick and discarded it via
// `(void)flight_controller.updateTimeStep(dt)`. fc_app was a simulator.
// The HIL bench gate (ADR-004) requires that every in-flight code change
// be replayed through a deterministic harness against a real or fake
// Betaflight before merge. IRcSink + the FC_RC_SINK env knob lets the
// same fc_app binary route channel writes to:
//
//   - NullSink           : default; logs first call then silent (matches
//                          the legacy `(void)` behaviour but observable
//                          via TEL `msp_tx_ratio` once added).
//   - RecordingSink      : CSV log per tick for offline analysis.
//   - FakeBetaflightSink : UDP-echo to a Python harness that asserts
//                          expected channel sequences.
//   - MspRcSink          : real Betaflight via USB MSP (S0.8). Refused
//                          here in S0.7 with an explicit error.
//
// The interface keeps the FC binary loosely coupled to its sink so the
// transport pivot (USB → UART, S0.8 → Sprint 1 H2) is a swap rather than
// a rewrite.

#include "FlightController.h" // BetaFlightCommand

#include <netinet/in.h> // sockaddr_in (cached destination in FakeBetaflightSink)

#include <cstdio>
#include <string>

namespace fc {

class IRcSink {
  public:
    virtual ~IRcSink() = default;

    // Called once per FC tick (50 Hz nominal) with the current channel
    // command and the FC-monotonic timestamp. Implementations must not
    // block the control loop — buffer or drop on slow downstream.
    virtual void writeChannels(const BetaFlightCommand& cmd, double timestamp_s) = 0;

    // Health: false signals the sink is in a degraded state (file write
    // failure, socket closed, MSP boot probe failure). main.cpp surfaces
    // this in TEL once we add `msp_tx_ratio` (S0.8) and uses it as a
    // pre-arm gate.
    virtual bool ok() const = 0;

    // Diagnostic name ("null", "recording", "fake", "msp"). Logged at
    // startup so operators can confirm which sink is active.
    virtual std::string name() const = 0;

    // EWMA over recent write attempts. Returned as a float in [0.0, 1.0]
    // and surfaced in TEL JSON as `msp_tx_ratio` (S0.8 commit 4) so the
    // operator can see the USB link's success rate degrade in real time
    // before it crosses ok()'s hard threshold. Sinks without a flaky
    // transport (NullSink, RecordingSink, FakeBetaflightSink) inherit the
    // 1.0 default — only MspRcSink overrides.
    virtual double tx_ratio() const { return 1.0; }

    // Most-recent observed RC arm-switch state, latched from MSP_RC
    // replies by MspRcSink. The data path is in S0.8; the consumer is the
    // S0.14 arm-authority gate's "RC switch held ≥1 s" condition. Non-
    // MSP sinks have no view of the FC-side arm switch and inherit false.
    virtual bool arm_switch() const { return false; }

    // True iff the FC's rate profile didn't reply to MSP_RC_TUNING within
    // the boot-probe window. main.cpp's apply_control_mode_safely refuses
    // Tracking/Takeoff transitions in this state per the dev-plan
    // requirement to "log mismatch as WARNING; continue but require
    // operator confirm before mode change". Non-MSP sinks have no rate
    // profile to mismatch and inherit false.
    virtual bool tuning_mismatch() const { return false; }
};

// Default sink. Counts writes for diagnostics; logs only the first one
// then stays silent so a 50 Hz log spam doesn't drown stderr. Matches
// the pre-S0.7 discard behaviour but with a recorded count.
class NullSink final : public IRcSink {
  public:
    void writeChannels(const BetaFlightCommand& cmd, double timestamp_s) override;
    bool ok() const override { return true; }
    std::string name() const override { return "null"; }

    // Test/diagnostic accessor — never read by the production loop.
    std::uint64_t calls() const { return calls_; }

  private:
    std::uint64_t calls_ = 0;
    bool first_logged_ = false;
};

// Append-only CSV log of every channel write. Used for offline analysis
// of HIL bench runs and as the input to scripts/dev/replay_mission.py.
//
// Format: header `timestamp_s,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4`
// followed by one row per writeChannels() call.
//
// File layout: <log_dir>/sink_<utc>.csv where <utc> is YYYYMMDDTHHMMSSZ
// at construction time. Each call flushes via std::fflush so a SIGINT
// crash mid-run does not lose tail data.
class RecordingSink final : public IRcSink {
  public:
    // Opens <log_dir>/sink_<utc>.csv. log_dir is created if missing
    // (mkdir -p). On failure, ok() returns false and writeChannels is
    // a no-op so the FC main loop keeps ticking.
    explicit RecordingSink(const std::string& log_dir);
    ~RecordingSink() override;

    // Non-copyable: owns a FILE*.
    RecordingSink(const RecordingSink&) = delete;
    RecordingSink& operator=(const RecordingSink&) = delete;

    void writeChannels(const BetaFlightCommand& cmd, double timestamp_s) override;
    bool ok() const override { return file_ != nullptr; }
    std::string name() const override { return "recording"; }

    // Test/diagnostic accessors.
    const std::string& path() const { return path_; }
    std::uint64_t rows_written() const { return rows_written_; }
    std::uint64_t rows_lost() const { return rows_lost_; }

  private:
    std::string path_;
    std::FILE* file_ = nullptr;
    std::uint64_t rows_written_ = 0;
    // Bumped when fprintf/fflush returns an error (full disk, read-only
    // remount, EIO, etc). On the first such error we close file_ and let
    // ok() flip false so the operator-facing health field flips to bad.
    std::uint64_t rows_lost_ = 0;
};

// UDP echoes every channel write as a self-contained JSON datagram to a
// configured host:port. The Python harness (scripts/dev/
// fake_betaflight_listener.py) consumes these and asserts expected
// sequences for HIL replay tests.
//
// Datagram format (one frame per writeChannels):
//   {"type":"RC","seq":<u64>,"timestamp_s":<f>,"channels":[r,p,y,t,a1,a2,a3,a4]}
// Self-contained per-frame so packet loss is non-fatal — consumers fall
// forward without waiting for retransmits, matching the TEL/VIS UDP
// design.
//
// Errors are logged-but-tolerated: a transient EAGAIN / ENOBUFS does
// NOT take ok() to false. ok() flips false only when the socket can't
// be opened at construction (operator-fixable config error).
class FakeBetaflightSink final : public IRcSink {
  public:
    // host: dotted-quad IPv4 literal (matches FC_TEL_HOST validation).
    // port: 1..65535.
    FakeBetaflightSink(const std::string& host, int port);
    ~FakeBetaflightSink() override;

    FakeBetaflightSink(const FakeBetaflightSink&) = delete;
    FakeBetaflightSink& operator=(const FakeBetaflightSink&) = delete;

    void writeChannels(const BetaFlightCommand& cmd, double timestamp_s) override;
    bool ok() const override { return socket_fd_ >= 0; }
    std::string name() const override { return "fake"; }

    // Test/diagnostic accessors.
    std::uint64_t frames_sent() const { return frames_sent_; }
    std::uint64_t frames_dropped() const { return frames_dropped_; }
    int port() const { return port_; }

  private:
    std::string host_;
    int port_ = 0;
    int socket_fd_ = -1;
    // Cached destination resolved once at construction so writeChannels'
    // hot path doesn't re-parse the host string every tick. We store the
    // raw sockaddr_in rather than just in_addr to keep the sendto call
    // a single move-the-pointer-and-go.
    sockaddr_in dest_addr_{};
    std::uint64_t seq_ = 0;
    std::uint64_t frames_sent_ = 0;
    std::uint64_t frames_dropped_ = 0;
};

} // namespace fc
