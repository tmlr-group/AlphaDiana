#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR%/repro}"
cd "${PROJECT_ROOT}"

OPENCLAW_IMAGE="${OPENCLAW_IMAGE:-tmlrgroup/alphadiana:v1}"
OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-}"

if [ -z "${OPENAI_BASE_URL}" ] || [ -z "${OPENAI_API_KEY}" ] || [ -z "${OPENAI_MODEL_NAME}" ]; then
  cat >&2 <<'EOF'
Missing required environment variables.

Set:
  OPENAI_BASE_URL
  OPENAI_API_KEY
  OPENAI_MODEL_NAME

Example:
  export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
  export OPENAI_API_KEY="sk-or-..."
  export OPENAI_MODEL_NAME="moonshotai/kimi-k2.5"
EOF
  exit 1
fi

echo "[1/6] Bootstrapping local AlphaDiana + ROCK environment"
bash scripts/quickstart.sh alphadiana

echo "[2/6] Loading ROCK environment"
# shellcheck disable=SC1091
source scripts/rock_env.sh
# shellcheck disable=SC1091
source scripts/.rock_ports.env

# Detect and initialise conda from common installation paths
_conda_sh=""
for _candidate in \
    "${CONDA_PREFIX:+${CONDA_PREFIX}/../../etc/profile.d/conda.sh}" \
    "${HOME}/miniconda3/etc/profile.d/conda.sh" \
    "${HOME}/anaconda3/etc/profile.d/conda.sh" \
    "/opt/conda/etc/profile.d/conda.sh"; do
  if [ -f "${_candidate}" ]; then
    _conda_sh="${_candidate}"
    break
  fi
done
if [ -z "${_conda_sh}" ] && command -v conda >/dev/null 2>&1; then
  _conda_sh="$(conda info --base 2>/dev/null)/etc/profile.d/conda.sh"
fi
if [ -z "${_conda_sh}" ] || [ ! -f "${_conda_sh}" ]; then
  echo "Could not locate conda.sh. Ensure conda is installed and in PATH." >&2
  exit 1
fi
# shellcheck disable=SC1090
source "${_conda_sh}"
conda activate alphadiana

echo "[3/6] Ensuring reasoning image is available: ${OPENCLAW_IMAGE}"
if ! docker image inspect "${OPENCLAW_IMAGE}" >/dev/null 2>&1; then
  echo "  image not present locally, trying docker pull"
  docker pull "${OPENCLAW_IMAGE}"
fi

TMPDIR_REPRO="$(mktemp -d /tmp/openclaw-reasoning-repro.XXXXXX)"
export TMPDIR_REPRO
cp openclaw_deploy/openclaw.json "${TMPDIR_REPRO}/openclaw.json"
cp openclaw_deploy/rock_agent_config.yaml "${TMPDIR_REPRO}/rock_agent_config.yaml"

python - <<'PY'
import json
import os
from pathlib import Path
import yaml

tmpdir = Path(os.environ["TMPDIR_REPRO"])
json_path = tmpdir / "openclaw.json"
yaml_path = tmpdir / "rock_agent_config.yaml"

with json_path.open("r", encoding="utf-8") as f:
    cfg = json.load(f)

cfg["env"]["OPENAI_BASE_URL"] = os.environ["OPENAI_BASE_URL"]
cfg["env"]["OPENAI_API_KEY"] = os.environ["OPENAI_API_KEY"]
cfg["env"]["OPENAI_MODEL_NAME"] = os.environ["OPENAI_MODEL_NAME"]

with json_path.open("w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

with yaml_path.open("r", encoding="utf-8") as f:
    agent_cfg = yaml.safe_load(f)

agent_cfg["working_dir"] = str(tmpdir)

with yaml_path.open("w", encoding="utf-8") as f:
    yaml.safe_dump(agent_cfg, f, sort_keys=False, allow_unicode=True)
PY

echo "[4/6] Deploying OpenClaw sandbox"
DEPLOY_LOG="${TMPDIR_REPRO}/deploy.log"
python openclaw_deploy/deploy.py \
  --agent-config "${TMPDIR_REPRO}/rock_agent_config.yaml" \
  --base-url "${ROCK_BASE_URL}" \
  --proxy-url "${ROCK_PROXY_URL}" \
  --image "${OPENCLAW_IMAGE}" | tee "${DEPLOY_LOG}"

SANDBOX_ID="$(grep -E '^Sandbox ID:' "${DEPLOY_LOG}" | tail -n 1 | awk '{print $3}')"
if [ -z "${SANDBOX_ID}" ]; then
  echo "Failed to parse SANDBOX_ID from deploy log: ${DEPLOY_LOG}" >&2
  exit 1
fi

PROXY_CHAT_URL="${ROCK_PROXY_URL}/sandboxes/${SANDBOX_ID}/proxy/v1/chat/completions"
export SANDBOX_ID PROXY_CHAT_URL

echo "[5/6] Running streamed smoke test"
python - <<'PY'
import httpx
import os

url = os.environ["PROXY_CHAT_URL"]
body = {
    "model": "openclaw",
    "stream": True,
    "messages": [{"role": "user", "content": "test: reply ok"}],
}
with httpx.Client(timeout=30.0) as client:
    with client.stream(
        "POST",
        url,
        headers={"Authorization": "Bearer OPENCLAW", "Content-Type": "application/json"},
        json=body,
    ) as response:
        print("status", response.status_code)
        seen = 0
        for line in response.iter_lines():
            if not line:
                continue
            print(line)
            seen += 1
            if seen >= 8:
                break
PY

echo "[6/6] Done"
echo
echo "SANDBOX_ID=${SANDBOX_ID}"
echo "ROCK proxy base: ${ROCK_PROXY_URL}/sandboxes/${SANDBOX_ID}/proxy/v1"
echo
echo "Manual math repro:"
cat <<EOF
curl -N "${PROXY_CHAT_URL}" \\
  -H "Authorization: Bearer OPENCLAW" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "openclaw",
    "stream": true,
    "max_tokens": 32768,
    "messages": [
      {
        "role": "user",
        "content": "Let \$\\\\triangle ABC\$ be a right triangle with \$\\\\angle A = 90^\\\\circ\$ and \$BC = 38\$. There exist points \$K\$ and \$L\$ inside the triangle such that \$\$AK = AL = BK = CL = KL = 14.\$\$ The area of the quadrilateral \$BKLC\$ can be expressed as \$n\\\\sqrt{3}\$ for some positive integer \$n\$. Find \$n\$. Reasoning step by step."
      }
    ]
  }'
EOF
