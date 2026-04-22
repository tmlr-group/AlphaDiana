# GPQA-Diamond

GPQA-Diamond evaluates expert-level science multiple-choice questions from
`fingertap/GPQA-Diamond`.

2026-04-18/19 增加了三份 OpenRouter/Qwen 三题 pilot 配置：

- `configs/examples/directllm_qwen35_27b_gpqa_diamond_pilot.yaml`
- `configs/examples/openclaw_qwen35_27b_gpqa_diamond_pilot.yaml`
- `configs/examples/opencode_qwen35_27b_gpqa_diamond_pilot.yaml`

这两份 pilot 配置统一使用 `OPENAI_BASE_URL`、`OPENAI_MODEL_NAME`、
`OPENAI_API_KEY`，其中 OpenRouter 对应的实际模型 slug 为
`qwen/qwen3.5-27b`，逻辑目标模型是 `Qwen/Qwen3.5-27B`。

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

When running from a local checkout, prefer the module entrypoint:

```bash
python -m alphadiana.cli env
```

`HF_TOKEN` is optional for this dataset, but setting it avoids anonymous Hub
rate limits.

## Supported Modes

| Mode | Status | Smoke / Debug Config | Full-run Config |
|---|---|---|---|
| `direct_llm` | smoke/debug supported | `configs/examples/direct_llm_gpqa_diamond.yaml` | `configs/full_runs/rollout_full_directllm_gpqa_diamond.yaml` |
| `openclaw` | smoke/debug supported | `configs/examples/openclaw_gpqa_diamond.yaml` | `configs/full_runs/rollout_full_openclaw_gpqa_diamond.yaml` |
| `opencode` | smoke/debug supported | `configs/examples/opencode_gpqa_diamond.yaml` | `configs/full_runs/rollout_full_opencode_gpqa_diamond.yaml` |
| `zeroclaw` | smoke/debug supported | `configs/examples/zeroclaw_gpqa_diamond.yaml` | `configs/full_runs/rollout_full_zeroclaw_gpqa_diamond.yaml` |

For the staged `72`-run local-vLLM campaign, use
[full-rollout-local-vllm-20260419.md](full-rollout-local-vllm-20260419.md)
instead of launching the four full configs manually.

## Full Run

Checked-in full configs now exist for all four harnesses and target the full
`test` split.

Validate them directly:

```bash
python -m alphadiana.cli validate configs/full_runs/rollout_full_directllm_gpqa_diamond.yaml
python -m alphadiana.cli validate configs/full_runs/rollout_full_openclaw_gpqa_diamond.yaml
python -m alphadiana.cli validate configs/full_runs/rollout_full_opencode_gpqa_diamond.yaml
python -m alphadiana.cli validate configs/full_runs/rollout_full_zeroclaw_gpqa_diamond.yaml
```

## DirectLLM

Config:
[configs/examples/direct_llm_gpqa_diamond.yaml](../../configs/examples/direct_llm_gpqa_diamond.yaml)

```bash
python -m alphadiana.cli validate configs/examples/direct_llm_gpqa_diamond.yaml
python -m alphadiana.cli run configs/examples/direct_llm_gpqa_diamond.yaml
```

## OpenClaw

Config:
[configs/examples/openclaw_gpqa_diamond.yaml](../../configs/examples/openclaw_gpqa_diamond.yaml)

Sequential `openclaw` benchmark runs now force a fresh ROCK sandbox session per
task so gateway/session state cannot leak across questions. Current main also
skips the OpenClaw chat-completions warmup by default on benchmark runs because
that warmup could contaminate the first question's default session.

```bash
python -m alphadiana.cli validate configs/examples/openclaw_gpqa_diamond.yaml
python -m alphadiana.cli run configs/examples/openclaw_gpqa_diamond.yaml
```

## OpenCode

Config:
[configs/examples/opencode_gpqa_diamond.yaml](../../configs/examples/opencode_gpqa_diamond.yaml)

```bash
python -m alphadiana.cli validate configs/examples/opencode_gpqa_diamond.yaml
python -m alphadiana.cli run configs/examples/opencode_gpqa_diamond.yaml
```

The checked-in OpenCode benchmark config now uses Docker controller isolation by
default. Build `alphadiana/tb2-opencode-controller:latest` first if it is not
already present. If you need the old host-process path for debugging, override
`-o agent.config.controller_mode=host`.

## ZeroClaw

Config:
[configs/examples/zeroclaw_gpqa_diamond.yaml](../../configs/examples/zeroclaw_gpqa_diamond.yaml)

ZeroClaw benchmark smoke is documented only for sandboxed execution:

- ROCK sandbox
- in-sandbox ZeroClaw CLI
- `max_tasks: 1`

Start ROCK first:

```bash
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh
```

Then validate and run:

```bash
python -m alphadiana.cli validate configs/examples/zeroclaw_gpqa_diamond.yaml
python -m alphadiana.cli run configs/examples/zeroclaw_gpqa_diamond.yaml \
  -o run_id=gpqa_zeroclaw_smoke
```

### Reproduce The 2026-04-18 Sandbox Smoke

This smoke run intentionally returns a fixed option letter so the benchmark path
finishes quickly. Under the smoke playbook, dashboard `X` is still a pass for
the execution path.

```bash
export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5

python -m alphadiana.cli run configs/examples/zeroclaw_gpqa_diamond.yaml \
  -o run_id=pr23_smoke_zeroclaw_gpqa_minimaxm25_boxA_20260418 \
  -o output_dir=./results/pr23_zeroclaw_smokes \
  -o agent.config.system_prompt='Smoke test mode: ignore the question. Do not use tools. Output exactly $$\\boxed{A}$$ and nothing else.'
```

Observed local verification on 2026-04-18:

- run_id: `pr23_smoke_zeroclaw_gpqa_minimaxm25_boxA_20260418`
- result: dashboard `X`, `predicted=A`, `ground_truth=D`, no `error`
- execution mode: ROCK sandbox + in-sandbox ZeroClaw CLI

## Result Locations

- `direct_llm`: `./results/`
- `openclaw`: `./results/openclaw_gpqa_diamond/`
- `opencode`: `./results/opencode_gpqa_diamond/`
- `zeroclaw`: `./results/zeroclaw_gpqa_diamond_smoke/`

## Qwen/OpenRouter 3-Task Pilot

环境变量：

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD
export HF_ENDPOINT=https://hf-mirror.com
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b
export OPENAI_API_KEY=sk-...
```

运行命令：

```bash
python -m alphadiana.cli run configs/examples/directllm_qwen35_27b_gpqa_diamond_pilot.yaml
python -m alphadiana.cli run configs/examples/openclaw_qwen35_27b_gpqa_diamond_pilot.yaml
python -m alphadiana.cli run configs/examples/opencode_qwen35_27b_gpqa_diamond_pilot.yaml
```

2026-04-18/19/20 本地真实 pilot 结果：

- `direct_llm`: `3/3` task records，全部 `score=1`
- `openclaw`: `3/3` task records，全部 `score=1`
- `opencode`: `3/3` task records，全部 `score=1`
  - April 19 uploaded quality pilot:
    `pilot_20260419_qwen35_27b_gpqa_diamond_opencode_t3`
  - April 20 default-Docker confirmation rerun:
    `pilot_20260420_qwen35_27b_gpqa_diamond_opencode_t3_docker_default`
  - April 20 rerun中三条 task JSON 都记录了
    `metadata.controller_mode=docker` 和
    `metadata.transport=opencode_cli_container`

Reviewer-facing evidence:

- `context/qwen-openrouter-pilots/pilot-validation.md`
- `context/qwen-openrouter-pilots/status-matrix.md`
