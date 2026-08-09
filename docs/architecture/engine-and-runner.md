# Engine & Runner

`Runner` in `alphadiana/engine/runner.py` is the orchestration boundary. It owns component setup, work-item expansion, checkpoint filtering, dispatch, scoring, persistence, reporting, and teardown.

## Lifecycle

1. `ExperimentConfig.from_yaml()` loads YAML, expands environment references, applies `-o` overrides, and constructs typed component blocks.
2. `ConfigValidator.validate()` checks component compatibility and numeric constraints.
3. `Runner.setup()` imports built-in modules for registration side effects, resolves the configured components, calls their `setup()` methods, and creates the result store and report generator.
4. `Runner.run()` loads benchmark tasks, expands them by `num_samples`, removes checkpoint-complete samples, dispatches the remaining work, scores responses, and persists each outcome.
5. The report generator reads persisted records and the run manifest to produce `RunSummary` and Markdown output.
6. `Runner.teardown()` releases agent and sandbox resources.

Registration is explicit: adding a backend normally does not change dispatch logic, but it does require importing the module in `Runner.setup()` so its registration call executes. See [Registries](./registries.md).

## Core config

```yaml
run_id: example-run

agent:
  name: direct_llm
  config: {}

benchmark:
  name: aime
  config: {}

scorer:
  name: math_verify
  config: {}

sandbox: null
max_concurrent: 1
num_samples: 1
task_retries: 0
redo_all: false
output_dir: results
```

`num_samples` controls work-item expansion for every benchmark. It defaults to one; use a larger value only when the experiment explicitly requires multiple samples and Pass@k/Avg@k reporting.

## Work items and dispatcher

Each benchmark task becomes `num_samples` work items. Before execution the runner binds a unique `execution_id` and the `sample_index` into a task copy so concurrent attempts cannot share task-local state accidentally.

The normal dispatcher limits concurrency with `max_concurrent`. Sandbox acquisition depends on the backend:

- ordinary backends create or borrow a session without task binding;
- `swebench_container` receives the current `BenchmarkTask`;
- OpenClaw gateway predeployment uses its own pool and quarantines dead sessions rather than requeueing them.

Recoverable task failures may be retried on fresh infrastructure according to `task_retries` and `task_retry_on_recoverable_only`. This retry layer is distinct from checkpoint resume across separate CLI invocations.

## Checkpoint resume

The result JSONL is the checkpoint source. For the current scorer, `ResultStore.completed_sample_ids()` accepts only records that load as `valid_scored`. Changing the scorer therefore does not silently reuse old scores.

The completion split is:

| Outcome | Checkpoint complete? |
| --- | --- |
| Normally scored response | Yes |
| Supported timeout represented as scored zero | Yes |
| Legacy row with explicit timeout evidence, normalized on load | Yes |
| Provider or context-overflow error | No |
| Runtime/control-plane failure | No |
| Heartbeat or session-taint rejection | No |
| Missing/unobserved verifier reward | No |
| Record produced by another scorer | No |

`redo_all: true` or CLI `--redo-all` bypasses checkpoint skipping.

## Lifecycle events and live status

The runner emits bounded, redacted lifecycle events for task start, progress, retries, completion, and failure. The terminal renderer and `status/dashboard.txt` consume those events. This live status file is separate from the React Dashboard UI and from the final Markdown report.

## CLI entry points

```bash
python -m alphadiana.cli validate configs/examples/direct_llm_gpqa_diamond.yaml
python -m alphadiana.cli run configs/examples/direct_llm_gpqa_diamond.yaml \
  -o run_id=engine_gpqa_directllm_t1_seq \
  -o benchmark.config.max_tasks=1 -o num_samples=1
python -m alphadiana.cli run configs/examples/direct_llm_gpqa_diamond.yaml \
  -o run_id=engine_gpqa_directllm_t1_concurrent \
  -o benchmark.config.max_tasks=1 -o num_samples=1 -o max_concurrent=2
python -m alphadiana.cli report results
python -m alphadiana.cli list-benchmarks
```

Use the exact command syntax shown by `python -m alphadiana.cli --help` in the current checkout if a subcommand changes. Environment and runtime setup are covered by the Getting Started and benchmark runbooks.

## See also

- [Architecture Overview](./)
- [Scoring & Results](./scoring-and-results.md)
- [Observability & Proxies](./observability.md)
