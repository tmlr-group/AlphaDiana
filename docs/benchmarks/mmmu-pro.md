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

The checked-in full configs also use `data_config: "vision"` so the full run
matches the image-backed path rather than a text-only variant.

When using a local Hugging Face snapshot, verify that the selected subset
directory contains actual data files before launching an agent run. A snapshot
that only has `README.md`, `.gitattributes`, and empty
`vision` / `standard (4 options)` / `standard (10 options)` directories fails
at dataset load time with `DataFilesNotFoundError` and is not runnable.

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

The checked-in OpenCode benchmark config now uses Docker controller isolation by
default. Build `alphadiana/tb2-opencode-controller:latest` first if it is not
already present. If you need the old host-process path for debugging, override
`-o agent.config.controller_mode=host`.

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

On the OpenAI-compatible `qwen3vl` endpoint, multimodal ZeroClaw currently works most
reliably through the same single sandbox CLI path now used for HLE on current
main. No extra override is required for the intended path: when a live ROCK
sandbox is present and the task carries image attachments, AlphaDiana uploads
them into the sandbox workspace, appends `[IMAGE:<absolute sandbox path>]`
markers to the prompt, and runs the stock `zeroclaw agent` CLI there. The
transport marker is `metadata.transport=zeroclaw_cli_sandbox`.

When the path still fails, current main preserves explicit failure metadata
such as `metadata.failure_reason=empty_response` or `provider_error` so the
task JSON remains diagnosable.

## Result Locations

- `direct_llm`: `./results/`
- `openclaw`: `./results/openclaw_mmmu_pro/`
- `opencode`: `./results/opencode_mmmu_pro/`
- `zeroclaw`: `./results/zeroclaw_mmmu_pro_smoke/`
