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

## Switching Models

For most example configs, switching to another OpenAI-compatible backend only
requires changing the three standard environment variables before running
`python -m alphadiana.cli run ...`:

```bash
export OPENAI_BASE_URL='https://api-inference.modelscope.cn/v1'
export OPENAI_API_KEY='ms-...'
export OPENAI_MODEL_NAME='Qwen/Qwen3.5-27B'
```

This works directly for configs that use `${OPENAI_MODEL_NAME}` or read the
provider settings from the environment, such as:

- `configs/examples/zeroclaw_imo_answerbench.yaml`
- `configs/examples/zeroclaw_hle.yaml`
- `configs/examples/zeroclaw_gpqa_diamond.yaml`
- `configs/examples/zeroclaw_mmmu_pro.yaml`

Some smoke/debug configs pin the model in YAML instead of reading
`OPENAI_MODEL_NAME`. For those configs, environment variables alone are not
enough; override the agent config explicitly with `-o`.

Example for `terminal-bench-2`:

```bash
TERMINAL_BENCH2_SMOKE_DIR=/tmp/terminal-bench-2-smoke-dbwal \
TMPDIR=/tmp/alphadiana-tb2-qwen \
python -m alphadiana.cli run configs/examples/terminal_bench2_zeroclaw_minimax.yaml \
  -o run_id=tb2_zeroclaw_qwen35b_smoke \
  -o output_dir=./results/tb2_zeroclaw_qwen35b_smoke \
  -o num_samples=1 \
  -o agent.config.model='Qwen/Qwen3.5-27B' \
  -o agent.config.api_base='https://api-inference.modelscope.cn/v1' \
  -o agent.config.api_key='ms-...' \
  -o agent.config.logs_base_dir=/tmp/alphadiana-tb2-qwen/tb2_logs
```

Reason: `configs/examples/terminal_bench2_zeroclaw_minimax.yaml` currently pins
`agent.config.model: "minimax-m2.5"`.

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
