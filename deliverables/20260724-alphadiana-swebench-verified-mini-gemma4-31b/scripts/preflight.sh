#!/usr/bin/env bash
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

require_env ALPHADIANA_ROOT
require_env DIRECTLLM_SWE_VERIFIED_ROOT
require_env OPENAI_BASE_URL
require_env SWE_CONTAINER_OPENAI_BASE_URL
require_env OPENAI_API_KEY
require_env OPENCLAW_GATEWAY_TOKEN

require_cmd curl
require_cmd docker
require_cmd python

[[ -d "$ALPHADIANA_ROOT/alphadiana" ]] || fail "not an AlphaDiana checkout: $ALPHADIANA_ROOT"
[[ -x "$DIRECTLLM_SWE_VERIFIED_ROOT/.venv/bin/sweagent" ]] \
  || fail "SWE-agent executable not found under $DIRECTLLM_SWE_VERIFIED_ROOT/.venv/bin"
[[ -d "$DIRECTLLM_SWE_VERIFIED_ROOT/SWE-agent" ]] \
  || fail "SWE-agent checkout not found: $DIRECTLLM_SWE_VERIFIED_ROOT/SWE-agent"

docker info >/dev/null

probe_payload='{"model":"google/gemma-4-31B-it","messages":[{"role":"user","content":"Reply OK."}],"temperature":0.0,"top_p":0.95,"max_tokens":16,"stream":false,"chat_template_kwargs":{"enable_thinking":true}}'
curl -fsS --max-time 60 \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$probe_payload" \
  "${OPENAI_BASE_URL%/}/chat/completions" >/dev/null

if [[ "${DEEP_CONTAINER_PROBE:-0}" == "1" ]]; then
  docker run --rm curlimages/curl:8.10.1 \
    -fsS --max-time 60 \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    -H 'Content-Type: application/json' \
    -d "$probe_payload" \
    "${SWE_CONTAINER_OPENAI_BASE_URL%/}/chat/completions" >/dev/null
fi

(
  cd "$ALPHADIANA_ROOT"
  python -m alphadiana.cli validate "$BUNDLE_ROOT/configs/openclaw.yaml"
  python -m alphadiana.cli validate "$BUNDLE_ROOT/configs/opencode.yaml"
  python -m alphadiana.cli validate "$BUNDLE_ROOT/configs/zeroclaw.yaml"
)

if command -v hf >/dev/null 2>&1; then
  hf auth whoami >/dev/null
else
  printf 'WARNING: hf CLI not found; install huggingface_hub before upload.\n' >&2
fi

printf 'Preflight passed.\n'
