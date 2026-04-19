# Full Benchmark Configs

This directory stores ready-to-run full benchmark entry points.

The smoke configs under `configs/examples/` intentionally pin one task with `dataset_index` or `max_tasks`. Do not use those files for full benchmark runs.

## SWE-bench Pro

These are the PR29 full-run configs for the Diana-backed SWE-bench Pro paths:

| Mode | Full-run config |
|---|---|
| `openclaw` | `p29_full_openclaw_swebench_pro.yaml` |
| `opencode` | `p29_full_opencode_swebench_pro.yaml` |
| `zeroclaw` | `p29_full_zeroclaw_swebench_pro.yaml` |

`directLLM` is intentionally not included here. For the direct-LLM SWE-bench Pro baseline, use the official repository `scaleapi/SWE-bench_Pro-os`, not Diana.

Common setup:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...

export SWE_BENCH_PRO_EVAL_SCRIPT=/abs/path/to/SWE-bench_Pro-os/swe_bench_pro_eval.py
export SWE_BENCH_PRO_SCRIPTS_DIR=/abs/path/to/SWE-bench_Pro-os/run_scripts

export OPENCLAW_FULL_MODEL_NAME=minimax-m2.5
export OPENCLAW_FULL_MODEL_CANDIDATES=minimax-m2.5,minimax
export OPENCLAW_AGENT_ID=main
export OPENCLAW_TOOLS_PROFILE=coding
export OPENCLAW_PROMPT_PROFILE=edit_first
export OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000
export OPENCLAW_REQUIRE_PATCH=1
export OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT=12
export OPENCLAW_MAX_NO_EDIT_SECONDS=180
export OPENCLAW_CONTEXT_WINDOW=128000
export OPENCLAW_COMPLETION_MAX_TOKENS=4096

export OPENCODE_FULL_MODEL_NAME=minimax
export OPENCODE_FULL_MODEL_CANDIDATES=minimax
export OPENCODE_STRATEGY_SEQUENCE=guided_edit_first
export OPENCODE_REQUIRE_PATCH=0
export OPENCODE_PROMPT_PROFILE=edit_first
export OPENCODE_AUTO_TARGET_HINTS=0
export OPENCODE_TARGET_FILE_HINTS=src/database/redis/main.js,src/database/mongo/main.js,src/database/postgres/main.js,src/user/email.js
export OPENCODE_PRIMARY_TARGET_FILE=src/database/redis/main.js
export OPENCODE_PROBLEM_STATEMENT_MAX_CHARS=12000
export OPENCODE_PREFLIGHT_TIMEOUT_SEC=45
export OPENCODE_STARTUP_TIMEOUT_SEC=180
export OPENCODE_IDLE_TIMEOUT_SEC=900
export OPENCODE_IDLE_POLL_SEC=15
export OPENCODE_MAX_ACTIVE_NO_EDIT_SEC=300
export OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT=24
export OPENCODE_ACTIVITY_HEARTBEAT_SEC=30

export ZEROCLAW_FULL_MODEL_NAME=minimax-m2.5
export ZEROCLAW_FULL_MODEL_CANDIDATES=minimax-m2.5,minimax
export ZEROCLAW_TIMEOUT_SEC=1800
export ZEROCLAW_REQUIRE_PATCH=0
export ZEROCLAW_PROMPT_PROFILE=edit_first
export ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000
export ZEROCLAW_WORKSPACE_ONLY=0
export ZEROCLAW_MAX_TOOL_ITERATIONS=100
export ZEROCLAW_MAX_ACTIONS_PER_HOUR=200
export ZEROCLAW_RUNTIME_TRACE_MODE=none
```

Validate before running:

```bash
python -m alphadiana.cli validate configs/full_runs/p29_full_openclaw_swebench_pro.yaml
python -m alphadiana.cli validate configs/full_runs/p29_full_opencode_swebench_pro.yaml
python -m alphadiana.cli validate configs/full_runs/p29_full_zeroclaw_swebench_pro.yaml
```

Run commands:

```bash
python -m alphadiana.cli run configs/full_runs/p29_full_openclaw_swebench_pro.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p29_full_opencode_swebench_pro.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p29_full_zeroclaw_swebench_pro.yaml --redo-all
```

Scope notes:

- All three configs use `benchmark.name: swebench_pro_os`, `split: test`, `subset: all`.
- `openclaw` full runs keep `OPENCLAW_REQUIRE_PATCH=1` because that path already returned non-empty smoke patches in local validation.
- `opencode` full runs default to `OPENCODE_REQUIRE_PATCH=0`, matching the smoke playbook contract where execution success is determined by task JSON and dashboard state rather than forcing a patch on every task.
- `zeroclaw` full runs default to `ZEROCLAW_REQUIRE_PATCH=0` for the same reason: task JSON and dashboard state remain the primary full-run completion signal.
- If Docker Hub cannot serve `tmlrgroup/alphadiana:opencode`, set `SWEBENCH_OPENCODE_RUNTIME_IMAGE` before the OpenCode run.
- If the default ZeroClaw runtime image is unavailable locally, set `SWEBENCH_ZEROCLAW_RUNTIME_IMAGE` before the ZeroClaw run.

## Legacy PR25 Matrix

These configs are the ready-to-run full benchmark entry points for the nine PR25 combinations that passed strict smoke validation.

## Supported Matrix

| Mode | IMO-AnswerBench | HLE | terminal-bench-2 |
|---|---|---|---|
| `direct_llm` | `p25_full_directllm_minimax_imo_answerbench.yaml` | `p25_full_directllm_minimax_hle.yaml` | `p25_full_terminal_bench2_directllm_minimax.yaml` |
| `opencode` | `p25_full_opencode_minimax_imo_answerbench.yaml` | `p25_full_opencode_minimax_hle.yaml` | `p25_full_terminal_bench2_opencode_minimax.yaml` |
| `openclaw` | `p25_full_openclaw_minimax_imo_answerbench.yaml` | `p25_full_openclaw_minimax_hle.yaml` | `p25_full_terminal_bench2_openclaw_minimax.yaml` |

## Common Setup

Run from the repo root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

HLE uses the gated `cais/hle` dataset. On a fresh machine:

```bash
export HF_TOKEN=hf_...
```

terminal-bench-2 needs a local task checkout:

```bash
git clone --depth=1 https://github.com/laude-institute/terminal-bench.git /tmp/terminal-bench
export TERMINAL_BENCH2_DIR=/tmp/terminal-bench/tasks
```

Pre-pull terminal-bench-2 task images before full runs:

```bash
python - <<'PY' | sort -u | xargs -r -n1 docker pull
import os, tomllib
from pathlib import Path

root = Path(os.environ["TERMINAL_BENCH2_DIR"])
for task_toml in root.glob("*/task.toml"):
    with task_toml.open("rb") as f:
        data = tomllib.load(f)
    image = data.get("environment", {}).get("docker_image")
    if image:
        print(image)
PY
```

Build the controller images before terminal-bench-2 full runs for `opencode` and `openclaw`:

```bash
docker build -f docker/terminal_bench2/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .

docker build -f docker/terminal_bench2/Dockerfile.openclaw-controller \
  -t alphadiana/tb2-openclaw-controller:latest .
```

## Run Commands

Validate first:

```bash
for cfg in configs/full_runs/p25_full_*.yaml; do
  python -m alphadiana.cli validate "$cfg" || exit 1
done
```

Run sequentially unless the machine has enough local Docker and ROCK capacity:

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_opencode_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_openclaw_minimax_imo_answerbench.yaml --redo-all

python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_hle.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_opencode_minimax_hle.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_openclaw_minimax_hle.yaml --redo-all

python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_openclaw_minimax.yaml --redo-all
```

## Scope Notes

- IMO-AnswerBench configs run the full configured `train` split.
- HLE configs run the full supported `multipleChoice` subset. Other answer types are not included in the current exact-match scoring path.
- terminal-bench-2 configs scan all local task directories under `TERMINAL_BENCH2_DIR`.
- DirectLLM configs default to `max_concurrent: 20`.
- OpenCode configs default to `max_concurrent: 20` for IMO/HLE, and `2` for terminal-bench-2 because it also starts Docker containers.
- OpenClaw configs default to `max_concurrent: 1`.
