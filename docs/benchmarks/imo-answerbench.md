# IMO-AnswerBench

IMO-AnswerBench evaluates mathematical-answer extraction and scoring on `Hwilner/imo-answerbench`.

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

The benchmark loads from HuggingFace. If the default mirror is slow, set `HF_ENDPOINT` before running.

## Supported Modes

| Mode | Status | Config |
|---|---|---|
| `direct_llm` | supported | `configs/full_runs/p25_full_directllm_minimax_imo_answerbench.yaml` |
| `opencode` | supported | `configs/full_runs/p25_full_opencode_minimax_imo_answerbench.yaml` |
| `openclaw` | supported | `configs/full_runs/p25_full_openclaw_minimax_imo_answerbench.yaml` |

The corresponding smoke configs remain under `configs/examples/` and pin `dataset_index: 367`, `max_tasks: 1`.

## Full Runs

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_opencode_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_openclaw_minimax_imo_answerbench.yaml --redo-all
```

## DirectLLM

DirectLLM calls the OpenAI-compatible endpoint directly and scores the returned answer with `math_verify`.

```bash
python -m alphadiana.cli run configs/examples/directllm_minimax_imo_answerbench.yaml \
  -o run_id=imo_directllm_smoke
```

## OpenCode

OpenCode runs the `opencode` CLI and uses the prompt file at `context/opencode_lean_math.md` for the IMO smoke config.

```bash
python -m alphadiana.cli run configs/examples/opencode_minimax_imo_answerbench.yaml \
  -o run_id=imo_opencode_smoke
```

The smoke config uses `timeout: 1800` because shorter bounds can kill valid slow model output before it reaches scoring.

## OpenClaw

OpenClaw uses ROCK auto-deploy and the gateway config in `openclaw_deploy/`.

```bash
python -m alphadiana.cli run configs/examples/openclaw_minimax_imo_answerbench.yaml \
  -o run_id=imo_openclaw_smoke
```

ROCK services must be healthy before this run. `scripts/activate.sh` loads the local ROCK port configuration.

## Smoke Selection

The checked-in minimax smoke configs pin `dataset_index: 367` and `max_tasks: 1` so the run stays deterministic and bounded.

Use the `configs/full_runs/` files for full evaluations.
