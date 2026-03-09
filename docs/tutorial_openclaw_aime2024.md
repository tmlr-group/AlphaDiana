# AlphaDiana 评估教程：OpenClaw + Qwen3-8B + AIME 2024

本教程以 **OpenClaw agent + Qwen3-8B 模型 + AIME 2024 数据集** 为例，演示如何使用 AlphaDiana 评估框架完成一次完整的端到端评估。

## 架构概览

```
AlphaDiana Runner
       │
       ▼
  ROCK Proxy (:9001)
       │
       ▼
  ROCK Sandbox (Docker)
       │
       ▼
  OpenClaw Gateway (:8080)
       │
       ▼
  vLLM (Qwen3-8B, :8000)
```

- **AlphaDiana**: 评估编排框架，负责加载数据集、调度任务、评分、保存结果
- **ROCK**: 沙箱系统，在 Docker 容器中运行 OpenClaw gateway
- **OpenClaw**: Agent 系统，通过多轮 LLM 调用进行推理
- **vLLM**: 高性能 LLM 推理服务，提供 OpenAI 兼容 API

## 前置条件

- Linux 服务器，有 NVIDIA GPU（建议 A800/A100，显存 ≥ 40GB）
- Docker 已安装且当前用户在 docker 组中
- Conda 包管理器

## Step 1: 创建 Conda 环境

```bash
conda create -n alphadiana python=3.11 -y
conda activate alphadiana
```

## Step 2: 安装 AlphaDiana

```bash
cd /hd2/chentao/AlphaDiana
pip install -e .
```

验证安装：

```bash
alphadiana list-benchmarks
# 输出:
#   - aime
#   - math
#   - hle
#   - frontier_math
#   - terminal_bench
#   - osworld
```

## Step 3: 启动 vLLM 服务

选一张空闲 GPU 启动 Qwen3-8B：

```bash
# 检查 GPU 使用情况
nvidia-smi

# 选一张空闲 GPU（例如 GPU 2）
CUDA_VISIBLE_DEVICES=2 python -m vllm.entrypoints.openai.api_server \
    --model /hd1/models/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218 \
    --host 0.0.0.0 \
    --port 8000 \
    --trust-remote-code \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    --max-model-len 16384 &
```

> **注意**: `--enable-auto-tool-choice --tool-call-parser hermes` 是 OpenClaw 所需的，因为 OpenClaw 使用 tool calling 进行多轮推理。

验证 vLLM 就绪：

```bash
curl http://localhost:8000/v1/models
# 应返回模型列表
```

## Step 4: 启动 ROCK 沙箱环境

### 4.1 安装 ROCK

```bash
cd /hd2/chentao/ROCK
pip install -e .
```

### 4.2 安装 Redis Stack

ROCK 依赖 Redis Stack（需要 JSON.SET 命令）：

```bash
sg docker -c 'docker run -d --name redis-stack \
    -p 6379:6379 \
    redis/redis-stack-server:latest'
```

### 4.3 配置 ROCK

创建配置文件 `~/.rock/config.ini`：

```ini
[ray]
address = auto
```

设置环境变量并创建 `.venv` 软链接：

```bash
export ROCK_PROJECT_ROOT=/hd2/chentao/ROCK

# LocalRuntimeEnv 需要 .venv 指向 Python 环境
ln -sf $(conda info --base)/envs/alphadiana /hd2/chentao/ROCK/.venv
```

### 4.4 启动 Ray + ROCK 服务

```bash
# 启动 Ray（ROCK 基于 Ray）
export ROCK_PROJECT_ROOT=/hd2/chentao/ROCK
ray start --head --port=6380

# 启动 ROCK admin（写操作，端口 9000）
sg docker -c 'python -m rock.deployments.admin &'

# 启动 ROCK proxy（读/运行时操作，端口 9001）
sg docker -c 'python -m rock.deployments.proxy &'
```

验证 ROCK 就绪：

```bash
curl http://localhost:9000/health     # admin 健康检查
curl http://localhost:9001/health     # proxy 健康检查
```

## Step 5: 部署 OpenClaw Gateway

### 5.1 准备部署配置

部署配置已在 `openclaw_deploy/` 目录下：

**`openclaw_deploy/rock_agent_config.yaml`** — ROCK agent 配置：

```yaml
working_dir: "."

run_cmd: >
  OPENCLAW_CONFIG_PATH=${working_dir}/openclaw.json
  nohup openclaw gateway >> /root/gateway.log 2>&1 &

runtime_env_config:
  type: node
  npm_registry: https://registry.npmmirror.com
  custom_install_cmd: npm install -g openclaw@2026.3.7
  install_timeout: 1200

env:
  OPENAI_BASE_URL: "http://192.0.2.14:8000/v1"    # ← 改为你的 vLLM 地址
  OPENAI_API_KEY: "EMPTY"
  OPENAI_MODEL_NAME: "/hd1/models/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218"
  OPENCLAW_GATEWAY_TOKEN: "OPENCLAW"
```

**`openclaw_deploy/openclaw.json`** — OpenClaw gateway 配置，定义模型 provider 和 gateway 认证方式。

### 5.2 创建 Sandbox 并部署

```bash
cd /hd2/chentao/AlphaDiana/openclaw_deploy
python deploy.py
```

脚本会输出 Sandbox ID，记下来：

```
Sandbox ID: a37118857bee43188375c7ce2f343eb5
API base: http://127.0.0.1:9001/apis/envs/sandbox/v1/sandboxes/a37118857bee43188375c7ce2f343eb5/proxy/v1
```

设置环境变量（后续测试用）：

```bash
export OPENCLAW_SANDBOX_ID=a37118857bee43188375c7ce2f343eb5
```

验证 OpenClaw gateway 可用：

```bash
curl -X POST \
  "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/${OPENCLAW_SANDBOX_ID}/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"What is 2+3?"}],"max_tokens":256}'
```

## Step 6: 编写评估配置

创建 YAML 配置文件 `configs/examples/openclaw_aime2024.yaml`：

```yaml
run_id: "openclaw-qwen3-8b-aime2024-001"

agent:
  name: openclaw
  version: "latest"
  config:
    api_base: "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1"
    model: openclaw
    gateway_token: "OPENCLAW"
    temperature: 0.0
    max_tokens: 8192

benchmark:
  name: aime
  config:
    dataset: "HuggingFaceH4/aime_2024"
    split: "train"

sandbox: null   # OpenClaw 已在 ROCK 中运行，不需要额外的 sandbox

scorer:
  name: numeric
  config:
    tolerance: 1e-6

max_concurrent: 1
output_dir: "./results"
metadata:
  author: "your_name"
  gpu: "A800"
  notes: "OpenClaw + Qwen3-8B on AIME 2024"
```

> **重要**: 将 `<SANDBOX_ID>` 替换为 Step 5 中获得的实际 Sandbox ID。

### 配置字段说明

| 字段 | 说明 |
|------|------|
| `run_id` | 本次运行的唯一标识，结果文件名基于此 |
| `agent.name` | 使用的 agent 名称（`openclaw`, `direct_llm`） |
| `agent.config` | Agent 特定配置（API 地址、模型名、token 等） |
| `benchmark.name` | 数据集名称（`aime`, `math`, `hle` 等） |
| `benchmark.config` | 数据集配置（HuggingFace 路径、split） |
| `scorer.name` | 评分方式（`numeric`, `exact_match`, `llm_judge`） |
| `max_concurrent` | 并行任务数（1 = 串行） |
| `output_dir` | JSONL 结果文件输出目录 |

## Step 7: 运行评估

### 方式 A: CLI 命令（推荐）

```bash
# 先验证配置
alphadiana validate configs/examples/openclaw_aime2024.yaml

# 运行评估
alphadiana run configs/examples/openclaw_aime2024.yaml
```

输出示例：

```
Run completed: openclaw-qwen3-8b-aime2024-001
  Accuracy:   0.2333
  Mean Score: 0.2333
  Tasks:      30/30 completed
```

### 方式 B: Python API

```python
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.runner.runner import Runner

config = ExperimentConfig.from_yaml("configs/examples/openclaw_aime2024.yaml")
runner = Runner(config)

runner.setup()
try:
    summary = runner.run()
    print(f"Accuracy: {summary.accuracy:.4f} ({int(summary.accuracy * 30)}/30)")
    print(f"Mean Score: {summary.mean_score:.4f}")
    print(f"Mean Wall Time: {summary.mean_wall_time_sec:.1f}s")
    print(f"Total Tokens: {summary.total_tokens}")
finally:
    runner.teardown()
```

### 方式 C: 直接构造 ExperimentConfig（无需 YAML 文件）

```python
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.runner.runner import Runner

config = ExperimentConfig(
    run_id="my-test-run",
    agent_name="openclaw",
    agent_version="latest",
    agent_config={
        "api_base": "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1",
        "model": "openclaw",
        "gateway_token": "OPENCLAW",
        "temperature": 0.0,
        "max_tokens": 8192,
    },
    benchmark_name="aime",
    benchmark_config={
        "dataset": "HuggingFaceH4/aime_2024",
        "split": "train",
    },
    scorer_name="numeric",
    scorer_config={"tolerance": 1e-6},
    max_concurrent=1,
    output_dir="./results",
)

runner = Runner(config)
runner.setup()
try:
    summary = runner.run()
finally:
    runner.teardown()
```

## Step 8: 查看结果

### 8.1 结果 JSONL 文件

评估结果保存在 `{output_dir}/{run_id}.jsonl`，每行一条 JSON 记录：

```bash
cat results/openclaw-qwen3-8b-aime2024-001.jsonl | python -m json.tool | head -30
```

每条记录包含：

```json
{
  "task_id": "aime_60",
  "problem": "Every morning Aya goes for a ...",
  "ground_truth": "204",
  "predicted": "204",
  "correct": true,
  "score": 1.0,
  "rationale": "Parsed predicted=204.0, expected=204.0, diff=0.0 <= tolerance=1e-06",
  "trajectory": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...（完整推理过程）..."}
  ],
  "token_usage": {"prompt_tokens": 150, "completion_tokens": 1200, "total_tokens": 1350},
  "wall_time_sec": 45.3,
  "timestamp": "2026-03-08T08:07:39.226945+00:00"
}
```

### 8.2 生成 Markdown 报告

```bash
alphadiana report ./results
```

或在 Python 中：

```python
from alphadiana.results.report import ReportGenerator
from alphadiana.results.result_store import ResultStore

store = ResultStore(output_dir="./results", run_id="openclaw-qwen3-8b-aime2024-001")
gen = ReportGenerator()
summary = gen.generate(store, config)
print(gen.to_markdown(summary))
```

### 8.3 逐题分析

```python
import json

with open("results/openclaw-qwen3-8b-aime2024-001.jsonl") as f:
    results = [json.loads(line) for line in f]

correct = sum(1 for r in results if r["correct"])
print(f"Accuracy: {correct}/{len(results)} = {correct/len(results):.4f}")

print(f"\n{'Task ID':<20} {'GT':>6} {'Pred':>6} {'OK':>4} {'Time':>8}")
print("-" * 50)
for r in results:
    mark = "Y" if r["correct"] else "N"
    print(f"{r['task_id']:<20} {r['ground_truth']:>6} {str(r['predicted']):>6} {mark:>4} {r['wall_time_sec']:>7.1f}s")
```

## Step 9: 运行测试套件（验证环境）

AlphaDiana 提供 6 个阶段的测试：

```bash
# Phase 1: 数据加载和评分（不需要外部服务）
python -m pytest tests/test_phase1_data.py -v

# Phase 2: vLLM 服务（需要 vLLM 已启动）
python -m pytest tests/test_phase2_vllm.py -v -m integration

# Phase 3: ROCK 沙箱（需要 ROCK 已启动）
python -m pytest tests/test_phase3_rock.py -v -m integration

# Phase 4: OpenClaw Gateway（需要 OpenClaw 已部署）
OPENCLAW_SANDBOX_ID=<your_id> python -m pytest tests/test_phase4_openclaw.py -v -m integration

# Phase 5: 单题验证
OPENCLAW_SANDBOX_ID=<your_id> python -m pytest tests/test_phase5_single.py -v

# Phase 6: 全量 AIME 2024 评估（30 题，耗时较长）
OPENCLAW_SANDBOX_ID=<your_id> python -m pytest tests/test_phase6_full.py -v -m integration

# 一次跑全部（不含集成测试）
python -m pytest tests/ -v -k "not integration"

# 全部测试（需要所有服务就绪）
OPENCLAW_SANDBOX_ID=<your_id> python -m pytest tests/ -v
```

## 扩展：使用其他 Agent/Benchmark 组合

### Direct LLM + MATH 数据集

```yaml
run_id: "direct-llm-qwen3-8b-math"
agent:
  name: direct_llm
  config:
    model: "/hd1/models/models--Qwen--Qwen3-8B/snapshots/..."
    api_base: "http://localhost:8000/v1"
    api_key: "EMPTY"
    temperature: 0.0
    max_tokens: 4096
benchmark:
  name: math
  config:
    dataset: "hendrycks/competition_math"
    split: "test"
scorer:
  name: exact_match
max_concurrent: 4
output_dir: "./results"
```

> **Direct LLM** 直接调用 vLLM API，不经过 OpenClaw/ROCK，适合快速基线测试。

### 自定义 Agent

继承 `Agent` 基类并注册：

```python
# alphadiana/agent/my_agent.py
from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry

class MyAgent(Agent):
    name = "my_agent"

    def setup(self, config: dict) -> None:
        self.version = config.get("version", "1.0")
        # 初始化你的 agent ...

    def solve(self, task, sandbox=None) -> AgentResponse:
        # 调用你的模型/服务 ...
        return AgentResponse(
            answer="42",
            trajectory=[...],
            raw_output="...",
            token_usage={},
            wall_time_sec=1.0,
        )

    def teardown(self) -> None:
        pass

AgentRegistry.register("my_agent", MyAgent)
```

## 常见问题

**Q: OpenClaw 超时返回空响应？**
OpenClaw 的默认超时为 120 秒，复杂 AIME 题目可能需要多轮 LLM 调用。可在 `openclaw.json` 中调整 `maxConcurrent` 或模型参数。AlphaDiana 会将超时视为预测失败（空答案）。

**Q: vLLM 报 400 错误 "auto tool choice requires --enable-auto-tool-choice"？**
OpenClaw 使用 tool calling，需要 vLLM 启动时加 `--enable-auto-tool-choice --tool-call-parser hermes`。

**Q: ROCK sandbox 容器立即退出？**
检查：
1. `ROCK_PROJECT_ROOT` 是否正确设置
2. `.venv` 软链接是否指向有效的 Python 环境
3. `gem-llm` 包是否已安装（`pip install gem-llm`）
4. Ray 是否在设置 `ROCK_PROJECT_ROOT` 后重启

**Q: 如何只跑数据集的子集？**
在 `benchmark_config` 中可以指定 `split` 和其他 HuggingFace 参数来控制加载的数据量。
