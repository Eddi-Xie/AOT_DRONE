#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

pytest -q tests/integration

if [[ "${RUN_FULL_E2E:-0}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
  pytest -q -m integration
fi
