# Benchmark Isolation Notes

Updated on April 20, 2026.

This note explains what "sandboxed" or "containerized" benchmark execution
means in AlphaDiana today for `openclaw`, `opencode`, and `zeroclaw`.

Short version:

- it is accurate to say AlphaDiana uses task-scoped sandbox or container
  runtimes on the supported benchmark paths
- it is not accurate to say every agent-benchmark pair always runs in a strong
  sandbox
- this is a practical evaluation boundary for reproducibility and host-side
  containment in normal use, not a formal security guarantee

## Paper-Safe Wording

Use wording like:

> AlphaDiana runs supported benchmark tasks in disposable task-scoped sandbox
> or container runtimes. These runtimes are intended to keep benchmark-side
> effects confined to the task environment in normal use rather than the host
> workspace. This is a practical evaluation boundary, not a formal security
> isolation claim.

Avoid wording like:

- "fully isolated from the host"
- "guaranteed not to affect the host"
- "all agents always run in a sandbox"

## Current Matrix

| Benchmark | `openclaw` | `opencode` | `zeroclaw` |
|---|---|---|---|
| `terminal-bench-2` | Docker task container + Dockerized controller | Docker task container + Dockerized controller | Docker task container + Dockerized controller |
| `SWE-bench Pro` | official per-task SWE container via `swebench_docker` | official per-task SWE container via `swebench_docker` | official per-task SWE container via `swebench_docker` |
| `MMMU-Pro` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |
| `IMO-AnswerBench` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |
| `GPQA-Diamond` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |
| `HLE` | ROCK sandbox | Dockerized controller in the checked-in config | ROCK sandbox |

## What This Means In Practice

- `openclaw` uses task-scoped runtimes across the benchmark paths covered in
  the current runbooks: ROCK for the standard text and multimodal benchmarks,
  Docker task containers for `terminal-bench-2`, and official SWE task
  containers for `SWE-bench Pro`.
- `zeroclaw` follows the same practical containment story on the documented
  benchmark runbooks. The local CLI path from the AIME tutorial is still useful
  for debugging, but it is not the benchmark path described here.
- `opencode` still supports both host and Docker controller modes, but the
  checked-in plain benchmark configs now default to the Docker controller path.
  Its `terminal-bench-2` and `SWE-bench Pro` paths were already containerized.
  April 20, 2026 OpenRouter/Qwen confirmation reruns on
  `GPQA-Diamond`, `IMO-AnswerBench`, `HLE`, and `MMMU-Pro` all wrote task JSON
  metadata with `controller_mode=docker` and
  `transport=opencode_cli_container`; the `MMMU-Pro` vision rerun also wrote
  `num_attachments=1` on all three tasks.

## If You Need The Cleanest Paper Story

- cite `openclaw` benchmark paths as sandboxed or containerized
- cite `zeroclaw` benchmark paths as sandboxed or containerized when you are
  referring to the documented benchmark runbooks rather than the local AIME
  tutorial path
- cite `opencode` as containerized on the checked-in benchmark configs,
  `terminal-bench-2`, `SWE-bench Pro`, and any custom run where
  `controller_mode=docker` is explicit

For the reviewer-facing audit table behind this note, see
[`context/benchmark-isolation-audit-20260419.md`](../context/benchmark-isolation-audit-20260419.md).
