#!/usr/bin/env bash
# Source this file to set up the AlphaDiana shell environment.
#
# Usage (from the project root):
#   source scripts/activate.sh
#
# What it does:
#   1. Activates the conda environment
#   2. Clears proxy variables that interfere with local ROCK services
#   3. Loads API keys from .env
#   4. Reuses scripts/rock_env.sh for ROCK ports, PYTHONPATH, and ROCK_CONFIG

_activate_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
_activate_project_root="$(cd "${_activate_script_dir}/.." && pwd -P)"
_activate_python="${PYTHON:-$(command -v python3 || command -v python || echo python3)}"
_activate_rock_root="${_activate_project_root}/ref/ROCK"
_activate_env_file="${_activate_script_dir}/.alphadiana_env"
_activate_default_env_name="$(
  "${_activate_python}" - "${_activate_project_root}" <<'PYEOF' 2>/dev/null || true
import sys
from pathlib import Path

project_root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(project_root))

from alphadiana.utils.rock_ports import default_checkout_env_name

print(default_checkout_env_name(project_root))
PYEOF
)"
_activate_is_weak_gateway_token() {
  case "${1:-}" in
    ""|OPENCLAW|openclaw|test|token|default|changeme|secret) return 0 ;;
    *) return 1 ;;
  esac
}
if [ -f "${_activate_env_file}" ]; then
  # shellcheck disable=SC1090
  source "${_activate_env_file}"
fi
if [ -n "${ALPHADIANA_ENV_PROJECT_ROOT:-}" ] && [ "${ALPHADIANA_ENV_PROJECT_ROOT}" != "${_activate_project_root}" ]; then
  echo "Warning: scripts/.alphadiana_env belongs to a different checkout (${ALPHADIANA_ENV_PROJECT_ROOT}); ignoring stale local state." >&2
  unset ALPHADIANA_ENV_NAME ALPHADIANA_ENV_PROJECT_ROOT ALPHADIANA_ROCK_ROOT
fi
if [ -n "${ALPHADIANA_ROCK_ROOT:-}" ] && [ ! -d "${ALPHADIANA_ROCK_ROOT}" ]; then
  echo "Warning: ALPHADIANA_ROCK_ROOT points to a missing directory (${ALPHADIANA_ROCK_ROOT}); falling back to this checkout." >&2
  unset ALPHADIANA_ROCK_ROOT
fi
if [ -d "${_activate_rock_root}" ]; then
  export ALPHADIANA_ROCK_ROOT="${_activate_rock_root}"
fi
_activate_env_name="${ALPHADIANA_ENV_NAME:-${_activate_default_env_name:-alphadiana}}"

# ── 1. Conda ─────────────────────────────────────────────────────────────────
eval "$(conda shell.bash hook)" 2>/dev/null
conda activate "${_activate_env_name}" 2>/dev/null
if [ $? -ne 0 ]; then
  echo "Warning: could not activate conda env '${_activate_env_name}'." >&2
  echo "Run 'bash scripts/quickstart.sh ${_activate_env_name}' first to create the checkout-local environment." >&2
fi

# ── 2. Clear proxy variables ─────────────────────────────────────────────────
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy

# ── 3. Load .env (API keys) ──────────────────────────────────────────────────
if [ -f "${_activate_project_root}/.env" ]; then
  set -a
  source "${_activate_project_root}/.env"
  set +a
else
  echo "Warning: .env not found. Create it with OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL_NAME." >&2
fi

if _activate_is_weak_gateway_token "${OPENCLAW_GATEWAY_TOKEN:-}"; then
  OPENCLAW_GATEWAY_TOKEN="$("${_activate_python}" - <<'PYEOF' 2>/dev/null || true
import secrets

print(secrets.token_urlsafe(32))
PYEOF
)"
  export OPENCLAW_GATEWAY_TOKEN
fi

# ── 4. ROCK environment variables ────────────────────────────────────────────
if [ ! -d "${_activate_rock_root}" ] && [ -z "${ALPHADIANA_ROCK_ROOT:-}" ]; then
  _activate_detected_rock_root="$(
    python - <<'PYEOF' 2>/dev/null || true
from pathlib import Path

try:
    import rock
except Exception:
    raise SystemExit(0)

print(Path(rock.__file__).resolve().parents[1])
PYEOF
  )"
  if [ -n "${_activate_detected_rock_root}" ] && [ -d "${_activate_detected_rock_root}" ]; then
    export ALPHADIANA_ROCK_ROOT="${_activate_detected_rock_root}"
  fi
fi

if [ -d "${ALPHADIANA_ROCK_ROOT:-${_activate_rock_root}}" ]; then
  if [ ! -f "${_activate_script_dir}/.rock_ports.env" ]; then
    echo "Warning: scripts/.rock_ports.env not found. Run 'python scripts/find_rock_ports.py --write-env scripts/.rock_ports.env' first." >&2
  fi
  # Reuse the shared ROCK helper so activate.sh and quickstart agree on the
  # dynamic config location and default local ports.
  # shellcheck disable=SC1091
  source "${_activate_script_dir}/rock_env.sh"
else
  echo "Warning: ROCK repository not found at ${_activate_rock_root}." >&2
  echo "Set ALPHADIANA_ROCK_ROOT=/abs/path/to/ROCK if this checkout does not vendor ref/ROCK." >&2
fi

# ── Cleanup temp vars ────────────────────────────────────────────────────────
unset _activate_default_env_name _activate_detected_rock_root _activate_env_file _activate_env_name _activate_project_root _activate_python _activate_rock_root _activate_script_dir

echo "AlphaDiana environment ready."
