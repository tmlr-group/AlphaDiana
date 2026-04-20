# ZeroClaw Qwen OpenRouter Pilot Matrix (2026-04-19)

## Scope

This runbook freezes the `Qwen/Qwen3.5-27B × zeroclaw` pilot matrix requested
for `2026-04-19`.

- Backend: `https://openrouter.ai/api/v1/`
- Model alias requested by the user: `Qwen/Qwen3.5-27B`
- Harness: `zeroclaw`
- Benchmarks:
  - `terminal-bench-2`
  - `SWE-bench Pro`
  - `MMMU-Pro`
  - `IMO-AnswerBench`
  - `GPQA-Diamond`
  - `HLE`
- Pilot size: `3` distinct tasks per benchmark
- No local vLLM host
- Global run concurrency cap: `5`

This pilot uses `max_tasks=3` and `num_samples=1` so each path covers three
distinct benchmark tasks instead of three repeated samples of one task.

## Preflight

Run from the repo root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL='https://openrouter.ai/api/v1/'
export OPENAI_API_KEY='sk-or-v1-...'
export OPENAI_MODEL_NAME='Qwen/Qwen3.5-27B'

export TERMINAL_BENCH2_SMOKE_DIR=/tmp/terminal-bench-2-smoke-qwen-t3
export SWE_BENCH_PRO_ROOT=/path/to/SWE-bench_Pro-os
export SWE_BENCH_PRO_EVAL_SCRIPT="$SWE_BENCH_PRO_ROOT/swe_bench_pro_eval.py"
export SWE_BENCH_PRO_SCRIPTS_DIR="$SWE_BENCH_PRO_ROOT/run_scripts"

mkdir -p logs
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh
```

Observed local preflight on `2026-04-19`:

- `zeroclaw-reasoning:0.6.9` image exists locally
- `alphadiana/tb2-zeroclaw-controller:latest` exists locally
- dataset loads succeeded for `IMO-AnswerBench`, `GPQA-Diamond`, `MMMU-Pro`,
  and cached `HLE`
- a live OpenRouter probe accepted model alias `Qwen/Qwen3.5-27B` and returned
  a normal chat completion, normalized by the provider to
  `qwen/qwen3.5-27b-20260224`

## Config Notes

- `IMO-AnswerBench`, `GPQA-Diamond`, `MMMU-Pro`, and `HLE` run in ROCK
  sandboxes with in-sandbox ZeroClaw CLI by forcing
  `agent.config.use_gateway_in_sandbox=false`.
- `terminal-bench-2` still needs explicit CLI overrides because
  `configs/examples/terminal_bench2_zeroclaw_minimax.yaml` pins the MiniMax
  model in YAML.
- `SWE-bench Pro` runs via `agent.name: swebench_docker` with
  `agent.config.agent_type: zeroclaw`.

Current harness-gap note for this worktree:

- the standard benchmark `zeroclaw` wrapper now forwards `temperature`,
  `provider_timeout_secs`, `provider_max_tokens`, `reasoning_enabled`, and
  `reasoning_effort`
- the `swebench_docker` ZeroClaw path now forwards the same controls through
  the inner `run_zeroclaw.sh` template
- explicit `top_p` and an explicit `stream=true` knob are still unsupported on
  the checked-in ZeroClaw path
- ZeroClaw `0.6.9` still prints a false warning
  `Unknown config key ignored: "provider_max_tokens"` even when the value is
  accepted by the schema and visible in `props list`

Per the rollout plan, these unsupported frozen semantics should be recorded as
pilot evidence instead of assumed covered.

## Wave 1 Commands

Launch five runs first. Each command is intended for its own `tmux` session and
writes the raw shell log to `logs/<run_id>.log`.

### IMO-AnswerBench

```bash
tmux new -d -s pilot-zc-imo "bash -lc '
  cd $PWD
  source scripts/activate.sh
  export PYTHONPATH=$PWD
  export OPENAI_BASE_URL='\''https://openrouter.ai/api/v1/'\''
  export OPENAI_API_KEY='\''sk-or-v1-...'\'' 
  export OPENAI_MODEL_NAME='\''Qwen/Qwen3.5-27B'\''
  source scripts/rock_env.sh
  python -m alphadiana.cli run configs/examples/zeroclaw_imo_answerbench.yaml \
    -o run_id=pr23_qwen_openrouter_zeroclaw_imo_t3_20260419 \
    -o output_dir=./results/pr23_qwen_openrouter_zeroclaw_pilot_20260419 \
    -o benchmark.config.max_tasks=3 \
    -o num_samples=1 \
    -o max_concurrent=1 \
    -o agent.config.temperature=0.6 \
    -o agent.config.use_gateway_in_sandbox=false \
    2>&1 | tee logs/pr23_qwen_openrouter_zeroclaw_imo_t3_20260419.log
'"
```

### GPQA-Diamond

```bash
tmux new -d -s pilot-zc-gpqa "bash -lc '
  cd $PWD
  source scripts/activate.sh
  export PYTHONPATH=$PWD
  export OPENAI_BASE_URL='\''https://openrouter.ai/api/v1/'\''
  export OPENAI_API_KEY='\''sk-or-v1-...'\'' 
  export OPENAI_MODEL_NAME='\''Qwen/Qwen3.5-27B'\''
  source scripts/rock_env.sh
  python -m alphadiana.cli run configs/examples/zeroclaw_gpqa_diamond.yaml \
    -o run_id=pr23_qwen_openrouter_zeroclaw_gpqa_t3_20260419 \
    -o output_dir=./results/pr23_qwen_openrouter_zeroclaw_pilot_20260419 \
    -o benchmark.config.max_tasks=3 \
    -o num_samples=1 \
    -o max_concurrent=1 \
    -o agent.config.temperature=0.6 \
    -o agent.config.use_gateway_in_sandbox=false \
    2>&1 | tee logs/pr23_qwen_openrouter_zeroclaw_gpqa_t3_20260419.log
'"
```

### MMMU-Pro

```bash
tmux new -d -s pilot-zc-mmmu "bash -lc '
  cd $PWD
  source scripts/activate.sh
  export PYTHONPATH=$PWD
  export OPENAI_BASE_URL='\''https://openrouter.ai/api/v1/'\''
  export OPENAI_API_KEY='\''sk-or-v1-...'\'' 
  export OPENAI_MODEL_NAME='\''Qwen/Qwen3.5-27B'\''
  source scripts/rock_env.sh
  python -m alphadiana.cli run configs/examples/zeroclaw_mmmu_pro.yaml \
    -o run_id=pr23_qwen_openrouter_zeroclaw_mmmu_t3_20260419 \
    -o output_dir=./results/pr23_qwen_openrouter_zeroclaw_pilot_20260419 \
    -o benchmark.config.max_tasks=3 \
    -o num_samples=1 \
    -o max_concurrent=1 \
    -o agent.config.temperature=0.6 \
    -o agent.config.use_gateway_in_sandbox=false \
    2>&1 | tee logs/pr23_qwen_openrouter_zeroclaw_mmmu_t3_20260419.log
'"
```

### HLE

```bash
tmux new -d -s pilot-zc-hle "bash -lc '
  cd $PWD
  source scripts/activate.sh
  export PYTHONPATH=$PWD
  export OPENAI_BASE_URL='\''https://openrouter.ai/api/v1/'\''
  export OPENAI_API_KEY='\''sk-or-v1-...'\'' 
  export OPENAI_MODEL_NAME='\''Qwen/Qwen3.5-27B'\''
  source scripts/rock_env.sh
  python -m alphadiana.cli run configs/examples/zeroclaw_hle.yaml \
    -o run_id=pr23_qwen_openrouter_zeroclaw_hle_t3_20260419 \
    -o output_dir=./results/pr23_qwen_openrouter_zeroclaw_pilot_20260419 \
    -o benchmark.config.max_tasks=3 \
    -o num_samples=1 \
    -o max_concurrent=1 \
    -o agent.config.temperature=0.6 \
    -o agent.config.use_gateway_in_sandbox=false \
    -o agent.config.max_tool_iterations=12 \
    2>&1 | tee logs/pr23_qwen_openrouter_zeroclaw_hle_t3_20260419.log
'"
```

### terminal-bench-2

```bash
tmux new -d -s pilot-zc-tb2 "bash -lc '
  cd $PWD
  source scripts/activate.sh
  export PYTHONPATH=$PWD
  export OPENAI_BASE_URL='\''https://openrouter.ai/api/v1/'\''
  export OPENAI_API_KEY='\''sk-or-v1-...'\'' 
  export OPENAI_MODEL_NAME='\''Qwen/Qwen3.5-27B'\''
  export TERMINAL_BENCH2_SMOKE_DIR=/tmp/terminal-bench-2-smoke-qwen-t3
  export TMPDIR=/tmp/pr23-qwen-openrouter-tb2
  mkdir -p \"$TMPDIR\"
  python -m alphadiana.cli run configs/examples/terminal_bench2_zeroclaw_minimax.yaml \
    -o run_id=pr23_qwen_openrouter_zeroclaw_tb2_t3_20260419 \
    -o output_dir=./results/pr23_qwen_openrouter_zeroclaw_pilot_20260419 \
    -o benchmark.config.tasks_dir=/tmp/terminal-bench-2-smoke-qwen-t3 \
    -o benchmark.config.max_tasks=3 \
    -o num_samples=1 \
    -o max_concurrent=1 \
    -o agent.config.model='\''Qwen/Qwen3.5-27B'\'' \
    -o agent.config.api_base='\''https://openrouter.ai/api/v1/'\'' \
    -o agent.config.api_key='\''sk-or-v1-...'\'' \
    -o agent.config.temperature=0.6 \
    -o agent.config.logs_base_dir=/tmp/pr23-qwen-openrouter-tb2/tb2_logs \
    2>&1 | tee logs/pr23_qwen_openrouter_zeroclaw_tb2_t3_20260419.log
'"
```

## Wave 2 Command

Launch this only after one Wave 1 session exits so the global benchmark-process
concurrency stays at `5`.

### SWE-bench Pro

```bash
tmux new -d -s pilot-zc-swe "bash -lc '
  cd $PWD
  source scripts/activate.sh
  export PYTHONPATH=$PWD
  export OPENAI_BASE_URL='\''https://openrouter.ai/api/v1/'\''
  export OPENAI_API_KEY='\''sk-or-v1-...'\'' 
  export OPENAI_MODEL_NAME='\''Qwen/Qwen3.5-27B'\''
  export ZEROCLAW_SMOKE_MODEL_NAME='\''Qwen/Qwen3.5-27B'\''
  export ZEROCLAW_SMOKE_MODEL_CANDIDATES='\''Qwen/Qwen3.5-27B'\''
  export ZEROCLAW_TEMPERATURE=0.6
  export ZEROCLAW_TIMEOUT_SEC=1500
  export ZEROCLAW_REQUIRE_PATCH=1
  export ZEROCLAW_PROMPT_PROFILE=edit_first
  export ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000
  export ZEROCLAW_WORKSPACE_ONLY=0
  export ZEROCLAW_MAX_TOOL_ITERATIONS=100
  export ZEROCLAW_MAX_ACTIONS_PER_HOUR=200
  export ZEROCLAW_RUNTIME_TRACE_MODE=none
  export SWE_BENCH_PRO_EVAL_SCRIPT="$SWE_BENCH_PRO_ROOT/swe_bench_pro_eval.py"
  export SWE_BENCH_PRO_SCRIPTS_DIR="$SWE_BENCH_PRO_ROOT/run_scripts"
  python -m alphadiana.cli run configs/examples/swebench_pro_zeroclaw_smoke.local.yaml \
    -o run_id=pr23_qwen_openrouter_zeroclaw_swebench_pro_t3_20260419 \
    -o output_dir=./results/pr23_qwen_openrouter_zeroclaw_pilot_20260419 \
    -o benchmark.config.max_tasks=3 \
    -o num_samples=1 \
    -o max_concurrent=1 \
    2>&1 | tee logs/pr23_qwen_openrouter_zeroclaw_swebench_pro_t3_20260419.log
'"
```

## Monitoring

```bash
tmux ls | rg '^pilot-zc-'
tail -n 80 logs/pr23_qwen_openrouter_zeroclaw_imo_t3_20260419.log
tail -n 80 logs/pr23_qwen_openrouter_zeroclaw_swebench_pro_t3_20260419.log
```

After completion, inspect:

- `results/pr23_qwen_openrouter_zeroclaw_pilot_20260419/<run_id>/status/dashboard.txt`
- `results/pr23_qwen_openrouter_zeroclaw_pilot_20260419/<run_id>/tasks/*.json`
- `logs/<run_id>.log`

## Observed Outcome

Observed on `2026-04-19` after the initial six pilot runs completed:

- `GPQA-Diamond`: `O/O/O`, `accuracy=1.0000`
- `HLE`: `O/O/O`, `accuracy=1.0000`
- `MMMU-Pro`: `X/X/X`, `accuracy=0.0000`
- `IMO-AnswerBench`: `X/X/X`, `accuracy=0.0000`
- `terminal-bench-2`: `X/X/X`, `accuracy=0.0000`
- `SWE-bench Pro`: one scored task at `score=0.0`, two task-level errors

Important failure evidence captured by the pilot:

- `SWE-bench Pro` `NodeBB` task aborted because the ZeroClaw loop detector
  fired on repeated `content_search` results
- `SWE-bench Pro` `ansible` task failed while building the runtime image
  because the focal base image does not provide `libssl3`

## Scaffolding Repair Follow-Up

Observed later on `2026-04-19` after the scaffolding-only repair:

- `terminal-bench-2` did not require a code-path repair. Re-audit of the
  original run showed all three task JSONs already had `trajectory_len=2`,
  archived artifacts, and no task-level `error`. The earlier withholding was
  an audit mistake caused by treating a nonzero solver returncode as abnormal
  even when the task result was fully preserved.
- `SWE-bench Pro` required a real repair run. The targeted rerun
  `pr23_qwen_openrouter_zeroclaw_swebench_pro_t3_repair_20260419` under
  `results/pr23_qwen_openrouter_zeroclaw_repair_20260419` finished `X/X/X`.
  All three task JSONs now have non-empty trajectories and `error=null`.
  `NodeBB` and `ansible` are preserved as `finish_reason=preserved_failure`
  instead of task-level hard errors.

Scaffolding changes validated by that repair:

- the SWE ZeroClaw runtime overlay no longer tries to install `libssl3`, so
  focal-based task images can build the runtime wrapper
- the `swebench_docker` ZeroClaw path now preserves CLI-abort and no-edit
  attempts as auditable task results when artifacts were captured

Repair rerun command used:

```bash
tmux new -d -s rerun-zc-swe "bash -lc '
  cd $PWD
  source scripts/activate.sh
  export PYTHONPATH=$PWD
  export OPENAI_BASE_URL='\''https://openrouter.ai/api/v1/'\''
  export OPENAI_API_KEY='\''sk-or-v1-...'\'' 
  export OPENAI_MODEL_NAME='\''Qwen/Qwen3.5-27B'\''
  export ZEROCLAW_SMOKE_MODEL_NAME='\''Qwen/Qwen3.5-27B'\''
  export ZEROCLAW_SMOKE_MODEL_CANDIDATES='\''Qwen/Qwen3.5-27B'\''
  export ZEROCLAW_TEMPERATURE=0.6
  export ZEROCLAW_TIMEOUT_SEC=1500
  export ZEROCLAW_REQUIRE_PATCH=1
  export ZEROCLAW_PROMPT_PROFILE=edit_first
  export ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000
  export ZEROCLAW_WORKSPACE_ONLY=0
  export ZEROCLAW_MAX_TOOL_ITERATIONS=100
  export ZEROCLAW_MAX_ACTIONS_PER_HOUR=200
  export ZEROCLAW_RUNTIME_TRACE_MODE=none
  export SWE_BENCH_PRO_EVAL_SCRIPT="$SWE_BENCH_PRO_ROOT/swe_bench_pro_eval.py"
  export SWE_BENCH_PRO_SCRIPTS_DIR="$SWE_BENCH_PRO_ROOT/run_scripts"
  python -m alphadiana.cli run configs/examples/swebench_pro_zeroclaw_smoke.local.yaml \
    -o run_id=pr23_qwen_openrouter_zeroclaw_swebench_pro_t3_repair_20260419 \
    -o output_dir=./results/pr23_qwen_openrouter_zeroclaw_repair_20260419 \
    -o benchmark.config.max_tasks=3 \
    -o num_samples=1 \
    -o max_concurrent=1 \
    2>&1 | tee logs/pr23_qwen_openrouter_zeroclaw_swebench_pro_t3_repair_20260419.log
'"
```

## Post-Run Audit And Archive

Observed on `2026-04-19` after corrected trajectory audit plus the SWE repair
rerun:

- uploaded to HF dataset repo `T-MARS/alphadiana-benchmark-results`:
  - `pilot_run/pr23_qwen_openrouter_zeroclaw_gpqa_t3_20260419/`
  - `pilot_run/pr23_qwen_openrouter_zeroclaw_hle_t3_20260419/`
  - `pilot_run/pr23_qwen_openrouter_zeroclaw_imo_t3_20260419/`
  - `pilot_run/pr23_qwen_openrouter_zeroclaw_mmmu_t3_20260419/`
  - `pilot_run/pr23_qwen_openrouter_zeroclaw_tb2_t3_20260419/`
  - `pilot_run/pr23_qwen_openrouter_zeroclaw_swebench_pro_t3_repair_20260419/`
- withheld from HF:
  - `pr23_qwen_openrouter_zeroclaw_swebench_pro_t3_20260419`
    because the initial pilot still contains task-level errors for `NodeBB` and
    `ansible`
  - `pr23_qwen_openrouter_zeroclaw_tb2_t3_repair_20260419`
    because the repair rerun was stopped after re-audit confirmed the original
    TB2 pilot already met the `3/3 normal trajectories` gate

Remote archival commit after the final upload:

- `785bd139c300bb74a56fd1172111bca20309b6ea`

## 2026-04-20 Pending-Item Recheck

The three `zeroclaw` items that were still pending in the later benchmark
table were rerun on `2026-04-20` with the real OpenRouter API. The accepted
v2-style rerun suffix used for this follow-up is `repair_r2`.

- `terminal-bench-2` rerun:
  `pilot_20260420_qwen35_27b_terminal_bench2_zeroclaw_t3_repair_r2`
  completed with `3/3` normal trajectories and was uploaded to
  `pilot_run/pilot_20260420_qwen35_27b_terminal_bench2_zeroclaw_t3_repair_r2/`
  but this archive was later superseded when a controller-path integration bug
  was identified and repaired
- `SWE-bench Pro` rerun:
  `pilot_20260420_qwen35_27b_swebench_pro_zeroclaw_t3_repair_r2`
  completed with `3/3` normal trajectories and was uploaded to
  `pilot_run/pilot_20260420_qwen35_27b_swebench_pro_zeroclaw_t3_repair_r2/`
- `IMO-AnswerBench` rerun:
  `pilot_20260420_qwen35_27b_imo_answerbench_zeroclaw_t3_repair_r2`
  initially remained abnormal because AlphaDiana was not writing ZeroClaw
  `provider_timeout_secs`, so the CLI fell back to its internal `120s`
  provider timeout and aborted long OpenRouter streams
- `IMO-AnswerBench` repaired rerun:
  `pilot_20260420_qwen35_27b_imo_answerbench_zeroclaw_t3_repair_r3`
  completed with `3/3` normal trajectories and was uploaded to
  `pilot_run/pilot_20260420_qwen35_27b_imo_answerbench_zeroclaw_t3_repair_r3/`

This follow-up reused healthy ROCK services already recorded in
`scripts/.rock_ports.env` because `scripts/start_zeroclaw.sh` could not start a
fresh stack in this worktree while `ref/ROCK` was absent.

Full reviewer-facing evidence, exact commands, and local log paths for the
rerun live in
[`../../context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/rerun_20260420_pending_recheck.md`](../../context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/rerun_20260420_pending_recheck.md).

## 2026-04-20 Smoke-Plan Alignment Probe

A same-day follow-up smoke rerun checked how closely the repaired ZeroClaw path
now matches the frozen rollout semantics
(`temperature=0.6`, `provider_max_tokens=32768`, `thinking=true`,
`timeout=1800s`).

- `IMO-AnswerBench`:
  `smoke_20260420_qwen35_27b_imo_answerbench_zeroclaw_align_r1`
  completed normally in ROCK with `1/1` task JSON written, `predicted='3'`,
  and `score=1.0`
- `SWE-bench Pro`:
  `smoke_20260420_qwen35_27b_swebench_pro_zeroclaw_align_r1`
  completed normally with a preserved-failure task record
  (`finish_reason=preserved_failure`, `error=null`)
- `terminal-bench-2`:
  `smoke_20260420_qwen35_27b_terminal_bench2_zeroclaw_align_r1`
  remained abnormal. The controller-side ZeroClaw run wrote only runtime logs
  plus one assistant line misidentifying the control workspace as the target
  environment, created `out.html` in the local controller workspace, and then
  hung without producing any scored task JSON

That initial TB2 alignment failure turned out to be an AlphaDiana integration
bug rather than model behavior: the TB2 controller path was still allowing
ZeroClaw to auto-enable its own internal Docker sandbox, which hid the mounted
controller workspace and broke the `./tb2-exec` contract. After AlphaDiana
started forcing `security_sandbox_enabled=false` for
`terminal_bench2_zeroclaw`, the repaired
`smoke_20260420_qwen35_27b_terminal_bench2_zeroclaw_align_r2` completed
normally as a reward-0 task record, and the replacement rerun
`pilot_20260420_qwen35_27b_terminal_bench2_zeroclaw_t3_repair_r3` completed
`3/3` with normal task JSONs (`tb2_break-filter-js-from-html -> score=0`,
`tb2_db-wal-recovery -> score=0`,
`tb2_fix-git -> score=1`) and was uploaded to
`pilot_run/pilot_20260420_qwen35_27b_terminal_bench2_zeroclaw_t3_repair_r3/`.

This means the repaired Qwen/OpenRouter ZeroClaw path is now close enough to
the frozen smoke plan for all three rechecked benchmarks, subject to the
remaining frozen-semantic gaps noted above (`top_p` and an explicit
`stream=true` knob are still not surfaced by the checked-in ZeroClaw path).

Reviewer-facing evidence for this matrix lives in
[`../../context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/README.md`](../../context/pr23-zeroclaw-openrouter-qwen-pilot-20260419/README.md).
