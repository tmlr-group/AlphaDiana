---
sidebar_position: 1
---

# Harnesses Overview

A *harness* in AlphaDiana is an `Agent` implementation: the code that takes a
`BenchmarkTask` and produces an answer. The four built-in harnesses differ in how
much machinery sits between the model and the task, from a single chat call to a
full coding agent running inside a sandboxed container.

| Agent | Mechanism | Sandbox / runtime | Memory model | Multi-turn? | Relative speed |
| --- | --- | --- | --- | --- | --- |
| [`direct_llm`](#direct_llm) | One system + user chat to an OpenAI-compatible endpoint; no tools, no code execution | None (in-process `httpx.Client`) | None | No | Fastest |
| [`opencode`](./opencode) | Wraps the `opencode` CLI (opencode-ai 1.3.2) with its native session store | host / docker / podman controller, or SWE-bench in-container runtime | Native `--session` chaining + sqlite store; optional harness prompt-injection bank, `/compact`, freeze-snapshot | Yes | Medium |
| [`openclaw`](./openclaw) | Drives the OpenClaw agent through an HTTP gateway inside a ROCK sandbox | ROCK Docker sandbox (admin :9000 / proxy :9001 / gateway :8080), or podman / SWE-bench container | `memory-lancedb` plugin (gateway autoCapture/autoRecall); persistent-memory path runs `openclaw agent --local` turns | Yes | Slowest |
| [`zeroclaw`](./zeroclaw) | Runs the native `zeroclaw agent` Rust CLI inside a live sandbox | ROCK Docker sandbox, or podman runtime | sqlite/vector store via a second post-solve `memory_store` agent turn (shared HOME) | Yes | Slow |

Each harness has its own page:

- [direct_llm](./direct_llm) — the no-framework baseline.
- [opencode](./opencode) — the `opencode` CLI controller (host/docker/podman) plus memory experiments.
- [openclaw](./openclaw) — ROCK gateway agent with lancedb memory.
- [zeroclaw](./zeroclaw) — native Rust CLI agent with sqlite memory.

The shared [Skills](../skills) bundles (file bundles mounted into a harness
sandbox) are documented separately.

## The `Agent` contract

All harnesses subclass `Agent` (`alphadiana/harness/base.py:39`), an ABC with two
abstract methods and one optional hook:

```python
class Agent(ABC):
    name: str = ""
    version: str = ""

    @abstractmethod
    def setup(self, config: dict) -> None: ...

    @abstractmethod
    def solve(self, task: BenchmarkTask, sandbox=None) -> AgentResponse: ...

    def teardown(self) -> None: ...
```

The runner drives one instance per run: it sets `agent.version`, calls
`setup(config)` once, then `solve(task, sandbox)` per task
(`alphadiana/engine/runner.py:609-611`). A sandbox is passed only for harnesses
that need one (OpenClaw, ZeroClaw, SWE-bench container runs); `direct_llm`
ignores it.

### `AgentResponse` fields

`solve()` returns an `AgentResponse` dataclass (`base.py:12`). Core fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `answer` | `Any` | Extracted answer (often a `\boxed{}` value or a git diff) |
| `trajectory` | `list[dict]` | Normalized step list |
| `raw_output` | `str` | Sanitized model/CLI output |
| `token_usage` | `dict` | Prompt/completion token counts |
| `token_entropy_stats` | `dict` | Per-token logprob/entropy stats |
| `wall_time_sec` | `float` | Wall-clock solve time |
| `metadata` | `dict` | Free-form status (`finish_reason`, failure flags, transport, etc.) |

Extended observability fields (absorbed from codex-dev) add
`reasoning_trajectory`, `request_messages`, `response_json`, `sandbox_id`,
`gateway_url`, `artifact_manifest`, `gateway_log_excerpt`,
`workspace_snapshot_paths`, `workspace_file_contents`, `sandbox_metadata`,
`system_prompt`, and `finish_reason`. Responses are persisted by the result
store at `alphadiana/analysis/io/result_store.py`.

## The `AgentRegistry`

`AgentRegistry` (`alphadiana/harness/registry.py:10`) is a classmethod-only
singleton holding a class-level `_registry: dict[str, Type[Agent]]`:

- `AgentRegistry.register(name, cls)` — register a class.
- `AgentRegistry.get(name)` — look up by name; raises `KeyError` listing the
  available names if missing.
- `AgentRegistry.list()` — sorted list of registered names.

The four built-in keys are `direct_llm`, `opencode`, `openclaw`, and
`zeroclaw`; you select one via `agent.name` in the config.

### Import-triggered registration

Registration is **import-for-side-effect**, not auto-discovery. Each agent calls
`AgentRegistry.register(...)` at module bottom (e.g. `direct_llm.py:509`,
`opencode/agent.py:2111`, `zeroclaw/agent.py:2175`,
`openclaw/agent.py:3321`), and the runner explicitly imports every agent module
so those calls fire (`runner.py:573-583`):

```python
import alphadiana.harness.direct_llm        # noqa: F401
import alphadiana.harness.openclaw.agent     # noqa: F401
import alphadiana.harness.opencode.agent     # noqa: F401
import alphadiana.harness.zeroclaw.agent     # noqa: F401
```

Benchmarks, sandboxes, and scorers use the same pattern. Adding a new agent means
adding **both** the module-level `register()` call **and** an import line in the
runner.

### Registering a custom Agent

```python
from alphadiana.harness.base import Agent, AgentResponse
from alphadiana.harness.registry import AgentRegistry

class MyAgent(Agent):
    name = "my_agent"

    def setup(self, config: dict) -> None:
        ...

    def solve(self, task, sandbox=None) -> AgentResponse:
        return AgentResponse(answer=...)

AgentRegistry.register("my_agent", MyAgent)
```

Then reference it in your config:

```yaml
agent:
  name: my_agent
  config:
    ...
```

A `register_agent(name)` decorator also exists in `registry.py`, but the built-in
agents call `AgentRegistry.register(...)` directly.

## The `direct_llm` baseline {#direct_llm}

`DirectLLMAgent` (`alphadiana/harness/direct_llm.py:76`, `name="direct_llm"`) is
the no-harness baseline: a single system + user chat to an OpenAI-compatible
endpoint, with **no tools, no multi-turn, and no code execution**. It builds its
own client with `httpx.Client(trust_env=False)` so inherited SOCKS/HTTP proxy
env vars cannot break it.

### Config keys

Set under `agent.config`. Settings resolve via `_resolve_setting`
(`direct_llm.py:143`) with env fallbacks where noted.

| Key | Default | Notes |
| --- | --- | --- |
| `model` | — | Env fallback `OPENAI_MODEL_NAME` |
| `api_base` | — | Env fallback `OPENAI_BASE_URL` |
| `api_key` | `EMPTY` | Env fallback `OPENAI_API_KEY`; use `sk-EMPTY` for local vLLM (literal `EMPTY` is treated as unset) |
| `temperature` | `0.7` | |
| `top_p` | — | |
| `max_tokens` | auto | If unset, GETs `{api_base}/models`, reads `data[0].max_model_len`, uses `max_model_len - 8192` (fallback `131072`) |
| `max_completion_tokens` | — | |
| `request_timeout` | `600` | |
| `stream` | `true` | |
| `stream_total_timeout` | — | On expiry sets `answer=None`, `finish_reason='timeout'` |
| `max_retries` | `3` | Exponential backoff with jitter |
| `system_prompt` | asks for `\boxed{}` | |
| `enable_thinking` | — | |
| `extra_body` | — | |
| `capture_logprobs` | `true` | DirectLLM captures logprobs by default |
| `top_logprobs` | `20` | |
| `logprobs_format` | `int16` | `int16` quantizes via `analysis.logprobs.quantize_records_int16`; or `float` |

Answer extraction prefers `\boxed{}` (`utils.math_answer.extract_answer_candidate`).
Reasoning is recovered from `reasoning_content` / `reasoning` model-extra fields
(Volcengine / OpenRouter Kimi) and from `<think>...</think>` tags (Qwen3/vLLM).

### Run it

```bash
python -m alphadiana.cli validate configs/examples/direct_llm_gpqa_diamond.yaml
python -m alphadiana.cli run     configs/examples/direct_llm_gpqa_diamond.yaml
```

## Logprob capture and proxies

Two distinct proxies live under `alphadiana/harness/proxies/`, with opposite
lifecycles:

- **`LogprobCaptureProxy`** (`logprob_proxy.py:938`) is an *in-process* threaded
  HTTP proxy that the harness agents (zeroclaw / openclaw / opencode) spin up so
  the in-sandbox CLI's OpenAI traffic is routed through it. It injects
  `logprobs=True` / `top_logprobs` into `POST /chat/completions` and captures
  per-token records. This is observability, started by the agent itself.
- **`tool_filter_proxy.py`** (`Proxy` class at `tool_filter_proxy.py:34`) is a
  *standalone* aiohttp CLI proxy, launched manually and not imported by any
  agent. It is an experimental intervention layer that can filter tools by
  `--allow`/`--block` regex, strip or replace the system prompt
  (`--harness-strip {opencode,openclaw,zeroclaw}`), strip the AlphaDiana user
  intro, and route/override OpenRouter reasoning.

```bash
python -m alphadiana.harness.proxies.tool_filter_proxy \
  --port 9100 --upstream https://openrouter.ai/api/v1 --api-key "$KEY" \
  --allow 'memory_.*' --harness-strip zeroclaw
```

The shared logprob helper `logprob_capture.py` is imported by **all** agents
(`direct_llm` plus the three harnesses); its
`resolve_logprob_capture_config` / `apply_openai_logprob_request` /
`extract_openai_logprob_records` / `finalize_logprob_capture` functions
standardize how `token_entropy_stats` and the
`logprobs_capture_status` metadata are produced.

## Skills

Skills are file bundles under `alphadiana/harness/skills/<name>/`, each with a
top-level `SKILL.md`. They are selected with `agent.config.skill_folder` (a bare
name resolves to the shipped bundle; a path is used as-is) and mounted into the
sandbox per harness. They are **not** auto-injected into context; the system
prompt must instruct the model to read the mounted `SKILL.md`. See
[Skills](../skills) for the full reference.
