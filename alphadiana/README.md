# Package ownership and import migration

The package root exposes the Python composition API. Implementations live with execution, harness protocols, scoring or saved results.

```text
alphadiana/
  __init__.py              # supported ad.* exports
  api.py                   # typed component recipes and public composition
  cli/
    __init__.py            # supported alphadiana.cli:main console group
    __main__.py            # python -m alphadiana.cli
    commands.py            # run, batch, validate, report, env, list-benchmarks
    single_rollout.py      # one-task controller command and JSON result
    campaign.py            # rollout matrix inspection and materialization
  engine/
    components.py          # independent registries and deferred built-in loading
    observations.py        # optional per-task observations after scoring
    runner.py              # task execution and runtime lifecycle
    rollout_campaign.py    # campaign expansion, preflight and command planning
  harness/
    attachments.py         # convert task attachments into agent input or files
    ...
  scorer/
    answers.py             # extract and normalize answer text for scoring
    ...
  results/
    artifacts.py           # reopen saved evidence and append linked rescoring
    logprobs.py             # raw/compact conversion and probability primitives
    store.py               # persist run records and sidecars
    reader.py              # load saved records for reports and Dashboard
    trace.py               # normalize saved trajectory records
    status.py              # classify scoring status
    statistics.py          # select samples and calculate run statistics
  analysis/
  benchmarks/
  dashboard/
```

`api.py` creates typed recipes and connects them to execution and analysis. `engine/components.py` owns registry state and explicit built-in registration; importing it does not load native runtimes. `results/logprobs.py` contains pure probability and encoding functions shared by capture, persistence and measurement; it does not import the analysis layer.

The CLI package preserves `alphadiana`, `python -m alphadiana.cli`, and `from alphadiana.cli import main`. The package initializer exports that existing console contract; the implementations have one owner in `commands.py`.

## Path migration

The following old paths are removed. Update imports and controller launchers before using this revision. No forwarding files are installed for these paths.

| Removed path | New owner / command |
| --- | --- |
| `alphadiana.artifacts.Artifacts` | `alphadiana.results.artifacts.Artifacts` |
| `alphadiana.answers` | `alphadiana.scorer.answers` |
| `alphadiana.attachments` | `alphadiana.harness.attachments` |
| `alphadiana.logprobs` | `alphadiana.results.logprobs` |
| `alphadiana.registry` | `alphadiana.engine.components` |
| `alphadiana.registry_loader.load_builtin_components` | `alphadiana.engine.components.register_builtin_components` |
| `alphadiana.registry_loader.load_builtin_*` family functions | same function names in `alphadiana.engine.components` |
| `alphadiana.metrics.base` / `answer_length` | `alphadiana.engine.observations` |
| `alphadiana.metrics.registry` | `alphadiana.engine.components` |
| `python -m alphadiana.single_rollout` | `python -m alphadiana.cli.single_rollout` |
| `python -m alphadiana.benchmark_rollout_cli` | `python -m alphadiana.cli.campaign` |

For example:

```python
from alphadiana.results.artifacts import Artifacts
from alphadiana.engine.components import register_builtin_components
```

```bash
python -m alphadiana.cli.single_rollout config.yaml --task-id TASK_ID
python -m alphadiana.cli.campaign summary --manifest PATH_TO_MANIFEST.yaml
```

An AlphaHebe launcher using the old single-rollout module must change its module argument to `alphadiana.cli.single_rollout`. Its configuration, options and JSON result fields are unchanged. External callers are not inventoried by repository search; this checkout updates its own callers, tests and documentation.

Supported domain registry imports (`harness.registry`, `benchmarks.registry`, `scorer.registry`, `engine.sandbox.registry`) continue to expose identical registry classes and decorators from `engine.components`. These existing extension imports retain their published contract and contain no separate state.

The public `ad.*` factories and recipes, `scorer.create`, result JSONL schemas, artifact references and scoring policies remain unchanged. The relocated `Artifacts` and registry classes have new Python `__module__` paths; no in-repository pickle or reflection consumer was found. Saved AlphaDiana evidence is read through its existing JSON/JSONL contracts.
