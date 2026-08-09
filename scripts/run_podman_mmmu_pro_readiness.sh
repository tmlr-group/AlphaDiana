#!/usr/bin/env bash
set -u
set -o pipefail

SCOPE="${1:-validate}"
RUN_PREFIX="${PODMAN_MMMU_RUN_PREFIX:-podman_mmmu_pro_$(date +%Y%m%d_%H%M%S)}"
COMMAND_TIMEOUT_SECONDS="${PODMAN_MMMU_COMMAND_TIMEOUT_SECONDS:-7200}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONFIG_DIR="${PODMAN_MMMU_CONFIG_DIR:-$ROOT_DIR/configs/macro_runs}"
STATUS_DIR="$ROOT_DIR/context/podman-mmmu-pro-readiness"
STATUS_FILE="$STATUS_DIR/run-status-${RUN_PREFIX}.tsv"
PREFLIGHT_STATUS_FILE="$STATUS_DIR/preflight-${RUN_PREFIX}.json"
AUDIT_SCRIPT="$ROOT_DIR/scripts/audit_podman_mmmu_pro_readiness.py"
PREFLIGHT_HELPER="$ROOT_DIR/scripts/podman_vlm_image_preflight.py"
PROVIDER_PREFLIGHT_IMAGE="${PODMAN_MMMU_PREFLIGHT_IMAGE:-docker.io/python:3.12-slim}"
PROVIDER_PREFLIGHT_NETWORK="${PODMAN_MMMU_PREFLIGHT_NETWORK:-host}"
PROVIDER_PREFLIGHT_TIMEOUT_SECONDS="${PODMAN_MMMU_PREFLIGHT_TIMEOUT_SECONDS:-120}"
PROVIDER_PREFLIGHT_MAX_TOKENS="${PODMAN_MMMU_PRO_MAX_TOKENS:-8192}"
PROVIDER_PREFLIGHT_IMAGE_URL="${PODMAN_MMMU_PRO_VLM_IMAGE_URL:-https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen3.5/demo/RealWorld/RealWorld-04.png}"

cd "$ROOT_DIR" || exit 1

PRESERVE_OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
PRESERVE_OPENAI_API_KEY="${OPENAI_API_KEY:-}"
PRESERVE_OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-}"
PRESERVE_HF_HOME="${HF_HOME:-}"
PRESERVE_HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -n "$PRESERVE_OPENAI_BASE_URL" ]]; then export OPENAI_BASE_URL="$PRESERVE_OPENAI_BASE_URL"; fi
if [[ -n "$PRESERVE_OPENAI_API_KEY" ]]; then export OPENAI_API_KEY="$PRESERVE_OPENAI_API_KEY"; fi
if [[ -n "$PRESERVE_OPENAI_MODEL_NAME" ]]; then export OPENAI_MODEL_NAME="$PRESERVE_OPENAI_MODEL_NAME"; fi
if [[ -n "$PRESERVE_HF_HOME" ]]; then export HF_HOME="$PRESERVE_HF_HOME"; fi
if [[ -n "$PRESERVE_HF_DATASETS_CACHE" ]]; then export HF_DATASETS_CACHE="$PRESERVE_HF_DATASETS_CACHE"; fi

export ALPHADIANA_OPENCLAW_PODMAN_IMAGE="${ALPHADIANA_OPENCLAW_PODMAN_IMAGE:-localhost/alphadiana-openclaw:latest}"
export ALPHADIANA_ZEROCLAW_PODMAN_IMAGE="${ALPHADIANA_ZEROCLAW_PODMAN_IMAGE:-localhost/zeroclaw-reasoning:0.6.9}"
export HF_HOME="${HF_HOME:-/tmp/alphadiana-podman-mmmu-hf-home}"
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-/tmp/alphadiana-podman-mmmu-hf-datasets}"

require_provider_env() {
  local missing=0
  local name
  for name in OPENAI_BASE_URL OPENAI_API_KEY OPENAI_MODEL_NAME; do
    if [[ -z "${!name:-}" ]]; then
      printf 'Missing required environment variable: %s\n' "$name" >&2
      missing=1
    fi
  done
  return "$missing"
}

provider_host() {
  python - "${OPENAI_BASE_URL:-}" <<'PY'
import sys
from urllib.parse import urlparse
print((urlparse(sys.argv[1]).hostname or "").lower())
PY
}

is_loopback_provider_base() {
  local host
  host="$(provider_host 2>/dev/null || true)"
  case "$host" in
    localhost|127.*|::1|0.0.0.0)
      return 0
      ;;
  esac
  return 1
}

reject_loopback_provider_base() {
  if is_loopback_provider_base && [[ "$PROVIDER_PREFLIGHT_NETWORK" != "host" ]]; then
    cat >&2 <<EOF
Refusing to launch with loopback OPENAI_BASE_URL from a non-host Podman network.
Set PODMAN_MMMU_PREFLIGHT_NETWORK=host or use a container-reachable provider URL.
EOF
    return 2
  fi
}

require_fully_qualified_image() {
  local image="$1"
  local registry="${image%%/*}"
  if [[ "$image" != */* || "$registry" != *.* && "$registry" != *:* && "$registry" != "localhost" ]]; then
    printf 'Podman preflight image must be fully qualified, got: %s\n' "$image" >&2
    return 2
  fi
}

append_no_proxy_host() {
  local var_name="$1"
  local host="$2"
  local current="${!var_name:-}"
  if [[ -z "$host" ]]; then
    return 0
  fi
  case ",$current," in
    *",$host,"*) ;;
    *) export "$var_name=${current:+$current,}$host" ;;
  esac
}

extend_no_proxy_for_provider() {
  local host
  host="$(provider_host 2>/dev/null || true)"
  append_no_proxy_host "NO_PROXY" "$host"
  append_no_proxy_host "no_proxy" "$host"
}

init_status() {
  mkdir -p "$STATUS_DIR" "$ROOT_DIR/logs"
  if [[ ! -f "$STATUS_FILE" ]]; then
    printf 'scope\trun_id\tconfig\texit_code\tlog_path\n' > "$STATUS_FILE"
  fi
}

cell_config() {
  local agent="$1"
  printf '%s/mmmu_pro_%s_qwen35_27b.yaml' "$CONFIG_DIR" "$agent"
}

preflight_provider_from_podman() {
  require_provider_env || return 2
  reject_loopback_provider_base || return "$?"
  require_fully_qualified_image "$PROVIDER_PREFLIGHT_IMAGE" || return "$?"
  if ! command -v podman >/dev/null 2>&1; then
    printf 'Podman VLM preflight failed: podman command not found.\n' >&2
    return 2
  fi
  if [[ ! -f "$PREFLIGHT_HELPER" ]]; then
    printf 'Podman VLM preflight failed: helper missing: %s\n' "${PREFLIGHT_HELPER#$ROOT_DIR/}" >&2
    return 2
  fi
  init_status
  extend_no_proxy_for_provider
  printf 'Preflight: probing thinking-mode image chat support from Podman for model %s\n' "${OPENAI_MODEL_NAME:-<unset>}"
  if ! timeout --foreground "$PROVIDER_PREFLIGHT_TIMEOUT_SECONDS" \
      podman run --rm --network "$PROVIDER_PREFLIGHT_NETWORK" \
        -e http_proxy= \
        -e https_proxy= \
        -e all_proxy= \
        -e HTTP_PROXY= \
        -e HTTPS_PROXY= \
        -e ALL_PROXY= \
        -e "NO_PROXY=${NO_PROXY:-}" \
        -e "no_proxy=${no_proxy:-}" \
        -e "OPENAI_BASE_URL=${OPENAI_BASE_URL:-}" \
        -e "OPENAI_API_KEY=${OPENAI_API_KEY:-}" \
        -e "OPENAI_MODEL_NAME=${OPENAI_MODEL_NAME:-}" \
        -e "PODMAN_MMMU_CONTAINER_ENGINE=podman" \
        -e "PODMAN_MMMU_NETWORK_MODE=${PROVIDER_PREFLIGHT_NETWORK}" \
        -e "PODMAN_MMMU_PRO_ENABLE_THINKING=1" \
        -e "PODMAN_MMMU_PRO_MAX_TOKENS=${PROVIDER_PREFLIGHT_MAX_TOKENS}" \
        -e "PODMAN_MMMU_PRO_VLM_IMAGE_URL=${PROVIDER_PREFLIGHT_IMAGE_URL}" \
        -e "PODMAN_MMMU_PRO_TOP_K=${PODMAN_MMMU_PRO_TOP_K:-20}" \
        -e "PODMAN_MMMU_PREFLIGHT_REQUEST_TIMEOUT=${PODMAN_MMMU_PREFLIGHT_REQUEST_TIMEOUT:-30}" \
        -v "$ROOT_DIR:/workspace:ro" \
        -w /workspace \
        "$PROVIDER_PREFLIGHT_IMAGE" \
        python scripts/podman_vlm_image_preflight.py | tee "$PREFLIGHT_STATUS_FILE"; then
    cat >&2 <<EOF
Podman VLM image preflight failed. The pilot was not launched.
Preflight status: ${PREFLIGHT_STATUS_FILE#$ROOT_DIR/}
EOF
    return 2
  fi
  printf 'Preflight passed: thinking-mode VLM image_url and data URL paths are reachable from Podman.\n'
}

validate_matrix() {
  require_provider_env || return 2
  init_status
  local agent config rc
  for agent in openclaw zeroclaw opencode; do
    config="$(cell_config "$agent")"
    printf 'Validate: %s\n' "${config#$ROOT_DIR/}"
    if python -m alphadiana.cli validate "$config"; then
      rc=0
    else
      rc=$?
    fi
    printf 'validate\t%s_%s_mmmu_pro\t%s\t%s\t%s\n' "$RUN_PREFIX" "$agent" "${config#$ROOT_DIR/}" "$rc" "-" >> "$STATUS_FILE"
    if [[ "$rc" -ne 0 ]]; then
      return "$rc"
    fi
  done
}

run_config() {
  local group="$1"
  local config="$2"
  local run_id="$3"
  local log_path="$ROOT_DIR/logs/${run_id}.log"
  local output_dir="$ROOT_DIR/results/${run_id}"
  printf '\n=== %s :: %s ===\n' "$group" "$run_id"
  printf 'Config: %s\n' "${config#$ROOT_DIR/}"
  (
    cd "$ROOT_DIR" || exit 1
    timeout --foreground "$COMMAND_TIMEOUT_SECONDS" \
      python -m alphadiana.cli run "$config" \
        --redo-all \
        -o "run_id=$run_id" \
        -o "output_dir=$output_dir" \
        -o 'benchmark.config.dataset_indices=[0,1,2]' \
        2>&1
  ) | tee "$log_path"
  local rc=${PIPESTATUS[0]}
  printf '%s\t%s\t%s\t%s\t%s\n' "$group" "$run_id" "${config#$ROOT_DIR/}" "$rc" "logs/${run_id}.log" >> "$STATUS_FILE"
  return "$rc"
}

run_pilot_cells() {
  extend_no_proxy_for_provider
  init_status
  local agent config run_id
  for agent in openclaw zeroclaw opencode; do
    config="$(cell_config "$agent")"
    run_id="${RUN_PREFIX}_${agent}_mmmu_pro"
    run_config "pilot" "$config" "$run_id" || return "$?"
  done
}

run_pilot() {
  require_provider_env || return 2
  preflight_provider_from_podman || return "$?"
  run_pilot_cells
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
  validate_matrix || return "$?"
  preflight_provider_from_podman || return "$?"
  run_pilot_cells || return "$?"
  run_audit
}

run_full() {
  if [[ "${PODMAN_MMMU_ALLOW_FULL:-}" != "1" ]]; then
    printf 'Full execution is gated. Set PODMAN_MMMU_ALLOW_FULL=1 only after a passing pilot audit.\n' >&2
    exit 2
  fi
  preflight_provider_from_podman || exit "$?"
  printf 'No full MMMU-Pro configs are defined by this Phase 6 pilot slice.\n' >&2
  exit 2
}

main() {
  local rc=0
  case "$SCOPE" in
    validate) validate_matrix; rc=$? ;;
    preflight) preflight_provider_from_podman; rc=$? ;;
    pilot) run_pilot; rc=$? ;;
    audit) run_audit; rc=$? ;;
    all|auto) run_all; rc=$? ;;
    full) run_full; rc=$? ;;
    *)
      printf 'Usage: %s [validate|preflight|pilot|audit|all|auto|full]\n' "$0" >&2
      exit 2
      ;;
  esac
  printf '\nStatus file: %s\n' "${STATUS_FILE#$ROOT_DIR/}"
  return "$rc"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
