# Contributing

AlphaDiana is extended at three points: you can **add a benchmark** (a new task
source), **add a harness** (a new way to run an agent over a task), or **add a
scorer** (a new way to grade an answer). All three follow the same wiring
pattern, so once you understand one you understand the others.

## The common thread: registries + import-for-side-effect

Every extensible component type has a string-keyed registry holding a
class-level `_registry: dict[str, Type[...]]`:

| Component | Registry | Base class | Selected by config key |
| --- | --- | --- | --- |
| Benchmark | `BenchmarkRegistry` (`alphadiana/benchmarks/registry.py`) | `Benchmark` (`alphadiana/benchmarks/base.py`) | `benchmark.name` |
| Harness / agent | `AgentRegistry` (`alphadiana/harness/registry.py`) | `Agent` (`alphadiana/harness/base.py`) | `agent.name` |
| Scorer | `ScorerRegistry` (`alphadiana/scorer/registry.py`) | `Scorer` (`alphadiana/scorer/base.py`) | `scorer.name` |

Each registry exposes the same classmethod API:

- `register(name, cls)` — register a class under a name.
- `get(name)` — look up by name; raises `KeyError` with the available names
  embedded in the message when missing.
- `list()` — sorted list of registered names.

The key thing to understand is that **registration is not auto-discovery**. A
class is only registered when its module is imported and the module-level
`register(...)` call (or `@register_*` decorator) actually runs. The runner
forces this by explicitly importing every component module in `Runner.setup()`,
with `# noqa: F401`, purely for the side effect:

```python
# benchmarks
import alphadiana.benchmarks.aime.benchmark   # noqa: F401
import alphadiana.benchmarks.gpqa.benchmark   # noqa: F401
# ... more benchmarks ...

# agents / harnesses
import alphadiana.harness.direct_llm          # noqa: F401
import alphadiana.harness.openclaw.agent      # noqa: F401
import alphadiana.harness.opencode.agent      # noqa: F401
import alphadiana.harness.zeroclaw.agent      # noqa: F401

# scorers
import alphadiana.scorer.exact_match          # noqa: F401
import alphadiana.scorer.numeric              # noqa: F401
import alphadiana.scorer.llm_judge            # noqa: F401
import alphadiana.scorer.math_verify_scorer   # noqa: F401
```

So adding any new component is always **two edits**: the `register(...)` call in
your module, plus an import line in `runner.py` so it fires. The runner then
resolves your config keys against the registries during `setup()`:
`BenchmarkRegistry.get(self.config.benchmark_name)`,
`AgentRegistry.get(self.config.agent_name)`, and
`ScorerRegistry.get(self.config.scorer_name)`.

## Add a benchmark

A benchmark turns a dataset into a list of `BenchmarkTask` objects. The
`BenchmarkTask` dataclass in `alphadiana/benchmarks/base.py` has `task_id`, `problem`,
`ground_truth`, `metadata`, and `attachments` (for multimodal inputs).

Subclass `Benchmark` and implement `load_tasks(config)`. The optional
`default_scorer()` method is a convention/hint only: it documents which scorer a
benchmark expects, but nothing calls it at runtime (it has zero callers). The
runner does **not** fall back to it. An unnamed scorer becomes `""`, and
`ScorerRegistry.get("")` raises, so `scorer.name` is always required in config.

```python
from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.registry import BenchmarkRegistry

class MyBenchmark(Benchmark):
    name = "my_bench"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        return [
            BenchmarkTask(task_id="1", problem="...", ground_truth="42"),
        ]

    def default_scorer(self) -> str:
        return "numeric"

BenchmarkRegistry.register("my_bench", MyBenchmark)
```

The built-in benchmarks call `BenchmarkRegistry.register(...)` directly at the
bottom of each implementation module; a `register_benchmark(name)` decorator also
exists in `registry.py`. For network datasets, prefer
`load_dataset_with_retry(...)` from `benchmarks/base.py`, which handles HF hub
rate limits and read-only cache errors. See [Adding a Benchmark](./new-benchmark.md)
for the full walkthrough, and the existing [Benchmarks](../benchmarks/) for
per-dataset examples.

## Add a harness

A harness is an `Agent`: the code that takes a `BenchmarkTask` and produces an
answer. This is the most involved extension point and has its own dedicated
walkthrough ([Adding a Harness](./new-harness.md)), but the contract is small. Subclass `Agent`, set `name`, and
implement `setup(config)` and `solve(task, sandbox=None) -> AgentResponse`
(plus an optional `teardown()`):

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

The runner drives one instance per run: it sets `agent.version`, calls
`setup(config)` once, then `solve(task, sandbox)` per task
in `Runner.run()`. A `sandbox` is passed only for harnesses that need one.

For the full `AgentResponse` field reference, the `direct_llm` baseline,
logprob-capture proxies, and the per-harness internals, start at the
[Harnesses overview](../harnesses/) and the individual pages:
[direct_llm](../harnesses/direct-llm.md), [opencode](../harnesses/opencode.md),
[openclaw](../harnesses/openclaw.md), [zeroclaw](../harnesses/zeroclaw.md). File
bundles a harness can mount into its sandbox are covered in
[Adding a Harness](./new-harness.md).

The shared startup, networking, image, and evidence checks for Podman-backed
harnesses are documented in [Podman Runtime Safeguards](./runtime-followups.md).

## Add a scorer

A scorer grades an `AgentResponse` against a `BenchmarkTask` and returns a
`ScoreResult` in `alphadiana/scorer/base.py` with `correct`, `score`, `expected`,
`predicted`, `rationale`, and `metadata`. Subclass `Scorer`, expose a `name`
property, and implement `score(task, response)`; override `setup(config)` if you
need configuration.

Scorers conventionally register via the `@register_scorer` decorator:

```python
from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer

@register_scorer("my_scorer")
class MyScorer(Scorer):
    @property
    def name(self) -> str:
        return "my_scorer"

    def score(self, task, response) -> ScoreResult:
        ok = str(response.answer) == str(task.ground_truth)
        return ScoreResult(
            correct=ok, score=1.0 if ok else 0.0,
            expected=task.ground_truth, predicted=response.answer,
        )
```

The built-in scorers (`exact_match`, `numeric`, `llm_judge`, `math_verify`) all
use this decorator in their implementation modules. Select your scorer
from config:

```yaml
scorer:
  name: my_scorer
  config: {}
```

The runner reads `scorer.name` into `scorer_name` and resolves it with
`ScorerRegistry.get(...)`. `scorer.name` is always required:
if it is omitted, `scorer_name` becomes `""` and `ScorerRegistry.get("")` raises
(the config validator also rejects an empty `scorer_name`). A benchmark's
`default_scorer()` is **not** consulted as a fallback; it is a convention/hint
only, with zero callers in the runtime path.

## Don't forget the import line

Whichever component you add, register the class **and** add its `# noqa: F401`
import to the matching block in `Runner.setup()` in
`alphadiana/engine/runner.py`. Without that import line the
module never loads, the `register(...)` call never runs, and `get(name)` raises
`KeyError` listing only the components that were imported.
