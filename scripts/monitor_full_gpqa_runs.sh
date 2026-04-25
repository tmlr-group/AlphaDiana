#!/usr/bin/env bash
# monitor_full_gpqa_runs.sh
# Reports progress, errors, and logprob sidecar counts for all 3 full GPQA runs.
# Usage: bash scripts/monitor_full_gpqa_runs.sh
#        bash scripts/monitor_full_gpqa_runs.sh --watch   # loops every 60s

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WATCH=false
[[ "${1:-}" == "--watch" ]] && WATCH=true

EXPECTED=198

report_harness() {
    local name="$1"
    local result_dir="${ROOT}/results/${name}"
    local log="${ROOT}/logs/${name}.log"
    local task_dir="${result_dir}/tasks"

    echo "── ${name} ──────────────────────────────────"

    # Task count
    local task_count=0
    if [ -d "${task_dir}" ]; then
        task_count=$(ls "${task_dir}" 2>/dev/null | wc -l | tr -d ' ')
    fi
    echo "  Tasks:     ${task_count} / ${EXPECTED}"

    # Logprob sidecar count
    local lp_count=0
    if [ -d "${task_dir}" ]; then
        lp_count=$(find "${task_dir}" -name "logprobs_int16*" 2>/dev/null | wc -l | tr -d ' ')
    fi
    echo "  LP sidecars (int16): ${lp_count} / ${task_count}"

    # Process alive? — probe known PID file locations
    local short_name
    short_name=$(echo "${name}" | sed 's/full_gpqa_//' | sed 's/_qwen35_27b_logprobs//')
    local proc_status="stopped"
    local pid=""
    # Try multiple candidate PID file names (zeroclaw uses a different naming convention)
    for pid_candidate_file in \
        "${ROOT}/logs/phase12_${short_name}_full.pid" \
        "${ROOT}/logs/phase12_${short_name}_after_openclaw.pid" \
        "${ROOT}/logs/phase12_${short_name}.pid"
    do
        if [ -f "${pid_candidate_file}" ]; then
            pid=$(cat "${pid_candidate_file}" 2>/dev/null || echo "")
            break
        fi
    done
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
        proc_status="running (PID=${pid})"
    elif [ -n "${pid}" ]; then
        proc_status="stopped (last PID=${pid})"
    fi
    echo "  Process:   ${proc_status}"

    # Last log line (progress indicator)
    if [ -f "${log}" ]; then
        local last
        last=$(tail -1 "${log}" 2>/dev/null || echo "(empty)")
        echo "  Last log:  ${last}"
        # Error count in log
        local err_count
        err_count=$(grep -c -i "error\|exception\|terminated\|traceback" "${log}" 2>/dev/null || echo 0)
        echo "  Errors in log: ${err_count}"
    else
        echo "  Log: not found (${log})"
    fi
    echo ""
}

show_report() {
    echo "======================================================"
    echo " GPQA Full Run Monitor — $(date '+%Y-%m-%d %H:%M:%S')"
    echo "======================================================"
    echo ""
    report_harness "full_gpqa_openclaw_qwen35_27b_logprobs"
    report_harness "full_gpqa_opencode_qwen35_27b_logprobs"
    report_harness "full_gpqa_zeroclaw_qwen35_27b_logprobs"
    echo "======================================================"
}

if $WATCH; then
    while true; do
        clear
        show_report
        echo "(Refreshing every 60s — Ctrl+C to stop)"
        sleep 60
    done
else
    show_report
fi
