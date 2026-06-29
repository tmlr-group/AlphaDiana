# SWE-bench Pro Reproduction Guide

Related references kept outside this folder:

- legacy SWE-bench Verified/container reproduction note:
  `docs/benchmarks/swebench-verified.md`
- older Verified/container internal note:
  `context/pr26-swebench-verified/implementation-notes.md`

This guide covers the AlphaDiana paths that were actually exercised for SWE-bench Pro smoke reproduction in this repo:

- `openclaw` via `swebench_docker`
- `opencode` via `swebench_docker`
- `zeroclaw` via `swebench_docker`

`directLLM` is intentionally not documented here as a Diana execution path. For
the direct-LLM baseline on SWE-bench Pro, use the official repository instead:

- `https://github.com/scaleapi/SWE-bench_Pro-os`

## Scope

AlphaDiana does not use the ROCK/AIME flow from the root [README.md](../../README.md) for SWE-bench Pro smoke. Instead, it:

- runs `benchmark.name: swebench_pro_os`
- uses `agent.name: swebench_docker`
- injects the agent runtime into the official SWE task image
- calls the official evaluator assets through `scorer.name: swebench_pro`

Shipped configs:

- `configs/examples/swebench_pro_openclaw_smoke.local.yaml`
- `configs/examples/swebench_pro_openclaw_podman_smoke.local.yaml`
- `configs/examples/swebench_pro_opencode_smoke.local.yaml`
- `configs/examples/swebench_pro_zeroclaw_smoke.local.yaml`
- `configs/examples/swebench_pro_direct_llm_smoke.local.yaml`
- `configs/full_runs/p29_full_openclaw_swebench_pro.yaml`
- `configs/full_runs/p29_full_opencode_swebench_pro.yaml`
- `configs/full_runs/p29_full_zeroclaw_swebench_pro.yaml`

The OpenClaw, OpenCode, ZeroClaw, and opt-in Podman OpenClaw configs are part
of the AlphaDiana reproduction path documented here. The `direct_llm` config is
separate from the Diana-managed execution modes and mirrors the official-path
baseline shape. The three `configs/full_runs/` files are the full benchmark
entry points for the Diana-backed paths. The Podman OpenClaw smoke is an
opt-in container-engine variant of the OpenClaw path and is still
evidence-gated; it is not a default-promotion claim.

## Differences From README

Compared with the root `README.md`, the SWE-bench Pro path differs in a few important ways:

- It does not use `agent.name: openclaw` or `agent.name: zeroclaw` plus ROCK deployment. It uses `agent.name: swebench_docker` with `agent.config.agent_type: openclaw|opencode|zeroclaw`.
- It requires upstream SWE-bench Pro evaluator assets via `SWE_BENCH_PRO_EVAL_SCRIPT` and `SWE_BENCH_PRO_SCRIPTS_DIR`.
- It runs on the official SWE-bench Pro task image from `jefzda/sweap-images:*`.
- It is a smoke subset run: `subset: smoke` and `max_tasks: 1`.
- The smoke configs ship with `max_concurrent: 1`, but the reproduction commands below override `max_concurrent=10` to match the local validation setup. With `max_tasks: 1`, effective task parallelism is still `1`.
- `OpenCode` may need an explicit runtime-image override when Docker Hub cannot serve `tmlrgroup/alphadiana:opencode`. The code already supports `SWEBENCH_OPENCODE_RUNTIME_IMAGE`.
- `ZeroClaw` may need an explicit runtime-image override when the default runtime image is unavailable. The code supports `SWEBENCH_ZEROCLAW_RUNTIME_IMAGE`.

## Prerequisites

### 1. AlphaDiana environment

Use the normal project environment setup from the root docs, then activate the shell environment before running:

```bash
source scripts/activate.sh
```

### 2. Official evaluator assets

AlphaDiana expects the official SWE-bench Pro evaluator entrypoint and run scripts to exist locally.

Example local checkout:

```bash
export SWE_BENCH_PRO_ROOT=/path/to/SWE-bench_Pro-os
git clone https://github.com/scaleapi/SWE-bench_Pro-os "$SWE_BENCH_PRO_ROOT"
```

Required exports:

```bash
export SWE_BENCH_PRO_EVAL_SCRIPT="$SWE_BENCH_PRO_ROOT/swe_bench_pro_eval.py"
export SWE_BENCH_PRO_SCRIPTS_DIR="$SWE_BENCH_PRO_ROOT/run_scripts"
```

### 3. Model endpoint

The local smoke reproductions used an OpenAI-compatible endpoint:

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.example.com/v1/
```

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b
```

Optional but recommended cache settings used in local reproduction:

```bash
export HF_HOME=/tmp/pr29-hf
export HF_DATASETS_CACHE=/tmp/pr29-hf/datasets
export HF_ENDPOINT=https://hf-mirror.com
```

## Validate The Configs

### OpenClaw

```bash
OPENCLAW_SMOKE_MODEL_NAME=minimax-m2.5 \
OPENCLAW_SMOKE_MODEL_CANDIDATES=minimax-m2.5,minimax \
OPENCLAW_AGENT_ID=main \
OPENCLAW_TOOLS_PROFILE=coding \
OPENCLAW_PROMPT_PROFILE=edit_first \
OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000 \
OPENCLAW_REQUIRE_PATCH=1 \
OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT=12 \
OPENCLAW_MAX_NO_EDIT_SECONDS=180 \
OPENCLAW_CONTEXT_WINDOW=128000 \
OPENCLAW_COMPLETION_MAX_TOKENS=4096 \
python -m alphadiana.cli validate configs/examples/swebench_pro_openclaw_smoke.local.yaml \
  -o run_id=swebench-pro-openclaw-smoke-local \
  -o max_concurrent=10
```

### OpenCode

Default runtime source image:

- `tmlrgroup/alphadiana:opencode`

If that image is unavailable from Docker Hub but you already have a local equivalent runtime image, set:

```bash
export SWEBENCH_OPENCODE_RUNTIME_IMAGE=external_benchmark/opencode:latest
```

Validation command:

```bash
SWEBENCH_OPENCODE_RUNTIME_IMAGE=${SWEBENCH_OPENCODE_RUNTIME_IMAGE:-external_benchmark/opencode:latest} \
OPENCODE_SMOKE_MODEL_NAME=minimax \
OPENCODE_SMOKE_MODEL_CANDIDATES=minimax \
OPENCODE_STRATEGY_SEQUENCE=guided_edit_first \
OPENCODE_REQUIRE_PATCH=0 \
OPENCODE_PROMPT_PROFILE=edit_first \
OPENCODE_AUTO_TARGET_HINTS=0 \
OPENCODE_TARGET_FILE_HINTS=src/database/redis/main.js,src/database/mongo/main.js,src/database/postgres/main.js,src/user/email.js \
OPENCODE_PRIMARY_TARGET_FILE=src/database/redis/main.js \
OPENCODE_PROBLEM_STATEMENT_MAX_CHARS=12000 \
OPENCODE_PREFLIGHT_TIMEOUT_SEC=45 \
OPENCODE_STARTUP_TIMEOUT_SEC=180 \
OPENCODE_IDLE_TIMEOUT_SEC=900 \
OPENCODE_IDLE_POLL_SEC=15 \
OPENCODE_MAX_ACTIVE_NO_EDIT_SEC=300 \
OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT=24 \
OPENCODE_ACTIVITY_HEARTBEAT_SEC=30 \
python -m alphadiana.cli validate configs/examples/swebench_pro_opencode_smoke.local.yaml \
  -o run_id=swebench-pro-opencode-smoke-local \
  -o max_concurrent=10
```

This is the playbook-aligned smoke contract. If you want a stricter patch-convergence debugging path, set `OPENCODE_REQUIRE_PATCH=1` and widen the alias/strategy matrix, but that is not required for a smoke pass.

### ZeroClaw

Default runtime source image:

- `zeroclaw-reasoning:0.6.9`

If you need to override it locally, set:

```bash
export SWEBENCH_ZEROCLAW_RUNTIME_IMAGE=zeroclaw-reasoning:0.6.9
```

The current ZeroClaw overlay copies the `zeroclaw` binary together with the
bundled runtime loader/libs into the official SWE task image and launches it
through a wrapper script. This avoids the older `GLIBC_2.34 not found` failure
without relying on `apt-get install` inside focal-based task images such as the
official `ansible` smoke sample.

Validation command:

```bash
ZEROCLAW_SMOKE_MODEL_NAME=minimax-m2.5 \
ZEROCLAW_SMOKE_MODEL_CANDIDATES=minimax-m2.5,minimax \
ZEROCLAW_TIMEOUT_SEC=1500 \
ZEROCLAW_REQUIRE_PATCH=1 \
ZEROCLAW_PROMPT_PROFILE=edit_first \
ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000 \
ZEROCLAW_WORKSPACE_ONLY=0 \
ZEROCLAW_MAX_TOOL_ITERATIONS=100 \
ZEROCLAW_MAX_ACTIONS_PER_HOUR=200 \
ZEROCLAW_RUNTIME_TRACE_MODE=none \
python -m alphadiana.cli validate configs/examples/swebench_pro_zeroclaw_smoke.local.yaml \
  -o run_id=swebench-pro-zeroclaw-smoke-local \
  -o max_concurrent=10
```

## Run The Smoke Tests

### OpenClaw smoke

```bash
OPENCLAW_SMOKE_MODEL_NAME=minimax-m2.5 \
OPENCLAW_SMOKE_MODEL_CANDIDATES=minimax-m2.5,minimax \
OPENCLAW_AGENT_ID=main \
OPENCLAW_TOOLS_PROFILE=coding \
OPENCLAW_PROMPT_PROFILE=edit_first \
OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000 \
OPENCLAW_REQUIRE_PATCH=1 \
OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT=12 \
OPENCLAW_MAX_NO_EDIT_SECONDS=180 \
OPENCLAW_CONTEXT_WINDOW=128000 \
OPENCLAW_COMPLETION_MAX_TOKENS=4096 \
python -m alphadiana.cli run configs/examples/swebench_pro_openclaw_smoke.local.yaml \
  -o run_id=swebench-pro-openclaw-smoke-local \
  -o max_concurrent=10
```

### OpenCode smoke

```bash
SWEBENCH_OPENCODE_RUNTIME_IMAGE=${SWEBENCH_OPENCODE_RUNTIME_IMAGE:-external_benchmark/opencode:latest} \
OPENCODE_SMOKE_MODEL_NAME=minimax \
OPENCODE_SMOKE_MODEL_CANDIDATES=minimax \
OPENCODE_STRATEGY_SEQUENCE=guided_edit_first \
OPENCODE_REQUIRE_PATCH=0 \
OPENCODE_PROMPT_PROFILE=edit_first \
OPENCODE_AUTO_TARGET_HINTS=0 \
OPENCODE_TARGET_FILE_HINTS=src/database/redis/main.js,src/database/mongo/main.js,src/database/postgres/main.js,src/user/email.js \
OPENCODE_PRIMARY_TARGET_FILE=src/database/redis/main.js \
OPENCODE_PROBLEM_STATEMENT_MAX_CHARS=12000 \
OPENCODE_PREFLIGHT_TIMEOUT_SEC=45 \
OPENCODE_STARTUP_TIMEOUT_SEC=180 \
OPENCODE_IDLE_TIMEOUT_SEC=900 \
OPENCODE_IDLE_POLL_SEC=15 \
OPENCODE_MAX_ACTIVE_NO_EDIT_SEC=300 \
OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT=24 \
OPENCODE_ACTIVITY_HEARTBEAT_SEC=30 \
python -m alphadiana.cli run configs/examples/swebench_pro_opencode_smoke.local.yaml \
  -o run_id=swebench-pro-opencode-smoke-local \
  -o max_concurrent=10
```

### ZeroClaw smoke

```bash
ZEROCLAW_SMOKE_MODEL_NAME=minimax-m2.5 \
ZEROCLAW_SMOKE_MODEL_CANDIDATES=minimax-m2.5,minimax \
ZEROCLAW_TIMEOUT_SEC=1500 \
ZEROCLAW_REQUIRE_PATCH=1 \
ZEROCLAW_PROMPT_PROFILE=edit_first \
ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000 \
ZEROCLAW_WORKSPACE_ONLY=0 \
ZEROCLAW_MAX_TOOL_ITERATIONS=100 \
ZEROCLAW_MAX_ACTIONS_PER_HOUR=200 \
ZEROCLAW_RUNTIME_TRACE_MODE=none \
python -m alphadiana.cli run configs/examples/swebench_pro_zeroclaw_smoke.local.yaml \
  -o run_id=swebench-pro-zeroclaw-smoke-local \
  -o max_concurrent=10
```

For a single-instance smoke rerun without changing the benchmark harness,
override `benchmark.config.instance_ids=<instance_id>`, for example
`-o benchmark.config.instance_ids=instance_qutebrowser__...`.

### Podman OpenClaw smoke status

- the official evaluator checkout was configured and `alphadiana validate`
  passed
- a writable Hugging Face cache was required because the default cache was
  read-only
- the task image
  `jefzda/sweap-images:nodebb.nodebb-NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5`
  pulled successfully
- pulling the default `tmlrgroup/alphadiana:v1` runtime source timed out after
  1200s, so a local validation-only compatible runtime source image was built
  from an existing OpenClaw image
- after image prep, OpenClaw gateway started but the embedded agent returned
  `LLM request timed out` and empty `openclaw_output.jsonl` for
  `openai/gpt-oss-20b`, `qwen/qwen3.5-9b`, and
  `deepseek/deepseek-v4-flash:free`

Do not treat the Podman OpenClaw SWE-bench Pro path as supported until this
provider/runtime blocker is repaired and a task JSON completes without a
top-level `error`. Phase 3 completion does not include a SWE-bench Pro Podman
support claim.

## Expected Results

Use the following smoke pass criteria:

- `results/<run_id>/tasks/<task>.json` exists
- the task record has no error dict
- dashboard shows `O` or `X`, not `-`

For SWE-bench Pro smoke in AlphaDiana, also inspect the root agent artifact directory:

- `patch.diff` should be non-empty if the agent produced a repository edit
- `*_attempt_matrix.json` should preserve alias evidence when retries happen

Interpretation:

- `X` means execution succeeded but the patch did not solve the benchmark task
- that is still a valid smoke pass for infrastructure reproduction
- for `opencode`, an empty patch is acceptable in smoke mode when `OPENCODE_REQUIRE_PATCH=0`; the task JSON should then show `error: null` and a rationale like `Empty patch produced; skipping SWE-bench evaluation.`
- for `zeroclaw`, `ZEROCLAW_REQUIRE_PATCH=1` still keeps patchless attempts
  strict for scoring, but loop-detector, no-edit, and CLI-abort outcomes are
  preserved as auditable task results when artifacts exist; expect
  `error: null`, a non-empty trajectory, and `finish_reason=preserved_failure`
  instead of a task-level hard error

## Where To Inspect Artifacts

Each smoke run produces two useful output trees:

- run results under `results_*`
- agent artifacts under `swebench_artifacts_*`

The PR-specific local validation bundle is organized under:

- `context/pr29-add-swebench-pro/`

## Full Runs

For full benchmark runs, use:

- `configs/full_runs/p29_full_openclaw_swebench_pro.yaml`
- `configs/full_runs/p29_full_opencode_swebench_pro.yaml`
- `configs/full_runs/p29_full_zeroclaw_swebench_pro.yaml`

Validate first:

```bash
python -m alphadiana.cli validate configs/full_runs/p29_full_openclaw_swebench_pro.yaml
python -m alphadiana.cli validate configs/full_runs/p29_full_opencode_swebench_pro.yaml
python -m alphadiana.cli validate configs/full_runs/p29_full_zeroclaw_swebench_pro.yaml
```

Then run:

```bash
python -m alphadiana.cli run configs/full_runs/p29_full_openclaw_swebench_pro.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p29_full_opencode_swebench_pro.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p29_full_zeroclaw_swebench_pro.yaml --redo-all
```

`directLLM` full runs remain outside Diana. Use the official `scaleapi/SWE-bench_Pro-os` repository for that path.
