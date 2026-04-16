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
| `direct_llm` | supported | `configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml` |
| `opencode` | supported | `configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml` |
| `openclaw` | native path implemented, live rerun pending | `configs/examples/terminal_bench2_openclaw_minimax.yaml` |

The full configs scan all task directories under `TERMINAL_BENCH2_DIR`. The corresponding smoke configs remain under `configs/examples/` and pin one staged task with `max_tasks: 1`.

## Full Runs

Pre-pull task Docker images first; see [configs/full_runs](../../configs/full_runs/README.md).

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml --redo-all
```

## Shared Runtime

All terminal-bench-2 agents now run on the Docker-capable control side. AlphaDiana starts the task container, creates a local control workspace, and writes these helpers:

- `tb2-exec`
- `tb2-copy-from`
- `tb2-copy-to`
- `tb2-test`

The task container remains the target environment only; agents do not install themselves into the task container.

For CLI agents, the control side is expected to be a dedicated controller container with Docker socket access, not the host shell and not the task container itself.

## DirectLLM

The DirectLLM path uses `terminal_bench2_docker`. It still uses multi-turn chat completions and `$ cmd` output, but commands now run in the control workspace against the shared `tb2-*` helpers.

```bash
python -m alphadiana.cli run configs/examples/terminal_bench2_directllm_minimax.yaml \
  -o run_id=tb2_directllm_smoke
```

## OpenCode

The OpenCode path uses `terminal_bench2_opencode`. OpenCode runs natively on the control side and operates on the task container only through the shared `tb2-*` helpers.

Build the controller image first:

```bash
docker build -f docker/terminal_bench2/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .
```

```bash
python -m alphadiana.cli run configs/examples/terminal_bench2_opencode_minimax.yaml \
  -o run_id=tb2_opencode_smoke
```

The smoke config uses `solver_timeout_sec: 1800`. A strict smoke should show visible model output and produce a scored JSONL result.

## OpenClaw

The OpenClaw path now uses `terminal_bench2_openclaw` as a control-side native runner. It no longer depends on the OpenClaw gateway planner or a ROCK sandbox for terminal-bench-2. Instead, AlphaDiana:

- creates the shared control workspace and `tb2-*` helpers
- runs `openclaw onboard --non-interactive` inside a controller container against the configured OpenAI-compatible provider
- runs `openclaw agent --local --json` in that workspace
- parses OpenClaw session JSONL from `OPENCLAW_HOME`

Build the controller image first:

```bash
docker build -f docker/terminal_bench2/Dockerfile.openclaw-controller \
  -t alphadiana/tb2-openclaw-controller:latest .
```

The checked-in example config assumes:

- a controller image contains `openclaw` and `docker`
- `OPENAI_BASE_URL` / `OPENAI_API_KEY` are exported
- `model_name` is set to the target provider model, for example `minimax-m2.5`

This implementation replaces the older relay-only planner path. As of 2026-04-16, fresh strict smoke reruns on the current native controller code have passed for both `opencode` and `openclaw`.

## 2026-04-16 Controller Smoke Notes

Single-task smoke runs against `db-wal-recovery` under `/tmp/terminal-bench-2-smoke-dbwal` now separate cleanly into runtime validation and task-quality validation.

Earlier controller checks showed that the control-side topology was viable but still had startup issues:

- `direct_llm`: `tb2_directllm_controllercheck_20260416` completed end-to-end with `reward=0`.
- `openclaw`: `tb2_openclaw_controllercheck3_20260416` reached scoring, but that run was still affected by an invalid CLI default (`verbose=normal`). After fixing controller defaults, `tb2_openclaw_controllercheck4_20260416` confirmed live controller-native execution with real `exec/read` tool calls; it was manually aborted before a final score was written.
- `opencode`: `tb2_opencode_controllercheck3_20260416` confirmed controller-container execution, but the run was manually aborted after the agent kept looping on the disappearing `main.db-wal` file instead of converging on a fix. The earlier `tb2_opencode_controllercheck2_20260416` reached scoring and returned `reward=0`.

Later on 2026-04-16, the native controller path was tightened further:

- `terminal_bench2_common` now snapshots `/app/main.db` and `/app/main.db-wal` into `./bootstrap/app/` plus untouched backups under `./bootstrap/original/` for `db-wal-recovery`.
- Controller images now include `sqlite3`, `xxd`, and `file` so local diagnosis can happen without falling back to the task container.
- Controller commands now run through `docker run --entrypoint /bin/bash -lc ...`, so the runtime no longer depends on whatever default entrypoint or command the controller image currently has.
- `terminal_bench2_opencode` now launches the controller-side CLI via `node /usr/lib/node_modules/opencode-ai/bin/opencode`, which avoids the broken direct-exec path seen with the npm-installed wrapper in the controller container.

With those fixes in place, these later live proofs were observed:

- `opencode`: `tb2_opencode_bootstrapcheck4_20260416` read `TASK.md` and `TASK_HINTS.md`, used the local `bootstrap/` snapshots, ran local `sqlite3`, noticed that opening `main.db` removed `bootstrap/app/main.db-wal`, restored the WAL from `bootstrap/original/`, and continued diagnosis. The run was manually aborted before verifier completion, so it does not count as a scored smoke result.
- `openclaw`: `tb2_openclaw_bootstrapcheck5_20260416` completed local `onboard`, wrote a session JSONL under `.openclaw-home`, read `TASK.md` and `TASK_HINTS.md`, inspected both `bootstrap/app/` and `bootstrap/original/`, used local `sqlite3`, observed the WAL disappearing after database access, restored it from `bootstrap/original/`, and hex-inspected the WAL with `xxd`. The run was also manually aborted before verifier completion.

Those later runs answered the runtime question, but they were still manually aborted before scoring. The final 2026-04-16 strict reruns closed the loop:

- `tb2_opencode_scored6_20260416`: passed `1/1` with `reward=1`. `wall_time_sec=737.9s`.
- `tb2_openclaw_scored6_20260416`: passed `1/1` with `reward=1`. `wall_time_sec=1200.7s`.

Those final reruns also showed why earlier scored attempts had failed even after the agents solved the task:

- Native agents kept calling `./tb2-test` themselves. Prompt guidance was not enough, so the native workspace helper is now replaced with a no-op stub and only the outer harness runs the real verifier.
- `/tests/test.sh` itself can still print external-network errors from the upstream uv installer (`curl https://astral.sh/... | sh`), but the harness now pre-seeds a local `uvx` shim and `/root/.local/bin/env` in the task container, so the same `pytest` suite still executes and writes `reward.txt`.

Those final reruns answer the runtime question:

- Runtime question: controller-side native execution is now working for both `opencode` and `openclaw` on the staged `db-wal-recovery` smoke task.
- Task-quality question: for the current smoke task, both native agents now solve WAL recovery and pass the strict verifier. The remaining variability is verifier wall time, not agent startup or solver correctness.

## Result Interpretation

terminal-bench-2 scoring is binary:

- `reward.txt == "1"` means the verifier passed.
- Any other reward means the task failed.

For smoke testing, first confirm the pipeline writes a JSONL result with a populated score. Then inspect score quality separately.
