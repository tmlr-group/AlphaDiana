# Harnesses Overview

AlphaDiana has four generic harness families plus benchmark-specific agent adapters. A harness implements the `Agent` contract; an adapter may bind one of those agents or a custom controller to a benchmark-specific container and verifier flow.

## Generic harness families

| Key | Execution model | Tools | Persistent-memory options |
| --- | --- | --- | --- |
| [`direct_llm`](./direct-llm.md) | Direct OpenAI-compatible provider request | No harness tool loop | None |
| [`opencode`](./opencode.md) | OpenCode CLI on host, Docker, Podman, or SWE-bench task container | OpenCode agent tools | Optional native session chaining, prompt bank, compaction, and freeze workflow |
| [`openclaw`](./openclaw.md) | OpenClaw gateway or local-agent memory path | OpenClaw tools | Optional solve/store path with LanceDB memory |
| [`zeroclaw`](./zeroclaw.md) | Native ZeroClaw CLI in a live sandbox or Podman runtime | ZeroClaw tools | Optional shared HOME, vector recall, and post-solve store turn |

These memory paths are opt-in and now exist in the current source. They are not implied by ordinary fresh-per-task runs, and their transfer-experiment controls must be configured explicitly.

## Benchmark-specific adapters

The agent registry also contains:

- `swebench_docker`;
- `terminal_bench2_docker`, `terminal_bench2_openclaw`, `terminal_bench2_opencode`, and `terminal_bench2_zeroclaw`.

These keys are selectable agents but are not additional generic harness families. Their benchmark runbooks define image, verifier, and artifact contracts.

## Agent contract

`Agent` in `alphadiana/harness/base.py` defines:

- `setup(config)` once per runner instance;
- `solve(task, sandbox)` once per work item;
- optional `teardown()`.

`solve()` returns an `AgentResponse`. Important fields include `answer`, `raw_output`, `finish_reason`, token/timing data, request/response envelopes, trajectories, sandbox metadata, workspace artifacts, and harness metadata. The scorer, not the harness, produces the universal top-level score.

## Registration

Agent registration is import-triggered. `Runner.setup()` imports all built-in harness and adapter modules before resolving the configured key. The current complete inventory is maintained in [Registries](../architecture/registries.md).

To add an agent:

1. implement `Agent`;
2. register a stable key;
3. import its module in `Runner.setup()`;
4. add config validation and focused tests;
5. run a real task in the intended container runtime before claiming support;
6. preserve intermediate artifacts needed to review the integration.

## Timeout and checkpoint behavior

Timeouts are not all rerunnable errors. The generic harnesses can emit scored-zero timeout responses with `finish_reason: timeout` and harness-specific metadata. Once scored, these are `valid_scored` and checkpoint-complete. Provider errors, context overflow, control-plane failures, session taint, and other non-timeout failures remain rerunnable.

See [Scoring & Results](../architecture/scoring-and-results.md) for the canonical validity rules.

## Observability

Logprob and trajectory coverage varies by harness and runtime:

- DirectLLM captures provider data directly;
- external CLI harnesses may use the shared logprob proxy;
- OpenCode parses JSON events;
- OpenClaw preserves gateway and local-agent evidence;
- ZeroClaw preserves a bounded native runtime trace plus normalized trajectories.

Do not infer capture from config inspection alone. Verify task metadata and sidecars from a real run. Shared proxy mechanics are documented once in [Observability & Proxies](../architecture/observability.md).

## Choosing a path

- Use DirectLLM for a provider baseline without an agent tool loop.
- Use OpenCode for a CLI coding/reasoning agent with selectable host or container controller.
- Use OpenClaw for gateway orchestration or its opt-in local-agent memory path.
- Use ZeroClaw when the native Rust CLI must run inside the benchmark sandbox or current Podman runtime.
- Use a benchmark-specific adapter when the benchmark runbook requires it; do not substitute a generic key solely because the underlying agent name is similar.

## Security boundary

Harness tool controls, prompt filtering, and container execution are separate mechanisms. None alone is a formal security guarantee. Review mounts, network access, secrets, controller mode, task artifacts, and sandbox provenance for the exact run.
