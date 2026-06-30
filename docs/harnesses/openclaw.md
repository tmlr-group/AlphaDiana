---
sidebar_position: 4
---

# OpenClaw

The OpenClaw harness drives the OpenClaw coding/reasoning agent inside a
[ROCK](../concepts/) Docker sandbox through an HTTP gateway. The entry class is
`OpenClawAgent(Agent)` at `alphadiana/harness/openclaw/agent.py:954`.

There are two solve paths in the same harness:

- The **default path** streams OpenAI `chat/completions` through the gateway. It
  is fast and stateless, but it never runs OpenClaw's own agent loop, so the
  in-gateway memory plugin stays inert.
- The **local-agent path** (`persistent_memory: true`) runs embedded
  `openclaw agent --local` turns so the lancedb memory plugin's recall/capture
  hooks actually fire.

This page also covers the operational doctrine: recommended defaults, retry
semantics, and the result-integrity contract (see the sections below). See also
the sibling harnesses [direct_llm](./direct-llm), [opencode](./opencode), and
[zeroclaw](./zeroclaw).

## Topology

```text
AlphaDiana Runner
       │
       ▼
  ROCK Admin (:9000) + ROCK Proxy (:9001)
       │
       ▼
  ROCK Sandbox (Docker)
       │
       ▼
  OpenClaw Gateway (:8080)
       │
       ▼
  vLLM (OpenAI-compatible)
```

- **AlphaDiana** loads tasks, schedules them, scores, and writes results.
- **ROCK** runs the OpenClaw gateway inside a Docker container. The admin plane
  lives on `:9000`, the proxy on `:9001`.
- **OpenClaw** is the agent loop (multi-round LLM + tool calls) behind the
  gateway.
- **vLLM** serves the model over an OpenAI-compatible API.

The agent reaches the gateway through the proxy at
`<proxy>/sandboxes/<sandbox_id>/proxy/v1`.

### ROCK sandbox wrapper

`ROCKSession(SandboxSession)` lives at `alphadiana/engine/sandbox/rock.py:447`.
`_start_sandbox` builds a `ROCKClientSandbox` from a `SandboxConfig`
(`base_url`, `image`, `memory`, `cpus`, `startup_timeout`, `auto_clear_seconds`,
`use_kata_runtime`), calls `sandbox.start()`, retries smaller resource profiles
on failure, and overrides `sandbox.url` to the proxy URL.
`proxy_v1_base()` returns `<proxy>/sandboxes/<sandbox_id>/proxy/v1`. `execute(cmd)`
runs a command in the sandbox bash `default` session through the SDK.
`proxy_timeout` defaults to 1800s. The `ROCKSandbox.setup()` wrapper
(`rock.py:891`) defaults `auto_clear_seconds` to 3600s; the OpenClaw predeploy
path (`runner.py:886`, `runner.py:1176`) raises this to 7200s via
`rock_auto_clear_seconds`.

`setup()` selects a runtime manager from
`alphadiana/harness/openclaw/runtime.py` based on config:
`OpenClawRuntimeManager` for ROCK/Docker, `OpenClawPodmanRuntimeManager` for
podman (both in `runtime.py`), and `OpenClawContainerRuntimeManager`
(in `container_runtime.py`) for `runtime == 'swebench_container'`.
The manager owns gateway lifecycle (`ensure_ready`, `_wait_for_gateway`,
`_warmup_gateway`, `collect_artifacts`).

### Gateway config

`_build_openclaw_config` (`runtime.py:614`) loads `openclaw.json`, forces
`gateway.port`, sets `gateway.bind = 'lan'`, and removes `customBindHost`. This
override matters: the static `openclaw.json` defaults to `bind = custom` on
`127.0.0.1`, which would make the ROCK-published host port unreachable from the
evaluator. Auth mode is `token`, the `chatCompletions` endpoint is enabled, and
tools allow `group:fs` / `group:runtime` / `group:web`.

## Default solve path: chat/completions

`solve(task, sandbox)` (`agent.py:2054`) builds a single user message. There is
no system role; the gateway builds its own system prompt. If
`self._user_system_prompt` is set, it is prepended to `task.problem`. Multimodal
inputs go through `build_openai_multimodal_user_content`.

The default path is a streaming `chat/completions` POST to the gateway with a
bearer header `Authorization: bearer <gateway_token>`. The payload is built by
`_build_request_payload` (`agent.py:1664`): model, messages, temperature, stream,
and optionally `max_tokens` / `top_p` / `logprobs`. A retry loop
(`max_attempts`, default 5) applies exponential backoff, with errors classified
by `classify_error` (`agent.py:415`) into recoverable transport vs terminal.

### Circuit breaker

A persistent backend outage manifests as an empty-SSE 200 response: the gateway
returns HTTP 200 but the stream closes without ever sending `[DONE]`. After
`backend_down_threshold` (default 5) consecutive such responses, `_circuit_open`
trips and every later `solve()` call raises `BackendDownError` immediately
(`agent.py:1063`, `2055`) instead of grinding through full timeouts.

## Result integrity guard

Before scoring, the runner runs `_openclaw_integrity_guard_reason`
(`alphadiana/engine/runner.py:459`). It rejects an OpenClaw response (writing
`score_status = runtime_error`) when any of the following hold:

- `metadata.received_done is False` (and not a timeout-scored-zero result)
- `metadata.session_tainted is True`
- `finish_reason == "incomplete"`
- the trajectory, request/response payloads, or raw output contain
  `Read HEARTBEAT.md` or `HEARTBEAT_OK`

A valid scored result therefore requires all of:

| Condition | Required value |
| --- | --- |
| `score_status` | `valid_scored` |
| `metadata.received_done` | `True` |
| `metadata.session_tainted` | not `True` |
| heartbeat markers in trajectory / payload | absent |

Rejected responses still preserve the partial raw output, trajectory, response
JSON, sandbox artifacts, and logprob sidecars, and `runtime_error` records remain
checkpoint-rerunnable.

Session taint is detected by `_detect_session_pollution` (`agent.py:331`), which
sets `session_tainted` when prior-task chat history leaks into the reconstructed
trajectory. The agent rebuilds the full assistant trajectory by reading the
OpenClaw session JSONL out of the sandbox (`_parse_openclaw_session`,
`_retrieve_trajectory_from_sandbox_session`).

## Local-agent path: solve + store turns

### Why it exists

The plain `chat/completions` path never runs OpenClaw's agent loop, so the
memory-lancedb plugin's `before_agent_start` (autoRecall) and `agent_end`
(autoCapture) hooks never fire and the plugin stays inert (`agent.py:1687`). To
make memory real, `persistent_memory` mode runs an embedded
`openclaw agent --local` turn against the same provider, plugin, and `dbPath`.

`solve()` gates on `self._persistent_memory and sandbox is not None` and calls
`_try_solve_via_local_agent` (`agent.py:1893`). If it returns non-`None`, that
response is used; otherwise execution falls through to `chat/completions`.

### Two turns per task

Mirroring [ZeroClaw](./zeroclaw), each task runs two `--local` turns:

1. **SOLVE turn.** Prompt = the user text plus a `[Memory]` instruction telling
   the agent to call `memory_recall` before solving. Uses `request_timeout` and
   `thinking` from `reasoning_enabled` / `enable_thinking` (`high` or `off`).
2. **STORE turn.** A forced `memory_store` turn that guarantees per-task storage.
   Timeout is capped at `min(120s, request_timeout)` with `thinking = low`.

The local-agent config emitted by `_build_local_agent_config_json`
(`agent.py:1683`) uses provider `local` (`api = openai-completions`), adds
`memory_store` / `memory_recall` / `memory_forget` to `tools.allow`, and sets
the plugin to **`autoCapture: false`, `autoRecall: true`**. Question
auto-capture is deliberately disabled (it captures the user question, not the
solution) in favor of the explicit forced store-turn.

### ROCK 85s cap and detached execution

ROCK's `run_in_session` caps each synchronous call at roughly 85s, far short of
a minutes-long agent turn. `_run_local_agent_turn` (`agent.py:1789`) therefore
launches the turn detached:

```text
timeout <t> openclaw --log-level info agent --local \
  --session-id <sid> --thinking <high|off|low> --timeout <t> -m "$prompt"
```

It runs via `setsid` writing to files, then polls for an `agent_done` marker
every 10s (up to `agent_timeout + 240s`). Storage and recall are verified
out-of-band: it counts lancedb `_transactions` files before/after
(`txn_before` / `txn_after`) as storage evidence and greps gateway stderr for
`injecting N memories` / `prepended context` as recall evidence. Returns
`(reply, diag, done)`.

The resulting `AgentResponse` carries metadata
`{openclaw_local_agent: True, openclaw_local_solve_diag, openclaw_local_store_diag,
persistent_memory: True}`, `finish_reason = 'stop'`, and an answer extracted via
`_extract_answer` (prefers `\boxed{}`).

### memory_mode gating

`task.metadata['memory_mode']` defaults to `build`. When set to `frozen` (the
test/transfer phase), the STORE turn is skipped so later test problems do not
learn from one another; recall still fires via autoRecall.

| `memory_mode` | SOLVE turn | STORE turn |
| --- | --- | --- |
| `build` (default) | runs (recall) | runs (forced store) |
| `frozen` | runs (recall only) | skipped |

## lancedb memory config

When a `memory_lancedb` block is present, `_build_openclaw_config` injects
`plugins.slots.memory = 'memory-lancedb'` and the plugin config (`runtime.py:661`).
A setup command (`_openclaw_memory_lancedb_setup_command`, `runtime.py:719`)
copies the memory-lancedb TS source from `/app` or `/opt/openclaw` into
`OPENCLAW_HOME/.openclaw/extensions` and symlinks `node_modules`, because the
dist build in current images ships manifests without compiled entries.

The two paths use **different embedding defaults**:

| Key | Gateway path (`runtime.py`) | Local-agent path (`agent.py`) |
| --- | --- | --- |
| `model` | `all-MiniLM-L6-v2` | `qwen3-embed-0.6b` |
| `dimensions` | 384 | 1024 |
| `auto_capture` | `True` | forced `False` |
| `auto_recall` | `True` | `True` |
| `db_path` | `/tmp/oc_home/.openclaw/memory/lancedb` | same |

The `memory_lancedb` config block accepts: `api_key`, `model`, `base_url`,
`dimensions`, `db_path`, `auto_capture`, `auto_recall`.

:::note
The memory-experiment configs set `persistent_memory: true` **without** a
`memory_lancedb` block, so the embedding defaults apply unless `OPENAI_BASE_URL`
(and friends) supply the embed endpoint.
:::

### oracle_feedback four-tuple

`self._oracle_feedback = bool(config.get('oracle_feedback', False))`
(`agent.py:1047`). It changes only the STORE-turn prompt.

- **`False` (default).** The store prompt asks for the single most useful
  technique/insight from the agent's own solution. No ground truth is shown.
- **`True`.** The store prompt includes `task.problem`, the agent's own
  `solve_reply`, **and** `task.ground_truth` (the official correct answer). The
  agent self-grades `CORRECT` / `WRONG` and stores a four-tuple: problem, its own
  answer plus correctness, the official answer, and a feedback note. Wrong
  attempts are stored **labeled wrong** as negative examples rather than
  silently polluting memory, a designed mitigation for the memory-poisoning seen
  in the memory experiments.

## Fresh-per-task contract

Fresh-per-task is the recommended full-run mode. With
`reuse_predeployed_sandboxes: false`, every task gets a brand-new
gateway/session; after a task writes its result, the runner closes that sandbox
and warms a standby replacement, so stale OpenClaw chat history cannot leak.

The predeploy pool is configured from `agent.config`
(`alphadiana/engine/runner.py:799`):

| Key | Default | Meaning |
| --- | --- | --- |
| `num_sandboxes` | auto | active target (else `ceil(max_concurrent / per-sandbox)`) |
| `standby_sandboxes` | none | extra fresh-per-task replacements |
| `reuse_predeployed_sandboxes` | `false` | fresh-per-task contract |
| `reset_predeployed_between_tasks` | `true` | clear session state on reuse |
| `predeployed_lease_probe` | `true` | probe sandbox liveness before lease |
| `predeployed_lease_probe_timeout` | `2.0` | probe timeout (s) |
| `predeploy_replenish_concurrency` | auto | parallel standby refill |

A reuse mode (`reuse_predeployed_sandboxes: true`) still exists but must clear
OpenClaw session state between tasks; it is not the recommended full-run mode.

## Heartbeat anti-pattern

Liveness must **never** be sent as a prompt into the model session. The integrity
guard actively rejects any response whose trajectory or payload contains
`HEARTBEAT` markers. Operational heartbeats belong in a separate monitor log:

```text
logs/<run_id>.monitor.log      # operator/wrapper heartbeats, never the model session
logs/<run_id>.log              # the benchmark shell log
```

## Recommended defaults

For long local-vLLM full runs, use fresh-per-task predeployed sandboxes:

```yaml
max_concurrent: 1
task_retries: 2
task_retry_on_recoverable_only: true

agent:
  name: openclaw
  config:
    max_tokens: 131072
    request_timeout: 9300
    stream_idle_timeout: 9300
    stream_total_timeout: 9000

    num_sandboxes: 1
    standby_sandboxes: 1
    predeploy_replenish_concurrency: 1
    reuse_predeployed_sandboxes: false
    predeployed_lease_probe: true

    capture_logprobs: true
    top_logprobs: 20
```

For `max_concurrent: 2`, use two active and two standby sandboxes and set
`predeploy_replenish_concurrency: 2`. Keep concurrency low unless the local vLLM
queue is healthy: long thinking-on samples can run near 30 tokens/sec, so a
131072-token cap can need more than an hour per task after agent/tool overhead.

### Key config reference

| Key | Default | Notes |
| --- | --- | --- |
| `runtime` / `runtime_backend` | `''` / `''` | `''`, `podman`, or `swebench_container` |
| `api_base` | none | gateway / proxy base |
| `model` | `openclaw` | served model name |
| `gateway_token` | `OPENCLAW` | bearer token |
| `temperature` | `0.7` | |
| `max_tokens` | none | per-request cap |
| `stream` / `streaming` | `True` | |
| `max_attempts` | `5` | retry loop |
| `request_timeout` | `1800` | |
| `stream_idle_timeout` | `min(rt, 180)` | |
| `proxy_timeout` | `600` | |
| `backend_down_threshold` | `5` | circuit breaker threshold |
| `persistent_memory` | `False` | enables local-agent path |
| `oracle_feedback` | `False` | self-grading four-tuple store |
| `capture_logprobs` / `top_logprobs` | none | logprob capture |

## vLLM tool-calling & timeout layers

### vLLM serving requirement

OpenClaw drives multi-round inference through tool calling, so the vLLM server
must be started with `--enable-auto-tool-choice --tool-call-parser <parser>`.
Without it, vLLM returns HTTP 400
`auto tool choice requires --enable-auto-tool-choice` on the first tool-bearing
request.

The `<parser>` value is parser- and model-specific: older Qwen3-8B configs use
`hermes`, while current `Qwen3.5-27B` configs use `qwen3_coder`. Use the parser
named by your model card / config, not a fixed value.

### Layered timeouts for empty/timeout responses

An empty or timed-out OpenClaw response can be cut off at any one of several
layers. Check them in order:

1. **Gateway agent timeout.** `openclaw.json -> agents.defaults.timeoutSeconds`.
   AlphaDiana derives this from `agent.config.request_timeout` and writes it into
   **both** `agents.defaults.timeoutSeconds` and `tools.exec.timeoutSec`, and
   also sets ROCK `agent_run_timeout` (`runtime.py:484`, `633`, `650`, `1085`).
2. **ROCK proxy.** `proxy_service.timeout` (raise via the ROCK YAML / `ROCK_CONFIG`,
   e.g. `proxy_service.timeout: 600`). Older external ROCK checkouts hardcode
   `timeout=120` in `rock/sandbox/service/sandbox_proxy_service.py`; patch the
   per-request timeout to use `read=None` plus a bounded connect timeout.
3. **Client.** `agent.config.request_timeout` (default `1800`s; `agent.py:996`).
4. **undici stream watchdog.** The prebuilt embedded provider is patched via
   `OPENCLAW_UNDICI_STREAM_TIMEOUT_MS` (`runtime.py:677`). When diagnosing
   `~1800s` empty-response retries, inspect the sandbox `openclaw.json` and the
   generated ROCK `run_cmd` patch for `opts?.timeoutMs ?? 18e5`.
5. **`max_tokens`.** Set `65536`+ so thinking models do not exhaust the budget
   before emitting output. AlphaDiana auto-retries empty responses (~5 attempts;
   `empty_response` backoff starts ~60s).

## Logprob capture

`OpenClawLogprobProxy` (`runtime.py:254`) is a host-side MITM HTTP proxy reachable
from the container via the host bridge IP (default `host.docker.internal`). It intercepts
container-to-vLLM calls, injects `logprobs` / `top_logprobs` into the request, and
captures per-token records. The proxy is reused across tasks when upstream and
`top_logprobs` match, and is deliberately not torn down mid-stream under
`max_concurrent >= 2` so it does not kill a still-streaming task.

## Run lifecycle

```bash
# validate then run
python -m alphadiana.cli validate <config>
python -m alphadiana.cli run <config>

# standalone single-sandbox bring-up (prints Sandbox ID + API base)
python alphadiana/harness/openclaw/deploy/deploy.py \
  --image tmlrgroup/alphadiana:v1 --memory 4g --cpus 1 \
  --model-base-url <vllm> --model-name <model>
```

`deploy.py` creates one ROCK sandbox, waits for running, warms the default
session, installs the ROCK agent, runs the gateway, and prints `Sandbox ID` plus
`API base: <proxy>/sandboxes/<id>/proxy/v1`. It requires a reachable redis-stack
and a `ref/ROCK/.venv` symlink to the active Python env.

Checkpoint resume is scorer-aware: `run` skips only latest records that are
completed for the configured scorer, while latest `runtime_error` records remain
rerunnable. Results land at `results/<run_id>.jsonl`, with per-task sample lists
at `results/<run_id>/tasks/*.json`. The result store itself is
`alphadiana/analysis/io/result_store.py`.

Before launch, run the security gate:

```bash
python scripts/security_guard.py --check
```