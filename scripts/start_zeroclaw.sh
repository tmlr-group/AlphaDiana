#!/usr/bin/env bash
# start_zeroclaw.sh — start Redis + Ray + ROCK admin/proxy for ZeroClaw auto-deploy runs
#
# Usage:
#   bash scripts/start_zeroclaw.sh
#   OPENAI_BASE_URL=... OPENAI_API_KEY=... OPENAI_MODEL_NAME=... bash scripts/start_zeroclaw.sh
#
# This script prepares the host-side ROCK control plane used by the standard
# ZeroClaw reasoning path (for example configs/examples/zeroclaw_aime2026.yaml).
# Unlike start_openclaw.sh, it does not pre-deploy a long-lived gateway sandbox:
# ZeroClaw sandboxes are auto-created per run by AlphaDiana itself.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROCK_ROOT="${PROJECT_ROOT}/ref/ROCK"
PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"
ZEROCLAW_IMAGE="${ZEROCLAW_SANDBOX_IMAGE:-zeroclaw-reasoning:0.6.9}"
ZEROCLAW_CONFIG="${ZEROCLAW_RUN_CONFIG:-${PROJECT_ROOT}/configs/examples/zeroclaw_aime2026.yaml}"
ROCK_BIND_HOST="${ROCK_BIND_HOST:-127.0.0.1}"
ROCK_REDIS_HOST="${ROCK_REDIS_HOST:-${ROCK_BIND_HOST}}"
ROCK_HTTP_HOST="${ROCK_HTTP_HOST:-${ROCK_BIND_HOST}}"
ROCK_PORT_PROBE_HOST="${ROCK_PORT_PROBE_HOST:-${ROCK_BIND_HOST}}"
LOG_DIR="${PROJECT_ROOT}/.cache/logs"
TMP_DIR="${PROJECT_ROOT}/.cache/tmp"
ALPHADIANA_ENV_NAME="${ALPHADIANA_ENV_NAME:-alphadiana}"

if [ ! -d "${ROCK_ROOT}" ]; then
    echo "ERROR: ROCK repository not found at ${ROCK_ROOT}"
    echo "       Run: bash scripts/quickstart.sh"
    exit 1
fi

if ! "${PYTHON}" -c "import rock" >/dev/null 2>&1; then
    if command -v conda >/dev/null 2>&1; then
        _conda_hook="$(conda shell.bash hook 2>/dev/null || true)"
        if [ -n "${_conda_hook}" ]; then
            eval "${_conda_hook}"
            conda activate "${ALPHADIANA_ENV_NAME}" >/dev/null 2>&1 || true
            PYTHON="$(command -v python3 || command -v python)"
        fi
    fi
fi

if ! "${PYTHON}" -c "import rock" >/dev/null 2>&1; then
    echo "ERROR: Python cannot import 'rock' in the current environment."
    echo "       Activate the correct env first, for example:"
    echo "         source scripts/activate.sh"
    exit 1
fi

source "${SCRIPT_DIR}/rock_env.sh"

# Recompute derived URLs from the active dynamic ports.
export ROCK_BASE_URL="http://${ROCK_HTTP_HOST}:${ROCK_ADMIN_PORT}"
export ROCK_PROXY_ROOT_URL="http://${ROCK_HTTP_HOST}:${ROCK_PROXY_PORT}"
export ROCK_PROXY_URL="${ROCK_PROXY_ROOT_URL}/apis/envs/sandbox/v1"

mkdir -p "${LOG_DIR}" "${TMP_DIR}"

_port_open() {
    "${PYTHON}" -c "
import socket, sys
s = socket.socket()
s.settimeout(1)
ok = s.connect_ex(('${ROCK_PORT_PROBE_HOST}', int('${1}'))) == 0
s.close()
raise SystemExit(0 if ok else 1)
" 2>/dev/null
}

_wait_for_port() {
    local port="$1"
    local timeout="$2"
    local deadline=$(( $(date +%s) + timeout ))
    while ! _port_open "${port}"; do
        if [ "$(date +%s)" -ge "${deadline}" ]; then
            return 1
        fi
        sleep 1
    done
}

_wait_for_route() {
    local url="$1"
    local pattern="$2"
    local timeout="$3"
    local deadline=$(( $(date +%s) + timeout ))
    while true; do
        if curl -fsS "${url}" 2>/dev/null | grep -q "${pattern}"; then
            return 0
        fi
        if [ "$(date +%s)" -ge "${deadline}" ]; then
            return 1
        fi
        sleep 2
    done
}

echo "[1/6] Environment loaded"
echo "      ROCK_ADMIN_PORT = ${ROCK_ADMIN_PORT}"
echo "      ROCK_PROXY_PORT = ${ROCK_PROXY_PORT}"
echo "      ROCK_RAY_PORT   = ${ROCK_RAY_PORT}"
echo "      ROCK_REDIS_PORT = ${ROCK_REDIS_PORT}"

echo "[2/6] Ensuring Redis is available..."
_redis_port=$("${PYTHON}" - <<PYEOF 2>/dev/null || echo "${ROCK_REDIS_PORT}"
import yaml
with open("${ROCK_CONFIG}") as f:
    cfg = yaml.safe_load(f)
print(cfg.get("redis", {}).get("port", ${ROCK_REDIS_PORT}))
PYEOF
)
if ! _port_open "${_redis_port}"; then
    echo "      Redis not reachable on port ${_redis_port}; recreating ${ROCK_REDIS_CONTAINER}"
    docker rm -f "${ROCK_REDIS_CONTAINER}" >/dev/null 2>&1 || true
    docker run -d --restart unless-stopped \
        --name "${ROCK_REDIS_CONTAINER}" \
        -p "${_redis_port}:6379" \
        redis/redis-stack-server:latest >/dev/null
fi
if ! _wait_for_port "${_redis_port}" 15; then
    echo "ERROR: Redis did not become reachable on port ${_redis_port}."
    exit 1
fi
echo "      Redis OK on ${ROCK_REDIS_HOST}:${_redis_port}"

echo "[3/6] Cleaning stale ROCK listeners..."
for _port in "${ROCK_ADMIN_PORT}" "${ROCK_PROXY_PORT}"; do
    _pids=$(lsof -ti ":${_port}" 2>/dev/null || true)
    if [ -n "${_pids}" ]; then
        echo "      Killing PIDs on port ${_port}: ${_pids}"
        kill ${_pids} 2>/dev/null || true
        sleep 1
    fi
done
_old_containers=$(docker ps -q --filter "ancestor=${ZEROCLAW_IMAGE}" 2>/dev/null || true)
if [ -n "${_old_containers}" ]; then
    echo "      Stopping leftover ZeroClaw sandbox container(s)..."
    docker stop ${_old_containers} >/dev/null 2>&1 || true
fi

echo "[4/6] Starting or reusing Ray head node..."
RAY_SESSION_DIR="${RAY_TMPDIR}/rock"
mkdir -p "${RAY_SESSION_DIR}"
if _port_open "${ROCK_RAY_PORT}"; then
    echo "      Ray GCS already listening on port ${ROCK_RAY_PORT} — reusing it"
else
    _ray_bin="$(dirname "${PYTHON}")/ray"
    pkill -u "$(id -un)" -f "gcs_server.*${ROCK_RAY_PORT}" 2>/dev/null || true
    sleep 2
    RAY_TMPDIR="${RAY_SESSION_DIR}" "${_ray_bin}" start \
        --head \
        --port="${ROCK_RAY_PORT}" \
        --dashboard-port="${ROCK_RAY_DASHBOARD_PORT}" \
        --ray-client-server-port="${ROCK_RAY_CLIENT_SERVER_PORT}" \
        --temp-dir="${RAY_SESSION_DIR}" \
        --min-worker-port=${ROCK_RAY_MIN_WORKER_PORT:-20000} \
        --max-worker-port=${ROCK_RAY_MAX_WORKER_PORT:-29999} \
        > "${LOG_DIR}/ray-start.log" 2>&1
    if ! _wait_for_port "${ROCK_RAY_PORT}" 30; then
        echo "ERROR: Ray GCS did not open port ${ROCK_RAY_PORT} within 30 s. Check ${LOG_DIR}/ray-start.log"
        exit 1
    fi
fi
RAY_GCS_HOST="${ROCK_RAY_HOST:-${ROCK_BIND_HOST}}"
RAY_GCS_ADDRESS="${RAY_GCS_HOST}:${ROCK_RAY_PORT}"
echo "      Ray is up (GCS ${RAY_GCS_ADDRESS})"

echo "[5/6] Starting ROCK admin and proxy..."
RUNTIME_ROCK_CONFIG="${TMP_DIR}/rock-local-proxy-runtime.yml"
"${PYTHON}" - <<PYEOF
import yaml

with open("${ROCK_CONFIG}") as f:
    cfg = yaml.safe_load(f)

cfg.setdefault("ray", {})["address"] = "${RAY_GCS_ADDRESS}"

with open("${RUNTIME_ROCK_CONFIG}", "w") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)
PYEOF
export ROCK_CONFIG="${RUNTIME_ROCK_CONFIG}"
echo "      ROCK_CONFIG (runtime) = ${ROCK_CONFIG}"

(
    cd "${ROCK_ROOT}"
    nohup "${PYTHON}" -m rock.admin.main \
        --env local-proxy --role admin --port "${ROCK_ADMIN_PORT}" \
        > "${LOG_DIR}/rock-admin.log" 2>&1
) &

if ! _wait_for_route \
    "http://${ROCK_HTTP_HOST}:${ROCK_ADMIN_PORT}/openapi.json" \
    "/apis/envs/sandbox/v1/start_async" \
    90
then
    echo "ERROR: ROCK admin did not expose start_async within 90 s."
    echo "── Last 20 lines of ${LOG_DIR}/rock-admin.log ──"
    tail -20 "${LOG_DIR}/rock-admin.log" 2>/dev/null || echo "(log not found)"
    echo "────────────────────────────────────────────────"
    exit 1
fi
echo "      ROCK admin is up and exposing sandbox lifecycle routes"

(
    cd "${ROCK_ROOT}"
    nohup "${PYTHON}" -m rock.admin.main \
        --env local-proxy --role proxy --port "${ROCK_PROXY_PORT}" \
        > "${LOG_DIR}/rock-proxy.log" 2>&1
) &

if ! _wait_for_route \
    "http://${ROCK_HTTP_HOST}:${ROCK_PROXY_PORT}/openapi.json" \
    "/apis/envs/sandbox/v1/sandboxes" \
    30
then
    echo "ERROR: ROCK proxy did not expose sandbox proxy routes within 30 s."
    echo "── Last 20 lines of ${LOG_DIR}/rock-proxy.log ──"
    tail -20 "${LOG_DIR}/rock-proxy.log" 2>/dev/null || echo "(log not found)"
    echo "────────────────────────────────────────────────"
    exit 1
fi
echo "      ROCK proxy is up and exposing sandbox proxy routes"

echo "[6/6] Validating ZeroClaw run prerequisites..."
if [ ! -f "${ZEROCLAW_CONFIG}" ]; then
    echo "ERROR: ZeroClaw config not found: ${ZEROCLAW_CONFIG}"
    exit 1
fi
if ! docker image inspect "${ZEROCLAW_IMAGE}" >/dev/null 2>&1; then
    echo "ERROR: ZeroClaw sandbox image not found: ${ZEROCLAW_IMAGE}"
    echo "       Build it with:"
    echo "         docker build -f zeroclaw_deploy/Dockerfile -t ${ZEROCLAW_IMAGE} ."
    exit 1
fi
"${PYTHON}" -m alphadiana.cli validate "${ZEROCLAW_CONFIG}"

_missing_env=()
for _key in OPENAI_BASE_URL OPENAI_API_KEY OPENAI_MODEL_NAME; do
    if [ -z "${!_key:-}" ]; then
        _missing_env+=("${_key}")
    fi
done

echo ""
echo "Done. Host ROCK services are ready for ZeroClaw."
echo "  Admin: ${ROCK_BASE_URL}"
echo "  Proxy: ${ROCK_PROXY_URL}"
echo "  Image: ${ZEROCLAW_IMAGE}"
echo "  Logs : ${LOG_DIR}/rock-admin.log  |  ${LOG_DIR}/rock-proxy.log"
echo ""
if [ "${#_missing_env[@]}" -gt 0 ]; then
    echo "Provider env vars not set in this shell: ${_missing_env[*]}"
    echo "Export them before running the benchmark."
    echo ""
fi
echo "Next step:"
echo "  source scripts/activate.sh"
echo "  source scripts/rock_env.sh"
echo "  alphadiana run ${ZEROCLAW_CONFIG}"
