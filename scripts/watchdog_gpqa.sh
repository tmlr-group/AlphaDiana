#!/usr/bin/env bash
# watchdog_gpqa.sh — monitor one GPQA harness run, restart if dead
# Usage: bash scripts/watchdog_gpqa.sh <harness>
#   harness: opencode | openclaw | zeroclaw
set -euo pipefail

HARNESS="${1:?Usage: $0 opencode|openclaw|zeroclaw}"
PROJECT="/path/to/xxx/AlphaDiana-dev"
cd "$PROJECT"

case "$HARNESS" in
  opencode)
    CONFIG="configs/full_runs/gpqa_opencode_qwen35_27b_logprobs.yaml"
    DASHBOARD="results/full_gpqa_v2_opencode_qwen35_27b_logprobs/status/dashboard.txt"
    LOG_PREFIX="logs/full_gpqa_v2_opencode_qwen35_27b_logprobs"
    NEEDS_ROCK=0
    ;;
  openclaw)
    CONFIG="configs/full_runs/gpqa_openclaw_qwen35_27b_logprobs.yaml"
    DASHBOARD="results/full_gpqa_v2_openclaw_qwen35_27b_logprobs/status/dashboard.txt"
    LOG_PREFIX="logs/full_gpqa_v2_openclaw_qwen35_27b_logprobs"
    NEEDS_ROCK=1
    ;;
  zeroclaw)
    CONFIG="configs/full_runs/gpqa_zeroclaw_qwen35_27b_logprobs.yaml"
    DASHBOARD="results/full_gpqa_v2_zeroclaw_qwen35_27b_logprobs/status/dashboard.txt"
    LOG_PREFIX="logs/full_gpqa_v2_zeroclaw_qwen35_27b_logprobs"
    NEEDS_ROCK=1
    ;;
  *) echo "Unknown harness: $HARNESS"; exit 1 ;;
esac

TOTAL=198
SLEEP=300
TRACKED_PID=""

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$HARNESS] $*"; }

find_pid() {
  pgrep -f "alphadiana.cli run ${CONFIG}" 2>/dev/null | head -1 || true
}

rock_ok() {
  curl -sf http://127.0.0.1:9002/ 2>/dev/null | grep -q "hello" && return 0 || return 1
}

done_count() {
  # Count unique task_ids in JSONL (includes checkpoint-skipped tasks)
  local jsonl="${DASHBOARD/status\/dashboard.txt/../${HARNESS}_qwen35_27b_logprobs.jsonl}"
  # Simpler path derivation:
  local run_id
  run_id=$(basename "$(dirname "$(dirname "$DASHBOARD")")")
  python3 -c "
import json, sys
seen=set()
try:
  [seen.add(json.loads(l)['task_id']) for l in open('results/${run_id}.jsonl') if l.strip()]
except: pass
print(len(seen))
" 2>/dev/null || echo 0
}

do_restart() {
  source scripts/rock_env.sh 2>/dev/null || true
  source scripts/activate.sh 2>/dev/null || true
  export PYTHONPATH="$PROJECT"
  export HF_DATASETS_OFFLINE=1
  export HF_HUB_OFFLINE=1
  if [ "$NEEDS_ROCK" -eq 1 ]; then
    export OPENCLAW_GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-OPENCLAW}"
  fi
  local ts; ts=$(date +%Y%m%d_%H%M%S)
  local newlog="${LOG_PREFIX}.watchdog_restart_${ts}.log"
  nohup python -u -m alphadiana.cli run "$CONFIG" > "$newlog" 2>&1 &
  TRACKED_PID=$!
  log "Restarted PID=$TRACKED_PID log=$newlog"
}

# Initial PID discovery
TRACKED_PID=$(find_pid)
log "Start watching. PID=${TRACKED_PID:-NONE}, done=$(done_count)/$TOTAL"

while true; do
  DONE=$(done_count)

  if [ "$DONE" -ge "$TOTAL" ]; then
    log "ALL $TOTAL TASKS COMPLETE. Exiting watchdog."
    exit 0
  fi

  # Check if process is alive
  CURRENT_PID=$(find_pid)
  if [ -n "$CURRENT_PID" ]; then
    TRACKED_PID="$CURRENT_PID"
    log "OK — PID=$TRACKED_PID done=$DONE/$TOTAL"
  else
    log "DEAD — done=$DONE/$TOTAL, attempting restart..."
    if [ "$NEEDS_ROCK" -eq 1 ] && ! rock_ok; then
      log "ROCK is down — skipping restart, will retry in ${SLEEP}s"
    else
      do_restart
    fi
  fi

  sleep "$SLEEP"
done
