#!/usr/bin/env bash
# Launch the three AIME 2026 pass@64 runs IN PARALLEL (the campaign default).
# Each run keeps max_concurrent=3 from its config -> ~9 concurrent provider
# requests total. Ctrl-C forwards the interrupt to all three children.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_VERSION="${RUN_VERSION:-v01}"
AGENTS=(openclaw opencode zeroclaw)

declare -A PIDS
declare -A CODES

cleanup() {
  for agent in "${!PIDS[@]}"; do
    kill "${PIDS[$agent]}" 2>/dev/null || true
  done
}
trap cleanup INT TERM

for agent in "${AGENTS[@]}"; do
  bash "$SCRIPT_DIR/run.sh" "$agent" &
  PIDS[$agent]=$!
  printf 'Launched %s (pid %s)\n' "$agent" "${PIDS[$agent]}"
done

status=0
for agent in "${AGENTS[@]}"; do
  if wait "${PIDS[$agent]}"; then
    CODES[$agent]=0
  else
    CODES[$agent]=$?
    status=1
  fi
done

printf '\n=== Campaign summary (RUN_VERSION=%s) ===\n' "$RUN_VERSION"
for agent in "${AGENTS[@]}"; do
  printf '  %-9s exit=%s\n' "$agent" "${CODES[$agent]}"
done
[[ "$status" -eq 0 ]] || printf 'At least one run failed; rerun that agent with the SAME run_id to resume from checkpoint.\n' >&2
exit "$status"
