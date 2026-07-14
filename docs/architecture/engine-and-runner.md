---
sidebar_position: 2
---

# Engine & Runner

The engine is the top-level orchestrator. Given a YAML config it loads an
`ExperimentConfig`, resolves the benchmark, agent, sandbox, and scorer from
string-keyed registries, expands tasks into work items, dispatches them through
a `TaskDispatcher`, and for each item runs `agent.solve` then `scorer.score`
then appends the result to a JSONL store. Everything lives under
`alphadiana/engine/` (the orchestrator and dispatcher) and
`alphadiana/engine/config/` (the config dataclass and validator).

## Core data flow

```
YAML Config
    │  ExperimentConfig.from_yaml()      # env expand → override merge → agent env defaults
    ▼
ConfigValidator.validate()              # hard errors block; warnings printed
    ▼
Runner.setup()
    │  import every backend module (registry side-effects)
    │  resolve Benchmark / Agent / Sandbox / Scorer by name
    ▼
Runner.run()
    │  benchmark.load_tasks(config) → tasks
    │  work_items = [(task, sample_index) for task in tasks for si in range(num_samples)]
    │  subtract already-completed items (checkpoint resume)
    │  for each work item:
    │    agent.solve(task, session)  → AgentResponse (answer, trajectory, ...)
    │    scorer.score(task, response) → ScoreResult (correct, score, ...)
    │    result_store.append(...)     → JSONL line + tasks/<task_id>.json
    │  report_generator.generate()    → RunSummary
    ▼
Runner.teardown()                       # release sessions, stop sandboxes
    ▼
results/<run_id>.jsonl + console report
```

## Runner lifecycle

The `Runner` (`alphadiana/engine/runner.py`) is driven by the CLI in a
`try/finally` block, so teardown always runs.

| Stage | What happens |
| --- | --- |
| `setup()` | Imports every benchmark/harness/sandbox/scorer module to trigger registry registration, then resolves classes via `BenchmarkRegistry.get` / `AgentRegistry.get` / `SandboxRegistry.get` / `ScorerRegistry.get`, instantiates each, and calls `agent.setup(agent_config)`, `sandbox.setup(sandbox_config)` (only when a sandbox is configured), and `scorer.setup(scorer_config)`. Builds the `ResultStore` and `ReportGenerator`. Any setup exception triggers `teardown()` and re-raises. |
| `run()` | Calls `benchmark.load_tasks(benchmark_config)`, writes `run_manifest.json`, expands work items, applies checkpoint resume, dispatches through `TaskDispatcher`, then generates the run report. |
| `teardown()` | Releases pool/predeployed sessions, closes per-task sessions, and stops sandboxes (ROCK containers also carry a server-side TTL safety net so leaked containers are eventually reclaimed). |

Backend wiring is registry-driven dependency injection: `setup()` imports roughly
thirty modules purely for their `@register` side effects, after which every
component is resolved by string name. Adding a backend means adding a `register`
call **and** an import line in the runner.

## ExperimentConfig

`ExperimentConfig` (`alphadiana/engine/config/experiment_config.py`) is a
`@dataclass` describing one run. Selected fields:

| Field | Default | Meaning |
| --- | --- | --- |
| `agent_name` | (required) | Registered agent key: `direct_llm`, `opencode`, `openclaw`, `zeroclaw`, ... |
| `agent_version` | (required) | Free-form version; must contain a digit |
| `benchmark_name` | (required) | Registered benchmark key |
| `scorer_name` | (required) | Registered scorer key |
| `run_id` | `uuid4().hex[:12]` | Namespaces all output; `/` is replaced with `_` |
| `agent_config` | `{}` | Passed to `agent.setup()` |
| `benchmark_config` | `{}` | Passed to `benchmark.load_tasks()` |
| `sandbox_name` | `None` | `None` means no sandbox (shared-gateway mode) |
| `sandbox_config` | `{}` | Passed to `sandbox.setup()` |
| `scorer_config` | `{}` | Passed to `scorer.setup()` |
| `max_concurrent` | `1` | Upper bound on parallelism |
| `output_dir` | `./results` | Root of the result tree |
| `redo_all` | `false` | Ignore the checkpoint and re-run everything |
| `num_samples` | `1` | Samples per task (pass@k / avg@k) |
| `task_retries` | `0` (via `from_yaml`) | Per-item retries on failure |
| `task_retry_on_recoverable_only` | `false` | Gate retries on recoverable failures |
| `strict_report` | `false` | Exit 1 if the report flags strict failures |
| `strict_isolation` | `false` | Fail closed on sandbox auto-create / predeploy failure |

`run_id` namespaces the output tree:

```
results/<run_id>.jsonl
results/<run_id>/
    run_manifest.json
    artifacts/<task_id>/
    tasks/<task_id>.json
    lifecycle/<task_id>[.sample_N].jsonl
    status/dashboard.txt
```

### `from_yaml` pipeline

`ExperimentConfig.from_yaml(path, overrides)` resolves a config in a fixed order:

1. `yaml.safe_load` the file (top level must be a mapping).
2. `_expand_env_vars` — recursively expand `$VAR` / `${VAR}` in string values via `os.path.expandvars`.
3. `deep_merge(data, overrides)` — non-mutating deep merge of CLI overrides on top.
4. `_clear_unresolved_env_placeholders` — any value still left as a bare `$VAR` placeholder becomes `""`.
5. `_apply_agent_env_defaults` — for API agents (`direct_llm`, `zeroclaw`, `opencode`, the `terminal_bench2_*` family, and `swebench_docker`), fill blank `api_base` / `api_key` from `OPENAI_BASE_URL` / `OPENAI_API_KEY`, plus the model field from `OPENAI_MODEL_NAME`. The model field is named `model` for `direct_llm` / `zeroclaw` but `model_name` for `opencode` (both sourced from `OPENAI_MODEL_NAME`). A field counts as blank when empty, the literal `EMPTY`, or an unresolved placeholder.

The YAML shape is:

```yaml
run_id: my_first_run            # optional; auto-generated if blank
agent:
  name: direct_llm
  version: v0.3.1
  config: { ... }               # → agent_config
benchmark:
  name: aime
  config: { ... }               # → benchmark_config
sandbox: null                   # or { name: rock, config: { ... } }
scorer:
  name: numeric
  config: { ... }               # → scorer_config
max_concurrent: 1
num_samples: 1
output_dir: ./results
metadata: { ... }
```

For local vLLM use `api_key: sk-EMPTY` (any non-`EMPTY` string); the literal
`EMPTY` is treated as blank by the validator.

## Work-item expansion

A run is the cartesian product of tasks and samples:

```python
work_items = [(task, si) for task in tasks for si in range(num_samples)]
```

Each item is bound to a runtime clone of the task carrying
`metadata.sample_index` and a fresh `metadata.execution_id`. `num_samples > 1`
is how pass@k and avg@k are produced; GPQA runs always use `num_samples: 1`
while AIME uses `num_samples: 4`.

## Checkpoint resume

Resume is built on the result JSONL itself, not a separate state file. Unless
`redo_all` is set, the runner subtracts already-completed items from the work
list before dispatching:

- `num_samples == 1` → subtract `ResultStore.completed_task_ids`.
- `num_samples > 1` → subtract `ResultStore.completed_sample_ids`.

Completion is decided by `is_valid_completed_record`
(`alphadiana/analysis/io/status.py`), which checks that
`infer_score_status(record)` equals the valid-scored status. Error, timeout,
and no-answer records are intentionally **not** counted as complete, so a re-run
retries exactly those items. `--redo-all` bypasses the check entirely. The
runner also warns when the configured scorer differs from prior records.

## TaskDispatcher

`TaskDispatcher` (`alphadiana/engine/task_dispatcher.py`) chooses its strategy
from `max_concurrent`:

- `max_concurrent == 1` → `_dispatch_sequential`, processing items in order.
- `max_concurrent > 1` → `_dispatch_concurrent`, a `ThreadPoolExecutor(max_workers=max_concurrent)`.

Both paths run each item through `_solve_with_retry`, which retries up to
`task_retries` times with exponential backoff (`min(2 * 2**attempt, 60)` plus
jitter). When `task_retry_on_recoverable_only` is true, a `retry_if` gate only
retries failures the harness marks recoverable. A shared `cancel_event` stops
submission and collection early. Each item produces an outcome dict:

```python
{"task_id": ..., "success": True,  "result": {...}}   # solve_fn return value
{"task_id": ..., "success": False, "error": "..."}    # exception string
```

`max_concurrent` is an upper bound, not a guarantee: after sandbox predeploy the
runner may lower it to the available sandbox capacity. See
[Sandboxes & Isolation](./sandboxes) for how concurrency interacts with
pooled and predeployed sandboxes, and the
[OpenClaw](../harnesses/openclaw) harness for its self-managed gateway pool.

## Lifecycle events

Per-item progress is appended to
`results/<run_id>/lifecycle/<task_id>[.sample_N].jsonl` at named stages:
`selected`, `launched`, `sandbox_started`, `agent_done`, `scorer_started`, and
`task_json_written`. These give a fine-grained audit trail of where each work
item is in the pipeline, separate from the final result record. The
`status/dashboard.txt` plain-text file gives a live run overview.

## ConfigValidator

`ConfigValidator` (`alphadiana/engine/config/validator.py`) is a hard gate run
before `Runner.setup()`. It exposes `validate()` (returns a list of **hard
errors** that block the run) and `warnings()` (returns **non-fatal** notices
that are printed but allow the run to proceed). Hard errors include:

- Missing `agent_name` / `agent_version` (must contain a digit) / `benchmark_name` / `scorer_name`.
- `max_concurrent` not an integer in `[1, 64]`; `num_samples < 1`; `task_retries < 0`.
- `imo_answerbench` must use the `imo_verify` scorer.
- `terminal_bench` and `osworld` require a sandbox.
- API agents need `api_base` (or the relevant auto-deploy fields, e.g. `rock_agent_config_path` + `openclaw_config_path` for openclaw, `rock_image` for zeroclaw).
- `opencode` `controller_mode` must be one of `host` / `docker` / `podman`; the docker mode preflights the controller image via `docker image inspect`.
- `swebench_docker` environment requirements and `terminal_bench2` `tasks_dir` existence.

Like the config loader, the validator treats the literal `EMPTY` and unresolved
`$VAR` placeholders as blank.

## CLI

The CLI (`alphadiana/cli.py`) is a Click group exposing the run engine:

```bash
# Run an experiment (override syntax: -o a.b.c=value, auto-cast to bool/int/float)
alphadiana run <config.yaml> [-o agent.config.temperature=0.5 ...] [--redo-all]

# Validate a config without running it
alphadiana validate <config.yaml> [-o ...]

# Regenerate the report from an existing results directory
alphadiana report <results_dir>

# Run several configs back-to-back (or --parallel)
alphadiana batch <config1.yaml> <config2.yaml> ... [--parallel] [-o ...]

# ROCK service + ownership health check; prints start commands
alphadiana env

# List registered benchmarks
alphadiana list-benchmarks
```

`run` parses overrides with `parse_override` (one `-o a.b.c=value` becomes a
nested dict, deep-merged), passes `--redo-all` as `redo_all: true`, loads the
config, runs `ConfigValidator` (exiting 1 on hard errors and printing
warnings), performs benchmark-specific preflight, then drives
`Runner.setup()` → `run()` → `teardown()`. On completion it prints accuracy,
mean score, Pass@N, Avg@N, and completed/total counts, exiting 1 when
`strict_report` flagged a failure.

`batch` runs each config with its own `Runner` lifecycle:
`BatchRunner._run_sequential` isolates per-config failures as `None`, while
`--parallel` uses a `ThreadPoolExecutor` sized to the number of configs.

## Result store and report

`ResultStore` (`alphadiana/analysis/io/result_store.py`) writes one JSON line
per `(task_id, sample_index)` to `results/<run_id>.jsonl` and mirrors each to
`tasks/<task_id>.json`. Each record embeds the run metadata plus the problem,
ground truth, `predicted` (the response answer), `correct`, `score`, rationale,
normalized trajectory, reasoning trajectory, raw output, request/response
payloads, token usage, logprob sidecars, sandbox metadata, finish reason, and a
computed `score_status`. `append_error` records `correct = None` / `score =
None` while preserving artifacts. `load` dedupes by `(task_id, sample_index)`
with last-write-wins and skips malformed lines. Sensitive keys are redacted
before persistence.

`ReportGenerator.generate` produces a `RunSummary` with accuracy
(`correct / scored`), accuracy over the expected sample count, mean score,
pass@k (fraction of unique tasks with at least one correct sample), avg@k, and
per-category variants. See the [Dashboard](./observability) page for the
live status file and report views.

## See also

- [Sandboxes & Isolation](./sandboxes) — isolation modes, pools, and predeploy.
- [DirectLLM](../harnesses/direct-llm), [OpenCode](../harnesses/opencode), [OpenClaw](../harnesses/openclaw), [ZeroClaw](../harnesses/zeroclaw) — the registered agents.
