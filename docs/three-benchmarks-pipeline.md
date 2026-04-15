# Three Benchmarks 3x3 Smoke Matrix

Last updated: 2026-04-16
Worktree: `/tmp/AlphaDiana-dev-pr25`

This document is the current operator-facing record for PR25 smoke validation across the full 3x3 matrix:

- 3 modes: `openclaw`, `direct_llm`, `opencode`
- 3 benchmarks: `imo_answerbench`, `hle`, `terminal_bench2`

The goal here is execution evidence, not score quality. Every cell below completed a 1-task smoke run end-to-end and reached scoring.

## Recommended Environment

Run from the repo root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

terminal-bench-2 smoke is intentionally staged to a single known task:

```bash
git clone --depth=1 https://github.com/harbor-framework/terminal-bench-2.git /tmp/terminal-bench-2
rm -rf /tmp/terminal-bench-2-smoke-dbwal
mkdir -p /tmp/terminal-bench-2-smoke-dbwal
cp -a /tmp/terminal-bench-2/db-wal-recovery /tmp/terminal-bench-2-smoke-dbwal/
docker pull alexgshaw/db-wal-recovery:20251031
export TERMINAL_BENCH2_DIR=/tmp/terminal-bench-2-smoke-dbwal
```

HLE note:

- If `cais/hle` is already cached locally, the benchmark now loads without forcing `HF_TOKEN`.
- On a fresh machine, export `HF_TOKEN` before running HLE.

## Proven Matrix

| Mode | Benchmark | Config | Run ID | Result | Notes |
|---|---|---|---|---|---|
| `direct_llm` | `imo_answerbench` | `configs/examples/directllm_minimax_imo_answerbench.yaml` | `directllm_minimax_imo_smoke_20260415_d` | completed `1/1` | bounded smoke via `dataset_index=367`, `max_tokens=512` |
| `direct_llm` | `hle` | `configs/examples/directllm_minimax_hle.yaml` | `directllm_minimax_hle_smoke_20260415` | completed `1/1` | uses cached HLE row `dataset_index=1` |
| `direct_llm` | `terminal_bench2` | `configs/examples/terminal_bench2_directllm_minimax.yaml` | `terminal_bench2_directllm_minimax_smoke_20260415` | completed `1/1` | `db-wal-recovery`, reward `0` |
| `opencode` | `imo_answerbench` | `configs/examples/opencode_minimax_imo_answerbench.yaml` | `opencode_minimax_imo_smoke_20260415` | completed `1/1` | bounded by `timeout=120`, reward path reaches scorer |
| `opencode` | `hle` | `configs/examples/opencode_minimax_hle.yaml` | `opencode_minimax_hle_smoke_20260415_c` | completed `1/1` | bounded by `timeout=120`, cached HLE row |
| `opencode` | `terminal_bench2` | `configs/examples/terminal_bench2_opencode_minimax.yaml` | `terminal_bench2_opencode_minimax_smoke_20260415` | completed `1/1` | `db-wal-recovery`, `solver_timeout_sec=120`, reward `0` |
| `openclaw` | `imo_answerbench` | `configs/examples/openclaw_minimax_imo_answerbench.yaml` | `openclaw_minimax_imo_smoke_20260415` | completed `1/1` | bounded smoke via `dataset_index=367`, `max_tokens=512` |
| `openclaw` | `hle` | `configs/examples/openclaw_minimax_hle.yaml` | `openclaw_minimax_hle_smoke_20260415` | completed `1/1` | cached HLE row, full ROCK auto-deploy path |
| `openclaw` | `terminal_bench2` | `configs/examples/terminal_bench2_openclaw_minimax.yaml` | `terminal_bench2_openclaw_minimax_smoke_20260415_b` | completed `1/1` | `db-wal-recovery`, planner timed out once, verifier still ran and scored `0` |

All nine cells above reached result writing. None of the smoke scores are being treated as quality claims.

## Smoke Config Conventions

These configs are intentionally bounded for reproducibility:

- IMO smoke configs pin `dataset_index=367`, a short number-theory problem.
- HLE smoke configs pin `dataset_index=1`, which is locally cached and already proven to load.
- OpenCode smoke configs use `timeout: 120` so the run completes even when the CLI does not converge.
- terminal-bench-2 DirectLLM smoke uses `max_rounds: 6`.
- terminal-bench-2 OpenCode smoke uses `solver_timeout_sec: 120`.
- terminal-bench-2 OpenClaw smoke uses `request_timeout: 60`, `max_attempts: 1`, and `continue_on_planner_error: true`.

The last setting is deliberate. For terminal-bench-2 smoke, if the OpenClaw planner does not answer within one bounded request window, the host-side relay now breaks out, runs the verifier, and records the attempt as reward `0` instead of leaving the whole run hung indefinitely.

## Merge-Readiness Notes

The branch now includes the fixes that previously blocked PR25 review:

- `scripts/full_pipeline.py` is restored, so `tests/test_external_benchmark_pipeline.py` collects and passes again.
- `python -m alphadiana.cli list-benchmarks` now includes `external_benchmark`.
- terminal-bench-2 now imports `tomllib` on Python 3.11 and falls back to `tomli` only when needed.
- `scripts/setup_alphadiana_rock.sh` installs `.[all,benchmarks,dev]`, so benchmark deps are present in the default setup path.
- `scripts/activate.sh` and `alphadiana.utils.rock_ports` now prefer explicit shell env over stale `.rock_ports.env` file values.
- `imo_answerbench` category filtering now uses the real dataset field `Category`.
- `imo_answerbench` and `hle` now support `dataset_index` for deterministic smoke selection.
- terminal-bench-2 logs are isolated by `task_id`, `sample_index`, and `execution_id`.
- New relay agents exist for `terminal_bench2_openclaw` and `terminal_bench2_opencode`.

## Commands Used For The Matrix

Representative commands:

```bash
python -m alphadiana.cli run configs/examples/directllm_minimax_imo_answerbench.yaml \
  -o run_id=directllm_minimax_imo_smoke_20260415_d

python -m alphadiana.cli run configs/examples/opencode_minimax_hle.yaml \
  -o run_id=opencode_minimax_hle_smoke_20260415_c

python -m alphadiana.cli run configs/examples/openclaw_minimax_hle.yaml \
  -o run_id=openclaw_minimax_hle_smoke_20260415

python -m alphadiana.cli run configs/examples/terminal_bench2_openclaw_minimax.yaml \
  -o run_id=terminal_bench2_openclaw_minimax_smoke_20260415_b
```

Use the config defaults as checked in unless there is a deliberate review reason to change the smoke bounds.
