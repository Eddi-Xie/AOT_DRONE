# Hardware-in-the-Loop (HIL) Bench

> **Status (2026-05-04):** scaffolding planned in Sprint 0 task S0.7 of
> `docs/development_plan.md`. The `IRcSink` abstraction, three sink
> implementations (`NullSink`, `RecordingSink`, `FakeBetaflightSink`), the
> `MspRcSink` USB driver, and the `scripts/dev/fake_betaflight_listener.py`
> harness are not yet committed. This document describes the *target* design
> so the implementation can match it.

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
- **Closed-loop fake-FC echoes.** `FakeBetaflightSink` emits MSP frames over
  UDP that a Python harness (`fake_betaflight_listener.py`) receives,
  decodes, and asserts against an expected channel sequence.
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

Default. Logs the first call ("`fc_app` startup observed: NullSink active —
no RC writes will be emitted"), then silent. Used in unit tests and for
runs where channel output is not interesting.

### 3.2 RecordingSink

Writes every `writeChannels` call as a CSV row to `logs/hil/sink_<utc>.csv`
with columns:

```text
timestamp_s, roll, pitch, yaw, throttle, aux1, aux2, aux3, aux4, mode, tracking_state, failsafe_state
```

Used for:

- Capturing baseline runs ("what does the controller do on this scenario today?")
- Diffing against a baseline after a controller change
- Feeding into post-bench analysis (`scripts/dev/read_flight_log.py` —
  planned, Sprint 1 P5.3, not yet committed — or a notebook)

### 3.3 FakeBetaflightSink

Emits each `writeChannels` as an MSP-framed UDP packet to a configured
host:port (default `127.0.0.1:9101`). Used together with
`scripts/dev/fake_betaflight_listener.py` to assert recorded mission
behaviours in CI.

### 3.4 MspRcSink

Real driver. Opens a serial device (`FC_RC_DEVICE`, default `/dev/cu.usbmodem*`
on macOS / `/dev/ttyACM*` on Linux), runs MSPv1 framing, sends
`MSP_SET_RAW_RC` (cmd 200) at 50 Hz with 16 bytes of `uint16_le` channels.

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
the FC failsafe state machine to `StaleHard` (ADR-003 / S0.4).

---

## 4. Recording a mission scenario

Mission scenarios are short (≤ 60 s) recorded sequences of (VIS UDP, CMD
TCP) inputs that exercise a slice of behaviour: "person walks across frame",
"vision drops mid-flight", "operator pushes Takeoff with no arm switch", etc.

> **Note:** the `record_mission.py` and `replay_mission.py` helpers below
> are planned scaffolding (Sprint 0 task S0.7 of `docs/development_plan.md`)
> and have not been committed yet. The workflow in this section describes
> the *target* behaviour. Until the scripts land, capture is manual (run
> the stack, observe `logs/hil/sink_*.csv`); replay is not yet possible.

Workflow (target):

```bash
# 1. Capture (live webcam + manual operator)
FC_RC_SINK=recording ./build/src/fc/fc_app &
python -m src.vision.main --source webcam:0 --mode detect ... &
python -m uvicorn src.backend.app:app --host 127.0.0.1 --port 8000 &
python scripts/dev/record_mission.py --output missions/walk_across.jsonl   # planned (S0.7)

# 2. Inspect the recorded VIS+CMD trace + the RecordingSink CSV
ls logs/hil/sink_*.csv
head missions/walk_across.jsonl
```

Replay (target — no live vision needed):

```bash
FC_RC_SINK=recording ./build/src/fc/fc_app &
python scripts/dev/replay_mission.py missions/walk_across.jsonl   # planned (S0.7)

# Then diff the new RecordingSink CSV against a baseline
diff logs/hil/sink_<new>.csv missions/walk_across.baseline.csv
```

Expected gates (apply once the scripts above are committed):

- For unchanged controller code: byte-for-byte match against baseline.
- For tuning changes: bounded delta (e.g. `|throttle_delta| < 20 µs` per
  row).
- For structural changes: a fresh baseline is recorded *only* after manual
  review of the new behaviour.

---

## 5. Acceptance criteria

A change touches in-flight behaviour if it modifies any of:

- `src/fc/`
- `src/backend/cmd_*.py`, `src/backend/tel_*.py`, `src/backend/vis_*.py`
- `src/vision/vision_pipeline.py`, `src/vision/main.py` (the publish path)
- `docs/message-spec.md`

Such changes MUST include:

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

- Sprint 0 implements the scaffold listed above (S0.7) and the USB MSP
  driver (S0.8).
- Sprint 1 adds the real-FC bench acceptance items (H3) and the latency
  probe report (Sprint 1 P1.7).
- Future: a CI job that runs `replay_mission.py` against each PR and
  surfaces the CSV diff in the PR comments.
