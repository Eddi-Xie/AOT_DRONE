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

  private:
    std::string path_;
    std::FILE* file_ = nullptr;
    std::uint64_t rows_written_ = 0;
};

} // namespace fc
