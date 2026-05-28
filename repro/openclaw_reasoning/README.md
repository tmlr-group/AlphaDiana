# OpenClaw Reasoning Repro

这个目录是这次 PR 的最小复现包，面向两类读者：

1. 开发者：快速理解这次为什么改 AlphaDiana-dev，以及这些改动分别解决了什么问题
2. 使用者：按两条脚本把 `ROCK + 自定义 OpenClaw + Kimi` 跑起来，并验证 `reasoning_content` 确实能经由 ROCK proxy 流式返回

脚本只有两个：

- [setup.sh](setup.sh)
- [verify.sh](verify.sh)

## 这次 PR 解决什么问题

这次不是在 AlphaDiana 里新增一个全新的 OpenClaw 能力，而是把当前这条真实链路补齐到“可复现、可验证”的状态：

`AlphaDiana -> ROCK proxy -> sandbox 内自定义 OpenClaw -> Kimi`

我们这次确认并修复/补齐的是：

1. 可以用固定但不常见的本地端口起一套独立 ROCK，避免直接占用常见默认端口
2. 可以从固定 OpenClaw 基线 commit 出发，应用这次的 reasoning patch，再把自定义 OpenClaw 部署进 sandbox
3. 通过 ROCK proxy 的标准 OpenAI 风格 endpoint，可以真实流式拿到 `reasoning_content`
4. AlphaDiana 在当前这版 OpenClaw/ROCK 部署方式下，可以正确保留 reasoning，并且不会把“只有 reasoning、没有 final content”的情况当成空回复
5. AlphaDiana 可以在当前 sandbox 路径布局下，正确补回 `toolCall/toolResult`

## 对 AlphaDiana-dev 补了什么

这次 PR 里的有效内容可以分成两类。

### 1. 复现与验证入口

- [setup.sh](setup.sh)
  负责起本地 ROCK、准备固定版本的 OpenClaw、应用 patch、部署 sandbox、替换 sandbox 内 OpenClaw 并启动 gateway
- [verify.sh](verify.sh)
  负责直接验证 ROCK proxy 的流式 `reasoning_content`，并再跑一个最小 AlphaDiana agent smoke test
- [OPENCLAW_PATCH.md](OPENCLAW_PATCH.md)
  说明 OpenClaw patch 改了什么、为什么要改、基于哪个 commit
- [openclaw-stream-reasoning.patch](openclaw-stream-reasoning.patch)
  提供可直接应用到固定 OpenClaw 基线 commit 的补丁

### 2. AlphaDiana 兼容性修复

- [openclaw.py](../../alphadiana/agent/openclaw.py)
  补了 reasoning fallback 和当前 OpenClaw session 路径兼容
- [test_fix_empty_response.py](../../tests/test_fix_empty_response.py)
  增加“只有 reasoning 也不应视为空回复”的回归测试
- [aime.py](../../alphadiana/benchmark/aime.py)
  补了 AIME 2025 数据字段兼容，保证 smoke run 能稳定跑起来

这些改动里，`toolCall/toolResult` 的解析思路本来就存在；这次补的是对当前这版 OpenClaw 部署路径的兼容，不是重新发明一套轨迹机制。

## 前提

- 已安装 `docker`
- 已安装 `conda`
- 在 AlphaDiana 仓库根目录运行脚本
- 本地 OpenClaw 仓库默认路径是 `../openclaw`
- 运行前设置：

```bash
export ARK_API_KEY=your_real_key
```

如果本地 OpenClaw 不在默认路径，额外设置：

```bash
export LOCAL_OPENCLAW_ROOT=/path/to/openclaw
```

## OpenClaw 基线版本

这套流程不是直接拉最新 OpenClaw，而是固定到我们实际实验用的基线 commit：

```bash
mkdir -p ../openclaw
cd ../openclaw
git init
git remote add origin https://github.com/openclaw/openclaw.git
git fetch --depth 1 origin f8eb23de1c4a8c5256be679c5cfd23ca1a031a06
git checkout f8eb23de1c4a8c5256be679c5cfd23ca1a031a06
```

然后再由 [setup.sh](setup.sh) 自动应用本仓库里的 patch：

```bash
git apply /path/to/AlphaDiana-dev/repro/openclaw_reasoning/openclaw-stream-reasoning.patch
```

实际上你不需要手动做这几步：
- 如果 `../openclaw` 不存在，`setup.sh` 会自动初始化一个仓库并浅拉取这个 commit
- 如果 `../openclaw` 已存在但不是这个 commit，`setup.sh` 会在工作区干净时自动切到这个 commit

## 使用方式

```bash
bash repro/openclaw_reasoning/setup.sh
bash repro/openclaw_reasoning/verify.sh
```

## 如何验证修改效果

这套复现只要求验证两件事：

1. `curl` ROCK proxy 的 `/chat/completions`，在 `stream=true` 时能看到真实的 `reasoning_content`
2. AlphaDiana 调用这条链路时，不只是能拿到最终答案，还保留了 reasoning 和工具轨迹

`verify.sh` 会把这两件事都做掉。

## 这两个脚本分别做什么

### `setup.sh`

- 使用一组固定但不常见的端口启动 ROCK
- 使用固定 Redis 容器名 `redis-stack-openclaw-repro`
- 生成独立的 ROCK 配置，不依赖默认 `9000/9001/6379`
- 自动把 OpenClaw reasoning patch 应用到本地 OpenClaw 仓库
- 必要时自动 clone/checkout 到固定 OpenClaw 基线 commit
- 首次运行时自动安装 OpenClaw 的 pnpm 依赖
- 从本地 OpenClaw 执行 `npm pack`
- 按 README 主链 deploy sandbox
- 用打包后的本地 OpenClaw 替换 sandbox 内版本
- 启动支持流式 `reasoning_content` 的 gateway
- 把运行态信息写到 `repro/openclaw_reasoning/generated/runtime.env`

### `verify.sh`

- 对 ROCK proxy endpoint 发起 `stream=true` 的数学题请求
- 验证 SSE 里存在 `reasoning_content`
- 可选：对随机数题直接发请求，补看工具链
- 用 `OpenClawAgent.solve()` 跑一个最小 AlphaDiana smoke test
- 输出 `summary.json`，确认结果里有：
  - `reasoning_content`
  - `toolCall exec`
  - `toolResult`

## 各文件修改目的

如果你是在 review 这次 PR，可以重点看下面几个文件。

### `repro/openclaw_reasoning/setup.sh`

- 把复现入口压缩成一个部署脚本
- 固定一组不常见端口，减少和本机已有服务冲突的概率
- 固定 OpenClaw 基线 commit，避免“拉最新版导致补丁失效”
- 自动应用 OpenClaw reasoning patch，并把本地包部署进 sandbox

### `repro/openclaw_reasoning/verify.sh`

- 提供面向用户的最小验证路径
- 直接证明 ROCK proxy endpoint 会流式返回 `reasoning_content`
- 再通过 AlphaDiana 的 `OpenClawAgent.solve()` 证明结果不是空回复，且保留 reasoning/tool 轨迹

### `repro/openclaw_reasoning/openclaw-stream-reasoning.patch`

- 让 OpenClaw 的 OpenAI 兼容 gateway 暴露 `reasoning_content`
- 这是“通过 ROCK proxy 的 `/chat/completions` 就能看到流式 reasoning”这条实验结果的前提

### `alphadiana/agent/openclaw.py`

- 补 reasoning fallback
- 解决“推理时间长、没有 final content 时被判成空回复”的问题
- 兼容当前 OpenClaw 在 `/tmp/oc_home/.openclaw` 下落 session 的情况

### `tests/test_fix_empty_response.py`

- 防止后续回归把 reasoning-only 的场景再次打坏

### `alphadiana/benchmark/aime.py`

- 兼容当前 AIME 2025 数据字段，确保 smoke run 的 benchmark 侧不再因为字段名差异直接失败

## 我们这轮已经得到的实验结果

### 1. 经过 ROCK proxy 能流式拿到 reasoning content

目标 endpoint：

```text
http://localhost:<<ROCK_PORT>>/apis/envs/sandbox/v1/sandboxes/<<SANDBOX-ID>>/proxy/v1/chat/completions
```

在 `stream=true` 时，SSE 中可以看到：

```text
data: {"choices":[{"delta":{"reasoning_content":"..."}}]}
```

这不是 stub，而是：

`ROCK proxy -> sandbox 内本地 OpenClaw -> Kimi-K2.5`

### 2. AlphaDiana 现在能在这条链路上正确保留 reasoning 和工具轨迹

相关修复在：

- [openclaw.py](../../alphadiana/agent/openclaw.py)

当前结果文件里已经能保留：

- `response_json.choices[0].message.reasoning_content`
- `trajectory[].tool_calls`
- `trajectory` 中的 `toolResult`

### 3. AlphaDiana smoke 已验证

当前最小 smoke 已确认结果里同时有：

- 最终答案
- reasoning content
- toolCall / toolResult

## 当前明确不解决的点

- `max_tokens` 在当前 OpenClaw `chat/completions` 兼容层上没有被可靠执行
- 这是已确认问题，但本 PR 不处理

## 相关文件

- OpenClaw patch 说明：[OPENCLAW_PATCH.md](OPENCLAW_PATCH.md)
- 可直接应用的 patch：[openclaw-stream-reasoning.patch](openclaw-stream-reasoning.patch)
