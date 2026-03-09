# AlphaDiana 完整上手教程

AlphaDiana 是一个基金模型与 Agent 系统的评估框架，支持在多个 Benchmark（AIME、MATH、HLE 等）上评测不同的 Agent。

本教程以 **Qwen3-8B + AIME 2024** 为例，从零开始，一步步搭建并运行评估。

## 目录

- [0. 架构概览](#0-架构概览)
- [1. 环境准备](#1-环境准备)
- [2. 安装 AlphaDiana](#2-安装-alphadiana)
- [3. 快速验证（Direct LLM 基线）](#3-快速验证direct-llm-基线)
- [4. 搭建 ROCK 沙箱环境](#4-搭建-rock-沙箱环境)
- [5. 部署 OpenClaw Agent](#5-部署-openclaw-agent)
- [6. 运行 OpenClaw 评估](#6-运行-openclaw-评估)
- [7. 查看和分析结果](#7-查看和分析结果)
- [8. 自定义配置](#8-自定义配置)
- [附录 A：项目结构](#附录-a项目结构)
- [附录 B：常见问题](#附录-b常见问题)

---

## 0. 架构概览

AlphaDiana 有两种评估模式：

### 模式一：Direct LLM（直接调 LLM，简单快速）

```
AlphaDiana Runner
       │  直接调用 OpenAI 兼容 API
       ▼
  vLLM / 任何 OpenAI 兼容 API
```

- 只需要一个能跑的 LLM 服务
- 测的是**裸模型能力**（单轮问答，不带工具调用）
- 适合快速基线测试

### 模式二：OpenClaw（Agent 系统，完整评估）

```
AlphaDiana Runner
       │
       ▼
  ROCK Proxy (:9001)
       │  转发请求到沙箱
       ▼
  ROCK 沙箱 (Docker 容器)
       │
       ▼
  OpenClaw Gateway (:8080)
       │  内部多轮 Agent 循环:
       │  thinking → tool_call → execute → reasoning → ...
       ▼
  vLLM (:8000)
```

- 需要搭建 ROCK 沙箱 + 部署 OpenClaw
- 测的是 **Agent 系统能力**（多轮推理、工具调用、代码执行等）
- OpenClaw 内部自动构建 system prompt（~8K tokens），包含 25 个工具定义

**建议先跑模式一确认环境没问题，再搭模式二。**

---

## 1. 环境准备

### 1.1 硬件要求

| 组件 | 最低要求 |
|------|---------|
| GPU | 1 张 40GB+ 显存（A100/A800），用于 vLLM |
| 内存 | 32GB+ |
| 磁盘 | 50GB+ 空闲 |
| Docker | 已安装，当前用户在 docker 组 |

### 1.2 创建 Conda 环境

```bash
conda create -n alphadiana python=3.11 -y
conda activate alphadiana
```

> Python 版本必须 >=3.10，推荐 3.11。不要用 3.13（ROCK 依赖 gem-llm 只支持 <=3.12）。

### 1.3 安装 vLLM

```bash
pip install vllm
```

### 1.4 准备模型

以 Qwen3-8B 为例。如果模型已下载到本地，记下路径；如果没有：

```bash
# 方式一：用 huggingface-cli 下载
pip install huggingface_hub
huggingface-cli download Qwen/Qwen3-8B --local-dir /your/path/Qwen3-8B

# 方式二：如果服务器上已有，直接用路径
# 例如: /hd1/models/models--Qwen--Qwen3-8B/snapshots/b968826d...
```

### 1.5 启动 vLLM

选一张空闲 GPU：

```bash
# 查看 GPU 使用情况
nvidia-smi

# 启动 vLLM（替换 GPU 编号和模型路径）
CUDA_VISIBLE_DEVICES=0 python -m vllm.entrypoints.openai.api_server \
    --model /path/to/Qwen3-8B \
    --host 0.0.0.0 \
    --port 8000 \
    --trust-remote-code \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    --max-model-len 32768 &
```

> `--enable-auto-tool-choice --tool-call-parser hermes` 是给 OpenClaw 用的。如果只跑 Direct LLM 基线可以不加。
>
> `--max-model-len 32768` 很重要：OpenClaw 的内部 system prompt 约 8K tokens，16K 会溢出。

验证：

```bash
curl http://localhost:8000/v1/models
# 应返回模型列表
```

---

## 2. 安装 AlphaDiana

```bash
git clone <repo_url> AlphaDiana
cd AlphaDiana

# 安装（含所有可选依赖）
pip install -e ".[all,dev]"
```

验证安装：

```bash
alphadiana list-benchmarks
```

输出：

```
Registered benchmarks:
  - aime
  - math
  - hle
  - frontier_math
  - terminal_bench
  - osworld
```

---

## 3. 快速验证（Direct LLM 基线）

这是最简单的模式，不需要 ROCK 和 OpenClaw，只需要 vLLM 在跑。Direct LLM 是一个纯 LLM 基线 Agent，直接单轮调用 LLM API，不带工具调用和多轮推理。

### 3.1 编写配置文件

创建 `configs/my_first_run.yaml`：

```yaml
run_id: "direct-llm-qwen3-8b-aime2024"

agent:
  name: direct_llm
  version: "1.0"
  config:
    model: "/path/to/Qwen3-8B"           # ← 改成你的模型路径
    api_base: "http://localhost:8000/v1"  # ← vLLM 地址
    api_key: "EMPTY"
    temperature: 0.0
    max_tokens: 4096

benchmark:
  name: aime
  config:
    dataset: "HuggingFaceH4/aime_2024"
    split: "train"

scorer:
  name: numeric
  config:
    tolerance: 1e-6

max_concurrent: 1
output_dir: "./results"
```

> **注意：** `model` 字段要和 vLLM 启动时的 `--model` 参数**完全一致**（包括完整路径）。

### 3.2 验证配置

```bash
alphadiana validate configs/my_first_run.yaml
# 输出: Config is valid.
```

### 3.3 运行评估

```bash
alphadiana run configs/my_first_run.yaml
```

这会：
1. 从 HuggingFace 加载 AIME 2024 数据集（30 道题）
2. 逐题发给 vLLM，获取回答
3. 用 NumericScorer 评分
4. 结果写入 `results/direct-llm-qwen3-8b-aime2024.jsonl`

输出示例：

```
Run completed: direct-llm-qwen3-8b-aime2024
  Accuracy:   0.1333
  Mean Score: 0.1333
  Tasks:      30/30 completed
```

### 3.4 用 Python API 运行（可选）

如果想更灵活地控制，可以用 Python：

```python
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.runner.runner import Runner

config = ExperimentConfig.from_yaml("configs/my_first_run.yaml")
runner = Runner(config)

runner.setup()
try:
    summary = runner.run()
    print(f"Accuracy: {summary.accuracy:.4f}")
finally:
    runner.teardown()
```

**到这里，如果 Direct LLM 基线能跑通，说明 AlphaDiana + vLLM 环境没问题。** 下面搭建 Agent 系统。

---

## 4. 搭建 ROCK 沙箱环境

ROCK 是阿里巴巴开源的沙箱系统，用 Docker 容器隔离运行 Agent。OpenClaw 需要部署在 ROCK 沙箱中。

### 4.1 安装 ROCK

```bash
cd /path/to/your/workspace
git clone https://github.com/alibaba/ROCK.git
cd ROCK
pip install -e .
pip install gem-llm    # ROCK 内部依赖，提供 gem 模块
```

### 4.2 启动 Redis Stack

ROCK 依赖 Redis Stack（需要 JSON.SET 命令，普通 Redis 不行）：

```bash
# 如果需要 sg docker 来获取 docker 组权限：
sg docker -c 'docker run -d --name redis-stack -p 6379:6379 redis/redis-stack-server:latest'

# 或者直接（如果 docker 权限已配置）：
docker run -d --name redis-stack -p 6379:6379 redis/redis-stack-server:latest
```

验证：

```bash
redis-cli ping
# 应返回 PONG
```

### 4.3 配置 ROCK

创建配置文件：

```bash
mkdir -p ~/.rock
cat > ~/.rock/config.ini << 'EOF'
[ray]
address = auto
EOF
```

创建 `.venv` 软链接（ROCK 的 `LocalRuntimeEnv` 需要它来挂载 Python 环境到容器内）：

```bash
cd /path/to/ROCK
ln -sf $(python -c "import sys; print(sys.prefix)") .venv
```

### 4.4 启动 Ray + ROCK 服务

```bash
# 设置 ROCK 项目根目录（很重要，Ray worker 需要这个）
export ROCK_PROJECT_ROOT=/path/to/ROCK

# 启动 Ray
ray start --head --port=6380

# 启动 ROCK admin（端口 9000，处理写操作）
sg docker -c 'python -m rock.deployments.admin &'

# 启动 ROCK proxy（端口 9001，处理读/运行时操作）
sg docker -c 'python -m rock.deployments.proxy &'
```

> 如果不需要 `sg docker`（已有 docker 权限），去掉 `sg docker -c '...'`，直接运行。

验证：

```bash
curl http://localhost:9000/    # 应返回 {"message":"hello, ROCK!"}
curl http://localhost:9001/    # 同上
```

---

## 5. 部署 OpenClaw Agent

OpenClaw 是一个 Agent 框架，通过 npm 安装到 ROCK 沙箱中。它接收用户问题后，会自动进行多轮推理和工具调用。

### 5.1 配置 OpenClaw

编辑 `openclaw_deploy/rock_agent_config.yaml`：

```yaml
working_dir: "."

run_cmd: >
  OPENCLAW_CONFIG_PATH=${working_dir}/openclaw.json
  nohup openclaw gateway >> /root/gateway.log 2>&1 &

runtime_env_config:
  type: node
  npm_registry: https://registry.npmmirror.com
  custom_install_cmd: npm install -g openclaw@latest
  install_timeout: 1200

env:
  OPENAI_BASE_URL: "http://<YOUR_HOST_IP>:8000/v1"  # ← vLLM 地址（容器内访问宿主机）
  OPENAI_API_KEY: "EMPTY"
  OPENAI_MODEL_NAME: "/path/to/Qwen3-8B"            # ← 与 vLLM 的 --model 一致
  OPENCLAW_GATEWAY_TOKEN: "OPENCLAW"
```

> **重要：** `OPENAI_BASE_URL` 不能写 `localhost`，因为这是在 Docker 容器内运行的。用宿主机的实际 IP（`hostname -I | awk '{print $1}'` 查看）。

### 5.2 部署

```bash
cd AlphaDiana/openclaw_deploy
python deploy.py
```

等待 3-5 分钟（npm 安装 + 启动 gateway），输出：

```
Creating sandbox...
Sandbox ID: 90d4a0c01ad1497baa4f4db4b72f3923
Installing OpenClaw agent...
Running OpenClaw gateway...

OpenClaw deployed successfully!
Sandbox ID: 90d4a0c01ad1497baa4f4db4b72f3923
API base: http://127.0.0.1:9001/apis/envs/sandbox/v1/sandboxes/90d4a0c01ad1497baa4f4db4b72f3923/proxy/v1
```

**记下 Sandbox ID**，后续配置要用。

### 5.3 验证 OpenClaw

```bash
SANDBOX_ID="<你的Sandbox ID>"

curl -s -X POST \
  "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/${SANDBOX_ID}/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"What is 2+3?"}],"max_tokens":512}'
```

应返回包含答案 "5" 的 JSON 响应。

---

## 6. 运行 OpenClaw 评估

### 6.1 编写配置文件

创建 `configs/openclaw_aime2024.yaml`：

```yaml
run_id: "openclaw-qwen3-8b-aime2024"

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
```

> 把 `<SANDBOX_ID>` 替换为第 5 步获得的 Sandbox ID。

### 6.2 运行

```bash
alphadiana run configs/openclaw_aime2024.yaml
```

每道题大约需要 30-60 秒（Agent 内部多轮推理），30 道题预计 15-30 分钟。

### 6.3 或者先跑单题试试

```python
from alphadiana.benchmark.aime import AIMEBenchmark
from alphadiana.agent.openclaw import OpenClawAgent
from alphadiana.scorer.numeric import NumericScorer

# 加载数据
benchmark = AIMEBenchmark()
tasks = benchmark.load_tasks({"dataset": "HuggingFaceH4/aime_2024", "split": "train"})
task = tasks[0]

# 初始化 Agent
agent = OpenClawAgent()
agent.setup({
    "version": "latest",
    "api_base": "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1",
    "model": "openclaw",
    "gateway_token": "OPENCLAW",
    "max_tokens": 8192,
})

# 求解
resp = agent.solve(task)
print(f"Answer: {resp.answer}")
print(f"Wall time: {resp.wall_time_sec:.1f}s")

# 查看 agentic trace
for step in resp.trajectory:
    role = step.get("role", "?")
    thinking = step.get("thinking", "")
    tool_calls = step.get("tool_calls", [])
    content = step.get("content", "")[:100]

    if thinking:
        print(f"  [{role}] THINKING: {thinking[:100]}...")
    for tc in tool_calls:
        print(f"  [{role}] TOOL: {tc['tool']}({list(tc['input'].keys())})")
    if content:
        print(f"  [{role}] {content}")

# 评分
scorer = NumericScorer()
scorer.setup({"tolerance": 1e-6})
score = scorer.score(task, resp)
print(f"Correct: {score.correct}, Score: {score.score}")
```

---

## 7. 查看和分析结果

### 7.1 结果文件

评估结果保存在 `results/<run_id>.jsonl`，每行一条 JSON 记录：

```bash
# 查看结果摘要
alphadiana report ./results
```

### 7.2 逐题分析

```python
import json

with open("results/openclaw-qwen3-8b-aime2024.jsonl") as f:
    results = [json.loads(line) for line in f]

correct = sum(1 for r in results if r["correct"])
print(f"Accuracy: {correct}/{len(results)} = {correct/len(results):.2%}")

print(f"\n{'Task':<15} {'GT':>5} {'Pred':>8} {'OK':>4} {'Time':>6}")
print("-" * 45)
for r in results:
    mark = "Y" if r["correct"] else "N"
    print(f"{r['task_id']:<15} {r['ground_truth']:>5} {str(r['predicted']):>8} {mark:>4} {r['wall_time_sec']:>5.0f}s")
```

### 7.3 查看 Agent 的推理过程

每条结果的 `trajectory` 字段包含完整的 agentic trace：

```python
r = results[0]  # 第一题

for step in r["trajectory"]:
    role = step.get("role", "?")

    # thinking: Agent 的内部思考
    if step.get("thinking"):
        print(f"[{role}] THINKING: {step['thinking'][:200]}...")

    # tool_calls: Agent 调用了什么工具
    for tc in step.get("tool_calls", []):
        print(f"[{role}] TOOL CALL: {tc['tool']}({json.dumps(tc['input'])[:100]})")

    # tool_results: 工具执行结果
    for tr in step.get("tool_results", []):
        print(f"[{role}] TOOL RESULT: {str(tr['content'])[:100]}")

    # content: Agent 的文本输出
    if step.get("content"):
        print(f"[{role}] OUTPUT: {step['content'][:200]}")
```

---

## 8. 自定义配置

### 8.1 配置文件字段说明

```yaml
run_id: string           # 运行标识（不填则自动生成 UUID）

agent:
  name: string           # "direct_llm" | "openclaw"
  version: string        # 版本号（如 "latest", "v1.0"）
  config: {}             # Agent 特定配置（见下方）

benchmark:
  name: string           # "aime" | "math" | "hle" | "frontier_math" | "terminal_bench" | "osworld"
  config: {}             # Benchmark 特定配置（见下方）

sandbox: null | {}       # 沙箱配置（terminal_bench 和 osworld 必需，其他可选）
  name: string           # "local" | "rock" | "boxlite"
  config: {}

scorer:
  name: string           # "numeric" | "exact_match" | "llm_judge"
  config: {}

max_concurrent: int      # 并行任务数（默认 1）
output_dir: string       # 结果输出目录（默认 "./results"）
metadata: {}             # 自定义元数据
```

### 8.2 Agent 配置

**Direct LLM（直接调 LLM 基线）：**

```yaml
agent:
  name: direct_llm
  version: "1.0"
  config:
    model: "模型名称"
    api_base: "http://localhost:8000/v1"
    api_key: "EMPTY"
    temperature: 0.0
    max_tokens: 4096
    # system_prompt: "自定义 system prompt"  # 可选
```

**OpenClaw（Agent 系统）：**

```yaml
agent:
  name: openclaw
  version: "latest"
  config:
    api_base: "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<ID>/proxy/v1"
    model: openclaw
    gateway_token: "OPENCLAW"
    temperature: 0.0
    max_tokens: 8192
    # 注意: OpenClaw 有自己的 system prompt，不需要手动设置
```

### 8.3 Benchmark 配置

```yaml
# AIME 2024
benchmark:
  name: aime
  config:
    dataset: "HuggingFaceH4/aime_2024"
    split: "train"

# MATH
benchmark:
  name: math
  config:
    dataset: "hendrycks/competition_math"
    split: "test"
    # subset: "algebra"  # 可选，只跑子集

# HLE (Humanity's Last Exam)
benchmark:
  name: hle
  config:
    split: "test"
```

### 8.4 Scorer 配置

```yaml
# 数值比较（AIME 用）
scorer:
  name: numeric
  config:
    tolerance: 1e-6

# 精确匹配（MATH 用）
scorer:
  name: exact_match
  config: {}

# LLM 判断（HLE 用）
scorer:
  name: llm_judge
  config:
    judge_model: "gpt-4o"
    api_base: "https://api.openai.com/v1"
    api_key: "sk-..."
```

### 8.5 Direct LLM vs OpenClaw 对比

| | Direct LLM | OpenClaw |
|---|---------|----------|
| 评测对象 | 裸模型能力 | Agent 系统能力 |
| 推理方式 | 单轮问答 | 多轮推理 + 工具调用 |
| 需要 ROCK | 否 | 是 |
| 需要 Docker | 否 | 是 |
| 每题耗时 | 5-20 秒 | 30-120 秒 |
| 搭建难度 | 低 | 中 |
| Trajectory | 简单 (system/user/assistant) | 丰富 (thinking/tool_call/tool_result) |

---

## 附录 A：项目结构

```
AlphaDiana/
├── alphadiana/                    # 核心代码
│   ├── cli.py                     # CLI 入口 (alphadiana run/validate/report)
│   ├── agent/                     # Agent 实现
│   │   ├── base.py                #   Agent 基类 + AgentResponse
│   │   ├── registry.py            #   Agent 注册表
│   │   ├── direct_llm.py           #   Direct LLM: 直接调 LLM 基线
│   │   └── openclaw.py            #   OpenClaw: Agent 系统（多轮推理+工具调用）
│   ├── benchmark/                 # Benchmark 数据集
│   │   ├── base.py                #   Benchmark 基类 + BenchmarkTask
│   │   ├── registry.py            #   Benchmark 注册表
│   │   ├── aime.py                #   AIME 竞赛数学
│   │   ├── math_bench.py          #   Hendrycks MATH
│   │   ├── hle.py                 #   Humanity's Last Exam
│   │   ├── frontier_math.py       #   FrontierMath
│   │   ├── terminal_bench.py      #   终端操作（需要沙箱）
│   │   └── osworld.py             #   OS 交互（需要沙箱）
│   ├── scorer/                    # 评分器
│   │   ├── base.py                #   Scorer 基类 + ScoreResult
│   │   ├── numeric.py             #   数值比较
│   │   ├── exact_match.py         #   精确匹配
│   │   └── llm_judge.py           #   LLM 评判
│   ├── runner/                    # 运行引擎
│   │   ├── runner.py              #   Runner: 主编排器
│   │   └── task_dispatcher.py     #   TaskDispatcher: 并行调度
│   ├── results/                   # 结果管理
│   │   ├── result_store.py        #   JSONL 结果存储
│   │   └── report.py              #   报告生成
│   ├── sandbox/                   # 沙箱实现
│   │   ├── local.py               #   本地 subprocess
│   │   ├── rock.py                #   ROCK 远程沙箱
│   │   └── boxlite.py             #   Boxlite 云沙箱
│   └── config/                    # 配置系统
│       ├── experiment_config.py   #   ExperimentConfig 数据类
│       └── validator.py           #   配置校验器
├── configs/examples/              # 示例配置
├── openclaw_deploy/               # OpenClaw 部署配置
│   ├── deploy.py                  #   部署脚本
│   ├── rock_agent_config.yaml     #   ROCK agent 配置
│   └── openclaw.json              #   OpenClaw gateway 配置
├── results/                       # 评估结果输出目录
├── tests/                         # 测试（6 个阶段）
└── docs/                          # 文档
```

### 核心数据流

```
YAML Config
    │  ExperimentConfig.from_yaml()
    ▼
Runner.setup()
    │  从注册表加载 Benchmark / Agent / Scorer
    ▼
Runner.run()
    │  benchmark.load_tasks() → 加载数据集
    │  for each task:
    │    agent.solve(task) → AgentResponse (answer, trajectory, ...)
    │    scorer.score(task, response) → ScoreResult (correct, score, ...)
    │    result_store.append(task, response, score) → 写入 JSONL
    │  report_generator.generate() → RunSummary
    ▼
Runner.teardown()
    │  清理资源
    ▼
输出: results/<run_id>.jsonl + 控制台报告
```

---

## 附录 B：常见问题

### Q: vLLM 启动报显存不足？

减小 `--max-model-len`，或者用更大显存的 GPU：

```bash
# 用更小的上下文长度
--max-model-len 16384

# 注意：如果要跑 OpenClaw，最少需要 32768（OpenClaw 系统 prompt ~8K tokens）
```

### Q: ROCK 沙箱容器立即退出？

检查以下几项：
1. `ROCK_PROJECT_ROOT` 环境变量是否设置
2. `.venv` 软链接是否指向有效的 Python 环境
3. `gem-llm` 包是否已安装
4. Ray 是否在设置 `ROCK_PROJECT_ROOT` 之后重启

```bash
# 完整的重启流程
export ROCK_PROJECT_ROOT=/path/to/ROCK
ray stop
ray start --head --port=6380
```

### Q: OpenClaw 返回 "Context overflow"？

vLLM 的 `--max-model-len` 太小。OpenClaw 内部 system prompt 约 8K tokens，需要至少 32K 的上下文窗口。

```bash
# 重启 vLLM
--max-model-len 32768
```

### Q: OpenClaw 返回 "404 The model does not exist"？

`rock_agent_config.yaml` 中的 `OPENAI_MODEL_NAME` 必须与 vLLM 的 `--model` 参数**完全一致**（包括完整路径）。

### Q: `OPENAI_BASE_URL` 填什么？

在 `rock_agent_config.yaml` 中，这是 Docker 容器内访问 vLLM 的地址。不能用 `localhost`（那是容器内的 localhost）。用宿主机 IP：

```bash
hostname -I | awk '{print $1}'
# 例如: 192.0.2.14
# 则填: http://192.0.2.14:8000/v1
```

### Q: 如何只跑数据集的一部分？

目前需要修改 Benchmark 实现或在 Python API 中手动切片：

```python
tasks = benchmark.load_tasks(config)
tasks = tasks[:5]  # 只跑前 5 题
```

### Q: 如何注册自定义 Agent？

```python
# my_agent.py
from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry

class MyAgent(Agent):
    name = "my_agent"

    def setup(self, config: dict) -> None:
        self.version = config.get("version", "1.0")
        # 初始化你的 Agent...

    def solve(self, task, sandbox=None) -> AgentResponse:
        # 调用你的模型/Agent 系统...
        return AgentResponse(answer="42", raw_output="...", trajectory=[...])

    def teardown(self) -> None:
        pass

AgentRegistry.register("my_agent", MyAgent)
```

然后在配置中使用 `agent.name: "my_agent"`。

### Q: 评估结果文件在哪里？

在配置文件的 `output_dir` 目录下（默认 `./results/`），文件名为 `<run_id>.jsonl`。

每行一条 JSON 记录，包含：

```json
{
  "task_id": "aime_60",
  "problem": "题目内容...",
  "ground_truth": "204",
  "predicted": "204",
  "correct": true,
  "score": 1.0,
  "rationale": "Numeric comparison: expected=204.0, predicted=204.0, ...",
  "trajectory": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "thinking": "...", "tool_calls": [...]}
  ],
  "token_usage": {"prompt_tokens": 150, "completion_tokens": 1200},
  "wall_time_sec": 47.3,
  "timestamp": "2026-03-09T..."
}
```
