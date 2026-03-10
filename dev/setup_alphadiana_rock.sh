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
PROJECT_ROOT="${SCRIPT_DIR%/dev}"
PROJECT_NAME="$(basename "${PROJECT_ROOT}")"
ROCK_REL="ref/ROCK"
ENV_REL="dev/rock_env.sh"
ROCK_REPO_URL="${ROCK_REPO_URL:-https://github.com/alibaba/ROCK.git}"

if ! command -v conda >/dev/null 2>&1; then
  echo "conda not found in PATH" >&2
  exit 1
fi

log_stage "Initialize conda shell integration"
log_progress "Loading conda shell hook"
init_conda_shell
log_progress "Conda shell hook loaded"

cd "${PROJECT_ROOT}"

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
log_progress "Upgrading pip"
with_nounset_disabled conda run -n "${ENV_NAME}" python -m pip install --upgrade pip
log_progress "Installing AlphaDiana editable dependencies"
with_nounset_disabled conda run -n "${ENV_NAME}" python -m pip install -e ".[all,dev]"
log_progress "Installing pinned nacos-sdk-python==2.0.9"
with_nounset_disabled conda run -n "${ENV_NAME}" python -m pip install "nacos-sdk-python==2.0.9"
log_progress "Dependency installation completed"

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
_rock_env_project_root="${_rock_env_script_dir%/dev}"
_rock_env_rock_root="${_rock_env_project_root}/ref/ROCK"

if [ ! -d "${_rock_env_rock_root}" ]; then
  echo "ROCK repository not found at ${_rock_env_rock_root}" >&2
  return 1 2>/dev/null || exit 1
fi

export PYTHONPATH="${_rock_env_rock_root}:${_rock_env_project_root}${PYTHONPATH:+:${PYTHONPATH}}"
export ROCK_CONFIG="${_rock_env_rock_root}/rock-conf/rock-local-proxy.yml"
export ROCK_WORKER_ENV_TYPE="local"
export ROCK_PROJECT_ROOT="${_rock_env_rock_root}"
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy
EOF

chmod +x "${ENV_REL}"
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

The helper uses absolute paths, so you can source it from the project root or from ${ROCK_REL}.
It also clears HTTP/HTTPS/ALL proxy variables to avoid local ROCK services inheriting host proxy settings.

Start services in separate terminals:
  cd ${ROCK_REL}
  ray start --head --port=6380 --disable-usage-stats --block
  python -m rock.admin.main --env local-proxy --role admin --port 9000
  python -m rock.admin.main --env local-proxy --role proxy --port 9001
EOF
