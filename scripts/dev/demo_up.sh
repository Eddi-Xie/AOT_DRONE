#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

RUN_DIR="${ROOT_DIR}/.run"
LOG_DIR="${ROOT_DIR}/logs"
PID_FILE="${RUN_DIR}/demo_pids"

BACKEND_LOG="${LOG_DIR}/demo_backend.log"
FC_LOG="${LOG_DIR}/demo_fc.log"
VISION_LOG="${LOG_DIR}/demo_vision.log"
WEBAPP_LOG="${LOG_DIR}/demo_webapp.log"

DEMO_BACKEND_HOST="${DEMO_BACKEND_HOST:-127.0.0.1}"
DEMO_BACKEND_PORT="${DEMO_BACKEND_PORT:-8000}"
DEMO_WEBAPP_HOST="${DEMO_WEBAPP_HOST:-127.0.0.1}"
DEMO_WEBAPP_PORT="${DEMO_WEBAPP_PORT:-5173}"
DEMO_SKIP_BACKEND="${DEMO_SKIP_BACKEND:-0}"
DEMO_SKIP_FC="${DEMO_SKIP_FC:-0}"
DEMO_SKIP_VISION="${DEMO_SKIP_VISION:-0}"
DEMO_SKIP_WEBAPP="${DEMO_SKIP_WEBAPP:-0}"

export BACKEND_TEL_HOST="${BACKEND_TEL_HOST:-127.0.0.1}"
export BACKEND_TEL_PORT="${BACKEND_TEL_PORT:-9001}"
export BACKEND_VIS_HOST="${BACKEND_VIS_HOST:-127.0.0.1}"
export BACKEND_VIS_PORT="${BACKEND_VIS_PORT:-9003}"
export BACKEND_FC_HOST="${BACKEND_FC_HOST:-127.0.0.1}"
export BACKEND_FC_PORT="${BACKEND_FC_PORT:-9002}"
export BACKEND_CMD_BRIDGE_ENABLED="${BACKEND_CMD_BRIDGE_ENABLED:-1}"
export BACKEND_TEL_INGEST_ENABLED="${BACKEND_TEL_INGEST_ENABLED:-1}"
export BACKEND_VIS_INGEST_ENABLED="${BACKEND_VIS_INGEST_ENABLED:-1}"
export BACKEND_VIDEO_ENABLED="${BACKEND_VIDEO_ENABLED:-1}"

export VISION_BACKEND_HTTP="${VISION_BACKEND_HTTP:-http://${DEMO_BACKEND_HOST}:${DEMO_BACKEND_PORT}}"
export VISION_VIS_UDP_HOST="${VISION_VIS_UDP_HOST:-${BACKEND_VIS_HOST}}"
export VISION_VIS_UDP_PORT="${VISION_VIS_UDP_PORT:-${BACKEND_VIS_PORT}}"

export VITE_BACKEND_HTTP_URL="${VITE_BACKEND_HTTP_URL:-http://${DEMO_BACKEND_HOST}:${DEMO_BACKEND_PORT}}"
export VITE_BACKEND_WS_URL="${VITE_BACKEND_WS_URL:-ws://${DEMO_BACKEND_HOST}:${DEMO_BACKEND_PORT}/ws}"
export VITE_VIDEO_URL="${VITE_VIDEO_URL:-/video}"
export VITE_OVERLAY_SOURCE="${VITE_OVERLAY_SOURCE:-VIS}"

DEMO_VISION_ARGS="${DEMO_VISION_ARGS:---source webcam:0 --mode detect --model-path yolov8n.pt --target-class 0 --preview --conf-threshold 0.25 --detect-hold-n 5 --infer-size 640}"

BACKEND_URL="http://${DEMO_BACKEND_HOST}:${DEMO_BACKEND_PORT}"
STATUS_URL="${BACKEND_URL}/api/status"

backend_pid=""
fc_pid=""
vision_pid=""
webapp_pid=""
startup_success=0

log() {
  printf '[demo_up] %s\n' "$*"
}

err() {
  printf '[demo_up] ERROR: %s\n' "$*" >&2
}

is_skip_enabled() {
  [[ "$1" == "1" ]]
}

is_running_pid() {
  local pid="$1"
  [[ -n "${pid}" ]] && [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

stop_pid_quiet() {
  local name="$1"
  local pid="$2"
  if ! is_running_pid "${pid}"; then
    return 0
  fi

  log "Stopping ${name} (pid=${pid})"
  kill "${pid}" 2>/dev/null || true
  local i
  for i in $(seq 1 25); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      return 0
    fi
    sleep 0.2
  done

  if kill -0 "${pid}" 2>/dev/null; then
    log "Force-killing ${name} (pid=${pid})"
    kill -9 "${pid}" 2>/dev/null || true
  fi
}

cleanup_started() {
  stop_pid_quiet "webapp" "${webapp_pid}"
  stop_pid_quiet "vision" "${vision_pid}"
  stop_pid_quiet "fc_app" "${fc_pid}"
  stop_pid_quiet "backend" "${backend_pid}"
  rm -f "${PID_FILE}"
}

on_exit() {
  if [[ "${startup_success}" -ne 1 ]]; then
    if [[ -n "${backend_pid}${fc_pid}${vision_pid}${webapp_pid}" ]]; then
      err "Startup failed; cleaning up any started demo processes"
    fi
    cleanup_started
  fi
}
trap on_exit EXIT

validate_skip_flags() {
  local key value
  for key in DEMO_SKIP_BACKEND DEMO_SKIP_FC DEMO_SKIP_VISION DEMO_SKIP_WEBAPP; do
    value="${!key}"
    if [[ "${value}" != "0" && "${value}" != "1" ]]; then
      err "${key} must be 0 or 1 (got '${value}')"
      exit 1
    fi
  done

  if is_skip_enabled "${DEMO_SKIP_BACKEND}" \
    && is_skip_enabled "${DEMO_SKIP_FC}" \
    && is_skip_enabled "${DEMO_SKIP_VISION}" \
    && is_skip_enabled "${DEMO_SKIP_WEBAPP}"; then
    err "All components are skipped. Set at least one DEMO_SKIP_* flag to 0."
    exit 1
  fi
}

require_venv() {
  if [[ ! -f "${ROOT_DIR}/.venv/bin/activate" ]]; then
    err "Missing .venv. Create it first: python -m venv .venv"
    err "Then install deps: python -m pip install -r requirements-dev.txt -r src/backend/requirements.txt -r src/vision/requirements.txt"
    exit 1
  fi
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.venv/bin/activate"
}

require_node_modules() {
  if [[ ! -d "${ROOT_DIR}/src/webapp/node_modules" ]]; then
    err "Missing src/webapp/node_modules. Run npm --prefix src/webapp install"
    exit 1
  fi
}

require_fc_binary() {
  if [[ ! -x "${ROOT_DIR}/build/src/fc/fc_app" ]]; then
    err "Missing ./build/src/fc/fc_app. Run cmake -S . -B build && cmake --build build -j"
    exit 1
  fi
}

require_python_modules() {
  local missing
  missing="$(python - <<'PY'
import importlib.util
modules = ["uvicorn", "fastapi", "httpx", "ultralytics", "cv2"]
missing = [name for name in modules if importlib.util.find_spec(name) is None]
print(" ".join(missing))
PY
)"

  if [[ -n "${missing}" ]]; then
    err "Missing Python modules: ${missing}"
    err "Install: python -m pip install -r requirements-dev.txt -r src/backend/requirements.txt -r src/vision/requirements.txt"
    exit 1
  fi
}

check_port_with_python_bind() {
  local proto="$1"
  local host="$2"
  local port="$3"

  python - "${proto}" "${host}" "${port}" <<'PY' >/dev/null 2>&1
import socket
import sys

proto, host, port_raw = sys.argv[1], sys.argv[2], sys.argv[3]
port = int(port_raw)
sock_type = socket.SOCK_STREAM if proto == "tcp" else socket.SOCK_DGRAM
sock = socket.socket(socket.AF_INET, sock_type)
try:
    sock.bind((host, port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

PORT_CONFLICTS=()
check_port_available() {
  local label="$1"
  local proto="$2"
  local port="$3"

  if command -v lsof >/dev/null 2>&1; then
    local output=""
    if [[ "${proto}" == "tcp" ]]; then
      output="$(lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
    else
      output="$(lsof -nP -iUDP:"${port}" 2>/dev/null || true)"
    fi

    if [[ -n "${output}" ]]; then
      local line cmd pid holder
      while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        cmd="$(printf '%s\n' "${line}" | awk '{print $1}')"
        pid="$(printf '%s\n' "${line}" | awk '{print $2}')"
        holder="${cmd}/${pid}"
        if [[ "${pid}" =~ ^[0-9]+$ ]]; then
          PORT_CONFLICTS+=("${label} (${proto}/${port}) by ${holder} (free with: kill ${pid})")
        else
          PORT_CONFLICTS+=("${label} (${proto}/${port}) by ${holder}")
        fi
      done < <(printf '%s\n' "${output}" | tail -n +2)
    fi
    return
  fi

  if ! check_port_with_python_bind "${proto}" "127.0.0.1" "${port}"; then
    PORT_CONFLICTS+=("${label} (${proto}/${port}) is already in use (process unknown; install lsof for process identification)")
  fi
}

require_ports_free() {
  if ! is_skip_enabled "${DEMO_SKIP_BACKEND}"; then
    check_port_available "backend" "tcp" "${DEMO_BACKEND_PORT}"
    if [[ "${BACKEND_TEL_INGEST_ENABLED}" == "1" ]]; then
      check_port_available "TEL ingest" "udp" "${BACKEND_TEL_PORT}"
    fi
    if [[ "${BACKEND_VIS_INGEST_ENABLED}" == "1" ]]; then
      check_port_available "VIS ingest" "udp" "${BACKEND_VIS_PORT}"
    fi
  fi
  if ! is_skip_enabled "${DEMO_SKIP_FC}"; then
    check_port_available "FC command" "tcp" "${BACKEND_FC_PORT}"
  fi
  if ! is_skip_enabled "${DEMO_SKIP_WEBAPP}"; then
    check_port_available "webapp" "tcp" "${DEMO_WEBAPP_PORT}"
  fi

  if [[ "${#PORT_CONFLICTS[@]}" -gt 0 ]]; then
    err "Port preflight failed. The following ports are already in use:"
    local conflict
    for conflict in "${PORT_CONFLICTS[@]}"; do
      err "  - ${conflict}"
    done
    err "Stop conflicting services or adjust env vars before rerunning demo_up.sh"
    exit 1
  fi
}

extract_model_path() {
  python - "${DEMO_VISION_ARGS}" <<'PY'
import shlex
import sys

args = shlex.split(sys.argv[1])
model = "yolov8n.pt"
for idx, token in enumerate(args):
    if token == "--model-path" and idx + 1 < len(args):
        model = args[idx + 1]
    elif token.startswith("--model-path="):
        model = token.split("=", 1)[1]
print(model)
PY
}

require_model_file() {
  local model_path
  model_path="$(extract_model_path)"

  local resolved_path="${model_path}"
  if [[ "${resolved_path}" != /* ]]; then
    resolved_path="${ROOT_DIR}/${resolved_path}"
  fi

  if [[ ! -f "${resolved_path}" ]]; then
    err "Vision model not found at ${model_path}"
    err "Place a model file there (e.g. yolov8n.pt) or override via DEMO_VISION_ARGS, for example:"
    err "  DEMO_VISION_ARGS='--source webcam:0 --mode detect --model-path /abs/path/model.pt ...' ./scripts/dev/demo_up.sh"
    exit 1
  fi
}

check_existing_pid_file() {
  if [[ ! -f "${PID_FILE}" ]]; then
    return
  fi

  local running=()
  while IFS='=' read -r key value; do
    case "${key}" in
      backend_pid|fc_pid|vision_pid|webapp_pid)
        if [[ "${value}" =~ ^[0-9]+$ ]] && kill -0 "${value}" 2>/dev/null; then
          running+=("${key}=${value}")
        fi
        ;;
    esac
  done < "${PID_FILE}"

  if [[ "${#running[@]}" -gt 0 ]]; then
    err "Existing demo pid file indicates running processes:"
    local item
    for item in "${running[@]}"; do
      err "  - ${item}"
    done
    err "Run ./scripts/dev/demo_down.sh before starting again"
    exit 1
  fi

  rm -f "${PID_FILE}"
}

start_cmd() {
  local name="$1"
  local log_file="$2"
  shift 2

  log "Starting ${name}: $*" >&2
  "$@" >"${log_file}" 2>&1 &
  local pid=$!

  sleep 0.2
  if ! kill -0 "${pid}" 2>/dev/null; then
    err "${name} exited immediately"
    tail -n 50 "${log_file}" >&2 || true
    return 1
  fi

  printf '%s\n' "${pid}"
}

wait_for_backend_status() {
  local timeout_s="$1"
  local started_s
  started_s="$(date +%s)"

  while true; do
    if python - "${STATUS_URL}" <<'PY' >/dev/null 2>&1
import json
import sys
from urllib import request

url = sys.argv[1]
with request.urlopen(url, timeout=0.5) as response:
    if response.getcode() != 200:
        raise SystemExit(1)
    payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(1)
PY
    then
      return 0
    fi

    if ! is_running_pid "${backend_pid}"; then
      return 1
    fi

    local now_s
    now_s="$(date +%s)"
    if (( now_s - started_s >= timeout_s )); then
      return 1
    fi

    sleep 0.2
  done
}

read_status_counters() {
  python - "${STATUS_URL}" <<'PY'
import json
import sys
from urllib import request

url = sys.argv[1]
with request.urlopen(url, timeout=0.8) as response:
    payload = json.loads(response.read().decode("utf-8"))
    vis = int(payload.get("vis_rx_ok", 0))
    frames = int(payload.get("frames_rx_ok", 0))
    print(f"{vis} {frames}")
PY
}

wait_for_vision_signal() {
  local baseline_vis="$1"
  local baseline_frames="$2"
  local timeout_s="$3"
  local started_s
  started_s="$(date +%s)"

  while true; do
    if ! is_running_pid "${vision_pid}"; then
      err "Vision process exited while waiting for VIS/frame signal"
      tail -n 50 "${VISION_LOG}" >&2 || true
      return 1
    fi

    local counters
    if counters="$(read_status_counters 2>/dev/null)"; then
      local vis_now frames_now
      vis_now="$(printf '%s' "${counters}" | awk '{print $1}')"
      frames_now="$(printf '%s' "${counters}" | awk '{print $2}')"
      if [[ "${vis_now}" =~ ^[0-9]+$ ]] && [[ "${frames_now}" =~ ^[0-9]+$ ]]; then
        if (( vis_now > baseline_vis || frames_now > baseline_frames )); then
          return 0
        fi
      fi
    fi

    local now_s
    now_s="$(date +%s)"
    if (( now_s - started_s >= timeout_s )); then
      return 1
    fi

    sleep 0.25
  done
}

write_pid_file() {
  cat > "${PID_FILE}" <<EOF_PID
backend_pid=${backend_pid}
fc_pid=${fc_pid}
vision_pid=${vision_pid}
webapp_pid=${webapp_pid}
demo_skip_backend=${DEMO_SKIP_BACKEND}
demo_skip_fc=${DEMO_SKIP_FC}
demo_skip_vision=${DEMO_SKIP_VISION}
demo_skip_webapp=${DEMO_SKIP_WEBAPP}
backend_url=${BACKEND_URL}
webapp_url=http://${DEMO_WEBAPP_HOST}:${DEMO_WEBAPP_PORT}
backend_log=${BACKEND_LOG}
fc_log=${FC_LOG}
vision_log=${VISION_LOG}
webapp_log=${WEBAPP_LOG}
EOF_PID
}

main() {
  validate_skip_flags
  mkdir -p "${RUN_DIR}" "${LOG_DIR}"
  check_existing_pid_file
  require_venv
  if ! is_skip_enabled "${DEMO_SKIP_WEBAPP}"; then
    require_node_modules
  fi
  if ! is_skip_enabled "${DEMO_SKIP_FC}"; then
    require_fc_binary
  fi
  if ! is_skip_enabled "${DEMO_SKIP_BACKEND}" || ! is_skip_enabled "${DEMO_SKIP_VISION}"; then
    require_python_modules
  fi
  if ! is_skip_enabled "${DEMO_SKIP_VISION}"; then
    require_model_file
  fi
  require_ports_free

  if ! is_skip_enabled "${DEMO_SKIP_BACKEND}"; then
    backend_pid="$(start_cmd "backend" "${BACKEND_LOG}" \
      python -m uvicorn src.backend.app:app --host "${DEMO_BACKEND_HOST}" --port "${DEMO_BACKEND_PORT}")"

    if ! wait_for_backend_status 5; then
      err "Backend did not become healthy at ${STATUS_URL} within 5s"
      tail -n 50 "${BACKEND_LOG}" >&2 || true
      exit 1
    fi
  else
    log "Skipping backend startup (DEMO_SKIP_BACKEND=1)"
  fi

  local baseline_vis=0 baseline_frames=0 counters
  if ! is_skip_enabled "${DEMO_SKIP_BACKEND}" && ! is_skip_enabled "${DEMO_SKIP_VISION}"; then
    counters="$(read_status_counters 2>/dev/null || echo '0 0')"
    baseline_vis="$(printf '%s' "${counters}" | awk '{print $1}')"
    baseline_frames="$(printf '%s' "${counters}" | awk '{print $2}')"
  fi

  if ! is_skip_enabled "${DEMO_SKIP_FC}"; then
    fc_pid="$(start_cmd "fc_app" "${FC_LOG}" ./build/src/fc/fc_app)"
    sleep 1
  else
    log "Skipping fc_app startup (DEMO_SKIP_FC=1)"
  fi

  if ! is_skip_enabled "${DEMO_SKIP_VISION}"; then
    local vision_cmd
    vision_cmd="python -m src.vision.main ${DEMO_VISION_ARGS}"
    vision_pid="$(start_cmd "vision" "${VISION_LOG}" bash -lc "${vision_cmd}")"

    if ! is_skip_enabled "${DEMO_SKIP_BACKEND}"; then
      if ! wait_for_vision_signal "${baseline_vis}" "${baseline_frames}" 10; then
        err "Vision did not produce VIS/frame signals within 10s"
        err "Hint: check camera permissions, model path, and DEMO_VISION_ARGS"
        tail -n 50 "${VISION_LOG}" >&2 || true
        exit 1
      fi
    else
      log "Skipping VIS/frame status check because backend is skipped"
    fi
  else
    log "Skipping vision startup (DEMO_SKIP_VISION=1)"
  fi

  if ! is_skip_enabled "${DEMO_SKIP_WEBAPP}"; then
    webapp_pid="$(start_cmd "webapp" "${WEBAPP_LOG}" \
      npm --prefix src/webapp run dev -- --host "${DEMO_WEBAPP_HOST}" --port "${DEMO_WEBAPP_PORT}")"
  else
    log "Skipping webapp startup (DEMO_SKIP_WEBAPP=1)"
  fi

  write_pid_file
  startup_success=1

  log "Demo stack is up"
  if ! is_skip_enabled "${DEMO_SKIP_BACKEND}"; then
    log "Backend URL: ${BACKEND_URL}"
  fi
  if ! is_skip_enabled "${DEMO_SKIP_WEBAPP}"; then
    log "Webapp URL: http://${DEMO_WEBAPP_HOST}:${DEMO_WEBAPP_PORT}"
  fi
  log "Logs:"
  log "  - ${BACKEND_LOG}"
  log "  - ${FC_LOG}"
  log "  - ${VISION_LOG}"
  log "  - ${WEBAPP_LOG}"
  log "PIDs file: ${PID_FILE}"
  log "Stop with: ./scripts/dev/demo_down.sh"
}

main
