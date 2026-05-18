#!/usr/bin/env bash
set -u
set -o pipefail

SCOPE="${1:-validate}"
RUN_PREFIX="${PODMAN_SWE_RUN_PREFIX:-podman_swe_verified_$(date +%Y%m%d_%H%M%S)}"
COMMAND_TIMEOUT_SECONDS="${PODMAN_SWE_COMMAND_TIMEOUT_SECONDS:-28800}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONFIG_DIR="${PODMAN_SWE_CONFIG_DIR:-$ROOT_DIR/configs/smokes/podman_swe_verified_readiness}"
STATUS_DIR="$ROOT_DIR/context/podman-swe-verified-readiness"
STATUS_FILE="$STATUS_DIR/run-status-${RUN_PREFIX}.tsv"
PREFLIGHT_STATUS_FILE="$STATUS_DIR/preflight-${RUN_PREFIX}.json"
PREFLIGHT_SCRIPT="$ROOT_DIR/scripts/preflight_podman_swe_verified_readiness.py"
AUDIT_SCRIPT="$ROOT_DIR/scripts/audit_podman_swe_verified_readiness.py"
REDO_ALL="${PODMAN_SWE_REDO_ALL:-1}"
MAX_CONCURRENT="${PODMAN_SWE_MAX_CONCURRENT:-1}"

cd "$ROOT_DIR" || exit 1

PRESERVE_OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
PRESERVE_OPENAI_API_KEY="${OPENAI_API_KEY:-}"
PRESERVE_OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-}"
PRESERVE_ALPHADIANA_PODMAN_SOCKET="${ALPHADIANA_PODMAN_SOCKET:-}"
PRESERVE_ALPHADIANA_PODMAN_DOCKER_API_VERSION="${ALPHADIANA_PODMAN_DOCKER_API_VERSION:-}"
PRESERVE_ALPHADIANA_VLLM_LOG="${ALPHADIANA_VLLM_LOG:-}"
PRESERVE_PODMAN_SWE_CONTAINER_OPENAI_BASE_URL="${PODMAN_SWE_CONTAINER_OPENAI_BASE_URL:-}"
PRESERVE_PODMAN_SWE_ZEROCLAW_BINARY_SOURCE_IMAGE="${PODMAN_SWE_ZEROCLAW_BINARY_SOURCE_IMAGE:-}"
PRESERVE_PODMAN_SWE_PREFLIGHT_PROVIDER_IMAGE="${PODMAN_SWE_PREFLIGHT_PROVIDER_IMAGE:-}"
PRESERVE_PODMAN_SWE_RUN_PREFIX="${PODMAN_SWE_RUN_PREFIX:-}"
PRESERVE_PODMAN_SWE_REDO_ALL="${PODMAN_SWE_REDO_ALL:-}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -n "$PRESERVE_OPENAI_BASE_URL" ]]; then export OPENAI_BASE_URL="$PRESERVE_OPENAI_BASE_URL"; fi
if [[ -n "$PRESERVE_OPENAI_API_KEY" ]]; then export OPENAI_API_KEY="$PRESERVE_OPENAI_API_KEY"; fi
if [[ -n "$PRESERVE_OPENAI_MODEL_NAME" ]]; then export OPENAI_MODEL_NAME="$PRESERVE_OPENAI_MODEL_NAME"; fi
if [[ -n "$PRESERVE_ALPHADIANA_PODMAN_SOCKET" ]]; then export ALPHADIANA_PODMAN_SOCKET="$PRESERVE_ALPHADIANA_PODMAN_SOCKET"; fi
if [[ -n "$PRESERVE_ALPHADIANA_PODMAN_DOCKER_API_VERSION" ]]; then export ALPHADIANA_PODMAN_DOCKER_API_VERSION="$PRESERVE_ALPHADIANA_PODMAN_DOCKER_API_VERSION"; fi
if [[ -n "$PRESERVE_ALPHADIANA_VLLM_LOG" ]]; then export ALPHADIANA_VLLM_LOG="$PRESERVE_ALPHADIANA_VLLM_LOG"; fi
if [[ -n "$PRESERVE_PODMAN_SWE_CONTAINER_OPENAI_BASE_URL" ]]; then export PODMAN_SWE_CONTAINER_OPENAI_BASE_URL="$PRESERVE_PODMAN_SWE_CONTAINER_OPENAI_BASE_URL"; fi
if [[ -n "$PRESERVE_PODMAN_SWE_ZEROCLAW_BINARY_SOURCE_IMAGE" ]]; then export PODMAN_SWE_ZEROCLAW_BINARY_SOURCE_IMAGE="$PRESERVE_PODMAN_SWE_ZEROCLAW_BINARY_SOURCE_IMAGE"; fi
if [[ -n "$PRESERVE_PODMAN_SWE_PREFLIGHT_PROVIDER_IMAGE" ]]; then export PODMAN_SWE_PREFLIGHT_PROVIDER_IMAGE="$PRESERVE_PODMAN_SWE_PREFLIGHT_PROVIDER_IMAGE"; fi
if [[ -n "$PRESERVE_PODMAN_SWE_RUN_PREFIX" ]]; then export PODMAN_SWE_RUN_PREFIX="$PRESERVE_PODMAN_SWE_RUN_PREFIX"; fi
if [[ -n "$PRESERVE_PODMAN_SWE_REDO_ALL" ]]; then export PODMAN_SWE_REDO_ALL="$PRESERVE_PODMAN_SWE_REDO_ALL"; fi

export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:8011/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-EMPTY}"
export OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-Qwen/Qwen3.5-27B}"
export ALPHADIANA_PODMAN_SOCKET="${ALPHADIANA_PODMAN_SOCKET:-/run/user/$(id -u)/podman/podman.sock}"
export PODMAN_SWE_TASK_NETWORK_MODE="${PODMAN_SWE_TASK_NETWORK_MODE:-host}"
export PODMAN_SWE_PREFLIGHT_PROVIDER_NETWORK="${PODMAN_SWE_PREFLIGHT_PROVIDER_NETWORK:-$PODMAN_SWE_TASK_NETWORK_MODE}"
export PODMAN_SWE_ZEROCLAW_BINARY_SOURCE_IMAGE="${PODMAN_SWE_ZEROCLAW_BINARY_SOURCE_IMAGE:-localhost/zeroclaw-reasoning:0.6.9}"
export PODMAN_SWE_PREFLIGHT_PROVIDER_IMAGE="${PODMAN_SWE_PREFLIGHT_PROVIDER_IMAGE:-docker.io/library/python:3.12-slim}"
export PODMAN_SWE_RUN_PREFIX="$RUN_PREFIX"
export PODMAN_SWE_REDO_ALL="${PODMAN_SWE_REDO_ALL:-$REDO_ALL}"

derive_container_url() {
  python - "$OPENAI_BASE_URL" <<'PY'
import sys
from urllib.parse import urlparse, urlunparse

raw = sys.argv[1]
parsed = urlparse(raw)
host = parsed.hostname or ""
if host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
    netloc = "host.containers.internal"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    print(urlunparse((parsed.scheme, netloc, parsed.path, "", "", "")))
else:
    print(raw)
PY
}

if [[ -z "${PODMAN_SWE_CONTAINER_OPENAI_BASE_URL:-}" ]]; then
  if [[ "$PODMAN_SWE_TASK_NETWORK_MODE" == "host" ]]; then
    export PODMAN_SWE_CONTAINER_OPENAI_BASE_URL="$OPENAI_BASE_URL"
  else
    export PODMAN_SWE_CONTAINER_OPENAI_BASE_URL="$(derive_container_url)"
  fi
fi

init_status() {
  mkdir -p "$STATUS_DIR" "$ROOT_DIR/logs"
  if [[ ! -f "$STATUS_FILE" ]]; then
    printf 'scope\ttier\trun_id\tconfig\texit_code\tlog_path\tmax_concurrent\n' > "$STATUS_FILE"
  fi
}

discover_configs_for_tier() {
  local tier="$1"
  CONFIG_PATHS=()
  while IFS= read -r path; do
    CONFIG_PATHS+=("$path")
  done < <(find "$CONFIG_DIR" -maxdepth 1 -type f -name "*_${tier}.yaml" | sort)
  if [[ ${#CONFIG_PATHS[@]} -eq 0 ]]; then
    printf 'No SWE-bench Podman configs found for tier %s in %s\n' "$tier" "${CONFIG_DIR#$ROOT_DIR/}" >&2
    return 2
  fi
}

discover_all_configs() {
  CONFIG_PATHS=()
  while IFS= read -r path; do
    CONFIG_PATHS+=("$path")
  done < <(find "$CONFIG_DIR" -maxdepth 1 -type f -name "*.yaml" | sort)
  if [[ ${#CONFIG_PATHS[@]} -eq 0 ]]; then
    printf 'No SWE-bench Podman configs found in %s\n' "${CONFIG_DIR#$ROOT_DIR/}" >&2
    return 2
  fi
}

run_suffix_for_config() {
  local config_path="$1"
  local filename
  filename="$(basename "$config_path")"
  printf '%s\n' "${filename%.yaml}"
}

validate_config() {
  init_status
  discover_all_configs || return "$?"
  local rc=0
  local config_path config_rc run_suffix tier
  for config_path in "${CONFIG_PATHS[@]}"; do
    run_suffix="$(run_suffix_for_config "$config_path")"
    tier="${run_suffix##*_}"
    printf 'Validate: %s\n' "${config_path#$ROOT_DIR/}"
    if python -m alphadiana.cli validate "$config_path"; then
      config_rc=0
    else
      config_rc=$?
      rc=$config_rc
    fi
    printf 'validate\t%s\t%s_%s\t%s\t%s\t%s\t%s\n' "$tier" "$RUN_PREFIX" "$run_suffix" "${config_path#$ROOT_DIR/}" "$config_rc" "-" "$MAX_CONCURRENT" >> "$STATUS_FILE"
  done
  return "$rc"
}

run_preflight() {
  init_status
  if [[ ! -f "$PREFLIGHT_SCRIPT" ]]; then
    printf 'Preflight script not found: %s\n' "${PREFLIGHT_SCRIPT#$ROOT_DIR/}" >&2
    return 2
  fi
  local rc
  python "$PREFLIGHT_SCRIPT" \
    --config-dir "$CONFIG_DIR" \
    --output "$PREFLIGHT_STATUS_FILE" \
    --root "$ROOT_DIR"
  rc=$?
  printf 'preflight\t-\t%s\t%s\t%s\t%s\t%s\n' "$RUN_PREFIX" "${PREFLIGHT_SCRIPT#$ROOT_DIR/}" "$rc" "${PREFLIGHT_STATUS_FILE#$ROOT_DIR/}" "$MAX_CONCURRENT" >> "$STATUS_FILE"
  return "$rc"
}

run_config() {
  local tier="$1"
  local config_path="$2"
  local run_suffix run_id log_path output_dir checkpoint_mode
  run_suffix="$(run_suffix_for_config "$config_path")"
  run_id="${RUN_PREFIX}_${run_suffix}"
  log_path="$ROOT_DIR/logs/${run_id}.log"
  output_dir="$ROOT_DIR/results/${run_id}"
  local redo_args=()
  checkpoint_mode="redo-all"
  case "${PODMAN_SWE_REDO_ALL:-1}" in
    0|false|False|FALSE|no|No|NO)
      checkpoint_mode="resume"
      ;;
    *)
      redo_args+=(--redo-all)
      ;;
  esac
  printf '\n=== %s :: %s ===\n' "$tier" "$run_id"
  printf 'Config: %s\n' "${config_path#$ROOT_DIR/}"
  printf 'Provider(host): %s\n' "$OPENAI_BASE_URL"
  printf 'Provider(container): %s\n' "$PODMAN_SWE_CONTAINER_OPENAI_BASE_URL"
  printf 'Checkpoint mode: %s\n' "$checkpoint_mode"
  (
    cd "$ROOT_DIR" || exit 1
    timeout --foreground "$COMMAND_TIMEOUT_SECONDS" \
      python -m alphadiana.cli run "$config_path" \
        "${redo_args[@]}" \
        -o "run_id=$run_id" \
        -o "output_dir=$output_dir" \
        -o "max_concurrent=$MAX_CONCURRENT" \
        2>&1
  ) | tee "$log_path"
  local rc=${PIPESTATUS[0]}
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$tier" "$tier" "$run_id" "${config_path#$ROOT_DIR/}" "$rc" "logs/${run_id}.log" "$MAX_CONCURRENT" >> "$STATUS_FILE"
  return "$rc"
}

run_tier_only() {
  local tier="$1"
  discover_configs_for_tier "$tier" || return "$?"
  local config_path
  for config_path in "${CONFIG_PATHS[@]}"; do
    run_config "$tier" "$config_path" || return "$?"
  done
}

run_tier() {
  local tier="$1"
  validate_config || return "$?"
  run_preflight || return "$?"
  run_tier_only "$tier"
}

run_audit() {
  init_status
  if [[ ! -f "$AUDIT_SCRIPT" ]]; then
    printf 'Audit script not found: %s\n' "${AUDIT_SCRIPT#$ROOT_DIR/}" >&2
    return 2
  fi
  local tiers_arg="${1:-${PODMAN_SWE_AUDIT_TIERS:-}}"
  local args=(
    "$AUDIT_SCRIPT"
    --run-prefix "$RUN_PREFIX"
    --config-dir "$CONFIG_DIR"
    --results-dir "$ROOT_DIR/results"
    --logs-dir "$ROOT_DIR/logs"
    --output-dir "$STATUS_DIR"
    --root "$ROOT_DIR"
    --preflight-status-file "$PREFLIGHT_STATUS_FILE"
  )
  if [[ -n "$tiers_arg" ]]; then
    args+=(--tiers "$tiers_arg")
  fi
  python "${args[@]}"
}

run_auto() {
  validate_config || return "$?"
  run_preflight || return "$?"
  local completed=""
  local tier
  for tier in smoke pilot32 long64 sample128; do
    run_tier_only "$tier" || return "$?"
    if [[ -z "$completed" ]]; then
      completed="$tier"
    else
      completed="$completed,$tier"
    fi
    run_audit "$completed" || return "$?"
  done
}

main() {
  local rc=0
  case "$SCOPE" in
    validate) validate_config; rc=$? ;;
    preflight) run_preflight; rc=$? ;;
    smoke|pilot32|long64|sample128) run_tier "$SCOPE"; rc=$? ;;
    audit) shift; run_audit "$*"; rc=$? ;;
    all|auto) run_auto; rc=$? ;;
    *)
      printf 'Usage: %s [validate|preflight|smoke|pilot32|long64|sample128|audit|all|auto]\n' "$0" >&2
      exit 2
      ;;
  esac
  printf '\nStatus file: %s\n' "${STATUS_FILE#$ROOT_DIR/}"
  return "$rc"
}

main "$@"
