# Podman TerminalBench2 Task-Container Readiness

This directory contains the Phase 7 opt-in TerminalBench2 Podman pilot:
OpenCode on five deterministic official TerminalBench2 tasks.

Selected task names:

- `db-wal-recovery`
- `fix-git`
- `overfull-hbox`
- `adaptive-rejection-sampler`
- `break-filter-js-from-html`

Expected task ids:

- `tb2_db-wal-recovery`
- `tb2_fix-git`
- `tb2_overfull-hbox`
- `tb2_adaptive-rejection-sampler`
- `tb2_break-filter-js-from-html`

Run from the repository root:

```bash
export TERMINAL_BENCH2_DIR=/path/to/terminal-bench-2/tasks
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

`pilot` runs preflight before launching tasks. The pilot writes raw shell logs
under `logs/`, task JSONs under `results/`, and preflight/status/audit
artifacts under `context/podman-terminal-bench2-readiness/`.

Scope boundaries:

- TerminalBench2 only.
- `terminal_bench2_opencode` only.
- Direct x TerminalBench2 remains out of scope.
- OpenClaw and ZeroClaw TB2 task-container expansion is out of scope for this
  pilot unless separately proven.
- SWE-bench, SWE-bench Pro, external_benchmark, MMMU-Pro, standard-reasoning reruns,
  Podman global default promotion, and ROCK/Docker deletion are out of scope.
