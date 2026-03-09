# AlphaDiana 实验说明：OpenClaw + OpenRouter `openai/gpt-oss-120b:free` + `MathArena/hmmt_nov_2025`

本文档说明如何在 AlphaDiana 中，用 **OpenClaw agent** 评估 **`MathArena/hmmt_nov_2025`**，底层模型使用 OpenRouter 的 **`openai/gpt-oss-120b:free`**。

这份说明先聚焦实验方案和配置，不要求你先把整套流程跑通。等文档确认后，再补脚本或执行细节更稳妥。

## 1. 实验目标

- Agent: `openclaw`
- Model provider: OpenRouter
- Model ID: `openai/gpt-oss-120b:free`
- Dataset: `MathArena/hmmt_nov_2025`
- Benchmark loader: `aime`
- Scorer: `exact_match`

选择 `aime` loader 的原因是它已经支持从 Hugging Face 直接读取指定数据集，并允许自定义 `dataset`、`split`、`problem_field`、`answer_field`。

选择 `exact_match` 的原因是这类竞赛数学题答案不一定是纯数字，也可能是分式、根式、LaTeX 表达式。`numeric` scorer 容易把合法答案误判为无法解析。

## 2. 当前假设

本实验基于以下假设：

- Hugging Face 数据集 ID 为 `MathArena/hmmt_nov_2025`
- split 使用 `train`
- 题目字段为 `problem`
- 标准答案字段为 `answer`
- 数据集规模为 30 题

如果你后续确认字段名不同，只需要调整配置中的 `problem_field` 或 `answer_field`。

## 3. 整体架构

```text
AlphaDiana Runner
       |
       v
ROCK Proxy (:9001)
       |
       v
ROCK Sandbox
       |
       v
OpenClaw Gateway (:8080 in sandbox)
       |
       v
OpenRouter API
       |
       v
openai/gpt-oss-120b:free
```

这里的关键点是：

- AlphaDiana 不直接调用 OpenRouter
- AlphaDiana 调的是 sandbox 里的 OpenClaw gateway
- OpenClaw gateway 再转发到 OpenRouter

## 4. API Key 管理

建议在仓库根目录放一个 `.env` 文件：

```bash
OPENROUTER_API_KEY="sk-or-v1-..."
```

如果你要在 bash 里使用它，可以这样加载：

```bash
set -a
source .env
set +a
```

这样后续 Python 进程就能直接通过：

```python
import os

api_key = os.environ["OPENROUTER_API_KEY"]
```

读取这个 key。

## 5. OpenClaw 的 ROCK 配置

当前仓库已有 OpenClaw 的 ROCK agent 配置模板：

- `ref/ROCK/examples/agents/openclaw/rock_agent_config.yaml`
- `ref/ROCK/examples/agents/openclaw/openclaw.json`

你需要把 `rock_agent_config.yaml` 中的 OpenAI 兼容后端改成 OpenRouter：

```yaml
env:
  OPENAI_BASE_URL: "https://openrouter.ai/api/v1"
  OPENAI_API_KEY: "YOUR_OPENROUTER_API_KEY"
  OPENAI_MODEL_NAME: "openai/gpt-oss-120b:free"
  OPENCLAW_GATEWAY_TOKEN: "OPENCLAW"
```

更稳妥的做法不是把真实 key 直接写进 git 跟踪文件，而是运行前生成一份本地副本，或者在部署脚本里把 `.env` 中的值注入进去。

## 6. AlphaDiana 评估配置

建议新增一个实验配置，例如：

```yaml
run_id: "openclaw-gpt-oss-120b-free-hmmt-nov-2025"

agent:
  name: openclaw
  version: "latest"
  config:
    api_base: "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1"
    model: openclaw
    gateway_token: "OPENCLAW"
    temperature: 0.0
    max_tokens: 16384

benchmark:
  name: aime
  config:
    dataset: "MathArena/hmmt_nov_2025"
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
  notes: "OpenClaw + OpenRouter openai/gpt-oss-120b:free on MathArena/hmmt_nov_2025"
```

说明：

- `sandbox: null` 是因为 OpenClaw 已经预先部署在 ROCK sandbox 中，Runner 这里直接打 gateway
- `max_concurrent: 1` 是保守配置，先降低 OpenRouter 免费模型的限流风险
- `api_base` 里的 `<SANDBOX_ID>` 需要替换成实际部署出来的 sandbox ID

## 7. 推荐执行顺序

推荐按下面顺序推进：

1. 准备 `.env`
2. 启动 Redis
3. 启动 Ray
4. 启动 ROCK admin / proxy
5. 部署 OpenClaw gateway 到 sandbox
6. 用一条简单问题验证 gateway 可用
7. 再跑 `MathArena/hmmt_nov_2025`

不要一开始就直接跑 benchmark。先确保 gateway 对一个最小请求能返回结果，否则排查成本会很高。

## 8. 最小验证请求

在 OpenClaw sandbox 部署完成后，可以先测一条简单请求：

```bash
curl -X POST \
  "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openclaw",
    "messages": [
      {"role": "user", "content": "What is 2+3?"}
    ],
    "temperature": 0.0,
    "max_tokens": 128
  }'
```

如果这一步都不通，就不要直接进入 benchmark。

## 9. 结果文件

运行完成后，结果会写到：

```text
results/<run_id>.jsonl
```

建议至少记录以下信息：

- OpenRouter model ID
- OpenClaw 版本
- 数据集 ID
- split
- sandbox ID
- 运行日期
- 是否命中过限流

## 10. 风险与注意事项

- `free` 后缀是 OpenRouter 侧的产品标签，不应长期假设永远可用，实际执行前最好再查一次模型页面
- 免费模型常见问题是速率限制、排队和偶发 5xx
- 数学题答案格式可能不统一，必要时要针对 `exact_match` 做额外归一化
- 如果 `MathArena/hmmt_nov_2025` 的字段名不是 `problem` / `answer`，评估会在数据加载阶段失败

## 11. 下一步

这份文档先把实验定义固定下来。下一步可以继续做两件事：

1. 把上面的 YAML 真正落成 `configs/examples/...`
2. 写一个脚本，从 `.env` 读取 `OPENROUTER_API_KEY`，生成本地运行配置并执行
