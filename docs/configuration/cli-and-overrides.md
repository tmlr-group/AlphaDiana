---
sidebar_position: 3
---

# CLI & Overrides

The `alphadiana` command-line tool is a [Click](https://click.palletsclick.com/) group
(`alphadiana/cli.py`, the `main` group at `cli.py:49-52`). Every workflow, from a single
run to a batch sweep, goes through one of its subcommands. This page documents those
subcommands, the `-o` dotted-override syntax, and the `run_id` / `num_samples` conventions
that govern where results land and how many samples each task produces.

## Subcommands

| Command | Argument | Purpose |
|---------|----------|---------|
| `run` | `<config.yaml>` | Run one evaluation experiment end to end. |
| `validate` | `<config.yaml>` | Load and validate a config without running. |
| `report` | `<results_dir>` | Regenerate a `RunSummary` from an existing results directory. |
| `batch` | `<config1.yaml> <config2.yaml> ...` | Run several configs sequentially (or with `--parallel`). |
| `env` | — | Check ROCK service health and ownership; print start instructions. |
| `list-benchmarks` | — | List the benchmarks registered in the `BenchmarkRegistry`. |

All commands that take a config accept `-o`/`--override` (see below). The `run` and `batch`
commands additionally accept run-control flags.

### `run`

```bash
alphadiana run configs/test_openclaw_quick.yaml
```

`run` builds the override dict, loads the config with `ExperimentConfig.from_yaml`, runs the
`ConfigValidator` (hard errors abort with exit code 1), emits non-fatal warnings, does a
Terminal-Bench-2 preflight, warns on inherited proxy env vars, runs a ROCK service preflight
for ROCK-backed runs, then drives `Runner.setup() -> run() -> teardown()` in a
`try`/`finally`. On completion it prints accuracy, mean score, Pass@N, Avg@N, and
completed/total counts, exiting 1 when `strict_report` is set and the strict checks fail.

To ignore the checkpoint and re-run every task:

```bash
alphadiana run configs/test_openclaw_quick.yaml --redo-all
```

### `validate`

```bash
alphadiana validate configs/test_openclaw_quick.yaml
```

Loads the config and runs the same `ConfigValidator` as `run`, printing `Config is valid.`
when clean. Use this in CI or before launching a long sweep.

### `report`

```bash
alphadiana report results/my-run-id
```

Rebuilds the `RunSummary` (accuracy, mean score, Pass@N, Avg@N, per-category breakdowns)
directly from the JSONL result store, without re-running any tasks.

### `batch`

```bash
alphadiana batch configs/a.yaml configs/b.yaml configs/c.yaml
alphadiana batch configs/a.yaml configs/b.yaml --parallel
```

The `BatchRunner` (`alphadiana/engine/batch_runner.py`) runs each config with its own
`Runner` and `setup`/`run`/`teardown` lifecycle. Sequential mode isolates failures (a failed
config yields `None` and the others continue); `--parallel` uses a `ThreadPoolExecutor`.

### `env`

```bash
alphadiana env
```

Reports ROCK service status (admin, proxy, redis, docker) and ownership, and prints the
commands to start anything that is down. See [Quickstart Commands](../quickstart_commands)
for the full bring-up sequence.

### `list-benchmarks`

```bash
alphadiana list-benchmarks
```

Lists every benchmark string registered in the `BenchmarkRegistry`, which is also the set of
valid `benchmark.name` values for a config.

## The `-o` override syntax

Any config field can be overridden on the command line with `-o a.b.c=value`. The flag is
repeatable; each override is parsed into a nested dict by `parse_override` and merged with
`deep_merge` (both in `alphadiana/engine/config/experiment_config.py`). The merge happens
after the YAML is loaded, so overrides win.

```bash
alphadiana run configs/aime.yaml \
  -o agent.config.temperature=0.6 \
  -o max_concurrent=4 \
  -o num_samples=4
```

Values are **auto-cast** (`experiment_config.py:141-171`): `true`/`false` become booleans,
integer-looking strings become `int`, decimal strings become `float`, everything else stays
a string. To force a string, supply a non-numeric value.

The dotted path mirrors the YAML structure exactly:

| Override | Effect |
|----------|--------|
| `-o agent.config.temperature=0.6` | Sets `agent.config.temperature`. |
| `-o benchmark.config.subset=diamond` | Sets a benchmark config key. |
| `-o max_concurrent=4` | Top-level concurrency. |
| `-o run_id=my-experiment` | Names the run (see conventions below). |
| `-o sandbox.config.rock_image=...` | Sandbox config key. |

:::warning Do not override v2 contract params
Do not use `-o` to change reasoning controls or `max_tokens` on contract-bound v2 runs
(e.g. `agent.config.enable_thinking`, `agent.config.max_tokens`). Those are the experimental
variable, not plumbing; a downscaled run must use a distinct `run_id` with an explicit
suffix.
:::

### `--redo-all`

```bash
alphadiana run configs/aime.yaml --redo-all
```

By default `run` resumes from the checkpoint: any task with an existing valid-scored record
is skipped. `--redo-all` sets `overrides['redo_all'] = True`, which bypasses the checkpoint
and re-runs every `(task, sample_index)` work item. Checkpoint state lives in the result
JSONL itself, not a separate file: completion means a record whose
`infer_score_status` is `valid_scored`. Error, timeout, and no-answer records are
intentionally **not** complete, so a plain re-run retries exactly those.

## `run_id` conventions

`run_id` namespaces every output of a run. If left blank it defaults to `uuid4().hex[:12]`
(`ExperimentConfig.__post_init__`), and any `/` in the value is replaced with `_` so the id
is always a safe path segment.

Given `run_id: my-run`, the result store (`alphadiana/analysis/io/result_store.py`) writes:

```
results/
  my-run.jsonl                     # one JSON line per (task_id, sample_index)
  my-run/
    run_manifest.json              # expected counts, task ids, config metadata
    artifacts/<task_id>/           # per-task artifacts
    tasks/<task_id>.json           # mirrored per-task record
    lifecycle/                     # per-item lifecycle event logs
    status/                        # dashboard.txt and status files
```

Each JSONL record embeds the problem, `ground_truth`, `predicted` (the response answer),
`correct`, `score`, the (normalized) trajectory, token usage, and a `score_status`. See the
[Result Store](./result-store) page for the full record shape, and [Dashboard](../dashboard)
for `status/dashboard.txt`.

## `num_samples` conventions

`num_samples` (default `1`) controls how many samples are drawn per task. The work list is
the cartesian expansion `[(task, si) for task in tasks for si in range(num_samples)]`, so a
30-task benchmark at `num_samples=4` produces 120 work items. The choice also changes how
metrics are computed: with `num_samples > 1` the report exposes Pass@N (fraction of tasks
with at least one correct sample) and Avg@N (mean per-task correct fraction).

Standing conventions for the headline benchmarks:

| Benchmark | `num_samples` | Metric |
|-----------|---------------|--------|
| AIME | `4` | pass@4 / avg@4 |
| GPQA | `1` | pass@1 |

GPQA is **always** pass@1 (`num_samples: 1`); never run it at pass@4. AIME uses
`num_samples: 4`. Checkpoint resume keys off `completed_sample_ids` when `num_samples > 1`
and `completed_task_ids` when `num_samples == 1`.

## Minimal config shape

A config is YAML with `agent`, `benchmark`, optional `sandbox`, and `scorer` blocks plus
top-level run controls. `from_yaml` expands `$VAR`/`${VAR}` from the environment, applies
overrides, clears unresolved placeholders, and fills `api_base`/`api_key`/`model` from
`OPENAI_BASE_URL`/`OPENAI_API_KEY`/`OPENAI_MODEL_NAME` when blank.

```yaml
run_id: my-aime-run

agent:
  name: direct_llm
  version: v1
  config:
    api_base: http://127.0.0.1:8011/v1
    api_key: sk-EMPTY        # any non-"EMPTY" string for local vLLM
    model: gemma-4-31b-it
    temperature: 0.6

benchmark:
  name: aime2024
  config: {}

sandbox: null                # or {name: rock, config: {...}}

scorer:
  name: numeric
  config: {}

max_concurrent: 4
num_samples: 4
output_dir: ./results
```

Common top-level keys: `run_id`, `max_concurrent`, `num_samples`, `output_dir`,
`redo_all`, `sandbox_retries`, `task_retries`, `task_retry_on_recoverable_only`,
`strict_report`, `strict_isolation`, `metadata`.

:::note Local vLLM API key
The validator treats the literal string `EMPTY` (and unresolved `$VAR`) as blank. For local
vLLM use `sk-EMPTY` or any other non-`EMPTY` string.
:::

For agent-specific config blocks see the harness pages
([direct_llm](../harnesses/direct-llm), [zeroclaw](../harnesses/zeroclaw),
[opencode](../harnesses/opencode), [openclaw](../harnesses/openclaw)). For sandbox and
isolation modes see [Benchmark Isolation](../benchmark-isolation).
