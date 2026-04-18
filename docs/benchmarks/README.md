# Benchmark Runbooks

This directory contains user-facing runbooks for the benchmark evaluation paths.

Start from [`context/current_eval_status.md`](../../context/current_eval_status.md)
for the current cross-benchmark support snapshot, then open the matching
runbook here.

These files should answer:

- how to run a benchmark path
- which configs are supported
- what status or caveats apply right now

Reviewer-facing evidence, dated pilot notes, internal design notes, and
cross-benchmark status snapshots belong under `context/`.

Each benchmark document describes prerequisites, supported execution modes, example configs, and smoke-test commands:

- [IMO-AnswerBench](imo-answerbench.md)
- [HLE](hle.md)
- [GPQA-Diamond](gpqa-diamond.md)
- [MMMU-Pro](mmmu-pro.md)
- [SWE-bench Pro](swebench-pro.md)
- [SWE-bench Verified](swebench-verified.md)
- [terminal-bench-2](terminal-bench-2.md)

Ready-to-run full benchmark configs live in [configs/full_runs](../../configs/full_runs/README.md). Use those for full evaluations.

The configs under `configs/examples/` are smoke/debug configs. They intentionally pin one task with `dataset_index` or `max_tasks` and should not be used for full benchmark runs.

Common setup for all examples:

```bash
source scripts/activate.sh

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

For the April 18, 2026 OpenRouter-backed Qwen pilot configs, use:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b
```

That slug is the OpenRouter model ID for the logical target
`Qwen/Qwen3.5-27B`.

When running from a local checkout, prefer `python -m alphadiana.cli ...` so the
current workspace code is used.

For GPQA-Diamond and MMMU-Pro, dedicated smoke/debug example configs now exist
for all three modes:

- `direct_llm`
- `openclaw`
- `opencode`

Dedicated 3-task OpenRouter pilot configs also exist for:

- `IMO-AnswerBench x direct_llm`
- `IMO-AnswerBench x openclaw`
- `GPQA-Diamond x direct_llm`
- `GPQA-Diamond x openclaw`

Current limitation: on `main`, `opencode` text-only benchmark tasks still run
through the local CLI path rather than a benchmark-managed sandbox. That is fine
for smoke/debug usage, but it is not equivalent to the OpenClaw sandbox path.

Smoke-test success means the evaluation path loads tasks, invokes the selected agent mode, and writes scored results. It does not mean the model answered correctly.

Related non-runbook references kept outside this folder:

- HLE multimodal deep-dive note:
  [`context/P25-three-benchmarks/openclaw_opencode_hle_multimodal_validation_20260417.md`](../../context/P25-three-benchmarks/openclaw_opencode_hle_multimodal_validation_20260417.md)
- Older SWE-bench Verified/container internal note:
  [`context/pr26-swebench-verified/implementation-notes.md`](../../context/pr26-swebench-verified/implementation-notes.md)
