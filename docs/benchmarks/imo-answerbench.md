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
| `zeroclaw` | supported | `configs/full_runs/p25_full_zeroclaw_minimax_imo_answerbench.yaml` |

The corresponding smoke configs remain under `configs/examples/` and pin `dataset_index: 367`, `max_tasks: 1`.

## Full Runs

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_opencode_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_openclaw_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_zeroclaw_minimax_imo_answerbench.yaml --redo-all
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

## ZeroClaw

ZeroClaw uses the same ROCK auto-deploy path as the PR23 AIME integration.

Unlike the AIME quickstart in the main `README.md`, the formal benchmark smoke here is counted only when the task executes inside a ROCK sandbox. Do not clear `agent.config.rock_image` for the benchmark smoke.

Start ROCK first:

```bash
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh
```

If another branch is already using ROCK, edit `scripts/.rock_ports.env` before startup so this worktree gets isolated admin/proxy/redis/ray ports.

```bash
python -m alphadiana.cli run configs/examples/zeroclaw_imo_answerbench.yaml \
  -o run_id=imo_zeroclaw_smoke
```

### Reproduce The 2026-04-17 Formal Sandbox Smoke

This is the exact smoke style used for local validation of the ZeroClaw sandbox path. It intentionally forces a fast wrong answer so the run terminates quickly with dashboard `X`, which is enough for the execution-path smoke criterion.

```bash
export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax

python -m alphadiana.cli run configs/examples/zeroclaw_imo_answerbench.yaml \
  -o run_id=pr26_formal_smoke_zeroclaw_imo_minimax_rock_cli_box0_20260417_v2 \
  -o agent.config.use_gateway_in_sandbox=false \
  -o benchmark.config.dataset_index=0 \
  -o agent.config.system_prompt='Smoke test mode: ignore the math problem. Do not use tools. Output exactly $$\\boxed{0}$$ and nothing else.'
```

Expected result:

- dashboard: `X`
- task file exists under `results/zeroclaw_imo_answerbench_smoke/<run_id>/tasks/`
- task JSON has no `error`
- the recorded task is `imo_answerbench_0`

Observed local verification on 2026-04-17:

- run_id: `pr26_formal_smoke_zeroclaw_imo_minimax_rock_cli_box0_20260417_v2`
- result: dashboard `X`, `predicted=0`, `ground_truth=3`, no `error`
- execution mode: ROCK sandbox + in-sandbox ZeroClaw CLI

## Smoke Selection

The checked-in minimax smoke configs pin `dataset_index: 367` and `max_tasks: 1` so the run stays deterministic and bounded.

Use the `configs/full_runs/` files for full evaluations.
