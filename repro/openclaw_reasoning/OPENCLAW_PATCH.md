# OpenClaw Patch Notes

这个 PR 不把整份 OpenClaw 源码 vendoring 到 AlphaDiana 仓库里。  
改动通过一个 patch 文件提供：

- [openclaw-stream-reasoning.patch](openclaw-stream-reasoning.patch)

## patch 做了什么

这份 patch 是基于 OpenClaw commit：

- `f8eb23de1c4a8c5256be679c5cfd23ca1a031a06`

### 1. 让 OpenAI 兼容网关暴露 `reasoning_content`

涉及文件：

- `src/gateway/openai-http.ts`

效果：

- 同步响应里返回 `message.reasoning_content`
- 流式响应里返回 `delta.reasoning_content`

### 2. 把 reasoning stream 从内部 agent command 一路透传到 gateway

涉及文件：

- `src/agents/command/types.ts`
- `src/agents/agent-command.ts`

效果：

- 网关发起的请求现在能订阅并转发内部 reasoning stream

### 3. 增加回归测试

涉及文件：

- `src/gateway/openai-http.test.ts`

效果：

- 覆盖同步 reasoning_content
- 覆盖流式 reasoning_content

## 如何应用

在本地 OpenClaw 仓库里执行：

```bash
git apply /path/to/AlphaDiana-dev/repro/openclaw_reasoning/openclaw-stream-reasoning.patch
```

或者直接运行本目录的 `setup.sh`，脚本会尝试自动应用 patch。

## 为什么需要这份 patch

没有这份 patch 时：

- `ROCK proxy -> sandbox OpenClaw /chat/completions`
- 虽然模型内部可能在推理
- 但外部 SSE 拿不到 `reasoning_content`

应用后：

- 通过 ROCK proxy 的标准 OpenAI 风格 endpoint
- 可以直接看到真实流式 `reasoning_content`
