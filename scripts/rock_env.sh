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
