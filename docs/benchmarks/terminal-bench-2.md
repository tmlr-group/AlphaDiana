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

All three paths use the same local AlphaDiana `terminal_bench2` benchmark loader and scorer.

OpenRouter/Qwen pilot status on April 19, 2026:

- `direct_llm` official Harbor baseline:
  approved 3-task pilot completed `3/3`, but all verifier rewards were `0`;
  one task ended with `AgentTimeoutError`
- `opencode`: approved 3-task pilot passed `3/3`
- `openclaw`: `3/3` task records were written, but only `1/3` passed and the
  first two tasks needed manual watchdog interruption to advance

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
- controller images for `opencode` and `openclaw`

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

Observed outcome on that official baseline:

- Harbor completed `3/3` trials and preserved all trial artifacts
- all verifier rewards were `0`
- `adaptive-rejection-sampler` ended with `AgentTimeoutError`
- `bn-fit-modify` and `break-filter-js-from-html` both reached the verifier and
  still scored `0`

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

## Build Controller Images

`direct_llm` does not need a controller image. `opencode` and `openclaw` do.

Build both once:

```bash
docker build -f docker/terminal_bench2/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .

docker build -f docker/terminal_bench2/Dockerfile.openclaw-controller \
  -t alphadiana/tb2-openclaw-controller:latest .
```

## Runtime Model

AlphaDiana runs `terminal-bench-2` from the Docker-capable control side:

- AlphaDiana starts the task container.
- AlphaDiana creates a local control workspace.
- The workspace exposes `tb2-exec`, `tb2-copy-from`, `tb2-copy-to`, and `tb2-test`.
- The task container remains the target environment only.

Mode-specific behavior:

- `direct_llm`: multi-turn chat loop that emits `$ cmd` lines and `DONE`
- `opencode`: native controller-container CLI runner
- `openclaw`: native controller-container CLI runner

For native agents, the workspace `tb2-test` helper is intentionally disabled and the outer harness runs the real verifier once at the end.

## Smoke Runs

Validate the smoke configs first:

```bash
python -m alphadiana.cli validate configs/examples/terminal_bench2_directllm_minimax.yaml
python -m alphadiana.cli validate configs/examples/terminal_bench2_opencode_minimax.yaml
python -m alphadiana.cli validate configs/examples/terminal_bench2_openclaw_minimax.yaml
```

Run the three smoke configs:

```bash
python -m alphadiana.cli run configs/examples/terminal_bench2_directllm_minimax.yaml --redo-all
python -m alphadiana.cli run configs/examples/terminal_bench2_opencode_minimax.yaml --redo-all
python -m alphadiana.cli run configs/examples/terminal_bench2_openclaw_minimax.yaml --redo-all
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
```

Run them:

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_openclaw_minimax.yaml --redo-all
```

Recommended concurrency:

- `direct_llm`: `max_concurrent: 4`
- `opencode`: `max_concurrent: 2`
- `openclaw`: `max_concurrent: 1`

Adjust only if the local machine has enough Docker and API capacity.

## Result Interpretation

The current AlphaDiana `terminal_bench2` scorer is binary:

- `reward.txt == "1"` means pass
- missing or non-`1` reward means fail

The JSONL `score` comes from that reward path.

## Current Config Semantics

Smoke configs:

- live under `configs/examples/`
- use `TERMINAL_BENCH2_SMOKE_DIR`
- intentionally run one staged task

Full-run configs:

- live under `configs/full_runs/`
- use `TERMINAL_BENCH2_DIR`
- scan all task directories under that root

For the current checked-in smoke setup, the canonical staged task is
`db-wal-recovery`.

The April 19 OpenRouter/Qwen pilot used the approved trio
`db-wal-recovery`, `fix-git`, and `break-filter-js-from-html` instead of a
single staged task.
