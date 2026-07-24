#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT="${1:-}"
MODE="${2:-}"
RUN_VERSION="${RUN_VERSION:-v01}"

usage() {
  printf 'Usage: %s {directllm|openclaw|opencode|zeroclaw} [--smoke]\n' "$0" >&2
  exit 2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ "$AGENT" =~ ^(directllm|openclaw|opencode|zeroclaw)$ ]] || usage
[[ -z "$MODE" || "$MODE" == "--smoke" ]] || usage
[[ "$RUN_VERSION" =~ ^v[0-9][0-9]$ ]] || fail "RUN_VERSION must match vNN"

require_env() {
  local name="$1"
  [[ -n "${!name:-}" ]] || fail "missing environment variable: $name"
}

require_env ALPHADIANA_ROOT
require_env OPENAI_BASE_URL
require_env OPENAI_API_KEY

mkdir -p "$ALPHADIANA_ROOT/logs"

if [[ "$AGENT" == "directllm" ]]; then
  [[ -z "$MODE" ]] || fail "DirectLLM smoke slicing depends on the installed SWE-agent version; use the runbook guidance."
  require_env DIRECTLLM_SWE_VERIFIED_ROOT
  SWEBENCH_ROOT="$DIRECTLLM_SWE_VERIFIED_ROOT"
  [[ -x "$SWEBENCH_ROOT/.venv/bin/sweagent" ]] || fail "missing $SWEBENCH_ROOT/.venv/bin/sweagent"
  [[ -d "$SWEBENCH_ROOT/SWE-agent" ]] || fail "missing $SWEBENCH_ROOT/SWE-agent"

  RUN_ID="full_swe_bench_verified_mini_directllm_gemma4_31b_${RUN_VERSION}"
  (
    cd "$SWEBENCH_ROOT/SWE-agent"
    export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1}"
    OPENAI_API_KEY="$OPENAI_API_KEY" OPENAI_BASE_URL="$OPENAI_BASE_URL" \
      ../.venv/bin/sweagent run-batch \
        --config config/default.yaml \
        --output_dir "../sweagent_results/$RUN_ID" \
        --num_workers 4 \
        --random_delay_multiplier 0 \
        --instances.type swe_bench \
        --instances.subset verified \
        --instances.split test \
        --instances.path_override MariusHobbhahn/swe-bench-verified-mini \
        --instances.shuffle=False \
        --instances.evaluate=False \
        --instances.deployment.type docker \
        --instances.deployment.startup_timeout 1800 \
        --agent.model.name openai/gemma-4-31b-it \
        --agent.model.api_base "$OPENAI_BASE_URL" \
        --agent.model.api_key "$OPENAI_API_KEY" \
        --agent.model.temperature 0.0 \
        --agent.model.top_p 0.95 \
        --agent.model.max_output_tokens 131072 \
        --agent.model.per_instance_cost_limit 0 \
        --agent.model.total_cost_limit 0 \
        --agent.model.per_instance_call_limit 80 \
        --progress_bar False

    cd "$SWEBENCH_ROOT"
    ./.venv/bin/python -m swebench.harness.run_evaluation \
      --dataset_name MariusHobbhahn/swe-bench-verified-mini \
      --split test \
      --predictions_path "sweagent_results/$RUN_ID/preds.json" \
      --run_id "$RUN_ID" \
      --max_workers 4
  ) 2>&1 | tee "$ALPHADIANA_ROOT/logs/$RUN_ID.log"
  exit "${PIPESTATUS[0]}"
fi

require_env SWE_CONTAINER_OPENAI_BASE_URL
require_env OPENCLAW_GATEWAY_TOKEN

RUN_ID="full_swe_bench_verified_mini_${AGENT}_gemma4_31b_${RUN_VERSION}"
CONFIG_PATH="$BUNDLE_ROOT/configs/$AGENT.yaml"
EXTRA_ARGS=(-o "run_id=$RUN_ID")
if [[ "$MODE" == "--smoke" ]]; then
  RUN_ID="smoke_swe_bench_verified_mini_${AGENT}_gemma4_31b_${RUN_VERSION}"
  EXTRA_ARGS=(
    -o "run_id=$RUN_ID"
    -o "benchmark.config.max_tasks=1"
    -o "max_concurrent=1"
  )
fi

(
  cd "$ALPHADIANA_ROOT"
  python -m alphadiana.cli run "$CONFIG_PATH" "${EXTRA_ARGS[@]}"
) 2>&1 | tee "$ALPHADIANA_ROOT/logs/$RUN_ID.log"
exit "${PIPESTATUS[0]}"
