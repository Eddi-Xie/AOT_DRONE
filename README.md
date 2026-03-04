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
- Pinned vision runtime deps: `src/vision/requirements.txt`
- CI-equivalent local check command: `./scripts/dev/runall.sh`

Install dependencies before running lint/tests:

```bash
python -m pip install --upgrade pip==26.0.1
python -m pip install -r requirements-dev.txt
python -m pip install -r src/backend/requirements.txt
python -m pip install -r src/vision/requirements.txt
```

`./scripts/dev/runall.sh` now checks `ruff` availability explicitly and exits with a clear error if dev tooling is missing.

## How To Run

Run all commands from the repository root unless noted.

### 1) One-time local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip==26.0.1
python -m pip install -r requirements-dev.txt
python -m pip install -r src/backend/requirements.txt
python -m pip install -r src/vision/requirements.txt

npm --prefix src/webapp install
cmake -S . -B build
cmake --build build -j
```

### 2) Quick Demo (recommended)

```bash
./scripts/dev/demo_up.sh
./scripts/dev/demo_status.sh
./scripts/dev/demo_down.sh
```

What `demo_up.sh` does:
- preflight checks for `.venv`, Python modules, `node_modules`, `fc_app`, and required ports
- starts backend, `fc_app`, vision, and webapp
- writes logs to `logs/demo_*.log`
- writes PIDs to `.run/demo_pids`
- supports partial-stack startup with `DEMO_SKIP_BACKEND`, `DEMO_SKIP_FC`, `DEMO_SKIP_VISION`, `DEMO_SKIP_WEBAPP` (`0` or `1`)

Vision defaults in demo mode:
- Uses detect mode with this default `DEMO_VISION_ARGS`:
  - `--source webcam:0 --mode detect --model-path yolov8n.pt --target-class 0 --preview --conf-threshold 0.25 --detect-hold-n 5 --infer-size 640`
- `yolov8n.pt` must exist at repo root (or override model path via `DEMO_VISION_ARGS`).

Override example:

```bash
DEMO_VISION_ARGS="--source webcam:0 --mode detect --model-path /abs/path/custom.pt --target-class 0 --preview --conf-threshold 0.3 --detect-hold-n 5 --infer-size 640" ./scripts/dev/demo_up.sh
```

Partial-stack examples:

```bash
DEMO_SKIP_FC=1 ./scripts/dev/demo_up.sh
DEMO_SKIP_VISION=1 DEMO_SKIP_WEBAPP=1 ./scripts/dev/demo_up.sh
```

### 3) Manual run (debug)

Use this if you want explicit 4-terminal control.

Runtime environment (bash/zsh):

```bash
source .venv/bin/activate

export BACKEND_TEL_HOST=127.0.0.1
export BACKEND_TEL_PORT=9001
export BACKEND_VIS_HOST=127.0.0.1
export BACKEND_VIS_PORT=9003
export BACKEND_FC_HOST=127.0.0.1
export BACKEND_FC_PORT=9002
export BACKEND_CMD_BRIDGE_ENABLED=1
export BACKEND_TEL_INGEST_ENABLED=1
export BACKEND_VIS_INGEST_ENABLED=1
export BACKEND_VIDEO_ENABLED=1

export VISION_BACKEND_HTTP=http://127.0.0.1:8000
export VISION_VIS_UDP_HOST=127.0.0.1
export VISION_VIS_UDP_PORT=9003

export VITE_BACKEND_HTTP_URL=http://127.0.0.1:8000
export VITE_BACKEND_WS_URL=ws://127.0.0.1:8000/ws
export VITE_VIDEO_URL=/video
export VITE_OVERLAY_SOURCE=VIS
```

Terminal 1: backend

```bash
source .venv/bin/activate
python -m uvicorn src.backend.app:app --host 127.0.0.1 --port 8000
```

Terminal 2: FC app

```bash
./build/src/fc/fc_app
```

Terminal 3: vision (detect person mode)

```bash
source .venv/bin/activate
python -m src.vision.main --source webcam:0 --mode detect --model-path yolov8n.pt --target-class 0 --preview --conf-threshold 0.25 --detect-hold-n 5 --infer-size 640
```

Terminal 4: webapp

```bash
npm --prefix src/webapp run dev
```

Quick smoke checks:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/api/status | head -c 400 && echo
```

### 4) Test commands

```bash
pytest -q
pytest -q tests/integration
./scripts/dev/e2e.sh
RUN_FULL_E2E=1 ./scripts/dev/e2e.sh
./scripts/dev/runall.sh
```

## Vision Local Runner (PR-V2)
Run the local vision runner with real frame capture + backend outputs:

```bash
python -m src.vision.main --source webcam:0 --mode pattern --pattern sweep --preview
python -m src.vision.main --source file:assets/test.mp4 --vis-hz 20 --frame-fps 10 --max-frames 300
```

Optional outputs:
- `--no-output` disables stdout VIS emission
- `--output jsonl:logs/vis.jsonl` writes VIS messages to a JSONL file
- `--vis-hz` and `--frame-fps` use independent monotonic schedules
- `--no-vis-udp` disables VIS UDP send
- `--no-frame-push` disables frame HTTP push

See `src/vision/README.md` for full CLI/config options and end-to-end smoke steps.

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
