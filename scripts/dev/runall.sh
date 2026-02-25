#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

python -m pip install --upgrade pip==24.3.1
python -m pip install -r requirements-dev.txt
python -m pip install -r src/backend/requirements.txt

if ! python -m ruff --version >/dev/null 2>&1; then
  echo "ERROR: ruff is unavailable. Install dev deps: python -m pip install -r requirements-dev.txt" >&2
  exit 1
fi

python -m ruff check src tests
python -m pre_commit run --all-files
pytest -q
cmake -S . -B build
cmake --build build -j
