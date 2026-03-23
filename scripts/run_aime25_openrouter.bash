#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 2 || "$1" != "--config" ]]; then
  echo "Usage: bash dev/run_aime25_openrouter.bash --config <path/to/config.yaml>" >&2
  exit 2
fi

bash "${SCRIPT_DIR}/run_aime25_openrouter_oneclick.sh" --config "$2"
