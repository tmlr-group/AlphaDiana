# terminal-bench-2

terminal-bench-2 evaluates agents on containerized terminal tasks. AlphaDiana loads local task directories and runs task-specific Docker images.

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

Clone or stage terminal-bench-2 tasks locally:

```bash
git clone --depth=1 https://github.com/harbor-framework/terminal-bench-2.git /tmp/terminal-bench-2

rm -rf /tmp/terminal-bench-2-smoke-dbwal
mkdir -p /tmp/terminal-bench-2-smoke-dbwal
cp -a /tmp/terminal-bench-2/db-wal-recovery /tmp/terminal-bench-2-smoke-dbwal/

docker pull alexgshaw/db-wal-recovery:20251031
export TERMINAL_BENCH2_DIR=/tmp/terminal-bench-2-smoke-dbwal
```

`benchmark.config.tasks_dir` can also point at a full terminal-bench-2 checkout. The smoke configs use the single-task staging directory for reproducibility.

## Supported Modes

| Mode | Status | Config |
|---|---|---|
| `direct_llm` | supported | `configs/examples/terminal_bench2_directllm_minimax.yaml` |
| `opencode` | supported | `configs/examples/terminal_bench2_opencode_minimax.yaml` |
| `openclaw` | not supported as of 2026-04-16 | `configs/examples/terminal_bench2_openclaw_minimax.yaml` exists for debugging only |

## DirectLLM

The DirectLLM path uses `terminal_bench2_docker`. AlphaDiana starts the task container, asks the model for shell commands, relays commands through `docker exec`, then runs `/tests/test.sh`.

```bash
python -m alphadiana.cli run configs/examples/terminal_bench2_directllm_minimax.yaml \
  -o run_id=tb2_directllm_smoke
```

## OpenCode

The OpenCode path uses `terminal_bench2_opencode`. AlphaDiana starts the task container and gives OpenCode helper scripts:

- `tb2-exec`
- `tb2-copy-from`
- `tb2-copy-to`
- `tb2-test`

```bash
python -m alphadiana.cli run configs/examples/terminal_bench2_opencode_minimax.yaml \
  -o run_id=tb2_opencode_smoke
```

The smoke config uses `solver_timeout_sec: 1800`. A strict smoke should show visible model output and produce a scored JSONL result.

## OpenClaw

OpenClaw is not currently a supported terminal-bench-2 mode.

As of 2026-04-16, `terminal_bench2_openclaw` was tested on `tb2_db-wal-recovery` with:

- model: `minimax-m2.5`
- request timeout: `1800` seconds
- `continue_on_planner_error: false`

The planner produced no visible model output within the 30-minute request window, and the run completed `0/1`. Treat this as an integration blocker, not as a passing smoke. The config remains in `configs/examples/terminal_bench2_openclaw_minimax.yaml` only for debugging and future development.

## Result Interpretation

terminal-bench-2 scoring is binary:

- `reward.txt == "1"` means the verifier passed.
- Any other reward means the task failed.

For smoke testing, first confirm the pipeline writes a JSONL result with a populated score. Then inspect score quality separately.
