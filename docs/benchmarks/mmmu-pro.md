# MMMU-Pro

本文档说明如何在 `MMMU-Pro` 上运行 `direct_llm`、`openclaw` 和
`opencode` 三种模式。

## 运行前准备

在项目根目录执行：

```bash
source scripts/activate.sh
```

从当前 checkout 运行时，优先使用模块入口：

```bash
python -m alphadiana.cli env
```

如果使用 `openclaw`，先确认上面的环境检查里 admin/proxy/redis 都是可用的。

## Direct LLM

配置文件：[configs/examples/direct_llm_mmmu_pro.yaml](../../configs/examples/direct_llm_mmmu_pro.yaml)

先设置环境变量：

```bash
export OPENAI_MODEL=minimax-m2.5
export OPENAI_API_BASE=https://api.example.com/v1/
export OPENAI_API_KEY=...
```

校验并运行：

```bash
python -m alphadiana.cli validate configs/examples/direct_llm_mmmu_pro.yaml
python -m alphadiana.cli run configs/examples/direct_llm_mmmu_pro.yaml
```

如果要切换为纯文本或视觉版本，修改：

```yaml
data_config: "standard (4 options)"
```

可选值：

```text
standard (4 options)
standard (10 options)
vision
```

## OpenClaw

配置文件：[configs/examples/openclaw_mmmu_pro.yaml](../../configs/examples/openclaw_mmmu_pro.yaml)

先设置环境变量：

```bash
export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=...
export OPENAI_MODEL_NAME=minimax-m2.5
```

校验并运行：

```bash
python -m alphadiana.cli validate configs/examples/openclaw_mmmu_pro.yaml
python -m alphadiana.cli run configs/examples/openclaw_mmmu_pro.yaml
```

## OpenCode

配置文件：[configs/examples/opencode_mmmu_pro.yaml](../../configs/examples/opencode_mmmu_pro.yaml)

先设置环境变量：

```bash
export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=...
```

校验并运行：

```bash
python -m alphadiana.cli validate configs/examples/opencode_mmmu_pro.yaml
python -m alphadiana.cli run configs/examples/opencode_mmmu_pro.yaml
```

说明：当前 `main` 上，`opencode` 的文本题路径是本地 CLI 执行路径，不会像
`openclaw` 一样进入 benchmark sandbox。这条链路仍然可以做 smoke/debug，
但它验证的是 `opencode` 求解路径，不是 sandbox 隔离。

## 结果位置

- `direct_llm`: `./results/`
- `openclaw`: `./results/openclaw_mmmu_pro/`
- `opencode`: `./results/opencode_mmmu_pro/`
