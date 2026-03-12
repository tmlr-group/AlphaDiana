#!/usr/bin/env bash
# One-click quickstart for AlphaDiana + ROCK local environment.
#
# Usage:
#   bash dev/quickstart.sh [ENV_NAME]
#
# This script:
#   1. Checks prerequisites (conda, docker, git)
#   2. Runs setup_alphadiana_rock.sh (conda env, ROCK clone, pip install)
#   3. Installs ROCK editable package
#   4. Detects available ports
#   5. Starts Redis, Ray, admin, proxy (all in background)
#   6. Runs health checks
#
# After completion, source dev/rock_env.sh and dev/.rock_ports.env in any new terminal.

set -euo pipefail

ENV_NAME="${1:-alphadiana}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR%/dev}"

cd "${PROJECT_ROOT}"

# ── helpers ──────────────────────────────────────────────────────────────────

log_section() { echo -e "\n========== $1 =========="; }
log_ok()      { echo "  ✓ $1"; }
log_fail()    { echo "  ✗ $1" >&2; }

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

# ── 1. Prerequisites ────────────────────────────────────────────────────────

log_section "Checking prerequisites"

for cmd in conda git; do
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

# ── 3. Init conda in this shell ─────────────────────────────────────────────

log_section "Initializing conda for this shell"
init_conda_shell
with_nounset_disabled conda activate "${ENV_NAME}"
log_ok "Activated conda env ${ENV_NAME}"

# ── 4. Install ROCK editable package ────────────────────────────────────────

log_section "Installing ROCK editable package"
if python -c "import rock" >/dev/null 2>&1; then
  log_ok "rl-rock already importable"
else
  pip install --no-build-isolation -e ref/ROCK
  log_ok "rl-rock installed"
fi

# ── 5. Source rock_env and detect ports ──────────────────────────────────────

log_section "Detecting available ports"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/rock_env.sh"
python "${SCRIPT_DIR}/find_rock_ports.py" --write-env "${SCRIPT_DIR}/.rock_ports.env"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/.rock_ports.env"
log_ok "Ports written to dev/.rock_ports.env"

# ── 6. Start services ───────────────────────────────────────────────────────

log_section "Starting services"

# Redis
echo "  Starting Redis..."
docker start "${ROCK_REDIS_CONTAINER}" 2>/dev/null \
  || docker run -d --name "${ROCK_REDIS_CONTAINER}" -p "${ROCK_REDIS_PORT}:6379" redis/redis-stack-server:latest
sleep 2
if docker exec "${ROCK_REDIS_CONTAINER}" redis-cli ping 2>/dev/null | grep -q PONG; then
  log_ok "Redis is running on port ${ROCK_REDIS_PORT}"
else
  log_fail "Redis health check failed"
  exit 1
fi

# Ray
echo "  Starting Ray head..."
cd ref/ROCK
ray stop 2>/dev/null || true
ray start --head \
  --port="${ROCK_RAY_PORT}" \
  --dashboard-port="${ROCK_RAY_DASHBOARD_PORT}" \
  --ray-client-server-port="${ROCK_RAY_CLIENT_SERVER_PORT}" \
  --temp-dir="${RAY_TMPDIR}" \
  --disable-usage-stats
cd "${PROJECT_ROOT}"
log_ok "Ray head started on port ${ROCK_RAY_PORT}"

# Admin
echo "  Starting ROCK admin on port ${ROCK_ADMIN_PORT}..."
cd ref/ROCK
nohup python -m rock.admin.main --env local-proxy --role admin --port "${ROCK_ADMIN_PORT}" \
  > "${PROJECT_ROOT}/dev/generated/admin.log" 2>&1 &
ADMIN_PID=$!
cd "${PROJECT_ROOT}"

# Proxy
echo "  Starting ROCK proxy on port ${ROCK_PROXY_PORT}..."
cd ref/ROCK
nohup python -m rock.admin.main --env local-proxy --role proxy --port "${ROCK_PROXY_PORT}" \
  > "${PROJECT_ROOT}/dev/generated/proxy.log" 2>&1 &
PROXY_PID=$!
cd "${PROJECT_ROOT}"

# Wait for admin/proxy to be ready
echo "  Waiting for admin and proxy to start..."
for i in $(seq 1 30); do
  if curl -s "${ROCK_BASE_URL}/" >/dev/null 2>&1 && curl -s "${ROCK_PROXY_ROOT_URL}/" >/dev/null 2>&1; then
    break
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
  conda activate ${ENV_NAME}
  source dev/rock_env.sh
  source dev/.rock_ports.env

To deploy OpenClaw:
  python dev/run_openclaw_aime_openrouter.py
  python openclaw_deploy/deploy.py --agent-config dev/generated/rock_agent_config.openrouter_qwen_2_5_7b.resolved.yaml
EOF
