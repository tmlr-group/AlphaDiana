---
sidebar_position: 4
---

# Isolation & Fairness

AlphaDiana compares harnesses ([`direct_llm`](../harnesses/direct-llm), [`zeroclaw`](../harnesses/zeroclaw), [`opencode`](../harnesses/opencode), [`openclaw`](../harnesses/openclaw)) on the same benchmarks. For comparisons that permit tools or filesystem side effects, tasks should run in clean task-scoped environments. If one task could leave files, processes, or state behind for the next task, accuracy numbers would reflect leakage rather than capability. AlphaDiana provides task-scoped runtimes for supported harness paths and records the isolation mode that was actually realized.

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

The `Runner` records the chosen mode in `run_metadata["isolation_mode"]` and mirrors it into the run manifest in `alphadiana/engine/runner.py`. The mode is derived from whether a sandbox was configured, auto-created, or predeployed as a pool.

| `isolation_mode` | When it applies |
|---|---|
| `shared_gateway` | No sandbox configured; tasks share one gateway. Weakest boundary. |
| `explicit_sandbox` | `sandbox.name` is set in the config; the runner drives that backend. |
| `auto_single_sandbox` | One ROCK sandbox auto-created for the run. |
| `predeployed_pool` / `partial_predeploy` | A pool of ROCK sandboxes predeployed up front (gateway agents). |
| `fresh_predeployed_pool` / `partial_fresh_predeployed_pool` | Self-healing pool that recreates sandboxes as tasks consume them. |

`shared_gateway` does not establish per-task sandbox isolation. It can be the
intentional mode for a harness that does not use ROCK, or a fallback when an
OpenClaw ROCK auto-create/predeploy path is unavailable. The other labels
describe how the runner obtained a sandbox or pool, but they are still runtime
evidence rather than a formal security guarantee.

## `strict_isolation`: the fail-closed switch

`strict_isolation` is a config-level boolean (`ExperimentConfig`, default
`false`). For harness paths that use ROCK sandbox auto-creation or gateway
predeployment, `true` turns setup failures into hard errors instead of allowing
OpenClaw to fall back to `shared_gateway`. ZeroClaw already rejects a failed ROCK
auto-create even when this flag is false.

The flag does **not** make every harness use a per-task sandbox. For example,
`direct_llm` does not acquire a ROCK sandbox merely because this value is true.
Always inspect `isolation_mode` in `results/<run_id>/run_manifest.json` before
making an isolation claim about a run.

```yaml
# On ROCK auto-create/predeploy paths, abort instead of falling back.
strict_isolation: true
```

| Config key | Type | Default | Effect |
|---|---|---|---|
| `strict_isolation` | bool | `false` | `true` turns auto-create / predeploy failures into a hard error instead of falling back to `shared_gateway`. |

Both `isolation_mode` and `strict_isolation` are written into run metadata and
the run manifest. Use those recorded values, together with the configured
harness and sandbox backend, to audit the realized boundary.

## Per-benchmark isolation matrix

| Benchmark | `openclaw` | `opencode` | `zeroclaw` |
|---|---|---|---|
| `terminal-bench-2` | Docker task container + Dockerized controller | Docker task container + Dockerized controller | Docker task container + Dockerized controller |
| `SWE-bench Pro` | official per-task SWE container via `swebench_docker` | official per-task SWE container via `swebench_docker` | official per-task SWE container via `swebench_docker` |
| `MMMU-Pro` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |
| `IMO-AnswerBench` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |
| `GPQA-Diamond` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |
| `HLE` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |

`opencode` still supports both `host` and `docker` controller modes, but the checked-in plain benchmark configs default to the Docker controller path. The generic `zeroclaw` harness is sandbox-only and runs its CLI inside a live ROCK sandbox.

## Cross-task safeguards

Beyond the runtime boundary, the engine adds defenses against state that escapes a single task:

- **OpenClaw integrity guard.** Responses flagged as session-tainted, stream-incomplete, or carrying heartbeat taint text are rejected by `_openclaw_integrity_guard_reason` in `alphadiana/engine/runner.py` and recorded as errors rather than scored, guarding against leaked cross-task state.
- **Secret redaction.** Runner and result-store helpers scrub recognized credential assignments and sensitive keys before persistence. This is best-effort defense in depth, not a substitute for reviewing artifacts and running a secret scan before publication.
- **ROCK TTL safety net.** `auto_clear_seconds` is a server-side container TTL. Explicit ROCK sandbox config defaults to 3600 seconds; auto-created OpenClaw/ZeroClaw paths default to 7200 seconds, and dashboard-oriented paths may use 28800 seconds. Record the effective value rather than assuming one global default.

## Preserved runtime artifacts

These benchmark paths preserve readable, task-scoped runtime artifacts in the result record. This is task-local evidence, not cross-task memory.

- `openclaw` preserves stable aliases such as `openclaw_session.jsonl`, `openclaw_workspace_listing.txt`, `openclaw_workspace_state.json`, `openclaw_sessions_index.json`, `openclaw_runtime_config.json`, `openclaw_request_payload.json`, and `openclaw_selected_response.json`.
- `opencode` preserves the main event/session stream plus task-local state under aliases such as `opencode_session.jsonl`, `opencode_workspace_listing.txt`, `opencode_config.json`, `memory/opencode_db_files.json`, and `memory/opencode.db.summary.json`.
- `zeroclaw` preserves the equivalent task-local CLI evidence: `config.toml`, readable `state/*` files, and runtime artifacts such as `status.json`, `runtime_trace.jsonl`, and `provider_exchange_summary.json`.

Recognized credentials in JSON artifacts are redacted on supported persistence
paths. Before sharing results, inspect the payload and run a secret scan because
unrecognized formats or third-party artifacts can still contain sensitive data.

## See also

- [Engine & Runner](../architecture/engine-and-runner) for how work items are expanded and dispatched.
- [Sandboxes](../architecture/sandboxes) for the ROCK / Docker / Podman backends.
- Harness pages: [`zeroclaw`](../harnesses/zeroclaw), [`opencode`](../harnesses/opencode), [`openclaw`](../harnesses/openclaw).
