#!/usr/bin/env bash

set -euo pipefail

unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy

# Security preflight: abort if known misconfigurations are present (HIGH/CRITICAL).
# Matches the pattern used by scripts/start_openclaw.sh and scripts/start_zeroclaw.sh
# so setup_alphadiana_rock cannot silently bring up services with public 0.0.0.0 bindings.
_SG_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
echo "[preflight] Running security_guard.py --check..."
if ! python3 "${_SG_SCRIPT_DIR}/security_guard.py" --check; then
    echo ""
    echo "ERROR: Security preflight check failed. Setup aborted."
    echo "       Fix the issues above, or set SECURITY_GUARD_BYPASS=1 to override."
    exit 1
fi

TOTAL_STEPS=6
CURRENT_STEP=0

log_stage() {
  CURRENT_STEP=$((CURRENT_STEP + 1))
  echo
  echo "[${CURRENT_STEP}/${TOTAL_STEPS}] $1"
}

log_progress() {
  echo "    -> $1"
}

with_nounset_disabled() {
  local had_nounset=0
  case $- in
    *u*) had_nounset=1 ;;
  esac

  set +u
  "$@"
  local status=$?

  if [ "${had_nounset}" -eq 1 ]; then
    set -u
  fi

  return "${status}"
}

env_has_required_python_stack() {
  with_nounset_disabled conda run -n "${ENV_NAME}" python -c "
from pathlib import Path
import importlib.metadata as md
import alphadiana, rock, ray, openai, httpx, requests, datasets, yaml
from packaging.version import Version

project_root = Path(r'''${PROJECT_ROOT}''').resolve()
module_path = Path(alphadiana.__file__).resolve()
rock_path = Path(rock.__file__).resolve()

assert project_root in module_path.parents
assert rock_path == (project_root / 'ref' / 'ROCK' / 'rock' / '__init__.py')
assert md.version('alphadiana') == '0.1.0'
assert Version(md.version('rl-rock')) >= Version('1.3.0')
assert md.version('nacos-sdk-python') == '2.0.9'
"
}

init_conda_shell() {
  local had_nounset=0
  local conda_hook
  local status=0

  case $- in
    *u*) had_nounset=1 ;;
  esac

  set +u
  conda_hook="$(conda shell.bash hook)"
  status=$?

  if [ "${status}" -ne 0 ]; then
    if [ "${had_nounset}" -eq 1 ]; then
      set -u
    fi
    return "${status}"
  fi

  eval "${conda_hook}"
  status=$?

  if [ "${had_nounset}" -eq 1 ]; then
    set -u
  fi

  return "${status}"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
PROJECT_NAME="$(basename "${PROJECT_ROOT}")"
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
ROCK_REL="ref/ROCK"
ENV_REL="scripts/rock_env.sh"
ENV_MARKER_REL="scripts/.alphadiana_env"
ROCK_REPO_URL="${ROCK_REPO_URL:-https://github.com/alibaba/ROCK.git}"
ROCK_REVISION="${ROCK_REVISION:-908f20d4bf2fcbc362dba6a068c1cd399a201f82}"
ROCK_REPO_ARCHIVE_URL="${ROCK_REPO_ARCHIVE_URL:-https://codeload.github.com/alibaba/ROCK/tar.gz/${ROCK_REVISION}}"
LOCAL_TMPDIR="${PROJECT_ROOT}/.cache/tmp"

checkout_rock_revision() {
  local current_revision
  current_revision="$(git -C "${ROCK_REL}" rev-parse HEAD 2>/dev/null || true)"
  if [ "${current_revision}" = "${ROCK_REVISION}" ]; then
    return 0
  fi
  log_progress "Checking out pinned ROCK revision ${ROCK_REVISION}"
  git -C "${ROCK_REL}" fetch --depth=1 origin "${ROCK_REVISION}"
  git -C "${ROCK_REL}" checkout --detach FETCH_HEAD
}

is_weak_gateway_token() {
  case "${1:-}" in
    ""|OPENCLAW|openclaw|test|token|default|changeme|secret) return 0 ;;
    *) return 1 ;;
  esac
}

load_existing_gateway_token() {
  if [ ! -f "${ENV_MARKER_REL}" ]; then
    return 0
  fi
  awk -F'"' '/^export OPENCLAW_GATEWAY_TOKEN=/{print $2; exit}' "${ENV_MARKER_REL}"
}

generate_gateway_token() {
  python - <<'PYEOF'
import secrets

print(secrets.token_urlsafe(32))
PYEOF
}

resolve_gateway_token() {
  local token="${OPENCLAW_GATEWAY_TOKEN:-}"
  if is_weak_gateway_token "${token}"; then
    token="$(load_existing_gateway_token)"
  fi
  if is_weak_gateway_token "${token}"; then
    token="$(generate_gateway_token)"
  fi
  printf '%s' "${token}"
}

if ! command -v conda >/dev/null 2>&1; then
  echo "conda not found in PATH" >&2
  exit 1
fi

log_stage "Initialize conda shell integration"
log_progress "Loading conda shell hook"
init_conda_shell
log_progress "Conda shell hook loaded"

cd "${PROJECT_ROOT}"
mkdir -p "${LOCAL_TMPDIR}"
export TMPDIR="${LOCAL_TMPDIR}"

log_stage "Prepare ROCK source tree"
if [ ! -d "${ROCK_REL}" ]; then
  log_progress "Cloning ROCK into ${ROCK_REL}"
  mkdir -p ref
  if git -c http.proxy= -c https.proxy= clone --depth=1 --filter=blob:none "${ROCK_REPO_URL}" "${ROCK_REL}"; then
    checkout_rock_revision
    log_progress "ROCK clone completed"
  else
    log_progress "git clone failed; falling back to GitHub source archive"
    rm -rf "${ROCK_REL}"
    curl -L --fail --max-time 600 "${ROCK_REPO_ARCHIVE_URL}" -o "${LOCAL_TMPDIR}/rock-source.tar.gz"
    rm -rf "${LOCAL_TMPDIR}/rock-source"
    mkdir -p "${LOCAL_TMPDIR}/rock-source"
    tar -xzf "${LOCAL_TMPDIR}/rock-source.tar.gz" -C "${LOCAL_TMPDIR}/rock-source"
    mv "${LOCAL_TMPDIR}/rock-source"/ROCK-* "${ROCK_REL}"
    log_progress "ROCK source archive extracted into ${ROCK_REL}"
  fi
else
  log_progress "ROCK repository already exists at ${ROCK_REL}"
  if [ -d "${ROCK_REL}/.git" ]; then
    checkout_rock_revision
  fi
fi

log_stage "Prepare conda environment ${ENV_NAME}"
if ! with_nounset_disabled conda env list | awk '{print $1}' | grep -Fxq "${ENV_NAME}"; then
  log_progress "Creating environment ${ENV_NAME}"
  with_nounset_disabled conda create -n "${ENV_NAME}" python=3.11 -y
  log_progress "Environment ${ENV_NAME} created"
else
  log_progress "Environment ${ENV_NAME} already exists"
fi

log_stage "Install Python dependencies"
if env_has_required_python_stack; then
  log_progress "Environment already has AlphaDiana and required dependencies"
else
  log_progress "Upgrading pip"
  with_nounset_disabled conda run -n "${ENV_NAME}" python -m pip install --upgrade pip
  log_progress "Installing AlphaDiana editable dependencies"
  # Reuse the active environment's build backend packages so repeat setup runs
  # do not fail in network-restricted shells while trying to create an isolated
  # build env for the local project.
  with_nounset_disabled conda run -n "${ENV_NAME}" python -m pip install --no-build-isolation -e ".[all,benchmarks]"
  log_progress "Installing ROCK editable package from ${ROCK_REL}"
  with_nounset_disabled conda run -n "${ENV_NAME}" python -m pip install --no-build-isolation -e "${ROCK_REL}"
  log_progress "Installing pinned nacos-sdk-python==2.0.9"
  with_nounset_disabled conda run -n "${ENV_NAME}" python -m pip install "nacos-sdk-python==2.0.9"
  log_progress "Dependency installation completed"
fi

log_stage "Prepare ROCK local runtime environment"
log_progress "Linking ${ROCK_REL}/.venv to conda env ${ENV_NAME}"
ln -sfn "$(with_nounset_disabled conda run -n "${ENV_NAME}" python -c 'import sys; print(sys.prefix)')" "${ROCK_REL}/.venv"
log_progress "ROCK local runtime symlink is ready"

log_stage "Write ROCK environment helper"
if [ ! -f "${ENV_REL}" ]; then
  echo "Missing ${ENV_REL}; cannot continue." >&2
  exit 1
fi
GATEWAY_TOKEN="$(resolve_gateway_token)"
log_progress "Persisting checkout-local env and OpenClaw gateway token to ${ENV_MARKER_REL}"
cat > "${ENV_MARKER_REL}" <<EOF
export ALPHADIANA_ENV_NAME="${ENV_NAME}"
export ALPHADIANA_ENV_PROJECT_ROOT="${PROJECT_ROOT}"
export ALPHADIANA_ROCK_ROOT="${PROJECT_ROOT}/${ROCK_REL}"
export OPENCLAW_GATEWAY_TOKEN="${GATEWAY_TOKEN}"
EOF
chmod +x "${ENV_REL}" "${ENV_MARKER_REL}"
mkdir -p "${ROCK_REL}/dev"
cat > "${ROCK_REL}/dev/rock_env.sh" <<'EOF'
#!/usr/bin/env bash

_rock_wrapper_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck disable=SC1091
source "${_rock_wrapper_dir}/../../../scripts/rock_env.sh"
EOF
chmod +x "${ROCK_REL}/dev/rock_env.sh"
cat > "${ROCK_REL}/dev/.rock_ports.env" <<'EOF'
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/../../../scripts/.rock_ports.env"
EOF
log_progress "Environment helper is ready"
log_progress "The helper exports absolute ROCK paths and clears inherited proxy variables"

cat <<EOF
Setup completed.

Activate the environment:
  source scripts/activate.sh

Go to the project root:
  cd ${PROJECT_NAME}

Load ROCK variables in each terminal after activating conda:
  source ${ENV_REL}

Run source ${ENV_REL} from the project root.
If you are already inside ${ROCK_REL}, use:
  source ../../${ENV_REL}

The helper exports absolute ROCK paths after it is loaded.
It also clears HTTP/HTTPS/ALL proxy variables to avoid local ROCK services inheriting host proxy settings.
If scripts/.rock_ports.env exists, it will also load dynamically detected ROCK ports.
If dev/generated/rock-local-proxy.dynamic.yml exists, ROCK_CONFIG will point to that generated config.
Before running python scripts/find_rock_ports.py manually, unset TMPDIR to avoid carrying an old long socket path:
  unset TMPDIR

Start services in separate terminals:
  source scripts/activate.sh
  docker start \$ROCK_REDIS_CONTAINER || docker run -d --restart unless-stopped --name \$ROCK_REDIS_CONTAINER -p 127.0.0.1:\$ROCK_REDIS_PORT:6379 redis/redis-stack-server:latest
  cd ${ROCK_REL}
  ray start --head --port=\$ROCK_RAY_PORT --dashboard-port=\$ROCK_RAY_DASHBOARD_PORT --ray-client-server-port=\$ROCK_RAY_CLIENT_SERVER_PORT --temp-dir="\$RAY_TMPDIR" --disable-usage-stats --block
  python ../../scripts/run_rock_admin_local.py --env local-proxy --role admin --port \$ROCK_ADMIN_PORT
  python ../../scripts/run_rock_admin_local.py --env local-proxy --role proxy --port \$ROCK_PROXY_PORT
EOF
