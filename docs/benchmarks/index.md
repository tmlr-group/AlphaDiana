---
sidebar_position: 1
---

# Benchmarks Overview

A benchmark in AlphaDiana is a self-registering loader that turns a dataset into
a list of `BenchmarkTask` objects. The runner resolves the loader by name,
calls its `load_tasks(config)`, and feeds each task to the selected harness and
scorer. This page covers the task data model, the registered benchmark names and
their default scorers, how a benchmark is selected from config, and the smoke vs
full convention. Per-benchmark detail lives in the sibling pages linked below.

## The `BenchmarkTask` data model

Every loader emits `BenchmarkTask` instances
(`alphadiana/benchmarks/base.py:26-34`):

| Field | Type | Notes |
| --- | --- | --- |
| `task_id` | `str` | Stable per-benchmark id (e.g. `aime_3`, `gpqa_12`, `tb2_<dir>`). |
| `problem` | `str` | The prompt text shown to the agent. |
| `ground_truth` | `Any` | Polymorphic; shape depends on the benchmark (see below). |
| `metadata` | `dict` | Defaults to `{}`; carries source/category/scoring side data. |
| `attachments` | `dict[str, bytes]` | Defaults to `{}`; images are stored here (e.g. `image_1`). |

### `ground_truth` is intentionally polymorphic

`ground_truth` is typed `Any` and its shape varies by benchmark. Treat it
per-benchmark, never as one type:

- **String** for text, multiple-choice, and patch benchmarks: `aime`, `gpqa_diamond`,
  `hle`, `mmmu_pro`, `imo_answerbench`, `custom`, and the SWE-bench patch.
- **Dict** for `swebench_pro_os` (`{instance_id, repo, base_commit}`) and
  `external_benchmark` (`{level, problem_id, name}`).
- **The literal string `"1"`** for `terminal_bench2`: the scorer compares
  `response.answer.strip() == "1"` against the reward observed by the verifier.

### Robust dataset loading

HuggingFace-backed loaders fetch through `load_dataset_with_retry`
(`base.py:37-87`): up to three attempts with exponential backoff (base `2.0s`,
cap `60.0s`, jitter) for transient `ConnectionError`/`TimeoutError`. It does not
retry `NON_RETRYABLE_DATASET_ERRORS` (`PermissionError`, `FileNotFoundError`,
`ValueError`, `OSError`), and a read-only HF cache is converted into an
actionable error asking you to set `HF_DATASETS_CACHE`.

## Registered benchmarks and default scorers

A `Benchmark` subclass sets a class attribute `name`, implements
`load_tasks(config: dict) -> list[BenchmarkTask]`, and may override
`default_scorer()` (the base default is `exact_match`). Each module ends with an
explicit `BenchmarkRegistry.register(<name>, <cls>)` call.

| `benchmark.name` | Default scorer | Source / shape |
| --- | --- | --- |
| `aime` | `numeric` | HF math dataset (e.g. `MathArena/aime_2026`). |
| `gpqa_diamond` | `exact_match` | `fingertap/GPQA-Diamond`; A/B/C/D MCQ. |
| `hle` | `exact_match` | `cais/hle` (gated); multiple-choice. |
| `mmmu_pro` | `exact_match` | `MMMU/MMMU_Pro`; multimodal MCQ. |
| `imo_answerbench` | `imo_verify` | `Hwilner/imo-answerbench`; math-answer verify. |
| `custom` | `numeric` | Inline `problems` defined in YAML. |
| `swe_bench` | `swe_bench` | `SWE-bench/SWE-bench_Verified`; ground truth is a patch. |
| `swebench_pro_os` | `swebench_pro` | `ScaleAI/SWE-bench_Pro`; ground truth is a dict. |
| `terminal_bench2` | `terminal_bench2` | Local task tree; ground truth is `"1"`. |
| `external_benchmark` | `external_benchmark` | KernelBench GPU-kernel optimization. |

Note that the SWE-bench Pro registry name is `swebench_pro_os` (class
`SWEBenchProBenchmark`), while its scorer name is `swebench_pro`. The default
scorer is only a suggestion; `scorer_name` in config wins when set.

`BenchmarkRegistry` (`alphadiana/benchmarks/registry.py`) keeps a class-level
`_registry` dict. `BenchmarkRegistry.get(name)` raises `KeyError` listing the
available names if the loader was never imported, and `BenchmarkRegistry.list()`
returns the sorted names. A `register_benchmark(name)` decorator also exists, but
the shipped benchmarks use the explicit `.register()` call form instead.

List the registered names at any time:

```bash
python -m alphadiana.cli list-benchmarks
```

## How a benchmark is selected

Registration is import-side-effect driven. Importing the package does not
auto-load every loader, so `Runner.setup()` (`alphadiana/engine/runner.py`)
explicitly imports all ten `benchmark.benchmark` modules (plus the scorer and
harness modules) before resolving the class:

```python
BenchmarkRegistry.get(self.config.benchmark_name)
```

`Runner.run()` then calls `self.benchmark.load_tasks(self.config.benchmark_config)`
and writes the resulting per-task metadata into the run manifest. Adding a new
benchmark file without adding it to that explicit import block would make
`get()` raise `KeyError` even though the file exists on disk.

In config, you select the benchmark by its registry-name string:

```yaml
benchmark:
  name: aime          # one of the names in the table above
  config:
    dataset: MathArena/aime_2026
    split: train
    num_samples: 4
```

### Common `benchmark.config` keys

These keys are read by the loaders via `config.get(...)`. Not every key applies
to every benchmark; see the per-benchmark page for the full set.

| Key | Used by | Meaning |
| --- | --- | --- |
| `dataset` | most HF loaders | HF dataset path/slug (required for several). |
| `data_config` | `aime`, `mmmu_pro` | HF dataset config name. |
| `split` | most HF loaders | Dataset split (default `train` or `test` by loader). |
| `problem_field` / `answer_field` | text loaders | Column names for the prompt / answer. |
| `max_tasks` | all HF loaders | Cap on number of tasks; `0` short-circuits to an empty list. |
| `dataset_index` | most HF loaders | Pin a single row by index (smoke convention). |
| `dataset_indices` | most HF loaders | Select multiple rows; mutually exclusive with `dataset_index`. |
| `seed` | `gpqa_diamond` | Per-task MCQ shuffle seed (default `42`, stable across runs). |
| `category` / `category_field` | `hle`, `imo_answerbench` | Optional category filter. |
| `answer_types` | `hle` | Keep only rows whose answer type is in the list. |
| `subset` / `instance_ids` / `repos` | `swebench_pro_os` | `smoke` (default) vs `all`; explicit instance/repo filters. |
| `task_ids` / `taskset_path` / `include_hints` | `swe_bench` | Instance selection and hint inclusion. |
| `tasks_dir` / `categories` / `task_ids` | `terminal_bench2` | Local task tree and dir-name filters. |
| `levels` / `problems` / `max_rounds` / `backend` / `source` | `external_benchmark` | KernelBench selection and run shape. |
| `problems` | `custom` | Inline list of `{id, problem, answer}` dicts. |

For AIME, `max_tasks` slices the split as `f"{split}[:{N}]"` rather than
post-filtering, unless a single index is pinned or the split is already sliced.

HF-backed loaders honor the HF mirror via `export HF_ENDPOINT=https://hf-mirror.com`
(and `HF_TOKEN` for the gated `cais/hle`). TerminalBench2 has no network
dependency; it reads tasks off disk and needs `tasks_dir` or the
`TERMINAL_BENCH2_DIR` environment variable.

## Smoke vs full convention

Configs under `configs/examples/` are smoke/debug configs. They intentionally
pin one task with `dataset_index` or `max_tasks` and should not be used for full
benchmark runs.

> Smoke-test success means the evaluation path loads tasks, invokes the selected
> agent mode, and writes scored results. It does not mean the model answered
> correctly.

For a single-task smoke, pin `benchmark.config.dataset_index=<i>`. Do not also
set `max_tasks` together with a sliced split such as `train[16:17]`; pick one
selection mechanism. For SWE-bench Pro, the default `subset: smoke` loads only
the five hardcoded `DEFAULT_SMOKE_INSTANCE_IDS`, so a full run needs
`subset: all`.

Common setup for the example configs:

```bash
source scripts/activate.sh

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

Run a benchmark and (optionally) produce a report:

```bash
python -m alphadiana.cli run <config.yaml> \
  -o run_id=my_run -o output_dir=./results/my_run -o num_samples=1
alphadiana report ./results/my_run
```

For most example configs, switching backends only requires changing the three
standard environment variables before `python -m alphadiana.cli run ...`. Some
smoke configs pin the model in YAML; for those, override
`agent.config.model` / `agent.config.api_base` / `agent.config.api_key` with `-o`.

## A note on `memory_mode`

`memory_mode` is a memory-experiment hook, not core benchmark metadata. Only the
`custom` loader injects it at load time (`metadata={"memory_mode": ...}`, default
`"build"`). The three agentic harnesses read `task.metadata.get("memory_mode", "build")`
and special-case `"frozen"` to suppress the post-task memory store. See
[Evaluation Axes](../concepts/evaluation-axes.md) and the
[harness pages](../harnesses/index.md) for how this ties into the Memory axis.

## Per-benchmark pages

- [AIME](./aime.md)
- [GPQA-Diamond](./gpqa-diamond.md)
- [HLE](./hle.md)
- [MMMU-Pro](./mmmu-pro.md)
- [IMO-AnswerBench](./imo-answerbench.md)
- [SWE-bench Verified](./swebench-verified.md)
- [SWE-bench Verified Mini](./swebench-verified-mini.md)
- [SWE-bench Pro](./swebench-pro.md)
- [Terminal-Bench 2](./terminal-bench-2.md)

## Where results land

Scored results are written by `ResultStore`
(`alphadiana/analysis/io/result_store.py`): one redacted JSON record per
`(task_id, sample_index)` appended to `{run_id}.jsonl`, plus per-task JSON,
artifacts, and logprob sidecars under `{run_id}/`. See
[Scoring & Results](../architecture/scoring-and-results) and the per-benchmark pages
for reading and reporting on a run.
