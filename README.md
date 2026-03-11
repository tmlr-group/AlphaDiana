# AlphaDiana

`AlphaDiana` 用于评估 foundation model 和 agent system 在推理 benchmark 上的表现。本README当前重点验证一条可复现链路：

- OpenClaw agent system
- OpenRouter 上的 `qwen/qwen-2.5-7b-instruct`
- AIME 2024 benchmark
- ROCK sandbox 本地部署

## Quick Start

默认约定：除非某一步明确写了 `cd ref/ROCK`，否则 README 里的命令都应在仓库根目录执行，也就是：

```bash
cd AlphaDiana
```

如果你当前在 `ref/ROCK`，不要直接执行 `source dev/rock_env.sh` 或 `python dev/find_rock_ports.py`，先切回仓库根目录。

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
# cd AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
```

如果你用了自定义环境名，把 `alphadiana` 替换掉。

`dev/rock_env.sh` 会：

- 设置 `PYTHONPATH`
- 优先把 `ROCK_CONFIG` 指向 `dev/generated/rock-local-proxy.dynamic.yml`，不存在时再回退到 `ref/ROCK/rock-conf/rock-local-proxy.yml`
- 设置 `ROCK_WORKER_ENV_TYPE=local`
- 默认设置 `HF_ENDPOINT=https://hf-mirror.com`，避免直连 Hugging Face 时网络不可达
- 如果存在 `dev/.rock_ports.env`，自动加载动态探测出的端口
- 清理 `ALL_PROXY`、`HTTP_PROXY`、`HTTPS_PROXY`

其中 `ref/ROCK/.venv` 很重要。`ROCK` 的 `LocalRuntimeEnv` 会把它挂载到容器内的 `/tmp/miniforge`，如果这个链接不存在或失效，sandbox 容器会启动后立刻退出。

## 启动本地 ROCK 环境

### 1. 启动 Redis

`local-proxy` 模式依赖 Redis，而且 Redis 端口也应该动态化。先生成端口和动态 ROCK 配置：

```bash
cd /data2/xuan/AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
python dev/find_rock_ports.py --write-env dev/.rock_ports.env
source dev/.rock_ports.env
```

这一步会同时生成：

```text
dev/generated/rock-local-proxy.dynamic.yml
```

`dev/rock_env.sh` 会自动把 `ROCK_CONFIG` 指向这个动态文件。

首次启动 Redis：

```bash
docker run -d --name "$ROCK_REDIS_CONTAINER" -p "$ROCK_REDIS_PORT:6379" redis/redis-stack-server:latest
```

如果已经创建过这个容器，不要重复 `docker run`，直接启动：

```bash
newgrp docker
docker start "$ROCK_REDIS_CONTAINER"
```

NOTE: 出现问题请查看FAQ 5.

然后确认它真的在运行：

```bash
docker ps
```

如果你需要先清理本用户之前残留的 ROCK / OpenClaw 相关监听端口，可以在仓库根目录执行：

```bash
cd /data2/xuan/AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env
bash dev/cleanup_rock_ports.sh
```

它会只尝试结束当前 Linux 用户在这些端口上的监听进程：

- `ROCK_RAY_PORT`
- `ROCK_RAY_DASHBOARD_PORT`
- `ROCK_RAY_CLIENT_SERVER_PORT`
- `ROCK_REDIS_PORT`
- `ROCK_ADMIN_PORT`
- `ROCK_PROXY_PORT`

推荐直接做一次健康检查：

```bash
docker ps --filter name="$ROCK_REDIS_CONTAINER"
docker exec "$ROCK_REDIS_CONTAINER" redis-cli ping
docker port "$ROCK_REDIS_CONTAINER" 6379/tcp
```

预期返回：

```text
PONG
```

并且 `docker port "$ROCK_REDIS_CONTAINER" 6379/tcp` 应该返回你刚才探测出的宿主机端口，例如：

```text
0.0.0.0:<ROCK_REDIS_PORT>
```

### 2. 启动服务

当前服务器多人共用时，`6379/6380/9000/9001` 很容易被占用。先检测一组可用端口：

```bash
# cd AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
python dev/find_rock_ports.py --write-env dev/.rock_ports.env
source dev/.rock_ports.env
```

你会看到类似输出：

```bash
export ROCK_RAY_PORT=6382
export ROCK_RAY_DASHBOARD_PORT=8266
export ROCK_RAY_CLIENT_SERVER_PORT=30001
export ROCK_REDIS_PORT=6383
export ROCK_REDIS_CONTAINER=redis-stack
export ROCK_ADMIN_PORT=9004
export ROCK_PROXY_PORT=9005
export ROCK_BASE_URL=http://127.0.0.1:9004
export ROCK_PROXY_ROOT_URL=http://127.0.0.1:9005
export ROCK_PROXY_URL=http://127.0.0.1:9005/apis/envs/sandbox/v1
export ROCK_DYNAMIC_CONFIG="/data2/xuan/AlphaDiana/dev/generated/rock-local-proxy.dynamic.yml"
export RAY_TMPDIR="/data2/xuan/AlphaDiana/.cache/ray"
```

之后所有终端都先执行：

```bash
# cd AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env
```

需要 3 个终端。

启动顺序固定为：

1. 先启动 Redis
2. 再启动 Ray head
3. 再启动 admin
4. 最后启动 proxy

终端 0：

```bash
docker start "$ROCK_REDIS_CONTAINER" || docker run -d --name "$ROCK_REDIS_CONTAINER" -p "$ROCK_REDIS_PORT:6379" redis/redis-stack-server:latest
docker exec "$ROCK_REDIS_CONTAINER" redis-cli ping
```

终端 1：

```bash
cd ref/ROCK
ray start --head \
  --port="$ROCK_RAY_PORT" \
  --dashboard-port="$ROCK_RAY_DASHBOARD_PORT" \
  --ray-client-server-port="$ROCK_RAY_CLIENT_SERVER_PORT" \
  --temp-dir="$RAY_TMPDIR" \
  --disable-usage-stats \
  --block
```

终端 2：

```bash
newgrp docker

cd ref/ROCK
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env
python -m rock.admin.main --env local-proxy --role admin --port "$ROCK_ADMIN_PORT"
```

终端 3：

```bash
newgrp docker

cd ref/ROCK
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env
python -m rock.admin.main --env local-proxy --role proxy --port "$ROCK_PROXY_PORT"
```

### 3. 健康检查

在任意新终端里执行：

```bash
cd /data2/xuan/AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env
docker exec "$ROCK_REDIS_CONTAINER" redis-cli ping
curl "$ROCK_BASE_URL/"
curl "$ROCK_PROXY_ROOT_URL/"
```

NOTE: 如果有问题，请查看 FAQ 5.

预期都返回：

```json
{"message":"hello, ROCK!"}
```

并且 Redis 应返回：

```text
PONG
```

## 部署 OpenClaw

### 1. 生成部署配置

```bash
cd /data2/xuan/AlphaDiana
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
newgrp docker

cd /data2/xuan/AlphaDiana
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
API base: http://127.0.0.1:<ROCK_PROXY_PORT>/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1
```

记下这个 `SANDBOX_ID`。

### 3. 生成评估配置

```bash
cd /data2/xuan/AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
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
  "$ROCK_PROXY_URL/sandboxes/<SANDBOX_ID>/proxy/v1/chat/completions" \
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

- `$ROCK_ADMIN_PORT` 和 `$ROCK_PROXY_PORT` 是否都还在监听
- `deploy.py` 是否打印了 `OpenClaw deployed successfully!`
- `.env` 里的 `OPENROUTER_API_KEY` 是否可用

## 运行 AIME 2024

先校验配置：

```bash
cd /data2/xuan/AlphaDiana
alphadiana validate dev/generated/openclaw_qwen25_7b_openrouter_aime2024.resolved.yaml
```

再运行评估：

```bash
cd /data2/xuan/AlphaDiana
alphadiana run dev/generated/openclaw_qwen25_7b_openrouter_aime2024.resolved.yaml
```

结果会写到：

```text
results/openclaw-qwen-2.5-7b-instruct-openrouter-aime2024.jsonl
```

## 查看结果

查看已经完成多少题：

```bash
cd /data2/xuan/AlphaDiana
wc -l results/openclaw-qwen-2.5-7b-instruct-openrouter-aime2024.jsonl
```

查看简单摘要：

```bash
cd /data2/xuan/AlphaDiana
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

## FAQs

### 1. 不要用 `rock admin start`

当前这个环境里，`rock admin start` 可能内部调用一个不存在的 `admin` 可执行文件，命令表面退出但服务没有真正起来。

请使用：

```bash
python -m rock.admin.main --env local-proxy --role admin --port "$ROCK_ADMIN_PORT"
python -m rock.admin.main --env local-proxy --role proxy --port "$ROCK_PROXY_PORT"
```

### 2. 不要用 `configs/examples/rock_local.yaml`

那份配置缺少 Redis 配置，启动 `local-proxy` 会报连接错误。

请使用：

```bash
cd /data2/xuan/AlphaDiana
source dev/rock_env.sh
```

或者显式设置：

```bash
export ROCK_CONFIG=dev/generated/rock-local-proxy.dynamic.yml
```

### 3. `deploy.py` 报 sandbox 启动超时

先检查：

```bash
cd /data2/xuan/AlphaDiana
curl "$ROCK_BASE_URL/"
curl "$ROCK_PROXY_ROOT_URL/"
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
- 默认读取 `ROCK_BASE_URL` 和 `ROCK_PROXY_URL`
- 部署前预检当前动态端口
- sandbox 启动失败时输出最新状态

### 4. `python -m rock.admin.main` 启动时报 Redis `ConnectionError`

如果你看到类似：

```text
connecting to localhost:<ROCK_REDIS_PORT>
```

说明不是 admin 端口问题，而是 Redis 没启动。先执行：

```bash
cd /data2/xuan/AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env
docker start "$ROCK_REDIS_CONTAINER"
docker exec "$ROCK_REDIS_CONTAINER" redis-cli ping
```

如果容器还不存在，再执行：

```bash
docker run -d --name "$ROCK_REDIS_CONTAINER" -p "$ROCK_REDIS_PORT:6379" redis/redis-stack-server:latest
docker exec "$ROCK_REDIS_CONTAINER" redis-cli ping
```

确认返回 `PONG` 后，再重启：

```bash
cd /data2/xuan/AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env
cd ref/ROCK
python -m rock.admin.main --env local-proxy --role admin --port "$ROCK_ADMIN_PORT"
```

### 5. `docker start redis-stack` / `docker ps` 报 `permission denied` 访问 `/var/run/docker.sock`

如果你看到类似：

```text
permission denied while trying to connect to the Docker daemon socket
```

这说明不是 Redis 容器名字错了，而是当前 Linux 用户没有 Docker daemon 权限。共享服务器上这很常见。

先检查当前用户是否在 `docker` 组里：

```bash
id
getent group docker
```

判断标准：

- `getent group docker` 里出现你的用户名，说明管理员已经把你加入了 `docker` 组
- 但只有当 `id` 的输出里也出现 `docker` 组时，当前 shell 才真正拿到 Docker 权限
- 最终验收标准不是组信息，而是 `docker ps` 能成功执行

`conda activate` 不会帮你刷新 Linux 组权限。即使之前某个 shell 已经能用 Docker，只要你换了一个旧 shell，仍然可能再次看到 `permission denied`。

如果 `getent group docker` 已经包含你，但 `id` 还没有 `docker`，说明权限变更还没注入当前会话。需要执行以下任一操作后再继续：

```bash
newgrp docker
```

或者直接退出当前终端，重新登录。

重新进入 shell 后，再次确认：

```bash
id
docker ps
```

推荐直接执行这组修复命令：

```bash
cd /data2/xuan/AlphaDiana
newgrp docker
```

进入新 shell 后，再执行：

```bash
id
docker ps
conda activate alphadiana
source dev/rock_env.sh
python dev/find_rock_ports.py --write-env dev/.rock_ports.env
source dev/.rock_ports.env
docker start "$ROCK_REDIS_CONTAINER" || docker run -d --name "$ROCK_REDIS_CONTAINER" -p "$ROCK_REDIS_PORT:6379" redis/redis-stack-server:latest
docker exec "$ROCK_REDIS_CONTAINER" redis-cli ping
```

如果这组命令里的 `docker ps` 仍然失败，就不要继续后面的部署步骤，先解决当前 shell 的 Docker 权限。

如果 Docker 权限是在中途才修好的，还需要额外注意一件事：

- 已经启动过的 `ray start`、`python -m rock.admin.main --role admin`、`python -m rock.admin.main --role proxy` 不会自动继承新权限
- 必须在拿到 Docker 权限的新 shell 里，把这 3 个长期进程全部重启一遍

推荐顺序：

```bash
cd /data2/xuan/AlphaDiana
newgrp docker
```

进入新 shell 后：

```bash
id
docker ps
conda activate alphadiana
source dev/rock_env.sh
python dev/find_rock_ports.py --write-env dev/.rock_ports.env
source dev/.rock_ports.env
docker start "$ROCK_REDIS_CONTAINER" || docker run -d --name "$ROCK_REDIS_CONTAINER" -p "$ROCK_REDIS_PORT:6379" redis/redis-stack-server:latest
```

然后分别在 3 个新终端里重新启动：

```bash
cd /data2/xuan/AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env
cd ref/ROCK
ray stop
ray start --head --port="$ROCK_RAY_PORT" --dashboard-port="$ROCK_RAY_DASHBOARD_PORT" --ray-client-server-port="$ROCK_RAY_CLIENT_SERVER_PORT" --temp-dir="$RAY_TMPDIR" --disable-usage-stats --block
```

```bash
cd /data2/xuan/AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env
cd ref/ROCK
python -m rock.admin.main --env local-proxy --role admin --port "$ROCK_ADMIN_PORT"
```

```bash
cd /data2/xuan/AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env
cd ref/ROCK
python -m rock.admin.main --env local-proxy --role proxy --port "$ROCK_PROXY_PORT"
```

可行处理方式：

1. 让管理员把你的用户加入 `docker` 组，然后重新登录：

```bash
sudo usermod -aG docker "$USER"
```

2. 如果机器允许 `sudo`，直接用：

```bash
sudo docker start redis-stack
sudo docker ps
```

3. 如果这是多人共用机器，且别的用户也遇到同样问题，最稳妥的是让管理员统一处理一次 Docker 权限，或统一启动共享的 Redis / ROCK 依赖服务。

在 Docker 权限没有解决前，后面的 Redis、sandbox、OpenClaw 部署都无法正常完成。

### 6. `ray start` 报 `8265` 被占用或 `/tmp/ray/ray_current_cluster` 无权限

这是多人共用机器上的常见问题：

- `8265` 是 Ray dashboard 默认端口，也需要一起避开
- 某些机器上 `/tmp/ray` 不可写，Ray 会在写 `ray_current_cluster` 时失败
- `ray_client_server` 不能落在 Ray 默认 worker 端口区间 `10002-19999`，因此这里单独使用 `30001+`

现在 `python dev/find_rock_ports.py --write-env dev/.rock_ports.env` 会同时生成：

- `ROCK_RAY_DASHBOARD_PORT`
- `ROCK_RAY_CLIENT_SERVER_PORT`
- `RAY_TMPDIR`

确认当前 shell 已加载：

```bash
cd /data2/xuan/AlphaDiana
source dev/rock_env.sh
source dev/.rock_ports.env
```

然后使用：

```bash
cd ref/ROCK
ray start --head \
  --port="$ROCK_RAY_PORT" \
  --dashboard-port="$ROCK_RAY_DASHBOARD_PORT" \
  --ray-client-server-port="$ROCK_RAY_CLIENT_SERVER_PORT" \
  --temp-dir="$RAY_TMPDIR" \
  --disable-usage-stats \
  --block
```

## 最短复现版

如果你已经知道各步骤在做什么，只想直接照抄命令：

```bash
cd /data2/xuan/AlphaDiana
conda activate alphadiana
source dev/rock_env.sh
python dev/find_rock_ports.py --write-env dev/.rock_ports.env
source dev/.rock_ports.env

cd ref/ROCK
ray start --head --port="$ROCK_RAY_PORT" --dashboard-port="$ROCK_RAY_DASHBOARD_PORT" --ray-client-server-port="$ROCK_RAY_CLIENT_SERVER_PORT" --temp-dir="$RAY_TMPDIR" --disable-usage-stats --block
cd /data2/xuan/AlphaDiana

python dev/run_openclaw_aime_openrouter.py
python openclaw_deploy/deploy.py \
  --agent-config dev/generated/rock_agent_config.openrouter_qwen_2_5_7b.resolved.yaml

python dev/run_openclaw_aime_openrouter.py --sandbox-id <SANDBOX_ID>

curl -X POST \
  "$ROCK_PROXY_URL/sandboxes/<SANDBOX_ID>/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"What is 2+3? Return just the answer."}],"temperature":0.0,"max_tokens":128}'

alphadiana validate dev/generated/openclaw_qwen25_7b_openrouter_aime2024.resolved.yaml
alphadiana run dev/generated/openclaw_qwen25_7b_openrouter_aime2024.resolved.yaml
```
