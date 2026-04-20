# MMMU-Pro

MMMU-Pro evaluates multimodal multiple-choice reasoning on `MMMU/MMMU_Pro`.

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

## Supported Modes

| Mode | Status | Config |
|---|---|---|
| `direct_llm` | smoke/debug supported | `configs/examples/direct_llm_mmmu_pro.yaml` |
| `openclaw` | smoke/debug supported | `configs/examples/openclaw_mmmu_pro.yaml` |
| `opencode` | smoke/debug supported | `configs/examples/opencode_mmmu_pro.yaml` |
| `zeroclaw` | smoke/debug supported | `configs/examples/zeroclaw_mmmu_pro.yaml` |

There is no checked-in ZeroClaw full-run config for MMMU-Pro yet. The current
documented path is the example smoke/debug config.

## Data Configs

The benchmark supports:

```text
standard (4 options)
standard (10 options)
vision
```

The ZeroClaw smoke config uses `vision` so the benchmark path exercises image
attachment handling inside the sandbox workspace.

Current dataset note: the Hugging Face `vision` subset now exposes a singular
`image` field. AlphaDiana normalizes that payload into `image_1` task
attachments before handing the task to the agent.

## DirectLLM

Config:
[configs/examples/direct_llm_mmmu_pro.yaml](../../configs/examples/direct_llm_mmmu_pro.yaml)

```bash
python -m alphadiana.cli validate configs/examples/direct_llm_mmmu_pro.yaml
python -m alphadiana.cli run configs/examples/direct_llm_mmmu_pro.yaml
```

## OpenClaw

Config:
[configs/examples/openclaw_mmmu_pro.yaml](../../configs/examples/openclaw_mmmu_pro.yaml)

```bash
python -m alphadiana.cli validate configs/examples/openclaw_mmmu_pro.yaml
python -m alphadiana.cli run configs/examples/openclaw_mmmu_pro.yaml
```

## OpenCode

Config:
[configs/examples/opencode_mmmu_pro.yaml](../../configs/examples/opencode_mmmu_pro.yaml)

```bash
python -m alphadiana.cli validate configs/examples/opencode_mmmu_pro.yaml
python -m alphadiana.cli run configs/examples/opencode_mmmu_pro.yaml
```

The checked-in OpenCode benchmark config now uses Docker controller isolation by
default. Build `alphadiana/tb2-opencode-controller:latest` first if it is not
already present. If you need the old host-process path for debugging, override
`-o agent.config.controller_mode=host`.

### Qwen/OpenRouter Vision Pilot (2026-04-19/20)

Accepted local pilot:

- run_id: `pilot_20260419_qwen35_27b_mmmu_pro_opencode_t3_vision_docker`
- result: `3/3` normal trajectories in Docker isolation, one task scored `0`
  but no abnormal behavior
- run_id:
  `pilot_20260420_qwen35_27b_mmmu_pro_opencode_t3_vision_docker_default`
- result: `3/3` normal Docker-default trajectories with scores `0/1/1`, and
  every task recorded `num_attachments=1`

Historical non-canonical run:

- `pilot_20260419_qwen35_27b_mmmu_pro_opencode_t3_vision`
  wrote `3/3` normal task records, but it used `controller_mode=host` before
  the checked-in config default switched to Docker isolation, so it is kept as
  historical evidence only

Command:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b

python -m alphadiana.cli run configs/examples/opencode_mmmu_pro.yaml \
  -o run_id=pilot_20260420_qwen35_27b_mmmu_pro_opencode_t3_vision_docker_default \
  -o benchmark.config.data_config=vision \
  -o benchmark.config.max_tasks=3 \
  -o max_concurrent=3 \
  -o agent.config.model_name=qwen/qwen3.5-27b \
  -o agent.config.model=custom/qwen/qwen3.5-27b
```

## ZeroClaw

Config:
[configs/examples/zeroclaw_mmmu_pro.yaml](../../configs/examples/zeroclaw_mmmu_pro.yaml)

ZeroClaw benchmark smoke is documented only for sandboxed execution:

- ROCK sandbox
- in-sandbox ZeroClaw CLI
- `data_config: "vision"`
- `max_tasks: 1`

Start ROCK first:

```bash
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh
```

Then validate and run:

```bash
python -m alphadiana.cli validate configs/examples/zeroclaw_mmmu_pro.yaml
python -m alphadiana.cli run configs/examples/zeroclaw_mmmu_pro.yaml \
  -o run_id=mmmu_pro_zeroclaw_smoke
```

### Reproduce The 2026-04-18 Sandbox Smoke

This smoke run intentionally returns a fixed option letter so the benchmark path
finishes quickly while still proving that attachment upload, prompt plumbing,
and scoring all complete.

```bash
export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5

python -m alphadiana.cli run configs/examples/zeroclaw_mmmu_pro.yaml \
  -o run_id=pr23_smoke_zeroclaw_mmmupro_minimaxm25_boxA_20260418 \
  -o output_dir=./results/pr23_zeroclaw_smokes \
  -o agent.config.use_gateway_in_sandbox=false \
  -o agent.config.system_prompt='Smoke test mode: ignore the question and attachments. Do not use tools. Output exactly $$\\boxed{A}$$ and nothing else.'
```

Observed local verification on 2026-04-18:

- run_id: `pr23_smoke_zeroclaw_mmmupro_minimaxm25_boxA_20260418`
- result: dashboard `X`, `predicted=A`, `ground_truth=B`, no `error`
- execution mode: ROCK sandbox + in-sandbox ZeroClaw CLI

### Qwen/OpenRouter Vision Pilot (2026-04-19)

Accepted local pilot:

- run_id: `pilot_20260419_qwen35_27b_mmmu_pro_zeroclaw_t3_vision_r3`
- result: `3/3` normal trajectories with `use_gateway_in_sandbox=false`,
  preserved `attachments/image_1.png`, no provider warning, and no
  `command_history` pollution in successful task metadata

Rejected earlier attempts from the same day:

- `pilot_20260419_qwen35_27b_mmmu_pro_zeroclaw_t3_vision`
  one task failed after a ROCK proxy `http proxy failed` fallback hit a binary
  upload decode bug
- `pilot_20260419_qwen35_27b_mmmu_pro_zeroclaw_t3_vision_r1`
  the binary upload decode bug was fixed, but the first fallback implementation
  still hit `/bin/sh` argument-length limits on a large image attachment
- `pilot_20260419_qwen35_27b_mmmu_pro_zeroclaw_t3_vision_r2`
  wrote `3/3` normal task records, but it still emitted a ZeroClaw config
  warning and preserved noisy sandbox `command_history`, so it is retained as
  historical evidence only

Current recommended OpenRouter/Qwen command:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b

python -m alphadiana.cli run configs/examples/zeroclaw_mmmu_pro.yaml \
  -o run_id=pilot_20260419_qwen35_27b_mmmu_pro_zeroclaw_t3_vision_r3 \
  -o benchmark.config.max_tasks=3 \
  -o max_concurrent=1 \
  -o agent.config.use_gateway_in_sandbox=false
```

## Result Locations

- `direct_llm`: `./results/`
- `openclaw`: `./results/openclaw_mmmu_pro/`
- `opencode`: `./results/opencode_mmmu_pro/`
- `zeroclaw`: `./results/zeroclaw_mmmu_pro_smoke/`
