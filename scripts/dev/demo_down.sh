#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

PID_FILE="${ROOT_DIR}/.run/demo_pids"

log() {
  printf '[demo_down] %s\n' "$*"
}

is_running_pid() {
  local pid="$1"
  [[ -n "${pid}" ]] && [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

stop_pid() {
  local name="$1"
  local pid="$2"

  if ! [[ "${pid}" =~ ^[0-9]+$ ]]; then
    log "${name}: no pid recorded"
    return 0
  fi

  if ! is_running_pid "${pid}"; then
    log "${name}: not running (pid=${pid})"
    return 0
  fi

  log "Stopping ${name} (pid=${pid}) with SIGTERM"
  kill "${pid}" 2>/dev/null || true

  local i
  for i in $(seq 1 25); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      log "${name}: stopped"
      return 0
    fi
    sleep 0.2
  done

  if kill -0 "${pid}" 2>/dev/null; then
    log "${name}: still running, sending SIGKILL"
    kill -9 "${pid}" 2>/dev/null || true
  fi

  if kill -0 "${pid}" 2>/dev/null; then
    log "${name}: failed to stop (pid=${pid})"
    return 1
  fi

  log "${name}: stopped"
  return 0
}

load_pids() {
  backend_pid=""
  fc_pid=""
  vision_pid=""
  webapp_pid=""

  while IFS='=' read -r key value; do
    case "${key}" in
      backend_pid) backend_pid="${value}" ;;
      fc_pid) fc_pid="${value}" ;;
      vision_pid) vision_pid="${value}" ;;
      webapp_pid) webapp_pid="${value}" ;;
    esac
  done < "${PID_FILE}"
}

main() {
  if [[ ! -f "${PID_FILE}" ]]; then
    log "Demo not running (missing ${PID_FILE})"
    exit 0
  fi

  load_pids

  # Stop in reverse dependency order.
  stop_pid "webapp" "${webapp_pid}"
  stop_pid "vision" "${vision_pid}"
  stop_pid "fc_app" "${fc_pid}"
  stop_pid "backend" "${backend_pid}"

  rm -f "${PID_FILE}"
  log "Removed pid file: ${PID_FILE}"
  log "Demo stack is down"
}

main
