# AIME

AIME benchmark runs use `benchmark.name: aime` with numeric scoring on
`MathArena/aime_2026`.

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export QWEN_VLLM_API_BASE=http://127.0.0.1:8011/v1
export QWEN_VLLM_API_KEY=EMPTY
export HF_HOME=/path/to/xxx/hf
export HF_DATASETS_CACHE=/path/to/xxx/hf/datasets
export HUGGINGFACE_HUB_CACHE=/path/to/xxx/hf/hub
```

The checked-in examples cover AIME 2024, 2025, and 2026. Check each config's
dataset before running it; the year is part of the evaluation contract.

## Supported Modes

| Mode | Status | Config |
|---|---|---|
| `direct_llm` | AIME 2024 example | `configs/examples/direct_llm.yaml` |
| `openclaw` | AIME 2024/2025 examples | `configs/examples/openclaw_aime2024.yaml`, `configs/examples/openclaw_aime2025_glm5.yaml` |
| `opencode` | AIME 2024 Podman smoke | `configs/examples/opencode_aime_podman_smoke.yaml` |
| `zeroclaw` | AIME 2026 ROCK example | `configs/examples/zeroclaw_aime2026.yaml` |

## Run a checked-in smoke

Validate the selected year/config pair, then run it:

```bash
python -m alphadiana.cli validate configs/examples/zeroclaw_aime2026.yaml
python -m alphadiana.cli run configs/examples/zeroclaw_aime2026.yaml --redo-all
```

Start ROCK and export its URLs first as described in the
[ZeroClaw harness guide](../harnesses/zeroclaw). The checked-in
`zeroclaw_aime2026_local_smoke.yaml` is a legacy validation fixture, not a
runnable host-mode path: current generic ZeroClaw raises when no live sandbox
session is provided.

This checkout does not ship an AIME full-run YAML. For a full evaluation,
create and review a dedicated config with the intended dataset year, task
selection, sample count, model/reasoning contract, output directory, and
concurrency. Validate that config before launching it.

## Evidence

- Smoke proof:
  `pilot_20260423_qwen35_27b_aime2026_directllm_t1_r1`
  completed `1` task with `2` samples and preserved:
  `tasks/aime_1.json`,
  `logprobs/aime_1.jsonl`,
  `logprobs/aime_1/sample_1.jsonl`.
- Active full run:
  `full_20260423_qwen35_27b_aime2026_directllm_r1`
  is the matching `30 x 32` local-vLLM run launched from the checked-in config.
