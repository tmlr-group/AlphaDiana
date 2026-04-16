# Current Eval Status

Last updated: 2026-04-16
Worktree: `/tmp/AlphaDiana-dev-pr25`

Use this with:

- `benchmark_execution_playbook_v1.md`
- `three-benchmarks-pipeline.md`
- `../AGENTS.md`

## Current Proven State

PR25 now has a fully completed 3x3 smoke matrix on the local PR worktree.

Latest full rerun: 2026-04-16, using API base `https://api.example.com/v1/` and model `minimax-m2.5`.

| Mode | IMO-AnswerBench | HLE | terminal-bench-2 |
|---|---|---|---|
| `direct_llm` | `pr25_live2_20260416_directllm_imo` | `pr25_live2_20260416_directllm_hle` | `pr25_live2_20260416_directllm_tb2` |
| `opencode` | `pr25_live2_20260416_opencode_imo` | `pr25_live2_20260416_opencode_hle` | `pr25_live2_20260416_opencode_tb2` |
| `openclaw` | `pr25_live2_20260416_openclaw_imo` | `pr25_live2_20260416_openclaw_hle` | `pr25_live2_20260416_openclaw_tb2` |

Every run above completed `1/1` tasks and wrote results.

## Operator Notes

- Smoke quality is intentionally bounded. These runs prove path completeness, not benchmark competitiveness.
- IMO smoke now uses `dataset_index=367`.
- HLE smoke now uses `dataset_index=1`.
- terminal-bench-2 smoke now uses a single staged task directory rooted at `/tmp/terminal-bench-2-smoke-dbwal`.
- HLE can load from local cache without forcing `HF_TOKEN`; fresh machines still need `HF_TOKEN`.
- terminal-bench-2 OpenClaw smoke uses `continue_on_planner_error: true` so a planner timeout still falls through to verifier execution and result writing.
- OpenClaw x HLE can take several minutes after the gateway `/chat/completions` request returns HTTP 200; wait for artifact collection before classifying it as stuck.

## Regression Status Relative To Earlier PR25 Review

The earlier merge blockers are now locally fixed:

- `tests/test_external_benchmark_pipeline.py` passes again because `scripts/full_pipeline.py` exists.
- `python -m alphadiana.cli list-benchmarks` includes `external_benchmark`.
- terminal-bench-2 no longer hard-requires `tomli` on Python 3.11 at import time.
- the default setup path now includes benchmark dependencies.

## Related Docs

- Detailed matrix notes: `three-benchmarks-pipeline.md`
- IMO pipeline snapshot: `imo_pipeline_status_20260415.md`
- OpenClaw x IMO runbook: `openclaw_imo_answerbench_runbook_20260415.md`
