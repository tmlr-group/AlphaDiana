#!/usr/bin/env bash
# One-click quickstart for AlphaDiana + ROCK local environment.
#
# Usage:
#   bash scripts/quickstart.sh [ENV_NAME]
#
# This script:
#   1. Checks prerequisites (conda, docker, git)
#   2. Runs setup_alphadiana_rock.sh (conda env, ROCK clone, pip install)
#   3. Installs ROCK editable package
#   4. Detects available ports
#   5. Starts Redis, Ray, admin, proxy (all in background)
#   6. Runs health checks
#
# After completion, source scripts/rock_env.sh and scripts/.rock_ports.env in any new terminal.

set -euo pipefail

unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy

# Security preflight: abort if known misconfigurations are present (HIGH/CRITICAL).
# Matches the pattern used by scripts/start_openclaw.sh and scripts/start_zeroclaw.sh
# so quickstart cannot silently bring up Redis/Ray/ROCK with public 0.0.0.0 bindings.
_SG_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
echo "[preflight] Running security_guard.py --check..."
if ! python3 "${_SG_SCRIPT_DIR}/security_guard.py" --check; then
    echo ""
    echo "ERROR: Security preflight check failed. Start aborted."
    echo "       Fix the issues above, or set SECURITY_GUARD_BYPASS=1 to override."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
DEFAULT_ENV_NAME="$(
python - "${PROJECT_ROOT}" <<'PYEOF' 2>/dev/null || true
import sys
from pathlib import Path

project_root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(project_root))

from alphadiana.utils.rock_ports import default_checkout_env_name

print(default_checkout_env_name(project_root))
PYEOF
)"
ENV_NAME="${1:-${DEFAULT_ENV_NAME:-alphadiana}}"

cd "${PROJECT_ROOT}"

# ── helpers ──────────────────────────────────────────────────────────────────

log_section() { echo -e "\n========== $1 =========="; }
log_ok()      { echo "  ✓ $1"; }
log_fail()    { echo "  ✗ $1" >&2; }
log_info()    { echo "  • $1"; }
log_warn()    { echo "  ! $1"; }

with_nounset_disabled() {
  local had_nounset=0
  case $- in *u*) had_nounset=1 ;; esac
  set +u; "$@"; local status=$?
  [ "${had_nounset}" -eq 1 ] && set -u
  return "${status}"
}

init_conda_shell() {
  local had_nounset=0; local status=0
  case $- in *u*) had_nounset=1 ;; esac
  set +u
  eval "$(conda shell.bash hook)" || status=$?
  [ "${had_nounset}" -eq 1 ] && set -u
  return "${status}"
}

detect_container_host_port() {
  local container_name="$1"
  local container_port="$2"
  local line

  while IFS= read -r line; do
    case "${line}" in
      *:*)
        echo "${line##*:}"
        return 0
        ;;
    esac
  done < <(docker port "${container_name}" "${container_port}" 2>/dev/null || true)

  return 1
}

refresh_generated_ports() {
  python "${SCRIPT_DIR}/find_rock_ports.py" \
    --ray-port "${ROCK_RAY_PORT}" \
    --ray-dashboard-port "${ROCK_RAY_DASHBOARD_PORT}" \
    --ray-client-server-port "${ROCK_RAY_CLIENT_SERVER_PORT}" \
    --redis-port "${ROCK_REDIS_PORT}" \
    --redis-container "${ROCK_REDIS_CONTAINER}" \
    --admin-port "${ROCK_ADMIN_PORT}" \
    --proxy-port "${ROCK_PROXY_PORT}" \
    --write-env "${SCRIPT_DIR}/.rock_ports.env" >/dev/null
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/rock_env.sh"
}

ensure_writable_dir() {
  local dir_path="$1"
  mkdir -p "${dir_path}"
  [ -w "${dir_path}" ]
}

# ── 1. Prerequisites ────────────────────────────────────────────────────────

log_section "Checking prerequisites"

for cmd in conda git curl; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_fail "${cmd} not found in PATH"
    exit 1
  fi
  log_ok "${cmd} $(${cmd} --version 2>&1 | head -1)"
done

if command -v docker >/dev/null 2>&1 && docker ps >/dev/null 2>&1; then
  log_ok "docker $(docker --version 2>&1 | head -1)"
elif command -v sg >/dev/null 2>&1; then
  log_ok "docker available via sg docker"
  # wrap docker so subsequent calls work
  docker() { sg docker -c "docker $*"; }
else
  log_fail "docker not accessible — ensure your user is in the docker group"
  exit 1
fi

# ── 2. Setup conda env + ROCK clone + pip install ────────────────────────────

log_section "Running setup_alphadiana_rock.sh (env=${ENV_NAME})"
bash "${SCRIPT_DIR}/setup_alphadiana_rock.sh" "${ENV_NAME}"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/.alphadiana_env"

# ── 3. Init conda in this shell ─────────────────────────────────────────────

log_section "Initializing conda for this shell"
init_conda_shell
with_nounset_disabled conda activate "${ENV_NAME}"
log_ok "Activated conda env ${ENV_NAME}"

# ── 4. Install ROCK editable package ────────────────────────────────────────

log_section "Verifying ROCK editable package"
EXPECTED_ROCK_INIT="$(cd ref/ROCK && pwd -P)/rock/__init__.py"
CURRENT_ROCK_INIT="$(python - <<'PYEOF' 2>/dev/null || true
from pathlib import Path

try:
    import rock
except Exception:
    raise SystemExit(0)

print(Path(rock.__file__).resolve())
PYEOF
)"
if [ "${CURRENT_ROCK_INIT}" = "${EXPECTED_ROCK_INIT}" ]; then
  log_ok "rl-rock already points at ${EXPECTED_ROCK_INIT}"
else
  python -m pip install --no-build-isolation -e ref/ROCK
  log_ok "rl-rock installed from ${EXPECTED_ROCK_INIT}"
fi

# ── 5. Source rock_env and detect ports ──────────────────────────────────────

log_section "Detecting available ports"
unset ROCK_DYNAMIC_CONFIG RAY_TMPDIR ROCK_CONFIG TMPDIR
python "${SCRIPT_DIR}/find_rock_ports.py" --write-env "${SCRIPT_DIR}/.rock_ports.env"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/rock_env.sh"
log_ok "Ports written to scripts/.rock_ports.env"

# ── 6. Start services ───────────────────────────────────────────────────────

log_section "Starting services"
mkdir -p dev/generated

if [ -f "${SCRIPT_DIR}/cleanup_rock_ports.sh" ]; then
  log_info "Cleaning existing listeners on selected ROCK ports"
  bash "${SCRIPT_DIR}/cleanup_rock_ports.sh"
fi

# Redis
echo "  Starting Redis..."
docker start "${ROCK_REDIS_CONTAINER}" 2>/dev/null \
  || docker run -d --restart unless-stopped --name "${ROCK_REDIS_CONTAINER}" -p "127.0.0.1:${ROCK_REDIS_PORT}:6379" redis/redis-stack-server:latest
sleep 2
ACTUAL_REDIS_PORT="$(detect_container_host_port "${ROCK_REDIS_CONTAINER}" 6379/tcp || true)"
if [ -n "${ACTUAL_REDIS_PORT}" ] && [ "${ACTUAL_REDIS_PORT}" != "${ROCK_REDIS_PORT}" ]; then
  log_info "Redis container is mapped to host port ${ACTUAL_REDIS_PORT}; updating runtime ports"
  export ROCK_REDIS_PORT="${ACTUAL_REDIS_PORT}"
  refresh_generated_ports
fi
if docker exec "${ROCK_REDIS_CONTAINER}" redis-cli ping 2>/dev/null | grep -q PONG; then
  log_ok "Redis is running on port ${ROCK_REDIS_PORT}"
else
  log_fail "Redis health check failed"
  exit 1
fi

# Ray
echo "  Starting Ray head..."
if ! ensure_writable_dir "${RAY_TMPDIR}"; then
  log_fail "RAY_TMPDIR is not writable: ${RAY_TMPDIR}"
  exit 1
fi
(
  cd ref/ROCK
  ray start --head \
    --port="${ROCK_RAY_PORT}" \
    --dashboard-port="${ROCK_RAY_DASHBOARD_PORT}" \
    --ray-client-server-port="${ROCK_RAY_CLIENT_SERVER_PORT}" \
    --temp-dir="${RAY_TMPDIR}" \
    --disable-usage-stats
)
log_ok "Ray head started on port ${ROCK_RAY_PORT}"

# Admin
echo "  Starting ROCK admin on port ${ROCK_ADMIN_PORT}..."
(
  cd ref/ROCK
  nohup python ../../scripts/run_rock_admin_local.py --env local-proxy --role admin --port "${ROCK_ADMIN_PORT}" \
    < /dev/null > ../../dev/generated/admin.log 2>&1 &
  echo $! > ../../dev/generated/admin.pid
  disown || true
)
ADMIN_PID="$(cat dev/generated/admin.pid)"
rm -f dev/generated/admin.pid

# Proxy
echo "  Starting ROCK proxy on port ${ROCK_PROXY_PORT}..."
(
  cd ref/ROCK
  nohup python ../../scripts/run_rock_admin_local.py --env local-proxy --role proxy --port "${ROCK_PROXY_PORT}" \
    < /dev/null > ../../dev/generated/proxy.log 2>&1 &
  echo $! > ../../dev/generated/proxy.pid
  disown || true
)
PROXY_PID="$(cat dev/generated/proxy.pid)"
rm -f dev/generated/proxy.pid

# Wait for admin/proxy to be ready
echo "  Waiting for admin and proxy to start..."
for i in $(seq 1 30); do
  if curl -s "${ROCK_BASE_URL}/" >/dev/null 2>&1 && curl -s "${ROCK_PROXY_ROOT_URL}/" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "${ADMIN_PID}" 2>/dev/null; then
    log_fail "ROCK admin exited early; see dev/generated/admin.log"
    exit 1
  fi
  if ! kill -0 "${PROXY_PID}" 2>/dev/null; then
    log_fail "ROCK proxy exited early; see dev/generated/proxy.log"
    exit 1
  fi
  sleep 2
done

# ── 7. Health checks ────────────────────────────────────────────────────────

log_section "Health checks"

_check() {
  local name="$1" cmd="$2"
  if eval "${cmd}" >/dev/null 2>&1; then
    log_ok "${name}"
  else
    log_fail "${name}"
    return 1
  fi
}

_check "Redis PONG" "docker exec ${ROCK_REDIS_CONTAINER} redis-cli ping 2>/dev/null | grep -q PONG"
_check "ROCK admin (${ROCK_BASE_URL})" "curl -sf ${ROCK_BASE_URL}/"
_check "ROCK proxy (${ROCK_PROXY_ROOT_URL})" "curl -sf ${ROCK_PROXY_ROOT_URL}/"

log_section "Done!"
cat <<EOF
All services are running:
  Redis:  port ${ROCK_REDIS_PORT}  (container: ${ROCK_REDIS_CONTAINER})
  Ray:    port ${ROCK_RAY_PORT}
  Admin:  port ${ROCK_ADMIN_PORT}  (PID ${ADMIN_PID}, log: dev/generated/admin.log)
  Proxy:  port ${ROCK_PROXY_PORT}  (PID ${PROXY_PID}, log: dev/generated/proxy.log)

In any new terminal, run:
  source scripts/activate.sh

Minimal OpenClaw smoke path:
  docker pull tmlrgroup/alphadiana:v1
  export OPENAI_BASE_URL=...
  export OPENAI_API_KEY=...
  export OPENAI_MODEL_NAME=...
  alphadiana validate configs/examples/openclaw_quickstart.yaml
  alphadiana run configs/examples/openclaw_quickstart.yaml

Manual deploy path:
  python alphadiana/harness/openclaw/deploy/deploy.py --agent-config alphadiana/harness/openclaw/deploy/rock_agent_config.prebuilt.yaml --image tmlrgroup/alphadiana:v1
EOF
