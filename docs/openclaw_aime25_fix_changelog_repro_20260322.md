# OpenClaw AIME25 修复过程、Changelog 与稳定复现指南（2026-03-22）

## 1. 目标与范围

本次修复目标：

- 在 OpenRouter + qwen/qwen3-235b-a22b-2507 + AIME2025 组合下，形成可重复的稳定运行路径
- 清理运行链路中的硬编码地址、端口、路径与环境耦合
- 补齐启动超时、会话预热、重试与资源降级策略
- 输出可复现命令（含一键脚本和手动动态流程）

适用配置：

- Benchmark: MathArena/aime_2025
- Agent: openclaw
- Sandbox backend: ROCK

---

## 2. 问题现象与根因定位

### 2.1 初始高频故障

- sandbox 启动阶段长时间卡住或超时（Get timed out / not ready）
- deploy 后 `run in session failed` 或 `upstream server is not reachable`
- 同一套命令在不同机器成功率差异很大

### 2.2 根因分层

1) 启动脚本环境耦合

- Python / ray 可执行路径存在机器绑定
- 地址与端口存在固定值使用场景
- Redis 检查依赖 redis-cli，部分机器无该命令

2) SDK/服务端超时之外缺少客户端硬超时

- 仅依赖服务端 startup timeout 时，可能出现调用侧“无返回长挂”

3) 会话准备与请求竞争

- sandbox 标记 running 后，default session 未完全可用
- install/run 早到请求触发 transient upstream 错误

4) 资源策略缺少竞争感知

- 仅按机器静态规格选 profile，未考虑当前负载与内存竞争

---

## 3. 修复过程（按阶段）

### 阶段 A：运行链路去硬编码与环境解耦

#### A1. 端口探测输出改为可覆盖变量

文件：dev/find_rock_ports.py

- 输出从“固定 URL 值”改为“由 ROCK_BIND_HOST + 端口动态拼接”
- 避免写入机器绝对路径和用户特定临时目录

效果：

- 跨机器复制 .rock_ports.env 后仍可通过环境变量覆盖

#### A2. 启动脚本主机与路径参数化

文件：dev/start_openclaw.sh

- PYTHON 改为 PATH 动态解析（可被外部 PYTHON 覆盖）
- 新增 ROCK_BIND_HOST/ROCK_REDIS_HOST/ROCK_HTTP_HOST/ROCK_PORT_PROBE_HOST
- Redis TCP 探测与 admin/proxy 健康检查改为可配置 host
- Redis 启动提示容器名改为 ROCK_REDIS_CONTAINER

效果：

- 去除单机路径依赖，降低地址绑定问题

#### A3. deploy preflight 的 Redis 检查支持自定义 host

文件：openclaw_deploy/deploy.py

- _check_redis(container, host, port)
- 默认从 ROCK_REDIS_HOST 或 ROCK_BIND_HOST 获取 host

效果：

- 不再默认固定 loopback，适配更多部署拓扑

#### A4. OpenClaw 模板配置去除私有地址与本机模型路径

文件：openclaw_deploy/openclaw.json

- OPENAI_BASE_URL 置空
- OPENAI_MODEL_NAME 置空
- 依赖运行时注入

效果：

- 避免配置模板携带环境污染值

---

### 阶段 B：稳定性补强（超时、预热、重试）

#### B1. sandbox 启动增加客户端 wait_for 守卫

文件：alphadiana/sandbox/rock.py

- 对 sandbox.start() 增加 asyncio.wait_for
- requested profile 与 fallback profile 分别设置超时策略
- 仅缓存 requested 成功 profile，避免 fallback 污染

效果：

- 长挂改为可失败可重试，后续会话不被错误 profile 影响

#### B2. run_in_session 与 agent helper 增加 transient 错误重试

文件：alphadiana/sandbox/rock.py

- 识别 upstream not reachable / run in session failed
- 增加重试与 session ready 探测

效果：

- 短时上游抖动可被吸收

#### B3. deploy 阶段加入 default session warmup

文件：openclaw_deploy/deploy.py

- sandbox.start() 加客户端 wait_for
- 新增 _warmup_default_session()
- default session 不存在时自动 create 并重试探活

效果：

- install 前会话可用性更稳定，显著降低首请求失败

#### B4. 默认 start_retries 从 1 提升到 3

文件：alphadiana/runner/runner.py

- OpenClaw 运行路径默认 rock_start_retries=3

效果：

- 低概率启动失败有恢复机会

---

### 阶段 C：资源估算器与一键复现脚本

#### C1. 新增竞争感知资源估算器

文件：dev/generated/sandbox_resource_estimator.py

能力：

- 机器静态能力：CPU 数、总内存
- 运行时竞争：load per cpu、MemAvailable 比例
- 竞争等级：low / medium / high
- 竞争等级缓存 TTL：high 短、medium 中、low 长
- 支持从本地成功结果学习更小可用 profile

效果：

- 大机器但高竞争时自动保守，不再盲目用大规格

#### C2. 新增一键运行脚本

文件：dev/run_aime25_openrouter_oneclick.sh

流程：

1. 自动加载环境
2. 动态端口探测并写入 dev/.rock_ports.env
3. 调估算器拿推荐 profile
4. 启动 OpenClaw
5. 首次运行失败后自动降级重试
6. 成功后回写估算缓存

效果：

- 将“探测 + 估算 + 启动 + 运行 + fallback + 学习”固化为一次执行

---

## 4. Changelog（面向提交）

本次修复相关文件变更摘要：

- alphadiana/runner/runner.py
  - OpenClaw 默认 rock_start_retries: 1 -> 3

- alphadiana/sandbox/rock.py
  - 增加 sandbox.start 客户端 wait_for
  - 增加 fallback_startup_timeout
  - requested/fallback 超时分流
  - 仅缓存 requested 成功 profile
  - 增加 run_in_session transient 错误重试
  - 增加 agent helper 侧重试与 session ready 探测

- dev/find_rock_ports.py
  - 输出变量改为 host 可配置 + URL 动态拼接

- dev/start_openclaw.sh
  - Python/ray 路径动态解析
  - host 相关参数可配置
  - Redis 检查支持无 redis-cli 场景
  - 健康检查与探测逻辑去固定地址

- dev/.rock_ports.env
  - 变为可覆盖默认值模板
  - URL 由 host + port 拼接

- openclaw_deploy/deploy.py
  - Redis preflight 支持 host 参数
  - sandbox.start 客户端 wait_for
  - default session warmup + 缺失 session 自动创建

- openclaw_deploy/openclaw.json
  - 清理 OPENAI_BASE_URL/OPENAI_MODEL_NAME 的环境耦合值

- tests/test_fix_0321_rock_agent_retry.py
  - 新增回归测试，覆盖重试与超时守卫

- dev/generated/sandbox_resource_estimator.py
  - 新增：竞争感知资源估算 + 缓存

- dev/run_aime25_openrouter_oneclick.sh
  - 新增：支持 `--config` 的一键稳定复现脚本

- dev/run_aime25_openrouter.bash
  - 新增：配置驱动入口（`bash dev/run_aime25_openrouter.bash --config ...`）

- configs/examples/aime25_openrouter_oneclick.yaml
  - 新增：首用即跑通的配置模板

---

## 5. 稳定复现命令

## 5.1 配置驱动一键方式（推荐）

```bash
bash dev/run_aime25_openrouter.bash --config configs/examples/aime25_openrouter_oneclick.yaml
```

首用流程（面向新用户）：

1. 按 README 完成环境准备
2. 复制并编辑 YAML（填写 API key、模型、API base、数据集）
3. 运行一条命令完成动态探测与评测

```bash
cp configs/examples/aime25_openrouter_oneclick.yaml /tmp/my_aime25.yaml
# 编辑 /tmp/my_aime25.yaml
bash dev/run_aime25_openrouter.bash --config /tmp/my_aime25.yaml
```

等价入口：

```bash
bash dev/run_aime25_openrouter_oneclick.sh --config configs/examples/aime25_openrouter_oneclick.yaml
```

前置要求：

- 已在 config 指定 `provider.openai_api_key` 或 `provider.openai_api_key_env`
- docker / ray / ROCK 依赖可用

失败时 warning 提示（脚本内置）：

- 鉴权失败（401/403）时提示检查 key/env 配置
- `run in session failed` / `upstream not reachable` 时提示 transient 重试建议
- `startup timeout` / `not ready` 时提示增大超时或调小 fallback profile
- Ray 版本不匹配时提示清理 stale ray
- Redis 连接失败时提示检查容器与端口

warning 会附带修复文档路径：

- `docs/openclaw_aime25_fix_changelog_repro_20260322.md`

---

## 5.2 手动动态方式（便于调试）

```bash
set -euo pipefail

# 1) 环境
set -a
source .env
set +a
source dev/rock_env.sh

# 2) 动态端口
python3 dev/find_rock_ports.py --write-env dev/.rock_ports.env
source dev/.rock_ports.env

# 3) 模型参数（使用环境注入）
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://openrouter.ai/api/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-${OPENROUTER_API_KEY:-}}"
export OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-qwen/qwen3-235b-a22b-2507}"

# 4) 推荐资源（竞争感知）
eval "$(python3 dev/generated/sandbox_resource_estimator.py recommend --format shell)"
echo "profile=${ROCK_EST_MEMORY}/${ROCK_EST_CPUS} source=${ROCK_EST_SOURCE} contention=${ROCK_EST_CONTENTION_TIER}"

# 5) 启动 OpenClaw
bash dev/start_openclaw.sh || true

# 6) 运行
RUN_ID="aime25-qwen235b-dyn-$(date +%Y%m%d-%H%M%S)"
alphadiana run configs/examples/openclaw_aime2025_dynamic.yaml \
  -o benchmark.config.max_tasks=1 \
  -o agent.config.rock_memory="${ROCK_EST_MEMORY}" \
  -o agent.config.rock_cpus="${ROCK_EST_CPUS}" \
  -o agent.config.rock_startup_timeout=300 \
  -o agent.config.rock_start_retries=3 \
  -o run_id="${RUN_ID}"
```

失败时建议降级重试：

```bash
alphadiana run configs/examples/openclaw_aime2025_dynamic.yaml \
  -o benchmark.config.max_tasks=1 \
  -o agent.config.rock_memory=1536m \
  -o agent.config.rock_cpus=0.75 \
  -o agent.config.rock_startup_timeout=300 \
  -o agent.config.rock_start_retries=3 \
  -o run_id="${RUN_ID}-fb1"
```

---

## 6. 复现稳定性建议

- 首选一键脚本，避免人工漏掉探测与 fallback 步骤
- 高竞争时段优先接受 estimator 的小规格建议
- 若长期稳定，可将成功 profile 作为机器默认起始档位
- 观察指标：
  - sandbox start 成功率
  - warmup 成功率
  - 单题 wall time
  - fallback 触发比例

---

## 7. 已知边界

- `openclaw.json` 的 controlUi allowedOrigins 仍包含 localhost 族地址，这是本地网关控制面的预期安全边界，不属于凭证泄露
- 文档与脚本中使用 `<your-key>`、`${...}` 等占位或环境变量引用，不包含真实密钥

---

## 8. 验收标准（建议）

满足以下条件可视为本次修复完成：

1. 一键脚本在当前机器连续两次单题 smoke 成功
2. 当负载升高时，估算器推荐 profile 会自动下调且任务可完成
3. 运行链路不再依赖机器私有路径/IP
4. push 前检查无真实 API key、私钥、用户敏感路径
