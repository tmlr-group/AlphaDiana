# AlphaDiana

`AlphaDiana` 用于评估 foundation model 和 agent system 在推理 benchmark 上的表现。本README当前重点验证一条可复现链路：

- OpenClaw agent system
- OpenRouter 上的 `qwen/qwen-2.5-7b-instruct`
- AIME 2024 benchmark
- ROCK sandbox 本地部署

## Quick Start

### 1. 前置条件

需要先准备：

- Linux
- `conda`
- Docker
- Git
- 一个可用的 OpenRouter API Key

建议先确认：

```bash
docker --version
git --version
conda --version
```

### 2. 初始化环境

在仓库根目录执行：

```bash
bash ./dev/setup_alphadiana_rock.sh
```

如果要指定 conda 环境名：

```bash
bash ./dev/setup_alphadiana_rock.sh alphadiana-VeryGood
```

这个脚本会：

- 准备 `ref/ROCK`
- 创建 conda 环境
- 安装 `AlphaDiana` 依赖
- 创建 `ref/ROCK/.venv -> 当前 conda 环境` 的软链接
- 生成 `dev/rock_env.sh`

### 3. 配置 OpenRouter API Key

在仓库根目录创建 `.env`：

```bash
cat > .env <<'EOF'
OPENROUTER_API_KEY="sk-or-v1-xxxxxxxx"
EOF
```

如需导入当前 shell：

```bash
set -a
source .env
set +a
```

### 4. 每个新终端先加载环境

```bash
conda activate alphadiana
source dev/rock_env.sh
```

如果你用了自定义环境名，把 `alphadiana` 替换掉。

`dev/rock_env.sh` 会：

- 设置 `PYTHONPATH`
- 设置 `ROCK_CONFIG=ref/ROCK/rock-conf/rock-local-proxy.yml`
- 设置 `ROCK_WORKER_ENV_TYPE=local`
- 清理 `ALL_PROXY`、`HTTP_PROXY`、`HTTPS_PROXY`

其中 `ref/ROCK/.venv` 很重要。`ROCK` 的 `LocalRuntimeEnv` 会把它挂载到容器内的 `/tmp/miniforge`，如果这个链接不存在或失效，sandbox 容器会启动后立刻退出。

## 启动本地 ROCK 环境

### 1. 启动 Redis

`local-proxy` 模式依赖 Redis。首次启动：

```bash
docker run -d --name redis-stack -p 6379:6379 redis/redis-stack-server:latest
```

如果已经启动过，确认它还在运行：

```bash
docker ps
```

### 2. 启动服务

需要 3 个终端。

终端 1：

```bash
conda activate alphadiana
source dev/rock_env.sh
cd ref/ROCK
ray start --head --port=6380 --disable-usage-stats --block
```

终端 2：

```bash
conda activate alphadiana
source dev/rock_env.sh
cd ref/ROCK
python -m rock.admin.main --env local-proxy --role admin --port 9000
```

终端 3：

```bash
conda activate alphadiana
source dev/rock_env.sh
cd ref/ROCK
python -m rock.admin.main --env local-proxy --role proxy --port 9001
```

### 3. 健康检查

在任意新终端里执行：

```bash
curl http://localhost:9000/
curl http://localhost:9001/
```

预期都返回：

```json
{"message":"hello, ROCK!"}
```

## 部署 OpenClaw

### 1. 生成部署配置

```bash
conda activate alphadiana
source dev/rock_env.sh
python dev/run_openclaw_aime_openrouter.py
```

会生成：

```text
dev/generated/rock_agent_config.openrouter_qwen_2_5_7b.resolved.yaml
```

### 2. 部署到 ROCK sandbox

```bash
conda activate alphadiana
source dev/rock_env.sh
python openclaw_deploy/deploy.py \
  --agent-config dev/generated/rock_agent_config.openrouter_qwen_2_5_7b.resolved.yaml
```

注意，dev/generated/rock_agent_config.openrouter_qwen_2_5_7b.resolved.yaml 含有API-Key信息，不要泄漏或者push到GitHub。

Sandbox 需要一些时间创建，正常情况下会输出：

```text
Creating sandbox...
Sandbox ID: <SANDBOX_ID>
Installing OpenClaw agent...
Running OpenClaw gateway...

OpenClaw deployed successfully!
Sandbox ID: <SANDBOX_ID>
API base: http://127.0.0.1:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1
```

记下这个 `SANDBOX_ID`。

### 3. 生成评估配置

```bash
python dev/run_openclaw_aime_openrouter.py --sandbox-id <SANDBOX_ID>
```

会生成：

```text
dev/generated/openclaw_qwen25_7b_openrouter_aime2024.resolved.yaml
```

### 4. 先验证 OpenClaw 网关

不要一上来就跑 benchmark，先做最小验证：

```bash
curl -X POST \
  "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openclaw",
    "messages": [
      {"role": "user", "content": "What is 2+3? Return just the answer."}
    ],
    "temperature": 0.0,
    "max_tokens": 128
  }'
```

如果这一步不通，不要继续跑 benchmark，先检查：

- 9000 和 9001 是否都还在监听
- `deploy.py` 是否打印了 `OpenClaw deployed successfully!`
- `.env` 里的 `OPENROUTER_API_KEY` 是否可用

## 运行 AIME 2024

先校验配置：

```bash
alphadiana validate dev/generated/openclaw_qwen25_7b_openrouter_aime2024.resolved.yaml
```

再运行评估：

```bash
alphadiana run dev/generated/openclaw_qwen25_7b_openrouter_aime2024.resolved.yaml
```

结果会写到：

```text
results/openclaw-qwen-2.5-7b-instruct-openrouter-aime2024.jsonl
```

## 查看结果

查看已经完成多少题：

```bash
wc -l results/openclaw-qwen-2.5-7b-instruct-openrouter-aime2024.jsonl
```

查看简单摘要：

```bash
python - <<'PY'
import json
from pathlib import Path

p = Path("results/openclaw-qwen-2.5-7b-instruct-openrouter-aime2024.jsonl")
rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

correct = sum(1 for r in rows if r.get("correct"))
avg = sum(float(r.get("wall_time_sec", 0) or 0) for r in rows) / len(rows)

print("rows =", len(rows))
print("correct =", correct)
print("avg_wall_time_sec =", round(avg, 2))
print("last_task =", rows[-1]["task_id"])
PY
```

## 常见坑

### 1. 不要用 `rock admin start`

当前这个环境里，`rock admin start` 可能内部调用一个不存在的 `admin` 可执行文件，命令表面退出但服务没有真正起来。

请使用：

```bash
python -m rock.admin.main --env local-proxy --role admin --port 9000
python -m rock.admin.main --env local-proxy --role proxy --port 9001
```

### 2. 不要用 `configs/examples/rock_local.yaml`

那份配置缺少 Redis 配置，启动 `local-proxy` 会报连接错误。

请使用：

```bash
source dev/rock_env.sh
```

或者显式设置：

```bash
export ROCK_CONFIG=ref/ROCK/rock-conf/rock-local-proxy.yml
```

### 3. `deploy.py` 报 sandbox 启动超时

先检查：

```bash
curl http://127.0.0.1:9000/
curl http://127.0.0.1:9001/
docker ps
```

常见原因：

- Docker 没启动
- Redis 没启动
- `ref/ROCK/.venv` 没有指向当前 conda 环境
- `admin/proxy` 启动时没有先 `source dev/rock_env.sh`
- sandbox 镜像拉取过慢或失败

当前 `openclaw_deploy/deploy.py` 已经支持：

- `--agent-config`
- 部署前预检 `9000/9001`
- sandbox 启动失败时输出最新状态

## 最短复现版

如果你已经知道各步骤在做什么，只想直接照抄命令：

```bash
conda activate alphadiana
source dev/rock_env.sh

python dev/run_openclaw_aime_openrouter.py
python openclaw_deploy/deploy.py \
  --agent-config dev/generated/rock_agent_config.openrouter_qwen_2_5_7b.resolved.yaml

python dev/run_openclaw_aime_openrouter.py --sandbox-id <SANDBOX_ID>

curl -X POST \
  "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"What is 2+3? Return just the answer."}],"temperature":0.0,"max_tokens":128}'

alphadiana validate dev/generated/openclaw_qwen25_7b_openrouter_aime2024.resolved.yaml
alphadiana run dev/generated/openclaw_qwen25_7b_openrouter_aime2024.resolved.yaml
```
