# Message Spec (v1) — UDP Telemetry + TCP Commands

This document is the source of truth for inter-process communication between:
- FC process (C/C++) running the control loop
- Backend (Python) providing WebSocket API to React UI
- Vision (Python) producing tracking signals

## 1. Enums

### 1.1 TrackingState
Numeric values must match Python DetectState IntEnum.

- NoTarget = 1
- TargetDetected = 2
- Tracking = 3
- Searching = 4

### 1.2 ControlMode
- Manual = 0
- Tracking = 1
- LandSafely = 2
- Takeoff = 3

## 2. Transport Overview

### 2.1 Telemetry (FC -> Backend): UDP
Telemetry is sent as UDP datagrams. Loss is acceptable; backend displays latest.

- Protocol: UDP
- Address: 127.0.0.1
- Port: 9001
- Rate: 50 Hz (or same as FC loop)
- Max datagram payload: 1024 bytes
- Each datagram must be self-contained and parseable independently.

### 2.2 Vision (Vision -> Backend): UDP (optional but recommended)
Vision tracking is sent as UDP datagrams.

- Protocol: UDP
- Address: 127.0.0.1
- Port: 9003
- Rate: 10–30 Hz
- Max datagram payload: 512 bytes

### 2.3 Commands (Backend -> FC): TCP
Commands are reliable. Backend connects to FC’s TCP command server.

- Protocol: TCP
- FC listens on: 0.0.0.0:9002
- Backend connects to: <FC_IP>:9002
- Command frames are length-prefixed (see Section 4).
- Max frame length: 4096 bytes

## 3. Message Formats

All messages are UTF-8 JSON in v1.

### 3.1 Telemetry Datagram (UDP, FC -> Backend)
JSON object fields:

Required:
- type: "TEL"
- seq: integer (monotonic, wraps allowed)
- timestamp_s: float (seconds, monotonic preferred)
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

Semantics:
- target_x, target_y are normalized image coordinates in [-1, 1]
  - (0,0) is center, +x right, +y up
- bound_w, bound_h are normalized sizes in [0, 1]
- confidence in [0, 1]

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
  "confidence": 0.86
}

### 3.2 Vision Datagram (UDP, Vision -> Backend)
JSON object fields:

Required:
- type: "VIS"
- seq: integer (monotonic, wraps allowed)
- timestamp_s: float
- tracking_state: int (TrackingState)
- loc_x: float (normalized [-1, 1])
- loc_y: float (normalized [-1, 1])
- bound_w: float (normalized [0, 1])
- bound_h: float (normalized [0, 1])
- confidence: float [0, 1]

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

## 4. TCP Command Framing (Backend -> FC)

TCP stream is framed with a 4-byte big-endian unsigned length prefix:

- Header: uint32_be payload_length
- Payload: payload_length bytes (UTF-8 JSON)
- payload_length must be 1..4096
- If payload_length is outside limits, receiver must close the connection.

### 4.1 Command Payload (JSON)
Required fields:
- type: "CMD"
- timestamp_s: float
- desired_mode: int (ControlMode)

Optional fields (can be expanded later):
- arm: bool
- land_safely: bool
- setpoints: object
- tracking: object (latest vision fused data)

Example:
{
  "type": "CMD",
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

## 5. Safety & Timeout Behavior

### 5.1 Command Timeout
FC must track last valid command receive time.

- CMD_TIMEOUT_S = 0.5 seconds

If no valid CMD frame is received within CMD_TIMEOUT_S:
- FC must enter LandSafely OR apply neutral/hold behavior (implementation-defined),
- and reflect the degraded state in telemetry.

### 5.2 Backend Disconnect
Backend should retry connecting to FC with a backoff (e.g., 1s intervals).
UI should indicate command link status.

## 6. Versioning
Messages include `type` field; a future `ver` field may be added.
Any breaking changes must update this doc first.
