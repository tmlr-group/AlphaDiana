#!/usr/bin/env bash
# Launch one AIME 2026 pass@64 run (openclaw | opencode | zeroclaw).
# --smoke runs 1 task x 2 samples to prove the pipeline and sampling diversity.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT="${1:-}"
MODE="${2:-}"
RUN_VERSION="${RUN_VERSION:-v01}"

usage() {
  printf 'Usage: %s {openclaw|opencode|zeroclaw} [--smoke]\n' "$0" >&2
  exit 2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ "$AGENT" =~ ^(openclaw|opencode|zeroclaw)$ ]] || usage
[[ -z "$MODE" || "$MODE" == "--smoke" ]] || usage
[[ "$RUN_VERSION" =~ ^v[0-9][0-9]$ ]] || fail "RUN_VERSION must match vNN"

require_env() {
  local name="$1"
  [[ -n "${!name:-}" ]] || fail "missing environment variable: $name"
}

if [[ -z "${ALPHADIANA_ROOT:-}" && -d "$BUNDLE_ROOT/../../alphadiana" ]]; then
  ALPHADIANA_ROOT="$(cd "$BUNDLE_ROOT/../.." && pwd)"
  export ALPHADIANA_ROOT
fi

require_env ALPHADIANA_ROOT
require_env OPENAI_BASE_URL
require_env OPENAI_API_KEY
require_env ROCK_BASE_URL
require_env ROCK_PROXY_URL
if [[ "$AGENT" == "openclaw" ]]; then
  require_env OPENCLAW_GATEWAY_TOKEN
fi

mkdir -p "$ALPHADIANA_ROOT/logs"

RUN_ID="full_aime2026_pass64_${AGENT}_qwen35_27b_${RUN_VERSION}"
CONFIG_PATH="$BUNDLE_ROOT/configs/$AGENT.yaml"
EXTRA_ARGS=(-o "run_id=$RUN_ID")
if [[ "$MODE" == "--smoke" ]]; then
  RUN_ID="smoke_aime2026_pass64_${AGENT}_qwen35_27b_${RUN_VERSION}"
  EXTRA_ARGS=(
    -o "run_id=$RUN_ID"
    -o "benchmark.config.max_tasks=1"
    -o "num_samples=2"
    -o "max_concurrent=2"
  )
fi

(
  cd "$ALPHADIANA_ROOT"
  python -m alphadiana.cli run "$CONFIG_PATH" "${EXTRA_ARGS[@]}"
) 2>&1 | tee "$ALPHADIANA_ROOT/logs/$RUN_ID.log"
exit "${PIPESTATUS[0]}"
