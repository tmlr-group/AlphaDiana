# HLE

HLE evaluates multiple-choice Humanity's Last Exam tasks from `cais/hle`.

Related references:

- current status summary: `context/current_eval_status.md`
- historical multimodal validation note:
  `context/P25-three-benchmarks/openclaw_opencode_hle_multimodal_validation_20260417.md`
- reviewer-facing debug trail:
  `context/P25-three-benchmarks/openclaw-hle-multimodal-fix-20260417.md`

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

`cais/hle` is gated on HuggingFace. On a fresh machine, export `HF_TOKEN` before running:

```bash
export HF_TOKEN=hf_...
```

If the dataset is already cached locally, the loader can run without forcing `HF_TOKEN`.

## Supported Modes

| Mode | Status | Config |
|---|---|---|
| `direct_llm` | supported | `configs/full_runs/p25_full_directllm_minimax_hle.yaml` |
| `opencode` | supported | `configs/full_runs/p25_full_opencode_minimax_hle.yaml` |
| `openclaw` | supported | `configs/full_runs/p25_full_openclaw_minimax_hle.yaml` |

The full configs run the supported HLE `multipleChoice` subset. Other HLE answer types are not included in the current exact-match scoring path.

The corresponding smoke configs remain under `configs/examples/` and pin `dataset_index: 1`, `max_tasks: 1`.

## Full Runs

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_hle.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_opencode_minimax_hle.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_openclaw_minimax_hle.yaml --redo-all
```

## DirectLLM

```bash
python -m alphadiana.cli run configs/examples/directllm_minimax_hle.yaml \
  -o run_id=hle_directllm_smoke
```

## OpenCode

```bash
python -m alphadiana.cli run configs/examples/opencode_minimax_hle.yaml \
  -o run_id=hle_opencode_smoke
```

The smoke config uses `timeout: 1800` to allow visible model output before scoring.

## OpenClaw

```bash
python -m alphadiana.cli run configs/examples/openclaw_minimax_hle.yaml \
  -o run_id=hle_openclaw_smoke
```

OpenClaw HLE responses can take several minutes after the gateway returns HTTP 200. Wait for artifact collection and result writing before classifying the run as stuck.

## Smoke Selection

The checked-in minimax smoke configs pin:

- `dataset_index: 1`
- `answer_types: ["multipleChoice"]`
- `max_tasks: 1`

The scorer is `exact_match`, so the final answer should be one of the multiple-choice options.

Use the `configs/full_runs/` files for full supported HLE multiple-choice evaluations.
