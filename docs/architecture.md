# System Architecture

This project implements a modular drone control stack composed of four primary components:

1. Flight Controller (FC) process — C/C++
2. Vision process — Python
3. Backend gateway — Python
4. Web UI — React (browser)

The architecture is designed to:
- keep the real-time control loop isolated and deterministic
- prevent UI or network failures from affecting flight safety
- allow each component to restart independently
- make inter-process communication explicit and well-defined

> **Current state (2026-05):** The C++ FC process (`fc_app`) computes RC channel
> commands every 20 ms but does not yet emit them to the physical Betaflight
> flight controller — there is no MSP/UART/serial output path implemented in
> `src/fc/`. Sprint 0 of `docs/development_plan.md` introduces an `IRcSink`
> abstraction (`NullSink`, `RecordingSink`, `FakeBetaflightSink`) and a USB
> `MspRcSink` for bench validation. UART pivot, real-FC HIL acceptance, and
> first autonomous flight are scheduled for Sprint 1. Until those land,
> autonomous flight is not possible and `fc_app` should be treated as a
> simulator — never connect a propellered airframe to it expecting hardware
> behaviour.

---

## High-Level Overview
```
                                                   +------------------+
                                                   |                  |
                                                   |  Vision Process  |
                                                   |  (Python / YOLO) |
                                                   |                  |
                                                   +--------+---------+
                                                            |
                                                            | UDP (vision data)
                                                            v
+------------------+        UDP (telemetry)        +-------------------+
|                  | --------------------------->  |                   |
|  Flight Control  |                               |     Backend       |
|  (C / C++)       | <---------------------------  |    (Python)       |
|                  |        TCP (commands)         |                   |
+------------------+                               +---------+---------+
                                                           |
                                                           | WebSocket
                                                           v
                                                   +-------------------+
                                                   |                   |
                                                   |   React Web UI    |
                                                   |   (Browser)       |
                                                   +-------------------+
```
---

## Component Responsibilities

### 1. Flight Controller (C/C++)

The Flight Controller process is responsible for all safety-critical and time-vsensitive behavior.

Responsibilities:
- Communicate with the physical flight controller hardware (e.g. MSP)
- Run the main control loop at a fixed rate
- Apply control logic based on the latest received command
- Send telemetry data periodically
- Enforce safety behavior on command loss or degraded states

Design principles:
- Must never block on UI or network delays
- Must remain safe if backend or UI crashes
- Treats commands as advisory, not authoritative

---

### 2. Vision Process (Python)

The Vision process performs perception tasks and produces tracking signals.

Responsibilities:
- Capture video frames
- Run object detection / tracking (e.g. YOLO)
- Estimate target position and confidence
- Report tracking state transitions

Notes:
- Vision output is treated as **best-effort**
- Loss of vision input must not crash or destabilize the system
- Vision does not directly command the FC

---

### 3. Backend Gateway (Python)

The Backend acts as the **integration and coordination layer** between systems.

Responsibilities:
- Receive telemetry from the FC via UDP
- Receive vision data via UDP
- Expose a WebSocket API to the React UI
- Accept user commands from the UI
- Send reliable command messages to the FC over TCP
- Maintain connection state and retry logic

Design principles:
- Backend failure must not affect FC safety
- Backend may restart independently
- Backend maintains the “latest desired state” and resends commands periodically

---

### 4. Web UI (React)

The Web UI provides user interaction and visualization.

Responsibilities:
- Display live telemetry
- Display tracking state and confidence
- Allow mode selection and command input
- Show connection and system status

Notes:
- UI has no direct connection to FC
- UI failure must not affect flight behavior

---

## Communication Design

### Telemetry: FC → Backend
- Protocol: UDP
- Rationale: loss-tolerant, non-blocking, low overhead
- Each telemetry message is self-contained

### Vision Data: Vision → Backend
- Protocol: UDP
- Rationale: best-effort perception data, high update rate

### Commands: Backend → FC
- Protocol: TCP
- Framing: length-prefixed messages
- Rationale: commands must be delivered reliably and in order

### UI Communication
- Protocol: WebSocket
- Backend broadcasts telemetry and accepts commands

---

## Failure & Safety Philosophy

- FC must enter a safe state if command messages stop arriving
- Backend disconnection must not stall the FC loop
- UI disconnection must not affect backend or FC
- All communication boundaries are explicit and documented in `message-spec.md`

---

## Source of Truth

The following documents define the system contract:
- `docs/message-spec.md` — message formats, ports, enums, timeouts
- `docs/architecture.md` — system structure and responsibilities

Any change to communication behavior must update the documentation first.
