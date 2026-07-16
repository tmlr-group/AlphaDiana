---
sidebar_position: 6
---

# Observability & Proxies

AlphaDiana inserts HTTP proxies between a harness and the upstream LLM for two
unrelated reasons, and they have opposite lifecycles. One captures per-token
logprobs for offline analysis without changing the request; the other is an
out-of-process intervention tool used in micro-experiments to filter tools,
strip prompts, and override reasoning. This page covers both, plus the
trajectory normalization layer, the int16 logprob format, and the behavioral
metrics catalog computed from trajectories.

Everything here lives under `alphadiana/harness/proxies/` (the proxies and
normalizers) and `alphadiana/analysis/` (the offline metrics).

## Two proxies, opposite lifecycles

| | `LogprobCaptureProxy` | `tool_filter_proxy` |
|---|---|---|
| File | `alphadiana/harness/proxies/logprob_proxy.py:938` | `alphadiana/harness/proxies/tool_filter_proxy.py:34` |
| Stack | threaded `http.server` (`_LogprobCaptureServer` = `ThreadingMixIn` + `HTTPServer`) | `aiohttp` (`Proxy` class) |
| Lifecycle | in-process, started by the harness agent | out-of-process CLI launched manually |
| Purpose | observability (capture logprobs) | experimental intervention (mutate the request) |
| `trust_env` | n/a | `trust_env=True` (honors `HTTP(S)_PROXY`) |
| Wired into agents | yes (zeroclaw / openclaw / opencode) | no (launched manually as a standalone CLI) |

The two are never wired together. `LogprobCaptureProxy` is a passive observer;
`tool_filter_proxy` is an active editor of the request stream.

## LogprobCaptureProxy (in-process observability)

The harness agents (ZeroClaw, OpenClaw, OpenCode) spin up a
`LogprobCaptureProxy` so the in-sandbox CLI agent's OpenAI-compatible traffic is
routed through it. The proxy injects `logprobs=true` / `top_logprobs` into every
`POST /chat/completions` (`do_POST`, `logprob_proxy.py:646`), can normalize
system messages, ensure a user message exists, apply request overrides, force
upstream streaming, and capture request/response summaries.

It exposes:

- `proxy_url` / `local_url` — the advertised vs loopback URLs. The advertise
  host rewrites `0.0.0.0` to `127.0.0.1`; ZeroClaw binds it at `agent.py:1696`
  with `bind_host` / `advertise_host` resolved per sandbox.
- `captured_records()` / `drain_records()` — the accumulated per-token records.
- `request_summaries` / `response_summaries`.

The runner can also pre-deploy one proxy per gateway sandbox via
`start_logprob_proxy_for_gateway` and hand it to the agent through
`_predeployed_logprob_proxies` (`engine/runner.py`), so the proxy outlives a
single task when the sandbox is reused.

### The shared capture helper

`logprob_proxy.py` handles the in-sandbox path, but the actual record shaping is
shared with the no-harness baseline through
`alphadiana/harness/proxies/logprob_capture.py`, which every agent imports:

| Function | Role |
|---|---|
| `resolve_logprob_capture_config(config)` | `{enabled, top_logprobs}` from agent config |
| `apply_openai_logprob_request(payload, capture)` | sets `payload['logprobs']=True` + `top_logprobs` |
| `extract_openai_logprob_records(payload, ...)` | walks `choices[0].logprobs.content` into records |
| `finalize_logprob_capture(harness, ...)` | returns `(token_entropy_stats, response_metadata)` |

`finalize_logprob_capture` stamps the response metadata with a
`logprobs_capture_status` of `captured`, `requested_missing`, or
`not_requested`, plus a `logprob_capture_harness` tag. See
[Direct LLM](../harnesses/direct-llm) for how the baseline uses the same helper
directly (it builds its client with `httpx.Client(trust_env=False)` so inherited
proxy env vars cannot reach it).

Each captured record has the shape:

```json
{
  "token_index": 0,
  "token": " The",
  "logprob": -0.12,
  "top_logprobs": [{ "token": " The", "logprob": -0.12 }]
}
```

## tool_filter_proxy (experimental intervention)

`tool_filter_proxy` is a standalone `aiohttp` CLI proxy. It is **not imported by
any agent** — you launch it manually, point a harness at it, and it mutates
`POST /v1/chat/completions` (and `/chat/completions`); every other path is
passthrough. It is the request-side knob for the micro-experiments described in
[Evaluation Axes](../concepts/evaluation-axes).

It performs four kinds of mutation:

1. **Tool filtering** — `--allow` / `--block` regexes match `function.name`.
   Block wins over allow; an empty allow list means no whitelist.
2. **Prompt filtering** — strip or replace the system message via
   `--strip-prompt-tokens`, `--replace-system-prompt`, or the section-aware
   `--harness-strip` (see below).
3. **User-intro filtering** — `--strip-user-intro-tokens` strips AlphaDiana's
   prepended block before the `--- Problem ---` marker only.
4. **OpenRouter routing + reasoning override** — provider routing
   (`--ignore-providers` / `--only-providers`), reasoning control
   (`--reasoning-exclude` / `--reasoning-effort` / `--reasoning-max-tokens`),
   and reasoning re-tagging (`--rename-reasoning`, `--reasoning-plain`,
   `--reasoning-tag`, `--inject-final-tool-call`) to work around the OpenCode
   AI-SDK's reasoning-stream quirks.

It uses a 3600s upstream `ClientTimeout` and `trust_env=True` so it can reach
OpenRouter through `HTTP(S)_PROXY` on locked-down hosts.

```bash
python -m alphadiana.harness.proxies.tool_filter_proxy \
  --port 8900 \
  --upstream https://openrouter.ai/api \
  --api-key "$OPENROUTER_KEY" \
  [--model MODEL] \
  [--allow re,re | --block re,re] \
  [--strip-prompt-tokens t,t | --harness-strip {opencode,openclaw,zeroclaw} | --replace-system-prompt STR] \
  [--strip-user-intro-tokens t,t] \
  [--ignore-providers a,b | --only-providers a,b] \
  [--reasoning-exclude | --reasoning-effort {low,medium,high} | --reasoning-max-tokens N] \
  [--rename-reasoning [--reasoning-plain | --reasoning-tag think] [--inject-final-tool-call]]
```

:::warning Reasoning is an experimental variable, not plumbing
Do not add reasoning overrides to a `LogprobCaptureProxy` or to a production run
to "speed things up." Reasoning control belongs only on `tool_filter_proxy` in a
dedicated micro-experiment cell with its own run_id.
:::

## harness_strip: the no_tools cell

`alphadiana/harness/proxies/harness_strip.py` implements the section-aware
`--harness-strip` used to build the `no_tools` micro-cell — a harness with its
tool-documentation surgically removed from the system prompt, so you can measure
how much of a harness's effect is the scaffolding text versus the tools
themselves.

`strip_for_harness(harness, sys_text)` (`harness_strip.py:135`) dispatches to
`strip_zeroclaw`, `strip_openclaw`, or `strip_opencode`. Each deletes hard-coded
tool-doc section headers:

| Harness | Dropped section list | Notable headers |
|---|---|---|
| ZeroClaw | `_ZC_DROP_SECTIONS` | tool-doc headers |
| OpenClaw | `_OW_DROP_SECTIONS` | `## Skills (mandatory)`, `<available_skills>` |
| OpenCode | `_OC_DROP_SECTIONS` | `# Tool usage policy` |

`_split_sections` uses a header regex; deletion is a literal string replace, so
it neither over- nor under-deletes.

:::caution Brittle by design
The deletion strings are verbatim copies tied to a specific 2026-05-03
system-prompt snapshot per harness. Any later harness prompt change silently
breaks the strip (it under-deletes). Re-snapshot the system prompts before
trusting a fresh `no_tools` run.
:::

## Trajectory normalization (preservation.py)

`alphadiana/harness/proxies/preservation.py` normalizes raw harness runtime
artifacts (the messy per-CLI JSONL dumps) into the `AgentResponse`-shaped fields
the engine expects. All three harness agents import it.

| Function | Role |
|---|---|
| `parse_jsonl_records(raw_text)` | parse a runtime JSONL dump |
| `normalize_request_trajectory(request_messages)` | normalize the request side |
| `add_artifact_file_refs(...)` | merge file aliases into `manifest['files']` |
| `build_event_trajectories(...)` | build the event trajectory |
| `build_text_step_trajectories(...)` | returns `(trajectory, reasoning_trajectory)` |
| `build_runtime_trace_summary(...)` | builds the `response_json` envelope |
| `_runtime_record_to_steps(...)` | maps many record shapes (message / part / `choices` / `delta` / `tool_use`) into `{role, content, type, thinking}` |
| `_content_to_text(content)` | recursively flattens str / list / dict (including tool-call shapes) |

This is what lets ZeroClaw, OpenClaw, and OpenCode all land their wildly
different CLI output formats into one comparable trajectory shape. See the
per-harness pages for what each one emits:
[ZeroClaw](../harnesses/zeroclaw), [OpenClaw](../harnesses/openclaw),
[OpenCode](../harnesses/opencode).

## Int16 logprob sidecars

Raw-float logprob JSONL is bulky (~400-600 bytes per token), so AlphaDiana can
store a compact quantized form instead, controlled by the
`logprobs_format` agent-config key (`int16` or `float`). The conversion lives in
`alphadiana/analysis/logprobs.py`.

`quantize_records_int16(records, top_k=20)` rewrites each record from the
raw-float shape into:

```json
{
  "token_index": 0,
  "token": " The",
  "top20": [{ "token": " The", "prob_i16": 28911 }],
  "entropy_nats": 0.42
}
```

where `prob_i16 = round(p * 32767)` (clamped to `[0, INT16_PROB_SCALE]`,
`INT16_PROB_SCALE = 32767`) over the softmax-normalized top-K probabilities, and
`entropy_nats` is the Shannon entropy of the truncated+renormalized distribution
stored per token so downstream analyses don't recompute it.

:::note Lossy, one-way
The int16 transform discards the float logprob values. If you need the raw
floats later, set `logprobs_format: "float"` to keep raw floats; the default
`int16` is lossy/one-way.
:::

### Entropy aggregate

Independently of the per-token store, `_compute_entropy_stats`
(`alphadiana/analysis/entropy.py`) summarizes the whole record list into the
`token_entropy_stats` field on `AgentResponse`:

```json
{ "mean": 0.0, "max": 0.0, "p50": 0.0, "p90": 0.0, "n_tokens": 0 }
```

## Behavioral / trajectory metrics catalog

`alphadiana/analysis/trajectory_metrics.py` computes outcome-conditioned
metrics over normalized trajectories. Every event is first classified into one
of six canonical actions (`alphadiana/analysis/action_events.py`):

```
plan · reason · tool_use · verify · recover · answer
```

`compute_outcome_conditioned_metrics(...)` then produces the main metric family
(`MAIN_METRIC_NAMES`):

| Metric | What it captures |
|---|---|
| `DeltaVerifyShare` | verify-action share, correct minus incorrect |
| `DeltaToolUseShare` | tool-use share, correct minus incorrect |
| `AnswerAfterVerificationRate` | how often an answer follows a verify step |
| `ErrorRecoveryRate` | recovery after a failed/errored observation |
| `PrematureAnswerRate` | answering before verifying |
| `MotifOutcomeLift` | per-motif correct vs incorrect rate |
| `FailureCostRatio` | cost of failure trajectories |
| `LowEntropyLongCollapseRate` | long, low-entropy budget collapse |
| `ConfidenceInversion` | confidence vs correctness inversion |
| `PostToolEntropySeparation` | entropy shift around tool observations |
| `VerificationConversionRate` | verify steps that flip the outcome |
| `OperationalTaxAdjustedAccuracy` | accuracy net of scaffolding overhead |
| `PairedNetGain` | paired correct/incorrect net gain |
| `ScaffoldDominance` | how much outcome the scaffold drives |

The tail of the tuple (`ANALYZE_TOOLS_METRIC_NAMES = MAIN_METRIC_NAMES[7:]`) is
the entropy/tool-quality subset used by the analyze-tools scripts. Only
`valid_scored` sequences feed the deltas; the function also returns diagnostics
(`pooled_action_distribution`, `sequence_count`, `valid_scored_sequence_count`).

These metrics are the quantitative backbone of
[Harness-Aware Evaluation](../concepts/harness-aware-evaluation): they let two
harnesses with the same accuracy be distinguished by *how* they reached the
answer.
