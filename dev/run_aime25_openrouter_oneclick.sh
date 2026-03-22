#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR%/dev/generated}"

CFG_PATH="${CFG_PATH:-configs/examples/openclaw_aime2025_dynamic.yaml}"
RUN_PREFIX="${RUN_PREFIX:-aime25-qwen235b}"
TASK_KEY="${TASK_KEY:-aime25-openrouter-qwen235b}"
MAX_TASKS="${MAX_TASKS:-1}"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-300}"
START_RETRIES="${START_RETRIES:-3}"

cd "${REPO_ROOT}"

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

bash dev/start_openclaw.sh || true

run_with_profile() {
  local run_id="$1"
  local memory="$2"
  local cpus="$3"

  set +e
  alphadiana run "${CFG_PATH}" \
    -o benchmark.config.max_tasks="${MAX_TASKS}" \
    -o agent.config.rock_memory="${memory}" \
    -o agent.config.rock_cpus="${cpus}" \
    -o agent.config.rock_startup_timeout="${STARTUP_TIMEOUT}" \
    -o agent.config.rock_start_retries="${START_RETRIES}" \
    -o run_id="${run_id}"
  local rc=$?
  set -e
  return ${rc}
}

if run_with_profile "${RUN_ID}" "${ROCK_EST_MEMORY}" "${ROCK_EST_CPUS}"; then
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
if run_with_profile "${FALLBACK1_ID}" "1536m" "0.75"; then
  python3 dev/generated/sandbox_resource_estimator.py record-success \
    --task-key "${TASK_KEY}" \
    --run-prefix "${RUN_PREFIX}" \
    --memory "1536m" \
    --cpus "0.75" \
    --run-id "${FALLBACK1_ID}" >/dev/null 2>&1 || true
  echo "[done] run_id=${FALLBACK1_ID} profile=1536m/0.75"
  exit 0
fi

echo "[fallback] second profile failed, trying smallest safe profile..."
FALLBACK2_ID="${RUN_ID}-fb2"
if run_with_profile "${FALLBACK2_ID}" "1g" "0.5"; then
  python3 dev/generated/sandbox_resource_estimator.py record-success \
    --task-key "${TASK_KEY}" \
    --run-prefix "${RUN_PREFIX}" \
    --memory "1g" \
    --cpus "0.5" \
    --run-id "${FALLBACK2_ID}" >/dev/null 2>&1 || true
  echo "[done] run_id=${FALLBACK2_ID} profile=1g/0.5"
  exit 0
fi

echo "[failed] all profiles failed (${ROCK_EST_MEMORY}/${ROCK_EST_CPUS}, 1536m/0.75, 1g/0.5)" >&2
exit 1
