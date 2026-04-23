# Full Local-vLLM Rollout (2026-04-19)

This runbook prepares the requested staged full rollout for:

- `5` benchmarks
- `4` harnesses
- `3` local vLLM models

That expands to `60` concrete runs.

This runbook is about execution prep and command generation. It does not, by
itself, change support claims in `context/current_eval_status.md`.

## Scope

Benchmarks:

- `imo_answerbench`
- `gpqa_diamond`
- `hle`
- `mmmu_pro`
- `terminal_bench2`

Harnesses:

- `direct_llm`
- `openclaw`
- `opencode`
- `zeroclaw`

Models:

- `Qwen/Qwen3.5-27B`
- `google/gemma-4-31B-it`
- `nvidia/nemotron-3-nano-30b-a3b`

The canonical manifest is:

- `configs/full_runs/rollout_local_vllm_campaign_20260419.yaml`
- `configs/full_runs/rollout_local_vllm_campaign_20260419.env.example`

Internal bring-up / readiness plan for a separate execution host:

- `context/benchmark-rollout-full-plan-20260419.md`

## Environment Contract

Run from the repo root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export QWEN_VLLM_API_BASE=http://127.0.0.1:8001/v1
export QWEN_VLLM_API_KEY=EMPTY
export GEMMA4_VLLM_API_BASE=http://127.0.0.1:8002/v1
export GEMMA4_VLLM_API_KEY=EMPTY
export NEMOTRON_VLLM_API_BASE=http://127.0.0.1:8003/v1
export NEMOTRON_VLLM_API_KEY=EMPTY

export DIRECTLLM_ROOT=/path/to/directllm
export TERMINAL_BENCH2_DIR=/path/to/terminal-bench/tasks
export HF_TOKEN=hf_...
```

Notes:

- `QWEN_VLLM_API_KEY`, `GEMMA4_VLLM_API_KEY`, and `NEMOTRON_VLLM_API_KEY` may stay unset for local unauthenticated vLLM; generated official commands default them to `EMPTY`.
- Override the official terminal-bench checkout root with `DIRECTLLM_TB2_ROOT` when you do not want to rely on `DIRECTLLM_ROOT`.
- `MMMU-Pro` full configs use `data_config: "vision"`.
- Checked-in full-run configs set `strict_report: true`; AlphaDiana-backed full configs also set `strict_isolation: true`.
- The checked-in rollout configs now match the frozen
  `benchmark-rollout-full-plan-20260419` semantics where the current runtime
  actually exposes them:
  `temperature=0.0`, `top_p=0.95`, `max_tokens=32768`, `streaming=true`,
  `timeout=1800`.
- Keep the plan's gap list in mind when reading that line:
  `thinking` is not uniformly forwarded, `opencode` does not uniformly expose
  `top_p` / `max_tokens`, and `zeroclaw` does not uniformly expose
  `top_p` / `max_tokens` / `streaming`.

## Preflight

Inspect the matrix first:

```bash
python scripts/benchmark_rollout.py summary
```

Run the full preflight:

```bash
python scripts/benchmark_rollout.py preflight \
  --check-docker \
  --check-rock \
  --probe-vllm
```

That verifies:

- manifest and config paths
- required env vars and official checkout roots
- optional live `/models` probes for the three vLLM hosts
- Docker availability plus required controller/runtime images
- ROCK admin/proxy/redis health

## Waves

The checked-in manifest splits the `60` runs into four waves:

| Wave | Runs | Meaning |
|---|---:|---|
| `wave_a_mainline` | `46` | Pilot-ready or smoke-valid paths without an active blocker |
| `wave_b_official` | `3` | Official direct-LLM terminal-bench runs launched from the benchmark checkout |
| `wave_c_high_risk` | `8` | Paths kept in scope but isolated for modality/coding-risk reasons |
| `wave_d_blocked` | `3` | Known-problem paths that stay requested but must not contaminate the mainline claim |

Current special handling:

- `terminal_bench2 x openclaw` stays blocked because the April 19 pilot was still experimental.
- `nemotron-3-nano-30b-a3b` is isolated as high risk on `hle` and `mmmu_pro`.
- `SWE-bench Pro` is excluded from the current rollout scope while its scorer / evaluator health is fixed separately.

## Commands

Print the mainline run commands:

```bash
python scripts/benchmark_rollout.py commands --wave wave_a_mainline --kind run
```

Print one official direct-LLM command:

```bash
python scripts/benchmark_rollout.py commands \
  --benchmark terminal_bench2 \
  --harness direct_llm \
  --model qwen35_27b \
  --kind run
```

Materialize runnable shell scripts under `generated/rollout_campaigns/`:

```bash
python scripts/benchmark_rollout.py materialize --wave wave_a_mainline --kind both
```

The generated run commands intentionally omit `--redo-all`. AlphaDiana resumes
from checkpoint by default.

## Logging And Artifacts

- AlphaDiana runs keep raw shell logs under `logs/<run_id>.log`.
- Official direct-LLM commands also tee back into this repo's `logs/` even
  though the work executes from the official benchmark checkouts.
- Result trees remain benchmark-native:
  `results/<run_id>/...` for AlphaDiana,
  `jobs/...` for Harbor.

Reviewer-facing rollout state lives in:

- `context/full-rollout-local-vllm-20260419/README.md`

## April 22 Local Qwen Smoke Update

Fresh branch-local validation against the local endpoint
`http://127.0.0.1:8011/v1` and served model `Qwen/Qwen3.5-27B` now exists in:

- `context/full-rollout-local-vllm-20260419/local-qwen-normalized-trace-validation-20260422.md`

Current practical takeaway from that note:

- `direct_llm` wrote normal scored task JSONs plus the new
  `artifact_manifest.files.normalized_trace` artifact on
  `imo_answerbench`, `hle`, `gpqa_diamond`, and `mmmu_pro`
- `opencode` now reaches the namespaced local model alias correctly and also
  writes the normalized trace artifact on the same four benchmarks
- on this local `32768`-context Qwen deployment, the current plain-benchmark
  `opencode` path still over-requests output tokens and preserves
  `ContextOverflowError` task records for `hle`, `gpqa_diamond`, and
  `mmmu_pro`
- as of April 23, 2026, the checked-in plain-benchmark `opencode` configs keep
  `tool_call: true` again. We intentionally do not rewrite benchmark prompts
  or add a token-budget proxy in front of the local vLLM server for this path;
  if vLLM rejects the request with `ContextOverflowError` /
  `VLLMValidationError`, AlphaDiana stores that upstream failure directly as a
  provider-error task record
- the same direct-provider default now applies across the local-Qwen harnesses:
  current main no longer does request-side token estimation / max-token
  down-capping in `direct_llm`, and the SWE container path no longer adds
  default problem-statement truncation or OpenClaw completion-cap shrinking
  just to avoid overflow. Oversized requests are left to the provider, and
  upstream overflow stays a preserved task error
- `swebench_docker` also now reuses cached local base/runtime images instead of
  forcing `docker pull` before every derived runtime-image build. That removes
  one common Docker Hub failure mode on shared hosts that already have the
  required SWE images cached
- `openclaw` / `zeroclaw` local-Qwen follow-up from this checkout is still
  blocked by ROCK bring-up drift rather than by the plain benchmark configs
