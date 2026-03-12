# 在 AlphaDiana 跑「某个模型 x 某个 Benchmark」的标准流程

这份文档是一个可复用模板，目标是快速完成一次评测并拿到可对比结果。

## 0. 适用范围

- `direct_llm`：模型直连（baseline）
- `openclaw`：通过 ROCK sandbox + OpenClaw gateway 跑 agent
- benchmark 侧用 AlphaDiana 已注册 loader（常见是 `aime`，可加载任意 HF 数据集）

## 1. 先准备运行环境

在仓库根目录执行（每个新终端都要做）：

```bash
source /home/user/miniconda3/etc/profile.d/conda.sh
conda activate alphadiana
cd /home/user/projects/AlphaDiana
source dev/rock_env.sh
source dev/.rock_ports.env
```

若用 OpenRouter：

- 确认根目录 `.env` 里有 `OPENROUTER_API_KEY`
- 或在当前 shell 直接 `export OPENROUTER_API_KEY=...`

## 2. 选定评测对象（模型 + benchmark）

先明确 4 件事：

1. 模型 ID（例：`qwen/qwen-2.5-7b-instruct`）
2. benchmark loader（例：`aime`）
3. 数据集 ID（例：`MathArena/hmmt_feb_2026`）
4. 字段映射（通常 `problem_field=problem`, `answer_field=answer`）

可先快速检查数据集字段：

```bash
python - <<'PY'
from datasets import load_dataset
ds = load_dataset("MathArena/hmmt_feb_2026", split="train")
print("rows:", len(ds))
print("columns:", ds.column_names)
print("sample_answer:", ds[0]["answer"])
PY
```

## 3. 跑 baseline（`direct_llm`）

新建配置，例如 `dev/generated/direct_llm_<model>_<benchmark>.yaml`：

```yaml
run_id: "direct-llm-qwen25-7b-hmmt-feb-2026"

agent:
  name: direct_llm
  version: "v0.1.0"
  config:
    model: "qwen/qwen-2.5-7b-instruct"
    api_base: "https://openrouter.ai/api/v1"
    api_key: "YOUR_OPENROUTER_API_KEY"
    temperature: 0.0
    max_tokens: 4096

benchmark:
  name: aime
  config:
    dataset: "MathArena/hmmt_feb_2026"
    split: "train"
    problem_field: "problem"
    answer_field: "answer"

sandbox: null

scorer:
  name: exact_match

max_concurrent: 1
output_dir: "./results"
metadata:
  author: "your_name"
  notes: "Direct LLM baseline"
```

执行：

```bash
alphadiana validate dev/generated/direct_llm_<model>_<benchmark>.yaml
alphadiana run dev/generated/direct_llm_<model>_<benchmark>.yaml
```

## 4. 跑 OpenClaw（同 benchmark）

### 4.1 前提检查

确保这几个服务是健康的：

- Redis
- Ray
- ROCK admin（如 `:9010`）
- ROCK proxy（如 `:9011`）
- OpenClaw sandbox 已启动（有 `OPENCLAW_SANDBOX_ID`）

可先做一次真实请求 smoke test：

```bash
export OPENCLAW_SANDBOX_ID=<your_sandbox_id>
curl -X POST \
  "${ROCK_PROXY_URL}/sandboxes/${OPENCLAW_SANDBOX_ID}/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"What is 2+3? Return only the number."}],"temperature":0.0,"max_tokens":64}'
```

### 4.2 配置与执行

新建配置，例如 `dev/generated/openclaw_<model>_<benchmark>.yaml`：

```yaml
run_id: "openclaw-qwen25-7b-hmmt-feb-2026"

agent:
  name: openclaw
  version: "2026.3.7"
  config:
    api_base: "http://127.0.0.1:9011/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1"
    model: openclaw
    gateway_token: "OPENCLAW"
    temperature: 0.0
    max_tokens: 16384
    request_timeout: 600
    max_attempts: 2

benchmark:
  name: aime
  config:
    dataset: "MathArena/hmmt_feb_2026"
    split: "train"
    problem_field: "problem"
    answer_field: "answer"

sandbox: null

scorer:
  name: exact_match

max_concurrent: 1
output_dir: "./results"
metadata:
  author: "your_name"
  notes: "OpenClaw agent run"
```

执行：

```bash
alphadiana validate dev/generated/openclaw_<model>_<benchmark>.yaml
alphadiana run dev/generated/openclaw_<model>_<benchmark>.yaml
```

## 5. 看结果与对比

每次 run 会生成：

- `results/<run_id>.jsonl`（逐题明细）
- `results/<run_id>.summary.json`（聚合结果）

快速查看：

```bash
cat results/<run_id>.summary.json
wc -l results/<run_id>.jsonl
```

对比两次 run（baseline vs openclaw）：

```bash
python - <<'PY'
import json
from pathlib import Path

runs = [
    "direct-llm-qwen25-7b-hmmt-feb-2026",
    "openclaw-qwen25-7b-hmmt-feb-2026",
]
for rid in runs:
    p = Path("results") / f"{rid}.summary.json"
    d = json.loads(p.read_text())
    print(rid, "accuracy=", d.get("accuracy"), "completed=", d.get("completed"), "total=", d.get("total_tasks"))
PY
```

## 6. 常见坑

1. `api_key` 未正确注入（配置里是字面量字符串而不是实际 key）。
2. `OPENCLAW_SANDBOX_ID` 与 `api_base` 不一致。
3. benchmark 字段名不对（`problem_field/answer_field` 与数据集列不匹配）。
4. 共享机器端口冲突，忘记 `source dev/.rock_ports.env`。
5. `run_id` 重复导致文件被覆盖，建议每次运行改唯一 ID。
