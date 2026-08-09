# OpenClaw

The generic `openclaw` harness normally calls an OpenClaw gateway through an OpenAI-compatible chat-completions endpoint. AlphaDiana makes one consolidated gateway request per attempt; a solve may retry up to `max_attempts` (default 5). The gateway internally runs OpenClaw's multi-turn orchestration, tool use, and context compaction, so calling it is not the same as bypassing the OpenClaw agent loop.

The harness also has an opt-in local-agent memory path.

## Runtime selectors

`runtime` and `runtime_backend` are separate keys:

| Key/value | Path |
| --- | --- |
| `runtime` unset | Standard OpenClaw runtime manager / gateway path |
| `runtime: swebench_container` | OpenClaw gateway inside a SWE-bench task container |
| `runtime_backend: podman` | Podman runtime manager |

## Standard gateway path

The harness resolves or starts the configured gateway, builds a user message, and sends a streaming or non-streaming request. The gateway handles agentic execution internally. AlphaDiana then parses the consolidated answer and attempts to recover session trajectory and workspace artifacts from the sandbox/runtime.

Common gateway inputs include `api_base`, `model`, `gateway_token`, sampling settings, `stream`, retry counts, and timeout controls. Runtime-backed configs may instead supply image/config paths from which the manager deploys the gateway and returns its effective API base.

## Fresh-per-task gateway pools

For concurrent task separation, the runner can predeploy a gateway pool and assign sessions per task. Pooling behavior is owned by the runner/runtime, not by model memory. Dead gateways are quarantined based on explicit liveness evidence, and replacements may be created when the live pool is exhausted.

Fresh task sessions reduce cross-task state leakage and file contention. They do not constitute a formal security guarantee; inspect backend, mounts, network, and credentials.

## Persistent-memory path

When `persistent_memory: true` and a live sandbox is available, `solve()` first tries the embedded local-agent path. This is implemented in the current source and is distinct from the ordinary gateway request.

The path runs:

1. a solve turn that can recall existing memory;
2. an eligible store turn that asks the agent to persist a lesson.

`task.metadata.memory_mode` supports build/frozen transfer experiments: frozen tasks may recall the built memory but skip the store turn. `oracle_feedback: true` changes the store prompt to reveal the official answer and request a self-graded problem/attempt/correct-answer/lesson record. This is a materially different experimental condition and must be reported.

`memory_lancedb` config is consumed by the runtime manager to install/configure the OpenClaw LanceDB memory plugin, set its embedding provider, and allow memory tools. Supply an explicit embedding base URL and credentials appropriate to the environment; a configured plugin does not prove that recall succeeded, so inspect the trace.

## Integrity and status

For ordinary non-timeout streams, the runner rejects responses that explicitly report `received_done=false`, finish as incomplete, or contain heartbeat/session-taint evidence. These rejections are rerunnable error records.

Timeout-scored-zero is the explicit exception: request or stream budget exhaustion can return `answer=None`, `finish_reason: timeout`, and `openclaw_timeout_scored_zero=true`. Such a response does not need `received_done=true`; after scoring it is `valid_scored` and checkpoint-complete.

Structured provider, tool, and control-plane errors remain errors rather than being converted to timeout zero.

## Timeout layers

| Key | Meaning |
| --- | --- |
| `request_timeout` | Overall request/runtime timeout input; also propagated into generated OpenClaw/ROCK settings where supported |
| `stream_idle_timeout` | Maximum quiet interval while reading a stream; defaults to at most 180 seconds |
| `stream_total_timeout` | Total stream budget even while chunks continue; defaults from `response_timeout` or `request_timeout` |
| `proxy_timeout` | Proxy-side request budget |
| `max_attempts` | Gateway request attempts |

Increasing `request_timeout` alone does not widen the default 180-second idle budget. Conversely, a stream that keeps emitting chunks can avoid the idle timer, so long local-provider validation should set `stream_total_timeout` explicitly.

## Logprobs and trajectories

Runtime-backed OpenClaw can place the shared logprob proxy between the gateway and upstream provider. Verify capture through task metadata, request summaries, and sidecar references.

The harness preserves the gateway response, stream status, logs, session/workspace artifacts, and normalized trajectories available on the chosen path. Standard gateway, local-memory, and SWE-bench paths produce different evidence shapes; do not assume one artifact is present everywhere.

## Config reference

| Area | Keys |
| --- | --- |
| Selector | `runtime`, `runtime_backend` |
| Gateway/provider | `api_base`, `api_key`, `model`, `gateway_token`, `temperature`, `top_p`, `max_tokens`, `stream` |
| Retry/timeout | `max_attempts`, `request_timeout`, `stream_idle_timeout`, `stream_total_timeout`, `proxy_timeout` |
| ROCK/pool | `rock_sandbox_url`, `sandbox_id`, `gateway_pool`, runtime image/config fields |
| Prompt/agent | `system_prompt`, `agent_md_mode`, `agent_md_content` |
| Memory | `persistent_memory`, `oracle_feedback`, `memory_lancedb` |
| Observability | shared logprob-capture and proxy bind/advertise settings |

## Artifacts to inspect

- stream status and `received_done`/finish reason;
- timeout or integrity-guard metadata;
- gateway request/response and session trace;
- sandbox/gateway identity and quarantine/replacement evidence;
- memory solve/store traces when enabled;

## Related pages

- [Harnesses Overview](./)
- [Sandboxes & Isolation](../architecture/sandboxes.md)
- [Scoring & Results](../architecture/scoring-and-results.md)
