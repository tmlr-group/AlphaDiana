#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
REPRO_ROOT="${PROJECT_ROOT}/repro/openclaw_reasoning"
GENERATED_DIR="${REPRO_ROOT}/generated"

source "${GENERATED_DIR}/runtime.env"
export PYTHONPATH="${PROJECT_ROOT}/ref/ROCK:${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy

MATH_LOG="${GENERATED_DIR}/math_stream.log"
RANDOM_LOG="${GENERATED_DIR}/random_tool.log"
SMOKE_CONFIG="${GENERATED_DIR}/smoke.yaml"
SMOKE_LOG="${GENERATED_DIR}/smoke.log"
SUMMARY_JSON="${GENERATED_DIR}/summary.json"
RUN_ID="openclaw-kimi-k2-5-repro-smoke"

curl -N -sS \
  "http://127.0.0.1:${ROCK_PROXY_PORT}/apis/envs/sandbox/v1/sandboxes/${SID}/proxy/v1/chat/completions" \
  -H "Authorization: bearer ${OPENCLAW_GATEWAY_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openclaw",
    "stream": true,
    "max_tokens": 32768,
    "messages": [
      {
        "role": "user",
        "content": "Let $\\triangle ABC$ be a right triangle with $\\angle A = 90^\\circ$ and $BC = 38$. There exist points $K$ and $L$ inside the triangle such that $$AK = AL = BK = CL = KL = 14.$$ The area of the quadrilateral $BKLC$ can be expressed as $n\\sqrt{3}$ for some positive integer $n$. Find $n$. Reasoning step by step."
      }
    ]
  }' | tee "${MATH_LOG}"

if ! grep -q 'reasoning_content' "${MATH_LOG}"; then
  echo "math_stream.log does not contain reasoning_content" >&2
  exit 1
fi

curl -N -sS \
  "http://127.0.0.1:${ROCK_PROXY_PORT}/apis/envs/sandbox/v1/sandboxes/${SID}/proxy/v1/chat/completions" \
  -H "Authorization: bearer ${OPENCLAW_GATEWAY_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openclaw",
    "stream": true,
    "max_tokens": 32768,
    "messages": [
      {
        "role": "user",
        "content": "请用 Python 的 random 库，以 20060307 作为随机种子（seed），生成 5 个 1 到 100 之间的随机整数。请告诉我这 5 个数分别是什么？"
      }
    ]
  }' | tee "${RANDOM_LOG}"

cat > "${SMOKE_CONFIG}" <<EOF
run_id: "${RUN_ID}"

agent:
  name: openclaw
  version: "2026.3.14"
  config:
    api_base: "http://127.0.0.1:${ROCK_PROXY_PORT}/apis/envs/sandbox/v1/sandboxes/${SID}/proxy/v1"
    model: openclaw
    gateway_token: "${OPENCLAW_GATEWAY_TOKEN}"
    max_tokens: 32768
    request_timeout: 1800
    max_attempts: 3
    system_prompt: |
      You are an expert problem solver. When given a problem, actively use your available tools and skills throughout your reasoning process. Do not attempt to solve problems purely in your head when tools can help. Use code execution, search, or any other available capabilities to verify intermediate steps, explore approaches, and confirm your final answer.

      When you have reached your final answer, you MUST present it in the following format:

      $$\boxed{your answer here}$$

      Do not skip the boxed format. The boxed answer must appear at the very end of your response and contain only the final answer, not explanations.

benchmark:
  name: aime
  config:
    dataset: "opencompass/AIME2025"
    data_config: "AIME2025-I"
    split: "test"
    max_tasks: 1

sandbox: null

scorer:
  name: math_verify
  config:
    tolerance: 1e-6

max_concurrent: 1
output_dir: "./results"
EOF

unset HF_ENDPOINT
export HF_HUB_OFFLINE=1
export HF_DATASETS_OFFLINE=1
conda run --no-capture-output -n alphadiana \
  alphadiana run "${SMOKE_CONFIG}" 2>&1 | tee "${SMOKE_LOG}"

python - <<'PY' "${PROJECT_ROOT}/results/${RUN_ID}/tasks/aime_0.json" "${SUMMARY_JSON}"
import json, sys
src, out = sys.argv[1], sys.argv[2]
data = json.load(open(src, encoding="utf-8"))[0]
msg = (((data.get("response_json") or {}).get("choices") or [{}])[0].get("message") or {})
summary = {
    "run_id": data.get("run_id"),
    "predicted": data.get("predicted"),
    "correct": data.get("correct"),
    "raw_output_empty": not bool((data.get("raw_output") or "").strip()),
    "has_reasoning_content": bool(msg.get("reasoning_content")),
    "tool_names": [c.get("tool") for s in data.get("trajectory", []) for c in (s.get("tool_calls") or [])],
    "tool_result_previews": [(s.get("content") or "")[:160] for s in data.get("trajectory", []) if s.get("role") == "toolResult"],
}
open(out, "w", encoding="utf-8").write(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY
