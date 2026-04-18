#!/usr/bin/env bash
# start_openclaw.sh — start Ray + ROCK services and deploy the default OpenClaw reasoning sandbox
# Usage:
#   ./dev/start_openclaw.sh [deploy.py options]
#   OPENAI_BASE_URL=... OPENAI_API_KEY=... OPENAI_MODEL_NAME=... ./dev/start_openclaw.sh
#
# Any unrecognised options are forwarded verbatim to openclaw_deploy.deploy.
# Common examples:
#   --model-base-url URL  --model-api-key KEY  --model-name NAME
#   --memory 8g  --cpus 2  --auto-clear-seconds 28800
#
# The script is idempotent: it kills any stale ROCK processes on the configured
# ports and stops any leftover sandbox containers before starting fresh.

set -euo pipefail

DEFAULT_IMAGE="${OPENCLAW_SANDBOX_IMAGE:-tmlrgroup/alphadiana:v1}"

# Collect all CLI arguments to forward to deploy.py
DEPLOY_EXTRA_ARGS=("$@")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR%/dev}"
ROCK_ROOT="${PROJECT_ROOT}/ref/ROCK"
PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"
ROCK_BIND_HOST="${ROCK_BIND_HOST:-127.0.0.1}"
ROCK_REDIS_HOST="${ROCK_REDIS_HOST:-${ROCK_BIND_HOST}}"
ROCK_HTTP_HOST="${ROCK_HTTP_HOST:-${ROCK_BIND_HOST}}"
ROCK_PORT_PROBE_HOST="${ROCK_PORT_PROBE_HOST:-${ROCK_BIND_HOST}}"

# ── 0. Preflight: verify rock editable install points to THIS project ────────
# rock is installed via `pip install -e .` and its path is hardcoded in the
# conda env. If you switch between project directories without re-running
# `pip install -e .`, the container's rocklet will fail with:
#   ModuleNotFoundError: No module named 'rock'
_rock_installed=$("${PYTHON}" -c "import rock; print(rock.__file__)" 2>/dev/null || echo "")
_rock_expected="${PROJECT_ROOT}/ref/ROCK/rock/__init__.py"
if [ "${_rock_installed}" != "${_rock_expected}" ]; then
    echo "ERROR: 'rock' editable install path does not match this project."
    echo "  Currently: ${_rock_installed:-(not installed)}"
    echo "  Expected:  ${_rock_expected}"
    echo ""
    echo "  Fix: re-install rock from this project, then re-run this script:"
    echo "    ${PYTHON%/python}/pip install -e ${PROJECT_ROOT}/ref/ROCK -q"
    echo ""
    echo "  Tip: when switching between project directories, re-run the above each time."
    exit 1
fi

# ── 1. Load environment ──────────────────────────────────────────────────────
source "${SCRIPT_DIR}/rock_env.sh"

# ── 1.5. Security preflight check ───────────────────────────────────────────
echo "[0/6] Running security preflight check..."
if ! "${PYTHON}" "${SCRIPT_DIR}/security_guard.py" --check; then
    echo ""
    echo "ERROR: Security preflight check failed. Start aborted."
    echo "       Fix the issues above, or set SECURITY_GUARD_BYPASS=1 to override."
    exit 1
fi

# Recompute derived ROCK URLs from the active dynamic ports in this shell.
# This prevents stale ROCK_BASE_URL/ROCK_PROXY_URL values from older sessions
# from pointing deploy.py to the wrong admin/proxy ports.
export ROCK_BASE_URL="http://${ROCK_HTTP_HOST}:${ROCK_ADMIN_PORT}"
export ROCK_PROXY_ROOT_URL="http://${ROCK_HTTP_HOST}:${ROCK_PROXY_PORT}"
export ROCK_PROXY_URL="${ROCK_PROXY_ROOT_URL}/apis/envs/sandbox/v1"

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
if command -v redis-cli >/dev/null 2>&1; then
    if ! redis-cli -p "${_redis_port}" ping >/dev/null 2>&1; then
        echo "ERROR: Redis is not responding on port ${_redis_port}."
        echo "       Start it with: docker run -d --name ${ROCK_REDIS_CONTAINER} -p ${_redis_port}:6379 redis/redis-stack-server:latest"
        exit 1
    fi
else
    if ! python3 - <<PYEOF >/dev/null 2>&1
import socket
sock = socket.socket()
sock.settimeout(2)
ok = sock.connect_ex(("${ROCK_REDIS_HOST}", int("${_redis_port}"))) == 0
sock.close()
raise SystemExit(0 if ok else 1)
PYEOF
    then
        echo "ERROR: Redis TCP port ${_redis_port} is not reachable."
        echo "       Start it with: docker run -d --name ${ROCK_REDIS_CONTAINER} -p ${_redis_port}:6379 redis/redis-stack-server:latest"
        exit 1
    fi
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
_old_containers=$(docker ps -q --filter "ancestor=${DEFAULT_IMAGE}" 2>/dev/null || true)
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
sys.exit(s.connect_ex(('${ROCK_PORT_PROBE_HOST}', $1))); s.close()
" 2>/dev/null
}

if _port_open "${ROCK_RAY_PORT}"; then
    # Verify the existing cluster was started with the same Python version.
    # ray.init itself raises RuntimeError with "Version mismatch" when they differ —
    # we catch that specific error to extract the cluster's Python version.
    _version_check=$("${PYTHON}" -c "
import ray, sys, re, os, logging
os.environ.setdefault('RAY_LOG_TO_STDERR', '0')
logging.disable(logging.CRITICAL)
try:
    ray.init(address='${ROCK_RAY_HOST:-${ROCK_BIND_HOST}}:${ROCK_RAY_PORT}', ignore_reinit_error=True, logging_level=50)
    try: ray.shutdown()
    except: pass
    print('ok')
except RuntimeError as e:
    msg = str(e)
    if 'Version mismatch' in msg:
        m = re.search(r'Python: (\S+)', msg)
        print('mismatch:' + (m.group(1) if m else 'unknown'))
    else:
        print('ok')
except Exception:
    print('ok')
" 2>/dev/null)

    if [[ "${_version_check}" == mismatch:* ]]; then
        _cluster_py="${_version_check#mismatch:}"
        _our_py=$("${PYTHON}" -c "import sys; print(sys.version.split()[0])")
        _ray_bin="$(dirname "${PYTHON}")/ray"
        # Check if this is our own Ray process (can we kill it?)
        _ray_pid=$(lsof -ti ":${ROCK_RAY_PORT}" 2>/dev/null | head -1)
        _ray_owner=$(ps -o user= -p "${_ray_pid}" 2>/dev/null || echo "unknown")
        _our_user="$(id -un)"
        echo ""
        echo "  ERROR: Ray cluster Python version mismatch — cannot start ROCK admin."
        echo "         Port ${ROCK_RAY_PORT}: cluster Python=${_cluster_py}, this script Python=${_our_py}"
        echo ""
        if [ "${_ray_owner}" = "${_our_user}" ]; then
            echo "  The Ray cluster on port ${ROCK_RAY_PORT} belongs to YOU."
            echo "  Stop it and re-run this script:"
            echo ""
            echo "    ${_ray_bin} stop --force && ./dev/start_openclaw.sh $*"
        else
            echo "  The Ray cluster on port ${ROCK_RAY_PORT} belongs to user '${_ray_owner}' — do NOT kill it."
            echo "  Allocate fresh ports for yourself instead:"
            echo ""
            echo "    ${PYTHON} ${SCRIPT_DIR}/find_rock_ports.py --write-env ${SCRIPT_DIR}/.rock_ports.env"
            echo "    ./dev/start_openclaw.sh $*"
        fi
        echo ""
        exit 1
    else
        echo "      Ray GCS already listening on port ${ROCK_RAY_PORT} — reusing it"
    fi
fi
if ! _port_open "${ROCK_RAY_PORT}"; then
    # Kill leftover processes that might hold the port
    pkill -u "$(id -un)" -f "gcs_server.*${ROCK_RAY_PORT}" 2>/dev/null || true
    sleep 2

    _ray_bin="$(dirname "${PYTHON}")/ray"
    RAY_TMPDIR="${RAY_SESSION_DIR}" "${_ray_bin}" start \
        --head \
        --port="${ROCK_RAY_PORT}" \
        --dashboard-port="${ROCK_RAY_DASHBOARD_PORT}" \
        --temp-dir="${RAY_SESSION_DIR}" \
        --min-worker-port=${ROCK_RAY_MIN_WORKER_PORT:-20000} \
        --max-worker-port=${ROCK_RAY_MAX_WORKER_PORT:-29999} \
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
RAY_GCS_HOST="${ROCK_RAY_HOST:-${ROCK_BIND_HOST}}"
RAY_GCS_ADDRESS="${RAY_GCS_HOST}:${ROCK_RAY_PORT}"
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
    if curl -sf "http://${ROCK_HTTP_HOST}:${ROCK_ADMIN_PORT}/" >/dev/null 2>&1; then
        echo "      ROCK admin is up"
        break
    fi
    if [ "$(date +%s)" -ge "${_deadline}" ]; then
        echo "ERROR: ROCK admin did not start within 90 s."
        echo ""
        echo "── Last 20 lines of ${LOG_DIR}/rock-admin.log ──"
        tail -20 "${LOG_DIR}/rock-admin.log" 2>/dev/null || echo "(log not found)"
        echo "────────────────────────────────────────────────"
        # Detect common failure causes and give targeted hints
        if grep -q "Version mismatch" "${LOG_DIR}/rock-admin.log" 2>/dev/null; then
            _cluster_ver=$(grep -oP "Ray: \K[^\n]+" "${LOG_DIR}/rock-admin.log" | head -1)
            _cluster_pyv=$(grep -oP "Python: \K[^\n]+" "${LOG_DIR}/rock-admin.log" | head -1)
            _our_pyv=$("${PYTHON}" -c "import sys; print(sys.version.split()[0])")
            echo ""
            echo "  HINT: Ray cluster Python version mismatch."
            echo "        Cluster started with Python ${_cluster_pyv:-?}, this script uses Python ${_our_pyv}."
            echo "        Fix: stop the stale cluster and re-run this script."
            echo "        $ $(dirname "${PYTHON}")/ray stop --force && ./dev/start_openclaw.sh ..."
        elif grep -q "Connection refused\|Cannot connect to" "${LOG_DIR}/rock-admin.log" 2>/dev/null; then
            echo ""
            echo "  HINT: ROCK admin could not connect to Ray at ${RAY_GCS_ADDRESS}."
            echo "        Check that Ray is actually running: $(dirname "${PYTHON}")/ray status"
        elif grep -q "redis\|Redis" "${LOG_DIR}/rock-admin.log" 2>/dev/null; then
            echo ""
            echo "  HINT: Redis connection issue detected."
            echo "        Check Redis: redis-cli -p ${ROCK_REDIS_PORT} ping"
        fi
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
    if curl -sf "http://${ROCK_HTTP_HOST}:${ROCK_PROXY_PORT}/" >/dev/null 2>&1; then
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
echo "[6/6] Deploying OpenClaw sandbox..."
echo "      Image : ${DEFAULT_IMAGE}"
echo "      Admin : ${ROCK_BASE_URL}"
echo "      Proxy : ${ROCK_PROXY_URL}"
echo ""

"${PYTHON}" -m openclaw_deploy.deploy \
    --image "${DEFAULT_IMAGE}" \
    --startup-timeout 300 \
    "${DEPLOY_EXTRA_ARGS[@]}"

echo ""
echo "Done. OpenClaw is ready."
echo "Logs: ${LOG_DIR}/rock-admin.log  |  ${LOG_DIR}/rock-proxy.log"
