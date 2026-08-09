# AIME

AIME runs use `benchmark.name: aime`. The selected dataset and scorer are part
of each config: checked-in examples include both AIME 2024 and AIME 2026, and
use numeric or math-aware scoring. The loader's suggested default scorer is
`numeric`, but the runner requires an explicit `scorer.name`.

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-4B
export HF_HOME=/path/to/writable/hf
export HF_DATASETS_CACHE=$HF_HOME/datasets
export HUGGINGFACE_HUB_CACHE=$HF_HOME/hub
```

The checked-in examples cover AIME 2024 and 2026. Check each config's
dataset before running it; the year is part of the evaluation contract.

## Supported Modes

| Mode | Status | Config |
|---|---|---|
| `direct_llm` | AIME 2024 example | `configs/examples/direct_llm.yaml` |
| `openclaw` | AIME 2026 macro config | `configs/macro_runs/aime2026_openclaw_qwen35_27b.yaml` |
| `opencode` | AIME 2024 Podman smoke | `configs/examples/opencode_aime_podman_smoke.yaml` |
| `zeroclaw` | AIME 2026 ROCK example | `configs/examples/zeroclaw_aime2026.yaml` |

The opt-in [Podman runtime runbook](./podman) covers the separate
scale-readiness configs for ZeroClaw, OpenCode, and OpenClaw.

## Run a checked-in smoke

Validate the selected year/config pair, then run it:

```bash
python -m alphadiana.cli validate configs/examples/zeroclaw_aime2026.yaml
python -m alphadiana.cli run configs/examples/zeroclaw_aime2026.yaml --redo-all
```

Start ROCK and export its URLs first as described in the
[ZeroClaw harness guide](../harnesses/zeroclaw). The generic ZeroClaw harness
requires a live sandbox/container session.

This checkout does not ship an AIME full-run YAML. For a full evaluation,
create and review a dedicated config with the intended dataset year, task
selection, sample count, model/reasoning contract, output directory, and
concurrency. Validate that config before launching it.

## Evidence

This website branch intentionally excludes `results/`, `logs/`, and the
reviewer-facing `context/` archive. It therefore does not publish a dated AIME
support or accuracy claim. Treat any run ID mentioned outside this checkout as
a provenance pointer until its task JSON list (`data[0]`), raw log, trajectory,
and provider/logprob artifacts are available together.
