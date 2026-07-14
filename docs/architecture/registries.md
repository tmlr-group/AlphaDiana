---
sidebar_position: 3
---

# Registries

AlphaDiana resolves every pluggable component by a short string key. There are four registries, one per layer of a run: agents (harnesses), benchmarks, sandboxes, and scorers. A YAML config names the backend it wants (`agent.name`, `benchmark.name`, `sandbox.name`, `scorer.name`) and the [`Runner`](./engine-and-runner.md) looks the class up at setup time.

This keeps the config declarative: a run is fully described by four names plus their `config` blocks, and adding a backend never touches the engine.

## The four registries

Each registry is a classmethod-only singleton holding a class-level `_registry: dict[str, Type[...]]`. The API is identical across all four: `register(name, cls)`, `get(name)`, and `list()` (returns sorted keys). `get()` raises a plain `KeyError` whose message embeds the available names when the key is unknown.

| Layer | Registry class | Module | Base class | Config key |
| --- | --- | --- | --- | --- |
| Agent (harness) | `AgentRegistry` | `alphadiana/harness/registry.py` | `Agent` | `agent.name` |
| Benchmark | `BenchmarkRegistry` | `alphadiana/benchmarks/registry.py` | `Benchmark` | `benchmark.name` |
| Sandbox | `SandboxRegistry` | `alphadiana/engine/sandbox/registry.py` | `Sandbox` | `sandbox.name` |
| Scorer | `ScorerRegistry` | `alphadiana/scorer/registry.py` | `Scorer` | `scorer.name` |

Resolution happens in `Runner.setup()` (`alphadiana/engine/runner.py:604-620`):

```python
benchmark_cls = BenchmarkRegistry.get(self.config.benchmark_name)
agent_cls     = AgentRegistry.get(self.config.agent_name)
if self.config.sandbox_name:
    sandbox_cls = SandboxRegistry.get(self.config.sandbox_name)
scorer_cls    = ScorerRegistry.get(self.config.scorer_name)
```

The sandbox is only resolved when `sandbox.name` is set; harnesses that deploy their own gateway run with `sandbox: null`.

## How registration happens

Registration is **import-triggered**, not auto-discovered. There is no plugin scan: a backend is in the registry only if its module ran a `register()` call, and that call only fires if something imported the module. `Runner.setup()` performs those imports for side effect up front, before resolving anything (`alphadiana/engine/runner.py:561-600`):

```python
# Import all benchmark/agent/sandbox/scorer modules to trigger registration.
import alphadiana.benchmarks.aime.benchmark        # noqa: F401
import alphadiana.harness.direct_llm               # noqa: F401
import alphadiana.harness.opencode.agent           # noqa: F401
import alphadiana.harness.zeroclaw.agent           # noqa: F401
import alphadiana.harness.openclaw.agent           # noqa: F401
import alphadiana.engine.sandbox.rock              # noqa: F401
import alphadiana.scorer.exact_match               # noqa: F401
# ... one import line per registered backend
```

The `# noqa: F401` is deliberate: the imports look unused, but each one runs the module body, which is where the `register()` call lives. Same pattern for all four families.

The strings registered today:

| Layer | Registered names |
| --- | --- |
| Agents | `direct_llm`, `opencode`, `openclaw`, `zeroclaw` (plus benchmark-specific harnesses) |
| Sandboxes | `local`, `podman`, `rock`, `swebench_container` |
| Scorers | `exact_match`, `llm_judge`, `math_verify`, `numeric` |

`AgentRegistry.list()` (and the equivalents) returns the live sorted set, so the `KeyError` from a typo'd `agent.name` will show you exactly what is available.

## register() vs the register_* decorators

Every registry module also ships a decorator (`register_agent`, `register_benchmark`, `register_sandbox`, `register_scorer`) that wraps `Registry.register(name, cls)`. Both styles produce identical results; which one a module uses is a per-family convention:

- **Agents and benchmarks use the explicit call** at module bottom, e.g. `AgentRegistry.register("direct_llm", DirectLLMAgent)` (`alphadiana/harness/direct_llm.py:509`) and `BenchmarkRegistry.register("aime", AIMEBenchmark)` (`alphadiana/benchmarks/aime/benchmark.py:153`).
- **Sandboxes and scorers use the decorator**, e.g. `@register_sandbox("rock")` (`alphadiana/engine/sandbox/rock.py:862`) and `@register_scorer("exact_match")` (`alphadiana/scorer/exact_match.py:28`).

When in doubt, follow the convention of the sibling backends in the same package so the file reads consistently.

## Adding a backend

Two edits, always. First, register the class in its own module. Second, add the import line to `Runner.setup()` so the registration fires. Forgetting the second step is the usual cause of a `KeyError: "...not found. Available: [...]"` even though the class exists.

### 1. Register in the backend module

Agent example (matching the explicit-call convention):

```python
# alphadiana/harness/my_agent.py
from alphadiana.harness.base import Agent
from alphadiana.harness.registry import AgentRegistry

class MyAgent(Agent):
    name = "my_agent"
    version = "1.0"

    def setup(self, config: dict) -> None:
        ...

    def solve(self, task, sandbox=None):
        ...  # return an AgentResponse

AgentRegistry.register("my_agent", MyAgent)
```

Scorer example (matching the decorator convention):

```python
# alphadiana/scorer/my_scorer.py
from alphadiana.scorer.registry import register_scorer
from alphadiana.scorer.base import Scorer

@register_scorer("my_scorer")
class MyScorer(Scorer):
    ...
```

### 2. Add the import line to the runner

In `Runner.setup()` (`alphadiana/engine/runner.py`), add the module to the matching import block so its registration runs:

```python
# Import agent modules to trigger registration.
import alphadiana.harness.my_agent      # noqa: F401
```

### 3. Select it in YAML

```yaml
agent:
  name: my_agent
  version: "1.0"
  config:
    # backend-specific keys consumed by MyAgent.setup()
```

The `name` is the registry key; everything under `config` is passed straight to the backend's `setup()`, so the engine never needs to know the new keys.

## Related pages

- [Engine & Runner](./engine-and-runner.md) — where `setup()` runs the imports and resolves the four classes.
- [Architecture Overview](./index.md) — the end-to-end `solve` -> `score` -> `append` flow.
- [Harnesses](../harnesses/) — the agents behind the `agent.name` keys: `direct_llm`, [opencode](../harnesses/opencode.md), [openclaw](../harnesses/openclaw.md), and [zeroclaw](../harnesses/zeroclaw.md).
