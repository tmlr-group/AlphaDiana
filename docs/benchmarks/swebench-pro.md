# SWE-bench Pro Reproduction Guide

Related references kept outside this folder:

- legacy SWE-bench Verified/container reproduction note:
  `docs/benchmarks/swebench-verified.md`
- older Verified/container internal note:
  `context/pr26-swebench-verified/implementation-notes.md`

This guide covers the AlphaDiana paths that were actually exercised for SWE-bench Pro smoke reproduction in this repo:

- `openclaw` via `swebench_docker`
- `opencode` via `swebench_docker`

`directLLM` is intentionally not documented here as a Diana execution path. For
the direct-LLM baseline on SWE-bench Pro, use the official repository instead:

- `https://github.com/scaleapi/SWE-bench_Pro-os`

The official repo provides the canonical patch gathering and evaluation flow
around `swe_bench_pro_eval.py`, `run_scripts/`, and its own harness guidance.
The April 19, 2026 OpenRouter/Qwen direct-LLM evidence below is for that
official path, not for a Diana-managed execution mode.

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

OpenRouter/Qwen pilot status on April 19, 2026:

- `directLLM` official SWE-agent follow-up:
  the repaired official-checkout archive
  `pilot_20260419_qwen35_27b_swebench_pro_directllm_t3_repair_r1`
  now has `3/3` accepted normal trajectories on the selected smoke subset,
  assembled from
  `pilot_20260419_qwen35_27b_swebench_pro_directllm_nodebb_emailstatus_r5`,
  `pilot_20260419_qwen35_27b_swebench_pro_directllm_nodebb_webfinger_r5`, and
  `pilot_20260419_qwen35_27b_swebench_pro_directllm_qutebrowser_qtlog_r6`.
  Treat this as repaired trajectory-health evidence, not a correctness claim
  or a stock upstream invocation.
- `opencode`: `3/3` normal task records written on the smoke subset, all
  `score=0`
- `openclaw`: smoke-valid on the canonical `r4` rerun with `3/3` normal task
  records, all `score=0`

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

For the April 19, 2026 OpenRouter/Qwen pilot:

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

## Official DirectLLM Follow-Up

This section documents the official `scaleapi/SWE-bench_Pro-os` path used for
the April 19 OpenRouter/Qwen smoke follow-up. It is intentionally separate from
the AlphaDiana `swebench_docker` modes.

Representative single-instance command shape from the official repo:

```bash
cd /path/to/SWE-bench_Pro-os/SWE-agent
unset ALL_PROXY all_proxy HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
export OPENAI_API_KEY=...

sweagent run-batch \
  --config config/tool_use.yaml \
  --output_dir ../sweagent_results/<run_id> \
  --num_workers 1 \
  --random_delay_multiplier 0 \
  --instances.type expert_file \
  --instances.path ../pilot_data/<expert_instances_file>.yaml \
  --instances.filter '^<instance_id>$' \
  --instances.deployment.type docker \
  --instances.deployment.startup_timeout 1800 \
  --agent.model.name openrouter/qwen/qwen3.5-27b \
  --agent.model.api_base https://openrouter.ai/api/v1 \
  --agent.model.api_key '$OPENAI_API_KEY' \
  --agent.model.temperature 0.6 \
  --agent.model.top_p 0.95 \
  --agent.model.max_output_tokens 32768 \
  --agent.model.per_instance_cost_limit 0 \
  --agent.model.per_instance_call_limit 20 \
  --progress_bar False
```

The repaired OpenRouter/Qwen follow-up ultimately used three single-instance
source runs:

- `pilot_20260419_qwen35_27b_swebench_pro_directllm_nodebb_emailstatus_r5`
- `pilot_20260419_qwen35_27b_swebench_pro_directllm_nodebb_webfinger_r5`
- `pilot_20260419_qwen35_27b_swebench_pro_directllm_qutebrowser_qtlog_r6`

The accepted archive combining the manually audited normal trajectories is:

- `pilot_20260419_qwen35_27b_swebench_pro_directllm_t3_repair_r1`

Additional local fixes required inside the official checkout before the repaired
`r5/r6` reruns:

- apply the shipped `SWE-agent/swerex_patches/patch.py --yes`
- patch installed `.venv/.../swerex/deployment/docker.py` to use `/bin/bash`
  entrypoint, `pip --target /tmp/swerex-site swe-rex==1.4.0` on Python 3.11
  images, and the official standalone-Python build path only for the Python 3.9
  NodeBB image
- patch `SWE-agent/sweagent/tools/tools.py` for OpenRouter/Qwen tool-call
  compatibility
- patch `SWE-agent/sweagent/agent/models.py` to suppress cost-accounting noise
  from missing usage fields
- add `SWE-agent/tools/registry/lib/registry.py`, because the default
  `edit_anthropic` bundle imported `registry` but the official checkout did not
  ship the Python module
- install `socksio` into the official `.venv`
- set `git config --global core.pager cat` in the runtime to avoid pager hangs

Observed outcome on the repaired official direct-LLM follow-up:

- all three source runs autosubmitted cleanly and preserved trajectories
- `pilot_20260419_qwen35_27b_swebench_pro_directllm_nodebb_emailstatus_r5`:
  normal trajectory; observed `ECONNREFUSED` only inside agent debugging/test
  attempts
- `pilot_20260419_qwen35_27b_swebench_pro_directllm_nodebb_webfinger_r5`:
  normal trajectory; no framework-level formatting, cost, or runtime anomalies
- `pilot_20260419_qwen35_27b_swebench_pro_directllm_qutebrowser_qtlog_r6`:
  normal trajectory; one traceback came from an agent-authored validation
  script, not from the harness
- the accepted archive
  `pilot_20260419_qwen35_27b_swebench_pro_directllm_t3_repair_r1`
  was uploaded to `T-MARS/alphadiana-benchmark-results` under
  `pilot_run/pilot_20260419_qwen35_27b_swebench_pro_directllm_t3_repair_r1/`
- the accepted gate for this repaired follow-up was trajectory health, not task
  correctness

Treat this official direct-LLM path as smoke-valid for trajectory integrity on
OpenRouter/Qwen after the local official-checkout fixes. It is still not a
correctness claim and should not be treated as a stock upstream invocation.

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

## April 19 OpenRouter/Qwen 3-Task Pilot

The April 19 pilot reused the checked-in smoke YAMLs and overrode the task
count, run IDs, and output directories at the CLI.

Shared additional environment:

```bash
export SWE_BENCH_PRO_ROOT=/path/to/SWE-bench_Pro-os
export SWE_BENCH_PRO_EVAL_SCRIPT="$SWE_BENCH_PRO_ROOT/swe_bench_pro_eval.py"
export SWE_BENCH_PRO_SCRIPTS_DIR="$SWE_BENCH_PRO_ROOT/run_scripts"
export SWEBENCH_OPENCODE_RUNTIME_IMAGE=${SWEBENCH_OPENCODE_RUNTIME_IMAGE:-external_benchmark/opencode:latest}
```

### OpenClaw

Validation:

```bash
OPENCLAW_SMOKE_MODEL_NAME=qwen/qwen3.5-27b \
OPENCLAW_SMOKE_MODEL_CANDIDATES=qwen/qwen3.5-27b \
OPENCLAW_AGENT_ID=main \
OPENCLAW_TOOLS_PROFILE=coding \
OPENCLAW_PROMPT_PROFILE=edit_first \
OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000 \
OPENCLAW_REQUIRE_PATCH=1 \
OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT=12 \
OPENCLAW_MAX_NO_EDIT_SECONDS=180 \
OPENCLAW_CONTEXT_WINDOW=32768 \
OPENCLAW_COMPLETION_MAX_TOKENS=4096 \
python -m alphadiana.cli validate configs/examples/swebench_pro_openclaw_smoke.local.yaml \
  -o run_id=pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3 \
  -o output_dir=./results \
  -o agent.config.output_dir=./swebench_artifacts/pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3 \
  -o benchmark.config.max_tasks=3 \
  -o max_concurrent=2
```

Initial run:

```bash
OPENCLAW_SMOKE_MODEL_NAME=qwen/qwen3.5-27b \
OPENCLAW_SMOKE_MODEL_CANDIDATES=qwen/qwen3.5-27b \
OPENCLAW_AGENT_ID=main \
OPENCLAW_TOOLS_PROFILE=coding \
OPENCLAW_PROMPT_PROFILE=edit_first \
OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000 \
OPENCLAW_REQUIRE_PATCH=1 \
OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT=12 \
OPENCLAW_MAX_NO_EDIT_SECONDS=180 \
OPENCLAW_CONTEXT_WINDOW=32768 \
OPENCLAW_COMPLETION_MAX_TOKENS=4096 \
python -m alphadiana.cli run configs/examples/swebench_pro_openclaw_smoke.local.yaml \
  -o run_id=pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3 \
  -o output_dir=./results \
  -o agent.config.output_dir=./swebench_artifacts/pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3 \
  -o benchmark.config.max_tasks=3 \
  -o max_concurrent=2 \
  2>&1 | tee logs/pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3.log
```

Current branch fix and rerun:

- `alphadiana/agent/swebench_assets/run_openclaw.sh` no longer depends on
  `curl`; it uses `python3` and `urllib.request` for the gateway readiness
  probe and the streaming request path
- `alphadiana/agent/swebench_docker.py` now falls back from `docker stop`
  timeout to `docker rm -f` during best-effort cleanup
- after those fixes, the canonical rerun was
  `pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3_r4`

```bash
OPENCLAW_SMOKE_MODEL_NAME=qwen/qwen3.5-27b \
OPENCLAW_SMOKE_MODEL_CANDIDATES=qwen/qwen3.5-27b \
OPENCLAW_AGENT_ID=main \
OPENCLAW_TOOLS_PROFILE=coding \
OPENCLAW_PROMPT_PROFILE=edit_first \
OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000 \
OPENCLAW_REQUIRE_PATCH=1 \
OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT=12 \
OPENCLAW_MAX_NO_EDIT_SECONDS=180 \
OPENCLAW_CONTEXT_WINDOW=32768 \
OPENCLAW_COMPLETION_MAX_TOKENS=4096 \
python -m alphadiana.cli run configs/examples/swebench_pro_openclaw_smoke.local.yaml \
  -o run_id=pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3_r4 \
  -o output_dir=./results \
  -o agent.config.output_dir=./swebench_artifacts/pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3_r4 \
  -o benchmark.config.max_tasks=3 \
  -o max_concurrent=2 \
  2>&1 | tee logs/pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3_r4.log
```

Observed result:

- initial run `pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3` exposed
  `provider_failure` with `curl exited with status 127`
- canonical rerun `pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3_r4`
  wrote `3/3` normal task records, all `error=None`, all `score=0`
- all three canonical rerun task artifact roots include
  `trajectory.jsonl`, `openclaw_session.jsonl`, `openclaw_output.jsonl`,
  `openclaw_selected_attempt.json`, and non-empty `patch.diff`

### OpenCode

Validation:

```bash
SWEBENCH_OPENCODE_RUNTIME_IMAGE=${SWEBENCH_OPENCODE_RUNTIME_IMAGE:-external_benchmark/opencode:latest} \
OPENCODE_SMOKE_MODEL_NAME=qwen/qwen3.5-27b \
OPENCODE_SMOKE_MODEL_CANDIDATES=qwen/qwen3.5-27b \
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
  -o run_id=pilot_20260419_qwen35_27b_swebench_pro_opencode_t3 \
  -o output_dir=./results \
  -o agent.config.output_dir=./swebench_artifacts/pilot_20260419_qwen35_27b_swebench_pro_opencode_t3 \
  -o benchmark.config.max_tasks=3 \
  -o max_concurrent=2
```

Run:

```bash
SWEBENCH_OPENCODE_RUNTIME_IMAGE=${SWEBENCH_OPENCODE_RUNTIME_IMAGE:-external_benchmark/opencode:latest} \
OPENCODE_SMOKE_MODEL_NAME=qwen/qwen3.5-27b \
OPENCODE_SMOKE_MODEL_CANDIDATES=qwen/qwen3.5-27b \
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
  -o run_id=pilot_20260419_qwen35_27b_swebench_pro_opencode_t3 \
  -o output_dir=./results \
  -o agent.config.output_dir=./swebench_artifacts/pilot_20260419_qwen35_27b_swebench_pro_opencode_t3 \
  -o benchmark.config.max_tasks=3 \
  -o max_concurrent=2 \
  2>&1 | tee logs/pilot_20260419_qwen35_27b_swebench_pro_opencode_t3.log
```

Observed result:

- `pilot_20260419_qwen35_27b_swebench_pro_opencode_t3` wrote `3/3` normal task
  records, all `error=None`, all `score=0`
- `NodeBB` preserved a no-edit explanation after `24` tool calls with no tracked
  repository edits
- `ansible` and `qutebrowser` both produced non-empty `patch.diff` artifacts but
  still scored `0`

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

Additional Qwen/OpenRouter outcomes observed on `2026-04-19`:

- `directLLM` official repaired follow-up:
  `pilot_20260419_qwen35_27b_swebench_pro_directllm_t3_repair_r1`,
  `3/3` accepted normal trajectories after local official-checkout fixes
- `opencode`: `pilot_20260419_qwen35_27b_swebench_pro_opencode_t3`,
  `3/3` task records, all `score=0`, all `error=None`
- `openclaw` initial run:
  `pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3`,
  pre-fix `provider_failure` because the runtime image lacked `curl`
- `openclaw` canonical rerun:
  `pilot_20260419_qwen35_27b_swebench_pro_openclaw_t3_r4`,
  `3/3` normal task records, all `score=0`
- reviewer-facing summary:
  `context/qwen-openrouter-pilots/pilot-validation.md`

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
