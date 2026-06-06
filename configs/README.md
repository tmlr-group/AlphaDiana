# configs/

AlphaDiana experiment configuration directory.

```
configs/
├── README.md                  ← you are here
├── PROMPTS.md                 ← canonical system prompts for every benchmark × harness
├── schema.yaml                ← annotated full-field reference for config structure
├── full_runs/                 ← production runs (pinned models, logprob capture)
│   └── README.md              ← inventory and naming conventions for full runs
├── smokes/                    ← active smoke matrices with pinned local settings
├── examples/                  ← smoke / pilot templates (env-var placeholders, max_tasks ≤ 5)
│   └── *.yaml
├── test_direct_llm_qwen.yaml  ← quick sanity-check: direct_llm via OpenRouter
├── test_openclaw_quick.yaml   ← quick sanity-check: openclaw via OpenRouter
└── observe_prompt_injection_test.yaml  ← security test: prompt injection detection
```

---

## Which file to use

| Goal | Where to look |
|---|---|
| Run a full benchmark | `full_runs/` |
| Run the active local-Qwen harness smoke matrix | `smokes/` |
| Smoke-test a new model or harness ad hoc (1–5 tasks) | `examples/` |
| Run external_benchmark with Podman | `examples/external_benchmark_*_podman_smoke.yaml` |
| Understand what fields are valid | `schema.yaml` |
| Check which system prompt to use | `PROMPTS.md` |
| Quick one-off sanity check | root-level `test_*.yaml` |

---

## `full_runs/` — production runs

Each file is one benchmark × harness × model combination, fully pinned.

- Model, URL, and all hyperparameters are explicit (no `${ENV_VAR}` placeholders except tokens)
- `capture_logprobs: true` + `top_logprobs: 20` for top-20 int16 logprob sidecar capture
- `system_prompt` is always set explicitly — see `PROMPTS.md`
- Naming: `{benchmark}_{harness}_{model_short}_logprobs.yaml`

See [`full_runs/README.md`](full_runs/README.md) for the full file inventory.

---

## `smokes/` — active smoke matrices

The validation-only Podman nightly matrix lives in
[`smokes/podman_nightly_validation/`](smokes/podman_nightly_validation/).
It contains opt-in configs for OpenClaw, ZeroClaw, and OpenCode standard
reasoning rows across AIME, GPQA-Diamond, HLE, and IMO-AnswerBench, plus the
already validated Podman task-container cells for TerminalBench2 and
SWE-bench Verified. Run it with
`bash scripts/run_podman_nightly_validation.sh [standard|task|all]`. Current
evidence and caveats are recorded in
[`alphadiana/context/podman-nightly-validation/README.md`](../alphadiana/context/podman-nightly-validation/README.md);
these configs do not promote Podman defaults.

The standard-reasoning Podman scale-readiness pilot matrix lives in
[`smokes/podman_scale_readiness/`](smokes/podman_scale_readiness/). It covers
OpenClaw, ZeroClaw, and OpenCode across AIME, GPQA-Diamond, HLE, and
IMO-AnswerBench with three tasks per cell. Run it with
`bash scripts/run_podman_scale_readiness.sh [validate|pilot|audit]`. Operator
commands and support boundaries are documented in
[`docs/benchmarks/podman.md`](../docs/benchmarks/podman.md), and evidence is in
[`alphadiana/context/podman-scale-readiness/README.md`](../alphadiana/context/podman-scale-readiness/README.md).

The current prompt-aligned local-Qwen smoke matrix lives in
[`smokes/harness_prompt_alignment_20260425/`](smokes/harness_prompt_alignment_20260425/).
It contains OpenClaw and ZeroClaw configs for AIME 2024, IMO-AnswerBench,
GPQA-Diamond, HLE, and MMMU-Pro in both `trunc5k` and `long64k` settings.
ZeroClaw `long64k` smokes are serial per config (`max_concurrent: 1`) because
the April 26 local-Qwen recovery evidence found concurrent ZeroClaw long runs
can stall before the first provider request and therefore cannot save logprobs.

---

## `examples/` — smoke / pilot templates

Templates with `${OPENAI_BASE_URL}` / `${OPENAI_MODEL_NAME}` / `${OPENAI_API_KEY}` placeholders.
Copy and fill in values to create a production config. Most have `max_tasks: 1–5` for fast smoke runs.

### Direct LLM

| File | Benchmark | Notes |
|---|---|---|
| `direct_llm.yaml` | AIME | Generic env-var template |
| `direct_llm_gpqa_diamond.yaml` | GPQA | Generic env-var template |
| `direct_llm_hle.yaml` | HLE | 1-task smoke |
| `direct_llm_mmmu_pro.yaml` | MMMU-Pro | Generic env-var template |
| `directllm_minimax_imo_answerbench.yaml` | IMO | MiniMax model, 1-task smoke |
| `directllm_qwen35_27b_gpqa_diamond_pilot.yaml` | GPQA | Qwen3.5-27B, 3-task pilot |
| `directllm_qwen35_27b_hle_pilot.yaml` | HLE | Qwen3.5-27B, 3-task pilot |
| `directllm_qwen35_27b_imo_answerbench_pilot.yaml` | IMO | Qwen3.5-27B, 3-task pilot |

### OpenClaw

| File | Benchmark | Notes |
|---|---|---|
| `openclaw_aime2024.yaml` | AIME 2024 | env-var, full run template |
| `openclaw_aime2024_multisandbox.yaml` | AIME 2024 | multi-sandbox parallel variant |
| `openclaw_aime2025_glm5.yaml` | AIME 2025 | GLM-5 model |
| `openclaw_gpqa_diamond.yaml` | GPQA | env-var template, full |
| `openclaw_hle.yaml` | HLE | 1-task smoke |
| `openclaw_imo_answerbench.yaml` | IMO | 1-task smoke |
| `openclaw_minimax_hle.yaml` | HLE | MiniMax model, 1-task smoke |
| `openclaw_minimax_imo_answerbench.yaml` | IMO | MiniMax model, 1-task smoke |
| `openclaw_mmmu_pro.yaml` | MMMU-Pro | env-var template, full |
| `openclaw_qwen35_27b_gpqa_diamond_pilot.yaml` | GPQA | Qwen3.5-27B, 3-task pilot |
| `openclaw_qwen35_27b_hle_pilot.yaml` | HLE | Qwen3.5-27B, 3-task pilot |
| `openclaw_qwen35_27b_imo_answerbench_pilot.yaml` | IMO | Qwen3.5-27B, 3-task pilot |
| `openclaw_swe_bench.yaml` | SWE-Bench | 1-task smoke |

### OpenCode

| File | Benchmark | Notes |
|---|---|---|
| `opencode_gpqa_diamond.yaml` | GPQA | MiniMax model, full |
| `opencode_minimax_hle.yaml` | HLE | MiniMax, 1-task smoke |
| `opencode_minimax_imo_answerbench.yaml` | IMO | MiniMax, 1-task smoke |
| `opencode_mmmu_pro.yaml` | MMMU-Pro | MiniMax, full |
| `opencode_qwen35_27b_gpqa_diamond_pilot.yaml` | GPQA | Qwen3.5-27B, 3-task pilot |
| `opencode_qwen35_27b_hle_pilot.yaml` | HLE | Qwen3.5-27B, 3-task pilot |
| `opencode_qwen35_27b_imo_answerbench_pilot.yaml` | IMO | Qwen3.5-27B, 3-task pilot |
| `opencode_swe_bench.yaml` | SWE-Bench | 1-task smoke |

### ZeroClaw

| File | Benchmark | Notes |
|---|---|---|
| `zeroclaw_aime2026.yaml` | AIME 2026 | env-var template, 1-task smoke |
| `zeroclaw_aime2026_local_smoke.yaml` | AIME 2026 | local vLLM variant, 1-task smoke |
| `zeroclaw_gpqa_diamond.yaml` | GPQA | env-var template, 1-task smoke |
| `zeroclaw_hle.yaml` | HLE | env-var template, 1-task smoke |
| `zeroclaw_imo_answerbench.yaml` | IMO | env-var template, 1-task smoke |
| `zeroclaw_mmmu_pro.yaml` | MMMU-Pro | env-var template, 1-task smoke |
| `zeroclaw_swe_bench.yaml` | SWE-Bench | env-var template, 1-task smoke |

### external_benchmark

| File | Notes |
|---|---|
| `external_benchmark_openclaw.yaml` | OpenClaw on external_benchmark |
| `external_benchmark_openclaw_L1_batch.yaml` | Batch variant |
| `external_benchmark_openclaw_L1_batch_v2.yaml` | Batch v2 |
| `external_benchmark_openclaw_codex.yaml` | Codex scaffold |
| `external_benchmark_opencode_L1_batch.yaml` | OpenCode batch |
| `external_benchmark_zeroclaw_L1_batch.yaml` | ZeroClaw batch |
| `external_benchmark_claude_code.yaml` | Claude Code agent |
| `external_benchmark_claude_code_codex.yaml` | Claude Code + Codex |
| `external_benchmark_openclaw_smoke.yaml` | Smoke (shared env) |
| `external_benchmark_openclaw_smoke.local.yaml` | Smoke (local overrides) |
| `external_benchmark_opencode_smoke.local.yaml` | Smoke (local overrides) |
| `external_benchmark_zeroclaw_smoke.local.yaml` | Smoke (local overrides) |

### Terminal Bench 2

| File | Harness | Notes |
|---|---|---|
| `terminal_bench2.yaml` | DirectLLM | Generic env-var, 1-task smoke |
| `terminal_bench2_directllm_minimax.yaml` | DirectLLM | MiniMax, 1-task smoke |
| `terminal_bench2_openclaw_minimax.yaml` | OpenClaw | MiniMax, 1-task smoke |
| `terminal_bench2_opencode_minimax.yaml` | OpenCode | MiniMax, 1-task smoke |
| `terminal_bench2_zeroclaw_minimax.yaml` | ZeroClaw | MiniMax, 1-task smoke |

### SWE-Bench Pro

| File | Notes |
|---|---|
| `swebench_pro_direct_llm_smoke.local.yaml` | Local override, 1-task smoke |
| `swebench_pro_openclaw_smoke.local.yaml` | Local override, 1-task smoke |
| `swebench_pro_opencode_smoke.local.yaml` | Local override, 1-task smoke |
| `swebench_pro_zeroclaw_smoke.local.yaml` | Local override, 1-task smoke |

---

## Root-level files

| File | Purpose |
|---|---|
| `schema.yaml` | Annotated field reference — lists every valid key with types and descriptions |
| `test_direct_llm_qwen.yaml` | Quick sanity-check: `direct_llm` calling Qwen3-235B via OpenRouter |
| `test_openclaw_quick.yaml` | Quick sanity-check: `openclaw` via OpenRouter (1 task) |
| `observe_prompt_injection_test.yaml` | Security test: verifies AlphaDiana detects prompt injection in task inputs |

---

## File suffix conventions

| Suffix | Meaning |
|---|---|
| _(none)_ | Standard template with env-var placeholders |
| `_pilot` | 3-task pilot, model partially pinned |
| `_smoke` | 1-task smoke run |
| `.local` | Local machine overrides (not committed to CI) — gitignored except by exception |
| `_logprobs` | Top-20 int16 logprob capture enabled |
