# ZeroClaw MiniMax Smoke Matrix (2026-04-18)

## Scope

This document records a real smoke run for `zeroclaw` with MiniMax on the
current `pr23` worktree.

- Model: `minimax-m2.5`
- API base: `https://api.example.com/v1/`
- Mode: `zeroclaw`
- Samples per runnable benchmark: `3`
- Result root: `results/pr23_zeroclaw_matrix_20260418`

Benchmarks in scope:

1. IMO-AnswerBench
2. HLE
3. GPQA-Diamond
4. MMMU-Pro
5. terminal-bench-2
6. swe-bench pro

Practical scope in this worktree:

- `swe-bench pro` was not run.
- Reason: this branch does not expose a runnable `zeroclaw × swe-bench pro`
  path in the current benchmark registry/config set.
- `terminal-bench-2` does support `zeroclaw` in this branch and was run.

## Environment

Start the ZeroClaw ROCK control plane first:

```bash
source scripts/activate.sh
export OPENAI_BASE_URL='https://api.example.com/v1/'
export OPENAI_MODEL_NAME='minimax-m2.5'
export OPENAI_API_KEY='...'
bash scripts/start_zeroclaw.sh
```

Then run the smoke commands.

## Switching This Runbook To Another Model

This runbook records the MiniMax evidence for `2026-04-18`, but the same
benchmark commands can be reused with another OpenAI-compatible backend.

For `IMO-AnswerBench`, `HLE`, `GPQA-Diamond`, and `MMMU-Pro`, switch the three
provider env vars first:

```bash
export OPENAI_BASE_URL='https://api-inference.modelscope.cn/v1'
export OPENAI_API_KEY='ms-...'
export OPENAI_MODEL_NAME='Qwen/Qwen3.5-27B'
```

Then rerun the same `python -m alphadiana.cli run ...` commands with a new
`run_id`.

`terminal-bench-2` is the exception in this worktree. Its example config
`configs/examples/terminal_bench2_zeroclaw_minimax.yaml` pins
`agent.config.model: "minimax-m2.5"`, so changing only `OPENAI_MODEL_NAME` is
not sufficient. Use CLI overrides:

```bash
TERMINAL_BENCH2_SMOKE_DIR=/tmp/terminal-bench-2-smoke-dbwal \
TMPDIR=/tmp/alphadiana-tb2-qwen \
python -m alphadiana.cli run configs/examples/terminal_bench2_zeroclaw_minimax.yaml \
  -o run_id=pr23_zeroclaw_tb2_qwen35b_s1_r2_20260418 \
  -o output_dir=./results/pr23_zeroclaw_matrix_20260418 \
  -o num_samples=1 \
  -o agent.config.model='Qwen/Qwen3.5-27B' \
  -o agent.config.api_base='https://api-inference.modelscope.cn/v1' \
  -o agent.config.api_key='ms-...' \
  -o agent.config.logs_base_dir=/tmp/alphadiana-tb2-qwen/tb2_logs
```

Base smoke commands:

```bash
python -m alphadiana.cli run configs/examples/zeroclaw_imo_answerbench.yaml \
  -o run_id=pr23_zeroclaw_imo_k3_20260418 \
  -o output_dir=./results/pr23_zeroclaw_matrix_20260418 \
  -o num_samples=3

python -m alphadiana.cli run configs/examples/zeroclaw_gpqa_diamond.yaml \
  -o run_id=pr23_zeroclaw_gpqa_k3_20260418 \
  -o output_dir=./results/pr23_zeroclaw_matrix_20260418 \
  -o num_samples=3

python -m alphadiana.cli run configs/examples/zeroclaw_mmmu_pro.yaml \
  -o run_id=pr23_zeroclaw_mmmu_k3_20260418 \
  -o output_dir=./results/pr23_zeroclaw_matrix_20260418 \
  -o num_samples=3

TERMINAL_BENCH2_SMOKE_DIR=/tmp/terminal-bench-2-smoke-dbwal \
TMPDIR=/tmp/alphadiana-tb2-pr23 \
python -m alphadiana.cli run configs/examples/terminal_bench2_zeroclaw_minimax.yaml \
  -o run_id=pr23_zeroclaw_tb2_k3_20260418 \
  -o output_dir=./results/pr23_zeroclaw_matrix_20260418 \
  -o num_samples=3 \
  -o agent.config.logs_base_dir=/tmp/alphadiana-tb2-pr23/tb2_logs
```

HLE required a stabilized rerun.

Default HLE smoke config (`max_tool_iterations=100`) repeatedly entered a
search loop and was not suitable for smoke readiness on this backend. Per the
execution playbook, that required a new `run_id` for the setup change.

Stabilized HLE command used for the final matrix:

```bash
python -m alphadiana.cli run configs/examples/zeroclaw_hle.yaml \
  -o run_id=pr23_zeroclaw_hle_k3_mt12_20260418 \
  -o output_dir=./results/pr23_zeroclaw_matrix_20260418 \
  -o num_samples=3 \
  -o agent.config.max_tool_iterations=12
```

## Final 6x1 Completion Matrix

Cell format: `报错 / reward=1 / reward=0` over 3 samples.

| Benchmark | zeroclaw |
| --- | --- |
| IMO-AnswerBench | `0 / 0 / 3` |
| HLE | `0 / 1 / 2` |
| GPQA-Diamond | `0 / 1 / 2` |
| MMMU-Pro | `0 / 0 / 3` |
| terminal-bench-2 | `0 / 0 / 3` |
| swe-bench pro | `N/A` |

## Final 6x1 Trajectory Matrix

Cell format: `检查通过 / 检查有异常` over 3 samples.

Trajectory check rule used in this report:

- `检查有异常` if the sample has a non-null `error`, or the stored trajectory
  contains clear runtime/provider/IO anomaly markers such as
  `rate_limited`, `429 Too Many Requests`, `BadRequestError`,
  `I don't see any attachment`, `tool failures`, or `max iterations reached`.
- Wrong answers alone are not counted as trajectory anomalies.

| Benchmark | zeroclaw |
| --- | --- |
| IMO-AnswerBench | `0 / 3` |
| HLE | `1 / 2` |
| GPQA-Diamond | `3 / 0` |
| MMMU-Pro | `0 / 3` |
| terminal-bench-2 | `0 / 3` |
| swe-bench pro | `N/A` |

## How To Interpret `3/3` Abnormal Trajectories

`3/3` abnormal trajectories do not automatically mean the implementation is
completely unusable.

What it does mean:

- the path is not clean enough to claim stable smoke quality under the exact
  backend/settings used in that run
- raw trajectories retained repeated runtime/provider/tool anomalies
- more debugging or rerun cleanup is needed before treating the path as clean
  execution evidence

What it does not necessarily mean:

- it does not prove the benchmark path can never finish
- it does not prove the wrapper integration is fundamentally broken
- some `3/3` abnormal cases in this report came from first-wave provider
  rate-limit conditions rather than a benchmark-specific logic bug

## Artifact Map

Final run IDs used in the matrix:

- IMO: `pr23_zeroclaw_imo_k3_20260418`
- HLE: `pr23_zeroclaw_hle_k3_mt12_20260418`
- GPQA-Diamond: `pr23_zeroclaw_gpqa_k3_20260418`
- MMMU-Pro: `pr23_zeroclaw_mmmu_k3_20260418`
- terminal-bench-2: `pr23_zeroclaw_tb2_k3_20260418`

Primary per-task artifacts:

- `results/pr23_zeroclaw_matrix_20260418/pr23_zeroclaw_imo_k3_20260418/tasks/imo_answerbench_0.json`
- `results/pr23_zeroclaw_matrix_20260418/pr23_zeroclaw_hle_k3_mt12_20260418/tasks/hle_1.json`
- `results/pr23_zeroclaw_matrix_20260418/pr23_zeroclaw_gpqa_k3_20260418/tasks/gpqa_0.json`
- `results/pr23_zeroclaw_matrix_20260418/pr23_zeroclaw_mmmu_k3_20260418/tasks/mmmu_pro_test_History_1.json`
- `results/pr23_zeroclaw_matrix_20260418/pr23_zeroclaw_tb2_k3_20260418/tasks/tb2_db-wal-recovery.json`

## Notes

- The first wave used a different API key and hit provider-side rate limits
  under multi-benchmark overlap. Those results are still valuable for smoke
  completion, but many trajectories retain rate-limit evidence.
- Switching to a different provider/model is straightforward for the
  env-driven ZeroClaw example configs, but `terminal-bench-2` currently needs
  explicit `agent.config.*` CLI overrides because its example YAML pins the
  MiniMax model.
- The model-switch instructions in this document were spot-checked locally with
  `Qwen/Qwen3.5-27B` under serial single-sample reruns. The detailed evidence
  is archived in `../../context/pr23-zeroclaw-smoke-20260418/README.md`.
- HLE is the only benchmark whose final matrix entry comes from a stabilized
  rerun with a different setup parameter.
- The detailed local execution trail is archived in
  `../../context/pr23-zeroclaw-smoke-20260418/README.md`.
