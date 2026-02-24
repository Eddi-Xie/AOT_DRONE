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

`runall.sh` installs pinned dependencies, runs lint/tests, and verifies the C++ build.

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

## Continuous Integration (CI)

GitHub Actions runs the following on every push and pull request:

- Pre-commit checks (formatting and linting)
- C/C++ build using CMake
- Python test discovery using pytest

If CI fails, the failure should be reproducible locally using the commands above.

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
