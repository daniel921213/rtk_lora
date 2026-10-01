#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLKIT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${TOOLKIT_ROOT}/runtime"

ACTION="${1:-status}"
BASE_IP_ARG="${2:-}"

RECV_SCRIPT="${RECV_SCRIPT:-${TOOLKIT_ROOT}/extras/rtcm_lora_link/receiver.py}"
STR2STR="${STR2STR:-$(command -v str2str || true)}"
STR2STR="${STR2STR:-${HOME}/.local/bin/str2str}"
INIT_SCRIPT="${INIT_SCRIPT:-${TOOLKIT_ROOT}/rover/init_rover_receiver.py}"

LORA_PORT="${LORA_PORT:-/dev/ttyUSB0}"
LORA_BAUD="${LORA_BAUD:-115200}"
GNSS_OUT_PORT="${GNSS_OUT_PORT:-/dev/ttyUSB1}"
GNSS_OUT_BAUD="${GNSS_OUT_BAUD:-115200}"

BASE_IP="${BASE_IP:-100.125.163.60}"
TCP_PORT="${TCP_PORT:-2101}"
AUTO_INIT_ROVER="${AUTO_INIT_ROVER:-1}"
INIT_PORT="${INIT_PORT:-${GNSS_OUT_PORT}}"
INIT_BAUD="${INIT_BAUD:-115200}"

PID_FILE="${PID_FILE:-${RUNTIME_DIR}/rover_source.pid}"
STATE_FILE="${STATE_FILE:-${RUNTIME_DIR}/rover_source.state.json}"
LOG_LORA="${LOG_LORA:-${RUNTIME_DIR}/rover_source_lora.log}"
LOG_TCP="${LOG_TCP:-${RUNTIME_DIR}/rover_source_tcp.log}"

usage() {
  cat <<USAGE
Usage:
  $(basename "$0") lora
  $(basename "$0") tcp [BASE_IP]
  $(basename "$0") stop
  $(basename "$0") status

Environment overrides:
  LORA_PORT=${LORA_PORT}
  LORA_BAUD=${LORA_BAUD}
  GNSS_OUT_PORT=${GNSS_OUT_PORT}
  GNSS_OUT_BAUD=${GNSS_OUT_BAUD}
  BASE_IP=${BASE_IP}
  TCP_PORT=${TCP_PORT}
  AUTO_INIT_ROVER=${AUTO_INIT_ROVER}   # 1=auto init before lora/tcp switch, 0=disable
  INIT_PORT=${INIT_PORT}
  INIT_BAUD=${INIT_BAUD}
USAGE
}

serial_name() {
  local p="$1"
  p="${p##*/}"
  printf '%s' "$p"
}

is_running_pid() {
  local pid="$1"
  [[ -n "$pid" ]] || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1
}

write_state() {
  local source="$1"
  local status="$2"
  local pid="$3"
  local log_file="$4"
  local cmd="$5"

  mkdir -p "${RUNTIME_DIR}"
  python3 - "$STATE_FILE" "$source" "$status" "$pid" "$log_file" "$cmd" <<'PY'
import json
import sys
import time

path, source, status, pid, log_file, cmd = sys.argv[1:]
try:
    pid_v = int(pid)
except Exception:
    pid_v = None
payload = {
    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    "source": source,
    "status": status,
    "pid": pid_v,
    "log_file": log_file,
    "cmd": cmd,
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
PY
}

stop_managed() {
  local pid=""
  if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  fi

  if is_running_pid "$pid"; then
    kill -INT "$pid" >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if ! is_running_pid "$pid"; then
        break
      fi
      sleep 0.2
    done
    if is_running_pid "$pid"; then
      kill -TERM "$pid" >/dev/null 2>&1 || true
    fi
  fi

  rm -f "$PID_FILE"
}

cleanup_conflicts() {
  pkill -f "str2str.*serial://$(serial_name "$LORA_PORT"):${LORA_BAUD}:8:n:1.*serial://$(serial_name "$GNSS_OUT_PORT"):${GNSS_OUT_BAUD}:8:n:1" >/dev/null 2>&1 || true
  pkill -f "${RECV_SCRIPT}.*--out-serial ${GNSS_OUT_PORT}" >/dev/null 2>&1 || true
  pkill -f "str2str.*tcpcli://.*:${TCP_PORT}#rtcm3.*serial://$(serial_name "$GNSS_OUT_PORT"):${GNSS_OUT_BAUD}:8:n:1#rtcm3" >/dev/null 2>&1 || true
}

maybe_init_receiver() {
  if [[ "${AUTO_INIT_ROVER}" != "1" ]]; then
    echo "[rover-source] skip receiver init (AUTO_INIT_ROVER=${AUTO_INIT_ROVER})"
    return
  fi
  if [[ ! -f "${INIT_SCRIPT}" ]]; then
    echo "[rover-source] init script not found: ${INIT_SCRIPT}" >&2
    exit 2
  fi
  echo "[rover-source] init receiver ${INIT_PORT}@${INIT_BAUD}"
  /usr/bin/python3 "${INIT_SCRIPT}" --port "${INIT_PORT}" --baud "${INIT_BAUD}"
}

start_lora() {
  mkdir -p "${RUNTIME_DIR}"
  stop_managed
  cleanup_conflicts
  maybe_init_receiver

  # No format suffix: forward the received bytes unchanged to GNSS.
  local cmd=(
    "$STR2STR"
    -in "serial://$(serial_name "$LORA_PORT"):${LORA_BAUD}:8:n:1"
    -out "serial://$(serial_name "$GNSS_OUT_PORT"):${GNSS_OUT_BAUD}:8:n:1"
  )

  "${cmd[@]}" >>"$LOG_LORA" 2>&1 &
  local pid=$!
  echo "$pid" > "$PID_FILE"
  sleep 0.5

  if ! is_running_pid "$pid"; then
    echo "[rover-source] failed to start LoRa source, see $LOG_LORA" >&2
    write_state "lora" "failed" "0" "$LOG_LORA" "${cmd[*]}"
    exit 1
  fi

  write_state "lora" "running" "$pid" "$LOG_LORA" "${cmd[*]}"
  echo "[rover-source] running source=lora pid=$pid"
  echo "[rover-source] lora=${LORA_PORT}@${LORA_BAUD} -> gnss=${GNSS_OUT_PORT}@${GNSS_OUT_BAUD}"
  echo "[rover-source] log=$LOG_LORA"
}

start_tcp() {
  mkdir -p "${RUNTIME_DIR}"
  stop_managed
  cleanup_conflicts
  maybe_init_receiver

  local base_ip="$BASE_IP"
  if [[ -n "$BASE_IP_ARG" ]]; then
    base_ip="$BASE_IP_ARG"
  fi

  local gnss_name
  gnss_name="$(serial_name "$GNSS_OUT_PORT")"

  local cmd=(
    "$STR2STR"
    -in "tcpcli://${base_ip}:${TCP_PORT}#rtcm3"
    -out "serial://${gnss_name}:${GNSS_OUT_BAUD}:8:n:1#rtcm3"
  )

  "${cmd[@]}" >>"$LOG_TCP" 2>&1 &
  local pid=$!
  echo "$pid" > "$PID_FILE"
  sleep 0.5

  if ! is_running_pid "$pid"; then
    echo "[rover-source] failed to start TCP source, see $LOG_TCP" >&2
    write_state "tcp" "failed" "0" "$LOG_TCP" "${cmd[*]}"
    exit 1
  fi

  write_state "tcp" "running" "$pid" "$LOG_TCP" "${cmd[*]}"
  echo "[rover-source] running source=tcp pid=$pid"
  echo "[rover-source] tcp=tcpcli://${base_ip}:${TCP_PORT} -> gnss=${GNSS_OUT_PORT}@${GNSS_OUT_BAUD}"
  echo "[rover-source] log=$LOG_TCP"
}

show_status() {
  if [[ -f "$STATE_FILE" ]]; then
    cat "$STATE_FILE"
  else
    echo "[rover-source] no state file: $STATE_FILE"
  fi

  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if is_running_pid "$pid"; then
      echo "[rover-source] process alive pid=$pid"
      ps -p "$pid" -o pid=,etime=,cmd=
      return 0
    fi
    echo "[rover-source] pid file exists but process not alive"
    return 1
  fi

  echo "[rover-source] not running"
}

case "$ACTION" in
  lora)
    start_lora
    ;;
  tcp)
    start_tcp
    ;;
  stop)
    stop_managed
    write_state "none" "stopped" "0" "" ""
    echo "[rover-source] stopped"
    ;;
  status)
    show_status
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "[rover-source] unknown action: $ACTION" >&2
    usage
    exit 2
    ;;
esac
