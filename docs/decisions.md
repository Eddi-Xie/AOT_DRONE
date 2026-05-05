# Architecture Decision Records (ADRs)

This file is the project's running ADR log. New decisions are appended; superseded
decisions are kept with a "Superseded by ADR-NNN" header so history is preserved.

Format per record:

```text
## ADR-NNN — Title
- Status: Proposed | Accepted | Superseded by ADR-MMM | Deprecated
- Date: YYYY-MM-DD
- Context: what's the situation / problem
- Decision: what we chose
- Consequences: what flows from this decision
```

---

## Pre-ADR notes (historical)

PR2 — 2026-02-03

- Pivoted from a C# desktop GUI to a React web UI (browser-based).
- Standardised on UDP for telemetry / vision and TCP for commands.
- Chose JSON as the on-the-wire format for v1 (debuggability over wire size).

These decisions are now formalised below as ADR-001 / ADR-002.

---

## ADR-001 — Serialization is JSON v1 (debuggability over wire size)

- Status: Accepted
- Date: 2026-02-03 (formalised 2026-05-04)
- Context: We need an inter-process message format that we can debug by hand
  with `tcpdump`/`nc`/`socat`, that is easy to evolve, and that is supported on
  every language in the stack (C++ FC, Python backend + vision, TS webapp).
- Decision: Use UTF-8 JSON for all wire formats (UDP TEL, UDP VIS, TCP CMD,
  WebSocket envelopes). Fields and enums normative in `docs/message-spec.md`.
  A `type` field identifies the kind; future breaking changes will introduce
  a `ver` field or a new event type rather than mutating existing schemas.
- Consequences:
  - Pros: trivial to inspect on the wire; easy interop; loss-tolerant on UDP.
  - Cons: ~3-5× wire overhead vs. binary (protobuf/msgpack); per-packet parse
    cost on the 50 Hz hot path. Acceptable for current single-machine /
    LAN-scale deployment. Re-evaluate if we ever push real-time data over
    a constrained radio link.

## ADR-002 — Vision goes via Backend, not direct to FC

- Status: Accepted
- Date: 2026-02-03 (formalised 2026-05-04)
- Context: The Vision process produces tracking signals that must reach the
  FC's control loop. Two valid topologies: (a) Vision → Backend → FC, where
  the backend is the integration layer; (b) Vision → FC directly via UDP,
  with the backend a side-channel for UI only.
- Decision: Adopt topology (a). Backend ingests `VIS` UDP, exposes the
  freshest sample via WS to the UI, *and* inlines it into the next outbound
  CMD frame. Backend also enforces the safety gate "if VIS is stale, force
  `desired_mode = LandSafely`" — see `src/backend/cmd_schema.py`.
- Consequences:
  - Pros: single source of truth for "current desired vehicle state";
    backend is the natural place to enforce vis-stale safety override;
    the FC needs only one reliable command transport (TCP) instead of two.
  - Cons: backend death = forced LandSafely mid-flight (the FC's CMD link
    goes stale within `CMD_TIMEOUT_S=0.5`). Mitigations: process supervision
    (Sprint 1 H7), and an optional Vision → FC UDP fast-path (`docs/development_plan.md` H7 / P6.10) re-evaluated post-first-flight.
  - +20 ms typical added latency vs. direct (one bridge tick at 50 Hz).
    Documented as part of the latency budget; acceptable today.

## ADR-003 — RC switch is the sole arming authority

- Status: Accepted
- Date: 2026-05-04
- Context: An autonomous drone that can spool its own motors purely from a
  software command is a finger-loss event waiting to happen. The previous
  iteration of `runTakeoffMode` called `setArm(true)` unconditionally
  whenever the FC was placed into Takeoff mode, with no involvement from a
  human-held switch.
- Decision: `fc_app` *requests* arm by setting an internal flag and exposing
  `arm_gate_status` in TEL. Arming is *granted* only when all three are
  true: (a) CMD frame `arm: true`; (b) tracking-state-OK within 100 ms;
  (c) the operator's RC arm-switch is held high for ≥1 s, observed via
  `MSP_RC` query. Betaflight modes config has aux1 ARM range `1700-2100 µs`
  with stage-1 + stage-2 failsafe configured, ensuring the firmware-side
  switch always wins on disagreement.
- Consequences:
  - Pros: human-in-the-loop is structurally required, not just procedurally
    encouraged. Disarming the RC switch is always the fastest way to stop
    motors, regardless of `fc_app` state.
  - Cons: requires the boot-time `MSP_RC` query to be reliable; if MSP
    transport degrades, arming is denied (which is the safe direction).

## ADR-004 — First-flight gate requires HIL bench acceptance

- Status: Accepted
- Date: 2026-05-04
- Context: With safety-critical software written by a small team and not yet
  flight-tested, "looks correct on review" is not a sufficient bar.
- Decision: No autonomous flight — even tethered or netted — until the
  Pre-First-Flight Gate checklist in `docs/development_plan.md` is 100%
  green. The gate includes a Hardware-in-the-Loop bench run replaying
  recorded mission scenarios against a real Betaflight FC (motors detached)
  with `MspRcSink` writing real channel µs values, observable via Betaflight
  Configurator. See `docs/hil.md`.
- Consequences:
  - Pros: every change that could move RC channels gets a dry-run before any
    motor draws current.
  - Cons: longer change cycle for in-flight-behaviour edits; bench setup
    cost; requires keeping the Python `fake_betaflight_listener.py` and
    `replay_mission.py` in working order.

## ADR-005 — RC sink transport: USB MSP first, UART pivot in Sprint 1 if needed

- Status: Proposed (resolves to Accepted after Sprint 0 USB validation)
- Date: 2026-05-04
- Context: We need to drive RC channel µs values into a Betaflight FC. The
  practical options are: (1) MSP `MSP_SET_RAW_RC` over USB serial; (2) MSP
  over a hardware UART (Pi GPIO or USB-UART dongle to a free FC UART);
  (3) RC inject via CRSF/ELRS. Option 1 is the lowest-friction path Eddi can
  test alone at home (no soldering); the previous iteration of this project
  noted UART unreliability. Betaflight historically refuses to *arm* motors
  while USB is connected (battery-detect safety) — a known constraint.
- Decision: Sprint 0 implements `MspRcSink` over USB. Goal is to validate
  the full MSP framing + boot probes + 50 Hz channel writeback path with
  motors detached, observable in Betaflight Configurator. Arming itself is
  not attempted over USB. If Sprint 1 (with John) confirms USB-arming is
  refused or unreliable, pivot to UART by soldering Pi GPIO 14/15 to a free
  FC UART and re-running the same `MspRcSink` against `/dev/ttyAMA0`. The
  driver is the same; only the device path changes.
- Consequences:
  - Pros: Sprint 0 produces a validated software path immediately. Sprint 1
    can move directly to motor-on bench tests instead of debugging
    framing-level issues. Falls back cleanly if USB doesn't work.
  - Cons: USB cable strain-relief matters more than I'd like. The "USB
    detected → no arm" Betaflight behaviour means an unsupervised flip from
    bench to flight requires switching cables, which is its own risk;
    UART pivot is the more sustainable path long-term.

## ADR-006 — Sprint 0 / Sprint 1 split: software-first solo, hardware integration with John

- Status: Accepted
- Date: 2026-05-04
- Context: Eddi is solo on the software side until John returns 2026-05-11.
  John has the 3D printer and electrical/soldering tools. Eddi has the FC,
  Pi, and laptop at home. The audit produced ~150 findings; landing them
  in arbitrary order risks cross-cutting regressions.
- Decision: Sprint 0 (Eddi solo, May 4 → May 11) front-loads everything
  testable on a laptop or USB-tethered FC: documentation, dead-code purge,
  network-safety fixes, FC software bugs, vision pipeline robustness,
  webapp error handling, the HIL scaffold, the USB MSP driver, and the
  hover-throttle calibration script. Sprint 1 (John back, summer) focuses
  on physical assembly (mounts, harness), real-FC HIL acceptance, field
  tuning, and the tethered → netted → outdoor first-flight progression.
  Detailed task list and gates are in `docs/development_plan.md`.
- Consequences:
  - Pros: maximises the hardware-free week. By May 11 the codebase is in a
    "needs hardware integration, not bulk coding" state.
  - Cons: any Sprint 0 change to in-flight behaviour must still wait for a
    Sprint 1 real-FC HIL run before flight (per ADR-004) — the gate doesn't
    move. Sprint 0 work is validated on `RecordingSink` / `FakeBetaflightSink`
    and on a USB-attached Betaflight only.
