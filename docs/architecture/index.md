---
sidebar_position: 1
---

# Architecture Overview

AlphaDiana is an evaluation framework for foundation-model and agent systems. A run is described by a single YAML file and orchestrated by one top-level object, the `Runner`. The `Runner` resolves a benchmark, an agent (harness), an optional sandbox, and a scorer from four string-keyed registries, then for every (task, sample) work item it calls `agent.solve` -> `scorer.score` -> `ResultStore.append`, and finally emits a report.

This page traces that end-to-end data flow and links to the sub-pages that cover each layer in depth.

## End-to-end data flow

```
YAML config
    │  ExperimentConfig.from_yaml()        (alphadiana/engine/config/experiment_config.py)
    ▼
Runner.setup()                            (alphadiana/engine/runner.py:559)
    │  import every benchmark/harness/sandbox/scorer module (registry side effects)
    │  resolve classes by name: BenchmarkRegistry / AgentRegistry / SandboxRegistry / ScorerRegistry
    │  instantiate + agent.setup(), sandbox.setup() (if any), scorer.setup()
    │  build ResultStore(output_dir, run_id) and ReportGenerator
    ▼
Runner.run()                              (alphadiana/engine/runner.py:649)
    │  tasks = benchmark.load_tasks(benchmark_config)
    │  write run_manifest.json (expected counts, task ids, config metadata)
    │  work_items = [(task, sample_index) for task in tasks for si in range(num_samples)]
    │  subtract already-completed items (checkpoint resume) unless redo_all
    │  TaskDispatcher dispatches each item:
    │      response = agent.solve(runtime_task, sandbox_session)   → AgentResponse
    │      result   = scorer.score(task, response)                → ScoreResult
    │      result_store.append(task, response, result)            → JSONL + tasks/<id>.json
    │  report_generator.generate()                                → RunSummary
    ▼
Runner.teardown()                         (clean up sessions / sandboxes)
    ▼
Output: results/<run_id>.jsonl + results/<run_id>/{run_manifest.json, artifacts/, tasks/, lifecycle/, status/}
         + console report (accuracy, mean_score, Pass@N, Avg@N)
```

The framework is registry-driven dependency injection: `setup()` imports roughly thirty modules purely for their `@register` side effects, and everything downstream is resolved by string name. Adding a backend is a `register` call plus an import line. The `CLI` entry point (`alphadiana/cli.py`) wires `Runner.setup() -> run() -> teardown()` together in a `try/finally`.

## The CLI

The entry point is a Click group `main` in `alphadiana/cli.py`. The `run` subcommand loads the YAML, applies `-o` overrides, validates the config, runs preflight checks, then drives the `Runner`.

```bash
alphadiana run    <config.yaml> [-o agent.config.temperature=0.5 ...] [--redo-all]
alphadiana validate <config.yaml> [-o ...]
alphadiana report <results_dir>
alphadiana batch  <config1.yaml> <config2.yaml> ... [--parallel]
alphadiana env            # ROCK service + ownership health check
alphadiana list-benchmarks
```

Override syntax is `-o a.b.c=value`; dotted keys build a nested dict and values auto-cast to `bool`/`int`/`float`. `--redo-all` ignores the checkpoint and recomputes every item. See [Configuration](../configuration) for the full YAML schema and override rules.

## ExperimentConfig

`ExperimentConfig` is a dataclass (`alphadiana/engine/config/experiment_config.py:175`) built by `from_yaml`. The load pipeline is: `yaml.safe_load` -> expand `$VAR`/`${VAR}` env references -> deep-merge any `-o` overrides -> clear unresolved env placeholders to `""` -> apply agent env defaults (fill blank `api_base`/`api_key`/`model` from `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL_NAME`).

The YAML shape mirrors the four pluggable layers:

```yaml
run_id: "my-run"          # blank → uuid4().hex[:12]; any "/" becomes "_"

agent:
  name: direct_llm        # registry key: direct_llm | opencode | openclaw | zeroclaw
  version: "1.0"
  config: { ... }

benchmark:
  name: aime
  config: { ... }

sandbox: null             # null = no sandbox, or { name, config }

scorer:
  name: numeric
  config: { ... }

max_concurrent: 1
num_samples: 1
output_dir: "./results"
```

Selected top-level fields read by the engine:

| Field | Default | Purpose |
| --- | --- | --- |
| `run_id` | uuid4 hex[:12] | Namespaces all output paths; `/` is replaced with `_`. |
| `max_concurrent` | `1` | Upper bound on parallel work items (validated to `[1, 64]`). |
| `num_samples` | `1` | Samples per task; expands work items to `tasks × num_samples`. |
| `output_dir` | `./results` | Root for the JSONL and the `<run_id>/` artifact tree. |
| `redo_all` | `False` | Bypass checkpoint-resume and recompute everything. |
| `task_retries` | `0` (from YAML) | Retries per work item with exponential backoff. |
| `task_retry_on_recoverable_only` | `False` | Restrict retries to recoverable failures. |
| `strict_report` | `False` | Exit non-zero if scored items fall short of expected. |
| `strict_isolation` | `False` | Turn sandbox auto-create / predeploy failures into hard errors instead of degrading to a shared gateway. |

The `ConfigValidator` (`alphadiana/engine/config/validator.py`) returns blocking errors and non-fatal warnings. It enforces required names, the `agent_version` must contain a digit, `max_concurrent` in `[1, 64]`, `num_samples >= 1`, that `terminal_bench`/`osworld` declare a sandbox, and per-harness requirements (for example `openclaw` auto-deploy needs `rock_agent_config_path` + `openclaw_config_path`). It treats the literal string `"EMPTY"` and any unresolved `$VAR` as blank, so use `sk-EMPTY` (any non-`"EMPTY"` string) as the api key for local vLLM.

## The Runner

`Runner.setup()` imports and resolves the four backends, instantiates them, and calls each one's `setup()`. `Runner.run()` loads tasks, writes `run_manifest.json`, expands the work-item list, and hands items to a `TaskDispatcher`. For each item the per-work-item closure acquires a session, runs `agent.solve(task, sandbox_session)`, normalizes the answer for numeric scorers, scores, and appends the record. Per-item lifecycle events (`selected`, `launched`, `sandbox_started`, `agent_done`, `scorer_started`, `task_json_written`) are streamed to `results/<run_id>/lifecycle/`.

### Concurrency

The `TaskDispatcher` (`alphadiana/engine/task_dispatcher.py`) runs sequentially when `max_concurrent == 1`, otherwise it uses a `ThreadPoolExecutor(max_workers=max_concurrent)`. Retries apply exponential backoff (`min(2 * 2^attempt, 60)` plus jitter). When a sandbox is set and `max_concurrent > 1`, a fixed-size `SandboxPool` pre-creates sessions and recycles them across work items.

### Checkpoint resume

Resume is built on the result JSONL itself, not a separate state file. A work item counts as complete only when its record's `infer_score_status` is `valid_scored`; errors, timeouts, and no-answer records are intentionally left incomplete so a re-run retries exactly those. `redo_all: true` bypasses this entirely.

### Isolation modes

The `Runner` records an `isolation_mode` in the run metadata: `shared_gateway` (no sandbox), `explicit_sandbox`, `auto_single_sandbox`, `predeployed_pool` / `partial_predeploy`, `fresh_predeployed_pool`, and `partial_fresh_predeployed_pool`. `strict_isolation` is a fail-closed switch: any auto-create or predeploy failure becomes a hard `RuntimeError` rather than silently degrading to a shared gateway (which would cause cross-task workspace contention). See [Sandboxes & Isolation](./sandboxes) and the [benchmark isolation](../concepts/isolation-and-fairness) statement for the paper-safe wording.

## Result store and report

`ResultStore` (`alphadiana/analysis/io/result_store.py`) writes one JSON line per `(task_id, sample_index)` to `results/<run_id>.jsonl` and mirrors each record to `results/<run_id>/tasks/<task_id>.json`. Each record embeds the run metadata plus `problem`, `ground_truth`, `predicted`, `correct`, `score`, `rationale`, the normalized `trajectory` and `reasoning_trajectory`, `request_messages`, `response_json`, `token_usage`, sandbox metadata, and a `score_status`. Per-task raw runtime artifacts (response streams, session traces, normalized trace, workspace files) are preserved under `results/<run_id>/artifacts/<task_id>/`. `load()` dedupes by `(task_id, sample_index)` with last-write-wins.

`ReportGenerator.generate` (`alphadiana/analysis/report.py`) produces a `RunSummary` with `accuracy`, `mean_score`, `pass_at_k` (fraction of unique tasks with at least one correct sample), and `avg_at_k`. The CLI prints these and exits non-zero when `strict_report` fails.

## Where to go next

- [Harnesses](../harnesses) define the `Agent` ABC, the `AgentResponse` dataclass, and the `direct_llm`, [opencode](../harnesses/opencode), [openclaw](../harnesses/openclaw), and [zeroclaw](../harnesses/zeroclaw) agents.
- [Sandboxes & Isolation](./sandboxes) covers the `Sandbox` / `SandboxSession` abstraction and the `local`, `rock`, `podman`, and `swebench_container` backends.
- [Configuration](../configuration) documents the full run YAML schema, the `-o` override syntax, and run-id conventions.
- [Benchmarks](../benchmarks) lists the available datasets and scorers.
- [Getting Started](../getting-started) walks through installing AlphaDiana and running a first benchmark.
