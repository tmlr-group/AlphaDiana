# SWE-bench Verified: OpenClaw / OpenCode / ZeroClaw 配置与运行说明

说明：

- 这是当前仓库中面向用户的 `SWE-bench Verified` / container 路径 runbook。
- `SWE-bench Pro` 是另一条独立路径，见 `docs/benchmarks/swebench-pro.md`。
- 这条 Verified 路径的内部设计说明见 `context/pr26-swebench-verified/implementation-notes.md`。
- 本地验证证据见 `context/pr26-swebench-verified/`。

本文面向想要复现当前仓库 SWE-bench Verified 实验结果的用户，说明三个配置文件的用途、差异、环境准备方式、烟测命令和预期结果。

文档分层如下：

- `docs/benchmarks/swebench-verified.md`：复现入口、配置说明、烟测命令、预期结果
- `context/pr26-swebench-verified/implementation-notes.md`：实现原理、执行时序、关键文件职责
- `context/pr26-swebench-verified/`：本地真实 smoke 结果、run id、review 证据和开发 handoff

涉及的三个配置文件：

- `configs/examples/openclaw_swe_bench.yaml`
- `configs/examples/opencode_swe_bench.yaml`
- `configs/examples/zeroclaw_swe_bench.yaml`

## 1. 先说当前仓库状态

这三个 YAML 当前都可以通过 `validate`。其中：

- `openclaw` / `opencode` 已有更早的 MiniMax smoke 通过证据，见 `context/pr26-swebench-verified/`
- `opencode` 在本地 `Qwen/Qwen3.5-27B` / vLLM 上已确认会把 provider-side overflow 保留成 `provider_error`，而不是再落成空 patch
- `zeroclaw` 在同一本地 Qwen 路径上当前仍会因为上下文窗口过大写出保留的 `provider_error`；这是当前限制证据，不是“已跑通”声明

```bash
source scripts/activate.sh
python -m alphadiana.cli validate configs/examples/openclaw_swe_bench.yaml \
  -o run_id=my-swebench-smoke \
  -o benchmark.config.max_tasks=1

python -m alphadiana.cli validate configs/examples/opencode_swe_bench.yaml \
  -o run_id=my-opencode-smoke \
  -o benchmark.config.max_tasks=1

python -m alphadiana.cli validate configs/examples/zeroclaw_swe_bench.yaml \
  -o run_id=my-zeroclaw-smoke \
  -o benchmark.config.max_tasks=1
```

当前仓库里已经注册了 `swe_bench` scorer。它会复用官方 `swebench` harness：

- 先根据 task metadata 构建 / 复用官方评测镜像
- 再把 agent 产出的 patch 送进官方评测容器
- 最后把 `report.json`、`run_instance.log`、`test_output.txt` 挂到 AlphaDiana 的结果 artifacts

因此：

- `validate` 只检查配置结构。
- `run` 才会真正触发 Docker、数据集加载、任务容器启动、agent 执行和官方评测。
- 如果 dashboard 显示 `X`，那表示链路跑通但 patch 没解题；这是模型结果，不是执行失败。

## 2. 运行前环境准备

你给的准备方式是对的，推荐按下面顺序执行。

### 2.1 激活 Python 环境

```bash
source scripts/activate.sh
```

### 2.2 确认 Docker 正常

```bash
docker ps
```

SWE-bench 的 `swebench_container` sandbox 会为每个任务启动一个官方任务容器。没有 Docker，这两种模式都跑不起来。

### 2.3 配置上游模型环境变量

这三项对两种模式都重要：

```bash
export OPENAI_BASE_URL=...
export OPENAI_API_KEY=...
export OPENAI_MODEL_NAME=...
```

说明：

- `OPENAI_BASE_URL`：容器内能访问到的 OpenAI 兼容接口地址。
- `OPENAI_API_KEY`：接口密钥。如果你是本地模型代理，通常填任意非空字符串也可以。
- `OPENAI_MODEL_NAME`：真正要调用的模型名。

如果模型服务跑在宿主机上，容器里通常不能用 `http://localhost:...` 访问宿主机服务。更稳妥的写法通常是 Docker bridge 地址，例如：

```bash
export OPENAI_BASE_URL=http://host.docker.internal:8080/v1
```

`opencode_swe_bench.yaml` 里已经在注释里明确写了这一点。OpenClaw 模式本质上也一样，因为最终发起请求的也是任务容器里的 OpenClaw gateway。

如果你要复现本次通过的真实 smoke，可以直接按下面这组兼容 OpenAI 接口的环境变量来写：

```bash
export OPENAI_BASE_URL="https://api.example.com/v1/"
export OPENAI_API_KEY="<your-api-key>"
export OPENAI_MODEL_NAME="minimax-m2.5"
```

说明：

- `minimax` 和 `minimax-m2.5` 都可作为调用名，但本地真实 smoke 使用的是 `minimax-m2.5`
- 本次验证使用的最大并发约束是 `max_concurrent <= 10`

### 2.4 可能还需要 Hugging Face 镜像

SWE-bench 数据集默认从 Hugging Face 加载。如果当前环境直连 Hugging Face 不稳定，可以考虑：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

## 3. 三个配置文件分别是怎么配的

## 3.1 OpenClaw 配置

文件：`configs/examples/openclaw_swe_bench.yaml`

核心配置可以概括为：

- `agent.name: openclaw`
- `agent.config.runtime: swebench_container`
- `agent.config.openclaw_config_path: openclaw_deploy/openclaw_swe_bench.runtime.json`
- `sandbox.name: swebench_container`
- `benchmark.name: swe_bench`
- `benchmark.config.dataset: SWE-bench/SWE-bench_Verified`
- `scorer.name: swe_bench`

它的关键点是：

- 不是直接在宿主机调用模型。
- 也不是只把 prompt 发给一个普通 LLM。
- 它会在每个 SWE-bench 任务容器里安装并启动 `openclaw gateway`。
- 然后 `alphadiana/agent/openclaw.py` 再通过 OpenAI 兼容的 `/v1/chat/completions` 接口和这个 gateway 通信。

`agent.config` 里的主要字段含义如下：

| 字段 | 作用 |
| --- | --- |
| `runtime: swebench_container` | 表示 OpenClaw 不是接外部已部署 gateway，而是在 SWE-bench 任务容器里临时启动 |
| `openclaw_config_path` | 指向 OpenClaw 的基础 runtime JSON 模板 |
| `container_gateway_host` / `container_gateway_port` | 容器内部 OpenClaw gateway 的监听地址和端口 |
| `gateway_token` | 调 OpenClaw gateway 时的鉴权 token |
| `model` | 发送到 gateway 的模型名，当前是 `openclaw` |
| `max_tokens` / `request_timeout` / `max_attempts` | 控制请求长度、超时和重试 |
| `system_prompt` | 发给 OpenClaw 的用户消息前缀，告诉它要输出最小 patch |

`openclaw_deploy/openclaw_swe_bench.runtime.json` 里定义的是 OpenClaw gateway 的基础行为，包括：

- provider 如何读取 `${OPENAI_BASE_URL}`、`${OPENAI_API_KEY}`、`${OPENAI_MODEL_NAME}`
- 默认 agent model 是 `local/${OPENAI_MODEL_NAME}`
- 打开 `group:fs`、`group:runtime`、`group:web` 等工具组
- gateway 监听 `8080`
- gateway 鉴权方式是 token

运行时，`alphadiana/agent/openclaw_container_runtime.py` 会把你的环境变量注入到这个 JSON 模板里，再上传进任务容器。

## 3.2 OpenCode 配置

文件：`configs/examples/opencode_swe_bench.yaml`

核心配置可以概括为：

- `agent.name: opencode`
- `agent.config.runtime: swebench_container`
- `sandbox.name: swebench_container`
- `benchmark.name: swe_bench`
- `benchmark.config.dataset: SWE-bench/SWE-bench_Verified`
- `scorer.name: swe_bench`

它和 OpenClaw 的最大区别是：

- OpenCode 不会在容器里启动一个 OpenAI 兼容 gateway。
- 它会直接在任务容器里执行 `opencode run`。
- 任务完成后再通过 `git diff HEAD` 提取代码改动，作为最终 patch。

`agent.config` 里的主要字段含义如下：

| 字段 | 作用 |
| --- | --- |
| `runtime: swebench_container` | 表示直接在 SWE-bench 任务容器里跑 opencode |
| `tool_call: true` | 声明模型支持工具调用 |
| `timeout` | 单任务超时，传给容器内 `timeout` 命令和 opencode provider 配置 |
| `system_prompt` | 告诉 opencode 这是 SWE-bench 的修 bug 任务 |
| `api_base` / `api_key` / `model_name` | 可以直接写在 YAML，也可以从环境变量读取 |

默认情况下，OpenCode 模式优先从环境变量读取：

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL_NAME`

然后 `alphadiana/agent/opencode_container_runtime.py` 会在容器里生成：

```text
/tmp/opencode-xdg/opencode/opencode.json
```

这个文件就是 opencode 的 provider 配置文件。

## 4. 两种模式的共同配置

两者都复用了下面这几部分：

### 4.1 benchmark

```yaml
benchmark:
  name: swe_bench
  config:
    dataset: "SWE-bench/SWE-bench_Verified"
    split: "test"
    include_hints: false
    max_tasks: 1
```

含义：

- 数据集是 `SWE-bench/SWE-bench_Verified`
- 默认跑 `test` split
- `include_hints: false` 表示默认不把 `hints_text` 拼到问题里
- `max_tasks` 控制 smoke test 任务数

### 4.2 sandbox

```yaml
sandbox:
  name: swebench_container
  config:
    namespace: "swebench"
    force_rebuild: false
    keep_container: false
    keep_logs: true
    log_dir: "./logs/swebench_container"
    gateway_host: "127.0.0.1"
    gateway_port: 8080
```

含义：

- 每个 task 都会创建一个官方 SWE-bench 实例容器
- `force_rebuild: false` 表示优先复用已有镜像
- `keep_container: false` 表示任务结束后自动删容器
- `keep_logs: true` 表示保留日志
- `gateway_port: 8080` 是容器里为 OpenClaw gateway 预留的映射端口

### 4.3 scorer

```yaml
scorer:
  name: swe_bench
  config:
    timeout: 1800
    cache_level: "env"
    namespace: "swebench"
    force_rebuild: false
    log_dir: "./swe_bench_logs"
```

这里用的是仓库内置的 `swe_bench` scorer。它会调用官方 `swebench` harness 来做 patch 应用与测试执行，而不是走 `exact_match` 之类的通用打分器。

## 5. 怎么跑 smoke test

## 5.1 先做配置校验

```bash
source scripts/activate.sh

export OPENAI_BASE_URL=...
export OPENAI_API_KEY=...
export OPENAI_MODEL_NAME=...

python -m alphadiana.cli validate configs/examples/openclaw_swe_bench.yaml \
  -o run_id=my-swebench-smoke \
  -o benchmark.config.max_tasks=1

python -m alphadiana.cli validate configs/examples/opencode_swe_bench.yaml \
  -o run_id=my-opencode-smoke \
  -o benchmark.config.max_tasks=1

python -m alphadiana.cli validate configs/examples/zeroclaw_swe_bench.yaml \
  -o run_id=my-zeroclaw-smoke \
  -o benchmark.config.max_tasks=1
```

## 5.2 真正执行

OpenClaw：

```bash
python -m alphadiana.cli run configs/examples/openclaw_swe_bench.yaml \
  -o run_id=my-swebench-smoke \
  -o benchmark.config.max_tasks=1
```

OpenCode：

```bash
python -m alphadiana.cli run configs/examples/opencode_swe_bench.yaml \
  -o run_id=my-opencode-smoke \
  -o benchmark.config.max_tasks=1
```

ZeroClaw：

```bash
python -m alphadiana.cli run configs/examples/zeroclaw_swe_bench.yaml \
  -o run_id=my-zeroclaw-smoke \
  -o benchmark.config.max_tasks=1
```

如果你想直接复现“1 个 task、单 agent、MiniMax M2.5 后端”的 smoke，建议保持：

```bash
-o benchmark.config.max_tasks=1
-o max_concurrent=1
```

## 5.3 运行前要注意什么

这里真正容易卡住的点已经从“scorer 未注册”变成了下面几类：

1. 本地依赖没装齐：至少要有 `pip install -e '.[agents,benchmarks,swebench]'`
2. Docker 不可用：官方任务容器和官方评测容器都依赖 Docker
3. 上游模型配置不通：`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL_NAME`
4. Hugging Face 数据集拉取异常：必要时设置 `HF_ENDPOINT=https://hf-mirror.com`

按 smoke 标准，成功与否要看三件事：

- `results/<run_id>/tasks/<task_id>.json` 已经写出
- 任务记录里没有 `error`
- dashboard 显示 `O` 或 `X`，而不是 `-`

对当前这三个 example config，更具体的预期结果是：

- `openclaw_swe_bench.yaml`：应当拿到 task JSON、dashboard `O/X`，并且 artifacts 中能看到 OpenClaw session / gateway 相关产物
- `opencode_swe_bench.yaml`：应当拿到 task JSON、dashboard `O/X`，并且 patch 优先来自 `git diff HEAD`
- `zeroclaw_swe_bench.yaml`：应当拿到 task JSON，以及容器内 `zeroclaw_output.txt` / `zeroclaw_stderr.log` artifacts；如果是当前本地 `Qwen/Qwen3.5-27B` 路径，provider overflow 现在会保留成 `provider_error`

本地真实 smoke 证据见：

- `context/pr26-swebench-verified/smoke-validation.md`

## 6. 和 main 分支的 SWE-bench Pro 路径有什么不同

你之前用的“预装镜像”路径，和当前仓库这条 Verified 路径不是同一套 runtime。

`main` 分支里已经验证过的 OpenClaw SWE-bench Pro smoke，走的是：

- `agent.name: swebench_docker`
- `agent.config.agent_type: openclaw`
- 通过 `alphadiana/agent/swebench_assets/run_openclaw.sh` 把 runner 和配置注入任务镜像
- 默认 runtime image 来自 `PREBUILT_SANDBOX_IMAGE`

也就是说，`main` 的 SWE-bench Pro 路径本质上是“预装 runtime image + 注入 runner/assets”。

当前仓库里的 `configs/examples/openclaw_swe_bench.yaml` 则不同：

- `agent.name: openclaw`
- `agent.config.runtime: swebench_container`
- 每个 SWE-bench task container 都由 `alphadiana/agent/openclaw_container_runtime.py` 在容器内准备 Node.js、OpenClaw 和 gateway

因此，两条链虽然都能跑 OpenClaw，但工程取舍不同：

- `main` 的 SWE-bench Pro 路径更接近长期最佳实践，优点是复现更稳、网络依赖更少、单任务启动更快
- 当前仓库的 Verified 路径更灵活，但运行时依赖更多，尤其对容器内网络更敏感

如果目标是长期维护和多机复现，建议优先向 `main` 的 prebuilt-image 路径对齐；当前仓库的 runtime install 方案更适合作为过渡或 fallback。

## 7. 当前 OpenClaw 的网络稳态策略

当前仓库里的 OpenClaw 容器链路已经做了一个显式稳态修复，用来处理 `openclaw@2026.3.7` 依赖树里最容易卡住的 GitHub 依赖 `libsignal-node`。

同时，当前 `swebench_container` 路径还有一个很重要的默认约束：

- task container 里的 OpenClaw gateway 不能默认只绑 `127.0.0.1`
- 如果 runtime JSON 渲染成 `gateway.bind=custom` 且 `customBindHost=127.0.0.1`，那么容器内 `openclaw-gateway` 进程虽然活着，宿主机通过 Docker published port 去访问 `http://127.0.0.1:<host-port>/v1/models` 仍然会直接 `connection reset by peer`
- 当前主线代码已经把这个默认行为改成“非 loopback bind”，只有在你显式指定 `container_gateway_bind_host` / `gateway_bind_host` 时才走自定义 bind
- 因此，如果你在本地 smoke 里看到 task container 已经起来、gateway log 也正常，但 host 侧 `/v1/models` 一直 reset，不要先怀疑模型服务，先检查渲染后的 `openclaw.json` 里 `gateway.bind` / `customBindHost`

默认行为现在是：

- OpenClaw 仍然在 task container 里执行 `npm install -g openclaw@2026.3.7`
- 但在安装前，宿主机会先准备 `libsignal-node` 的 bare git mirror
- runtime 会把这个 mirror 作为 tar 包上传到 task container
- 容器内通过 `git url.insteadOf` 把 `https://github.com/whiskeysockets/libsignal-node.git` 重写到本地 `file://` mirror

这样做的目的不是“完全取消容器内安装”，而是：

- 避免容器在安装 OpenClaw 时直接访问 GitHub
- 避免把宿主机的 loopback 代理地址直接传进容器后失效

同时，`swebench_container` 现在会跳过把 `127.0.0.1` / `localhost` / `::1` 这类 loopback proxy 环境变量透传进 task container。因为 bridge 网络下的容器无法直接使用宿主机 loopback 代理。

要注意，这只是当前路径的稳态修复，不是长期最佳实践。它仍然依赖：

- npm registry
- Node tarball 下载
- 宿主机至少能在预热 bare mirror 时访问 GitHub，或者本地已经有缓存 mirror

长期建议仍然是预装 runtime image，把 OpenClaw 和关键依赖直接烘进镜像。

### 7.1 单题耗时该怎么理解

本地真实 smoke 的具体耗时记录放在 `context/pr26-swebench-verified/smoke-validation.md`。

对使用者而言，真正要记住的是：

- 当前 Verified 路径可以稳定跑通真实 smoke
- 单题优化收益通常不是数量级差异
- prebuilt overlay 更大的价值在于减少运行时安装带来的网络波动，而不是把单题时间砍到很小

### 7.2 什么时候值得做 prebuilt overlay

从投入产出比看，这件事更偏向“稳定性优化”，而不只是“单题提速”：

- 如果只是做少量 smoke 或人工调试，当前 runtime install 路径已经够用
- 如果后面要跑较多 task、pilot 或 full run，prebuilt overlay 会更值得
- 更大的收益通常不是单题更快，而是少掉容器内 `npm install` 带来的 GitHub / npm 网络波动

所以当前建议是：

- 先保留现有可用路径作为默认 fallback
- 等后续进入批量运行阶段，再把 prebuilt overlay 作为单独优化项推进

## 8. OpenClaw 和 OpenCode 的实际区别

可以把两者理解成两条不同的“任务求解路径”。

### 8.1 OpenClaw

特点：

- 在任务容器里安装并启动 `openclaw gateway`
- AlphaDiana 通过 HTTP 调这个 gateway
- gateway 内部会自己执行 agent loop、工具调用、轨迹记录
- 结果里还能附带 session 轨迹、gateway 日志、workspace 快照

适合场景：

- 需要完整 agent 轨迹
- 希望保留 OpenClaw 的内部编排能力
- 希望统一走 OpenAI 兼容 API 接口

### 8.2 OpenCode

特点：

- 在任务容器里直接执行 CLI
- 不需要单独的 gateway 进程
- 通过 `git diff HEAD` 抽取最终补丁
- 实现链路更直接

适合场景：

- 只关心代码修改结果
- 想减少 gateway 启动和 HTTP 调用这一层
- 希望更贴近“命令行 coding agent 直接改仓库”的执行方式

## 9. 建议的排查顺序

如果后面运行有问题，推荐按这个顺序检查：

1. `source scripts/activate.sh` 是否执行了
2. `docker ps` 是否正常
3. `OPENAI_BASE_URL` 是否是容器内可访问地址，而不是宿主机的 `localhost`
4. `python -m alphadiana.cli validate ...` 是否通过
5. 当前是否已经补齐 `swe_bench` scorer 注册
6. `logs/swebench_container` 和 `swe_bench_logs` 里是否有构建或执行日志

## 10. 你现在最该关注的文件

配置入口：

- `configs/examples/openclaw_swe_bench.yaml`
- `configs/examples/opencode_swe_bench.yaml`
- `configs/examples/zeroclaw_swe_bench.yaml`
- `openclaw_deploy/openclaw_swe_bench.runtime.json`

执行入口：

- `alphadiana/cli.py`
- `alphadiana/runner/runner.py`

SWE-bench 数据与容器：

- `alphadiana/benchmark/swe_bench.py`
- `alphadiana/utils/swebench.py`
- `alphadiana/sandbox/swebench_container.py`

三个 agent 的具体实现：

- `alphadiana/agent/openclaw.py`
- `alphadiana/agent/openclaw_container_runtime.py`
- `alphadiana/agent/opencode.py`
- `alphadiana/agent/opencode_container_runtime.py`
- `alphadiana/agent/zeroclaw.py`

如果你想看“原理、时序、每个文件分别干什么”，请继续看
`context/pr26-swebench-verified/implementation-notes.md`。
