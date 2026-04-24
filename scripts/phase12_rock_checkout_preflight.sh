#!/usr/bin/env bash
# phase12_rock_checkout_preflight.sh — checkout-owned ROCK readiness check and remediation
#
# Run this before OpenClaw or ZeroClaw ROCK-backed smokes to ensure all required
# ROCK services (admin, proxy, redis) are reachable and owned by this checkout.
#
# Usage:
#   bash scripts/phase12_rock_checkout_preflight.sh
#
# Exit codes:
#   0 — all services healthy
#   1 — services still unhealthy after remediation attempt
#
# The script executes this fixed sequence:
#   1. source scripts/activate.sh
#   2. source scripts/rock_env.sh
#   3. alphadiana env  (checks service health + ownership)
#   4. Validate both Phase 11 smoke configs
#
# If alphadiana env output does not contain admin ✓, proxy ✓, and redis ✓,
# it auto-runs bash scripts/start_openclaw.sh then bash scripts/start_zeroclaw.sh
# and retries alphadiana env once. If still unhealthy, exits 1.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_ROOT}/logs"
mkdir -p "${LOG_DIR}"

echo "=== Phase 12 ROCK Checkout Preflight ==="
echo "    Project root: ${PROJECT_ROOT}"
echo ""

# ── Step 1: Activate the environment ─────────────────────────────────────────
echo "[1/4] Activating environment..."
# shellcheck disable=SC1091
source scripts/activate.sh

# ── Step 2: Load ROCK environment variables ───────────────────────────────────
echo "[2/4] Loading ROCK environment..."
# shellcheck disable=SC1091
source scripts/rock_env.sh

# ── Helper: check if all three key ROCK services are healthy ─────────────────
_rock_services_healthy() {
    local _env_output=""
    _env_output="$(python -m alphadiana.cli env 2>&1)" || true
    echo "${_env_output}"
    # Must contain all three: admin ✓, proxy ✓, redis ✓
    if echo "${_env_output}" | grep -q "admin" && \
       echo "${_env_output}" | grep -q "proxy" && \
       echo "${_env_output}" | grep -q "redis"; then
        # Check that none of the three are marked as ✗
        if ! echo "${_env_output}" | grep -E "✗ (admin|proxy|redis)" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# ── Step 3: Check ROCK services via alphadiana env ────────────────────────────
echo "[3/4] Running alphadiana env..."
if ! _rock_services_healthy; then
    echo ""
    echo "    ROCK services not healthy. Attempting auto-remediation..."
    echo "    Starting OpenClaw infrastructure..."
    bash scripts/start_openclaw.sh 2>&1 || true
    echo ""
    echo "    Starting ZeroClaw infrastructure..."
    bash scripts/start_zeroclaw.sh 2>&1 || true
    echo ""
    echo "    Re-checking ROCK services after remediation..."
    if ! _rock_services_healthy; then
        echo ""
        echo "ERROR: phase12 rock preflight failed"
        echo "       admin ✓, proxy ✓, redis ✓ not all present after remediation."
        echo "       Check logs in .cache/logs/ for details."
        exit 1
    fi
fi
echo ""
echo "    ROCK services healthy: admin ✓  proxy ✓  redis ✓"

# ── Step 4: Validate both smoke configs ───────────────────────────────────────
echo "[4/4] Validating smoke configs..."

echo "    Validating OpenClaw smoke config..."
python -m alphadiana.cli validate \
    configs/full_runs/phase11_openclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml
echo "    OpenClaw config: valid"

echo "    Validating ZeroClaw smoke config..."
python -m alphadiana.cli validate \
    configs/full_runs/phase11_zeroclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml
echo "    ZeroClaw config: valid"

echo ""
echo "=== Phase 12 ROCK Checkout Preflight: PASSED ==="
echo "    Ready to run OpenClaw and ZeroClaw ROCK-backed smokes."
echo ""
echo "    Next steps:"
echo "      python -m alphadiana.cli run configs/full_runs/phase11_openclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml"
echo "      python -m alphadiana.cli run configs/full_runs/phase11_zeroclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml"
