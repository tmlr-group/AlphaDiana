# Memory Scope Micro Configs Design

## Goal

Ship one runnable AIME 2026 + Qwen3.5-27B reference configuration for every
combination of memory scope (`intra_task`, `cross_sample`, `cross_task`) and
agentic harness (OpenClaw, OpenCode, ZeroClaw). The nine configs are the
verified release seed; the historical 36-cell benchmark/model matrix can be
generated only after the seed is validated.

## Scope semantics

`intra_task` uses each harness's native memory tools during one solve but does
not retain state for the next work item. `cross_sample` enables native
persistence while processing the samples of one task in order, then resets the
harness and sandbox before the next task. `cross_task` enables native
persistence for the full, task-major run. Stateful scopes are always dispatched
sequentially.

The runner reads `agent.config.memory_scope`. It validates the three accepted
values, forces effective concurrency to one for stateful scopes, and owns the
cross-sample boundary. Harness implementations remain responsible for their
native stores: OpenCode's persistent HOME/session, OpenClaw's local-agent memory
plugin, and ZeroClaw's shared HOME/sqlite store.

## Lifecycle

Work items are already ordered `(task 0, sample 0..N), (task 1, sample 0..N)`.
For `cross_sample`, the runner recreates the configured agent and the shared
sandbox session immediately before sample zero of every task except the first.
This clears the previous task's native store while preserving state between
samples of the current task. For `cross_task`, neither is reset. For
`intra_task`, `persistent_memory` is disabled and existing per-execution paths
provide isolation.

Checkpoint resume cannot reconstruct arbitrary stateful history. Cross-Task
rejects a partial resume. Cross-Sample permits resume only after every sample
of each already-completed task is present; a partially sampled task requires a
new run ID or `--redo-all`.

## Verification

Unit tests cover scope parsing, concurrency coercion, work-item boundary
detection, and agent recreation without starting providers. All nine YAML files
must parse through `ExperimentConfig`, use `max_concurrent: 1`, and differ only
where harness mechanics require it. A real smoke uses one AIME task, two samples
for cross-sample, and two AIME tasks with one sample each for cross-task; traces
and native store diagnostics must show the expected visibility boundary.
