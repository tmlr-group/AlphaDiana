# Quick Start

This page runs your first benchmark end to end with the `direct_llm` baseline:
write (or point to) a config, validate it, run it, and read the report. The
`direct_llm` agent is the simplest path because it needs no sandbox, no gateway,
and no ROCK services. It is a single-turn system+user chat to an
OpenAI-compatible endpoint with no tools and no multi-turn loop, which makes it
the reference baseline against the [harnesses](../harnesses/).

If you have not installed AlphaDiana yet, start with
[Installation](./installation.md) first.

## The flow at a glance

Every run follows the same pipeline. A YAML `ExperimentConfig` is loaded,
benchmark / agent / scorer are resolved from string-keyed registries, tasks are
expanded into `(task, sample_index)` work items, and each item runs
`agent.solve -> scorer.score -> ResultStore.append`:

```
config.yaml
  → ExperimentConfig.from_yaml      (alphadiana/engine/config/experiment_config.py)
  → ConfigValidator                 (alphadiana/engine/config/validator.py)
  → Runner.setup()  resolves benchmark / agent / scorer from registries
  → Runner.run()    load_tasks → per (task, sample): agent.solve → scorer.score → ResultStore.append
  → ReportGenerator.generate → RunSummary
  → Runner.teardown()
  → results/<run_id>.jsonl  (+ results/<run_id>/...)
```

The CLI entry point is the Click group `main` in `alphadiana/cli.py`; the
orchestrator is `Runner` in `alphadiana/engine/runner.py`; results are written by
`ResultStore` in `alphadiana/analysis/io/result_store.py`.

## 1. Point the agent at a model

`direct_llm` builds its own OpenAI client and resolves the model, endpoint, and
key from config first, then from environment variables. For a local vLLM server
set:

```bash
# These names match configs/macro_runs/aime2026_directllm_qwen35_27b.yaml and the loader fallbacks.
export OPENAI_MODEL_NAME="Qwen/Qwen3.5-27B"
export OPENAI_BASE_URL="http://127.0.0.1:8011/v1"
export OPENAI_API_KEY="sk-EMPTY"
```

Use `sk-EMPTY` (any non-`EMPTY` string) for a keyless local server. The literal
string `EMPTY` is treated as blank by both the validator and the agent. For a
hosted provider, set `OPENAI_BASE_URL=https://openrouter.ai/api/v1` and a real
key.

## 2. Write (or point to) a config

A ready-made example ships at
`configs/macro_runs/aime2026_directllm_qwen35_27b.yaml`. Its essential fields
are shown below; use the checked-in file as the source of truth:

```yaml
run_id: macro_aime2026_directllm_qwen35_27b

agent:
  name: direct_llm
  version: "1.0"
  config:
    model: "${OPENAI_MODEL_NAME}"
    api_base: "${OPENAI_BASE_URL}"
    api_key: "${OPENAI_API_KEY}"
    temperature: 0.0
    top_p: 0.95
    max_tokens: 32768
    stream: true

benchmark:
  name: aime
  config:
    dataset: MathArena/aime_2026
    split: "train"

scorer:
  name: numeric
  config:
    tolerance: 1e-6

max_concurrent: 1
num_samples: 1
output_dir: ./results
strict_report: true
strict_isolation: true
```

`$VAR` / `${VAR}` placeholders are expanded from the environment at load time.
An unresolved placeholder becomes a blank string, and for `direct_llm` a blank
`model` / `api_base` / `api_key` then falls back to `OPENAI_MODEL_NAME` /
`OPENAI_BASE_URL` / `OPENAI_API_KEY`.

### Top-level keys

| Key | Meaning | Default |
|---|---|---|
| `run_id` | Namespaces all output; blank → `uuid4().hex[:12]`; `/` is replaced with `_` | `""` |
| `agent` | `{name, version, config}`; `name` selects the registered agent | required |
| `benchmark` | `{name, config}`; `name` selects the dataset loader | required |
| `scorer` | `{name, config}`; e.g. `exact_match`, `numeric` | required |
| `sandbox` | `null` (no sandbox) or `{name, config}`; not needed for `direct_llm` | `null` |
| `max_concurrent` | Worker count; `1` is sequential, otherwise a `ThreadPoolExecutor`; integer in `[1, 64]` | `1` |
| `num_samples` | Samples per task; drives Pass@k / Avg@k | `1` |
| `output_dir` | Where `<run_id>.jsonl` and `<run_id>/` are written | `./results` |
| `metadata` | Free-form notes embedded into each record | `{}` |

### Useful `direct_llm` agent.config keys

| Key | Meaning | Default |
|---|---|---|
| `model` / `api_base` / `api_key` | OpenAI-compatible target; blank → env fallback | env |
| `temperature` | Sampling temperature | `0.7` |
| `top_p` | Nucleus sampling; only sent when explicitly set | unset (`None`) |
| `max_tokens` | Output cap; if unset, auto-resolved from the server's `max_model_len - 8192` | auto |
| `stream` | Stream the completion | `true` |
| `max_retries` | Retries on rate-limit / timeout / 5xx with exponential backoff | `3` |
| `system_prompt` | System message; default asks for a `\boxed{}` answer | built-in |
| `capture_logprobs` / `top_logprobs` | Capture token logprobs; `direct_llm` captures by default | `true` / `20` |

For the full schema and the dotted `-o` override syntax, see
[Configuration](../configuration/). For agents other than the baseline, see
[Harnesses](../harnesses/).

## 3. Validate

`validate` runs the same `ConfigValidator` as `run`, but without executing
anything. It blocks on hard errors (missing `agent.name`, an `agent.version`
with no digit, `max_concurrent` out of range, a scorer mismatch, and so on):

```bash
alphadiana validate configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o benchmark.config.max_tasks=1
```

Expected output:

```
Config is valid.
```

You can layer overrides on either `validate` or `run`. Each `-o a.b.c=value` is
parsed into a nested dict and auto-cast to bool/int/float:

```bash
alphadiana validate configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o benchmark.config.max_tasks=20
```

## 4. Run

```bash
alphadiana run configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o run_id=quickstart_aime_directllm_t1_k1 \
  -o benchmark.config.max_tasks=1 \
  -o num_samples=1
```

The runner loads the tasks, expands them into `(task, sample_index)` work items,
and for each one calls `agent.solve` then `scorer.score` then appends a record.
Runs are checkpoint-resumable: rerunning this command with the same explicit
`run_id` skips any task that already has a scorer-matching `valid_scored`
record. Provider/runtime errors
and no-answer records remain retryable. Timeout-classified outcomes from the
current harnesses are scored zero with `finish_reason: timeout`, so they are
checkpoint-complete rather than retried. To ignore the checkpoint and
recompute everything:

```bash
alphadiana run configs/macro_runs/aime2026_directllm_qwen35_27b.yaml --redo-all \
  -o run_id=quickstart_aime_directllm_t1_k1 \
  -o benchmark.config.max_tasks=1 \
  -o num_samples=1
```

When the run finishes, the CLI prints the headline metrics:

```
Run completed: quickstart_aime_directllm_t1_k1
  Accuracy:   <model-dependent value>
  Mean Score: <model-dependent value>
  Pass@1:     <model-dependent value>
  Avg@1:      <model-dependent value>
  Tasks:      1/1 completed
```

## 5. Read the report

`run` prints a summary automatically. To regenerate the Markdown report for a
finished run later, point `report` at the directory containing the `.jsonl`:

```bash
alphadiana report ./results
```

It loads `<run_id>.jsonl`, deduplicates by `(task_id, sample_index)`, and prints
the same `RunSummary` metrics broken out per category.

## Results layout

`run_id` namespaces everything under `output_dir`. The flat `.jsonl` is the
source of truth for metrics and checkpointing; the `<run_id>/` directory holds
the manifest and per-task artifacts:

```
results/
  <run_id>.jsonl                      # one JSON record per (task_id, sample_index)
  <run_id>/
    run_manifest.json                 # expected task / sample counts, config metadata
    artifacts/<task_id>/...           # raw runtime artifacts, logprob sidecars
    tasks/<task_id>.json              # JSON list of samples (even when n=1)
    lifecycle/<task_id>.jsonl         # per-item lifecycle events
    status/                           # status / dashboard files
```

Each line in `<run_id>.jsonl` carries the problem, the model's answer, the
score, and observability fields:

```json
{
  "task_id": "aime_0",
  "problem": "Competition math problem...",
  "ground_truth": "42",
  "predicted": "42",
  "correct": true,
  "score": 1.0,
  "score_status": "valid_scored",
  "rationale": "Numeric match within tolerance",
  "trajectory": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "thinking": "..."}
  ],
  "token_usage": {"prompt_tokens": 150, "completion_tokens": 1200},
  "finish_reason": "stop",
  "wall_time_sec": 47.3,
  "timestamp": "2026-06-24T..."
}
```

`predicted` is the agent's extracted answer, `correct` is the scorer's verdict,
and `score_status` is what checkpointing keys off: only a scorer-matching
`valid_scored` record counts as complete. Errors are recorded too, with
`correct: null` and `score: null`, so nothing is silently dropped. Current
timeout-classified harness outcomes are the exception to the intuitive
"no-answer means retry" rule: they are normalized to `score: 0`,
`correct: false`, and `score_status: valid_scored`.

## Accuracy, Pass@k, and Avg@k

The report metrics come from `ReportGenerator.generate`
(`alphadiana/analysis/report.py`):

| Metric | Definition |
|---|---|
| **Accuracy** | `correct / scored` — fraction correct among records that were actually scored |
| **Accuracy (total)** | `correct / expected_sample_count` — denominator is the planned count, so unfinished or errored samples count against it |
| **Mean Score** | Mean of the `score` field over scored records (equals accuracy for binary scorers) |
| **Pass@k** | Fraction of unique tasks with at least one correct sample (`k = num_samples`) |
| **Avg@k** | Per task, the correct-sample rate `n_correct / num_samples`, then averaged across tasks |

With `num_samples: 1` (the default), Pass@1 and Avg@1 both equal Accuracy
because there is exactly one sample per task. They diverge only with
multi-sample runs. With `num_samples: 4`, for example, Pass@4 rewards getting
the answer at least once across four draws, while Avg@4 measures average
per-draw reliability. Choose the sample count required by the study protocol;
the validator only requires a positive integer.

## Next steps

- Try a different benchmark: [GPQA-Diamond](../benchmarks/gpqa-diamond.md) or
  [AIME](../benchmarks/aime.md). Set `num_samples` above 1 only when you want a
  multi-sample Pass@k / Avg@k protocol.
- Swap the baseline for a real agent scaffold: see the
  [harnesses](../harnesses/) (`opencode`, `openclaw`, `zeroclaw`), which add
  tools, sandboxes, and memory.
- Tune the run YAML, overrides, and run-id conventions in
  [Configuration](../configuration/).
