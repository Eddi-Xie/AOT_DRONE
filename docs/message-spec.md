# Message Spec (v1) — UDP Telemetry + TCP Commands

This document is the source of truth for inter-process communication between:
- FC process (C/C++) running the control loop
- Backend (Python) providing WebSocket API to React UI
- Vision (Python) producing tracking signals

All values and numeric codes in this document are normative.

---

## 1. Enums

### 1.1 TrackingState
Numeric values must match the Python DetectState IntEnum.

- NoTarget = 1
- TargetDetected = 2
- Tracking = 3
- Searching = 4

### 1.2 ControlMode
- Manual = 0
- Tracking = 1
- LandSafely = 2
- Takeoff = 3

---

## 2. Ports, Rates, Limits

### 2.1 Ports
- UDP telemetry (FC -> Backend): 127.0.0.1:9001
- UDP vision (Vision -> Backend): 127.0.0.1:9003
- TCP commands (Backend -> FC): FC listens on 0.0.0.0:9002

### 2.2 Nominal Rates
- Telemetry UDP: 50 Hz (or same as FC loop)
- Vision UDP: 10–30 Hz
- Commands TCP: 20–50 Hz (backend should resend latest command at a fixed rate)

### 2.3 Size Limits
- Max UDP telemetry datagram payload: 1024 bytes
- Max UDP vision datagram payload: 512 bytes
- Max TCP command frame payload: 4096 bytes

If a received UDP datagram exceeds the configured max size, the receiver must drop it and log the event.

---

## 3. Transport Overview

### 3.1 Telemetry (FC -> Backend): UDP
Telemetry is sent as UDP datagrams. Loss is acceptable; backend displays latest.

Design constraints:
- Sender must not block on telemetry output.
- Each datagram must be self-contained and parseable independently.

### 3.2 Vision (Vision -> Backend): UDP
Vision tracking is sent as UDP datagrams. Loss is acceptable; backend uses latest.

### 3.3 Commands (Backend -> FC): TCP
Commands are reliable. Backend connects to FC’s TCP command server.

- Backend connects to: <FC_IP>:9002
- FC listens on: 0.0.0.0:9002
- Command frames are length-prefixed (see Section 5).

---

## 4. Message Formats (JSON v1)

All messages are UTF-8 JSON in v1.

### 4.1 Common Fields
- type: string identifying message kind ("TEL", "VIS", "CMD")
- seq: integer, monotonic per-sender (wrap-around allowed)
- timestamp_s: float seconds

Timestamp guidance:
- Prefer a monotonic clock source where available.
- timestamp_s is for ordering/diagnostics; receivers must rely on seq for drop/gap detection.
- timestamp_s is monotonic *to the sender*. Each process has its own clock origin, so consumers MUST NOT compare `timestamp_s` values across senders. To compute "freshness from receiver's POV", record the local monotonic time of receipt and use that for age computations.

---

### 4.2 Telemetry Datagram (UDP, FC -> Backend)

Required fields:
- type: "TEL"
- seq: integer
- timestamp_s: float
- control_mode: int (ControlMode)
- tracking_state: int (TrackingState)
- distFront_m: float
- distBack_m: float
- distBottom_m: float
- target_x: float
- target_y: float
- bound_w: float
- bound_h: float
- confidence: float

Optional fields (added incrementally; consumers MUST tolerate absence):
- cmd_age_s: float — seconds since the FC last accepted a CMD frame from
  backend, observed by the FC. Used by the UI to surface command-link
  health.
- failsafe_state: int — 0=Healthy, 1=StaleSoft, 2=StaleHard. Reflects the
  FC's two-stage stale-CMD failsafe state machine.
- failsafe_age_s: float — time spent in the current `failsafe_state`.
- arm_gate_status: int — 0=Denied, 1=Requested, 2=Granted. Reflects the FC's
  three-condition arm-authority gate (CMD `arm:true` + tracking-state-OK +
  RC arm-switch held). See ADR-003.
- last_loop_dt_ms: float — observed dt of the previous FC control-loop
  iteration; used to detect loop stalls.
- msp_tx_ratio: float in [0,1] — fraction of MSP writes acknowledged in the
  last second.
- last_intent_id: string — UUID of the most recently-applied operator
  intent, echoed back from the originating CMD. Allows webapp → CMD → TEL
  end-to-end tracing.

Semantics:
- target_x, target_y are normalized image coordinates in [-1, 1]
  - (0, 0) is center, +x is right, +y is up
- bound_w, bound_h are normalized sizes in [0, 1]
- confidence in [0, 1]
- If tracking_state != Tracking, target_x/target_y/bound_w/bound_h/confidence should still be present.
  - Use 0.0 for target_x/target_y/bound_w/bound_h and 0.0 confidence when no target is available.
  - This 0.0 substitution is defined ONLY for these tracking-related fields.
- All numeric fields MUST be finite (no NaN, no ±Inf).
- For any required numeric field other than the tracking fields above,
  senders MUST NOT silently substitute 0.0 when a finite value is unavailable
  or non-finite, because 0.0 can look like a valid measurement (e.g. a
  distance of 0.0 m or a timestamp of zero) and would mask sensor or logic
  faults. Instead, senders MUST drop the TEL message and log/report the
  fault. Receivers MUST drop messages containing non-finite numerics and
  increment a drop counter for diagnostics.

Example:
{
  "type": "TEL",
  "seq": 123,
  "timestamp_s": 1706980000.123,
  "control_mode": 1,
  "tracking_state": 3,
  "distFront_m": 0.0,
  "distBack_m": 0.0,
  "distBottom_m": 1.2,
  "target_x": 0.10,
  "target_y": -0.05,
  "bound_w": 0.20,
  "bound_h": 0.30,
  "confidence": 0.86,
  "cmd_age_s": 0.018,
  "failsafe_state": 0,
  "failsafe_age_s": 0.0
}

---

### 4.3 Vision Datagram (UDP, Vision -> Backend)

Required fields:
- type: "VIS"
- seq: integer
- timestamp_s: float
- tracking_state: int (TrackingState)
- loc_x: float
- loc_y: float
- bound_w: float
- bound_h: float
- confidence: float

Semantics:
- loc_x, loc_y are normalized in [-1, 1]
  - (0, 0) is center, +x is right, +y is up
- bound_w, bound_h are normalized in [0, 1]
- confidence in [0, 1]
- If tracking_state != Tracking, set loc_x/loc_y/bound_w/bound_h/confidence to 0.0.
  - Receivers MAY accept tiny floating-point drift on the zero values
    (abs(value) <= 1e-6) and normalise such inputs to 0.0; values outside
    that tolerance MUST be treated as a schema violation and the message
    dropped + logged.

Example:
{
  "type": "VIS",
  "seq": 455,
  "timestamp_s": 1706980000.455,
  "tracking_state": 3,
  "loc_x": 0.12,
  "loc_y": -0.04,
  "bound_w": 0.21,
  "bound_h": 0.30,
  "confidence": 0.85
}

---

## 5. TCP Command Framing (Backend -> FC)

TCP stream is framed with a 4-byte big-endian unsigned length prefix:

- Header: uint32_be payload_length
- Payload: payload_length bytes (UTF-8 JSON)
- payload_length must be 1..4096
- If payload_length is outside limits, receiver must close the connection.

---

### 5.1 Command Payload (JSON)

Required fields:
- type: "CMD"
- seq: integer
- timestamp_s: float
- desired_mode: int (ControlMode)

Optional fields (expand later):
- arm: bool
- land_safely: bool
- setpoints: object
- tracking: object (latest vision data as used by backend)

Backend behavior:
- Backend should resend the latest CMD at a fixed rate (20–50 Hz) while connected.
- Backend may override requested `desired_mode=Tracking` to `desired_mode=LandSafely` if VIS is
  missing or stale (backend-side safety gate).

Example:
{
  "type": "CMD",
  "seq": 9001,
  "timestamp_s": 1706980000.456,
  "desired_mode": 1,
  "arm": true,
  "setpoints": {
    "yaw_rate": 0.0,
    "pitch": 0.0,
    "roll": 0.0,
    "throttle": 0.5
  }
}

---

## 6. Safety & Timeout Behavior

### 6.1 Command Timeout
FC must track last valid command receive time.

- CMD_TIMEOUT_S = 0.5 seconds

If no valid CMD frame is received within CMD_TIMEOUT_S:
- FC must enter LandSafely OR apply neutral/hold behavior (implementation-defined),
- and reflect the degraded state in telemetry.

### 6.2 Backend Disconnect
Backend should retry connecting to FC with a backoff (e.g., 1s intervals).
UI should indicate command link status.

---

## 7. Versioning
Messages include `type` field; a future `ver` field may be added.
Any breaking changes must update this document first.
