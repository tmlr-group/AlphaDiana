# OpenClaw Reasoning Repro

这个目录是 PR 版的最小复现包，目标只有两个：

1. 起一个不会和常见本地默认端口冲突的 ROCK 实例
2. 部署带本地自定义 OpenClaw 的 sandbox，并验证通过 ROCK proxy 可以流式拿到 `reasoning_content`

脚本只有两个：

- [setup.sh](setup.sh)
- [verify.sh](verify.sh)

## 前提

- 已安装 `docker`
- 已安装 `conda`
- 本仓库路径为 `/home/user/projects/AlphaDiana`
- 本地 OpenClaw 仓库路径默认为 `/home/user/projects/openclaw`
- 运行前设置：

```bash
export ARK_API_KEY=your_real_key
```

如果本地 OpenClaw 不在默认路径，额外设置：

```bash
export LOCAL_OPENCLAW_ROOT=/path/to/openclaw
```

## 使用方式

```bash
bash repro/openclaw_reasoning/setup.sh
bash repro/openclaw_reasoning/verify.sh
```

## 这两个脚本分别做什么

### `setup.sh`

- 使用一组固定但不常见的端口启动 ROCK
- 使用固定 Redis 容器名 `redis-stack-openclaw-repro`
- 生成独立的 ROCK 配置，不依赖默认 `9000/9001/6379`
- 自动把 OpenClaw reasoning patch 应用到本地 OpenClaw 仓库
- 从本地 OpenClaw 执行 `npm pack`
- 按 README 主链 deploy sandbox
- 用打包后的本地 OpenClaw 替换 sandbox 内版本
- 启动支持流式 `reasoning_content` 的 gateway
- 把运行态信息写到 `repro/openclaw_reasoning/generated/runtime.env`

### `verify.sh`

- 对 ROCK proxy endpoint 发起 `stream=true` 的数学题请求
- 验证 SSE 里存在 `reasoning_content`
- 对随机数题发起请求，验证工具链
- 跑一个 1 题的 `alphadiana run`
- 输出 `summary.json`，确认结果文件里有：
  - `reasoning_content`
  - `toolCall exec`
  - `toolResult`

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

### 2. AlphaDiana 现在能保存 reasoning 和工具轨迹

相关修复在：

- [openclaw.py](../../alphadiana/agent/openclaw.py)

当前结果文件里已经能保留：

- `response_json.choices[0].message.reasoning_content`
- `trajectory[].tool_calls`
- `trajectory` 中的 `toolResult`

### 3. AIME 2025 smoke run 已验证

1 题 smoke run 在当前链路上已经跑通，并且结果文件里同时有：

- 最终答案
- reasoning content
- toolCall / toolResult

## 当前明确不解决的点

- `max_tokens` 在当前 OpenClaw `chat/completions` 兼容层上没有被可靠执行
- 这是已确认问题，但本 PR 不处理

## 相关文件

- OpenClaw patch 说明：[OPENCLAW_PATCH.md](OPENCLAW_PATCH.md)
- 可直接应用的 patch：[openclaw-stream-reasoning.patch](openclaw-stream-reasoning.patch)
