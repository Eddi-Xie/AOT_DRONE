# Hardware-in-the-Loop (HIL) Bench

> **Status (2026-05-08):** S0.7 scaffold landed. `IRcSink` interface +
> `NullSink` + `RecordingSink` + `FakeBetaflightSink` are implemented in
> `src/fc/header/RcSink.h` / `src/fc/implementation/RcSink.cpp`; the
> `scripts/dev/fake_betaflight_listener.py` Python harness asserts seq
> monotonicity, observed Hz, and channel band. `replay_mission.py` is a
> stub (offline CSV summary only — full CMD-replay is Sprint 1). The
> real-hardware `MspRcSink` is the S0.8 task (next).

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

### 3.4 MspRcSink (S0.8 — not yet implemented)

Real driver. Will open a serial device (`FC_RC_DEVICE`, default `/dev/cu.usbmodem*`
on macOS / `/dev/ttyACM*` on Linux), run MSPv1 framing, send
`MSP_SET_RAW_RC` (cmd 200) at 50 Hz with 16 bytes of `uint16_le` channels.
For now `FC_RC_SINK=msp` errors out at startup pointing at S0.8.

Boot sequence:

1. Open device at 115200 8N1.
2. Send `MSP_API_VERSION` (cmd 1). Refuse to leave Manual / LandSafely modes
   until a valid reply arrives within 250 ms.
3. Send `MSP_RC_TUNING` (cmd 111). Compare reply against the rate profile
   recorded in `scripts/betaflight/aot_drone.diff`. On mismatch, log a
   WARNING and require operator confirm before mode change.
4. Begin 50 Hz `MSP_SET_RAW_RC` writeback. Track success ratio in
   `msp_tx_ratio` (TEL field).

Errors: short writes / `EAGAIN` retry once; persistent failure transitions
the FC failsafe state machine to `StaleHard` (see `docs/development_plan.md`
S0.4 for the failsafe state-machine definition).

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

- **S0.8 — `MspRcSink`** (next): real Betaflight via USB MSP. Reuses the
  same `IRcSink` interface — code path swap, not rewrite. Until landed,
  `FC_RC_SINK=msp` errors out at startup with a "deferred to S0.8"
  message.
- **Sprint 1 H3 — real-FC bench acceptance**: run the recorded mission
  scenarios above against a real FC over `MspRcSink`, motors detached,
  Betaflight Configurator open. Add the latency probe (Sprint 1 P1.7)
  to measure vision → MSP <100 ms p95.
- **Sprint 1 — `record_mission.py`** plus a CMD-injection mode for
  `replay_mission.py`: capture a (VIS, CMD) timeline as JSONL, replay
  it into the FC's TCP CMD listener, assert the resulting
  RecordingSink CSV matches the recorded baseline within tolerance.
- **Sprint 1+ — CI integration**: run `replay_mission.py` against each
  PR and surface the CSV diff in PR comments.
