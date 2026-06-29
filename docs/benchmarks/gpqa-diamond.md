# GPQA-Diamond

GPQA-Diamond evaluates expert-level science multiple-choice questions from
`fingertap/GPQA-Diamond`.

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

For local-vLLM `Qwen/Qwen3.5-27B` logprob smoke checks, the checked-in
Phase 11 configs cover OpenCode, OpenClaw, and ZeroClaw:

```bash
python -m alphadiana.cli run configs/full_runs/phase11_opencode_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/phase11_openclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/phase11_zeroclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml --redo-all
```

OpenClaw and ZeroClaw use a host-side provider proxy for logprob capture on the
ROCK path. The proxy injects the logprob request fields and uses a random
per-proxy bearer token for sandbox access.

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

## Result Locations

- `direct_llm`: `./results/`
- `openclaw`: `./results/openclaw_gpqa_diamond/`
- `opencode`: `./results/opencode_gpqa_diamond/`
- `zeroclaw`: `./results/zeroclaw_gpqa_diamond_smoke/`
