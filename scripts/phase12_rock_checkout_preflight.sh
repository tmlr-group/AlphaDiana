#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Required smoke configs for phase12 ROCK/Ray preflight.
OPENCLAW_SMOKE_CONFIG="configs/full_runs/phase11_openclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml"
ZEROCLAW_SMOKE_CONFIG="configs/full_runs/phase11_zeroclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml"

set +e
source "${SCRIPT_DIR}/activate.sh"
activate_rc=$?
set -e
if [ "${activate_rc}" -ne 0 ]; then
    echo "warning: scripts/activate.sh exited ${activate_rc}; continuing with current shell context"
fi
source "${SCRIPT_DIR}/rock_env.sh"

cd "${PROJECT_ROOT}"

for cfg in "${OPENCLAW_SMOKE_CONFIG}" "${ZEROCLAW_SMOKE_CONFIG}"; do
    if [ ! -f "${cfg}" ]; then
        echo "missing required smoke config: ${cfg}" >&2
        exit 1
    fi
done

python -m alphadiana.cli validate "${OPENCLAW_SMOKE_CONFIG}"
python -m alphadiana.cli validate "${ZEROCLAW_SMOKE_CONFIG}"

_run_env() {
    # Equivalent user-facing command: alphadiana env
    python -m alphadiana.cli env 2>&1
}

_rock_ready_from_env_output() {
    local env_output="$1"
    grep -q "✓ admin" <<<"${env_output}" \
        && grep -q "✓ proxy" <<<"${env_output}" \
        && grep -q "✓ redis" <<<"${env_output}"
}

echo "[phase12 preflight] checking ROCK service health..."
env_output="$(_run_env || true)"
echo "${env_output}"

if ! _rock_ready_from_env_output "${env_output}"; then
    echo "[phase12 preflight] remediation: starting checkout-owned OpenClaw/ZeroClaw ROCK services..."
    bash scripts/start_openclaw.sh
    bash scripts/start_zeroclaw.sh
    echo "[phase12 preflight] re-checking ROCK service health after remediation..."
    env_output="$(_run_env || true)"
    echo "${env_output}"
fi

if ! _rock_ready_from_env_output "${env_output}"; then
    echo "phase12 rock preflight failed" >&2
    exit 1
fi

echo "[phase12 preflight] ROCK services ready."
