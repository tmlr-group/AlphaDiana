# Benchmark Runbooks

This directory contains user-facing runbooks for benchmark evaluation paths.

Each benchmark document describes prerequisites, supported execution modes,
example configs, and smoke-test commands. The current ZeroClaw coverage in
this folder is:

- [IMO-AnswerBench](imo-answerbench.md)
- [HLE](hle.md)
- [GPQA-Diamond](gpqa-diamond.md)
- [MMMU-Pro](mmmu-pro.md)
- [terminal-bench-2](terminal-bench-2.md)
- [ZeroClaw MiniMax Smoke Matrix (2026-04-18)](zeroclaw-minimax-smoke-20260418.md)

Ready-to-run full benchmark configs live in
[configs/full_runs](../../configs/full_runs/README.md) where available. Use
those for full evaluations.

The configs under `configs/examples/` are smoke/debug configs. They intentionally pin one task with `dataset_index` or `max_tasks` and should not be used for full benchmark runs.

Common setup for all examples:

```bash
source scripts/activate.sh

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

When running from a local checkout, prefer `python -m alphadiana.cli ...` so the
current workspace code is used.

For GPQA-Diamond and MMMU-Pro, dedicated smoke/debug example configs now exist
for all four modes:

- `direct_llm`
- `openclaw`
- `opencode`
- `zeroclaw`

Current limitation: on `main`, `opencode` text-only benchmark tasks still run
through the local CLI path rather than a benchmark-managed sandbox. That is fine
for smoke/debug usage, but it is not equivalent to the OpenClaw sandbox path.

Smoke-test success means the evaluation path loads tasks, invokes the selected agent mode, and writes scored results. It does not mean the model answered correctly.

## ZeroClaw Note

For the benchmark runbooks in this folder, ZeroClaw smoke validation is documented only for sandboxed execution:

- `IMO-AnswerBench` and `HLE`: ROCK sandbox with in-sandbox ZeroClaw CLI
- `terminal-bench-2`: Docker task container plus Docker controller image

The host-local `_run_locally()` path is useful for debugging, but it is not counted as the formal benchmark smoke path in these runbooks.

For PR-scoped local evidence from the latest ZeroClaw benchmark smokes, see
[`context/pr23-zeroclaw-smoke-20260418/README.md`](../../context/pr23-zeroclaw-smoke-20260418/README.md).
