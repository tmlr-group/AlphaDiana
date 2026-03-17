#!/usr/bin/env bash
# start_openclaw.sh — start Ray + ROCK services and deploy the OpenClaw prebuilt sandbox
# Usage:
#   ./dev/start_openclaw.sh
#   OPENAI_BASE_URL=... OPENAI_API_KEY=... OPENAI_MODEL_NAME=... ./dev/start_openclaw.sh
#
# The script is idempotent: it kills any stale ROCK processes on the configured
# ports and stops any leftover sandbox containers before starting fresh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR%/dev}"
ROCK_ROOT="${PROJECT_ROOT}/ref/ROCK"
PYTHON="${PYTHON:-/data2/weikai/conda_envs/alphadiana/bin/python}"

# ── 1. Load environment ──────────────────────────────────────────────────────
source "${SCRIPT_DIR}/rock_env.sh"

echo "[1/6] Environment loaded"
echo "      ROCK_ADMIN_PORT = ${ROCK_ADMIN_PORT}"
echo "      ROCK_PROXY_PORT = ${ROCK_PROXY_PORT}"
echo "      RAY_TMPDIR      = ${RAY_TMPDIR}"

# ── 2. Check Redis ───────────────────────────────────────────────────────────
_redis_port=$(python3 -c "
import yaml
with open('${ROCK_CONFIG}') as f:
    cfg = yaml.safe_load(f)
print(cfg.get('redis', {}).get('port', 6379))
" 2>/dev/null || echo "${ROCK_REDIS_PORT}")

echo "[2/6] Checking Redis on port ${_redis_port}..."
if ! redis-cli -p "${_redis_port}" ping >/dev/null 2>&1; then
    echo "ERROR: Redis is not responding on port ${_redis_port}."
    echo "       Start it with: docker run -d --name redis-weikai -p ${_redis_port}:6379 redis/redis-stack-server:latest"
    exit 1
fi
echo "      Redis OK (PONG)"

# ── 3. Kill stale ROCK processes and sandbox containers ──────────────────────
echo "[3/6] Cleaning up stale processes..."
for _port in "${ROCK_ADMIN_PORT}" "${ROCK_PROXY_PORT}"; do
    _pids=$(lsof -ti ":${_port}" 2>/dev/null || true)
    if [ -n "${_pids}" ]; then
        echo "      Killing PIDs on port ${_port}: ${_pids}"
        kill ${_pids} 2>/dev/null || true
        sleep 1
    fi
done
_old_containers=$(docker ps -q --filter "ancestor=ghcr.io/tsrigo/openclaw-sandbox:2026.3.7" 2>/dev/null || true)
if [ -n "${_old_containers}" ]; then
    echo "      Stopping leftover sandbox container(s)..."
    docker stop ${_old_containers} >/dev/null 2>&1 || true
fi

# ── 4. Start Ray ─────────────────────────────────────────────────────────────
# We use a dedicated session dir so we can point ROCK to our exact Ray cluster.
RAY_SESSION_DIR="${RAY_TMPDIR}/rock"
mkdir -p "${RAY_SESSION_DIR}"

LOG_DIR="${PROJECT_ROOT}/.cache/logs"
mkdir -p "${LOG_DIR}"

echo "[4/6] Starting Ray head node (GCS port ${ROCK_RAY_PORT})..."

_port_open() {
    python3 -c "
import socket, sys
s = socket.socket(); s.settimeout(1)
sys.exit(s.connect_ex(('127.0.0.1', $1))); s.close()
" 2>/dev/null
}

if _port_open "${ROCK_RAY_PORT}"; then
    echo "      Ray GCS already listening on port ${ROCK_RAY_PORT} — reusing it"
else
    # Kill leftover processes that might hold the port
    pkill -u "$(id -un)" -f "gcs_server.*${ROCK_RAY_PORT}" 2>/dev/null || true
    sleep 2

    RAY_TMPDIR="${RAY_SESSION_DIR}" /data2/weikai/conda_envs/alphadiana/bin/ray start \
        --head \
        --port="${ROCK_RAY_PORT}" \
        --dashboard-port="${ROCK_RAY_DASHBOARD_PORT}" \
        --temp-dir="${RAY_SESSION_DIR}" \
        --min-worker-port=20000 \
        --max-worker-port=29999 \
        > "${LOG_DIR}/ray-start.log" 2>&1

    # Wait for GCS port to be open
    _deadline=$(( $(date +%s) + 30 ))
    while ! _port_open "${ROCK_RAY_PORT}"; do
        if [ "$(date +%s)" -ge "${_deadline}" ]; then
            echo "ERROR: Ray GCS did not open port ${ROCK_RAY_PORT} within 30 s. Check ${LOG_DIR}/ray-start.log"
            exit 1
        fi
        sleep 1
    done
fi
RAY_GCS_ADDRESS="192.0.2.13:${ROCK_RAY_PORT}"
echo "      Ray is up (GCS ${RAY_GCS_ADDRESS})"

# ── 5. Start ROCK admin and proxy ────────────────────────────────────────────
# Build a runtime ROCK config with the explicit Ray GCS address so ROCK admin
# connects to OUR Ray cluster and not another user's on this shared machine.
RUNTIME_ROCK_CONFIG="${PROJECT_ROOT}/.cache/tmp/rock-local-proxy-runtime.yml"
mkdir -p "$(dirname "${RUNTIME_ROCK_CONFIG}")"
python3 - <<PYEOF
import yaml

with open("${ROCK_CONFIG}") as f:
    cfg = yaml.safe_load(f)

cfg.setdefault("ray", {})["address"] = "${RAY_GCS_ADDRESS}"

with open("${RUNTIME_ROCK_CONFIG}", "w") as f:
    yaml.dump(cfg, f)
PYEOF
export ROCK_CONFIG="${RUNTIME_ROCK_CONFIG}"
echo "      ROCK_CONFIG (runtime) = ${ROCK_CONFIG}"
echo "      Ray address in config = ${RAY_GCS_ADDRESS}"

echo "[5/6] Starting ROCK admin (port ${ROCK_ADMIN_PORT})..."
(
    cd "${ROCK_ROOT}"
    nohup "${PYTHON}" -m rock.admin.main \
        --env local-proxy --role admin --port "${ROCK_ADMIN_PORT}" \
        > "${LOG_DIR}/rock-admin.log" 2>&1
) &

# Wait for admin (up to 90 s — first start may need to install Ray runtime env)
echo "      Waiting for ROCK admin to become ready..."
_deadline=$(( $(date +%s) + 90 ))
while true; do
    if curl -sf "http://127.0.0.1:${ROCK_ADMIN_PORT}/" >/dev/null 2>&1; then
        echo "      ROCK admin is up"
        break
    fi
    if [ "$(date +%s)" -ge "${_deadline}" ]; then
        echo "ERROR: ROCK admin did not start within 90 s. Check ${LOG_DIR}/rock-admin.log"
        exit 1
    fi
    sleep 2
done

echo "      Starting ROCK proxy (port ${ROCK_PROXY_PORT})..."
(
    cd "${ROCK_ROOT}"
    nohup "${PYTHON}" -m rock.admin.main \
        --env local-proxy --role proxy --port "${ROCK_PROXY_PORT}" \
        > "${LOG_DIR}/rock-proxy.log" 2>&1
) &

echo "      Waiting for ROCK proxy to become ready..."
_deadline=$(( $(date +%s) + 30 ))
while true; do
    if curl -sf "http://127.0.0.1:${ROCK_PROXY_PORT}/" >/dev/null 2>&1; then
        echo "      ROCK proxy is up"
        break
    fi
    if [ "$(date +%s)" -ge "${_deadline}" ]; then
        echo "ERROR: ROCK proxy did not start within 30 s. Check ${LOG_DIR}/rock-proxy.log"
        exit 1
    fi
    sleep 2
done

# ── 6. Deploy OpenClaw prebuilt image ────────────────────────────────────────
echo "[6/6] Deploying OpenClaw prebuilt sandbox..."
echo "      Image : ghcr.io/tsrigo/openclaw-sandbox:2026.3.7"
echo "      Admin : ${ROCK_BASE_URL}"
echo "      Proxy : ${ROCK_PROXY_URL}"
echo ""

"${PYTHON}" -m openclaw_deploy.deploy \
    --image ghcr.io/tsrigo/openclaw-sandbox:2026.3.7 \
    --startup-timeout 300

echo ""
echo "Done. OpenClaw is ready."
echo "Logs: ${LOG_DIR}/rock-admin.log  |  ${LOG_DIR}/rock-proxy.log"
