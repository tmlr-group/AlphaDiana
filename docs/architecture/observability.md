---
sidebar_position: 6
---

# Observability & Proxies

AlphaDiana preserves provider observations, normalized trajectories, runtime artifacts, and lifecycle status. Coverage is harness-dependent: no single proxy or trace format is used by every agent path.

## Logprob capture

The shared helpers live under `alphadiana/harness/proxies/`. `LogprobCaptureProxy` is an in-process HTTP proxy used by harnesses that must observe or override OpenAI-compatible requests made by an external CLI. It can:

- forward streaming and non-streaming chat-completion requests;
- capture token logprobs from JSON or SSE responses;
- apply explicitly configured request overrides;
- expose request summaries and captured records to the harness.

DirectLLM calls its provider directly and captures logprobs without this external-CLI proxy. OpenCode, OpenClaw, and ZeroClaw use proxy-backed capture only on the runtime paths that configure it. A result's metadata and artifact references are the authoritative evidence that capture actually occurred.

## Tool filtering

`tool_filter_proxy.py` is an experimental intervention proxy. It can filter advertised tools and strip harness-specific tool references from prompts. It is not a security sandbox: a model or runtime may still act through other available interfaces, and filtering a request does not isolate the surrounding process or container.

`harness_strip.py` contains harness-specific prompt transformations used by no-tools experiment cells. Keep these transformations scoped to the named experimental condition; they are not the normal harness contract.

## Trajectory normalization

Preservation helpers normalize provider and CLI events into two complementary views:

- `trajectory`: user, assistant, tool, and lifecycle events useful for reproducing the interaction;
- `reasoning_trajectory`: reasoning-oriented events when the provider/runtime exposes them.

The original response envelope, raw CLI output, request messages, and workspace artifacts may also be stored. Normalization is lossy and bounded; consult the raw artifact when a claim depends on exact event order or omitted fields.

## ZeroClaw runtime trace

Generic ZeroClaw configures its native runtime trace at `state/runtime-trace.jsonl` with `runtime_trace_max_entries = 200`. The harness:

- reads the bounded JSONL trace when present;
- builds normalized event and reasoning trajectories;
- preserves the trace as a workspace artifact;
- records whether runtime records were available;
- falls back to CLI output and request/final-response data when the trace is absent or incomplete.

With logprob capture enabled, the harness forces full runtime-trace mode. Persistent-memory runs preserve solve and store traces separately so the second turn does not erase the first turn's evidence.

## OpenCode and OpenClaw traces

OpenCode parses JSON output events, preserves its output stream and stderr, and may preserve a session trace or partial model output. The normalized trajectory should be read together with `returncode`, answer-source metadata, and controller artifacts.

OpenClaw preserves gateway request/response data, stream events, logs, and sandbox artifacts available on the selected path. The normal gateway path and `decodingtrust_openclaw_cli` path have different evidence shapes. Timeout-scored-zero responses are allowed to lack the normal completed-stream marker; non-timeout incomplete streams are rejected by the runner integrity guard.

## Lifecycle and live status

Runner lifecycle events feed terminal progress and the live status file at `status/dashboard.txt`. Sensitive-looking values are redacted before event persistence. The live status file is not the React Dashboard UI and is not the final report.

## Logprob artifacts

The result store can write raw float records and compact Int16 records when metadata uses the expected `logprob_records` and `logprob_int16_records` fields. See [Scoring & Results](./scoring-and-results) for the DirectLLM `logprobs_format: int16` caveat; do not infer sidecar validity merely from a filename.

Entropy and behavioral analysis operate on captured records and normalized trajectories. Always report the denominator: tasks without a usable trace or logprob sidecar are not evidence for trace-wide conclusions.

## Operational verification

For a claimed observable behavior, inspect at least:

1. the task record's `score_status`, finish reason, and harness metadata;
2. artifact references and the referenced files;
3. raw run log when execution stopped or output is incomplete;
4. provider request summaries for request overrides and streaming mode;
5. the normalized trajectory alongside its raw source.

## Related pages

- [Scoring & Results](./scoring-and-results)
- [DirectLLM](../harnesses/direct-llm)
- [OpenCode](../harnesses/opencode)
- [OpenClaw](../harnesses/openclaw)
- [ZeroClaw](../harnesses/zeroclaw)
