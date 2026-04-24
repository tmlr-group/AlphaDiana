# Benchmark Runbooks

This directory contains user-facing runbooks for benchmark evaluation paths.

Start from [`context/current_eval_status.md`](../../context/current_eval_status.md)
for the current cross-benchmark support snapshot, then open the matching
runbook here.

For paper-facing wording about whether a benchmark path is sandboxed or
containerized, also read
[`docs/benchmark-isolation.md`](../benchmark-isolation.md). That note keeps the
claim intentionally weaker than a formal security guarantee.

These files should answer:

- how to run a benchmark path
- which configs are supported
- what status or caveats apply right now

As of April 22, 2026, the benchmark runbooks also document current
result-semantics caveats for `qwen3vl` OpenCode errors, ZeroClaw failure
evidence, `terminal_bench2` verifier bookkeeping, the current OpenRouter
`nvidia/nemotron-nano-12b-v2-vl:free` multimodal smoke evidence, and the new
same-day early full-run caveats for `IMO-AnswerBench`, `MMMU-Pro`, and
`terminal-bench-2`.

Current main generic `zeroclaw` is now simpler than the earlier April 22
notes: normal benchmark runs use one sandbox-only CLI path
(`metadata.transport=zeroclaw_cli_sandbox`) for both text and image-backed
tasks. Historical `disable_tools`, gateway, and proxy path notes remain
useful audit evidence only.

As of April 23, 2026, current GPQA-Diamond `direct_llm` local-vLLM evidence
also includes a Phase 10 top-20 Int16 logprob sidecar smoke; see
[`context/current_eval_status.md`](../../context/current_eval_status.md) for
the run ID, config, log, result paths, and current harness limitations.

Reviewer-facing evidence, dated pilot notes, internal design notes, and
cross-benchmark status snapshots belong under `context/`.

For Hugging Face result archival rules, staging layout, and the current
DirectLLM `trajectory / artifacts / logprobs` upload contract, use
[context/hf-result-upload-spec-20260423.md](../../context/hf-result-upload-spec-20260423.md).

Each benchmark document describes prerequisites, supported execution modes,
example configs, and smoke-test commands:

- [IMO-AnswerBench](imo-answerbench.md)
- [HLE](hle.md)
- [GPQA-Diamond](gpqa-diamond.md)
- [MMMU-Pro](mmmu-pro.md)
- [SWE-bench Pro](swebench-pro.md)
- [SWE-bench Verified](swebench-verified.md)
- [terminal-bench-2](terminal-bench-2.md)
- [Full Local-vLLM Rollout (2026-04-19)](full-rollout-local-vllm-20260419.md)
- [ZeroClaw MiniMax Smoke Matrix (2026-04-18)](zeroclaw-minimax-smoke-20260418.md)
- [ZeroClaw Qwen OpenRouter Pilot Matrix (2026-04-19)](zeroclaw-qwen-openrouter-pilot-20260419.md)

## Find The Right Runbook

If you know the benchmark already:

- `IMO-AnswerBench`:
  [imo-answerbench.md](imo-answerbench.md) covers `direct_llm`,
  `openclaw`, `opencode`, and `zeroclaw`.
- `HLE`:
  [hle.md](hle.md) covers `direct_llm`, `openclaw`, `opencode`, and
  `zeroclaw`.
- `GPQA-Diamond`:
  [gpqa-diamond.md](gpqa-diamond.md) covers `direct_llm`, `openclaw`,
  `opencode`, and `zeroclaw`.
- `MMMU-Pro`:
  [mmmu-pro.md](mmmu-pro.md) covers `direct_llm`, `openclaw`, `opencode`,
  and `zeroclaw`.
- `terminal-bench-2`:
  [terminal-bench-2.md](terminal-bench-2.md) covers the Diana-native
  `openclaw`, `opencode`, and `zeroclaw` paths, and also points to the
  official external `direct_llm` Harbor path.
- `SWE-bench Pro`:
  [swebench-pro.md](swebench-pro.md) covers the Diana-native `openclaw`,
  `opencode`, and `zeroclaw` paths, and also points to the official external
  `direct_llm` SWE-agent path.

If you know the harness first:

- `direct_llm`:
  use the benchmark runbook directly for `IMO-AnswerBench`, `HLE`,
  `GPQA-Diamond`, and `MMMU-Pro`; for `terminal-bench-2` and
  `SWE-bench Pro`, the runbooks route you to the official external repos.
- `openclaw`:
  use the matching benchmark runbook for all six benchmarks.
- `opencode`:
  use the matching benchmark runbook for all six benchmarks.
- `zeroclaw`:
  use the matching benchmark runbook for all six benchmarks.

If you are searching for a real Qwen/OpenRouter pilot or a ZeroClaw-specific
smoke recipe rather than the general runbook, start from:

- [zeroclaw-minimax-smoke-20260418.md](zeroclaw-minimax-smoke-20260418.md)
- [zeroclaw-qwen-openrouter-pilot-20260419.md](zeroclaw-qwen-openrouter-pilot-20260419.md)
- [full-rollout-local-vllm-20260419.md](full-rollout-local-vllm-20260419.md)

Ready-to-run full benchmark configs live in
[configs/full_runs](../../configs/full_runs/README.md) where available. Use
those for full evaluations.

For the staged April 19 local-vLLM full campaign, start from
[Full Local-vLLM Rollout (2026-04-19)](full-rollout-local-vllm-20260419.md)
instead of expanding the current `60` active paths by hand.

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

For the April 18, 2026 OpenRouter-backed Qwen pilot configs, use:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b
```

That slug is the OpenRouter model ID for the logical target
`Qwen/Qwen3.5-27B`.

Dedicated 3-task OpenRouter pilot YAMLs now exist for `GPQA-Diamond`,
`IMO-AnswerBench`, and `HLE`. The April 19/20, 2026 `terminal-bench-2` and
`SWE-bench Pro` Qwen pilots reused the checked-in minimax smoke YAMLs with CLI
overrides instead of adding new example configs. The latest `MMMU-Pro` sandbox
follow-up also reused the checked-in smoke YAMLs plus explicit CLI overrides.
See the benchmark-specific runbooks for the exact commands.

When running from a local checkout, prefer `python -m alphadiana.cli ...` so the
current workspace code is used.

For GPQA-Diamond and MMMU-Pro, dedicated smoke/debug example configs now exist
for all four modes:

- `direct_llm`
- `openclaw`
- `opencode`
- `zeroclaw`

Dedicated 3-task OpenRouter pilot configs also exist for:

- `IMO-AnswerBench x direct_llm`
- `IMO-AnswerBench x openclaw`
- `IMO-AnswerBench x opencode`
- `GPQA-Diamond x direct_llm`
- `GPQA-Diamond x openclaw`
- `GPQA-Diamond x opencode`
- `HLE x direct_llm`
- `HLE x openclaw`
- `HLE x opencode`

OpenRouter/Qwen pilot coverage on April 19 also includes:

- `GPQA-Diamond x opencode`
- `IMO-AnswerBench x opencode`
- `HLE x direct_llm`
- `HLE x openclaw`
- `HLE x opencode`
- `MMMU-Pro x opencode`
- `terminal-bench-2 x direct_llm` via the official Harbor `terminus-2` path
- `terminal-bench-2 x opencode`
- `terminal-bench-2 x openclaw`
- `SWE-bench Pro x direct_llm` via the official `SWE-agent` path
- `SWE-bench Pro x opencode`
- `SWE-bench Pro x openclaw`

The two official `direct_llm` follow-ups were repaired and re-audited inside
the upstream benchmark checkouts rather than through AlphaDiana YAMLs. See the
benchmark-specific runbooks for the exact caveats and accepted archive IDs.

The checked-in plain-benchmark `opencode` configs for `GPQA-Diamond`,
`IMO-AnswerBench`, `HLE`, and the default `MMMU-Pro` smoke path now set
`controller_mode: docker` by default. Build
`alphadiana/tb2-opencode-controller:latest` before using those configs. The
host-process path is still available for debugging via
`-o agent.config.controller_mode=host`.

Smoke-test success means the evaluation path loads tasks, invokes the selected agent mode, and writes scored results. It does not mean the model answered correctly.

## ZeroClaw Note

For the benchmark runbooks in this folder, ZeroClaw smoke validation is documented only for sandboxed execution:

- `IMO-AnswerBench` and `HLE`: ROCK sandbox with in-sandbox ZeroClaw CLI
- `terminal-bench-2`: Docker task container with an in-container derived runtime image

The host-local `_run_locally()` path is useful for debugging, but it is not counted as the formal benchmark smoke path in these runbooks.

For PR-scoped local evidence from the latest ZeroClaw benchmark smokes and the
Qwen OpenRouter pilot repair audit, see
[`context/pr23-zeroclaw-smoke-20260418/README.md`](../../context/pr23-zeroclaw-smoke-20260418/README.md)
and
[`context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/README.md`](../../context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/README.md).
The `2026-04-20` rerun addendum for the three previously pending `zeroclaw`
items lives at
[`context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/rerun_20260420_pending_recheck.md`](../../context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/rerun_20260420_pending_recheck.md).

Related non-runbook references kept outside this folder:

- Main April 18-20 Qwen/OpenRouter benchmark pilot context:
  [`context/qwen-openrouter-pilots/README.md`](../../context/qwen-openrouter-pilots/README.md)
- HLE multimodal deep-dive note:
  [`context/P25-three-benchmarks/openclaw_opencode_hle_multimodal_validation_20260417.md`](../../context/P25-three-benchmarks/openclaw_opencode_hle_multimodal_validation_20260417.md)
- SWE-bench Verified PR26 evidence bundle:
  [`context/pr26-swebench-verified/README.md`](../../context/pr26-swebench-verified/README.md)
- SWE-bench Pro PR29 reproduction context:
  [`context/pr29-add-swebench-pro/README.md`](../../context/pr29-add-swebench-pro/README.md)
