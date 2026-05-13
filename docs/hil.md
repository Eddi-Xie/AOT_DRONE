# Hardware-in-the-Loop (HIL) Bench

> **Status (2026-05-13):** S0.8 landed and bench-verified. All four
> `IRcSink` implementations are in tree: `NullSink` + `RecordingSink` +
> `FakeBetaflightSink` (S0.7) + `MspRcSink` (S0.8 — real Betaflight
> over USB MSPv1). First bench session caught a safety-critical
> channel-mapping bug (RPYT-instead-of-AETR wire order) that would
> have been catastrophic with motors attached; documented in the
> writeChannels comment block and Section 3.4 below. The MSP_RC
> read-back channel log is the operator's substitute for Configurator's
> Receiver tab (USB-CDC port exclusivity prevents running both at
> once). `replay_mission.py` is still a stub (offline CSV summary
> only — full CMD-replay is Sprint 1).

The HIL bench is the project's last line of defence between a software change
and a real propellered airframe. ADR-004 mandates HIL acceptance before any
autonomous flight; ADR-005 chooses USB-MSP as the Sprint 0 transport with
UART pivot if USB arming proves unreliable.

This document describes how the bench is built, how to run a recorded mission
through it, and the acceptance criteria each new in-flight-behaviour change
must satisfy.

---

## 1. Why we have a HIL bench

The audit (March-April 2026) found ~150 issues; many touch the FC control
loop or the wire formats that feed it. Reviewing diffs is necessary but not
sufficient — the right rule is "every change that could move RC channels
gets a dry-run before motors draw current".

The HIL bench gives us:

- **Sink-recording dry runs.** `RecordingSink` writes every channel write to
  CSV at the rate of the control loop. After a change, replay any recorded
  mission and diff the CSV against a baseline.
- **Closed-loop fake-FC echoes.** `FakeBetaflightSink` emits one JSON
  datagram per tick over UDP (see Section 3.3 for the wire format) that
  a Python harness (`fake_betaflight_listener.py`) receives, decodes,
  and asserts against an expected channel sequence. The binary MSPv1
  framing lives in `MspRcSink` instead — it's the encoding firmware
  expects, not what the test harness needs.
- **Real-FC validation with motors detached.** `MspRcSink` drives a real
  Betaflight FC over USB or UART; Betaflight Configurator's "Receiver" tab
  visualises the channels. ESCs may be powered (no thrust without motors).
- **Latency probe.** `scripts/dev/latency_probe.py` (planned, Sprint 1
  P1.7 — not yet committed) will inject a synthetic bbox into the vision
  UDP stream and measure the time until the corresponding RC channel is
  written. Target: vision → MSP <100 ms p95.

---

## 2. Bench topology

```text
+--------------------+      UDP VIS       +--------------------+
| Vision (laptop or  | -----------------> | Backend (laptop)   |
| Pi)                |                    |                    |
+--------------------+                    +-----+----+---------+
                                                |    |
                                          TCP CMD    UDP TEL
                                                |    |
                                                v    |
                                          +----------+--------+
                                          | fc_app (laptop or |
                                          | Pi)               |
                                          +---+--------+------+
                                              |        |
                                  IRcSink     |        | UDP TEL (echo)
                                              v        v
                                  +--------------------+   +-+----------------+
                                  | NullSink           |   | Backend WS clients|
                                  | RecordingSink      |   +-------------------+
                                  | FakeBetaflightSink |
                                  | MspRcSink          |
                                  +--------------------+
                                          |
                                          | (MspRcSink only)
                                          v
                                  +----------------+
                                  | Betaflight FC  |
                                  | (USB or UART)  |
                                  +----------------+
```

Sink choice is set via `FC_RC_SINK=null|recording|fake|msp` env var.

---

## 3. Sink behaviours

### 3.1 NullSink

Default. Logs the first call only (`[FC] NullSink received first channel
write at t=… …`), then silent for the remainder of the run so 50 Hz
output doesn't drown stderr. Counts every write internally for tests.
Used as the safe default and in unit tests.

### 3.2 RecordingSink

Writes every `writeChannels` call as a CSV row to
`<FC_RC_LOG_DIR>/sink_<utc>.csv` (default `<repo>/logs/hil/`). The
filename embeds a UTC stamp like `sink_20260508T222239Z.csv` so files
sort lexicographically by start time and don't collide on rapid
restart. The directory is created if missing (`mkdir -p`).

Header (line 1):

```text
timestamp_s,roll,pitch,yaw,throttle,aux1,aux2,aux3,aux4
```

Each subsequent line is one tick. `timestamp_s` is the FC monotonic
clock (matching the corresponding TEL frame's `timestamp_s` to the
microsecond); the eight channels are the integer micro-seconds in the
RC band (1000–2000). `fflush` is called after every row so a SIGINT
mid-run leaves a complete tail.

Env vars:

- `FC_RC_SINK=recording` — selects this sink.
- `FC_RC_LOG_DIR=<path>` — where to write CSVs. Default `logs/hil`.

Used for:

- Capturing baseline runs ("what does the controller do on this scenario today?")
- Diffing against a baseline after a controller change
- Feeding into `scripts/dev/replay_mission.py` (offline summary today;
  full CMD-injection replay in Sprint 1).

### 3.3 FakeBetaflightSink

Emits each `writeChannels` as a self-contained UDP datagram (one frame
per tick) to a configured host:port. Used together with
`scripts/dev/fake_betaflight_listener.py` to assert recorded mission
behaviours in CI.

Wire format (one JSON datagram per tick):

```json
{"type":"RC","seq":7,"timestamp_s":0.140000,
 "channels":[1500,1500,1500,1100,1000,2000,1000,1000]}
```

`seq` advances monotonically from 0 within a single fc_app process.
`channels` is exactly `[roll, pitch, yaw, throttle, aux1, aux2, aux3,
aux4]` in microseconds. JSON lines (rather than MSPv1 binary framing)
keep the harness simple and parallel with the TEL/VIS UDP shape; the
binary MSPv1 encoding lives in `MspRcSink` (S0.8) where it actually
talks to firmware.

Env vars:

- `FC_RC_SINK=fake` — selects this sink.
- `FC_RC_FAKE_HOST=<dotted-quad>` — destination IPv4 (default `127.0.0.1`).
- `FC_RC_FAKE_PORT=<int>` — destination UDP port (default `9101`,
  clear of TEL `9001` / CMD `9002` / VIS `9003`).

Errors are logged-but-tolerated: a transient `EAGAIN`/`ENOBUFS` increments
an internal `frames_dropped_` counter but does not flip `ok()` to false,
so the FC main loop keeps ticking.

### 3.4 MspRcSink (S0.8)

Real driver. Opens a USB-CDC serial node (`FC_RC_DEVICE`), runs MSPv1
framing in `src/fc/implementation/MspFraming.cpp`, sends `MSP_SET_RAW_RC`
(cmd 200) at 50 Hz with 16 bytes of `uint16_le` channels, and polls
`MSP_RC` (cmd 105) at 5 Hz for arm-switch state plus a bench-
verification channel log.

Env vars:

- `FC_RC_SINK=msp` — selects this sink.
- `FC_RC_DEVICE=<path>` — required. `/dev/cu.usbmodem*` on macOS,
  `/dev/ttyACM*` on Linux. Validated up-front via `stat()` + `S_ISCHR`
  so operator typos refuse cleanly with the offending path named.
- `FC_RC_BAUD=<int>` — default `115200`. Allowlist enforced inside
  `from_device`: 9600, 19200, 38400, 57600, 115200, 230400, 460800,
  921600.

#### Channel-order asymmetry (read this before touching the code)

MSPv1's channel-order convention is unfortunately ASYMMETRIC between
write and read. Both sides are documented in `MspRcSink.cpp` but the
TL;DR is:

- **`MSP_SET_RAW_RC` (write)**: wire order is **AETR1234** under the
  default Betaflight rcmap — Aileron (Roll), Elevator (Pitch),
  Throttle, Rudder (Yaw), AUX1..4. Betaflight's `rxMspFrameReceive`
  applies `rcmap[]` to remap from this wire order to its internal
  channel layout. We MUST send AETR; rcmap reverses the remap.

- **`MSP_RC` (read)**: reply order is **RPYT1234** — Betaflight's
  internal channel constants (`ROLL=0, PITCH=1, YAW=2, THROTTLE=3,
  AUX1=4...`) from `rc.h`. Betaflight does NOT reverse-apply rcmap
  on the way back; `msp.c` just dumps `rcData[]` indexed by these
  constants.

So a writeChannels → readback round-trip sees `cmd.throttle` at wire
position 2 on write but at reply position 3 on read. The bench
verification log labels (`y=ch[2] t=ch[3]`) reflect the RPYT read
order; the writeChannels array (`{roll, pitch, throttle, yaw, ...}`)
reflects the AETR write order. They look swapped but are both
correct — the asymmetry is in Betaflight's MSP layer.

The first S0.8 bench run caught the original RPYT-write bug exactly
because of this asymmetry: the FC reported throttle on the yaw
channel via MSP_RC.

#### Boot sequence

1. `open(O_RDWR | O_NOCTTY | O_NONBLOCK)`, configure termios
   (115200 8N1 raw, `VMIN=0`, `VTIME=0`, no flow control), `tcflush`
   to drop boot chatter.
2. Send `MSP_API_VERSION`. Wait up to 500 ms via `select()` (50 ms
   polls). On no-reply, `boot_probe_failed_=true` → `ok()` flips
   false → caller refuses fc_app startup.
3. Send `MSP_RC_TUNING` (best-effort). Reply is logged for operator
   review; on no-reply, `tuning_mismatch_=true` (gates Tracking /
   Takeoff transitions identically to a boot-probe failure but does
   not flip `ok()` — fc_app keeps running, just refuses mode upgrades).
   Operator-confirm UI for the mismatch path lands with S0.14;
   recovery in S0.8 is "stop fc_app, reseat USB / power-cycle FC,
   restart".
4. Begin 50 Hz `MSP_SET_RAW_RC` heartbeat in AETR wire order.
   Surfaced as TEL field `msp_tx_ratio` (EWMA, α=0.05, init=1.0
   optimistic).
5. Every 10th tick (5 Hz), `poll_msp_rc_` drains pending bytes
   non-blocking, latches `arm_switch_` from aux1 ≥1700 µs (force-
   cleared after 1 s without a reply), emits the bench-verification
   channel log, and sends a fresh MSP_RC query for the next poll.

#### Error handling

Short writes / `EAGAIN`/`EINTR` retry-once with progress vs no-progress
separation (any successful write resets the budget; only consecutive
no-progress attempts count toward the cap). Hard errors
(`EPIPE`/`EBADF`/...) close the fd → `ok()` flips false. `main.cpp`
maintains a parallel `sink_degraded_engaged` latch alongside the
existing `failsafe_engaged` (CMD-stale) latch; both converge on
`setControlMode(LandSafely)` but each tracks its own entry/exit so
one recovery doesn't spuriously clear the other. SIGPIPE is ignored
process-wide so a USB unplug can't terminate fc_app outright.

Diagnostics: `MspRcSink` exposes `tx_ratio()`, `arm_switch()`,
`tuning_mismatch()`, `parse_errors()`, `writes_attempted()`, and
`writes_succeeded()` for tests + future TEL surfacing.

#### Bench acceptance procedure (S0.8 merge gate)

> **Run with motors physically detached.** ADR-004 mandates this for
> any in-flight-behaviour change. Arming via USB is firmware-dependent
> and may be refused — that pivot is documented in ADR-005 and arming
> work moves to the Sprint 1 H2 UART path.

**One-time Betaflight setup** (only if your FC's Receiver isn't
already on MSP):

1. With fc_app NOT running, open Betaflight Configurator, connect
   to the FC.
2. Configuration tab → Receiver → set "Serial Receiver Provider" to
   **MSP RX input**.
3. Save & Reboot. Disconnect Configurator (the USB-CDC port is
   exclusive — fc_app can't share it with Configurator).

Without this, the FC accepts `MSP_SET_RAW_RC` writes silently but
ignores them, reporting whatever its physical RX is doing (typically
failsafe values like ~885 µs throttle and 1500 µs centered sticks).

**Per-bench flow:**

1. **Hardware prep.** Remove motor connectors (or pull props if
   motors are still attached). Visually confirm. Plug FC into the
   laptop via USB.

2. **Identify the device path.**

   ```bash
   ls /dev/cu.usbmodem* 2>/dev/null   # macOS
   ls /dev/ttyACM* 2>/dev/null        # Linux
   ```

3. **Build + start fc_app.**

   ```bash
   cmake --build build -j
   FC_RC_SINK=msp \
   FC_RC_DEVICE=/dev/cu.usbmodem... \
     ./build/src/fc/fc_app
   ```

   Confirm stderr shows the boot probe ok, tuning probe reply (or
   tuning_mismatch=true), and "`RC sink=msp`". Within ~200 ms the
   periodic MSP_RC log starts:

   ```text
   [FC] MspRcSink: MSP_RC=[r=1500 p=1500 y=1500 t=1000 a1=1000 a2=2000 a3=1000 a4=1000]
   ```

   This is the FC's view of the channels (RPYT order — see
   asymmetry note above). With fc_app idle in LandSafely, expect
   `r=p=y=1500`, `t=1000`, `a1=1000`, `a2=2000`, `a3=a4=1000`. If
   `t` and `y` look swapped, fc_app's AETR write order is wrong —
   regression of the S0.8 fix.

4. **Start the webapp + backend** in separate terminals so CMD
   frames can reach fc_app:

   ```bash
   python -m uvicorn src.backend.app:app    # backend
   npm --prefix src/webapp run dev          # webapp
   ```

5. **Mode toggles.** From the webapp, switch through Manual /
   LandSafely / Tracking / Takeoff modes. fc_app's stderr should
   show `[FC] Applied CMD seq=... desired_mode=N` for each toggle
   AND the MSP_RC log should reflect the mode's internal setpoints
   (e.g. Takeoff drives throttle to the configured hover throttle).

6. **Setpoint deflection** *(blocked on no setpoint UI in webapp —
   open issue)*. Currently the webapp only sends `desired_mode`,
   not explicit r/p/y/t setpoints. Until that ships, exercise the
   deflection path via direct CMD injection (a Python script hitting
   the backend's CMD WS endpoint with explicit `setpoints` fields).
   See the open follow-up in Section 6.

7. **Failsafe drop.** Stop the CMD stream (close the webapp tab or
   kill the backend). Within `proto::CMD_TIMEOUT_S` (0.5 s) you
   should see:

   ```text
   [FC] CMD link stale (age=...s > 0.5s); entering LandSafely failsafe
   ```

   and the MSP_RC log should reflect the LandSafely setpoints. The
   drone must NOT arm.

8. **Sink-degraded path.** Unplug USB while fc_app is running.
   Within ~2 ticks (40 ms):

   ```text
   [FC] MspRcSink: write() failed (errno=...): ...; closing fd, ok() flipping to false
   [FC] RC sink degraded (ok=false, tx_ratio=...); entering LandSafely failsafe
   ```

   The MSP_RC log stops because there's no fd to read from.

9. **Arming via USB (informational, conditional ADR-005).** Re-plug,
   restart fc_app, drive aux1 high via setpoint injection. Outcome
   is firmware-dependent:
   - **Betaflight arms:** great. Note the firmware version + RX
     configuration in the PR description.
   - **Betaflight refuses:** add `ADR-005` to `docs/decisions.md`
     recording the symptom and deferring arming to the Sprint 1 H2
     UART pivot. S0.8 still passes — channel tracking is the gate,
     not arming.

10. **Wrap.** SIGINT fc_app. The "[FC] fc_app stopped" line should
    appear cleanly. Disconnect USB.

If steps 1-5, 7, 8 all pass with the MSP_RC log showing correct
channel values throughout, S0.8's foundational bench-acceptance gate
is satisfied. Steps 6 and 9 are deferred to follow-up bench sessions
once the missing tooling lands (setpoint injection script,
ADR-005 outcome).

---

## 4. Quickstart — running the bench today

Two flows are wired up end-to-end as of S0.7. Both run on a developer
laptop with no FC plugged in.

### 4.1 RecordingSink — capture a CSV trace

```bash
# Build once.
cmake -S . -B build && cmake --build build -j

# Run fc_app with the recording sink. Default log dir is logs/hil/.
FC_RC_SINK=recording ./build/src/fc/fc_app
```

Output:

```text
[FC] RecordingSink writing to logs/hil/sink_20260508T222239Z.csv
[FC] fc_app running. CMD TCP:9002 TEL UDP:127.0.0.1:9001 RC sink=recording
```

`Ctrl-C` to stop. Inspect the resulting CSV:

```bash
ls logs/hil/
head -3 logs/hil/sink_*.csv
python scripts/dev/replay_mission.py logs/hil/sink_<utc>.csv
```

`replay_mission.py` prints a summary and (with `--check-band`) fails if
any sample is outside the [1000, 2000] µs RC band.

### 4.2 FakeBetaflightSink — closed-loop UDP harness

```bash
# Terminal A: listener (asserts seq monotonicity + Hz + band)
python scripts/dev/fake_betaflight_listener.py --duration-s 5

# Terminal B: fc_app emits to 127.0.0.1:9101
FC_RC_SINK=fake ./build/src/fc/fc_app
```

Listener output on success:

```text
[harness] listening on 127.0.0.1:9101 for 5.0s (expect 50.0 Hz +/- 30%)
[harness] received=234 seq=0..233 gaps=0 observed_hz=49.91 (target 50.00 +/-30%) decode_errors=0
[harness] OK
```

Listener exits 0 on a clean run, exit 2 on any failure (decode error in
strict mode, out-of-band channel, observed Hz outside tolerance, seq
gap > `--max-seq-gap`, or fewer than `--min-frames` frames).

### 4.3 Mission replay (target — Sprint 1)

The full mission-replay workflow — record a (VIS, CMD) timeline and play
it back into the FC's TCP CMD listener — is a Sprint 1 task. Until then
capture is manual (run the stack live, observe `logs/hil/sink_*.csv`).

The S0.7 scaffold gives you everything needed to assert *behaviour at
the channel-write boundary* — i.e. "given these recorded inputs, the FC
emits this RC trace". The mission-injection harness only needs to feed
recorded CMDs at the right cadence; the assertion machinery is already
in place via RecordingSink CSV diff or fake_betaflight_listener strict
mode.

---

## 5. Acceptance criteria

A change touches in-flight behaviour if it modifies any of these
implementations:

- `src/fc/`
- `src/backend/cmd_*.py`, `src/backend/tel_*.py`, `src/backend/vis_*.py`
- `src/vision/vision_pipeline.py`, `src/vision/main.py` (the publish path)

Protocol implementations MUST also update `docs/message-spec.md` whenever
they alter the wire contract, field meanings, or message timing/semantics.
Documentation-only edits to `docs/message-spec.md` (e.g. clarifications,
typo fixes, or adding optional documentation fields without an implementation
change) do NOT, on their own, trigger the in-flight-behaviour gates below.

In-flight-behaviour changes MUST include:

- A unit test (where applicable).
- A replay against at least one recorded mission scenario
  (`replay_mission.py` — once committed; until then, manual bench observation).
- For changes that produce a different intended channel output, a new
  baseline CSV stored in `missions/`.

For changes that go further and need the real FC:

- A bench session with `MspRcSink` and Betaflight Configurator open, with
  motors physically detached.
- A note added to the change's PR description: "Bench validated:
  date — observation".

For first-flight clearance, see the Pre-First-Flight Gate in
`docs/development_plan.md`.

---

## 6. Open follow-ups

- **Bench step 6 — setpoint-injection script**: the webapp's
  ControlPanel currently only sends `desired_mode`; no UI for explicit
  roll/pitch/yaw/throttle setpoints. Bench step 6 (±20% stick
  deflection) needs either webapp UI work OR a small Python script
  that POSTs CMD frames with explicit `setpoints` fields to the
  backend. Quick win for the next bench session.
- **Bench step 9 — USB-arming verification**: outcome determines
  whether `ADR-005` lands and arming work moves to Sprint 1 H2 UART
  pivot. Run when convenient — non-blocking for S0.8 channel-tracking
  acceptance.
- **Sprint 1 H2 — UART pivot** (conditional on step 9): if USB arming
  proves unreliable, swap `MspRcSink`'s transport from USB-CDC to a
  hardware UART. The `IRcSink` interface keeps this a transport swap,
  not a rewrite. Unblocks running Configurator and `MspRcSink`
  simultaneously (UART for fc_app, USB for Configurator).
- **Sprint 1 H2 — periodic MSP_RC_TUNING re-probe**: today
  `tuning_mismatch_` is set-once and only clears on fc_app restart.
  An in-flight re-probe + operator-confirm UI lands with the UART
  pivot.
- **Sprint 1 H3 — full mission bench acceptance**: run recorded
  mission scenarios (S0.7's RecordingSink CSVs replayed via
  `replay_mission.py`'s CMD-injection mode) against a real FC over
  `MspRcSink`. Add the latency probe (Sprint 1 P1.7) to measure
  vision → MSP <100 ms p95.
- **Sprint 1 — `record_mission.py`** plus a CMD-injection mode for
  `replay_mission.py`: capture a (VIS, CMD) timeline as JSONL, replay
  it into the FC's TCP CMD listener, assert the resulting
  RecordingSink CSV matches the recorded baseline within tolerance.
- **Sprint 1+ — CI integration**: run `replay_mission.py` against each
  PR and surface the CSV diff in PR comments.
- **Future — atomic `std::cout` writes in fc_app**: pre-existing race
  between main and the CommandServer worker thread can interleave log
  lines mid-flush. Surfaced via CI flake during S0.8; deferred fix.
