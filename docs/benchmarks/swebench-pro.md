# SWE-bench Pro Reproduction Guide

This guide covers the AlphaDiana paths that were actually exercised for SWE-bench Pro smoke reproduction in this repo:

- `openclaw` via `swebench_docker`
- `opencode` via `swebench_docker`

`directLLM` is intentionally not documented here as a Diana execution path. For the direct-LLM baseline on SWE-bench Pro, use the official repository instead:

- `https://github.com/scaleapi/SWE-bench_Pro-os`

The official repo provides the canonical patch gathering and evaluation flow around `swe_bench_pro_eval.py`, `run_scripts/`, and its own harness guidance.

## Scope

AlphaDiana does not use the ROCK/AIME flow from the root [README.md](/path/to/users/xxx/projects/diana/AlphaDiana-dev/README.md) for SWE-bench Pro smoke. Instead, it:

- runs `benchmark.name: swebench_pro_os`
- uses `agent.name: swebench_docker`
- injects the agent runtime into the official SWE task image
- calls the official evaluator assets through `scorer.name: swebench_pro`

Shipped configs:

- `configs/examples/swebench_pro_openclaw_smoke.local.yaml`
- `configs/examples/swebench_pro_opencode_smoke.local.yaml`
- `configs/examples/swebench_pro_direct_llm_smoke.local.yaml`
- `configs/full_runs/p29_full_openclaw_swebench_pro.yaml`
- `configs/full_runs/p29_full_opencode_swebench_pro.yaml`

Only the first two are part of the AlphaDiana reproduction path documented here.
The two `configs/full_runs/` files are the full benchmark entry points for the Diana-backed paths.

## Differences From README

Compared with the root `README.md`, the SWE-bench Pro path differs in a few important ways:

- It does not use `agent.name: openclaw` plus ROCK deployment. It uses `agent.name: swebench_docker` with `agent.config.agent_type: openclaw|opencode`.
- It requires upstream SWE-bench Pro evaluator assets via `SWE_BENCH_PRO_EVAL_SCRIPT` and `SWE_BENCH_PRO_SCRIPTS_DIR`.
- It runs on the official SWE-bench Pro task image from `jefzda/sweap-images:*`.
- It is a smoke subset run: `subset: smoke` and `max_tasks: 1`.
- The smoke configs ship with `max_concurrent: 1`, but the reproduction commands below override `max_concurrent=10` to match the local validation setup. With `max_tasks: 1`, effective task parallelism is still `1`.
- `OpenCode` may need an explicit runtime-image override when Docker Hub cannot serve `tmlrgroup/alphadiana:opencode`. The code already supports `SWEBENCH_OPENCODE_RUNTIME_IMAGE`.

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
git clone https://github.com/scaleapi/SWE-bench_Pro-os /tmp/phase11-swebench-pro-os-full
```

Required exports:

```bash
export SWE_BENCH_PRO_EVAL_SCRIPT=/tmp/phase11-swebench-pro-os-full/swe_bench_pro_eval.py
export SWE_BENCH_PRO_SCRIPTS_DIR=/tmp/phase11-swebench-pro-os-full/run_scripts
```

### 3. Model endpoint

The local smoke reproductions used an OpenAI-compatible endpoint:

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.example.com/v1/
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

This is the exact user-facing smoke recipe that was validated locally on `2026-04-17`:

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

## Expected Results

Use the following smoke pass criteria:

- `results/<run_id>/tasks/<task>.json` exists
- the task record has no error dict
- dashboard shows `O` or `X`, not `-`

For SWE-bench Pro smoke in AlphaDiana, also inspect the root agent artifact directory:

- `patch.diff` should be non-empty if the agent produced a repository edit
- `*_attempt_matrix.json` should preserve alias/strategy evidence when retries happen

Interpretation:

- `X` means execution succeeded but the patch did not solve the benchmark task
- that is still a valid smoke pass for infrastructure reproduction
- for `opencode`, an empty patch is acceptable in smoke mode when `OPENCODE_REQUIRE_PATCH=0`; the task JSON should then show `error: null` and a rationale like `Empty patch produced; skipping SWE-bench evaluation.`

## Local Verified Outcomes

These are the outcomes actually observed during local validation on `2026-04-17`.

- `openclaw`: `minimax-m2.5`, dashboard `X`, task JSON `error=None`, root `patch.diff` size `4485` bytes
- `opencode`: `minimax` with `guided_edit_first`, dashboard `X`, task JSON `error=None`, root `patch.diff` absent, selected attempt classified as `active_session_no_patch`
- reviewer-facing summary: `context/pr29-add-swebench-pro/smoke-validation.md`
- compact matrix: `context/pr29-add-swebench-pro/status-matrix.md`

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

Validate first:

```bash
python -m alphadiana.cli validate configs/full_runs/p29_full_openclaw_swebench_pro.yaml
python -m alphadiana.cli validate configs/full_runs/p29_full_opencode_swebench_pro.yaml
```

Then run:

```bash
python -m alphadiana.cli run configs/full_runs/p29_full_openclaw_swebench_pro.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p29_full_opencode_swebench_pro.yaml --redo-all
```

`directLLM` full runs remain outside Diana. Use the official `scaleapi/SWE-bench_Pro-os` repository for that path.
