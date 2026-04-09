#!/usr/bin/env bash
# Source this file to set up the AlphaDiana shell environment.
#
# Usage (from the project root):
#   source scripts/activate.sh
#
# What it does:
#   1. Activates the conda environment
#   2. Clears proxy variables that interfere with local ROCK services
#   3. Loads ROCK port configuration
#   4. Loads API keys from .env
#   5. Sets PYTHONPATH and ROCK config variables

_activate_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_activate_project_root="$(cd "${_activate_script_dir}/.." && pwd)"
_activate_rock_root="${_activate_project_root}/ref/ROCK"

# ── 1. Conda ─────────────────────────────────────────────────────────────────
eval "$(conda shell.bash hook)" 2>/dev/null
conda activate alphadiana 2>/dev/null
if [ $? -ne 0 ]; then
  echo "Warning: could not activate conda env 'alphadiana'." >&2
  echo "Run 'bash scripts/quickstart.sh' first to create it." >&2
fi

# ── 2. Clear proxy variables ─────────────────────────────────────────────────
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy

# ── 3. Load ROCK port configuration ──────────────────────────────────────────
if [ -f "${_activate_script_dir}/.rock_ports.env" ]; then
  source "${_activate_script_dir}/.rock_ports.env"
else
  echo "Warning: scripts/.rock_ports.env not found. Run 'bash scripts/quickstart.sh' first." >&2
fi

# ── 4. Load .env (API keys) ──────────────────────────────────────────────────
if [ -f "${_activate_project_root}/.env" ]; then
  set -a
  source "${_activate_project_root}/.env"
  set +a
else
  echo "Warning: .env not found. Create it with OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL_NAME." >&2
fi

# ── 5. ROCK environment variables ────────────────────────────────────────────
export ROCK_RAY_PORT="${ROCK_RAY_PORT:-6380}"
export ROCK_RAY_DASHBOARD_PORT="${ROCK_RAY_DASHBOARD_PORT:-8265}"
export ROCK_RAY_CLIENT_SERVER_PORT="${ROCK_RAY_CLIENT_SERVER_PORT:-30001}"
export ROCK_REDIS_PORT="${ROCK_REDIS_PORT:-6379}"
export ROCK_REDIS_CONTAINER="${ROCK_REDIS_CONTAINER:-redis-stack}"
export ROCK_ADMIN_PORT="${ROCK_ADMIN_PORT:-9000}"
export ROCK_PROXY_PORT="${ROCK_PROXY_PORT:-9001}"
export ROCK_BIND_HOST="${ROCK_BIND_HOST:-127.0.0.1}"
export ROCK_BASE_URL="${ROCK_BASE_URL:-http://${ROCK_BIND_HOST}:${ROCK_ADMIN_PORT}}"
export ROCK_PROXY_ROOT_URL="${ROCK_PROXY_ROOT_URL:-http://${ROCK_BIND_HOST}:${ROCK_PROXY_PORT}}"
export ROCK_PROXY_URL="${ROCK_PROXY_URL:-${ROCK_PROXY_ROOT_URL}/apis/envs/sandbox/v1}"

_activate_user_name="${USER:-$(id -un 2>/dev/null || echo user)}"
export TMPDIR="${TMPDIR:-${_activate_project_root}/.cache/tmp}"
export RAY_TMPDIR="${RAY_TMPDIR:-/tmp/${_activate_user_name}-ray}"
mkdir -p "${TMPDIR}" 2>/dev/null
mkdir -p "${RAY_TMPDIR}" 2>/dev/null

if [ -d "${_activate_rock_root}" ]; then
  export PYTHONPATH="${_activate_rock_root}:${_activate_project_root}${PYTHONPATH:+:${PYTHONPATH}}"
  _activate_dynamic_config="${_activate_project_root}/scripts/generated/rock-local-proxy.dynamic.yml"
  if [ -f "${_activate_dynamic_config}" ]; then
    export ROCK_CONFIG="${_activate_dynamic_config}"
  else
    export ROCK_CONFIG="${_activate_rock_root}/rock-conf/rock-local-proxy.yml"
  fi
  export ROCK_WORKER_ENV_TYPE="local"
  export ROCK_PROJECT_ROOT="${_activate_rock_root}"
fi

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

# ── Cleanup temp vars ────────────────────────────────────────────────────────
unset _activate_script_dir _activate_project_root _activate_rock_root
unset _activate_user_name _activate_dynamic_config

echo "AlphaDiana environment ready."
