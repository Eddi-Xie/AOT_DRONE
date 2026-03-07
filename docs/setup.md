# Setup & Development Guide

This document describes how to set up the development environment and verify the repository is configured correctly.

---

## Prerequisites

### System
- macOS or Linux
- Git
- CMake ≥ 3.16
- C/C++ compiler (clang or gcc)
- Python ≥ 3.10
- Node.js (required for web UI build/tests)

---

## Repository Setup

Clone the repository and enter the root directory.

```bash
git clone <repo-url>
cd drone-stack
```

---

## Python Setup

Install Python tooling (recommended inside a virtual environment or conda environment).

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip==24.3.1
python -m pip install -r requirements-dev.txt
```

Install the pre-commit git hooks so checks run automatically on every commit.

```bash
python -m pre_commit install
```

Run all checks manually (this mirrors what CI runs).

```bash
python -m ruff check src tests
python -m pre_commit run --all-files
```

Install the required libraries for backend.
```bash
python -m pip install -r src/backend/requirements.txt
```

Install the required libraries for vision runtime.
```bash
python -m pip install -r src/vision/requirements.txt
```

Video stream defaults and optional strict decode validation:
```bash
export BACKEND_VIDEO_ENABLED=1
export BACKEND_VIDEO_FPS=10
export BACKEND_VIDEO_MAX_JPEG_BYTES=200000
export BACKEND_VIDEO_FRAME_FRESH_S=1.0
export BACKEND_VIDEO_VALIDATE_DECODE=0
```

The repository includes reproducible helper scripts:

```bash
./scripts/dev/lint.sh
./scripts/dev/runall.sh
```

`runall.sh` installs pinned dependencies, checks that `ruff` is available, runs lint/tests, and verifies the C++ build.

---

## Quick Demo (recommended)

Start/inspect/stop the full laptop demo stack with one-command scripts:

```bash
./scripts/dev/demo_up.sh
./scripts/dev/demo_status.sh
./scripts/dev/demo_down.sh
```

`demo_up.sh` starts backend + `fc_app` + vision + webapp, writes logs in `logs/`, and records PIDs in `.run/demo_pids`.
You can skip components for partial runs with `DEMO_SKIP_BACKEND`, `DEMO_SKIP_FC`, `DEMO_SKIP_VISION`, `DEMO_SKIP_WEBAPP` (set each to `0` or `1`).

Vision defaults in demo mode use:
- `DEMO_VISION_ARGS="--source webcam:0 --mode detect --model-path yolov8n.pt --target-class 0 --preview --conf-threshold 0.25 --detect-hold-n 5 --infer-size 640"`

Model expectation:
- `yolov8n.pt` must exist at repo root by default.
- Override the model/vision flags with `DEMO_VISION_ARGS`:

```bash
DEMO_VISION_ARGS="--source webcam:0 --mode detect --model-path /abs/path/custom.pt --target-class 0 --preview --conf-threshold 0.3 --detect-hold-n 5 --infer-size 640" ./scripts/dev/demo_up.sh
```

Partial-stack examples:

```bash
DEMO_SKIP_FC=1 ./scripts/dev/demo_up.sh
DEMO_SKIP_VISION=1 DEMO_SKIP_WEBAPP=1 ./scripts/dev/demo_up.sh
```

---

## Manual run (debug)

Use the manual 4-terminal flow when debugging individual components.

Runtime environment:

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

Terminal 1 (backend):

```bash
python -m uvicorn src.backend.app:app --host 127.0.0.1 --port 8000
```

Terminal 2 (`fc_app`):

```bash
./build/src/fc/fc_app
```

Terminal 3 (vision detect mode):

```bash
python -m src.vision.main --source webcam:0 --mode detect --model-path yolov8n.pt --target-class 0 --preview --conf-threshold 0.25 --detect-hold-n 5 --infer-size 640
```

Terminal 4 (webapp):

```bash
npm --prefix src/webapp run dev
```

---

## C/C++ Build Verification

The repository includes a minimal CMake target to verify that the build toolchain is working.

From the repository root:

```bash
cmake -S . -B build
cmake --build build -j
```

A successful build confirms:
- CMake is configured correctly
- A C/C++ compiler is available
- CI build steps should pass locally

---

## PR4 Verification (FC Protocol)

PR4 adds a runnable FC executable (`fc_app`) that listens for TCP commands and publishes UDP telemetry.

Build `fc_app`:

```bash
cmake -S . -B build
cmake --build build -j
```

Run `fc_app` (terminal 1):

```bash
./build/src/fc/fc_app
```

Listen to telemetry (terminal 2):

```bash
python scripts/dev/listen_tel.py
```

Send commands (terminal 3):

```bash
python scripts/dev/send_cmd.py 1 --repeat-hz 50 --burst-seconds 0.6
```

Expected behavior:
- Telemetry is published continuously at ~50 Hz on UDP `127.0.0.1:9001`.
- While CMD frames are received, telemetry `control_mode` follows `desired_mode` (for example `1`).
- When CMD frames stop for more than `CMD_TIMEOUT_S` (`0.5s`), telemetry falls back to `control_mode=2` (`LandSafely`).

Optional one-shot smoke check (requires `fc_app` running):

```bash
python scripts/dev/fc_protocol_smoke.py
```

Backend MJPEG smoke checks:

```bash
curl -i http://127.0.0.1:8000/video
```

Push a local JPEG into backend frame ingest:

```bash
python scripts/dev/push_frame.py /path/to/frame.jpg
```

Equivalent curl:

```bash
curl -X POST http://127.0.0.1:8000/api/frame \\
  -H 'Content-Type: image/jpeg' \\
  --data-binary '@/path/to/frame.jpg'
```

---

## Continuous Integration (CI)

GitHub Actions runs the following on every push and pull request:

- Pre-commit checks (formatting and linting)
- C/C++ build using CMake
- Python test discovery using pytest
- Webapp unit tests and production build (`npm run test`, `npm run build`)

If CI fails, the failure should be reproducible locally using the commands above.

Socket integration note:
- Keep at least one CI environment that can bind local UDP/TCP sockets so ingest and bridge loop tests execute without being skipped.
- In CI we enforce this with `REQUIRE_UDP_BIND_TESTS=1` and `REQUIRE_TCP_BIND_TESTS=1` for pytest.

PR11 end-to-end test flow:
- CI-safe E2E checks (VIS/TEL ingest paths, WS, status, CMD gating with fake FC) are part of the default suite:

```bash
pytest -q
```

- Exact PR11-focused commands:

```bash
python -m ruff check tests/integration
pytest -q tests/integration
RUN_FULL_E2E=1 pytest -q -m integration
```

- Convenience wrapper:

```bash
./scripts/dev/e2e.sh
RUN_FULL_E2E=1 ./scripts/dev/e2e.sh
```

- Optional subprocess smoke (backend + vision replay, and optionally `fc_app`) is marked `integration`:

```bash
pytest -q -m integration
RUN_FULL_E2E=1 pytest -q -m integration
RUN_FULL_E2E=1 RUN_FULL_E2E_WITH_FC=1 pytest -q -m integration
```

---

## Development Workflow

Recommended workflow for new changes:

1. Create a feature branch from `dev`
2. Make changes on the feature branch
3. Run pre-commit checks locally
4. Push the branch and open a pull request targeting `dev`
5. Merge only after CI passes

Direct commits to `main` are discouraged.

---

## Current Project State

At this stage, the repository contains:
- Implemented FC app command/telemetry loop and protocol handling
- Implemented backend ingest, command bridge, status APIs, WS, and video endpoints
- Implemented vision runner modes and backend publishing integration
- Implemented webapp live status/video overlay UI with tests
- Tooling and CI configuration

Refer to:
- `docs/architecture.md` for system structure
- `docs/message-spec.md` for communication contracts
