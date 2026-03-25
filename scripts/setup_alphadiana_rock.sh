#!/usr/bin/env bash

set -euo pipefail

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
import alphadiana, rock, ray, openai, httpx, requests, datasets, pytest, yaml
from packaging.version import Version

project_root = Path(r'''${PROJECT_ROOT}''').resolve()
module_path = Path(alphadiana.__file__).resolve()

assert project_root in module_path.parents
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

ENV_NAME="${1:-alphadiana}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_NAME="$(basename "${PROJECT_ROOT}")"
ROCK_REL="ref/ROCK"
ENV_REL="scripts/rock_env.sh"
ROCK_REPO_URL="${ROCK_REPO_URL:-https://github.com/alibaba/ROCK.git}"
LOCAL_TMPDIR="${PROJECT_ROOT}/.cache/tmp"

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
  git clone "${ROCK_REPO_URL}" "${ROCK_REL}"
  log_progress "ROCK clone completed"
else
  log_progress "ROCK repository already exists at ${ROCK_REL}"
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
  with_nounset_disabled conda run -n "${ENV_NAME}" python -m pip install --no-build-isolation -e ".[all,dev]"
  log_progress "Installing pinned nacos-sdk-python==2.0.9"
  with_nounset_disabled conda run -n "${ENV_NAME}" python -m pip install "nacos-sdk-python==2.0.9"
  log_progress "Dependency installation completed"
fi

log_stage "Prepare ROCK local runtime environment"
log_progress "Linking ${ROCK_REL}/.venv to conda env ${ENV_NAME}"
ln -sfn "$(with_nounset_disabled conda run -n "${ENV_NAME}" python -c 'import sys; print(sys.prefix)')" "${ROCK_REL}/.venv"
log_progress "ROCK local runtime symlink is ready"

log_stage "Write ROCK environment helper"
log_progress "Generating ${ENV_REL}"
cat > "${ENV_REL}" <<'EOF'
#!/usr/bin/env bash

# This file can be sourced from any working directory.
_rock_env_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_rock_env_project_root="$(cd "${_rock_env_script_dir}/.." && pwd)"
_rock_env_rock_root="${_rock_env_project_root}/ref/ROCK"
_rock_env_ports_file="${_rock_env_script_dir}/.rock_ports.env"
_rock_env_dynamic_config_default="${_rock_env_project_root}/dev/generated/rock-local-proxy.dynamic.yml"
_rock_env_cache_root="${_rock_env_project_root}/.cache"
_rock_env_user_name="${USER:-$(id -un 2>/dev/null || echo user)}"
_rock_env_ray_tmpdir_default="/tmp/${_rock_env_user_name}-ray"

if [ ! -d "${_rock_env_rock_root}" ]; then
  echo "ROCK repository not found at ${_rock_env_rock_root}" >&2
  return 1 2>/dev/null || exit 1
fi

if [ -f "${_rock_env_ports_file}" ]; then
  # Load dynamically detected local ports when available.
  # shellcheck disable=SC1090
  source "${_rock_env_ports_file}"
fi

export ROCK_RAY_PORT="${ROCK_RAY_PORT:-6380}"
export ROCK_RAY_DASHBOARD_PORT="${ROCK_RAY_DASHBOARD_PORT:-8265}"
export ROCK_RAY_CLIENT_SERVER_PORT="${ROCK_RAY_CLIENT_SERVER_PORT:-30001}"
export ROCK_REDIS_PORT="${ROCK_REDIS_PORT:-6379}"
export ROCK_REDIS_CONTAINER="${ROCK_REDIS_CONTAINER:-redis-stack}"
export ROCK_ADMIN_PORT="${ROCK_ADMIN_PORT:-9000}"
export ROCK_PROXY_PORT="${ROCK_PROXY_PORT:-9001}"
export ROCK_BASE_URL="${ROCK_BASE_URL:-http://127.0.0.1:${ROCK_ADMIN_PORT}}"
export ROCK_PROXY_ROOT_URL="${ROCK_PROXY_ROOT_URL:-http://127.0.0.1:${ROCK_PROXY_PORT}}"
export ROCK_PROXY_URL="${ROCK_PROXY_URL:-${ROCK_PROXY_ROOT_URL}/apis/envs/sandbox/v1}"
export ROCK_DYNAMIC_CONFIG="${ROCK_DYNAMIC_CONFIG:-${_rock_env_dynamic_config_default}}"
export TMPDIR="${TMPDIR:-${_rock_env_cache_root}/tmp}"
export RAY_TMPDIR="${RAY_TMPDIR:-${_rock_env_ray_tmpdir_default}}"
mkdir -p "${TMPDIR}"
mkdir -p "${RAY_TMPDIR}"

export PYTHONPATH="${_rock_env_rock_root}:${_rock_env_project_root}${PYTHONPATH:+:${PYTHONPATH}}"
if [ -f "${ROCK_DYNAMIC_CONFIG}" ]; then
  export ROCK_CONFIG="${ROCK_DYNAMIC_CONFIG}"
else
  export ROCK_CONFIG="${_rock_env_rock_root}/rock-conf/rock-local-proxy.yml"
fi
export ROCK_WORKER_ENV_TYPE="local"
export ROCK_PROJECT_ROOT="${_rock_env_rock_root}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy
EOF

chmod +x "${ENV_REL}"
mkdir -p "${ROCK_REL}/dev"
cat > "${ROCK_REL}/dev/rock_env.sh" <<'EOF'
#!/usr/bin/env bash

_rock_wrapper_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${_rock_wrapper_dir}/../../../scripts/rock_env.sh"
EOF
chmod +x "${ROCK_REL}/dev/rock_env.sh"
cat > "${ROCK_REL}/dev/.rock_ports.env" <<'EOF'
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../scripts/.rock_ports.env"
EOF
log_progress "Environment helper is ready"
log_progress "The helper exports absolute ROCK paths and clears inherited proxy variables"

cat <<EOF
Setup completed.

Activate the environment:
  conda activate ${ENV_NAME}

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
  source ${ENV_REL}
  docker start \$ROCK_REDIS_CONTAINER || docker run -d --restart unless-stopped --name \$ROCK_REDIS_CONTAINER -p \$ROCK_REDIS_PORT:6379 redis/redis-stack-server:latest
  cd ${ROCK_REL}
  ray start --head --port=\$ROCK_RAY_PORT --dashboard-port=\$ROCK_RAY_DASHBOARD_PORT --ray-client-server-port=\$ROCK_RAY_CLIENT_SERVER_PORT --temp-dir="\$RAY_TMPDIR" --disable-usage-stats --block
  python -m rock.admin.main --env local-proxy --role admin --port \$ROCK_ADMIN_PORT
  python -m rock.admin.main --env local-proxy --role proxy --port \$ROCK_PROXY_PORT
EOF
