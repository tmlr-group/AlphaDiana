# terminal-bench-2

This runbook describes the current AlphaDiana path for `terminal-bench-2`.

Use it for local AlphaDiana evaluation and smoke validation. It is not a claim of strict Harbor-equivalent leaderboard execution.

## Current Support

| Mode | Smoke config | Full-run config |
|---|---|---|
| `direct_llm` | `configs/examples/terminal_bench2_directllm_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml` |
| `opencode` | `configs/examples/terminal_bench2_opencode_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml` |
| `openclaw` | `configs/examples/terminal_bench2_openclaw_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_openclaw_minimax.yaml` |
| `zeroclaw` | `configs/examples/terminal_bench2_zeroclaw_minimax.yaml` | `configs/full_runs/p25_full_terminal_bench2_zeroclaw_minimax.yaml` |

All three paths use the same local AlphaDiana `terminal_bench2` benchmark loader and scorer.

## Prerequisites

Run from the repo root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

You also need:

- Docker
- a local `terminal-bench` task checkout
- pre-pulled task images
- controller images for `opencode`, `openclaw`, and `zeroclaw`

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

docker build -f docker/terminal_bench2/Dockerfile.zeroclaw-controller \
  -t alphadiana/tb2-zeroclaw-controller:latest .
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
- `zeroclaw`: native controller-container CLI runner

For native agents, the workspace `tb2-test` helper is intentionally disabled and the outer harness runs the real verifier once at the end.

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
