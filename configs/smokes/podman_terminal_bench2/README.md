# Podman TerminalBench2 Task-Container Readiness

This directory contains the Phase 7 opt-in TerminalBench2 Podman small matrix:
OpenClaw, OpenCode, and ZeroClaw on three deterministic official
TerminalBench2 tasks.

Configs:

- `terminal_bench2_openclaw_pilot.yaml`
- `terminal_bench2_opencode_pilot.yaml`
- `terminal_bench2_zeroclaw_pilot.yaml`

Selected task names:

- `db-wal-recovery`
- `overfull-hbox`
- `adaptive-rejection-sampler`

Expected task ids:

- `tb2_db-wal-recovery`
- `tb2_overfull-hbox`
- `tb2_adaptive-rejection-sampler`

Run from the repository root:

```bash
export TERMINAL_BENCH2_DIR=/path/to/terminal-bench-2/tasks
export OPENAI_BASE_URL=<openai-compatible-base-url>
export OPENAI_API_KEY=<api-key-or-placeholder>
export OPENAI_MODEL_NAME=<model-name>
export TB2_OPENCODE_RUNTIME_IMAGE=localhost/alphadiana/tb2-opencode-controller:latest
export TB2_OPENCLAW_RUNTIME_IMAGE=localhost/alphadiana-openclaw-swebench-runtime-source:latest
export TB2_ZEROCLAW_RUNTIME_IMAGE=localhost/zeroclaw-reasoning:0.6.9
# Output + task logs MUST live on a large disk, NOT /home (logprob dual-write
# alone can reach hundreds of GB). Point both at a /data* mount.
export ALPHADIANA_TB2_OUTPUT_DIR=/path/to/<user>/alphadiana/podman-tb2/results
export ALPHADIANA_TB2_LOGS_DIR=/path/to/<user>/alphadiana/podman-tb2/task-logs
export PODMAN_TB2_RUN_PREFIX=podman_tb2_$(date +%Y%m%d_%H%M%S)

bash scripts/run_podman_terminal_bench2_readiness.sh all
```

`output_dir` in the three pilot configs reads `${ALPHADIANA_TB2_OUTPUT_DIR}`,
so it must be exported. See the full-run pre-flight runbook at
`context/podman-terminal-bench2-readiness/README.md` for disk, keyring, and
vLLM checks that must pass before a full sweep.

Manual equivalent:

```bash
bash scripts/run_podman_terminal_bench2_readiness.sh validate
bash scripts/run_podman_terminal_bench2_readiness.sh preflight
bash scripts/run_podman_terminal_bench2_readiness.sh pilot
bash scripts/run_podman_terminal_bench2_readiness.sh audit
```

`pilot` runs preflight before launching tasks. `all` and `auto` run
`validate -> preflight -> pilot -> audit` fail-fast. The pilot writes raw shell
logs under `logs/`, task JSONs under `results/`, and preflight/status/audit
artifacts under `context/podman-terminal-bench2-readiness/`.

Scope boundaries:

- TerminalBench2 only.
- `terminal_bench2_openclaw`, `terminal_bench2_opencode`, and
  `terminal_bench2_zeroclaw` only.
- Direct x TerminalBench2 remains out of scope.
- SWE-bench, SWE-bench Pro, external_benchmark, MMMU-Pro, standard-reasoning reruns,
  Podman global default promotion, and ROCK/Docker deletion are out of scope.
