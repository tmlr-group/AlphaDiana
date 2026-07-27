#!/usr/bin/env bash
# Preflight for the AIME 2026 pass@64 campaign (OpenClaw / OpenCode / ZeroClaw).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_env() {
  local name="$1"
  [[ -n "${!name:-}" ]] || fail "missing environment variable: $name"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

# Default ALPHADIANA_ROOT to the enclosing checkout when the bundle lives in
# deliverables/ inside the repo; an explicit export always wins.
if [[ -z "${ALPHADIANA_ROOT:-}" && -d "$BUNDLE_ROOT/../../alphadiana" ]]; then
  ALPHADIANA_ROOT="$(cd "$BUNDLE_ROOT/../.." && pwd)"
  export ALPHADIANA_ROOT
fi

require_env ALPHADIANA_ROOT
require_env OPENAI_BASE_URL
require_env OPENAI_API_KEY
require_env OPENCLAW_GATEWAY_TOKEN
require_env ROCK_BASE_URL
require_env ROCK_PROXY_URL

require_cmd curl
require_cmd docker
require_cmd python

[[ -d "$ALPHADIANA_ROOT/alphadiana" ]] || fail "not an AlphaDiana checkout: $ALPHADIANA_ROOT"

docker info >/dev/null

# Provider probe with the exact sampling contract of the campaign.
probe_payload='{"model":"Qwen/Qwen3.5-27B","messages":[{"role":"user","content":"Reply OK."}],"temperature":0.6,"top_p":0.95,"max_tokens":16,"stream":false,"chat_template_kwargs":{"enable_thinking":true}}'
curl -fsS --max-time 60 \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$probe_payload" \
  "${OPENAI_BASE_URL%/}/chat/completions" >/dev/null \
  || fail "provider probe failed against ${OPENAI_BASE_URL%/}/chat/completions"

# The OpenCode controller requests the lowercase alias; both ids must resolve.
opencode_probe_payload='{"model":"qwen/qwen3.5-27b","messages":[{"role":"user","content":"Reply OK."}],"temperature":0.6,"top_p":0.95,"max_tokens":16,"stream":false}'
curl -fsS --max-time 60 \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$opencode_probe_payload" \
  "${OPENAI_BASE_URL%/}/chat/completions" >/dev/null \
  || fail "provider rejected model id 'qwen/qwen3.5-27b'; add it via --served-model-name (see RUNBOOK.md section 3)"

# ROCK services power the OpenClaw gateway autodeploy and ZeroClaw sandboxes.
(
  cd "$ALPHADIANA_ROOT"
  python -m alphadiana.cli env
)

# Disk headroom: 3 harnesses x 1920 samples with top-20 logprob sidecars.
avail_gb=$(df -BG --output=avail "$ALPHADIANA_ROOT/results" 2>/dev/null | tail -1 | tr -dc '0-9' || echo 0)
if [[ "${avail_gb:-0}" -lt 200 ]]; then
  printf 'WARNING: only %sGB free under results/; logprob sidecars for 5760 samples may need 200GB+.\n' "${avail_gb:-?}" >&2
fi

(
  cd "$ALPHADIANA_ROOT"
  python -m alphadiana.cli validate "$BUNDLE_ROOT/configs/openclaw.yaml"
  python -m alphadiana.cli validate "$BUNDLE_ROOT/configs/opencode.yaml"
  python -m alphadiana.cli validate "$BUNDLE_ROOT/configs/zeroclaw.yaml"
)

if command -v hf >/dev/null 2>&1; then
  hf auth whoami >/dev/null || printf 'WARNING: hf CLI present but not logged in; needed before upload.\n' >&2
else
  printf 'WARNING: hf CLI not found; install huggingface_hub before upload.\n' >&2
fi

printf 'Preflight passed.\n'
