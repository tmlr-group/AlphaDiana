---
sidebar_position: 2
---

# Adding a Harness

A harness in AlphaDiana is an **Agent**: a class that turns a benchmark task into
an answer (and a trajectory of how it got there). The baseline
[`direct_llm`](../harnesses/direct-llm) is a single chat call; the agentic
harnesses [`opencode`](../harnesses/opencode),
[`openclaw`](../harnesses/openclaw), and [`zeroclaw`](../harnesses/zeroclaw)
drive an external CLI inside a sandbox. They all implement the same small
interface, so adding a new one is a matter of subclassing `Agent`, returning an
`AgentResponse`, and registering the class.

## 1. Subclass `Agent`

The base class lives at `alphadiana/harness/base.py`. It is an `ABC` with two
abstract methods and one optional hook:

```python
from typing import Any, Optional

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.base import Agent, AgentResponse


class MyAgent(Agent):
    name = "myagent"      # registry key, used as agent.name in config
    version = ""          # set by the runner from config.agent_version

    def setup(self, config: dict) -> None:
        # Read agent.config here; store endpoint, model, timeouts, etc.
        ...

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        # Do the work; return an AgentResponse.
        ...

    def teardown(self) -> None:
        # Optional; default is a no-op. Clean up containers/processes here.
        ...
```

Set the two class attributes `name: str` and `version: str`. The `name` is the
registry key that a config selects via `agent.name`. The runner instantiates the
class, assigns `agent.version` from the config, then calls `setup()` once and
`solve()` per task in `alphadiana/engine/runner.py`.

### `setup(config)`

`config` is the `agent.config` block from the YAML. Read every key you need here
and stash it on `self`. By convention, treat the literal API key `EMPTY`
(case-insensitive) as *unset* and fall back to the environment, and use
`sk-EMPTY` in configs for keyless local vLLM (the validator rejects literal
`EMPTY`). The shared logprob helper `resolve_logprob_capture_config(config)`
(`alphadiana/harness/proxies/logprob_capture.py`) gives you a consistent
`{enabled, top_logprobs}` if you want observability parity with the other
harnesses.

### `solve(task, sandbox)`

`task` is a `BenchmarkTask` (`task.problem`, `task.ground_truth`,
`task.metadata`, etc.). `sandbox` is provided when the config sets a
`sandbox.name`; the baseline path leaves it `None`. Do the actual solving and
return an `AgentResponse`.

## 2. Return an `AgentResponse`

`AgentResponse` is a `@dataclass` in `alphadiana/harness/base.py`. General answer
scorers grade `answer`, but benchmark-specific scorers may also consume response
metadata or artifacts. Check every target scorer before defining the contract.

| Field | Type | Purpose |
|---|---|---|
| `answer` | `Any` | Extracted answer used by general answer scorers. `None` marks a failed/timed-out attempt. |
| `trajectory` | `list[dict]` | Per-step record of the run. |
| `raw_output` | `str` | Full model/CLI output. |
| `token_usage` | `dict` | Prompt/completion token counts. |
| `token_entropy_stats` | `dict` | Logprob-derived entropy (from the logprob helper). |
| `wall_time_sec` | `float` | End-to-end latency. |
| `metadata` | `dict` | Free-form. Failure reasons, transport, status flags. |

The dataclass also carries extended observability fields absorbed from `codex-dev`
(all optional, default-empty): `reasoning_trajectory`, `request_messages`,
`response_json`, `sandbox_id`, `gateway_url`, `artifact_manifest`,
`gateway_log_excerpt`, `workspace_snapshot_paths`, `workspace_file_contents`,
`sandbox_metadata`, `system_prompt`, and `finish_reason`. Populate whatever your
harness can produce; they make a run auditable, and scorer-specific fields can
affect scoring.
Set `answer = None` and record a reason in `metadata` (for example
`failure_reason`) when a run cannot produce a gradable answer.

## 3. Register the class

The registry is `AgentRegistry` in `alphadiana/harness/registry.py`, a
classmethod-only singleton over a class-level `_registry: dict[str, Type[Agent]]`.

| Method | Behavior |
|---|---|
| `AgentRegistry.register(name, cls)` | Add `cls` under string key `name`. |
| `AgentRegistry.get(name)` | Return the class; raises `KeyError` listing available names if missing. |
| `AgentRegistry.list()` | Sorted list of registered names. |

Register **at the bottom of your agent module** so the call fires on import. A
`register_agent(name)` decorator exists, but the shipped agents register
directly in their implementation modules:

```python
# Example module: alphadiana/harness/<your_agent>/agent.py
AgentRegistry.register("myagent", MyAgent)
```

## 4. Wire the import into the Runner

Registration is **import-triggered, not auto-discovered**. The module-level
`register()` call only runs if the module is imported, and the engine imports
each agent module explicitly in `Runner.setup()` in
`alphadiana/engine/runner.py`. Add your module to that list:

```python
# alphadiana/engine/runner.py, in Runner.setup()
import alphadiana.harness.direct_llm        # noqa: F401
import alphadiana.harness.openclaw.agent    # noqa: F401
import alphadiana.harness.opencode.agent    # noqa: F401
import alphadiana.harness.zeroclaw.agent    # noqa: F401
import alphadiana.harness.myagent.agent     # noqa: F401  # <- add this
```

The same import-for-side-effect pattern registers benchmarks, sandboxes, and
scorers. **Forgetting the import line is the most common mistake** — the agent
looks registered (the `register()` call is in the file) but `AgentRegistry.get`
never sees it, so `agent.name: myagent` fails with a `KeyError` listing the names
that *were* imported.

## 5. Select it from a config

Once registered and imported, a config selects the agent by its `name`:

```yaml
agent:
  name: myagent
  config:
    model: Qwen/Qwen3.5-27B
    api_base: http://127.0.0.1:8011/v1
    api_key: sk-EMPTY
```

Validate, then run:

```bash
python -m alphadiana.cli validate <config.yaml>
python -m alphadiana.cli run <config.yaml>
```

## Optional: skills

If your harness should expose AlphaDiana skill bundles to the model, read the
`agent.config.skill_folder` key in `setup()` and mount the bundle into the
sandbox. The existing agents resolve it with a per-harness `_resolve_skill_folder`:
empty disables it; an absolute
path is used as-is; a value with `/` resolves against the cwd; a bare name
resolves to `alphadiana/harness/skills/<name>/` (for example `advanced-maths`).
Skills are **not** auto-injected into context, so your system prompt must instruct
the model to read the mounted `SKILL.md`.
