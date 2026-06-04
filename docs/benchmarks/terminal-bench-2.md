# terminal-bench-2

This runbook describes the current AlphaDiana path for `terminal-bench-2`.

Use it for local AlphaDiana evaluation and smoke validation. It is not a claim of strict Harbor-equivalent leaderboard execution.

For the benchmark-plan `direct_llm` baseline, use the official
`terminal-bench-2` repository and Harbor's built-in `terminus-2` agent. The
April 19, 2026 OpenRouter/Qwen evidence for that official path is summarized
below and in `context/qwen-openrouter-pilots/pilot-validation.md`.

## Current Support

| Mode | Smoke config | Full-run config |
|---|---|---|
| `direct_llm` | `configs/examples/terminal_bench2_directllm_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml` |
| `opencode` | `configs/examples/terminal_bench2_opencode_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml` |
| `openclaw` | `configs/examples/terminal_bench2_openclaw_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_openclaw_minimax.yaml` |
| `zeroclaw` | `configs/examples/terminal_bench2_zeroclaw_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_zeroclaw_minimax.yaml` |

All three paths use the same local AlphaDiana `terminal_bench2` benchmark loader and scorer.

## Podman Task-Container Readiness

On May 15, 2026, Phase 7 validated the opt-in
`terminal_bench2_opencode` Podman task-container path with a five-task
official TerminalBench2 pilot.

Entry points:

- Config:
  `configs/smokes/podman_terminal_bench2/terminal_bench2_opencode_pilot.yaml`
- Runner:
  `scripts/run_podman_terminal_bench2_readiness.sh`
- Evidence:
  `context/podman-terminal-bench2-readiness/README.md`

Run shape:

```bash
export TERMINAL_BENCH2_DIR=<official-terminal-bench-2-task-root>
export OPENAI_BASE_URL=<openai-compatible-base-url>
export OPENAI_API_KEY=<api-key-or-placeholder>
export OPENAI_MODEL_NAME=<model-name>
export TB2_OPENCODE_RUNTIME_IMAGE=localhost/alphadiana/tb2-opencode-controller:latest
export ALPHADIANA_TB2_LOGS_DIR="$PWD/logs/podman-terminal-bench2-readiness/task-logs"
export PODMAN_TB2_RUN_PREFIX=podman_tb2_$(date +%Y%m%d_%H%M%S)

bash scripts/run_podman_terminal_bench2_readiness.sh validate
bash scripts/run_podman_terminal_bench2_readiness.sh preflight
bash scripts/run_podman_terminal_bench2_readiness.sh pilot
bash scripts/run_podman_terminal_bench2_readiness.sh audit
```

Passing evidence:

- Run prefix: `podman_tb2_20260515_phase7_abslogs`
- Pilot run:
  `podman_tb2_20260515_phase7_abslogs_terminal_bench2_opencode`
- Tasks:
  `db-wal-recovery`, `fix-git`, `overfull-hbox`,
  `adaptive-rejection-sampler`, `break-filter-js-from-html`
- Result: 5/5 task JSON rows, all `valid_scored`, `score=0.0`,
  `metadata.container_engine=podman`, verifier `ok`, and discoverable
  artifacts/logs.
- Audit: passed with five `clean` infrastructure rows.

This supports recommending a larger overnight TerminalBench2 OpenCode Podman
campaign. It does not prove Direct x TerminalBench2, OpenClaw/ZeroClaw
TerminalBench2 Podman readiness, full-sweep readiness, Podman global default
promotion, or any SWE-bench/external_benchmark/MMMU-Pro status.

OpenRouter/Qwen pilot status on April 19, 2026:

- `direct_llm` official Harbor baseline:
  the initial batch `pilot_20260419_qwen35_27b_terminal_bench2_directllm_t3`
  failed (`0/3` verifier rewards, one `AgentTimeoutError`), but the repaired
  follow-up archive
  `pilot_20260419_qwen35_27b_terminal_bench2_directllm_t3_repair_r1`
  is now `3/3` normal trajectories with `3/3 reward=1` on the approved trio.
  This is repaired official-checkout evidence, not a stock upstream invocation.
- `opencode` native in-container pilot:
  `pilot_20260420_qwen35_27b_terminal_bench2_opencode_t3_r2` completed `3/3`
  normal task records (`score=0,0,1`) and was uploaded to
  `pilot_run/pilot_20260420_qwen35_27b_terminal_bench2_opencode_t3_r2/`
- `openclaw` native in-container pilot:
  after removing the bad `/app/TASK.md` prompt assumption, the rerun
  `pilot_20260420_qwen35_27b_terminal_bench2_openclaw_t3_r4` completed `3/3`
  normal task records with `3/3 score=1` and was uploaded to
  `pilot_run/pilot_20260420_qwen35_27b_terminal_bench2_openclaw_t3_r4/`
- `zeroclaw` native in-container pilot:
  `pilot_20260420_qwen35_27b_terminal_bench2_zeroclaw_t3_repair_r5`
  completed `3/3` normal task records (`score=0,1,1`) and was uploaded to
  `pilot_run/pilot_20260420_qwen35_27b_terminal_bench2_zeroclaw_t3_repair_r5/`

Local follow-up on April 19, 2026:

- `rerun_20260419_qwen35_27b_terminal_bench2_openclaw_timeoutcheck_r2`
  completed unattended on the timeout-check sample and no longer reported the
  old inner `ctx=16000` mismatch
- `rerun_20260419_qwen35_27b_terminal_bench2_zeroclaw_fixgit_r1`
  no longer reproduced the old missing-repo / missing-site workspace mismatch;
  the preserved failure moved to the loop detector

Local OpenCode/logprob follow-up on April 25, 2026:

- `phase12_opencode_tb2_qwen35_64k_logprobs_t3_parallel_20260425` completed
  the same three-task local OpenCode path and wrote all three task JSONs.
- All three tasks have no task-level `error` and saved provider-proxy logprobs
  with matching float/int16 sidecars (`1388`, `1242`, and `4389` records).
- This was not a clean task-quality pass: `bn-fit-modify` is `valid_scored`,
  while `adaptive-rejection-sampler` and `break-filter-js-from-html` ended with
  `response_json.returncode=-1` and `score_status=verifier_error` after a long
  local run. The raw log also contains one vLLM HTTP 400 where accumulated
  OpenCode context plus the local output cap exceeded the model context window
  by one token.
- `terminal_bench2_opencode` now wraps the container-local OpenCode command in
  `timeout --kill-after`, so future solver timeouts should not leave
  container-local OpenCode processes continuing to hit the provider after the
  host-side `docker exec` timeout.

## Prerequisites

Run from the repo root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

For the April 19, 2026 OpenRouter/Qwen pilot:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b
```

You also need:

- Docker
- a local `terminal-bench` task checkout
- pre-pulled task images
- runtime source images for the native in-container agents:
  `tmlrgroup/alphadiana:v1` for `openclaw`,
  `alphadiana/tb2-opencode-controller:latest` for `opencode`,
  and `zeroclaw-reasoning:0.6.9` for `zeroclaw`

## Official DirectLLM Baseline

This section is for the official direct-LLM baseline only. It is outside the
AlphaDiana runtime, but it is the benchmark-plan meaning of
`DirectLLM x terminal-bench-2`.

Minimal Harbor invocation shape from the upstream repo:

```bash
cd /path/to/terminal-bench-2
unset ALL_PROXY all_proxy HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
export OPENROUTER_API_KEY=...

uv run harbor run --path . \
  --agent terminus-2 \
  --model openrouter/qwen/qwen3.5-27b \
  --n-concurrent 3
```

Local April 19 OpenRouter/Qwen pilot specifics:

- approved trio:
  `adaptive-rejection-sampler`, `bn-fit-modify`,
  `break-filter-js-from-html`
- runtime settings:
  `temperature=0.6`, `top_p=0.95`, `max_tokens=32768`,
  `reasoning_effort=high`
- output root:
  `jobs/pilot_20260419_qwen35_27b_terminal_bench2_directllm_t3`
- accepted repaired archive:
  `pilot_20260419_qwen35_27b_terminal_bench2_directllm_t3_repair_r1`

Observed outcome on the initial official baseline:

- Harbor completed `3/3` trials and preserved all trial artifacts
- all verifier rewards were `0`
- `adaptive-rejection-sampler` ended with `AgentTimeoutError`
- `bn-fit-modify` and `break-filter-js-from-html` both reached the verifier and
  still scored `0`

Local repair follow-up in the same official checkout:

- verifier entrypoints for
  `adaptive-rejection-sampler`, `bn-fit-modify`, and
  `break-filter-js-from-html` were normalized to a stable
  `python venv + pip + pytest` flow
- Harbor's local JSON parser was patched to ignore benign pre-JSON prefix text
  instead of surfacing a parser warning
- `adaptive-rejection-sampler` was rerun after tightening the task contract to
  the required `ars(density_fn, domain, n = ...)` interface and switching the
  task image to a lightweight local R image
- accepted repaired task runs:
  - `bn-fit-modify` from `pilot_20260419_qwen35_27b_terminal_bench2_directllm_t3_r2`:
    normal trajectory, `reward=1`, `exception_type=null`
  - `break-filter-js-from-html` from
    `pilot_20260419_qwen35_27b_terminal_bench2_directllm_break_filter_js_from_html_r4`:
    normal trajectory, `reward=1`, `exception_type=null`
  - `adaptive-rejection-sampler` from
    `pilot_20260419_qwen35_27b_terminal_bench2_directllm_adaptive_rejection_sampler_r6`:
    normal trajectory, `reward=1`, `exception_type=null`, verifier `9/9 passed`
- the accepted repaired bundle was uploaded to
  `T-MARS/alphadiana-benchmark-results` under
  `pilot_run/pilot_20260419_qwen35_27b_terminal_bench2_directllm_t3_repair_r1/`

Treat that repaired bundle as the current local smoke-valid signal for the
official `direct_llm` path on OpenRouter/Qwen.

## Prepare Tasks

Clone a local task checkout:

```bash
git clone --depth=1 https://github.com/laude-institute/terminal-bench.git /tmp/terminal-bench
```

Set the full-run task root:

```bash
export TERMINAL_BENCH2_DIR=/tmp/terminal-bench/tasks
```

Prepare a deterministic smoke staging directory with one task:

```bash
rm -rf /tmp/terminal-bench-smoke-dbwal
mkdir -p /tmp/terminal-bench-smoke-dbwal
cp -a /tmp/terminal-bench/tasks/db-wal-recovery /tmp/terminal-bench-smoke-dbwal/

export TERMINAL_BENCH2_SMOKE_DIR=/tmp/terminal-bench-smoke-dbwal
```

For the April 19 OpenRouter pilot, the approved staged trio was:

- `db-wal-recovery`
- `fix-git`
- `break-filter-js-from-html`

Example staging flow with environment variables instead of a hardcoded local
path:

```bash
export TERMINAL_BENCH2_SOURCE_ROOT=/path/to/terminal-bench-2/tasks
export TERMINAL_BENCH2_PILOT_ROOT=/path/to/staged-terminal-bench-2-qwen-t3

rm -rf "$TERMINAL_BENCH2_PILOT_ROOT"
mkdir -p "$TERMINAL_BENCH2_PILOT_ROOT"
cp -a "$TERMINAL_BENCH2_SOURCE_ROOT"/db-wal-recovery "$TERMINAL_BENCH2_PILOT_ROOT"/
cp -a "$TERMINAL_BENCH2_SOURCE_ROOT"/fix-git "$TERMINAL_BENCH2_PILOT_ROOT"/
cp -a "$TERMINAL_BENCH2_SOURCE_ROOT"/break-filter-js-from-html "$TERMINAL_BENCH2_PILOT_ROOT"/
```

The smoke configs assume `TERMINAL_BENCH2_SMOKE_DIR` points at a directory whose immediate children are task directories. The full-run configs assume `TERMINAL_BENCH2_DIR` points at the full task root.

Current loader note on April 22, 2026:
pointing `TERMINAL_BENCH2_DIR` at a normal checkout root is valid. The loader
skips non-task directories such as `.git` and `jobs`. On the current local
April 22 checkout used for the OpenRouter full-run follow-up, that meant
`91` immediate directories on disk but `89` loaded task roots.

## Pre-pull Task Images

Before any smoke or full run:

```bash
python - <<'PY' | sort -u | xargs -r -n1 docker pull
import os, tomllib
from pathlib import Path

for task_toml in Path(os.environ["TERMINAL_BENCH2_DIR"]).glob("*/task.toml"):
    with task_toml.open("rb") as f:
        data = tomllib.load(f)
    image = data.get("environment", {}).get("docker_image")
    if image:
        print(image)
PY
```

For the default smoke task specifically:

```bash
docker pull alexgshaw/db-wal-recovery:20251031
```

## Prepare Runtime Source Images

`direct_llm` still uses the helper-workspace controller path. The native agents
`opencode`, `openclaw`, and `zeroclaw` now run inside a derived task image, so
they need runtime source images instead of controller containers.

Prepare the checked-in sources once:

```bash
docker pull tmlrgroup/alphadiana:v1
docker image inspect alphadiana/tb2-opencode-controller:latest >/dev/null
docker pull zeroclaw-reasoning:0.6.9
```

The first native-agent smoke/full run automatically builds a derived
`alphadiana-tb2-runtime:<agent>-<fingerprint>` image from the task image plus
the selected runtime source image.

Early OpenRouter full-run evidence on April 22, 2026 uses
`nvidia/nemotron-3-nano-30b-a3b:free`:

- `full_20260422_openrouter_nemotron_3_nano_30b_a3b_terminal_bench2_directllm_r1`
  and `..._openclaw_r1` already wrote
  `tb2_adaptive-rejection-sampler.json` as `valid_scored` with
  `reward=0`, `metadata.verifier_status=ok`, and
  `metadata.verifier_reward_observed=true`
- `..._zeroclaw_r1` also advanced with normal `valid_scored` reward-0 task
  JSONs
- `..._opencode_r1` is less quiet so far: its first task wrote
  `score_status=verifier_error` with
  `metadata.verifier_status=missing_reward`
- the first task still takes roughly minutes before its JSON appears because of
  container bring-up plus verifier startup; do not classify the run as stalled
  during that gap alone

For ZeroClaw, prefer putting large temporary files on a data disk before running:

```bash
export TMPDIR=/path/to/$USER/tmp/alphadiana-tb2
mkdir -p "$TMPDIR"
```

## Runtime Model

AlphaDiana now uses two different TB2 execution contracts:

- `direct_llm`: helper-workspace controller mode.
  The model sees `tb2-exec`, `tb2-copy-from`, `tb2-copy-to`, and `tb2-test`.
- `opencode`, `openclaw`, `zeroclaw`: native in-container mode.
  AlphaDiana derives a runtime image from the task image, starts that task
  container directly, and runs the agent CLI inside it.

For the native agents:

- the model sees the live task filesystem directly
- `tb2-exec` / `tb2-copy-*` are not exposed to the model
- `/tests/test.sh` and `reward.txt` stay unchanged
- the outer harness still runs verification once at the end

## Smoke Runs

Validate the smoke configs first:

```bash
python -m alphadiana.cli validate configs/examples/terminal_bench2_directllm_minimax.yaml
python -m alphadiana.cli validate configs/examples/terminal_bench2_opencode_minimax.yaml
python -m alphadiana.cli validate configs/examples/terminal_bench2_openclaw_minimax.yaml
python -m alphadiana.cli validate configs/examples/terminal_bench2_zeroclaw_minimax.yaml
```

Run the three smoke configs:

```bash
python -m alphadiana.cli run configs/examples/terminal_bench2_directllm_minimax.yaml --redo-all
python -m alphadiana.cli run configs/examples/terminal_bench2_opencode_minimax.yaml --redo-all
python -m alphadiana.cli run configs/examples/terminal_bench2_openclaw_minimax.yaml --redo-all
python -m alphadiana.cli run configs/examples/terminal_bench2_zeroclaw_minimax.yaml --redo-all
```

April 19 OpenRouter/Qwen 3-task pilot commands:

```bash
python -m alphadiana.cli validate configs/examples/terminal_bench2_openclaw_minimax.yaml \
  -o run_id=pilot_20260419_qwen35_27b_terminal_bench2_openclaw_t3 \
  -o output_dir=./results \
  -o benchmark.config.tasks_dir="$TERMINAL_BENCH2_PILOT_ROOT" \
  -o benchmark.config.max_tasks=3 \
  -o agent.config.model_name=qwen/qwen3.5-27b

python -m alphadiana.cli validate configs/examples/terminal_bench2_opencode_minimax.yaml \
  -o run_id=pilot_20260419_qwen35_27b_terminal_bench2_opencode_t3 \
  -o output_dir=./results \
  -o benchmark.config.tasks_dir="$TERMINAL_BENCH2_PILOT_ROOT" \
  -o benchmark.config.max_tasks=3 \
  -o agent.config.model_name=qwen/qwen3.5-27b \
  -o agent.config.model=custom/qwen/qwen3.5-27b \
  -o agent.config.streaming=true \
  -o max_concurrent=2

python -m alphadiana.cli run configs/examples/terminal_bench2_openclaw_minimax.yaml \
  -o run_id=pilot_20260419_qwen35_27b_terminal_bench2_openclaw_t3 \
  -o output_dir=./results \
  -o benchmark.config.tasks_dir="$TERMINAL_BENCH2_PILOT_ROOT" \
  -o benchmark.config.max_tasks=3 \
  -o agent.config.model_name=qwen/qwen3.5-27b \
  2>&1 | tee logs/pilot_20260419_qwen35_27b_terminal_bench2_openclaw_t3.log

python -m alphadiana.cli run configs/examples/terminal_bench2_opencode_minimax.yaml \
  -o run_id=pilot_20260419_qwen35_27b_terminal_bench2_opencode_t3 \
  -o output_dir=./results \
  -o benchmark.config.tasks_dir="$TERMINAL_BENCH2_PILOT_ROOT" \
  -o benchmark.config.max_tasks=3 \
  -o agent.config.model_name=qwen/qwen3.5-27b \
  -o agent.config.model=custom/qwen/qwen3.5-27b \
  -o agent.config.streaming=true \
  -o max_concurrent=2 \
  2>&1 | tee logs/pilot_20260419_qwen35_27b_terminal_bench2_opencode_t3.log
```

Observed results for that pilot:

- `pilot_20260419_qwen35_27b_terminal_bench2_opencode_t3`:
  `3/3` task records, all `score=1`
- `pilot_20260419_qwen35_27b_terminal_bench2_openclaw_t3`:
  `3/3` task records, `tb2_fix-git -> score=1`,
  `tb2_db-wal-recovery -> score=0`,
  `tb2_break-filter-js-from-html -> score=0`
- The OpenClaw pilot captured
  `low context window: ... ctx=16000` on all three tasks and needed manual
  watchdog interruption on the first two tasks. Treat that path as experimental
  on OpenRouter/Qwen.
- the initial strict ZeroClaw smoke-plan alignment attempt
  `smoke_20260420_qwen35_27b_terminal_bench2_zeroclaw_align_r1` was abnormal
  because AlphaDiana still let ZeroClaw auto-enable its own internal Docker
  sandbox inside the TB2 controller image, which hid the mounted control
  workspace and broke the `./tb2-exec` contract
- after forcing `security_sandbox_enabled=false` on
  `terminal_bench2_zeroclaw`, the repaired
  `smoke_20260420_qwen35_27b_terminal_bench2_zeroclaw_align_r2` completed
  normally as a reward-0 task record, and the replacement rerun
  `pilot_20260420_qwen35_27b_terminal_bench2_zeroclaw_t3_repair_r3` completed
  `3/3` with normal task JSONs (`2` reward-0 failures, `1` reward-1 pass)
- the April 20 in-container migration smokes supersede the native-agent
  controller-specific caveats:
  `smoke_20260420_qwen35_27b_tb2_openclaw_incontainer_r2` completed `1/1` on
  `break-filter-js-from-html` with `score=1`,
  `smoke_20260420_qwen35_27b_tb2_opencode_incontainer_r2` completed `1/1` on
  `break-filter-js-from-html` with a normal reward-0 trajectory,
  and `smoke_20260420_qwen35_27b_tb2_zeroclaw_incontainer_r2` completed `1/1`
  on `db-wal-recovery` with a normal reward-0 trajectory after sanitizing
  runtime logs out of the top-level assistant text
- the full April 20 native TB2 reruns are now the accepted pilot evidence:
  `pilot_20260420_qwen35_27b_terminal_bench2_opencode_t3_r2` wrote `3/3`
  normal task JSONs and was uploaded,
  `pilot_20260420_qwen35_27b_terminal_bench2_openclaw_t3_r4` wrote `3/3`
  normal task JSONs after fixing the in-container prompt contract to stop
  assuming `/app/TASK.md`,
  and `pilot_20260420_qwen35_27b_terminal_bench2_zeroclaw_t3_repair_r5`
  wrote `3/3` normal task JSONs and supersedes the older controller-mode
  `repair_r3` archive

Smoke success means:

- the task loads
- the selected agent path runs
- `/tests/test.sh` runs
- a scored JSONL result is written

It does not mean the agent is competitive across the full benchmark.

## Full Runs

Validate the full-run configs first:

```bash
python -m alphadiana.cli validate configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml
python -m alphadiana.cli validate configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml
python -m alphadiana.cli validate configs/full_runs/p25_full_terminal_bench2_openclaw_minimax.yaml
python -m alphadiana.cli validate configs/full_runs/p25_full_terminal_bench2_zeroclaw_minimax.yaml
```

Run them:

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_openclaw_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_zeroclaw_minimax.yaml --redo-all
```

Recommended concurrency:

- `direct_llm`: `max_concurrent: 4`
- `opencode`: `max_concurrent: 2`
- `openclaw`: `max_concurrent: 1`
- `zeroclaw`: `max_concurrent: 1`

Adjust only if the local machine has enough Docker and API capacity.

## Result Interpretation

The current AlphaDiana `terminal_bench2` scorer is binary:

- `reward.txt == "1"` means pass
- missing or non-`1` reward means fail

The JSONL `score` comes from that reward path.

As of April 22, 2026, current main no longer fabricates `metadata.reward="0"`
for `metadata.verifier_status="skipped_duplicate"` when no reward file was
actually observed. Historical artifacts such as
`full_20260422_terminal_bench2_opencode_deepseek_chat_r2/tasks/tb2_adaptive-rejection-sampler.json`
remain useful audit evidence, but the intended current behavior is the
post-fix reproducer
`fixproof_after_20260422_tb2_opencode_deepseek_fast2_t1`, which records
`metadata.reward=null`, `metadata.verifier_reward_observed=false`, and
`score_status=verifier_error` for that bookkeeping path.

## Current Config Semantics

Smoke configs:

- live under `configs/examples/`
- use `TERMINAL_BENCH2_SMOKE_DIR`
- intentionally run one staged task

Full-run configs:

- live under `configs/full_runs/`
- use `TERMINAL_BENCH2_DIR`
- scan all task directories under that root

For the current checked-in smoke setup, the canonical staged task is `db-wal-recovery`.

The April 19 OpenRouter/Qwen pilot used the approved trio `db-wal-recovery`,
`fix-git`, and `break-filter-js-from-html` instead of a single staged task.

## ZeroClaw Reproduction Notes

`terminal-bench-2` does not use ROCK. The formal ZeroClaw smoke path is now
the TB2 native in-container path:

- AlphaDiana starts a derived TB2 runtime image for the selected task
- ZeroClaw runs directly inside that task container
- the task JSON is normal as long as the run writes a scored record, even if
  the verifier reward is `0`

### Reproduce The 2026-04-20 In-Container Smoke

Prepare the staged smoke task and runtime source image first:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
export PYTHONPATH=$PWD
export TERMINAL_BENCH2_SMOKE_DIR=/path/to/terminal-bench-smoke-dbwal
export TMPDIR=/path/to/$USER/tmp/alphadiana-tb2
mkdir -p "$TMPDIR"

docker pull alexgshaw/db-wal-recovery:20251031
docker pull zeroclaw-reasoning:0.6.9
```

Run the smoke:

```bash
python -m alphadiana.cli run configs/examples/terminal_bench2_zeroclaw_minimax.yaml \
  -o run_id=smoke_20260420_qwen35_27b_tb2_zeroclaw_incontainer_r2 \
  -o output_dir=./results/smoke_20260420_qwen35_27b_tb2_zeroclaw_incontainer_r2
```

Observed local verification on 2026-04-20:

- run_id: `smoke_20260420_qwen35_27b_tb2_zeroclaw_incontainer_r2`
- result: `1/1` completed, `predicted=0`, no `error`
- execution mode: derived in-container TB2 runtime image
- the assistant trajectory contains normal task reasoning after runtime-log
  sanitization; it no longer exposes `tb2-exec`/`tb2-copy-*`
