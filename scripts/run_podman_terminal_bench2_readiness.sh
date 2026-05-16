#!/usr/bin/env bash
set -u
set -o pipefail

SCOPE="${1:-validate}"
RUN_PREFIX="${PODMAN_TB2_RUN_PREFIX:-podman_tb2_$(date +%Y%m%d_%H%M%S)}"
COMMAND_TIMEOUT_SECONDS="${PODMAN_TB2_COMMAND_TIMEOUT_SECONDS:-14400}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONFIG_DIR="${PODMAN_TB2_CONFIG_DIR:-$ROOT_DIR/configs/smokes/podman_terminal_bench2}"
STATUS_DIR="$ROOT_DIR/context/podman-terminal-bench2-readiness"
STATUS_FILE="$STATUS_DIR/run-status-${RUN_PREFIX}.tsv"
PREFLIGHT_STATUS_FILE="$STATUS_DIR/preflight-${RUN_PREFIX}.json"
PREFLIGHT_SCRIPT="$ROOT_DIR/scripts/preflight_podman_terminal_bench2_readiness.py"
AUDIT_SCRIPT="$ROOT_DIR/scripts/audit_podman_terminal_bench2_readiness.py"

cd "$ROOT_DIR" || exit 1

PRESERVE_TERMINAL_BENCH2_DIR="${TERMINAL_BENCH2_DIR:-}"
PRESERVE_OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
PRESERVE_OPENAI_API_KEY="${OPENAI_API_KEY:-}"
PRESERVE_OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-}"
PRESERVE_TB2_OPENCODE_RUNTIME_IMAGE="${TB2_OPENCODE_RUNTIME_IMAGE:-}"
PRESERVE_TB2_OPENCLAW_RUNTIME_IMAGE="${TB2_OPENCLAW_RUNTIME_IMAGE:-}"
PRESERVE_TB2_ZEROCLAW_RUNTIME_IMAGE="${TB2_ZEROCLAW_RUNTIME_IMAGE:-}"
PRESERVE_PODMAN_TB2_PREFLIGHT_PROVIDER_IMAGE="${PODMAN_TB2_PREFLIGHT_PROVIDER_IMAGE:-}"
PRESERVE_ALPHADIANA_TB2_LOGS_DIR="${ALPHADIANA_TB2_LOGS_DIR:-}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -n "$PRESERVE_TERMINAL_BENCH2_DIR" ]]; then export TERMINAL_BENCH2_DIR="$PRESERVE_TERMINAL_BENCH2_DIR"; fi
if [[ -n "$PRESERVE_OPENAI_BASE_URL" ]]; then export OPENAI_BASE_URL="$PRESERVE_OPENAI_BASE_URL"; fi
if [[ -n "$PRESERVE_OPENAI_API_KEY" ]]; then export OPENAI_API_KEY="$PRESERVE_OPENAI_API_KEY"; fi
if [[ -n "$PRESERVE_OPENAI_MODEL_NAME" ]]; then export OPENAI_MODEL_NAME="$PRESERVE_OPENAI_MODEL_NAME"; fi
if [[ -n "$PRESERVE_TB2_OPENCODE_RUNTIME_IMAGE" ]]; then export TB2_OPENCODE_RUNTIME_IMAGE="$PRESERVE_TB2_OPENCODE_RUNTIME_IMAGE"; fi
if [[ -n "$PRESERVE_TB2_OPENCLAW_RUNTIME_IMAGE" ]]; then export TB2_OPENCLAW_RUNTIME_IMAGE="$PRESERVE_TB2_OPENCLAW_RUNTIME_IMAGE"; fi
if [[ -n "$PRESERVE_TB2_ZEROCLAW_RUNTIME_IMAGE" ]]; then export TB2_ZEROCLAW_RUNTIME_IMAGE="$PRESERVE_TB2_ZEROCLAW_RUNTIME_IMAGE"; fi
if [[ -n "$PRESERVE_PODMAN_TB2_PREFLIGHT_PROVIDER_IMAGE" ]]; then export PODMAN_TB2_PREFLIGHT_PROVIDER_IMAGE="$PRESERVE_PODMAN_TB2_PREFLIGHT_PROVIDER_IMAGE"; fi
if [[ -n "$PRESERVE_ALPHADIANA_TB2_LOGS_DIR" ]]; then export ALPHADIANA_TB2_LOGS_DIR="$PRESERVE_ALPHADIANA_TB2_LOGS_DIR"; fi

export TB2_OPENCODE_RUNTIME_IMAGE="${TB2_OPENCODE_RUNTIME_IMAGE:-localhost/alphadiana/tb2-opencode-controller:latest}"
export TB2_OPENCLAW_RUNTIME_IMAGE="${TB2_OPENCLAW_RUNTIME_IMAGE:-localhost/alphadiana-openclaw-swebench-runtime-source:latest}"
export TB2_ZEROCLAW_RUNTIME_IMAGE="${TB2_ZEROCLAW_RUNTIME_IMAGE:-localhost/zeroclaw-reasoning:0.6.9}"
export PODMAN_TB2_PREFLIGHT_PROVIDER_IMAGE="${PODMAN_TB2_PREFLIGHT_PROVIDER_IMAGE:-$TB2_OPENCODE_RUNTIME_IMAGE}"
export ALPHADIANA_TB2_LOGS_DIR="${ALPHADIANA_TB2_LOGS_DIR:-$ROOT_DIR/logs/podman-terminal-bench2-readiness/task-logs/$RUN_PREFIX}"
case "$ALPHADIANA_TB2_LOGS_DIR" in
  /*) ;;
  *) export ALPHADIANA_TB2_LOGS_DIR="$ROOT_DIR/$ALPHADIANA_TB2_LOGS_DIR" ;;
esac

init_status() {
  mkdir -p "$STATUS_DIR" "$ROOT_DIR/logs" "$ALPHADIANA_TB2_LOGS_DIR"
  if [[ ! -f "$STATUS_FILE" ]]; then
    printf 'scope\trun_id\tconfig\texit_code\tlog_path\n' > "$STATUS_FILE"
  fi
}

discover_configs() {
  CONFIG_PATHS=()
  while IFS= read -r path; do
    CONFIG_PATHS+=("$path")
  done < <(find "$CONFIG_DIR" -maxdepth 1 -type f -name '*_pilot.yaml' | sort)
  if [[ ${#CONFIG_PATHS[@]} -eq 0 ]]; then
    printf 'No TerminalBench2 pilot configs found in %s\n' "${CONFIG_DIR#$ROOT_DIR/}" >&2
    return 2
  fi
}

run_suffix_for_config() {
  local config_path="$1"
  local filename
  filename="$(basename "$config_path")"
  printf '%s\n' "${filename%_pilot.yaml}"
}

validate_config() {
  init_status
  discover_configs || return "$?"
  local rc=0
  local config_path config_rc run_suffix
  for config_path in "${CONFIG_PATHS[@]}"; do
    run_suffix="$(run_suffix_for_config "$config_path")"
    printf 'Validate: %s\n' "${config_path#$ROOT_DIR/}"
    if python -m alphadiana.cli validate "$config_path"; then
      config_rc=0
    else
      config_rc=$?
      rc=$config_rc
    fi
    printf 'validate\t%s_%s\t%s\t%s\t%s\n' "$RUN_PREFIX" "$run_suffix" "${config_path#$ROOT_DIR/}" "$config_rc" "-" >> "$STATUS_FILE"
  done
  return "$rc"
}

run_preflight() {
  init_status
  if [[ ! -f "$PREFLIGHT_SCRIPT" ]]; then
    printf 'Preflight script not found: %s\n' "${PREFLIGHT_SCRIPT#$ROOT_DIR/}" >&2
    return 2
  fi
  local args=(
    "$PREFLIGHT_SCRIPT"
    --config-dir "$CONFIG_DIR"
    --output "$PREFLIGHT_STATUS_FILE"
  )
  if [[ "${PODMAN_TB2_REQUIRE_LOCAL_IMAGES:-}" == "1" ]]; then
    args+=(--require-local-images)
  fi
  python "${args[@]}"
}

run_config() {
  local config_path="$1"
  local run_id="$2"
  local log_path="$ROOT_DIR/logs/${run_id}.log"
  local output_dir="$ROOT_DIR/results/${run_id}"
  printf '\n=== pilot :: %s ===\n' "$run_id"
  printf 'Config: %s\n' "${config_path#$ROOT_DIR/}"
  (
    cd "$ROOT_DIR" || exit 1
    timeout --foreground "$COMMAND_TIMEOUT_SECONDS" \
      python -m alphadiana.cli run "$config_path" \
        --redo-all \
        -o "run_id=$run_id" \
        -o "output_dir=$output_dir" \
        2>&1
  ) | tee "$log_path"
  local rc=${PIPESTATUS[0]}
  printf 'pilot\t%s\t%s\t%s\t%s\n' "$run_id" "${config_path#$ROOT_DIR/}" "$rc" "logs/${run_id}.log" >> "$STATUS_FILE"
  return "$rc"
}

run_all_configs() {
  discover_configs || return "$?"
  local config_path run_suffix
  for config_path in "${CONFIG_PATHS[@]}"; do
    run_suffix="$(run_suffix_for_config "$config_path")"
    run_config "$config_path" "${RUN_PREFIX}_${run_suffix}" || return "$?"
  done
}

run_pilot() {
  validate_config || return "$?"
  run_preflight || return "$?"
  run_all_configs
}

run_audit() {
  init_status
  if [[ ! -f "$AUDIT_SCRIPT" ]]; then
    printf 'Audit script not found: %s\n' "${AUDIT_SCRIPT#$ROOT_DIR/}" >&2
    exit 1
  fi
  python "$AUDIT_SCRIPT" \
    --run-prefix "$RUN_PREFIX" \
    --config-dir "$CONFIG_DIR" \
    --results-dir "$ROOT_DIR/results" \
    --logs-dir "$ROOT_DIR/logs" \
    --output-dir "$STATUS_DIR" \
    --preflight-status-file "$PREFLIGHT_STATUS_FILE"
}

run_all() {
  run_pilot || return "$?"
  run_audit
}

main() {
  local rc=0
  case "$SCOPE" in
    validate) validate_config; rc=$? ;;
    preflight) run_preflight; rc=$? ;;
    pilot) run_pilot; rc=$? ;;
    audit) run_audit; rc=$? ;;
    all|auto) run_all; rc=$? ;;
    *)
      printf 'Usage: %s [validate|preflight|pilot|audit|all|auto]\n' "$0" >&2
      exit 2
      ;;
  esac
  printf '\nStatus file: %s\n' "${STATUS_FILE#$ROOT_DIR/}"
  return "$rc"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
