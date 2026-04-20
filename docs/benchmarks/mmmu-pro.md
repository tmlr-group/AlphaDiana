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

| Mode | Status | Smoke / Debug Config | Full-run Config |
|---|---|---|---|
| `direct_llm` | smoke/debug supported | `configs/examples/direct_llm_mmmu_pro.yaml` | `configs/full_runs/rollout_full_directllm_mmmu_pro_vision.yaml` |
| `openclaw` | smoke/debug supported | `configs/examples/openclaw_mmmu_pro.yaml` | `configs/full_runs/rollout_full_openclaw_mmmu_pro_vision.yaml` |
| `opencode` | smoke/debug supported | `configs/examples/opencode_mmmu_pro.yaml` | `configs/full_runs/rollout_full_opencode_mmmu_pro_vision.yaml` |
| `zeroclaw` | smoke/debug supported | `configs/examples/zeroclaw_mmmu_pro.yaml` | `configs/full_runs/rollout_full_zeroclaw_mmmu_pro_vision.yaml` |

For the staged `72`-run local-vLLM campaign, use
[full-rollout-local-vllm-20260419.md](full-rollout-local-vllm-20260419.md)
instead of launching the four full configs manually.

## Data Configs

The benchmark supports:

```text
standard (4 options)
standard (10 options)
vision
```

The ZeroClaw smoke config uses `vision` so the benchmark path exercises image
attachment handling inside the sandbox workspace.

The checked-in full configs also use `data_config: "vision"` so the full run
matches the image-backed path rather than a text-only variant.

## Full Run

Validate the four full configs directly:

```bash
python -m alphadiana.cli validate configs/full_runs/rollout_full_directllm_mmmu_pro_vision.yaml
python -m alphadiana.cli validate configs/full_runs/rollout_full_openclaw_mmmu_pro_vision.yaml
python -m alphadiana.cli validate configs/full_runs/rollout_full_opencode_mmmu_pro_vision.yaml
python -m alphadiana.cli validate configs/full_runs/rollout_full_zeroclaw_mmmu_pro_vision.yaml
```

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

Current limitation: on `main`, `opencode` text-only benchmark tasks still run
through the local CLI path rather than a benchmark-managed sandbox. That is
fine for smoke/debug usage, but it is not equivalent to the OpenClaw or
ZeroClaw sandbox path.

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

## Result Locations

- `direct_llm`: `./results/`
- `openclaw`: `./results/openclaw_mmmu_pro/`
- `opencode`: `./results/opencode_mmmu_pro/`
- `zeroclaw`: `./results/zeroclaw_mmmu_pro_smoke/`
