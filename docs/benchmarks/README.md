# Benchmark Runbooks

This directory contains user-facing runbooks for the PR25 benchmarks.

Each benchmark document describes prerequisites, supported execution modes, example configs, and smoke-test commands. PR23/PR25 integration now covers `zeroclaw` on all three runbooks as well:

- [IMO-AnswerBench](imo-answerbench.md)
- [HLE](hle.md)
- [terminal-bench-2](terminal-bench-2.md)

Ready-to-run full benchmark configs live in [configs/full_runs](../../configs/full_runs/README.md). Use those for full evaluations.

The configs under `configs/examples/` are smoke/debug configs. They intentionally pin one task with `dataset_index` or `max_tasks` and should not be used for full benchmark runs.

Common setup for all examples:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

Smoke-test success means the evaluation path loads tasks, invokes the selected agent mode, and writes scored results. It does not mean the model answered correctly.
