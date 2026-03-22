#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR%/dev}"

usage() {
  cat <<'EOF'
Usage:
  bash dev/run_aime25_openrouter_oneclick.sh --config <path/to/config.yaml>

Config keys (YAML):
  provider:
    openai_base_url: "https://openrouter.ai/api/v1"
    openai_api_key: ""                # optional, prefer env in shared machines
    openai_api_key_env: "OPENROUTER_API_KEY"
    openai_model_name: "qwen/qwen3-235b-a22b-2507"
  benchmark:
    config_path: "configs/examples/openclaw_aime2025_dynamic.yaml"
    dataset: "MathArena/aime_2025"    # optional override
    split: "train"                    # optional override
    max_tasks: 1
  run:
    run_prefix: "aime25-qwen235b"
    run_id: ""                        # optional fixed run_id
    startup_timeout: 300
    start_retries: 3
    fallback_1_memory: "1536m"
    fallback_1_cpus: 0.75
    fallback_2_memory: "1g"
    fallback_2_cpus: 0.5
  estimator:
    task_key: "aime25-openrouter-qwen235b"

EOF
}

CONFIG_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${CONFIG_FILE}" ]]; then
  echo "--config is required." >&2
  usage
  exit 2
fi

CFG_PATH="${CFG_PATH:-configs/examples/openclaw_aime2025_dynamic.yaml}"
RUN_PREFIX="${RUN_PREFIX:-aime25-qwen235b}"
TASK_KEY="${TASK_KEY:-aime25-openrouter-qwen235b}"
MAX_TASKS="${MAX_TASKS:-1}"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-300}"
START_RETRIES="${START_RETRIES:-3}"
DATASET_OVERRIDE="${DATASET_OVERRIDE:-}"
SPLIT_OVERRIDE="${SPLIT_OVERRIDE:-}"
FALLBACK1_MEMORY="${FALLBACK1_MEMORY:-1536m}"
FALLBACK1_CPUS="${FALLBACK1_CPUS:-0.75}"
FALLBACK2_MEMORY="${FALLBACK2_MEMORY:-1g}"
FALLBACK2_CPUS="${FALLBACK2_CPUS:-0.5}"

apply_config() {
  local cfg_file="$1"
  if [[ ! -f "${cfg_file}" ]]; then
    echo "Config file not found: ${cfg_file}" >&2
    exit 2
  fi

  eval "$(python3 - "${cfg_file}" <<'PY'
import os
import shlex
import sys

try:
    import yaml
except Exception as exc:
    print(f'echo "[error] Missing pyyaml: {exc}" >&2')
    print('exit 2')
    raise SystemExit

cfg_path = sys.argv[1]
with open(cfg_path, 'r', encoding='utf-8') as f:
    cfg = yaml.safe_load(f) or {}

provider = cfg.get('provider') or {}
benchmark = cfg.get('benchmark') or {}
run = cfg.get('run') or {}
estimator = cfg.get('estimator') or {}

def emit(name, value):
    if value is None:
        return
    if isinstance(value, bool):
        value = 'true' if value else 'false'
    print(f'export {name}={shlex.quote(str(value))}')

emit('CFG_PATH', benchmark.get('config_path'))
emit('MAX_TASKS', benchmark.get('max_tasks'))
emit('DATASET_OVERRIDE', benchmark.get('dataset'))
emit('SPLIT_OVERRIDE', benchmark.get('split'))

emit('RUN_PREFIX', run.get('run_prefix'))
emit('RUN_ID', run.get('run_id'))
emit('STARTUP_TIMEOUT', run.get('startup_timeout'))
emit('START_RETRIES', run.get('start_retries'))
emit('FALLBACK1_MEMORY', run.get('fallback_1_memory'))
emit('FALLBACK1_CPUS', run.get('fallback_1_cpus'))
emit('FALLBACK2_MEMORY', run.get('fallback_2_memory'))
emit('FALLBACK2_CPUS', run.get('fallback_2_cpus'))

emit('TASK_KEY', estimator.get('task_key'))

emit('OPENAI_BASE_URL', provider.get('openai_base_url'))
emit('OPENAI_MODEL_NAME', provider.get('openai_model_name'))

api_key = provider.get('openai_api_key')
if api_key:
    emit('OPENAI_API_KEY', api_key)
else:
    env_name = provider.get('openai_api_key_env')
    if env_name:
        print(f'export OPENAI_API_KEY=${{{env_name}:-${{OPENAI_API_KEY:-}}}}')
PY
)"
}

print_warnings() {
  local log_file="$1"
  local admin_log="${REPO_ROOT}/.cache/logs/rock-admin.log"
  local proxy_log="${REPO_ROOT}/.cache/logs/rock-proxy.log"

  echo ""
  echo "[warning] Run failed. Suggested checks:"

  if [[ -f "${log_file}" ]] && rg -n "OPENAI_API_KEY|401|403|Authentication|Unauthorized" "${log_file}" >/dev/null 2>&1; then
    echo "- Provider auth may be invalid. Check provider.openai_api_key / provider.openai_api_key_env in config." 
  fi

  if [[ -f "${log_file}" ]] && rg -n "run in session failed|upstream server is not reachable|Service unavailable" "${log_file}" >/dev/null 2>&1; then
    echo "- Detected transient upstream/session errors. Re-run is often enough; fallback profiles are already attempted."
  fi

  if [[ -f "${log_file}" ]] && rg -n "Get timed out|not ready|startup timeout|timed out" "${log_file}" >/dev/null 2>&1; then
    echo "- Sandbox startup timeout likely. Try higher run.startup_timeout or smaller fallback profile in config."
  fi

  if [[ -f "${admin_log}" ]] && rg -n "Version mismatch" "${admin_log}" >/dev/null 2>&1; then
    echo "- Ray/Python version mismatch. Stop stale ray and restart with current env Python."
  fi

  if [[ -f "${admin_log}" ]] && rg -n "redis|Redis" "${admin_log}" >/dev/null 2>&1; then
    echo "- Redis connectivity issue. Ensure redis container is running on ROCK_REDIS_PORT."
  fi

  echo "- Detailed troubleshooting: docs/openclaw_aime25_fix_changelog_repro_20260322.md"
}

cd "${REPO_ROOT}"

apply_config "${CONFIG_FILE}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# shellcheck disable=SC1091
source dev/rock_env.sh

python3 dev/find_rock_ports.py --write-env dev/.rock_ports.env >/tmp/alphadiana_ports_detect.log 2>&1 || {
  cat /tmp/alphadiana_ports_detect.log >&2
  exit 1
}
# shellcheck disable=SC1091
source dev/.rock_ports.env

export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://openrouter.ai/api/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-${OPENROUTER_API_KEY:-}}"
export OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-qwen/qwen3-235b-a22b-2507}"

if [[ -z "${OPENAI_API_KEY}" ]]; then
  echo "OPENAI_API_KEY/OPENROUTER_API_KEY is empty." >&2
  exit 1
fi

EST_EXPORTS="$(python3 dev/generated/sandbox_resource_estimator.py recommend --task-key "${TASK_KEY}" --run-prefix "${RUN_PREFIX}" --format shell)"
eval "${EST_EXPORTS}"

echo "[estimator] source=${ROCK_EST_SOURCE} contention=${ROCK_EST_CONTENTION_TIER} load_per_cpu=${ROCK_EST_LOAD_PER_CPU} mem_avail_ratio=${ROCK_EST_MEM_AVAILABLE_RATIO} profile=${ROCK_EST_MEMORY}/${ROCK_EST_CPUS}"

default_run_id="${RUN_PREFIX}-dyn-$(date +%Y%m%d-%H%M%S)"
RUN_ID="${RUN_ID:-${default_run_id}}"
RUN_LOG="/tmp/${RUN_ID}.alphadiana.log"

if ! bash dev/start_openclaw.sh; then
  echo "[warning] dev/start_openclaw.sh failed; continue to run so fallback/warnings can surface."
fi

run_with_profile() {
  local run_id="$1"
  local memory="$2"
  local cpus="$3"
  local log_file="$4"
  local -a extra_args=()

  if [[ -n "${DATASET_OVERRIDE}" ]]; then
    extra_args+=( -o "benchmark.config.dataset=${DATASET_OVERRIDE}" )
  fi
  if [[ -n "${SPLIT_OVERRIDE}" ]]; then
    extra_args+=( -o "benchmark.config.split=${SPLIT_OVERRIDE}" )
  fi

  set +e
  alphadiana run "${CFG_PATH}" \
    -o benchmark.config.max_tasks="${MAX_TASKS}" \
    -o agent.config.rock_memory="${memory}" \
    -o agent.config.rock_cpus="${cpus}" \
    -o agent.config.rock_startup_timeout="${STARTUP_TIMEOUT}" \
    -o agent.config.rock_start_retries="${START_RETRIES}" \
    -o run_id="${run_id}" \
    "${extra_args[@]}" 2>&1 | tee "${log_file}"
  local rc=$?
  set -e
  return ${rc}
}

if run_with_profile "${RUN_ID}" "${ROCK_EST_MEMORY}" "${ROCK_EST_CPUS}" "${RUN_LOG}"; then
  python3 dev/generated/sandbox_resource_estimator.py record-success \
    --task-key "${TASK_KEY}" \
    --run-prefix "${RUN_PREFIX}" \
    --memory "${ROCK_EST_MEMORY}" \
    --cpus "${ROCK_EST_CPUS}" \
    --run-id "${RUN_ID}" >/dev/null 2>&1 || true
  echo "[done] run_id=${RUN_ID} profile=${ROCK_EST_MEMORY}/${ROCK_EST_CPUS}"
  exit 0
fi

echo "[fallback] first profile failed, trying lighter profile..."
FALLBACK1_ID="${RUN_ID}-fb1"
FALLBACK1_LOG="/tmp/${FALLBACK1_ID}.alphadiana.log"
if run_with_profile "${FALLBACK1_ID}" "${FALLBACK1_MEMORY}" "${FALLBACK1_CPUS}" "${FALLBACK1_LOG}"; then
  python3 dev/generated/sandbox_resource_estimator.py record-success \
    --task-key "${TASK_KEY}" \
    --run-prefix "${RUN_PREFIX}" \
    --memory "${FALLBACK1_MEMORY}" \
    --cpus "${FALLBACK1_CPUS}" \
    --run-id "${FALLBACK1_ID}" >/dev/null 2>&1 || true
  echo "[done] run_id=${FALLBACK1_ID} profile=${FALLBACK1_MEMORY}/${FALLBACK1_CPUS}"
  exit 0
fi

echo "[fallback] second profile failed, trying smallest safe profile..."
FALLBACK2_ID="${RUN_ID}-fb2"
FALLBACK2_LOG="/tmp/${FALLBACK2_ID}.alphadiana.log"
if run_with_profile "${FALLBACK2_ID}" "${FALLBACK2_MEMORY}" "${FALLBACK2_CPUS}" "${FALLBACK2_LOG}"; then
  python3 dev/generated/sandbox_resource_estimator.py record-success \
    --task-key "${TASK_KEY}" \
    --run-prefix "${RUN_PREFIX}" \
    --memory "${FALLBACK2_MEMORY}" \
    --cpus "${FALLBACK2_CPUS}" \
    --run-id "${FALLBACK2_ID}" >/dev/null 2>&1 || true
  echo "[done] run_id=${FALLBACK2_ID} profile=${FALLBACK2_MEMORY}/${FALLBACK2_CPUS}"
  exit 0
fi

print_warnings "${FALLBACK2_LOG}"
echo "[failed] all profiles failed (${ROCK_EST_MEMORY}/${ROCK_EST_CPUS}, ${FALLBACK1_MEMORY}/${FALLBACK1_CPUS}, ${FALLBACK2_MEMORY}/${FALLBACK2_CPUS})" >&2
exit 1
