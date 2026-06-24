---
sidebar_position: 4
---

# Isolation & Fairness

AlphaDiana compares harnesses ([`direct_llm`](../harnesses/direct-llm), [`zeroclaw`](../harnesses/zeroclaw), [`opencode`](../harnesses/opencode), [`openclaw`](../harnesses/openclaw)) on the same benchmarks. For that comparison to be fair, every task must run in its own clean environment. If one task could leave files, processes, or state behind for the next task, accuracy numbers would reflect leakage rather than capability. AlphaDiana addresses this with **task-scoped sandbox runtimes** and a fail-closed switch that refuses to silently degrade isolation.

## Why task-scoped isolation matters

A single run expands into work items `[(task, sample_index)]`. Each item runs `agent.solve -> scorer.score -> ResultStore.append`. If two items share a workspace, a tool agent can read another task's scratch files or continue another task's process, contaminating both the answer and the measured behavior.

Task-scoped runtimes keep benchmark-side effects confined to the task environment in normal use:

- ROCK Docker sandboxes for the standard text and multimodal benchmarks.
- Docker task containers for `terminal-bench-2`.
- Official per-task SWE containers (via `swebench_docker`) for `SWE-bench Pro`.

This is the **practical evaluation boundary** described below, not a formal security boundary.

## Paper-safe wording

When citing isolation in a paper or report, use wording like:

> AlphaDiana runs supported benchmark tasks in disposable task-scoped sandbox or container runtimes. These runtimes are intended to keep benchmark-side effects confined to the task environment in normal use rather than the host workspace. This is a practical evaluation boundary, not a formal security isolation claim.

Avoid wording like:

- "fully isolated from the host"
- "guaranteed not to affect the host"
- "all agents always run in a sandbox"

It is accurate to say AlphaDiana uses task-scoped sandbox or container runtimes on the supported benchmark paths. It is not accurate to say every agent-benchmark pair always runs in a strong sandbox.

## How a run picks its isolation mode

The `Runner` records the chosen mode in `run_metadata["isolation_mode"]` and mirrors it into the run manifest (`alphadiana/engine/runner.py:1225-1252`). The mode is derived from whether a sandbox was configured, auto-created, or predeployed as a pool.

| `isolation_mode` | When it applies |
|---|---|
| `shared_gateway` | No sandbox configured; tasks share one gateway. Weakest boundary. |
| `explicit_sandbox` | `sandbox.name` is set in the config; the runner drives that backend. |
| `auto_single_sandbox` | One ROCK sandbox auto-created for the run. |
| `predeployed_pool` / `partial_predeploy` | A pool of ROCK sandboxes predeployed up front (gateway agents). |
| `fresh_predeployed_pool` / `partial_fresh_predeployed_pool` | Self-healing pool that recreates sandboxes as tasks consume them. |

`shared_gateway` is the only mode without per-task isolation. It exists as a fallback when no sandbox is configured and auto-create or predeploy is unavailable.

## `strict_isolation`: the fail-closed switch

`strict_isolation` is a config-level boolean (`ExperimentConfig`, `alphadiana/engine/config/experiment_config.py:194`, default `False`). When `true`, any sandbox auto-create or predeploy failure becomes a hard `RuntimeError` instead of silently degrading to `shared_gateway` (`alphadiana/engine/runner.py:1017-1214`). This is the load-bearing knob for benchmark-fairness claims: it guarantees that a run either obtained real per-task isolation or aborted, rather than quietly producing numbers from a contaminated shared environment.

```yaml
# Abort the run if task-scoped isolation cannot be established.
strict_isolation: true
```

| Config key | Type | Default | Effect |
|---|---|---|---|
| `strict_isolation` | bool | `false` | `true` turns auto-create / predeploy failures into a hard error instead of falling back to `shared_gateway`. |

Both `isolation_mode` and `strict_isolation` are written into `run_metadata` and the run manifest, so the realized isolation of any run is auditable after the fact from its result store.

## Per-benchmark isolation matrix

| Benchmark | `openclaw` | `opencode` | `zeroclaw` |
|---|---|---|---|
| `terminal-bench-2` | Docker task container + Dockerized controller | Docker task container + Dockerized controller | Docker task container + Dockerized controller |
| `SWE-bench Pro` | official per-task SWE container via `swebench_docker` | official per-task SWE container via `swebench_docker` | official per-task SWE container via `swebench_docker` |
| `MMMU-Pro` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |
| `IMO-AnswerBench` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |
| `GPQA-Diamond` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |
| `HLE` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |

`opencode` still supports both `host` and `docker` controller modes, but the checked-in plain benchmark configs default to the Docker controller path. The local CLI path from the `zeroclaw` AIME tutorial is useful for debugging, but it is not the benchmark path described here.

## Cross-task safeguards

Beyond the runtime boundary, the engine adds defenses against state that escapes a single task:

- **OpenClaw integrity guard.** Responses flagged as session-tainted, stream-incomplete, or carrying heartbeat taint text are rejected (`_openclaw_integrity_guard_reason`, `alphadiana/engine/runner.py:459-480`) and recorded as errors rather than scored, guarding against leaked cross-task state.
- **Secret redaction.** Command, stdout, and stderr in sandbox metadata are scrubbed of credential assignments before persistence (`alphadiana/engine/runner.py:45-97`); the result store also redacts sensitive keys (`alphadiana/analysis/io/result_store.py`).
- **ROCK TTL safety net.** `auto_clear_seconds` (default 3600) is a server-side container TTL, so leaked or abandoned containers are eventually reclaimed by ROCK itself.

## Preserved runtime artifacts

These benchmark paths preserve readable, task-scoped runtime artifacts in the result record. This is task-local evidence, not cross-task memory.

- `openclaw` preserves stable aliases such as `openclaw_session.jsonl`, `openclaw_workspace_listing.txt`, `openclaw_workspace_state.json`, `openclaw_runtime_config.json`, `openclaw_request_payload.json`, and `openclaw_selected_response.json`.
- `opencode` preserves the main event/session stream plus task-local state under aliases such as `opencode_session.jsonl`, `opencode_workspace_listing.txt`, `opencode_config.json`, `memory/opencode_db_files.json`, and `memory/opencode.db.summary.json`.
- `zeroclaw` preserves the equivalent task-local CLI evidence: `config.toml`, `workspace_listing.txt`, readable `state/*` files, and `memory/brain.db.summary.json` when the runtime creates a memory database.

Credential-bearing JSON artifacts are redacted before they are persisted into the result payload.

## See also

- [Engine & Runner](../architecture/engine-and-runner) for how work items are expanded and dispatched.
- [Sandboxes](../architecture/sandboxes) for the ROCK / Docker / Podman backends.
- Harness pages: [`zeroclaw`](../harnesses/zeroclaw), [`opencode`](../harnesses/opencode), [`openclaw`](../harnesses/openclaw).
