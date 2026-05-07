# AOT Drone — Development Plan
**v2 — drafted 2026-05-04** (Eddi solo through May 11, then summer with John). Living document; updated across multiple Claude sessions.

> All file paths in this document are repo-relative. Run commands from your local checkout of the `AOT_DRONE` repository.

---

## Living-document conventions

This file is the canonical roadmap for the project and is used across multiple Claude/Codex chats. To keep it useful:

- **Checkboxes** track per-task status: `[ ]` not started, `[~]` in progress, `[x]` done, `[!]` blocked, `[-]` deferred.
- **Progress Log** at the bottom of this file gets a dated entry from each significant work session (one bullet per task touched). New chats should read it before starting.
- **File:line refs** are the source of truth — the audit findings are anchored to specific lines. When code moves, update the refs here.
- **Acceptance criteria are non-negotiable.** A task is `[x]` only after its acceptance criterion is demonstrably met (test green, bench observation logged, etc.).
- **Plan over implementation.** This file says *what* and *why*; implementation details live in code and ADRs (`docs/decisions.md`).

---

## Context

This plan operationalises a four-agent senior-engineer audit (March-April 2026) of the AOT Drone repo. The audit produced ~150 specific findings with file:line references across the C++ flight controller (`src/fc/`), system architecture / IPC, Python backend gateway (`src/backend/`), and the vision pipeline + React webapp.

**The single most consequential finding:** `fc_app` is currently a simulator. The C++ control loop computes `BetaFlightCommand` RC channel µs values every 20 ms and discards them — there is *no* MSP/UART/serial code anywhere in `src/fc/`. A grep for `MSP|UART|serial|termios` in the FC tree returns zero hits. Hardware autonomous flight is not possible until that path is built.

Several adjacent issues compound the risk if the MSP path were wired up naively:
- `hoverThrottle_=1100µs` default + `kAltHoldMaxThrottle=1500µs` ceiling → alt-hold "neutral" sinks the vehicle, max climb undershoots real hover.
- Stale-CMD failsafe stomps the state machine every tick with no hysteresis or auto-resume.
- `setArm(true)` unconditionally in `runTakeoffMode` — silent autonomous arming.
- Unfiltered vision input directly drives full-deflection RC commands.
- No rate limiting / slew on RC channel outputs; channels can step ±1000 µs in one 20 ms tick.

This plan front-loads everything that *can* be done with bench access at home (USB-attached FC, no flying) into Sprint 0 (May 4 → May 11) so John returns to a system that needs hardware integration, not bulk software coding.

---

## Hardware availability

| Item | Available at Eddi's | Needs John | Notes |
|---|---|---|---|
| Drone airframe (assembled) | ? — confirm | — | Verify state on Day 1 |
| Betaflight flight controller | ✓ | — | USB connection to laptop/Pi works |
| Raspberry Pi (vision compute) | ✓ | — | Confirm model + OS state |
| USB-A↔USB-C/micro cable for FC | ✓ assumed | — | Required for MSP-over-USB |
| RC TX + RX | ✓ assumed | — | For arm-switch authority |
| Camera (webcam or Picamera2) | ✓ | — | Webcam for laptop testing; Picamera2 for onboard |
| LiPo battery + charger | ✓ assumed | — | For powered bench tests |
| **3D printer** | ✗ | ✓ John has it | Pi mount, camera bracket, antenna mount |
| **Soldering iron + flux + solder** | ✗ | ✓ John has it | UART splice, FC pad work |
| **Crimper / wire stripper / heat-shrink gun** | ✗ | ✓ John has it | Harness assembly |
| Multimeter | ? — confirm | — | Useful for bench |
| Bench tether / netted enclosure | ✗ | ✓ build with John | First-flight requirement |

**Decision:** Sprint 0 assumes USB-MSP works end-to-end. UART pivot is deferred to Sprint 1 where John's soldering tools are available. Mounts, harnessing, and first-flight enclosure are Sprint 1.

---

## Audit summary (severity counts)

| Severity | Count | Headline examples |
|---|---|---|
| Critical | 8 | No MSP path; chunked-upload OOM; auto-arm in Takeoff; hover throttle 1100µs; backend `LandSafely` stomp on every tick; no React error boundary; no API auth; FC TEL hard-coded `127.0.0.1` |
| Major | ~50 | Pure-P controller w/ no I/D and no slew limiting; tracker re-init every detection frame; identity swap with multiple targets; backend `Vision→Backend→FC` dependency; loose WS schema validation; WS polling instead of event-driven |
| Minor | ~70 | UDP `SO_RCVBUF` not tuned; no TCP keepalives on bridge; spec drift (`cmd_age_s`); duplicated constants; hard-coded magic numbers |
| Nit | ~25 | Style, dead code, documentation gaps |

Top 10 individual-impact items (full audit transcript preserved in chat history; this is the working short-list):

1. Discarded RC output: `src/fc/implementation/main.cpp:263` (`(void)flight_controller.updateTimeStep(dt)`).
2. Hover throttle defaults: `src/fc/header/FlightController.h:145`, `src/fc/implementation/FlightController.cpp:15`.
3. Stale-CMD stomp: `src/fc/implementation/main.cpp:258-261`.
4. Auto-arm in Takeoff: `src/fc/implementation/FlightController.cpp:147`.
5. `/api/frame` chunked OOM: `src/backend/app.py:277-312`.
6. No auth anywhere: `src/backend/app.py:226-238`, `src/fc/implementation/CommandServer.cpp:63`.
7. No React error boundary: `src/webapp/src/main.tsx`.
8. KCF re-init every detection frame: `src/vision/vision_pipeline.py:159-161`.
9. Hard-coded `track_id=1`, no identity persistence: `src/vision/box_select.py:31-50`.
10. `last_cmd_seq=-1` sentinel: `src/fc/implementation/main.cpp:225,238`.

---

## Guiding principles

1. **Safety > function > polish.** Don't merge a feature whose failure modes haven't been bench-tested.
2. **Sim-to-real via HIL.** No autonomous flight without HIL bench acceptance — recorded mission scenarios replayed against real Betaflight (props off).
3. **RC switch is the sole arming authority.** `fc_app` may *request* arm; the human-held RC switch *grants* it via Betaflight modes config.
4. **Hysteresis, not edge triggers, on safety state machines.** Failsafe entry is a transition event, not a per-tick stomp.
5. **Filter the inputs, slew the outputs.** No raw vision sample drives a control output; no controller output steps the RC channels by more than a configured rate per second.
6. **Each task has acceptance criteria + a verification command.** "Done" means demonstrated, not merged.
7. **Front-load software in Sprint 0.** Anything that can be coded and bench-tested without John's specific tools (3D printer, soldering iron) goes into Sprint 0.
8. **Fail-safe defaults.** Empty intent → `LandSafely`. No CMD → `LandSafely`. Unknown desired_mode → keep current mode.
9. **Multi-chat continuity.** Update the Progress Log when a session ends or context fills up.

---

## Sprint map

```
SPRINT 0 (Eddi solo, May 4 → May 11) — software-heavy, hardware-aware
  P0  Foundation, hygiene, docs, HIL scaffold
  P0  Network safety (auth, chunked OOM, CORS, rate limits)
  P0  FC software bugs (seq filter, stale-CMD, host config)
  P0  USB MSP driver (NEW — testable at home with FC + USB)
  P0  Hover throttle calibration script (NEW — bench observation only)
  P1  Control quality (slew, filter, PID, search bounds)
  P1  Vision pipeline (threading, identity, model warmup, reconnect)
  P1  Webapp software fixes (error boundary, schema, watchdog)
  P2  Observability scaffold (structured logs, flight_id)
  P2  Schema strictness, fuzz tests, polish

SPRINT 1 (with John, May 11+) — hardware integration & first flights
  H1  Physical assembly (mounts, antenna, battery harness)
  H2  UART pivot if USB-arming refused (soldering required)
  H3  HIL bench: real Betaflight echoes channels, props off
  H4  Field tuning (gains, deadbands, FOV per actual cam)
  H5  Tethered → netted → outdoor first flight
  H6  Iteration & remaining polish from this plan
```

Priority within Sprint 0: P0 → P1 → P2. P0 must be green by May 11; P1 should be green; P2 is stretch.

---

# SPRINT 0 — Solo Foundation (May 4 → May 11)

> **Goal:** by May 11, the codebase is a clean, safe, mostly-complete software base. The MSP path works against a USB-attached Betaflight (with motors removed). All software-only audit findings are addressed. John's first day back focuses on physical mounts and first-flight prep, not catching up on code.

## P0 — Must-do

### S0.1 — Documentation & ADRs

- [ ] Update `docs/architecture.md` to state explicitly: *"`fc_app` was a simulator until v2 of this plan; the MSP/UART output path is implemented in Sprint 0 (USB) and finalised in Sprint 1 (UART if needed)."*
- [ ] Replace the 4-line `docs/decisions.md` with a real ADR log:
  - `ADR-001 Serialization is JSON v1.` (debuggability over wire size)
  - `ADR-002 Vision goes via Backend, not direct to FC.` Document trade-off (backend death = forced LandSafely).
  - `ADR-003 RC switch is the sole arming authority.` `fc_app` requests arm; firmware/RX grants.
  - `ADR-004 First-flight gate requires HIL bench acceptance.`
  - `ADR-005 RC sink transport: USB MSP (Sprint 0) → UART MSP (Sprint 1 if USB arming proves unreliable).`
- [ ] Create `docs/hil.md` describing HIL bench design (see S0.7).
- [ ] Create `docs/betaflight_setup.md` referencing `scripts/betaflight/aot_drone.diff` (see S0.14).
- [ ] Update `docs/message-spec.md`:
  - Add `cmd_age_s: float` to TEL schema (already emitted by FC at `src/fc/implementation/main.cpp:186`).
  - Add `failsafe_state: int` and `failsafe_age_s: float` (added in S0.4).
  - Add `arm_gate_status: int` (added in S0.4 / Sprint 1).
  - Replace "PR-B2 backend ingest accepts tiny drift" with implementation-defined wording.
  - Add explicit note: "timestamp_s is monotonic *to the sender*; consumers must not compare across senders."
  - Document max-payload constants and centralisation.
- [ ] **Acceptance:** John (or any new contributor) can read `docs/` in <30 minutes and understand the system + plan.

### S0.2 — Dead code & hygiene

- [ ] Delete `src/backend/udp_ingest.py` (unused, regressed earlier version — Backend audit F18/#20).
- [ ] Delete `src/backend/tcp_client.py` (unused — Backend audit #20).
- [ ] Delete `src/fc/implementation/placeholder.cpp` (dead code — FC audit A-14).
- [ ] `git rm -r --cached src/webapp/dist` and add `dist/` to `src/webapp/.gitignore`.
- [ ] Verify `.gitignore` covers all build artefacts under `build/`.
- [ ] Centralise `kMaxYawRateDps` (declared at `src/fc/implementation/FlightController.cpp:12` and `src/fc/implementation/main.cpp:21`) into `src/fc/header/RcConstants.h` or a new `src/fc/header/ControlConstants.h`.
- [ ] Move `clamp_target_coord` and `clamp_unit` (duplicated `FlightController.cpp:17-29` and `main.cpp:67-79`) into a new `src/fc/header/Clamp.h` shared header.
- [ ] Move PWM mappers (`normalized_axis_to_pwm`, `yaw_rate_to_pwm`, `throttle_to_pwm` in `main.cpp:81-116`) overlapping with `commandForward/Yaw/AltHoldDelta` into `src/fc/header/RcMath.h` used by both.
- [ ] Centralise `BACKEND_VIDEO_MAX_JPEG_BYTES` (default `200_000`, coupled `src/backend/app.py:86` and `src/vision/main.py:269`) into `src/backend/protocol_constants.py`.
- [ ] **Acceptance:** `git ls-files src/webapp/dist` returns empty; deleted files no longer appear in builds; constants exist in exactly one place; `cmake --build build -j` succeeds.

### S0.3 — Network safety (RCE/DoS-class fixes)

- [ ] **`/api/frame` chunked-upload OOM** (Backend audit #14, finding 5):
  - Reject `Transfer-Encoding: chunked` outright on `/api/frame` (vision uses Content-Length).
  - Add streaming reader fallback that aborts on running-byte overflow.
  - Test: `tests/test_video_ingest_endpoint.py::test_frame_ingest_rejects_chunked_oversize`.
- [ ] **API token auth** (architecture audit F34):
  - `BACKEND_API_TOKEN` env var, generated 32-byte hex.
  - Required `Authorization: Bearer <token>` on `/api/intent`, `/api/frame`, WS upgrade.
  - If unset → bind 127.0.0.1 + skip auth (current behaviour). Log WARN.
  - Webapp: `VITE_BACKEND_API_TOKEN` → header (REST) and `?token=` (WS).
- [ ] **CORS** (F35): `CORSMiddleware(allow_origins=[VITE_DEV_ORIGIN], allow_credentials=False, allow_methods=["GET","POST"])`.
- [ ] **Rate limit `/api/frame`**: 30 fps cap (per-IP token bucket).
- [ ] **Rate limit `/api/intent`**: 5 req/s.
- [ ] **FC TCP listener bind**: default `127.0.0.1`. Opt-in `0.0.0.0` via `FC_BIND_HOST`. (`src/fc/implementation/CommandServer.cpp:63`.)
- [ ] **Acceptance:** `pytest -q tests/test_video_ingest_endpoint.py` green; `curl -X POST http://127.0.0.1:8000/api/intent` without token → 401; lifespan logs reflect chosen mode.

### S0.4 — FC software fixes (no hardware needed)

- [ ] **Fix `seq` filter** (`src/fc/implementation/main.cpp:225,238`, FC audit C-02):
  - Replace `int last_cmd_seq = -1` with `std::optional<int>`.
  - Use signed-modular: accept iff `int32_t diff = (int32_t)(cmd.seq - last_seq); diff > 0`.
  - Add `tests/fc/test_cmd_seq_wrap.cpp` covering wrap, dedup, reseed.
- [ ] **Don't `setControlMode(LandSafely)` on parse failure** (`main.cpp:241-246`, C-04): on unknown `desired_mode` log + keep current mode.
- [ ] **Stale-CMD on transition only** (`main.cpp:258-261`, C-03):
  - Track `was_stale` boolean.
  - Two-stage failsafe: `Healthy` (cmd_age <0.5s) → `StaleSoft` (0.5–1.5s, hold attitude/altitude) → `StaleHard` (≥1.5s, LandSafely).
  - Auto-resume from StaleSoft on recovery; require operator confirm to exit StaleHard.
  - Surface `failsafe_state: int` and `failsafe_age_s: float` in TEL.
  - `tests/fc/test_failsafe_hysteresis.cpp` — simulate 0.6s, 1.0s, 1.6s gaps, assert transitions and recovery.
- [ ] **Configurable FC TEL host** (`main.cpp:211`, F1, A-05):
  - Read `FC_TEL_HOST` (default `127.0.0.1`) and `FC_TEL_PORT` (default `9001`).
- [ ] **Race fix on `last_cmd_` ↔ `last_cmd_time_s_`** (`src/fc/implementation/CommandServer.cpp:165-170,196-202`, A-01): publish `(cmd, time)` atomically inside `cmd_mutex_`; expose single getter.
- [ ] **`runHoverSearchState` redundant write** (`FlightController.cpp:182-184` then `commandYawRate`): drop dead store.
- [ ] **`is_armed` arithmetic** (`FlightController.cpp:31-37`, C-13): document why midpoint check; ensure call sites only use `setArm` to write the value (current `setArm` is correct — just add comment).
- [ ] **Pin LC_NUMERIC=C at startup** + `oss.imbue(std::locale::classic())` (A-07) so float formatting is locale-safe.
- [ ] **Manual-mode throttle preset reads stale `aux1`** (Copilot review on `chore/bugfixes` for commit `1394c65`): `setControlMode(Manual)` reads `is_armed(currentCommand_)` to choose `hoverThrottle_` vs `DRONE_MIN`, but `main.cpp` applies `desired_mode` BEFORE `cmd.arm`, so a single CMD that combines `Manual + arm:true` enters Manual seeing the *old* aux1 (disarmed) and leaves throttle at `DRONE_MIN` until a later setpoint update. Fix as part of this section's main.cpp ordering rework: apply `cmd.has_arm`/`setArm` BEFORE `setControlMode(desired_mode)` so the Manual transition sees the up-to-date aux1; or move the throttle preset out of `setControlMode` and recompute after the post-update `is_armed` guard at the end of `updateTimeStep`. Either fix needs HIL replay (`Manual+arm:true` scenario) before merge per ADR-004.
- [ ] **Add `Manual+arm:true` integration test that drives `main.cpp`'s real CMD-application path** (Copilot review on `chore/bugfixes` for `tests/test_flight_controller_modes.py`): the existing tests call `setControlMode(Manual)` directly and therefore don't catch the ordering bug above. Add a test that constructs a `CommandFrame` with `desired_mode=Manual, arm=true` and routes it through the same parse/apply path used by the FC binary, asserting throttle becomes `hoverThrottle_` (not `DRONE_MIN`).
- [ ] **Backend `state.py` cmd_seq APIs are not wrap-monotonicity-safe** (Copilot review on `chore/bugfixes` for `state.py:228-240`): both `reserve_cmd_seq(minimum=…)` and `ensure_cmd_seq_minimum(…)` use a linear `cmd_next_seq < clamped_minimum` check. After the counter wraps to a low value, a stale high minimum (e.g. from a `CmdBridge(seq_start=…)` restart against an FC that already saw the high seqs) will jump back into the pre-wrap range and reissue old sequence numbers. Rework alongside the FC `seq` filter modular-arithmetic change above: replace the linear comparison with signed-modular `int32_t diff = (int32_t)(clamped_minimum - cmd_next_seq); if (diff > 0) cmd_next_seq = clamped_minimum;`. In practice the wrap is ~497 days at 50 Hz, so the bug is a long-tail concern, but consistency with the FC fix is the right time to land it.
- [ ] **Reconcile wrap test with strict-monotonicity assertions in other tests** (Copilot review on `chore/bugfixes` for `tests/test_cmd_bridge_core.py`): `test_reserve_cmd_seq_wraps_after_int32_maximum` codifies that seqs may go `MAX → 0`, but `tests/test_cmd_bridge_send.py:35-37` and the e2e CMD tests assert `seqs[idx] > seqs[idx - 1]`. The two are consistent today only because no real test starts near `CMD_SEQ_MAX`, but the suite is internally inconsistent. When the modular-arithmetic rework above lands, also update the strict-`>` assertions to a wrap-aware helper (e.g. `assert_seq_advances(prev, curr)` doing modular comparison).
- [ ] **Acceptance:** `pytest -q tests/fc/` green; new tests cover seq wrap, malformed desired_mode, stale-CMD-transition, locale safety, the `Manual+arm:true` ordering above, and the modular `cmd_next_seq` advancement above.

### S0.5 — Vision software fixes (no hardware needed)

- [ ] **Tracker re-init bug** (`src/vision/vision_pipeline.py:159-161`, finding 8 / A3):
  - Re-init KCF only on (a) tracker fail or (b) IoU(tracker_box, detected_box) < 0.5.
  - Skip `tracker.update` when a detection arrives in the same frame (A12).
  - Test: 5 successive detections → tracker init exactly once.
- [ ] **Real confidence in TRACKING** (`vision_pipeline.py:244`, A9):
  - Pass through `detected.confidence` when present.
  - On tracker-only frames decay by `0.95/frame` to floor `0.3`.
  - Surface in UI sparkline.
- [ ] **Camera reconnect with backoff** (`src/vision/camera.py:42-67`, A10):
  - On `read()` False, release + reopen with backoff (0.25s → 5s cap).
  - Tolerate first 5 black frames after open.
- [ ] **Model warmup** (`src/vision/yolo_detector.py:34-55`, A18): in `__init__`, run inference on `np.zeros((640,480,3), dtype=uint8)`.
- [ ] **State flicker grace** (`vision_pipeline.py:129-131`, A22): one-frame grace before TARGET_DETECTED → NO_TARGET fallback.
- [ ] **Black-frame detection** (A11): warn after 30 consecutive frames with `frame.var() < 1.0`.
- [ ] **Acceptance:** `pytest -q tests/vision_*` green; manual smoke: yank webcam mid-run → vision recovers without process exit.

### S0.6 — Webapp software fixes (high-impact)

- [ ] **Top-level error boundary** (`src/webapp/src/main.tsx:1-11`, B1):
  - Class-based boundary wrapping `<App />` with fallback panel + reload button.
  - Per-section boundaries on `<VideoPanel>`, `<TelemetryPanel>`, `<TrackingSummary>`, `<Warnings>`.
- [ ] **Real schema validation on WS envelopes** (`src/webapp/src/types.ts:233-245`, B7):
  - Replace `value as TelUpdate` with field-by-field guards.
  - One-shot in-UI warning on shape mismatch per session.
  - Validate `confidence ∈ [0,1]`, `target_x/y ∈ [-1,1]`, `bound_w/h ∈ [0,1]`.
- [ ] **WS reconnect jitter + longer ceiling** (`src/webapp/src/ws.ts:162-173`, B2): ±30% jitter; 15 s ceiling.
- [ ] **WS heartbeat watchdog** (B15): if no message received for `2 * vis_fresh_s + 0.5s`, force close & reconnect.
- [ ] **Age sparkline range** (`src/webapp/src/components/TrackingSummary.tsx:147-153`, B10): replace `min=0,max=1` with `max = freshThresholdS * 4`.
- [ ] **Log WS errors** (`ws.ts:113-117`, B3): include URL + timestamp + close code; surface "WS error" warning after 3+ consecutive errors.
- [ ] **Use `projectAge` for accent badges** (`TelemetryPanel.tsx:262-268`, B11) instead of raw `vis_age_s`.
- [ ] **Acceptance:** `npm --prefix src/webapp run test` green; manual smoke: kill backend mid-stream → UI shows fallback within 2 s.

### S0.7 — HIL bench scaffold (`IRcSink` abstraction)

- [ ] Define `src/fc/header/RcSink.h`:
  ```cpp
  namespace fc {
  class IRcSink {
   public:
    virtual ~IRcSink() = default;
    virtual void writeChannels(const BetaFlightCommand& cmd, double timestamp_s) = 0;
    virtual bool ok() const = 0;
    virtual std::string name() const = 0;
  };
  }
  ```
- [ ] `NullSink` (default; logs first call, then silent).
- [ ] `RecordingSink` (writes every channel write to `logs/hil/sink_<utc>.csv`).
- [ ] `FakeBetaflightSink` (UDP-echoes received channels for the Python harness).
- [ ] Plumb sink into `main.cpp` via env `FC_RC_SINK=null|recording|fake|msp`.
- [ ] C++ unit test: `tests/fc/test_rc_sink.cpp` using a minimal in-tree gtest setup or CTest harness — match repo style.
- [ ] Python harness: `scripts/dev/fake_betaflight_listener.py` listening on UDP, decoding channel writes, asserting expected sequences.
- [ ] Document in `docs/hil.md`: launch `fc_app` with `FC_RC_SINK=fake` + Python listener + `scripts/dev/replay_mission.py` → recorded mission round-trip.
- [ ] **Acceptance:** `FC_RC_SINK=recording ./build/src/fc/fc_app` produces `logs/hil/sink_*.csv` with 50 Hz channel writes; `FC_RC_SINK=fake` mode round-trips through Python listener.

### S0.8 — USB MSP driver (testable at home)

> **Why this is in Sprint 0:** Eddi has the FC + USB cable. We can implement and unit-test against a real Betaflight FC (motors *removed*) by observing channel values in Betaflight Configurator's "Receiver" tab. Arming via USB is firmware-dependent and may not work — that pivot is Sprint 1.

- [ ] Implement `src/fc/implementation/MspRcSink.cpp` against `IRcSink`.
- [ ] **MSPv1 framing**: `$M<` + size + cmd + payload + xor checksum.
- [ ] **Cmd 200 (`MSP_SET_RAW_RC`)** — payload 16 bytes for 8 channels × `uint16_le`.
- [ ] Open `/dev/cu.usbmodem*` (macOS) / `/dev/ttyACM*` (Linux).
- [ ] `<termios.h>` config: 115200 8N1, raw mode, `VMIN=0`, `VTIME=0`.
- [ ] Selectable via env `FC_RC_DEVICE` (path) and `FC_RC_BAUD` (default 115200).
- [ ] **Boot probe**: send `MSP_API_VERSION` (cmd 1); refuse to enter `Tracking` / `Takeoff` modes if no reply.
- [ ] **Boot probe**: `MSP_RC_TUNING`; assert rate profile matches expected (configurable threshold). Log mismatch as WARNING; continue but require operator confirm before mode change.
- [ ] **Continuous heartbeat**: write at 50 Hz. Track success ratio in TEL via new field `msp_tx_ratio: float`.
- [ ] **Errors**: on `EAGAIN`/short write, retry once; on persistent failure, transition through the failsafe state machine (do *not* directly `setControlMode(LandSafely)` — go through `StaleHard` path).
- [ ] **MSP_RC query** for arm-switch state — used by S0.14 / Sprint 1 arm-authority logic.
- [ ] **Bench acceptance procedure** (run with motors physically detached):
  1. Connect FC via USB.
  2. `FC_RC_SINK=msp FC_RC_DEVICE=/dev/cu.usbmodem... ./build/src/fc/fc_app`.
  3. Open Betaflight Configurator → "Receiver" tab.
  4. Toggle `Manual` mode via webapp; confirm channel sliders match.
  5. Send synthetic CMD that drives roll/pitch/yaw/throttle to ±20% deflection; confirm bars track.
  6. Stop CMD → confirm StaleHard path; channels should snap to neutral / land profile (NOT arm).
- [ ] If USB arming is refused (expected), document the symptom in `docs/decisions.md` ADR-005 → defer to Sprint 1 UART pivot.
- [ ] **Acceptance:** Betaflight Configurator "Receiver" tab visibly tracks `fc_app` channel writes in real time, and `MSP_API_VERSION` boot probe succeeds.
- [ ] **Files (new):** `src/fc/header/MspRcSink.h`, `src/fc/implementation/MspRcSink.cpp`, `src/fc/implementation/MspFraming.cpp`, `src/fc/header/MspFraming.h`. **(modified):** `src/fc/CMakeLists.txt`, `src/fc/implementation/main.cpp`.

### S0.9 — Hover throttle calibration script

> **Bench-only.** No motors spinning. The script walks a stick command and observes Betaflight's reported channel; the operator notes lift-off µs only when motors are on (Sprint 1).

- [ ] `scripts/dev/hover_calibration.py`:
  - Connects to backend WS to drive `Manual` mode and override throttle setpoint.
  - Walks throttle from 1000 → 1700 µs in 10 µs steps, holding 1 s each.
  - Logs to CSV: `(t, requested_us, observed_msp_us)` via the FC's recording sink.
  - Prompts operator at each step for "lift-off / stable / clipping" classification.
- [ ] New `fc_app` flag / env: `--hover-throttle <us>` (default `0` = "uncalibrated").
- [ ] `runTakeoffMode` and `runFollowTargetLogic` refuse to operate with `hoverThrottle_ == 0` → transition to `StaleHard` with telemetry warning.
- [ ] **Fix throttle ceiling** (finding 2): rename `kAltHoldMaxThrottle` → `kAltHoldNeutralThrottle = 1500`; add `kAltHoldMaxThrottle = 1800` (or DRONE_MAX). Re-derive `commandAltHoldDelta` so positive delta truly climbs.
- [ ] `setHoverThrottle` clamps to `[rc::DRONE_MIN, kAltHoldMaxThrottle]` (the new wider ceiling).
- [ ] **Files:** `src/fc/implementation/FlightController.cpp:298, 431-451`, `src/fc/header/FlightController.h:145`, new `scripts/dev/hover_calibration.py`.
- [ ] **Acceptance:** Bench script runs to completion against `RecordingSink`; produces a CSV that John + Eddi can later use during Sprint 1 motor tests.

## P1 — Should-do

### S0.10 — Slew limiter on RC outputs

- [ ] Implement `src/fc/header/SlewLimiter.h` and apply before `clampCommandChannels` (`src/fc/implementation/FlightController.cpp:340-349`):
  - Roll/pitch ≤ 800 µs/s
  - Throttle ≤ 200 µs/s
  - Yaw ≤ 1500 µs/s
- [ ] Configurable via env / CLI; sensible defaults.
- [ ] **Acceptance:** `tests/fc/test_slew_limiter.cpp` asserts no channel ever steps more than allowed in any 20 ms window.

### S0.11 — Vision input filtering

- [ ] `src/fc/header/AlphaBetaFilter.h` (or constant-velocity Kalman) on `target_x`, `target_y`, `bound_h`.
- [ ] Hold last good estimate during gaps < 200 ms.
- [ ] Reject samples whose innovation > Nσ (configurable, default 4).
- [ ] **Acceptance:** `tests/fc/test_input_filter.cpp` with synthetic noisy bbox → smooth controller output.

### S0.12 — Yaw PID + gain scheduling

- [ ] Replace pure-P (`FlightController.cpp:320`) with PID:
  - I term with anti-windup
  - D term on `x_error` derivative
  - Gain schedule by `bound_h` (proxy for distance) — closer → less aggressive.
- [ ] Convert image error → angular error via horizontal FOV (`--fov-h-deg`, default 60).
- [ ] **Files:** new `src/fc/header/Pid.h`, modify `FlightController.h` and `FlightController.cpp:282-338`.
- [ ] **Acceptance:** HIL replay of "person walks across frame" scenarios → bounded, oscillation-free yaw output.

### S0.13 — Forward-speed and altitude controller polish

- [ ] PID on `bound_h` error using filtered input from S0.11.
- [ ] Smooth saturation `tanh(α·(size−target))` instead of hard clip (`FlightController.cpp:324-328`).
- [ ] Altitude: close on `position_m[2]` from telemetry instead of open-loop throttle delta — only when telemetry provides altitude (degrade gracefully).
- [ ] **Acceptance:** HIL replay produces stable altitude hold under simulated baro noise.

### S0.14 — Bounded search behaviour + arm-authority skeleton

- [ ] Replace open-loop `searchYawRate_dps_=60` (`FlightController.cpp:187`) with bounded sweep: ±90° from search-entry heading (`orientation_deg[2]`), then hover and surface "search exhausted" warning.
- [ ] **Arm-authority gate skeleton** (full enforcement is Sprint 1 with John):
  - `runTakeoffMode` does NOT auto-`setArm(true)`. (`FlightController.cpp:147` removed.)
  - Three-condition gate: (a) CMD `arm:true`, (b) tracking-state-OK <100 ms ago, (c) RC arm-switch held ≥1 s (read via `MSP_RC` query from S0.8).
  - TEL exposes `arm_gate_status: int` (0=denied, 1=requested, 2=granted).
  - Document in webapp UI with a tooltip explaining why arming hasn't happened.
- [ ] **Aux mapping documentation**:
  - `docs/betaflight_setup.md`: aux1=arm, aux2=angle, aux3=alt-hold, aux4=reserved.
  - Ship `scripts/betaflight/aot_drone.diff` (Betaflight CLI `diff` output template — operator runs `diff` against their FC, deltas indicate misconfig).
  - Boot-time MSP query of mode ranges (Sprint 1 verification).
- [ ] **Acceptance:** HIL test where target never returns → drone yaws to ±90°, settles, surfaces warning. Arm-gate status visible in TEL.

### S0.15 — Vision pipeline threading

- [ ] Refactor `src/vision/main.py:357-423` into 3-thread pipeline (bounded queues, drop-oldest):
  1. Capture → `latest_frame` slot.
  2. Inference + tracker → `latest_result` slot.
  3. Transport (JPEG encode, HTTP push, UDP send).
- [ ] Decouple `vis_hz` from inference rate via fixed timer thread.
- [ ] Surface achieved-fps in `_emit_shutdown_summary`.
- [ ] **Files:** new `src/vision/pipeline_runner.py`; refactor `src/vision/main.py`.
- [ ] **Acceptance:** laptop webcam → 30+ fps inference, VIS publishes at configured `vis_hz` regardless of inference rate.

### S0.16 — Identity-aware target selection

- [ ] Migrate from `model.predict()` to `model.track(persist=True)` (Ultralytics BoT-SORT/ByteTrack).
- [ ] `pick_best_box` (`src/vision/box_select.py`): when in TRACKING, score by distance-to-last-tracked-center *and* require `track_id == previous_track_id`.
- [ ] Surface real `track_id` in VIS payload and TEL.
- [ ] Webapp: show track_id badge with colour to make identity changes visible.
- [ ] **Acceptance:** multi-person test (record video with 2 people swapping center positions) → tracker holds initial identity.

### S0.17 — Vision robustness polish

- [ ] **Coordinate convention reconciliation** (A1, A19): pick one convention (recommend +y up, -1..1 origin-center) for the wire format. Eliminate dual convention in `normalize_bbox_xywh` (+y down, 0..1) vs UDP VIS payload (+y up, -1..1). Update `docs/message-spec.md`.
- [ ] **JPEG / HTTP push robustness** (A16):
  - Async httpx client with bounded queue (drop-oldest).
  - Circuit-breaker: skip pushing for N seconds after K failures.
  - Retry with backoff.
- [ ] **Detector imgsz rounding** to multiples of 32 (A7).
- [ ] **Score-box weight configurable** (A13): 0.25 magic constant becomes CLI flag.
- [ ] **Picamera2 / RTSP source stubs** (A17):
  - Implement `RtspSource` (recognise `rtsp:<url>` in `parse_source`).
  - Stub `Picamera2Source` (real implementation tested onboard in Sprint 1 if Pi has camera).
- [ ] **Config-file support** (A20): `--config <yaml>` with CLI overrides.
- [ ] **`clamp01` removal** (`src/vision/types.py:65`, A14): don't truncate off-screen targets at the source; protocol layer clamps at wire boundary.

### S0.18 — Backend & comms polish

- [ ] **Whitelist setpoint keys** (`src/backend/app.py:65-68`, Backend audit #23, F50): explicit Pydantic model `Setpoints(BaseModel)` with `roll/pitch/yaw_rate/throttle` and per-key range checks. Reject unknowns with 422.
- [ ] **NaN/Inf rejection** (`tel_schema.py`/`vis_schema.py`, Backend #28): add `math.isfinite` to `_require_float`.
- [ ] **TCP keepalives on cmd bridge** (`src/backend/cmd_bridge.py:147-160`, F20): `TCP_KEEPIDLE=2, TCP_KEEPINTVL=1, TCP_KEEPCNT=3`.
- [ ] **`_initial_cmd_seq` collision fix** (`src/backend/state.py:12-13`, F44): seed from `os.urandom(4)` mod int32.
- [ ] **Watchdog wrappers on broadcaster + cmd_bridge worker threads** (`src/backend/broadcast.py:236-240`, `src/backend/cmd_bridge.py:125-140`, Backend #35/#36): wrap inner loops in `try/except Exception: LOGGER.exception(...)`.
- [ ] **TEL/VIS UDP `SO_RCVBUF=1<<20`** (`src/backend/tel_ingest.py`, `src/backend/vis_ingest.py`, F16).
- [ ] **TCP `read_frame` error enum** (`src/fc/implementation/FrameCodec.cpp`, F10): replace `bool` with `enum class Result { Ok, Eof, InvalidLen, SocketErr }`; log specifically.
- [ ] **TCP magic prefix** (P3.5 in agent report): 2-byte `0xAA 0x55` before length prefix → resync after corruption without dropping connection.

### S0.19 — `cmd.tracking` semantics review

- [ ] Confirm intended behaviour: `tracking` block in CMD attaches whenever VIS is fresh, regardless of `desired_mode` (`src/backend/cmd_schema.py:56-57`, F24). If FC interprets "tracking present" as "use vision," restrict to `desired_mode == MODE_TRACKING`. Document in ADR.

## P2 — Stretch

### S0.20 — Observability scaffold

- [ ] **Structured logging baseline** (Backend audit #29):
  - `logging.basicConfig` with JSON formatter at `src/backend/app.py` startup.
  - Generate `flight_id` UUID at backend startup; log with every line; include in WS envelopes (new optional `flight_id` field, ADR + spec update).
  - Rotate log files in `logs/`.
- [ ] **`/metrics` endpoint** (Backend #30, F30):
  - Prometheus text format. Surface counters from `SharedState` and `VideoFrameHub`.
  - Histograms on tel_age, vis_age, cmd_round_trip.
- [ ] **`intent_id` correlation** (P5.5 in agent report):
  - UUID generated by webapp per intent; echoed in CMD; FC echoes back in TEL as `last_intent_id`.
- [ ] **WS slow-client overflow test** (Backend #43): registers a client, broadcasts `queue_max + 5`, asserts oldest 5 dropped.
- [ ] **WS asyncio.Queue dispatcher** (Backend #1, F22/F23) — *only if time permits*; replaces `deque` + 20 ms polling.

### S0.21 — Schema strictness & fuzz

- [ ] Hypothesis-based fuzz tests for TEL/VIS/CMD schemas under `tests/test_*_schema_fuzz.py` (F41).
- [ ] VIS-schema epsilon-boundary test (Backend #26): `tests/test_vis_schema.py::test_validate_vis_message_rejects_just_above_epsilon`.
- [ ] **`extra="forbid"` warnings** (F8): warn (don't drop) when TEL/VIS schemas receive unknown fields.

### S0.22 — Webapp polish (selected)

- [ ] **Aria + CSRF** (B16, F35): `aria-label`, `aria-pressed`, `aria-busy` on Arm/Mode buttons. Custom `X-AOT-Intent: 1` header for cheap CSRF defence.
- [ ] **Pending-intent visual** (B9): grey out selected mode button until actual matches; "pending" pulse animation.
- [ ] **Sparkline circular buffer** (B20): replace `slice` allocations in `historyBuffer.ts:23-25`.
- [ ] **`exactOptionalPropertyTypes: true`** (`src/webapp/tsconfig.json`, B21).
- [ ] **Memo cleanup** (B5): drop redundant `selectedTrackingPayload` memo.

### S0.23 — Run gates & CI

- [ ] After every commit: `./scripts/dev/runall.sh` green.
- [ ] `RUN_FULL_E2E=1 ./scripts/dev/e2e.sh` green.
- [ ] `npm --prefix src/webapp run build && npm --prefix src/webapp run test` green.
- [ ] Add the FULL_E2E run to at least one CI job (F42).
- [x] **C++ unit tests via `ctest`** (added on `chore/sprint0-cleanup` follow-up).
  Top-level `CMakeLists.txt` calls `enable_testing()` and adds
  `tests/fc/`. Tiny self-contained executables under `tests/fc/` use the
  always-on `TEST_ASSERT` macro from `tests/fc/test_assert.h` (no test
  framework dep). We deliberately avoid `<cassert>` because `assert()` is
  compiled out under `-DNDEBUG`, which would let broken logic silently
  pass in Release builds. Wired into both `scripts/dev/runall.sh` and
  `.github/workflows/ci.yml` so regressions break the build. Tests so
  far: `tests/fc/test_rc_math.cpp` (PWM helpers in `RcMath.h`) and
  `tests/fc/test_clamp.cpp` (NaN-safe clamps in `Clamp.h`).

## Sprint 0 Acceptance Gate

By **end of day May 11**, all P0 items above are `[x]` and all of:

- [ ] `./scripts/dev/runall.sh` green
- [ ] `RUN_FULL_E2E=1 ./scripts/dev/e2e.sh` green
- [ ] `npm --prefix src/webapp run build && test` green
- [ ] HIL bench: `FC_RC_SINK=fake ./build/src/fc/fc_app` + Python harness round-trips a recorded mission scenario
- [ ] **USB MSP bench**: real Betaflight Configurator "Receiver" tab tracks `fc_app` channel writes in real time (motors detached)
- [ ] Docs: ADRs added; `docs/hil.md`, `docs/betaflight_setup.md` exist; `docs/architecture.md` updated
- [ ] Webapp: error boundary catches an injected throw without blanking
- [ ] Backend: `/api/frame` rejects chunked uploads; auth headers enforced when token set
- [ ] Sprint 1 backlog (next section) committed and visible to John

---

# SPRINT 1 — With John (May 11+)

> **Goal:** flip the project from "software complete on the bench" to "first reliable autonomous flight". Software work in Sprint 1 is finishing touches, observability, and field tuning. The bulk is hardware integration.

## H1 — Physical hardware build

- [ ] **3D-print Pi mount** (John's printer). STL committed to `hardware/stl/pi_mount.stl`. Designed for the chosen frame.
- [ ] **3D-print camera bracket** (orientation matched to chosen FOV).
- [ ] **3D-print antenna mount** (separation from ESC noise).
- [ ] **Battery harness**: balance lead routing + secure mount.
- [ ] **Pi power**: regulated 5V from BEC or step-down; verify under motor load.
- [ ] **Cable routing**: minimise EMI to camera ribbon; tape/zip-tie.
- [ ] **Pre-flight inspection checklist** committed at `docs/preflight_checklist.md`.

## H2 — RC sink transport: USB → UART pivot if needed

- [ ] If S0.8's USB MSP path showed Betaflight refusing to arm over USB:
  - [ ] Identify a free FC UART (e.g., UART4/UART6 on most modern boards).
  - [ ] Solder Pi GPIO14/15 (or USB-UART dongle) to FC UART pads with John's iron.
  - [ ] Configure that UART as MSP in Betaflight CLI.
  - [ ] Implement `MspUartSink` (subclass of `MspRcSink` with different `open()` path).
  - [ ] Re-validate boot probe + `MSP_API_VERSION`.
  - [ ] Commit decision to ADR-005.
- [ ] If USB worked: skip H2; document in ADR-005.

## H3 — Real-FC HIL bench acceptance

- [ ] Bench setup: drone secured to test stand, motors physically detached or props removed, ESCs powered (no thrust).
- [ ] Run recorded mission scenarios from S0.7's HIL replay against real Betaflight via `MspRcSink`.
- [ ] Verify all of:
  - [ ] Channel writes match expected µs values within tolerance
  - [ ] Boot probe `MSP_API_VERSION` succeeds
  - [ ] Boot probe `MSP_RC_TUNING` matches `aot_drone.diff` profile
  - [ ] Aux mode ranges match (modes panel shows ARM/ANGLE/ALT-HOLD ranges as configured)
  - [ ] Failsafe Stage 1+2 configured in Betaflight (TX power-off → disarm <1s)
  - [ ] Arm gate denies arming when RC switch is not held
  - [ ] Arm gate grants arming when all three conditions met
  - [ ] Stale-CMD: 0.6s gap → StaleSoft (channels hold neutral); 1.6s gap → StaleHard (LandSafely throttle ramp)
  - [ ] Slew limiter prevents step changes >800 µs/s on roll/pitch
  - [ ] Latency budget: `scripts/dev/latency_probe.py` reports vision→MSP <100 ms p95
- [ ] Black-box log captures the bench session for replay.

## H4 — First-flight prep

- [ ] Geofence / max-altitude / max-speed clamp inside `fc_app` (independent of vision health):
  - [ ] `if altitude > 5 m → throttle = hover − 50`
  - [ ] `if speed > 1.5 m/s → forward command saturated`
- [ ] **Manual override aux channel**: high → autonomy; mid → passthrough. Operator's switch on TX.
- [ ] **Tether rig**: cord through ceiling hook or weighted base; length matches first-flight altitude target.
- [ ] **Netted enclosure** for pre-outdoor flights.
- [ ] **Communication plan**: pilot voice protocol, abort word, hand signals.

## H5 — Field tuning

- [ ] Onboard Pi vs. ground vision tradeoff: confirm which performs better with actual camera + Wi-Fi link.
- [ ] Camera FOV measurement → update `--fov-h-deg`.
- [ ] Hover-throttle calibration with motors on (uses S0.9 script).
- [ ] PID gains tuned via HIL replay → bench → tethered.
- [ ] Deadband retuning for actual camera noise levels.
- [ ] Aux mapping verified against `aot_drone.diff`.

## H6 — First-flight progression

> Each step blocks the next. Don't skip.

- [ ] **Step 1: Tethered hover (indoor or netted).** Operator on RC; autonomy off. Confirm hover throttle value real-world.
- [ ] **Step 2: Tethered autonomous hover.** Tracking mode; person stands center-frame; expect zero command output.
- [ ] **Step 3: Tethered tracking.** Person walks slowly side-to-side; observe yaw response.
- [ ] **Step 4: Netted free hover.** Operator-initiated takeoff via `Takeoff` mode; controlled descent via `LandSafely`.
- [ ] **Step 5: Netted tracking.** Person walks; drone tracks; operator holds override.
- [ ] **Step 6: Outdoor tethered.** Repeat steps 3 + 5 outdoors.
- [ ] **Step 7: Outdoor untethered with override switch held by manual pilot.** First "real" flight.

## H7 — Remaining polish (do as time permits during Sprint 1)

These are software items that didn't fit in Sprint 0 P2 and aren't blockers for first flight:

- [ ] **Video stream over WebSocket** (`/ws/video`, P6.2): replace `/api/frame` HTTP POST loop. Cache synthetic-fallback JPEG.
- [ ] **App context refactor** (Backend #37/#38, P6.3): `AppContext` dataclass on `app.state`; eliminate module globals.
- [ ] **Schema generation across stack** (P6.5): pydantic → JSON schema → TS types via `quicktype`. Eliminate drift.
- [ ] **Property-based tests** extension (P6.7): slew limiter, alpha-beta filter, frame codec.
- [ ] **systemd / docker-compose** (P6.9, F5): `deploy/systemd/aot-{fc,backend,vision}.service` with `Restart=on-failure`. `deploy/docker-compose.yml` for laptop dev.
- [ ] **VIS-direct fast-path decision** (P6.10, F2): document in ADR; implement if chosen.
- [ ] **Binary flight log (FC-side)** (P5.3): `src/fc/implementation/FlightLog.cpp` writes per-tick struct to `logs/flight_<utc>.bin`. Companion `scripts/dev/read_flight_log.py`.
- [ ] **`/api/intent` arm-confirm flow** (P3.2): split arm into separate endpoint with single-use challenge token.

---

## Pre-First-Flight Gate

Every item must be `[x]` before the first untethered autonomous flight (H6 step 7).

| # | Item | Verification |
|---|---|---|
| 1 | MSP path (USB or UART) end-to-end | Betaflight Configurator "Receiver" live tracking |
| 2 | RC switch is sole arming authority | Throttle high + flip disarm → motors stop ≤100 ms |
| 3 | Hover throttle calibrated with motors on | Tethered stable hover |
| 4 | Failsafe Stage 1+2 configured | TX power-off → Betaflight disarm <1s |
| 5 | Aux mode ranges match `aot_drone.diff` | Boot probe asserts on real FC |
| 6 | Latency budget vision→MSP <100 ms p95 | `latency_probe.py` report |
| 7 | Slew limit verified | Unit test + bench observation |
| 8 | Vision filter + identity active | HIL replay + multi-person test |
| 9 | Stale-CMD hysteresis verified | 0.6s → StaleSoft; 1.6s → StaleHard |
| 10 | Watchdog kills hung process | Inject sleep → process exits |
| 11 | Black-box logging on | Log file exists; reader plots mission |
| 12 | Webapp error boundary catches injected throw | Manual test |
| 13 | Backend auth enabled (token) | `curl` without token → 401 |
| 14 | Geofence / max-alt / max-speed clamp active | Unit + bench test |
| 15 | Manual override aux channel works | Bench switch test |
| 16 | Outdoor netted flight successful | Step H6.6 done |

---

## Verification per phase

| Phase | Primary command(s) | Hardware |
|---|---|---|
| Sprint 0 P0 | `./scripts/dev/runall.sh && RUN_FULL_E2E=1 ./scripts/dev/e2e.sh && npm --prefix src/webapp run test && FC_RC_SINK=msp ./build/src/fc/fc_app` (with Configurator visual) | Eddi: Pi + FC + USB cable |
| Sprint 0 P1 | HIL replay via `FC_RC_SINK=fake` + `scripts/dev/replay_mission.py`; multi-person test recording | Eddi: laptop + webcam |
| Sprint 0 P2 | `pytest -q tests/test_*_schema_fuzz.py`; `curl /metrics` parses | None |
| Sprint 1 H1 | Physical inspection per `docs/preflight_checklist.md` | John's tools |
| Sprint 1 H2 | UART boot probe success | Soldering + UART harness |
| Sprint 1 H3 | All bench items above | Real FC + ESCs powered, motors detached |
| Sprint 1 H4 | Geofence unit + bench tests | Bench + tether |
| Sprint 1 H5 | Hover stable; PID HIL replay still good after retune | Bench + motors-on |
| Sprint 1 H6 | Each step demonstrated before next | Tether → net → outdoor |

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| MSP-over-USB arming refused by Betaflight | High | Sprint slip | Pivot to UART in Sprint 1 H2; document decision; USB-MSP-channel-readback still validates the data path |
| Hand-rolled C++ MSP driver bug → spurious channel writes | Medium | Vehicle damage | Always test motors-detached; HIL bench mandatory; `MSP_API_VERSION` boot probe; slew limiter caps step size |
| USB cable jiggle / disconnect mid-bench | Medium | Test confusion | Cable strain relief; `MspRcSink` reports disconnect through TEL; auto-reconnect with backoff |
| Vision tracker swaps target identity mid-flight | Medium | Loss of intended target / unsafe pursuit | S0.16 BoT-SORT track_id; operator-visible identity badge in UI |
| Backend dies mid-flight → forced LandSafely | Medium | Aborted mission | systemd `Restart=on-failure` (H7); consider VIS-direct fast-path (H7) |
| Hover throttle miscalibration → vehicle drops | Medium | Vehicle damage | S0.9 script gated; refuse to fly with `hover_throttle = 0` |
| Network bound to LAN + no auth | High if exposed | RCE-class | S0.3 auth; default bind 127.0.0.1; document in deploy guide |
| First autonomous flight indoors / no override | We won't | Loss-of-vehicle | Pre-flight gate item 15 mandates override |
| Solo dev on safety-critical code | Active reality | Bug slip | Heavy automated testing; HIL replay before any code change to in-flight behaviour; multi-chat continuity via this file |
| Camera ribbon EMI from ESCs | Medium | Tracking glitches | Cable routing in H1; shielding tape if needed |
| Pi onboard inference too slow | Medium | Frame skew | S0.15 threading; consider NCNN/ONNX quantised model in H7 |

---

## Out of Scope (Deferred)

- GPS navigation and waypoint missions.
- Object avoidance / lidar / depth sensing.
- Multi-target tracking (single-target with identity persistence is the goal).
- Full FC firmware replacement (we keep Betaflight; we *only* drive RC channels).
- Cloud telemetry / OTA / fleet management.
- Voice or gesture commands.

These items from the original project report are intentionally not part of this plan and should be re-evaluated after first flight is achieved.

---

## What requires John (the Sprint 0 → Sprint 1 handoff list)

If Eddi can't move past these without John, they go in Sprint 1:

- 3D-printed mounts (Pi, camera, antenna) — needs printer
- UART pad soldering on the FC — needs soldering iron + flux + small-tip work
- Power harness assembly — needs crimper + heat-shrink gun
- Battery balance lead trimming — needs wire stripper
- Two-person bench: one holds the drone, one operates the laptop (safer)
- Field-day operation with manual override pilot

If Eddi can move past these without John, they go in Sprint 0:

- USB MSP driver development + bench test
- Channel readback verification via Betaflight Configurator
- Hover throttle calibration script (channel observation only, no motors)
- All software, tests, docs, observability, schemas, network safety
- HIL bench (laptop + Python harness)
- Webapp error handling, schema validation, polish

---

## Appendix A — Critical file index

| Concern | File | Phase |
|---|---|---|
| Discarded RC output | `src/fc/implementation/main.cpp:263` | S0.8 |
| Stale-CMD stomp | `src/fc/implementation/main.cpp:258-261` | S0.4 |
| Hard-coded TEL host | `src/fc/implementation/main.cpp:211` | S0.4 |
| `last_cmd_seq=-1` sentinel | `src/fc/implementation/main.cpp:225,238` | S0.4 |
| `setArm(true)` in Takeoff | `src/fc/implementation/FlightController.cpp:147` | S0.14 |
| Hover throttle defaults | `src/fc/header/FlightController.h:145`, `FlightController.cpp:15` | S0.9 |
| Tracker re-init bug | `src/vision/vision_pipeline.py:159-161` | S0.5 |
| Hardcoded `track_id=1` | `src/vision/yolo_detector.py:103`, `vision_pipeline.py:245` | S0.16 |
| Sync vision pipeline | `src/vision/main.py:357-423` | S0.15 |
| Camera read failure → exit | `src/vision/camera.py:42-67` | S0.5 |
| `/api/frame` chunked OOM | `src/backend/app.py:277-312` | S0.3 |
| Module globals in app.py | `src/backend/app.py:39-62` | H7 |
| WS polling dispatcher | `src/backend/app.py:402-408` | S0.20 / H7 |
| Synthetic frame re-render | `src/backend/app.py:439-462` | H7 |
| Dead modules | `src/backend/udp_ingest.py`, `tcp_client.py`, `src/fc/implementation/placeholder.cpp` | S0.2 |
| Loose WS schema validation | `src/webapp/src/types.ts:233-245` | S0.6 |
| No error boundary | `src/webapp/src/main.tsx:1-11` | S0.6 |
| WS reconnect no jitter | `src/webapp/src/ws.ts:162-173` | S0.6 |
| Sparkline range bug | `src/webapp/src/components/TrackingSummary.tsx:147-153` | S0.6 |
| FC TCP listener bind | `src/fc/implementation/CommandServer.cpp:63` | S0.3 |
| `last_cmd_` race | `src/fc/implementation/CommandServer.cpp:165-170,196-202` | S0.4 |
| Pure-P yaw, no slew | `src/fc/implementation/FlightController.cpp:320, 340-349` | S0.10/S0.12 |
| Locale-fragile JSON | `src/fc/implementation/main.cpp:179-196` | S0.4 |

---

## Appendix B — Findings → phase crosswalk

(Use when picking up a specific finding ID from the audit transcript.)

- **Sprint 0 P0:** F1, F4, F7/F49, F18, F20, F23, F40, F41 (move some to P2), Backend #14, Backend #20, Backend #23, Backend #28, Backend #35/#36, B1, B2/B3, B7/B8, B10, B15, A3/A9/A10/A18, C-01..C-08 (partial), A-05, A-07, A-08, A-13, M-09 (hover throttle ceiling).
- **Sprint 0 P1:** M-01..M-14 (controls quality cluster — slew, filter, PID, search), C-08 (alt-hold in safety branch), C-10/C-11 (timestamps), A1, A2, A4/A5, A6, A7, A8, A11, A12, A14, A16, A17, A20.
- **Sprint 0 P2:** F8/F9/F10/F11/F12/F38, F32, F22/F23, F44, Backend #1/#2 (if time), Backend #43, Backend #45, F30, F31, B5/B12/B13/B16/B17/B20/B21.
- **Sprint 1 H1:** Physical assembly (no audit findings).
- **Sprint 1 H2:** ADR-005 implementation if UART pivot.
- **Sprint 1 H3:** All bench acceptance items.
- **Sprint 1 H7:** P5.3 (binary flight log), P6.1 (asyncio.Queue if not done), P6.2 (video over WS), P6.3 (AppContext), P6.5 (schema generation), P6.9 (systemd), P6.10 (VIS-direct), P3.2 (arm-confirm).

---

## Progress Log

> Each Claude / Codex session ending or context-fill writes one entry. Format: `### YYYY-MM-DD — author/chat — summary` then bullets of tasks touched.

### 2026-05-04 — Eddi + Claude (initial plan) — kickoff

- Drafted this plan from four-agent audit findings.
- Sprint 0 priorities defined; bench-feasible work (USB MSP, hover calibration script) included since hardware is available at home.
- Sprint 1 reframed around John's tools (3D printer, soldering, harnessing) and first-flight progression.
- ADR-001 through ADR-005 to be authored in S0.1.
- Next session: start S0.1 (ADRs + doc updates) and S0.2 (dead-code purge) in parallel; expect them done in <2 hours.

### 2026-05-04 — Eddi + Claude — S0.2 dead-code purge + constants centralization

- Branch: `chore/sprint0-cleanup` off `dev` (post-merge of `chore/sprint0-docs`
  and `chore/bugfixes`).
- Dead-code deletions (audit A-14, F18, Backend #20):
  - `src/backend/udp_ingest.py` — earlier version of the UDP ingest path,
    no imports anywhere.
  - `src/backend/tcp_client.py` — earlier version of the TCP cmd bridge,
    no imports anywhere.
  - `src/fc/implementation/placeholder.cpp` — never referenced from
    `src/fc/CMakeLists.txt`, scaffolding artefact.
- `dist/` untracking (audit B17): no work needed — already covered by
  `src/webapp/.gitignore` and the root `.gitignore`. The audit finding
  was stale.
- FC constants centralization (audit M-06, A-06):
  - New `src/fc/header/Clamp.h`: `clamp_target_coord` and `clamp_unit` as
    inline functions in `namespace fc`. Removes the duplicated definitions
    from `FlightController.cpp:17-29` and `main.cpp:67-79`.
  - New `src/fc/header/RcMath.h`: `kAxisRangeUs`, `kMaxYawRateDps`, and the
    three PWM mappers (`normalized_axis_to_pwm`, `yaw_rate_to_pwm`,
    `throttle_to_pwm`) as inline functions in `namespace fc::rc`. Removes
    the duplicated `kMaxYawRateDps` (was at `FlightController.cpp:12` and
    `main.cpp:21`) and pulls the PWM mapper bodies (`main.cpp:81-116`)
    out of the binary's anonymous namespace into a header.
  - `FlightController.cpp` and `main.cpp` now use `using` aliases to keep
    call sites identical. No behavioural change. Header doc references
    audit P2.9 for the future consolidation of
    `kPitchRangeUs / kYawRangeUs / kAxisRangeUs` into a single
    `RcStickRangeUs` (Sprint 1 control-quality work).
- Backend / vision constants centralization (audit F46):
  - `VIDEO_MAX_JPEG_BYTES_DEFAULT = 200_000` added to
    `src/backend/protocol_constants.py` with a comment explaining why both
    sides must default to the same value.
  - `src/backend/app.py` and `src/vision/main.py` import the constant and
    use it as the default for their `BACKEND_VIDEO_MAX_JPEG_BYTES` env-var
    reads. Vision now imports from `src.backend.protocol_constants` —
    accepted as a wire-contract dependency (vision and backend must agree
    on this size).
- Verification:
  - `cmake --build build -j` clean.
  - `python -m ruff check src tests` clean.
  - `python -m pre_commit run --all-files` clean (ruff, ruff-format,
    clang-format, file hygiene all green).
  - `pytest -q` of the runnable subset (66 tests covering FC modes,
    cmd bridge core, backend API validation, schemas, etc.) all pass.
  - The pre-existing `tests.cmd_test_utils` / `tests.ws_test_utils`
    `ModuleNotFoundError` collection errors for 9 test files are inherited
    from `dev` and unrelated to S0.2 — separate pytest-discovery issue
    to fix later.
  - Smoke: `./build/src/fc/fc_app` boots, emits TEL seq=0 mode=2
    tracking_state=4, exits cleanly on signal.
- Branch scope after Copilot review rounds 1 and 2 (which also landed on
  this PR): no longer a pure cleanup branch. In addition to the
  dead-code deletions and constants centralization above, this branch now
  also adds:
  - C++ unit-test infrastructure (`enable_testing()` in the top-level
    `CMakeLists.txt`, new `tests/fc/CMakeLists.txt`, `ctest` wired into
    both `scripts/dev/runall.sh` and `.github/workflows/ci.yml`).
  - First two C++ unit tests under `tests/fc/`: `test_rc_math.cpp`
    (8 cases on the `RcMath.h` PWM helpers) and `test_clamp.cpp`
    (6 cases on the `Clamp.h` NaN-safe helpers).
  - Python sync regression suite `tests/test_video_max_jpeg_default_sync.py`
    that drives the actual production paths (TestClient(app) lifecycle
    on the backend side, `parse_args([])` on the vision side) so a
    future re-hardcode of the JPEG-bytes default in either entry point
    fails loudly.
  - `src/vision/frame_pusher.py` constructor default now also pulls from
    `VIDEO_MAX_JPEG_BYTES_DEFAULT` (round-1 review caught that the
    centralization missed it).
- Final stats from `git diff dev..HEAD --stat`:
  18 files changed, 394 insertions(+), 134 deletions(-). The durable
  takeaway: this PR spans cleanup + build/test wiring + regression
  coverage, with no intended runtime-behaviour change beyond the new
  verification surface.

### 2026-05-04 — Eddi + Claude — second Copilot review pass

- `chore/sprint0-docs` round 2 (this pass):
  - Removed the lingering `PR-B2 backend ingest accepts tiny drift` reference
    from the VIS datagram semantics in `message-spec.md` (round 1 missed it
    on line ~181). Replaced with self-contained "receivers MAY accept ≤1e-6
    drift; outside that range = drop+log" wording.
  - Fixed the wrong ADR citation in `hil.md` for the `MspRcSink` failsafe
    transition (was `ADR-003 / S0.4`, but ADR-003 is about arming; the
    failsafe state machine is defined in S0.4). Now points only at S0.4.
  - Narrowed the in-flight-behaviour gate in `hil.md` so doc-only edits to
    `docs/message-spec.md` no longer trigger HIL replay. Implementation
    changes that alter the wire contract still must update the spec; pure
    doc edits don't gate.
  - Added four explicit deferred-items rows to S0.4 of the development
    plan: (a) the `Manual+arm:true` integration test that drives the real
    main.cpp CMD-application path, (b) reworking `state.py`'s
    `reserve_cmd_seq` / `ensure_cmd_seq_minimum` to use signed-modular
    arithmetic alongside the FC seq filter rework, (c) replacing the strict
    `seqs[i] > seqs[i-1]` assertions in `test_cmd_bridge_send.py` and the
    e2e CMD tests with wrap-aware helpers when the modular rework lands.
- `chore/bugfixes` round 2 (separate commit, separate branch):
  - Fixed the empty-string Content-Length bypass in `app.py` (Copilot
    flagged that `strip()` + `if content_length_text:` skips validation
    when the header is present-but-empty, allowing the body read to
    proceed unguarded).
  - Added regression test for the empty-CL case.
- Deferred (tracked in plan, not actioned this pass): the Manual-mode
  throttle ordering bug itself (S0.4 — needs HIL replay per ADR-004), the
  modular cmd_next_seq rework (S0.4 — paired with FC seq filter), and the
  wrap-test consistency cleanup (S0.4 — paired with the modular rework).

### 2026-05-04 — Eddi + Claude — S0.1 Copilot review follow-up

- Addressed all 9 Copilot review comments on `chore/sprint0-docs` (PR open):
  - Replaced absolute developer path in `development_plan.md` with a
    repo-relative note.
  - Renamed `FakeSink` → `FakeBetaflightSink` in the `hil.md` topology
    diagram for naming consistency.
  - Tagged the four planned helper scripts (`latency_probe.py`,
    `read_flight_log.py`, `record_mission.py`, `replay_mission.py`) with
    explicit "(planned, not yet committed)" markers and the phase that
    introduces each, plus a clarifying note at the top of the workflow
    section.
  - Removed the implementation-history claim on the `cmd_age_s` field
    description (the field is already emitted by `fc_app`, not added in
    S0.4).
  - Tightened the "all numeric fields MUST be finite" rule into per-field
    semantics: 0.0 substitution is only permitted for the tracking fields;
    other required numerics MUST be dropped + logged on non-finite to
    avoid masking sensor or logic faults.
  - Fixed "debug under hand" → "debug by hand" typo in ADR-001.
- Plan addendum: added an explicit task in S0.4 documenting the
  Copilot-flagged ordering bug from `chore/bugfixes` (Manual-mode throttle
  preset reads stale `aux1` when `Manual + arm:true` arrive in the same
  CMD). Deferred from `chore/bugfixes` per ADR-004 (HIL bench required
  before fixing in-flight-behaviour code).
- Pre-commit clean (no code touched).
- Next: switch to `chore/bugfixes` and add the missing test coverage for
  `state.py` seq clamping (negative + over-max minimums) and `/api/frame`
  Content-Length edge cases (malformed + negative). Hold S0.2 until both
  PRs merge to `dev`.

### 2026-05-04 — Eddi + Claude — S0.1 complete on `chore/sprint0-docs`

- `[x] S0.1` — all four docs items done on branch `chore/sprint0-docs` off
  `dev`. Changeset: 3 modified, 2 new (199 +, 5 −).
  - `docs/architecture.md`: added explicit "current state — fc_app is a
    simulator until Sprint 0 lands MSP" callout right after the four-component
    overview.
  - `docs/decisions.md`: rebuilt as a real ADR log. Authored ADR-001 (JSON
    serialization), ADR-002 (Vision via Backend), ADR-003 (RC switch is sole
    arming authority), ADR-004 (HIL gate before flight), ADR-005 (USB MSP
    first, UART pivot if needed), ADR-006 (Sprint 0 / Sprint 1 split).
  - `docs/message-spec.md`: added optional TEL fields (`cmd_age_s`,
    `failsafe_state`, `failsafe_age_s`, `arm_gate_status`, `last_loop_dt_ms`,
    `msp_tx_ratio`, `last_intent_id`); added cross-sender timestamp note;
    added "all numerics MUST be finite" semantic.
  - `docs/hil.md` (new): full HIL bench design — IRcSink contract, four sink
    behaviours (`Null`/`Recording`/`Fake`/`Msp`), bench topology diagram,
    mission record/replay workflow, acceptance criteria for in-flight
    changes.
  - `docs/development_plan.md` (new): the plan itself, also tracked in this
    branch since it's the canonical reference everything else points to.
- Pre-existing markdown lint warnings (MD022/MD031/MD032/MD040) in
  architecture.md and message-spec.md were intentionally NOT fixed — pre-commit
  doesn't lint markdown so they don't block CI; full lint cleanup is a
  separate PR.
- Next session: S0.2 (dead code + constants centralization) on a fresh
  branch off `dev`. Branch name: `chore/sprint0-cleanup`.
- Hand-off note: `chore/bugfixes` is 4 commits ahead of `dev` with overlapping
  network-safety work (`9e5e5b1 /api/frame now validates Content-Length
  early`). When that PR merges to `dev`, expect a small rebase on
  `chore/sprint0-docs`. The `cmd_age_s` field documented in this PR is
  already emitted by `fc_app` (`src/fc/implementation/main.cpp:186`).

### 2026-05-06 — Eddi + Claude — S0.3 network safety (`chore/sprint0-network`)

- Branch `chore/sprint0-network` off `dev` (post-merge of `chore/sprint0-cleanup`).
  Five logical commits, one per S0.3 sub-item, plus this Progress Log entry:
  1. `feat(backend): reject chunked /api/frame uploads + stream-bounded body
     read` — closes the OOM/DoS hazard where a chunked POST with no
     Content-Length would buffer unbounded bytes before the size check
     fired. Three layered defences: reject Transfer-Encoding: chunked
     outright, require Content-Length explicitly (411 otherwise), and
     stream-read with a running cap so a misreported CL still aborts mid-body.
     Three new regression tests in `tests/test_video_ingest_endpoint.py`.
  2. `feat(backend,vision,webapp): bearer-token auth on /api/intent,
     /api/frame, /ws` — `BACKEND_API_TOKEN` env. REST routes use a FastAPI
     `Depends(_require_api_token)` parsing `Authorization: Bearer <token>`
     with `hmac.compare_digest`. The /ws upgrade reads the bearer token from
     `Sec-WebSocket-Protocol` using an `aot.bearer.<token>` subprotocol value
     (browsers cannot set Authorization on `new WebSocket(...)`) and closes
     pre-accept with code 4401 on mismatch. Vision FramePusher gains an
     `api_token` kwarg; webapp `BackendConfig` gains optional `apiToken` from
     `VITE_BACKEND_API_TOKEN`. Token unset preserves current local-dev
     behaviour and emits a startup WARN telling the operator to bind 127.0.0.1.
     9 cases in `tests/test_api_auth.py`. (The initial commit on this branch
     used a `?token=` query param; switched to subprotocol on Copilot review
     because URL query params get logged by reverse proxies, browser history,
     and referrer chains.)
  3. `feat(backend): env-driven CORS middleware` — `BACKEND_CORS_ALLOW_ORIGINS`
     comma-separated allowlist. Empty/unset => no middleware (default-deny
     cross-origin), preserving the Vite-proxy dev path. `allow_credentials=False`
     and `Authorization`/`Content-Type` whitelisted. Tests use an isolated
     fresh FastAPI app rather than `importlib.reload` of the singleton — the
     reload approach orphaned `app.state` from other tests' TestClient
     lifespans and broke `test_ws_vis_update_broadcast.py` (worth remembering:
     don't reload `src.backend.app` in tests).
  4. `feat(backend): per-IP token-bucket rate limit on /api/frame and
     /api/intent` — homegrown limiter (no slowapi, no async-middleware
     framework lock-in). New `src/backend/rate_limit.py` with `TokenBucket`
     and `IpRateLimiter`. Defaults: 30 Hz frame, 5 Hz intent; configurable
     via `BACKEND_FRAME_RATE_LIMIT_HZ` / `BACKEND_INTENT_RATE_LIMIT_HZ`;
     0 disables. Per-IP bucket dict is unbounded — LRU eviction is a
     follow-up. 5 cases in `tests/test_rate_limit.py`.
  5. `feat(fc): default TCP listener to 127.0.0.1 with FC_BIND_HOST opt-in` —
     `CommandServer` was binding `INADDR_ANY` regardless of deployment, so
     anyone on the LAN with the port could deliver CMD frames. New
     `bind_host` parameter (defaults to `127.0.0.1`); `inet_pton` validates
     the host up-front so a typo refuses to start instead of silently
     falling back to ANY. Smoke-tested across default / `0.0.0.0` /
     garbage-host states.
- Verification (every commit ended green):
  - `python -m pre_commit run --all-files` clean.
  - `pytest -q tests/` 113 passed, 1 skipped (UDP-bind dependent).
  - `npm --prefix src/webapp run build` + `vitest` 5/5.
  - `cmake --build build -j` clean; `ctest` 2/2.
  - FC binary smoke: default loopback, `FC_BIND_HOST=0.0.0.0`, and bad-host
    refusal all behave as expected.
- Wire-contract callouts the spec already covers; no `docs/message-spec.md`
  changes were needed for S0.3 because the new behaviour is auth/transport
  rather than payload schema. The new env vars (`BACKEND_API_TOKEN`,
  `BACKEND_CORS_ALLOW_ORIGINS`, `BACKEND_FRAME_RATE_LIMIT_HZ`,
  `BACKEND_INTENT_RATE_LIMIT_HZ`, `FC_BIND_HOST`) deserve a deployment-guide
  entry — deferred to whichever PR adds `docs/deployment.md` (none planned
  yet; capture in the next docs sweep).
- Next session: S0.4 (FC software fixes — seq filter rework with std::optional
  + signed-modular wrap math, stale-CMD hysteresis 0.5/1.5 s, `FC_TEL_HOST`
  env, `last_cmd_` race, locale-safe JSON, plus the deferred Manual+arm:true
  ordering bug from `chore/bugfixes`). Branch `chore/sprint0-fc-software`
  once #23 (this one) merges.

### (future entries here)
