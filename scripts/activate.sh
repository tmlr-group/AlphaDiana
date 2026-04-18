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

# ── 3. Load .env (API keys) ──────────────────────────────────────────────────
if [ -f "${_activate_project_root}/.env" ]; then
  set -a
  source "${_activate_project_root}/.env"
  set +a
else
  echo "Warning: .env not found. Create it with OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL_NAME." >&2
fi

# ── 4. ROCK environment variables ────────────────────────────────────────────
if [ -d "${_activate_rock_root}" ]; then
  if [ ! -f "${_activate_script_dir}/.rock_ports.env" ]; then
    echo "Warning: scripts/.rock_ports.env not found. Run 'bash scripts/quickstart.sh' first." >&2
  fi
  # Reuse the shared ROCK helper so activate.sh and quickstart agree on the
  # dynamic config location and default local ports.
  # shellcheck disable=SC1091
  source "${_activate_script_dir}/rock_env.sh"
else
  echo "Warning: ROCK repository not found at ${_activate_rock_root}." >&2
fi

# ── Cleanup temp vars ────────────────────────────────────────────────────────
unset _activate_script_dir _activate_project_root _activate_rock_root

echo "AlphaDiana environment ready."
