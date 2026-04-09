#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR%/dev}"

# Load the same dynamic port selection used by the README startup flow.
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/rock_env.sh"
if [ -f "${SCRIPT_DIR}/.rock_ports.env" ]; then
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/.rock_ports.env"
fi

CURRENT_UID="$(id -u)"
CURRENT_USER="$(id -un)"
WAIT_SECONDS="${WAIT_SECONDS:-2}"

PORT_NAMES=(
  ROCK_RAY_PORT
  ROCK_RAY_DASHBOARD_PORT
  ROCK_RAY_CLIENT_SERVER_PORT
  ROCK_REDIS_PORT
  ROCK_ADMIN_PORT
  ROCK_PROXY_PORT
)

port_value() {
  local name="$1"
  eval "printf '%s' \"\${${name}:-}\""
}

list_pids_for_port() {
  local port="$1"

  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true
    return
  fi

  if command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "${port}" 2>/dev/null | tr ' ' '\n' | sed '/^$/d' || true
    return
  fi

  ss -lntp 2>/dev/null \
    | awk -v target=":${port}" '$4 ~ target {print $NF}' \
    | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' \
    || true
}

owned_listener_pids() {
  local port="$1"
  local pid

  while read -r pid; do
    [ -n "${pid}" ] || continue
    if kill -0 "${pid}" 2>/dev/null; then
      if [ "$(ps -o uid= -p "${pid}" 2>/dev/null | tr -d ' ')" = "${CURRENT_UID}" ]; then
        printf '%s\n' "${pid}"
      fi
    fi
  done < <(list_pids_for_port "${port}")
}

describe_pid() {
  local pid="$1"
  ps -o pid=,ppid=,etime=,cmd= -p "${pid}" 2>/dev/null | sed 's/^ *//'
}

stop_port() {
  local name="$1"
  local port
  port="$(port_value "${name}")"

  if [ -z "${port}" ]; then
    echo "[skip] ${name}: not set"
    return
  fi

  mapfile -t pids < <(owned_listener_pids "${port}")
  if [ "${#pids[@]}" -eq 0 ]; then
    echo "[ok] ${name}=${port}: no listener owned by ${CURRENT_USER}"
    return
  fi

  echo "[kill] ${name}=${port}: ${pids[*]}"
  local pid
  for pid in "${pids[@]}"; do
    echo "  ${pid} $(describe_pid "${pid}")"
    kill -TERM "${pid}" 2>/dev/null || true
  done

  sleep "${WAIT_SECONDS}"

  mapfile -t pids < <(owned_listener_pids "${port}")
  if [ "${#pids[@]}" -gt 0 ]; then
    echo "[force] ${name}=${port}: ${pids[*]}"
    for pid in "${pids[@]}"; do
      kill -KILL "${pid}" 2>/dev/null || true
    done
  fi

  mapfile -t pids < <(owned_listener_pids "${port}")
  if [ "${#pids[@]}" -eq 0 ]; then
    echo "[done] ${name}=${port}: cleared"
  else
    echo "[warn] ${name}=${port}: still occupied by ${pids[*]}"
  fi
}

echo "Cleaning ROCK/OpenClaw ports for user ${CURRENT_USER} in ${PROJECT_ROOT}"
for name in "${PORT_NAMES[@]}"; do
  stop_port "${name}"
done
