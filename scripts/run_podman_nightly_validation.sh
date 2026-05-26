#!/usr/bin/env bash
set -u
set -o pipefail

SCOPE="${1:-all}"
RUN_PREFIX="${PODMAN_NIGHTLY_RUN_PREFIX:-podman_nightly_20260514}"
COMMAND_TIMEOUT_SECONDS="${PODMAN_NIGHTLY_COMMAND_TIMEOUT_SECONDS:-7200}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONFIG_DIR="$ROOT_DIR/configs/smokes/podman_nightly_validation"
STATUS_DIR="$ROOT_DIR/context/podman-nightly-validation"
STATUS_FILE="$STATUS_DIR/run-status-${RUN_PREFIX}.tsv"

cd "$ROOT_DIR" || exit 1

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export ALPHADIANA_OPENCLAW_PODMAN_IMAGE="${ALPHADIANA_OPENCLAW_PODMAN_IMAGE:-localhost/alphadiana-openclaw:latest}"
export ALPHADIANA_ZEROCLAW_PODMAN_IMAGE="${ALPHADIANA_ZEROCLAW_PODMAN_IMAGE:-localhost/zeroclaw-reasoning:0.6.9}"
export TB2_OPENCODE_RUNTIME_IMAGE="${TB2_OPENCODE_RUNTIME_IMAGE:-localhost/alphadiana/tb2-opencode-controller:latest}"
export TERMINAL_BENCH2_DIR="${TERMINAL_BENCH2_DIR:-/tmp/terminal-bench-2}"
export ALPHADIANA_TB2_LOGS_DIR="${ALPHADIANA_TB2_LOGS_DIR:-/tmp/alphadiana-podman-nightly-tb2-logs}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_DATASETS_CACHE="${PODMAN_NIGHTLY_HF_DATASETS_CACHE:-/tmp/alphadiana-hf-cache}"
export ALPHADIANA_PODMAN_SOCKET="${ALPHADIANA_PODMAN_SOCKET:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/podman/podman.sock}"

missing=0
for name in OPENAI_BASE_URL OPENAI_API_KEY OPENAI_MODEL_NAME; do
  if [[ -z "${!name:-}" ]]; then
    printf 'Missing required environment variable: %s\n' "$name" >&2
    missing=1
  fi
done
if [[ "$missing" -ne 0 ]]; then
  exit 2
fi

mkdir -p "$STATUS_DIR" "$ROOT_DIR/logs"
if [[ ! -f "$STATUS_FILE" ]]; then
  printf 'scope\trun_id\tconfig\texit_code\tlog_path\n' > "$STATUS_FILE"
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user start podman.socket >/dev/null 2>&1 || true
fi

if [[ ! -d "$TERMINAL_BENCH2_DIR" ]]; then
  printf 'TerminalBench2 task root not found: %s\n' "$TERMINAL_BENCH2_DIR" >&2
fi

prefetch_tb2_images() {
  local image
  for image in \
    docker.io/alexgshaw/db-wal-recovery:20251031 \
    docker.io/alexgshaw/fix-git:20251031 \
    docker.io/alexgshaw/overfull-hbox:20251031
  do
    printf 'Preflight Podman image: %s\n' "$image"
    if ! timeout 120s podman pull "$image"; then
      printf 'Image preflight failed: %s\n' "$image" >&2
      return 1
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
  printf 'Config: %s\n' "$config"
  (
    cd "$ROOT_DIR" || exit 1
    timeout --foreground "$COMMAND_TIMEOUT_SECONDS" \
      python -m alphadiana.cli run "$config" \
        --redo-all \
        -o "run_id=$run_id" \
        -o "output_dir=$output_dir" \
        2>&1
  ) | tee "$log_path"
  local rc=${PIPESTATUS[0]}
  printf '%s\t%s\t%s\t%s\t%s\n' "$group" "$run_id" "${config#$ROOT_DIR/}" "$rc" "logs/${run_id}.log" >> "$STATUS_FILE"
  return 0
}

run_standard() {
  local agent benchmark config run_id
  for agent in openclaw zeroclaw opencode; do
    for benchmark in aime gpqa hle imo; do
      config="$CONFIG_DIR/${agent}_${benchmark}.yaml"
      run_id="${RUN_PREFIX}_${agent}_${benchmark}"
      run_config "standard" "$config" "$run_id"
    done
  done
}

run_task_containers() {
  local tb2_ready=0
  prefetch_tb2_images || {
    printf 'Skipping TerminalBench2 run because one or more task images were unavailable.\n' >&2
    printf 'task-container\t%s_terminal_bench2_opencode\t%s\t%s\t%s\n' \
      "$RUN_PREFIX" \
      "configs/smokes/podman_nightly_validation/terminal_bench2_opencode.yaml" \
      "skipped-image-preflight" \
      "" >> "$STATUS_FILE"
    tb2_ready=1
  }
  if [[ "$tb2_ready" -eq 0 && -d "$TERMINAL_BENCH2_DIR" ]]; then
    run_config "task-container" "$CONFIG_DIR/terminal_bench2_opencode.yaml" "${RUN_PREFIX}_terminal_bench2_opencode"
  fi
  run_config "task-container" "$CONFIG_DIR/openclaw_swe_bench_verified.yaml" "${RUN_PREFIX}_openclaw_swe_bench_verified"
}

case "$SCOPE" in
  standard)
    run_standard
    ;;
  task|task-container|task-containers)
    run_task_containers
    ;;
  all)
    run_standard
    run_task_containers
    ;;
  *)
    printf 'Usage: %s [standard|task|all]\n' "$0" >&2
    exit 2
    ;;
esac

printf '\nStatus file: %s\n' "${STATUS_FILE#$ROOT_DIR/}"
