# AOT_DRONE
Autonomous Object Tracking Drone

# Drone Stack (C + Python + Web)

Monorepo for the drone control stack:
- `src/fc`: C/C++ flight controller comms and control loop
- `src/vision`: Python vision + tracking
- `src/backend`: Web gateway (WebSocket/REST)
- `src/webapp`: React UI

## Docs
- `docs/architecture.md`: system overview
- `docs/message-spec.md`: message contract (source of truth)
- `docs/setup.md`: build/run instructions
- `docs/decisions.md`: architecture decision records (ADRs)

## Reproducibility
- Pinned Python tooling: `requirements-dev.txt`
- Pinned backend runtime deps: `src/backend/requirements.txt`
- CI-equivalent local check command: `./scripts/dev/runall.sh`

Install dependencies before running lint/tests:

```bash
python -m pip install --upgrade pip==24.3.1
python -m pip install -r requirements-dev.txt
python -m pip install -r src/backend/requirements.txt
```

`./scripts/dev/runall.sh` now checks `ruff` availability explicitly and exits with a clear error if dev tooling is missing.

## Vision Local Runner (PR-V1)
Run the local vision scaffold (stub detector/tracker) with newline-delimited VIS JSON output:

```bash
python -m src.vision.main --source webcam:0 --preview
python -m src.vision.main --source file:assets/test.mp4 --vis-hz 20 --max-frames 300
```

Optional outputs:
- `--no-output` disables stdout VIS emission
- `--output jsonl:logs/vis.jsonl` writes VIS messages to a JSONL file
- `--vis-hz` uses monotonic time-based throttling (first frame emits immediately)

Run vision/test commands from the repository root so `src.*` imports resolve consistently.

## Backend WebSocket Stream (PR-B4)
Backend exposes `GET /ws` with event envelopes:

```json
{
  "ws_ver": 1,
  "event": "TEL_UPDATE|VIS_UPDATE|LINK_STATUS|WARNING",
  "data": {},
  "timestamp_s": 123.456,
  "seq": 42
}
```

Throttling defaults (configurable with env vars):
- `TEL_UPDATE`: capped at `BACKEND_WS_TEL_HZ` (default `20 Hz`)
- `VIS_UPDATE`: capped at `BACKEND_WS_VIS_HZ` (default `20 Hz`)
- `LINK_STATUS`: heartbeat at `BACKEND_WS_LINK_HZ` (default `2 Hz`)

## Current legacy message formats (to be standardized)

Telemetry:
`TEL <timestamp_s> <control_mode> <tracking_state> <distFront_m> <distBack_m> <distBottom_m> <target_x> <target_y> <bound_w> <bound_h> <confidence>`

Vision:
`<state> <loc_x> <loc_y> <bound_w> <bound_h> <confidence> <timestamp_s>`
