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
- Node.js (optional, for web UI later)

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

The repository includes reproducible helper scripts:

```bash
./scripts/dev/lint.sh
./scripts/dev/runall.sh
```

`runall.sh` installs pinned dependencies, checks that `ruff` is available, runs lint/tests, and verifies the C++ build.

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

---

## Continuous Integration (CI)

GitHub Actions runs the following on every push and pull request:

- Pre-commit checks (formatting and linting)
- C/C++ build using CMake
- Python test discovery using pytest

If CI fails, the failure should be reproducible locally using the commands above.

Socket integration note:
- Keep at least one CI environment that can bind local UDP sockets so ingest loop tests execute without being skipped.
- In CI we enforce this with `REQUIRE_UDP_BIND_TESTS=1` for pytest.

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
- Project scaffolding and structure
- Tooling and CI configuration
- Documentation defining architecture and message contracts

Functional implementations (backend logic, flight control integration, vision integration) will be added in subsequent pull requests.

Refer to:
- `docs/architecture.md` for system structure
- `docs/message-spec.md` for communication contracts
