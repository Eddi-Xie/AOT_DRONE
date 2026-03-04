#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

PID_FILE="${ROOT_DIR}/.run/demo_pids"

log() {
  printf '[demo_status] %s\n' "$*"
}

is_running_pid() {
  local pid="$1"
  [[ -n "${pid}" ]] && [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

print_proc_status() {
  local name="$1"
  local pid="$2"

  if ! [[ "${pid}" =~ ^[0-9]+$ ]]; then
    log "${name}: no pid recorded"
    return
  fi

  if is_running_pid "${pid}"; then
    log "${name}: RUNNING (pid=${pid})"
  else
    log "${name}: STOPPED (pid=${pid})"
  fi
}

tail_log_if_present() {
  local label="$1"
  local path="$2"

  if [[ -n "${path}" && -f "${path}" ]]; then
    log "Last 5 lines of ${label} log (${path}):"
    tail -n 5 "${path}"
  fi
}

load_state() {
  backend_pid=""
  fc_pid=""
  vision_pid=""
  webapp_pid=""
  demo_skip_backend="0"
  demo_skip_fc="0"
  demo_skip_vision="0"
  demo_skip_webapp="0"
  backend_url="http://127.0.0.1:8000"
  backend_log="${ROOT_DIR}/logs/demo_backend.log"
  fc_log="${ROOT_DIR}/logs/demo_fc.log"
  vision_log="${ROOT_DIR}/logs/demo_vision.log"
  webapp_log="${ROOT_DIR}/logs/demo_webapp.log"

  while IFS='=' read -r key value; do
    case "${key}" in
      backend_pid) backend_pid="${value}" ;;
      fc_pid) fc_pid="${value}" ;;
      vision_pid) vision_pid="${value}" ;;
      webapp_pid) webapp_pid="${value}" ;;
      demo_skip_backend) demo_skip_backend="${value}" ;;
      demo_skip_fc) demo_skip_fc="${value}" ;;
      demo_skip_vision) demo_skip_vision="${value}" ;;
      demo_skip_webapp) demo_skip_webapp="${value}" ;;
      backend_url) backend_url="${value}" ;;
      backend_log) backend_log="${value}" ;;
      fc_log) fc_log="${value}" ;;
      vision_log) vision_log="${value}" ;;
      webapp_log) webapp_log="${value}" ;;
    esac
  done < "${PID_FILE}"
}

print_backend_status_summary() {
  if ! command -v curl >/dev/null 2>&1; then
    log "curl not found; skipping /api/status summary"
    return
  fi

  local status_url="${backend_url}/api/status"
  local response
  if response="$(curl -fsS --max-time 1.0 "${status_url}" 2>/dev/null)"; then
    log "Backend /api/status:"
    printf '%s\n' "${response}"
  else
    log "Backend /api/status unavailable at ${status_url}"
  fi
}

main() {
  if [[ ! -f "${PID_FILE}" ]]; then
    log "Demo not running"
    exit 0
  fi

  load_state

  print_proc_status "backend" "${backend_pid}"
  print_proc_status "fc_app" "${fc_pid}"
  print_proc_status "vision" "${vision_pid}"
  print_proc_status "webapp" "${webapp_pid}"
  log "Skip flags: backend=${demo_skip_backend} fc=${demo_skip_fc} vision=${demo_skip_vision} webapp=${demo_skip_webapp}"

  tail_log_if_present "backend" "${backend_log}"
  tail_log_if_present "fc_app" "${fc_log}"
  tail_log_if_present "vision" "${vision_log}"
  tail_log_if_present "webapp" "${webapp_log}"

  print_backend_status_summary
}

main
