---
sidebar_position: 1
---

# Getting Started

This section gets you from a fresh checkout to a scored evaluation run: how to install AlphaDiana, run your first experiment, and recover when something goes wrong.

- [Installation](./installation) — set up the environment, install the package, and bring up the optional ROCK sandbox.
- [Quick Start](./quick-start) — validate a config and run your first Direct-LLM baseline end to end.
- [Troubleshooting](./troubleshooting) — fixes for config-validation, sandbox, and result-validity issues.

## What AlphaDiana Is

AlphaDiana is an evaluation framework for foundation models and agent systems. A single YAML file fully defines a run: which agent (harness), benchmark, sandbox, and scorer to use, plus top-level controls like `run_id`, `max_concurrent`, and `num_samples`. The CLI parses that YAML into an `ExperimentConfig` dataclass and the `Runner` orchestrates loading, solving, scoring, and persistence.

The harnesses live under `alphadiana/harness/` — `direct_llm`, [openclaw](../harnesses/openclaw), [opencode](../harnesses/opencode), and [zeroclaw](../harnesses/zeroclaw). The engine (runner, dispatcher, sandboxes, config) lives under `alphadiana/engine/`, and scored records are written by the result store at `alphadiana/analysis/io/result_store.py`.

### Evaluation flow

```
YAML Config
   │  ExperimentConfig.from_yaml()
   ▼
Runner.setup()      # resolve agent / benchmark / sandbox / scorer from registries
   │
   ▼
Runner.run()
   │  benchmark.load_tasks()
   │  for each (task, sample_index):
   │      agent.solve(task, session)  ->  AgentResponse
   │      scorer.score(task, response) ->  ScoreResult
   │      result_store.append(...)
   ▼
results/<run_id>.jsonl  +  results/<run_id>/{tasks,artifacts,lifecycle}/
   │
   ├── alphadiana report   (markdown summary, Pass@k / Avg@k)
   └── dashboard           (browse runs and trajectories)
```

## How to Run

A run is launched with the `alphadiana` CLI, a Click group defined in `alphadiana/cli.py` (the console entry point maps to `alphadiana.cli:main`). From a local checkout the equivalent module form is `python -m alphadiana.cli`.

```bash
# Validate first; prints "Config is valid." or lists "  - <error>" lines and exits 1.
alphadiana validate configs/examples/direct_llm.yaml

# Run an experiment.
alphadiana run configs/examples/direct_llm.yaml

# Re-running the same config resumes from the checkpoint (skips already-scored
# samples). Use --redo-all to ignore the checkpoint and recompute everything.
alphadiana run configs/examples/direct_llm.yaml --redo-all
```

### CLI subcommands

| Command | Purpose |
| --- | --- |
| `alphadiana run <config.yaml>` | Run a single experiment; `--redo-all` is sugar for `-o redo_all=true`. |
| `alphadiana validate <config.yaml>` | Check the config against `ConfigValidator`; non-zero exit on error. |
| `alphadiana report <results_dir>` | Regenerate the markdown report from existing `<run_id>.jsonl`. |
| `alphadiana batch <c1> <c2> ...` | Run multiple configs; `--parallel` runs them concurrently. |
| `alphadiana env` | ROCK service and port-ownership health check. |
| `alphadiana list-benchmarks` | List the diagnostic command's imported benchmark subset. |

### Overrides

Use `-o` (long form `--override`) with a dotted key path to override any config field without editing the YAML. It is repeatable and merges deeply. Values are auto-coerced in order: `true`/`false` to bool, then int, then float, else string.

```bash
python -m alphadiana.cli run configs/examples/direct_llm.yaml \
  -o run_id=my_test \
  -o output_dir=/tmp/runs/my_test \
  -o agent.config.temperature=0.5 \
  -o max_concurrent=4
```

A blank `run_id` is auto-filled with `uuid.uuid4().hex[:12]`, and any `/` in a `run_id` is replaced with `_`. Variant or downscaled runs should use a distinct `run_id` suffix rather than CLI-overriding contract parameters.

## Config Shape

A config is parsed into `ExperimentConfig` (`alphadiana/engine/config/experiment_config.py`). The standard YAML shape is:

```yaml
run_id: 20260423-gpqa_diamond-directllm-qwen35_27b-v01
agent:
  name: direct_llm        # direct_llm | openclaw | opencode | zeroclaw | swebench_docker | ...
  version: v1
  config:
    model: Qwen/Qwen3.5-27B
    api_base: http://127.0.0.1:8000/v1
    api_key: sk-EMPTY
    temperature: 0.7
benchmark:
  name: gpqa_diamond
  config: {}
sandbox: null             # null for direct_llm / self-managed OpenCode paths; else {name, config}
scorer:
  name: exact_match       # GPQA is multiple-choice; other benchmarks require their matching scorer
  config: {}
max_concurrent: 1
num_samples: 1
output_dir: ./results
```

`configs/schema.yaml` is the authoritative, fully-commented field reference. Top-level keys and their defaults:

| Key | Default | Notes |
| --- | --- | --- |
| `run_id` | auto (`uuid4().hex[:12]`) | `/` is replaced with `_`. |
| `agent.{name,version,config}` | required | `config` is an open pass-through dict to the harness. |
| `benchmark.{name,config}` | required | |
| `sandbox` | `null` | `null`, or `{name, config}` with name in `local`, `rock`, `podman`, `swebench_container`. |
| `scorer.{name,config}` | required | |
| `max_concurrent` | `1` | Integer in `[1, 64]`. |
| `num_samples` | `1` | Samples per task; increase it when the evaluation protocol calls for Pass@k / Avg@k. |
| `output_dir` | `./results` | |
| `task_retries` | `0` (from YAML) | Integer `>= 0`. |
| `strict_report`, `strict_isolation` | `false` | Fail-closed report and ROCK setup switches; see Configuration and Isolation for their distinct scopes. |

### Provider env and the EMPTY sentinel

For env-default provider paths (`direct_llm`, `zeroclaw`, `opencode`,
`terminal_bench2_*`, and `swebench_docker`), blank `agent.config` fields are
filled from the environment when left empty in the YAML:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
```

The validator treats `None`, `""`, the literal string `EMPTY` (case-insensitive), and an unresolved `${VAR}` placeholder as not populated. For local vLLM use `sk-EMPTY` (any non-literal-`EMPTY` string passes); the literal `EMPTY` fails validation.

OpenClaw has a separate gateway/provider distinction: its lowercase
`agent.config.api_base` is an already-running OpenClaw gateway, whereas the
upstream model endpoint for runtime startup belongs in
`agent.config.OPENAI_BASE_URL` (or `openai_base_url`). See
[Installation](./installation#provider-endpoints-and-agent-gateways) before an
OpenClaw auto-deploy run.

## Where Results Go

`run_id` namespaces all output. Per run you get `results/<run_id>.jsonl` (one
JSON line per `(task_id, sample_index)`) plus a `results/<run_id>/` directory
with `run_manifest.json`, `tasks/<task_id>.json`, `artifacts/<task_id>/`, and
`lifecycle/`. Each task JSON is a sample list even when `num_samples: 1`.
Checkpoint-resume is built on the JSONL: only scorer-matching `valid_scored`
records count as complete. Provider/runtime failures remain retryable, while
timeout outcomes normalized to scored zero are complete. Use `--redo-all` to
recompute completed samples too.

See [Quick Start](./quick-start) to walk through a first run, and [Troubleshooting](./troubleshooting) when a run does not produce valid scored records.
