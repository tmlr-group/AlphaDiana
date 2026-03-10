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
