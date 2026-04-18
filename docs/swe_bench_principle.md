# SWE-bench 运行原理与相关文件职责

本文从执行时序角度说明当前仓库里 OpenClaw 和 OpenCode 两种 SWE-bench 方案是怎么工作的，并解释各个关键文件分别负责什么。

## 1. 整体架构

不论是 OpenClaw 还是 OpenCode，外层总流程都是同一套 AlphaDiana 运行框架：

1. CLI 读取 YAML 配置与命令行 override
2. 解析为 `ExperimentConfig`
3. `Runner.setup()` 从 registry 中实例化 benchmark、agent、sandbox、scorer
4. benchmark 从 Hugging Face 加载 SWE-bench 任务
5. sandbox 为每个任务创建一个官方 SWE-bench 容器
6. agent 在容器里求解任务并产出 patch
7. scorer 对 patch 评分
8. result store 持久化结果、日志和元数据

其中真正的分叉点只在第 6 步：

- OpenClaw：在容器里启动 OpenClaw gateway，再通过 HTTP 调用它
- OpenCode：在容器里直接执行 `opencode run`

## 2. 执行主链路

## 2.1 CLI 层

文件：

- `alphadiana/cli.py`
- `alphadiana/config/experiment_config.py`
- `alphadiana/config/validator.py`

职责：

- `alphadiana/cli.py`
  - 提供 `validate`、`run`、`batch`、`report`、`env` 等命令
  - 把 `-o a.b.c=value` 这样的 override 合并进最终配置
  - 调用 `Runner.setup()` 和 `Runner.run()`
- `alphadiana/config/experiment_config.py`
  - 把 YAML 解析成 `ExperimentConfig`
  - 支持环境变量展开
  - 支持递归合并 override
- `alphadiana/config/validator.py`
  - 做结构级校验
  - 检查 `openclaw` / `opencode` 与 `swebench_container` 的组合是否合法

注意：

`validate` 只校验“配置结构是否合理”，不会检查 `scorer` 是否真的已经在注册表中实现。因此当前 `validate` 能过，不代表 `run` 一定能跑通。

## 2.2 Runner 层

文件：

- `alphadiana/runner/runner.py`

职责：

- 在 `setup()` 中导入各模块以触发 registry 注册
- 按配置实例化：
  - benchmark
  - agent
  - sandbox
  - scorer
- 在 `run()` 中：
  - 加载任务
  - 创建每个任务的 sandbox session
  - 调用 `agent.solve(task, sandbox_session)`
  - 调用 `scorer.score(task, response)`
  - 把结果写到结果目录

对当前 SWE-bench 配置来说，`Runner` 的关键行为是：

- 它会导入 `alphadiana.benchmark.swe_bench`
- 它会导入 `alphadiana.agent.openclaw` 与 `alphadiana.agent.opencode`
- 它会导入 `alphadiana.sandbox.swebench_container`
- 但当前并没有看到一个 `alphadiana.scorer.swe_bench` 被导入和注册

这就是当前 setup 阶段报错的根因。

## 3. SWE-bench 任务是如何被加载的

文件：

- `alphadiana/benchmark/swe_bench.py`
- `alphadiana/utils/swebench.py`

### 3.1 `alphadiana/benchmark/swe_bench.py`

职责：

- 从 `SWE-bench/SWE-bench_Verified` 加载数据
- 把每条样本转换成 `BenchmarkTask`
- 把下列信息塞进 `task.metadata`
  - `instance_id`
  - `repo`
  - `base_commit`
  - `version`
  - `FAIL_TO_PASS`
  - `PASS_TO_PASS`
  - `test_patch`
  - `environment_setup_commit`

生成后的 `BenchmarkTask` 有三类关键字段：

- `task_id`：例如 `swe_<instance_id>`
- `problem`：给 agent 的 issue 描述
- `ground_truth`：数据集里原始参考 patch

### 3.2 `alphadiana/utils/swebench.py`

职责：

- 从 `BenchmarkTask.metadata` 中恢复出 SWE-bench harness 需要的 instance dict
- 推断 `instance_id`
- 把 `FAIL_TO_PASS`、`PASS_TO_PASS` 转成稳定的 JSON 字符串格式

这个文件的作用是桥接：

- AlphaDiana 内部的 `BenchmarkTask`
- SWE-bench harness 需要的原始 instance 描述

## 4. 容器 sandbox 是如何工作的

文件：

- `alphadiana/sandbox/swebench_container.py`

这是整个 SWE-bench 链路的基础设施层。

### 4.1 它做了什么

每个任务开始时，这个 sandbox 会：

1. 根据 task 元数据重建一个 SWE-bench instance
2. 调用官方 SWE-bench harness 构建环境镜像和 instance 镜像
3. 基于 instance 镜像启动一个 Docker 容器
4. 暴露容器里的 gateway 端口到宿主机随机端口
5. 返回一个 `SWEBenchContainerSession`

### 4.2 `SWEBenchContainerSession` 提供什么能力

这个 session 是 agent 真正使用的执行接口。它提供：

- `execute(command)`：在容器里跑 shell 命令
- `upload(filename, content)`：把文件上传到容器
- `download(filename)`：从容器下载文件
- `read_text(filename)`：读取容器内文件
- `metadata()`：返回容器元数据
- `gateway_api_base()`：返回宿主机可访问的 gateway 地址

所以从 agent 的视角看：

- 它不用自己管 Docker
- 它只需要把容器当成一个“带文件系统和命令执行能力的远程工作区”

## 5. OpenClaw 模式的原理

文件：

- `configs/examples/openclaw_swe_bench.yaml`
- `openclaw_deploy/openclaw_swe_bench.runtime.json`
- `alphadiana/agent/openclaw.py`
- `alphadiana/agent/openclaw_container_runtime.py`

## 5.1 OpenClaw 的核心思想

OpenClaw 模式不是“在 Python 里实现 agent loop”，而是：

- 在任务容器里启动一个 `openclaw gateway`
- 让 gateway 自己去做多轮推理、工具调用、上下文管理和轨迹记录
- AlphaDiana 只负责把任务发给 gateway，并收集结果与产物

也就是说，AlphaDiana 在 OpenClaw 模式里更像一个：

- runtime 启动器
- HTTP 客户端
- 结果采集器

## 5.2 OpenClaw 的运行时序

一次任务大致会经历下面这些步骤：

1. `Runner` 创建 `SWEBenchContainerSession`
2. `OpenClawAgent.solve(task, sandbox)` 被调用
3. 如果配置了 `runtime=swebench_container`，进入 `OpenClawContainerRuntimeManager.ensure_ready()`
4. runtime manager 做这些事情：
   - 读取 `openclaw_deploy/openclaw_swe_bench.runtime.json`
   - 用环境变量填充 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL_NAME`
   - 把渲染后的 JSON 上传到容器内
   - 在宿主机预热 `libsignal-node` bare mirror，并上传到任务容器
   - 如有需要，在容器内安装 Node.js 与 `openclaw`
   - 用 `git url.insteadOf` 把该 GitHub 依赖重写到本地 `file://` mirror
   - 启动 `openclaw gateway`
   - 轮询 `/v1/models` 等待 gateway ready
   - 做一次 warmup 请求
5. `OpenClawAgent` 对 gateway 发起 `/v1/chat/completions`
6. gateway 在容器内部跑完整的 agent loop
7. `OpenClawAgent` 收到最终文本输出
8. 它再尝试从 session 文件、workspace、gateway 日志里恢复更多轨迹和产物
9. 最终返回 `AgentResponse`

## 5.3 `openclaw_deploy/openclaw_swe_bench.runtime.json` 的作用

这是 OpenClaw 的“基础运行时模板”，主要定义：

- 模型 provider 配置
- agent 默认模型
- token / timeout 等默认参数
- 可用工具组
- gateway 监听端口与鉴权方式

但它不是最终配置。

最终配置是在 `alphadiana/agent/openclaw_container_runtime.py` 里动态生成的，原因是：

- 每次任务运行时，真实的环境变量可能不同
- workspace 根目录可能因任务实例不同而不同
- 容器中的 `PATH` 也要动态补齐 testbed Python 和工具路径

## 5.4 `alphadiana/agent/openclaw_container_runtime.py` 的职责

这是 OpenClaw 容器模式真正的 runtime manager。主要负责：

- 定位并读取基础 runtime JSON
- 把上游模型环境变量写入 JSON
- 设置 workspace 路径
- 调整 gateway 配置
- 允许 `exec` 工具直接在容器里执行命令
- 在容器里按需安装 Node.js 和 `openclaw`
- 在宿主机准备并缓存 `libsignal-node` bare mirror
- 把 bare mirror 上传到任务容器，并用 `git url.insteadOf` 重写 GitHub 依赖
- 启动 `openclaw gateway`
- 探测 gateway 是否 ready
- 收集以下 artifacts：
  - gateway 日志
  - workspace 快照路径
  - 部分 workspace 文件内容
  - session JSONL 路径
  - sandbox metadata

这里要特别区分它和 `main` 分支的 SWE-bench Pro 路径：

- `main` 的 Pro smoke 走 `agent.name: swebench_docker`
- 它通过预装 runtime image 和 `swebench_assets/run_openclaw.sh` 注入运行时
- 当前分支的 Verified 路径才是这个文件负责的“task container 内现装 OpenClaw”

所以，`openclaw_container_runtime.py` 是当前分支新增路径的 runtime manager，不是 `main` 那条 prebuilt-image 执行链的核心组件。

## 5.5 `alphadiana/agent/openclaw.py` 的职责

这个文件是 AlphaDiana 侧的 OpenClaw agent wrapper。它负责：

- 接收 `BenchmarkTask`
- 构造要发给 OpenClaw 的 user message
- 决定是直连现成 gateway，还是通过 runtime manager 动态启动 gateway
- 对 `/chat/completions` 发请求
- 做重试、超时、错误分类
- 尝试恢复 reasoning trajectory
- 把 answer、trajectory、artifacts 统一包装成 `AgentResponse`

一句话概括：

- `openclaw_container_runtime.py` 负责“把容器里的 OpenClaw 拉起来”
- `openclaw.py` 负责“把任务发进去，再把结果收回来”

## 6. OpenCode 模式的原理

文件：

- `configs/examples/opencode_swe_bench.yaml`
- `alphadiana/agent/opencode.py`
- `alphadiana/agent/opencode_container_runtime.py`

## 6.1 OpenCode 的核心思想

OpenCode 模式更接近传统 CLI coding agent：

- 在任务容器中安装并执行 `opencode`
- 让它直接对仓库工作目录读写文件
- 任务结束后再用 `git diff HEAD` 把实际代码改动提取出来

这里没有单独的 gateway 进程，也没有一层 HTTP API。

## 6.2 OpenCode 的运行时序

一次任务大致经历这些步骤：

1. `Runner` 创建 `SWEBenchContainerSession`
2. `OpenCodeAgent.solve(task, sandbox)` 被调用
3. 因为 `runtime=swebench_container`，进入 `_solve_in_container()`
4. `OpenCodeContainerRuntimeManager.run_task()` 执行：
   - 在容器里按需安装 Node.js 与 `opencode`
   - 生成 `/tmp/opencode-xdg/opencode/opencode.json`
   - 写入 provider、model、apiKey、baseURL、timeout 等配置
   - 导出 `OPENAI_API_KEY` 与 `OPENAI_BASE_URL`
   - 在仓库目录执行 `opencode run --format json --dir <repo> ...`
5. opencode 在容器里直接修改仓库文件
6. 运行结束后执行 `git diff HEAD`
7. 以 `git diff` 结果作为优先 patch 输出
8. 如果没有 diff，再从 agent 文本输出里回退提取 patch

## 6.3 `alphadiana/agent/opencode_container_runtime.py` 的职责

它负责容器内的 CLI 运行环境准备与执行，主要包括：

- 在容器里安装 Node.js
- 安装 `opencode-ai`（其平台二进制通过 `optionalDependencies` 下发，不能加 `--omit=optional`）
- 生成 XDG 配置目录
- 写入 opencode provider JSON
- 执行 `opencode run`
- 用 `timeout` 包住命令，防止无限挂起
- 用 `git diff HEAD` 提取最终补丁
- 收集 session 文件列表

这个文件的关键设计点是：

- 最可信的结果来源不是模型最终回复文本
- 而是容器内仓库真实发生的代码修改，即 `git diff HEAD`

## 6.4 `alphadiana/agent/opencode.py` 的职责

它是 OpenCode 的 Python wrapper，负责：

- 读取 agent 配置和环境变量
- 在本地模式与容器模式之间切换
- 容器模式下调用 runtime manager
- 解析 JSON 行输出
- 优先使用 `git diff` 作为 patch
- 回退时从文本中抽取 diff
- 统一返回 `AgentResponse`

一句话概括：

- `opencode_container_runtime.py` 负责“在容器里跑 opencode CLI”
- `opencode.py` 负责“把任务包装成 CLI 输入，再把补丁包装回 AlphaDiana”

## 7. 两种模式的根本差异

可以把它们的差异归纳成下面四点。

### 7.1 执行形态不同

- OpenClaw：HTTP gateway + 内置 agent loop
- OpenCode：CLI 直跑 + 文件直接修改

### 7.2 结果获取方式不同

- OpenClaw：以 gateway 最终响应为主，附带 session 轨迹和日志
- OpenCode：以 `git diff HEAD` 为主，文本输出只是回退方案

### 7.3 运行时依赖不同

- OpenClaw：需要额外启动 `openclaw gateway`
- OpenCode：只要 `opencode` CLI 能跑就行

### 7.4 调试视角不同

- OpenClaw：更适合看内部轨迹、session 文件、gateway 日志
- OpenCode：更适合看最终改了哪些文件、diff 是什么

## 8. 当前已补齐的环节：`swe_bench` scorer

当前仓库已经补齐了 `alphadiana/scorer/swe_bench.py` 这条评测闭环。

它的职责是：

- 接收 agent 产出的 patch
- 调用官方 `swebench` harness 做 patch 应用和测试执行
- 回收 `report.json`、`run_instance.log`、`test_output.txt`
- 把 `correct`、`score` 和错误信息落到 AlphaDiana 的 task JSON

因此当前链路不是“只有 agent 能跑、没有正式评分”，而是：

- benchmark 负责把数据集样本转成 `BenchmarkTask`
- sandbox 负责拉起官方任务容器
- agent 负责在任务容器里生成 patch
- scorer 负责复用官方 harness 做最终验收

按 smoke 标准，最终要看的还是：

- `results/<run_id>/tasks/<task_id>.json` 是否存在
- task JSON 里是否没有 `error`
- dashboard 是否显示 `O` 或 `X`

其中：

- `O` 表示链路跑通且题目解对
- `X` 表示链路跑通但 patch 没解对

两者都属于执行成功，不是基础设施失败。

## 9. 建议你以后从哪些文件入手排查

如果问题出现在“配置加载前”，先看：

- `alphadiana/cli.py`
- `alphadiana/config/experiment_config.py`
- `alphadiana/config/validator.py`

如果问题出现在“数据集或任务构造”，先看：

- `alphadiana/benchmark/swe_bench.py`
- `alphadiana/utils/swebench.py`

如果问题出现在“容器起不来 / 文件传不进去 / 命令执行失败”，先看：

- `alphadiana/sandbox/swebench_container.py`

如果问题出现在“OpenClaw 不 ready / gateway 请求失败 / 轨迹丢失”，先看：

- `alphadiana/agent/openclaw_container_runtime.py`
- `alphadiana/agent/openclaw.py`
- `openclaw_deploy/openclaw_swe_bench.runtime.json`

如果问题出现在“OpenCode 没有改文件 / 没抽到 patch / CLI 超时”，先看：

- `alphadiana/agent/opencode_container_runtime.py`
- `alphadiana/agent/opencode.py`

如果问题出现在“评测阶段报错 / report 没生成 / 只有 patch diff 没有最终分数”，先看：

- `alphadiana/scorer/swe_bench.py`
- `alphadiana/runner/runner.py`
- `alphadiana/scorer/`

## 10. 和 main 分支的 SWE-bench Pro 路径如何对齐理解

如果只看架构思路，可以把两条路径这样对应：

- `main` 的 SWE-bench Pro：优先使用 prebuilt runtime image，把运行时准备前移到镜像构建阶段
- 当前分支的 SWE-bench Verified：优先使用 task-local runtime manager，把运行时准备放到每个 task container 启动阶段

当前分支为了稳住 OpenClaw 依赖安装，额外加入了 host-side bare mirror 这层保护。但这仍然是 runtime install 的稳态修复，不等于已经达到 `main` 那条 prebuilt-image 路径的复现稳定性。

如果后续要继续收敛实现，推荐方向是：

- 保留当前 runtime install 作为 fallback
- 同时补一条 prebuilt-image 优先路径
- 让 Verified 和 Pro 在 OpenClaw 运行时模型上尽量收敛

从真实 smoke 的投入产出比看，这个优化应当排在“批量运行稳定性”语境下理解：

- 单个 OpenClaw smoke 的总耗时里，runtime 准备部分通常只是几十秒
- 真正占大头的仍然是 agent 在仓库内阅读、编辑、测试和生成 patch 的时间
- 因此 prebuilt-image 的主要价值是减少运行时安装带来的网络和依赖波动，而不是把单题耗时降一个量级

这意味着当前分支没有必要为了 merge 立刻切到 overlay 方案，但后续如果要跑 pilot 或 full run，这会是合理的下一步收敛方向。

## 11. 一句话总结

这两个 SWE-bench 配置共用同一个 benchmark 和同一个任务容器后端，但求解方式不同：

- OpenClaw 是“容器内启动 gateway，再通过 HTTP 驱动 agent”
- OpenCode 是“容器内直接跑 coding agent CLI，再从 git diff 抽补丁”

当前仓库的主要风险点不在 scorer 是否注册，而在三处是否协同正常：

- agent 是否稳定产出 patch
- `swebench_container` 是否稳定拉起官方任务容器
- `swe_bench` scorer 是否稳定把 patch 送入官方 harness 并落盘评测结果
