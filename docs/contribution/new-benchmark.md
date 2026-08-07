---
sidebar_position: 3
---

# Adding a Benchmark

A benchmark in AlphaDiana is a `Benchmark` subclass that loads a dataset and
returns a list of `BenchmarkTask` objects. Classes self-register into the
`BenchmarkRegistry` by string name. The runner imports every benchmark module to
trigger that registration, then resolves the class with
`BenchmarkRegistry.get(benchmark_name)`. Adding a new benchmark is four edits:
subclass `Benchmark`, set `name` and `default_scorer`, register at the module
bottom, and add the import to `Runner.setup()`.

## The `Benchmark` contract

All benchmarks subclass `Benchmark` in `alphadiana/benchmarks/base.py`, an
ABC with one abstract method and one overridable hook:

```python
from typing import Any
from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.registry import BenchmarkRegistry


class MyBenchmark(Benchmark):
    name = "my_benchmark"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        ...

    def default_scorer(self) -> str:
        return "numeric"


BenchmarkRegistry.register("my_benchmark", MyBenchmark)
```

`load_tasks(config)` receives `benchmark.config` from the run YAML (a plain
`dict`) and returns the task list. `default_scorer()` returns `"exact_match"`
unless overridden; it only *suggests* a scorer, and the run config's
`scorer.name` always wins.

## `BenchmarkTask`

`BenchmarkTask` in `alphadiana/benchmarks/base.py` is the unit the runner
hands to a harness and the scorer:

| Field | Type | Notes |
| --- | --- | --- |
| `task_id` | `str` | Stable id, e.g. `aime_3`, `gpqa_12`. |
| `problem` | `str` | The prompt text shown to the agent. |
| `ground_truth` | `Any` | Polymorphic — see below. |
| `metadata` | `dict` | Default `{}`. Surfaced in results and the manifest. |
| `attachments` | `dict[str, bytes]` | Default `{}`. Image bytes for multimodal tasks. |

`ground_truth` is intentionally polymorphic. It is a plain string for text and
MCQ benchmarks (`aime`, `gpqa_diamond`, `hle`, `mmmu_pro`, `custom`,
`imo_answerbench`), a dict for `swebench_pro_os` (`{instance_id, repo,
base_commit}`), and the sentinel string `"1"` for `terminal_bench2`. Pick the
shape your scorer expects.

## Loading a HuggingFace dataset

For HF-backed benchmarks, use the `load_dataset_with_retry` helper in
`alphadiana/benchmarks/base.py` instead of calling
`datasets.load_dataset` directly. It wraps the load with up to three retries
(exponential backoff, `base_delay=2.0`, `max_delay=60.0`, jitter):

```python
from alphadiana.benchmarks.base import load_dataset_with_retry

dataset = load_dataset_with_retry(
    config.get("dataset"),
    config.get("data_config"),
    split=config.get("split", "test"),
)
```

It retries `ConnectionError`, `TimeoutError`, and generic exceptions, but does
**not** retry `NON_RETRYABLE_DATASET_ERRORS` (`PermissionError`,
`FileNotFoundError`, `ValueError`, `OSError`). It also detects a read-only HF
cache and raises an actionable "set `HF_DATASETS_CACHE`" error.

A new loader does not have to use HuggingFace at all. `terminal_bench2` reads a
local directory tree (`task.toml` + `instruction.md`) and has no network
dependency; `custom` reads problems inline from the YAML config.

## Reading config keys

Loaders read fields off `config` with `config.get(...)`. These keys are
conventional across the shipped benchmarks:

| Key | Used by | Meaning |
| --- | --- | --- |
| `dataset` | HF loaders | HF dataset path or repo id. |
| `data_config` | HF loaders | Dataset config name. |
| `split` | HF loaders | Split name (often `test`). |
| `problem_field` / `answer_field` | most | Column names for prompt / answer. |
| `max_tasks` | HF loaders | Cap task count; `0` short-circuits to an empty list. |
| `dataset_index` | HF loaders | Pin a single row (single-task smoke). |
| `dataset_indices` | HF loaders | Select rows; mutually exclusive with `dataset_index`. |

When you pin one task for a smoke run, set `dataset_index` alone. Do not also
set `max_tasks` together with a pre-sliced split.

## Registering the benchmark

Two things make a benchmark discoverable.

1. **Self-register at the module bottom.** Every shipped benchmark ends with an
   explicit call (the `register_benchmark` decorator in `registry.py` exists but
   is unused by the shipped loaders):

   ```python
   BenchmarkRegistry.register("my_benchmark", MyBenchmark)
   ```

   `BenchmarkRegistry` (`alphadiana/benchmarks/registry.py`) keeps a class-level
   `_registry` dict. `.get(name)` raises `KeyError` listing available names if
   the name is missing, and `.list()` returns the sorted names.

2. **Add the import to `Runner.setup()`.** Registration is import-side-effect
   driven, and `benchmarks/__init__.py` does **not** auto-import every module.
   `Runner.setup()` in `alphadiana/engine/runner.py` explicitly imports
   all benchmark modules before resolving the class:

   ```python
   import alphadiana.benchmarks.my_benchmark.benchmark  # noqa: F401
   ```

   Forgetting this import means `BenchmarkRegistry.get()` raises `KeyError` even
   though the file exists. The runner then calls
   `self.benchmark.load_tasks(self.config.benchmark_config)` and writes the
   per-task metadata into the run manifest.

## `memory_mode` metadata

`memory_mode` is a memory-experiment hook read by the agentic harnesses, **not**
core benchmark metadata. Only the `custom` benchmark injects it at load time in
`alphadiana/benchmarks/custom/benchmark.py`:

```python
metadata={"memory_mode": str(item.get("memory_mode", "build"))}
```

The three agentic harnesses read `task.metadata.get("memory_mode", "build")` and
special-case `"frozen"` to suppress the post-task memory store or fork a frozen
snapshot. See [`../harnesses/zeroclaw`](../harnesses/zeroclaw),
[`../harnesses/openclaw`](../harnesses/openclaw), and
[`../harnesses/opencode`](../harnesses/opencode). Most loaders should leave this
field out; the default everywhere is `"build"`.

## Running it

Select the benchmark by its registry name in the run config:

```yaml
benchmark:
  name: my_benchmark
  config:
    dataset: org/my-dataset
    split: test
    dataset_index: 0   # single-task smoke
scorer:
  name: numeric
```

```bash
# Quick diagnostic only; the command imports a subset of Runner modules
python -m alphadiana.cli list-benchmarks

# Run it
python -m alphadiana.cli run config.yaml -o run_id=smoke -o output_dir=./results
```

A smoke run passing means the path loaded tasks, invoked the agent mode, and
wrote scored results. It does not mean the model answered correctly.

## See also

- [Benchmark index](../benchmarks/) — the registered benchmarks and per-benchmark pages.
- [Harnesses overview](../harnesses/) — the `Agent` contract that consumes a `BenchmarkTask`.
- [Benchmark isolation](../concepts/isolation-and-fairness) — sandbox / containerization wording.
