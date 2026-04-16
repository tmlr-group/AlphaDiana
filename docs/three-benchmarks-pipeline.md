# Three Benchmarks 3x3 Smoke Matrix

Last updated: 2026-04-16
Worktree: `/tmp/AlphaDiana-dev-pr25`

This document is the current operator-facing record for PR25 smoke validation across the full 3x3 matrix:

- 3 modes: `openclaw`, `direct_llm`, `opencode`
- 3 benchmarks: `imo_answerbench`, `hle`, `terminal_bench2`

The goal here is execution evidence, not score quality. A cell counts as passing only if the model produces visible assistant output and the runner writes a scored result. Timeout-only fallback results are not counted as passing.

The latest full rerun was completed on 2026-04-16 using:

- API base: `https://api.example.com/v1/`
- Model: `minimax-m2.5`
- API key: supplied in the operator shell only; do not persist it in repo files.

## Recommended Environment

Run from the repo root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
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
| `direct_llm` | `imo_answerbench` | `configs/examples/directllm_minimax_imo_answerbench.yaml` | `pr25_live2_20260416_directllm_imo` | completed `1/1` | bounded smoke via `dataset_index=367`, `max_tokens=512` |
| `direct_llm` | `hle` | `configs/examples/directllm_minimax_hle.yaml` | `pr25_live2_20260416_directllm_hle` | completed `1/1` | uses cached HLE row `dataset_index=1` |
| `direct_llm` | `terminal_bench2` | `configs/examples/terminal_bench2_directllm_minimax.yaml` | `pr25_live2_20260416_directllm_tb2` | completed `1/1` | `db-wal-recovery`, verifier reward `0` |
| `opencode` | `imo_answerbench` | `configs/examples/opencode_minimax_imo_answerbench.yaml` | `pr25_live3_20260416_opencode_imo` | completed `1/1` | `timeout=1800`; visible model output; score `1.0` |
| `opencode` | `hle` | `configs/examples/opencode_minimax_hle.yaml` | `pr25_live3_20260416_opencode_hle` | completed `1/1` | `timeout=1800`; visible model output |
| `opencode` | `terminal_bench2` | `configs/examples/terminal_bench2_opencode_minimax.yaml` | `pr25_live3_20260416_opencode_tb2` | completed `1/1` | `solver_timeout_sec=1800`; visible model output; verifier reward `1` |
| `openclaw` | `imo_answerbench` | `configs/examples/openclaw_minimax_imo_answerbench.yaml` | `pr25_live2_20260416_openclaw_imo` | completed `1/1` | full ROCK auto-deploy path, bounded smoke via `dataset_index=367` |
| `openclaw` | `hle` | `configs/examples/openclaw_minimax_hle.yaml` | `pr25_live2_20260416_openclaw_hle` | completed `1/1` | cached HLE row, full ROCK auto-deploy path; model response took several minutes |
| `openclaw` | `terminal_bench2` | `configs/examples/terminal_bench2_openclaw_minimax.yaml` | `pr25_live3_20260416_openclaw_tb2` | blocked | `request_timeout=1800`; planner produced no visible model output and the run completed `0/1` |

Current strict status: `8/9` cells pass. `openclaw` x `terminal_bench2` is a real issue under the "must see model output" criterion.

## Smoke Config Conventions

These configs are intentionally bounded for reproducibility:

- IMO smoke configs pin `dataset_index=367`, a short number-theory problem.
- HLE smoke configs pin `dataset_index=1`, which is locally cached and already proven to load.
- OpenCode smoke configs use `timeout: 1800` so slow but valid model output is not misclassified as a timeout.
- terminal-bench-2 DirectLLM smoke uses `max_rounds: 6`.
- terminal-bench-2 OpenCode smoke uses `solver_timeout_sec: 1800`.
- terminal-bench-2 OpenClaw smoke uses `request_timeout: 1800`, `max_attempts: 1`, and `continue_on_planner_error: false`.

The last setting is deliberate. For terminal-bench-2 OpenClaw smoke, a planner timeout is now treated as a failed smoke path, not as a scored fallback sample.

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
  -o run_id=pr25_live2_20260416_directllm_imo

python -m alphadiana.cli run configs/examples/opencode_minimax_hle.yaml \
  -o run_id=pr25_live3_20260416_opencode_hle

python -m alphadiana.cli run configs/examples/openclaw_minimax_hle.yaml \
  -o run_id=pr25_live2_20260416_openclaw_hle

python -m alphadiana.cli run configs/examples/terminal_bench2_openclaw_minimax.yaml \
  -o run_id=pr25_live3_20260416_openclaw_tb2
```

Use the config defaults as checked in unless there is a deliberate review reason to change the smoke bounds.
