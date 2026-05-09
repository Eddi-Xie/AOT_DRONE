#pragma once

// MspRcSink — IRcSink implementation that drives a real Betaflight FC
// over a USB serial port using the MSPv1 protocol (framing in MspFraming).
//
// This is the fourth IRcSink, plug-compatible with NullSink (default),
// RecordingSink (CSV log), and FakeBetaflightSink (UDP echo). Selected at
// startup via `FC_RC_SINK=msp FC_RC_DEVICE=/dev/cu.usbmodem... ./fc_app`.
// See docs/development_plan.md S0.8 for the bench-acceptance procedure.
//
// What it does on the wire:
//   - Boot probe: sends MSP_API_VERSION; if no reply within 500 ms, ok()
//     flips false (sink keeps its fd open so caller can log + refuse).
//   - Best-effort tuning probe: sends MSP_RC_TUNING; absence of reply
//     sets `tuning_mismatch_` (gates Tracking/Takeoff transitions in
//     S0.8 commit 4).
//   - 50 Hz heartbeat: every writeChannels() encodes MSP_SET_RAW_RC and
//     ships it with one EAGAIN/EINTR/short-write retry. Persistent
//     failure closes the fd → ok() flips false → main.cpp's parallel
//     sink-degraded latch routes to LandSafely.
//   - 5 Hz arm-switch poll: every 10th writeChannels drains pending
//     bytes and sends a fresh MSP_RC query. The latched arm switch is
//     exposed via arm_switch() for S0.14's three-condition arm gate.
//
// Hardware caveat (ADR-004 / docs/hil.md): bench acceptance MUST be run
// with motors physically detached. Arming via USB is firmware-dependent
// and may be refused — that pivot is documented in ADR-005 and arming
// work moves to the Sprint 1 H2 UART path. S0.8 succeeds either way as
// long as channels visibly track in Configurator's Receiver tab.

#include "FlightController.h" // BetaFlightCommand
#include "RcSink.h"           // IRcSink

#include <chrono>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace fc {

class MspRcSink final : public IRcSink {
  public:
    // Production factory. Opens the serial device, configures termios
    // (115200 8N1 raw, VMIN=0, VTIME=0, no flow control), and runs both
    // probes. Returns nullptr on open()/tcgetattr/tcsetattr/baud-rate
    // failure (the operator-fixable cases). Returns a sink with ok()=false
    // if MSP_API_VERSION did not reply — caller logs + refuses startup.
    static std::unique_ptr<MspRcSink> from_device(const std::string& path, int baud);

    // Test seam. Takes ownership of a pre-opened fd (typically from
    // socketpair(2) in unit tests) and skips open() / termios / probes —
    // the kernel-pipe peer doesn't speak MSP. Tests that need to drive
    // probe behaviour stage synthetic $M> replies on the peer fd before
    // exercising writeChannels(); a "run probes against this fd" mode
    // was tried and removed (review finding #10) because no test could
    // exercise it without a fully-implemented fake FC.
    static std::unique_ptr<MspRcSink> make_for_testing(int fd);

    ~MspRcSink() override;
    MspRcSink(const MspRcSink&) = delete;
    MspRcSink& operator=(const MspRcSink&) = delete;

    void writeChannels(const BetaFlightCommand& cmd, double timestamp_s) override;
    bool ok() const override { return fd_ >= 0 && !boot_probe_failed_; }
    std::string name() const override { return "msp"; }
    double tx_ratio() const override { return tx_ratio_; }
    bool arm_switch() const override { return arm_switch_; }

    // True iff MSP_RC_TUNING failed to reply within the boot-probe
    // window. The dev plan calls for "log mismatch as WARNING; continue
    // but require operator confirm before mode change" — main.cpp's
    // apply_control_mode_safely gates Tracking/Takeoff transitions on
    // this flag, accessed through the IRcSink virtual.
    bool tuning_mismatch() const override { return tuning_mismatch_; }

    // Diagnostic counters. tx_ratio() is the user-facing metric; these
    // raw counts let tests assert the EWMA arithmetic without depending
    // on float equality.
    std::uint64_t writes_attempted() const { return writes_attempted_; }
    std::uint64_t writes_succeeded() const { return writes_succeeded_; }

    // Bad-checksum frames discarded by the parser. Bumped at every drain
    // (boot probe, tuning probe, MSP_RC poll). A non-zero counter usually
    // signals USB-CDC bit errors or a framing-state mismatch that should
    // prompt the operator to reseat the cable; surfaced via TEL is a
    // future enhancement.
    std::uint64_t parse_errors() const { return parse_errors_; }

  private:
    explicit MspRcSink(int fd);

    // Send MSP_API_VERSION and wait up to 500 ms for a $M> reply with
    // matching cmd. Sets boot_probe_failed_ on timeout/write error.
    bool run_boot_probe_();

    // Send MSP_RC_TUNING and wait up to 500 ms for a $M> reply. On
    // timeout, sets tuning_mismatch_ but does not flip ok() — the dev
    // plan wants the FC to keep ticking, just refuse mode upgrades.
    void run_tuning_probe_();

    // Drain pending bytes non-blocking, latch arm_switch_ from any
    // MSP_RC reply, then write a fresh MSP_RC query so the next poll
    // (≈200 ms later at 5 Hz) has something to drain. Never blocks.
    void poll_msp_rc_();

    // Write the full buffer with one retry on EAGAIN/EINTR/short write.
    // Persistent failure closes fd_ and returns false (caller updates
    // tx_ratio_ accordingly; ok() now reads false).
    bool write_frame_(const std::uint8_t* buf, std::size_t n);

    int fd_ = -1;
    bool boot_probe_failed_ = false;
    bool tuning_mismatch_ = false;
    // EWMA init=1.0 (optimistic) so a healthy sink reads ~1.0 from the
    // first tick rather than ramping up from 0 over the warmup window.
    double tx_ratio_ = 1.0;
    bool arm_switch_ = false;
    std::uint64_t tick_counter_ = 0;
    std::uint64_t writes_attempted_ = 0;
    std::uint64_t writes_succeeded_ = 0;
    std::uint64_t parse_errors_ = 0;
    // Last time an MSP_RC reply landed and updated arm_switch_. Used by
    // poll_msp_rc_ to force arm_switch_=false if no reply has arrived
    // within kArmSwitchStaleMs — without this guard, a USB hiccup or FC
    // reboot loop could leave a stale "switch on" latch that S0.14's
    // arm-authority gate would then act on.
    std::optional<std::chrono::steady_clock::time_point> last_msp_rc_reply_time_;
    // Accumulator for incremental MSP_RC parse across polls. Boot
    // probes also feed it; their consumed-bytes drain leaves any tail
    // for the first regular poll to pick up.
    std::vector<std::uint8_t> rx_buffer_;
};

} // namespace fc
