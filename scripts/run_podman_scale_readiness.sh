#!/usr/bin/env bash
set -u
set -o pipefail

SCOPE="${1:-pilot}"
RUN_PREFIX="${PODMAN_SCALE_RUN_PREFIX:-podman_scale_$(date +%Y%m%d_%H%M%S)}"
COMMAND_TIMEOUT_SECONDS="${PODMAN_SCALE_COMMAND_TIMEOUT_SECONDS:-7200}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONFIG_DIR="${PODMAN_SCALE_CONFIG_DIR:-$ROOT_DIR/configs/smokes/podman_scale_readiness}"
STATUS_DIR="$ROOT_DIR/context/podman-scale-readiness"
STATUS_FILE="$STATUS_DIR/run-status-${RUN_PREFIX}.tsv"
AUDIT_SCRIPT="$ROOT_DIR/scripts/audit_podman_scale_readiness.py"
PROVIDER_PREFLIGHT_IMAGE="${PODMAN_SCALE_PREFLIGHT_IMAGE:-docker.io/curlimages/curl:8.11.1}"
PROVIDER_PREFLIGHT_NETWORK="${PODMAN_SCALE_PREFLIGHT_NETWORK:-host}"
PROVIDER_PREFLIGHT_TIMEOUT_SECONDS="${PODMAN_SCALE_PREFLIGHT_TIMEOUT_SECONDS:-120}"
PROVIDER_PREFLIGHT_CURL_TIMEOUT_SECONDS="${PODMAN_SCALE_PREFLIGHT_CURL_TIMEOUT_SECONDS:-15}"

cd "$ROOT_DIR" || exit 1

PRESERVE_OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
PRESERVE_OPENAI_API_KEY="${OPENAI_API_KEY:-}"
PRESERVE_OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-}"
PRESERVE_OPENCLAW_GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-}"
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
if [[ -n "$PRESERVE_OPENCLAW_GATEWAY_TOKEN" ]]; then export OPENCLAW_GATEWAY_TOKEN="$PRESERVE_OPENCLAW_GATEWAY_TOKEN"; fi
if [[ -n "$PRESERVE_HF_HOME" ]]; then export HF_HOME="$PRESERVE_HF_HOME"; fi
if [[ -n "$PRESERVE_HF_DATASETS_CACHE" ]]; then export HF_DATASETS_CACHE="$PRESERVE_HF_DATASETS_CACHE"; fi

export ALPHADIANA_OPENCLAW_PODMAN_IMAGE="${ALPHADIANA_OPENCLAW_PODMAN_IMAGE:-localhost/alphadiana-openclaw:latest}"
export ALPHADIANA_ZEROCLAW_PODMAN_IMAGE="${ALPHADIANA_ZEROCLAW_PODMAN_IMAGE:-localhost/zeroclaw-reasoning:0.6.9}"
export HF_HOME="${HF_HOME:-/tmp/alphadiana-podman-scale-hf-home}"
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-/tmp/alphadiana-podman-scale-hf-datasets}"

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

require_openclaw_gateway_token() {
  if python - "${OPENCLAW_GATEWAY_TOKEN:-}" <<'PY'
import sys

from alphadiana.utils.openclaw_security import is_weak_openclaw_gateway_token

raise SystemExit(1 if is_weak_openclaw_gateway_token(sys.argv[1]) else 0)
PY
  then
    return 0
  fi
  cat >&2 <<'EOF'
Missing or weak OPENCLAW_GATEWAY_TOKEN.
Generate one before validation or execution:
  export OPENCLAW_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
EOF
  return 2
}

provider_url_part() {
  local part="$1"
  python - "$part" "${OPENAI_BASE_URL:-}" <<'PY'
import sys
from urllib.parse import urlparse

part, raw = sys.argv[1], sys.argv[2]
parsed = urlparse(raw)
if part == "host":
    print((parsed.hostname or "").lower())
elif part == "models_url":
    if not parsed.scheme or not parsed.netloc:
        raise SystemExit(2)
    print(raw.rstrip("/") + "/models")
else:
    raise SystemExit(2)
PY
}

is_loopback_provider_base() {
  local host
  host="$(provider_url_part host 2>/dev/null || true)"
  case "$host" in
    localhost|127.*|::1|0.0.0.0)
      return 0
      ;;
  esac
  return 1
}

warn_loopback_provider_base() {
  if is_loopback_provider_base; then
    if [[ "$PROVIDER_PREFLIGHT_NETWORK" == "host" ]]; then
      cat >&2 <<EOF
Notice: OPENAI_BASE_URL=${OPENAI_BASE_URL:-<unset>} is loopback and is only valid
for Phase 5 Podman runs because the provider preflight and pilot configs use
Podman host networking on this machine.
EOF
      return 0
    fi
    cat >&2 <<EOF
Warning: Phase 5 Podman scale-readiness runs use provider URLs from inside
Podman containers. OPENAI_BASE_URL=${OPENAI_BASE_URL:-<unset>} is loopback;
inside a container, localhost/127.0.0.1 refers to the container itself.
Use Podman host networking for this local provider, or set a non-loopback
container-reachable host gateway URL.
EOF
  fi
}

reject_loopback_provider_base() {
  if is_loopback_provider_base; then
    if [[ "$PROVIDER_PREFLIGHT_NETWORK" == "host" ]]; then
      return 0
    fi
    cat >&2 <<EOF
Refusing to launch Phase 5 Podman container runs with OPENAI_BASE_URL=${OPENAI_BASE_URL:-<unset>}.
In a Podman container, localhost/127.0.0.1 refers to the container itself,
not the host provider unless the Podman run uses --network host. Set
PODMAN_SCALE_PREFLIGHT_NETWORK=host and use host-network Phase 5 configs for
this local provider, or set OPENAI_BASE_URL to a non-loopback host gateway URL.
EOF
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
    *",$host,"*)
      ;;
    *)
      if [[ -n "$current" ]]; then
        export "$var_name=$current,$host"
      else
        export "$var_name=$host"
      fi
      ;;
  esac
}

extend_no_proxy_for_provider() {
  local host
  host="$(provider_url_part host 2>/dev/null || true)"
  append_no_proxy_host "NO_PROXY" "$host"
  append_no_proxy_host "no_proxy" "$host"
}

preflight_provider_from_podman() {
  require_provider_env || return 2
  reject_loopback_provider_base || return "$?"
  if ! command -v podman >/dev/null 2>&1; then
    printf 'Podman provider preflight failed: podman command not found.\n' >&2
    return 2
  fi

  local models_url
  if ! models_url="$(provider_url_part models_url 2>/dev/null)"; then
    printf 'Podman provider preflight failed: OPENAI_BASE_URL is not a valid URL: %s\n' "${OPENAI_BASE_URL:-<unset>}" >&2
    return 2
  fi
  extend_no_proxy_for_provider

  printf 'Preflight: probing provider from a Podman container: %s\n' "$models_url"
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
        "$PROVIDER_PREFLIGHT_IMAGE" \
        -fsS --max-time "$PROVIDER_PREFLIGHT_CURL_TIMEOUT_SECONDS" "$models_url" >/dev/null; then
    cat >&2 <<EOF
Podman provider preflight failed.
The provider base must be reachable from inside Podman containers.
Current OPENAI_BASE_URL=${OPENAI_BASE_URL:-<unset>}
Preflight image: ${PROVIDER_PREFLIGHT_IMAGE}
Network: ${PROVIDER_PREFLIGHT_NETWORK}
Expected for this host:
  export OPENAI_BASE_URL=http://localhost:8011/v1
  export PODMAN_SCALE_PREFLIGHT_NETWORK=host
or use another container-reachable host gateway URL with matching Podman
network settings.
EOF
    return 2
  fi
  printf 'Preflight passed: provider is reachable from Podman.\n'
}

init_status() {
  mkdir -p "$STATUS_DIR" "$ROOT_DIR/logs"
  if [[ ! -f "$STATUS_FILE" ]]; then
    printf 'scope\trun_id\tconfig\texit_code\tlog_path\n' > "$STATUS_FILE"
  fi
}

cell_config() {
  local agent="$1"
  local benchmark="$2"
  printf '%s/%s_%s_pilot.yaml' "$CONFIG_DIR" "$agent" "$benchmark"
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
        -o strict_report=true \
        2>&1
  ) | tee "$log_path"
  local rc=${PIPESTATUS[0]}
  printf '%s\t%s\t%s\t%s\t%s\n' "$group" "$run_id" "${config#$ROOT_DIR/}" "$rc" "logs/${run_id}.log" >> "$STATUS_FILE"
  return "$rc"
}

validate_matrix() {
  require_provider_env || return 2
  require_openclaw_gateway_token || return 2
  warn_loopback_provider_base
  init_status
  local agent benchmark config rc
  for agent in openclaw zeroclaw opencode; do
    for benchmark in aime gpqa hle imo; do
      config="$(cell_config "$agent" "$benchmark")"
      printf 'Validate: %s\n' "${config#$ROOT_DIR/}"
      if python -m alphadiana.cli validate "$config"; then
        rc=0
      else
        rc=$?
      fi
      printf 'validate\t%s_%s_%s\t%s\t%s\t%s\n' "$RUN_PREFIX" "$agent" "$benchmark" "${config#$ROOT_DIR/}" "$rc" "-" >> "$STATUS_FILE"
      if [[ "$rc" -ne 0 ]]; then
        return "$rc"
      fi
    done
  done
}

run_pilot() {
  require_provider_env || return 2
  require_openclaw_gateway_token || return 2
  preflight_provider_from_podman || return "$?"
  extend_no_proxy_for_provider
  init_status
  local agent benchmark config run_id rc overall_rc=0
  for agent in openclaw zeroclaw opencode; do
    for benchmark in aime gpqa hle imo; do
      config="$(cell_config "$agent" "$benchmark")"
      run_id="${RUN_PREFIX}_${agent}_${benchmark}"
      if run_config "pilot" "$config" "$run_id"; then
        rc=0
      else
        rc=$?
        overall_rc="$rc"
      fi
    done
  done
  return "$overall_rc"
}

run_audit() {
  init_status
  if [[ ! -f "$AUDIT_SCRIPT" ]]; then
    printf 'Audit script not found: %s\n' "${AUDIT_SCRIPT#$ROOT_DIR/}" >&2
    return 1
  fi
  python "$AUDIT_SCRIPT" \
    --run-prefix "$RUN_PREFIX" \
    --config-dir "$CONFIG_DIR" \
    --results-dir "$ROOT_DIR/results" \
    --logs-dir "$ROOT_DIR/logs" \
    --output-dir "$STATUS_DIR"
}

run_gate() {
  local pilot_rc=0 audit_rc=0
  run_pilot || pilot_rc=$?
  run_audit || audit_rc=$?
  if [[ "$pilot_rc" -ne 0 || "$audit_rc" -ne 0 ]]; then
    return 1
  fi
}

run_full() {
  if [[ "${PODMAN_SCALE_ALLOW_FULL:-}" != "1" ]]; then
    printf 'Full-scale execution is gated. Set PODMAN_SCALE_ALLOW_FULL=1 after a passing pilot audit.\n' >&2
    return 2
  fi
  require_openclaw_gateway_token || return 2
  preflight_provider_from_podman || return "$?"
  printf 'No full-scale configs are defined by this Phase 5 slice. Generate or provide them after pilot audit passes.\n' >&2
  return 2
}

main() {
  local rc=0
  case "$SCOPE" in
    validate)
      validate_matrix || rc=$?
      ;;
    pilot)
      run_pilot || rc=$?
      ;;
    audit)
      run_audit || rc=$?
      ;;
    gate|all)
      run_gate || rc=$?
      ;;
    full)
      run_full || rc=$?
      ;;
    *)
      printf 'Usage: %s [validate|pilot|audit|gate|all|full]\n' "$0" >&2
      return 2
      ;;
  esac

  printf '\nStatus file: %s\n' "${STATUS_FILE#$ROOT_DIR/}"
  return "$rc"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
