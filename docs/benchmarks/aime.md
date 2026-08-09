# AIME

AIME runs use `benchmark.name: aime`. The selected dataset and scorer are part
of each config. The release matrix uses AIME 2026 and math-aware scoring. The loader's suggested default scorer is
`numeric`, but the runner requires an explicit `scorer.name`.

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
export HF_HOME=/path/to/writable/hf
export HF_DATASETS_CACHE=$HF_HOME/datasets
export HUGGINGFACE_HUB_CACHE=$HF_HOME/hub
```

The checked-in macro configs all cover AIME 2026; the year is part of the
evaluation contract.

## Supported Modes

| Mode | Status | Config |
|---|---|---|
| `direct_llm` | AIME 2026 macro config | `configs/macro_runs/aime2026_directllm_qwen35_27b.yaml` |
| `openclaw` | AIME 2026 macro config | `configs/macro_runs/aime2026_openclaw_qwen35_27b.yaml` |
| `opencode` | AIME 2026 macro config | `configs/macro_runs/aime2026_opencode_qwen35_27b.yaml` |
| `zeroclaw` | AIME 2026 macro config | `configs/macro_runs/aime2026_zeroclaw_qwen35_27b.yaml` |

The opt-in [Podman runtime runbook](./podman.md) covers the separate
scale-readiness configs for ZeroClaw, OpenCode, and OpenClaw.

## Run a checked-in smoke

Validate the selected year/config pair, then run it:

```bash
python -m alphadiana.cli validate configs/macro_runs/aime2026_zeroclaw_qwen35_27b.yaml
python -m alphadiana.cli run configs/macro_runs/aime2026_zeroclaw_qwen35_27b.yaml --redo-all
```

Start ROCK and export its URLs first as described in the
[ZeroClaw harness guide](../harnesses/zeroclaw.md). The generic ZeroClaw harness
requires a live sandbox/container session.

The macro YAMLs select the full AIME 2026 split. Use
`-o benchmark.config.max_tasks=1` for a first smoke, then remove the override
only after reviewing the model, sample count, output directory, and concurrency.

## Evidence

This release branch intentionally excludes `results/`, `logs/`, and the
reviewer-facing `context/` archive. It therefore does not publish a dated AIME
support or accuracy claim. Treat any run ID mentioned outside this checkout as
a provenance pointer until its task JSON list (`data[0]`), raw log, trajectory,
and provider/logprob artifacts are available together.
