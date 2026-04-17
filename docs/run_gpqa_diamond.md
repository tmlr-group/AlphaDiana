# GPQA-Diamond 运行说明

本文只保留 `GPQA-Diamond` 的运行方法，分别覆盖 `direct_llm` 和 `openclaw` 两种模式。

## 运行前准备

在项目根目录执行：

```bash
source scripts/activate.sh
```

如果使用 `openclaw`，先确认 ROCK 相关服务正常：

```bash
alphadiana env
```

## Direct LLM

使用配置文件：[configs/examples/direct_llm_gpqa_diamond.yaml](/path/to/xxx/menghan/4_17/AlphaDiana-dev/configs/examples/direct_llm_gpqa_diamond.yaml)

先设置 Direct LLM 配置里使用的环境变量：

```bash
export OPENAI_MODEL=<your-model>
export OPENAI_API_BASE=<your-api-base>
export OPENAI_API_KEY=<your-api-key>
```

先校验配置：

```bash
alphadiana validate configs/examples/direct_llm_gpqa_diamond.yaml
```

开始运行：

```bash
alphadiana run configs/examples/direct_llm_gpqa_diamond.yaml
```

如果只想跑一个小子集，先在配置里取消这一行注释：

```yaml
# max_tasks: 100
```

## OpenClaw

使用配置文件：[configs/examples/openclaw_gpqa_diamond.yaml](/path/to/xxx/menghan/4_17/AlphaDiana-dev/configs/examples/openclaw_gpqa_diamond.yaml)

先设置 OpenClaw 自动部署配置里使用的环境变量：

```bash
export OPENAI_BASE_URL=<your-api-base>
export OPENAI_API_KEY=<your-api-key>
export OPENAI_MODEL_NAME=<your-model>
```

先校验配置：

```bash
alphadiana validate configs/examples/openclaw_gpqa_diamond.yaml
```

开始运行：

```bash
alphadiana run configs/examples/openclaw_gpqa_diamond.yaml
```

同样可以在配置中打开：

```yaml
# max_tasks: 100
```

## 结果位置

`direct_llm` 默认输出到 `./results/`。  
`openclaw` 默认输出到 `./results/openclaw_gpqa_diamond/`。
