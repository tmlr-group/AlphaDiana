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
SMOKE_LOG="${GENERATED_DIR}/smoke.log"
SUMMARY_JSON="${GENERATED_DIR}/summary.json"
VERIFY_RANDOM_TOOL="${VERIFY_RANDOM_TOOL:-0}"

: > "${MATH_LOG}"
set +e
timeout --signal=INT 20s curl -N -sS \
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
  }' | tee "${MATH_LOG}" >/dev/null
math_curl_status=${PIPESTATUS[0]}
math_tee_status=${PIPESTATUS[1]-0}
set -e

if [[ ${math_curl_status} -ne 0 && ${math_curl_status} -ne 124 && ${math_curl_status} -ne 130 ]]; then
  echo "math reasoning probe curl failed with status ${math_curl_status}" >&2
  exit "${math_curl_status}"
fi

if [[ ${math_tee_status} -ne 0 ]]; then
  echo "math reasoning probe pipeline failed" >&2
  exit 1
fi

if ! grep -q 'reasoning_content' "${MATH_LOG}"; then
  echo "math_stream.log does not contain reasoning_content" >&2
  exit 1
fi

if [[ "${VERIFY_RANDOM_TOOL}" == "1" ]]; then
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
fi

unset HF_ENDPOINT
export HF_HUB_OFFLINE=1
export HF_DATASETS_OFFLINE=1
conda run --no-capture-output -n alphadiana python - <<'PY' \
  "${ROCK_PROXY_PORT}" "${SID}" "${OPENCLAW_GATEWAY_TOKEN}" "${SUMMARY_JSON}" 2>&1 | tee "${SMOKE_LOG}"
import json
import sys

from alphadiana.agent.openclaw import OpenClawAgent
from alphadiana.benchmark.base import BenchmarkTask

rock_proxy_port, sid, gateway_token, summary_path = sys.argv[1:5]

agent = OpenClawAgent()
agent.setup({
    "api_base": f"http://127.0.0.1:{rock_proxy_port}/apis/envs/sandbox/v1/sandboxes/{sid}/proxy/v1",
    "model": "openclaw",
    "gateway_token": gateway_token,
    "max_tokens": 32768,
    "request_timeout": 300,
    "max_attempts": 1,
    "system_prompt": (
        "You are an expert problem solver. Actively use your tools. "
        "Give the final answer plainly at the end."
    ),
})

task = BenchmarkTask(
    task_id="random_tool_smoke",
    problem="请用 Python 的 random 库，以 20060307 作为随机种子（seed），生成 5 个 1 到 100 之间的随机整数。请告诉我这 5 个数分别是什么？",
    ground_truth="[83, 92, 28, 65, 83]",
)

response = agent.solve(task)
msg = (((response.response_json or {}).get("choices") or [{}])[0].get("message") or {})
summary = {
    "task_id": task.task_id,
    "answer": response.answer,
    "raw_output_empty": not bool((response.raw_output or "").strip()),
    "has_reasoning_content": bool(msg.get("reasoning_content")),
    "tool_names": [c.get("tool") for s in response.trajectory for c in (s.get("tool_calls") or [])],
    "tool_result_previews": [(s.get("content") or "")[:160] for s in response.trajectory if s.get("role") == "toolResult"],
}
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY
