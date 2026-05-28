#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_ROOT}/logs"
OPENCLAW_PID_FILE="${LOG_DIR}/phase12_openclaw_full.pid"
ZEROCLAW_LOG="${LOG_DIR}/full_gpqa_zeroclaw_qwen35_27b_logprobs.log"
ZEROCLAW_PID_FILE="${LOG_DIR}/phase12_zeroclaw_full.pid"
ZEROCLAW_CONFIG="configs/full_runs/full_gpqa_zeroclaw_qwen35_27b_logprobs.yaml"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_ROOT}"

echo "[phase12-seq] waiting for ${OPENCLAW_PID_FILE}..."
while [ ! -f "${OPENCLAW_PID_FILE}" ]; do
    sleep 5
done

openclaw_pid="$(cat "${OPENCLAW_PID_FILE}")"
echo "[phase12-seq] tracking OpenClaw PID ${openclaw_pid}"
while kill -0 "${openclaw_pid}" 2>/dev/null; do
    sleep 30
done

echo "[phase12-seq] OpenClaw finished; starting ZeroClaw full run"
source scripts/activate.sh
source scripts/rock_env.sh
nohup python -m alphadiana.cli run "${ZEROCLAW_CONFIG}" > "${ZEROCLAW_LOG}" 2>&1 &
echo $! > "${ZEROCLAW_PID_FILE}"
echo "[phase12-seq] ZeroClaw launched with PID $(cat "${ZEROCLAW_PID_FILE}")"
