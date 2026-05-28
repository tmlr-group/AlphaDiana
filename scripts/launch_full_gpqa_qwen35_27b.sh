#!/usr/bin/env bash
# launch_full_gpqa_qwen35_27b.sh
# Runs all 3 harnesses (OpenClaw, OpenCode, ZeroClaw) on full GPQA-Diamond (198 tasks)
# with Qwen/Qwen3.5-27B at http://127.0.0.1:8011/v1, Top-20 Int16 logprob capture.
#
# Execution order:
#   OpenCode (Docker-based, no ROCK)  → background, starts immediately
#   OpenClaw (ROCK-based)             → background, starts immediately
#   ZeroClaw (ROCK-based)             → starts after OpenClaw finishes
#
# Logs: logs/full_gpqa_<harness>_qwen35_27b_logprobs.log
# Results: results/full_gpqa_<harness>_qwen35_27b_logprobs/
#
# Usage: bash scripts/launch_full_gpqa_qwen35_27b.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# ── 1. Load environment (unsets proxy vars, sets ROCK URLs + HF_DATASETS_CACHE) ──
source "${SCRIPT_DIR}/rock_env.sh"
echo "[env] Proxy vars cleared, HF_DATASETS_CACHE=${HF_DATASETS_CACHE:-/path/to/datasets}"

# ── 2. Set weak gateway token for smoke compatibility ──────────────────────────
# generate_strong_token is done by start_openclaw.sh; for direct CLI runs use OPENCLAW as fallback
export OPENCLAW_GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-OPENCLAW}"
echo "[env] OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_GATEWAY_TOKEN}"

# ── 3. Verify vLLM endpoint ────────────────────────────────────────────────────
echo "[check] vLLM at http://127.0.0.1:8011/v1 ..."
if ! curl -sf http://127.0.0.1:8011/v1/models >/dev/null 2>&1; then
    echo "ERROR: vLLM not responding at http://127.0.0.1:8011/v1. Aborting."
    exit 1
fi
echo "[check] vLLM OK"

# ── 4. Verify ROCK services ────────────────────────────────────────────────────
echo "[check] ROCK admin at http://127.0.0.1:${ROCK_ADMIN_PORT:-9002} ..."
if ! curl -sf "http://127.0.0.1:${ROCK_ADMIN_PORT:-9002}/" >/dev/null 2>&1; then
    echo "WARN: ROCK admin not responding — attempting to start services..."
    bash "${SCRIPT_DIR}/start_openclaw.sh" || true
fi
echo "[check] ROCK OK"

# ── 5. Prepare log/result dirs ────────────────────────────────────────────────
mkdir -p logs results

# ── 6. Launch OpenCode (Docker, independent of ROCK) ─────────────────────────
OPENCODE_LOG="logs/full_gpqa_opencode_qwen35_27b_logprobs.log"
echo "[launch] OpenCode → ${OPENCODE_LOG}"
nohup python -m alphadiana.cli run \
    configs/full_runs/gpqa_opencode_qwen35_27b_logprobs.yaml \
    > "${OPENCODE_LOG}" 2>&1 &
OPENCODE_PID=$!
echo "[launch] OpenCode PID=${OPENCODE_PID}"

# ── 7. Launch OpenClaw (ROCK-based) ──────────────────────────────────────────
OPENCLAW_LOG="logs/full_gpqa_openclaw_qwen35_27b_logprobs.log"
echo "[launch] OpenClaw → ${OPENCLAW_LOG}"
nohup python -m alphadiana.cli run \
    configs/full_runs/gpqa_openclaw_qwen35_27b_logprobs.yaml \
    > "${OPENCLAW_LOG}" 2>&1 &
OPENCLAW_PID=$!
echo "[launch] OpenClaw PID=${OPENCLAW_PID}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " RUNNING: OpenCode (PID=${OPENCODE_PID}) + OpenClaw (PID=${OPENCLAW_PID})"
echo " ZeroClaw queued: starts automatically when OpenClaw finishes"
echo " 198 tasks per harness, ~2-5 min per task → est. 6-16h per harness"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 8. Wait for OpenClaw to finish, then launch ZeroClaw ─────────────────────
ZEROCLAW_LOG="logs/full_gpqa_zeroclaw_qwen35_27b_logprobs.log"

(
    echo "[monitor] Waiting for OpenClaw (PID=${OPENCLAW_PID}) to complete before starting ZeroClaw..."
    wait "${OPENCLAW_PID}" 2>/dev/null || true
    OC_EXIT=$?
    echo "[monitor] OpenClaw exited with code ${OC_EXIT}"
    echo "[launch] Starting ZeroClaw → ${ZEROCLAW_LOG}"
    source "${SCRIPT_DIR}/rock_env.sh"
    python -m alphadiana.cli run \
        configs/full_runs/gpqa_zeroclaw_qwen35_27b_logprobs.yaml \
        > "${ZEROCLAW_LOG}" 2>&1
    ZC_EXIT=$?
    echo "[monitor] ZeroClaw finished with exit code ${ZC_EXIT}" >> "${ZEROCLAW_LOG}"
    echo "[done] ZeroClaw complete. Check: ${ZEROCLAW_LOG}"
) &
SEQUENCER_PID=$!
echo "[launch] ZeroClaw sequencer PID=${SEQUENCER_PID} (will start after OpenClaw)"

echo ""
echo "Monitor with:"
echo "  tail -f ${OPENCLAW_LOG}"
echo "  tail -f logs/full_gpqa_opencode_qwen35_27b_logprobs.log"
echo "  tail -f ${ZEROCLAW_LOG}   (once ZeroClaw starts)"
echo ""
echo "PIDs written to: logs/full_gpqa_pids.txt"
echo "${OPENCLAW_PID} openclaw" > logs/full_gpqa_pids.txt
echo "${OPENCODE_PID} opencode" >> logs/full_gpqa_pids.txt
echo "${SEQUENCER_PID} zeroclaw-sequencer" >> logs/full_gpqa_pids.txt
