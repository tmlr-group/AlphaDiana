# SWE-bench: OpenClaw 与 OpenCode 的配置与运行说明

本文说明当前仓库里两个 SWE-bench 配置文件的用途、差异、环境准备方式，以及它们各自的实际运行链路。

涉及的两个配置文件：

- `configs/examples/openclaw_swe_bench.yaml`
- `configs/examples/opencode_swe_bench.yaml`

## 1. 先说当前仓库状态

这两个 YAML 当前都可以通过 `validate`：

```bash
source scripts/activate.sh
python -m alphadiana.cli validate configs/examples/openclaw_swe_bench.yaml \
  -o run_id=my-swebench-smoke \
  -o benchmark.config.max_tasks=1

python -m alphadiana.cli validate configs/examples/opencode_swe_bench.yaml \
  -o run_id=my-opencode-smoke \
  -o benchmark.config.max_tasks=1
```

但按当前代码直接进入 `Runner.setup()` 时，还会遇到一个运行时问题：

```text
KeyError: "Scorer 'swe_bench' is not registered. Available: exact_match, llm_judge, math_verify, numeric"
```

也就是说：

- 当前这两个配置的结构是合法的。
- 但当前仓库里还没有真正注册一个名为 `swe_bench` 的 scorer。
- 所以如果不补这个 scorer，或者不把配置改成一个已存在的 scorer，`run` 在 setup 阶段就会失败。

这点和 agent 本身的 OpenClaw / OpenCode 配置无关，而是评分器注册表当前缺了一环。

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
export OPENAI_BASE_URL=http://172.17.0.1:8080/v1
```

`opencode_swe_bench.yaml` 里已经在注释里明确写了这一点。OpenClaw 模式本质上也一样，因为最终发起请求的也是任务容器里的 OpenClaw gateway。

### 2.4 可能还需要 Hugging Face 镜像

SWE-bench 数据集默认从 Hugging Face 加载。如果当前环境直连 Hugging Face 不稳定，可以考虑：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

## 3. 两个配置文件分别是怎么配的

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

从配置意图看，这显然是想使用一个专门的 SWE-bench scorer。

但就当前仓库代码而言，这个 scorer 还没有被注册，所以这里是现在的实际阻塞点。

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

## 5.3 按当前仓库状态，运行前要注意什么

如果你保持当前 YAML 不变，`run` 进入 `Runner.setup()` 时会因为 scorer 未注册而失败。

因此当前有两种处理方向：

### 方向 A：补一个真正的 `swe_bench` scorer

这也是最符合配置意图的方式。这样 OpenClaw 和 OpenCode 产出的 patch 才能按 SWE-bench 预期去评估。

### 方向 B：临时改成现有 scorer 做链路 smoke

如果你只是想先验证 agent 和容器链路是否能跑通，可以临时换成一个当前已注册 scorer：

- `exact_match`
- `llm_judge`
- `math_verify`
- `numeric`

但要注意，这样只能验证框架链路，不等于真的完成 SWE-bench 的官方评测逻辑。

## 6. OpenClaw 和 OpenCode 的实际区别

可以把两者理解成两条不同的“任务求解路径”。

### 6.1 OpenClaw

特点：

- 在任务容器里安装并启动 `openclaw gateway`
- AlphaDiana 通过 HTTP 调这个 gateway
- gateway 内部会自己执行 agent loop、工具调用、轨迹记录
- 结果里还能附带 session 轨迹、gateway 日志、workspace 快照

适合场景：

- 需要完整 agent 轨迹
- 希望保留 OpenClaw 的内部编排能力
- 希望统一走 OpenAI 兼容 API 接口

### 6.2 OpenCode

特点：

- 在任务容器里直接执行 CLI
- 不需要单独的 gateway 进程
- 通过 `git diff HEAD` 抽取最终补丁
- 实现链路更直接

适合场景：

- 只关心代码修改结果
- 想减少 gateway 启动和 HTTP 调用这一层
- 希望更贴近“命令行 coding agent 直接改仓库”的执行方式

## 7. 建议的排查顺序

如果后面运行有问题，推荐按这个顺序检查：

1. `source scripts/activate.sh` 是否执行了
2. `docker ps` 是否正常
3. `OPENAI_BASE_URL` 是否是容器内可访问地址，而不是宿主机的 `localhost`
4. `python -m alphadiana.cli validate ...` 是否通过
5. 当前是否已经补齐 `swe_bench` scorer 注册
6. `logs/swebench_container` 和 `swe_bench_logs` 里是否有构建或执行日志

## 8. 你现在最该关注的文件

配置入口：

- `configs/examples/openclaw_swe_bench.yaml`
- `configs/examples/opencode_swe_bench.yaml`
- `openclaw_deploy/openclaw_swe_bench.runtime.json`

执行入口：

- `alphadiana/cli.py`
- `alphadiana/runner/runner.py`

SWE-bench 数据与容器：

- `alphadiana/benchmark/swe_bench.py`
- `alphadiana/utils/swebench.py`
- `alphadiana/sandbox/swebench_container.py`

两个 agent 的具体实现：

- `alphadiana/agent/openclaw.py`
- `alphadiana/agent/openclaw_container_runtime.py`
- `alphadiana/agent/opencode.py`
- `alphadiana/agent/opencode_container_runtime.py`

如果你想看“原理、时序、每个文件分别干什么”，请继续看 `docs/swe_bench_principle.md`。
