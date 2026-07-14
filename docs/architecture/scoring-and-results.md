---
sidebar_position: 5
---

# Scoring & Results

After a harness produces an `AgentResponse`, AlphaDiana turns each `(task, response)` pair
into a `ScoreResult` via a pluggable scorer, persists one redacted JSON record per
`(task_id, sample_index)` to the result store, and later derives accuracy, Pass@k, Avg@k,
and behavioral metrics over those records, gated by an inferred `score_status`.

```
agent.solve(task) -> AgentResponse
  -> scorer.score(task, response) -> ScoreResult
    -> result_store.append(...) -> {run_id}.jsonl  (+ per-task JSON, artifacts, logprobs)
      -> ReportGenerator.generate(...) -> RunSummary -> markdown / dashboard
```

## Scorers

Scorers live under `alphadiana/scorer/`. The base contract is in
`alphadiana/scorer/base.py` and the registry in `alphadiana/scorer/registry.py`.

`Scorer` is an `ABC` with a `name` property, an optional `setup(config)`, and the abstract
`score(task, response) -> ScoreResult`. The returned `ScoreResult` is a dataclass:

```python
@dataclass
class ScoreResult:
    correct: bool
    score: float
    expected: Any
    predicted: Any
    rationale: str = ""
    metadata: dict = field(default_factory=dict)
```

Scorers self-register with the `@register_scorer(name)` decorator into the class-level
`ScorerRegistry`. The runner imports every scorer module at `setup()` to trigger
registration, then resolves `ScorerRegistry.get(config.scorer_name)` and calls
`scorer.setup(config.scorer_config)`. Every built-in scorer short-circuits to
`score=0` with rationale `"No answer produced (answer is None)."` when `response.answer`
is `None`.

### Built-in generic scorers

| Scorer | When to use | Notes |
|--------|-------------|-------|
| **`math_verify`** | Math competition problems (AIME, HMMT, etc.) — **recommended default** | Symbolic equivalence via math-verify/SymPy (wraps answers in `\boxed{}`). Handles LaTeX, fractions, equivalent expressions. Falls back to normalized-string then numeric compare; `metadata.method` is one of `math_verify` / `normalized_string` / `numeric`. Requires `pip install math-verify`. |
| **`numeric`** | Integer or decimal answers only | Compares numeric values with configurable absolute + relative `tolerance` (default `1e-6`) via `parse_numeric_answer`. Fails if the prediction cannot be parsed as a number. |
| **`exact_match`** | String answers where reformulation should not count | Math-aware normalization (`utils.math_answer.normalize_math_text`) plus single-choice label coercion (`(A)` -> `A`), then strict equality. Does **not** equate `1/2` and `0.5`. |
| **`llm_judge`** | Open-ended or descriptive answers | Calls an OpenAI-compatible chat endpoint and parses JSON `{correct, rationale}` at `temperature 0.0`, retrying on 429/5xx. Reads `JUDGE_MODEL` / `JUDGE_API_BASE` env or `scorer_config` keys `api_base` / `api_key` / `judge_model` / `timeout`. |

The `scorer_name` config field selects the scorer; `scorer_config` is passed verbatim to
`scorer.setup()`. See the [config schema](../configuration/config-schema) for where these
keys sit in a run YAML.

### Benchmark-specific scorers

Benchmark scorers register the same way and live next to their benchmark code:

| Scorer | Location | Notes |
|--------|----------|-------|
| `swe_bench` | `alphadiana/benchmarks/swe_bench/scorer.py:98` | Wraps the official SWE-bench harness. |
| `swebench_pro` | `alphadiana/benchmarks/swebench_pro/scorer.py:53` | Wraps the official evaluator; empty patch maps to `predicted='unresolved'`; sets `metadata.resolved`. Reports an extra **Resolve Rate** row. |
| `terminal_bench2` | `alphadiana/benchmarks/terminal_bench2/scorer.py:8` | Reward only counts when the verifier observed it (see validity model below). |
| `external_benchmark` | `alphadiana/benchmarks/external_benchmark/scorer.py:24` | Molecule benchmark verifier. |

For SWE benchmarks, `direct_llm` is intentionally not a Diana path; see
[../benchmarks/swebench-pro](../benchmarks/swebench-pro). For the agent harnesses, see
[../harnesses/zeroclaw](../harnesses/zeroclaw) and siblings.

## Result store

The writer is `ResultStore(output_dir, run_id, *, run_metadata=None)` in
`alphadiana/analysis/io/result_store.py`. (The `alphadiana/results/` package is empty and
unused.) The runner calls `result_store.append(task, response, score, sample_index=...)`
per work item, or `result_store.append_error(task, error=..., response=..., sample_index=...)`
on failure. Writes are thread-safe: a global lock guards the JSONL append, with per-task and
per-artifact-key locks elsewhere.

### Directory layout

```
{output_dir}/
  {run_id}.jsonl                       # one record per (task_id, sample_index)
  {run_id}/
    run_manifest.json                  # run metadata
    tasks/{task_id}.json               # LIST of sample records (one per sample_index)
    artifacts/{task_id}/
      agent/                           # gateway.log, response.json, request_messages.json,
                                       #   system_prompt.txt, normalized_trace.json
      sandbox/                         # sandbox_meta.json
      workspace/                       # workspace files
                                       # (sample>0 nests all of the above under sample_<N>/)
    logprobs/{task_id}.jsonl           # sample 0 (flat); sample>0 nests as {task_id}/sample_<N>.jsonl
    logprobs_int16/...                 # compact Int16 form
```

`ALPHADIANA_RESULTS_DIR` (default `./results`) sets the directory consumed by the dashboard
and by `alphadiana report`. Per-task JSON files store a *list* of sample records;
`_save_per_task_json` replaces a matching `sample_index` in place or appends, migrating legacy
single-dict files to a list.

### Per-task JSONL record schema

`ResultStore.append` (`result_store.py:168-202`) writes one line per `(task_id, sample_index)`
with the following fields (plus everything in `run_metadata`):

| Group | Fields |
|-------|--------|
| Identity / run metadata | `task_id`, `sample_index`, `run_id`, `agent_name`, `agent_version`, `benchmark_name`, `scorer_name`, `num_samples`, `strict_isolation`, `isolation_mode` |
| Problem & scoring | `problem`, `ground_truth`, `task_metadata`, `predicted` (=`response.answer`), `correct`, `score`, `rationale`, `score_metadata`, `score_status` |
| Trajectory & output | `trajectory` (normalized), `reasoning_trajectory`, `raw_output`, `request_messages`, `response_json`, `system_prompt`, `finish_reason` |
| Tokens & logprobs | `token_usage`, `token_entropy_stats`, `logprobs_path`, `logprobs_int16_path`, `top_logprobs` (20 or 0), `int16_probability_scale` |
| Execution & artifacts | `wall_time_sec`, `sandbox_id`, `gateway_url`, `artifact_manifest`, `gateway_log_excerpt`, `workspace_snapshot_paths`, `sandbox_metadata`, `metadata`, `timestamp` (UTC ISO) |

`append_error` adds an `error: {}` object and sets `correct=None`, `score=None`.

The persisted `trajectory` is a **normalized** summary with stable step types
(`system` / `message` / `tool_use` / `tool_result` / `reasoning`), not a raw harness dump.

### Logprob sidecars

`ResultStore.write_logprob_sidecars` (`alphadiana/analysis/io/result_store.py`) pops
`logprob_records` / `logprob_int16_records` from `response.metadata` and writes a raw float
JSONL plus a compact Int16 form, using the constants and helpers in
`alphadiana/analysis/io/logprob_artifacts.py` (`INT16_PROB_SCALE=32767`,
`DEFAULT_TOP_LOGPROBS=20`, softmax via log-sum-exp, per-token `entropy_nats`,
`raw_record_to_int16_record`). Sample 0 uses the flat path `logprobs/{task_id}.jsonl`;
samples `>0` nest under `logprobs/{task_id}/sample_<N>.jsonl`.

### Redaction

Every record passes through `_redact_for_persistence` before write. Keys containing
`api_key` / `token` / `secret` / `password` / `authorization` are replaced with `<redacted>`,
and string values are scrubbed of `ENV=secret` assignments and `Authorization: Bearer`
headers. The same redaction is applied to written artifact files (`gateway.log`,
`response.json`, `request_messages.json`, `system_prompt.txt`, workspace files), so result
bundles are safe to share or upload.

## Validity model: `score_status`

`alphadiana/analysis/io/status.py` is the central validity gate. There is exactly one valid
status, `VALID_SCORE_STATUS = "valid_scored"`; everything else is an error or unscored bucket:

```python
VALID_SCORE_STATUS = "valid_scored"

INVALID_SCORE_STATUSES = {
    "unscored", "preserved_failure", "no_answer", "agent_empty_output",
    "agent_error", "provider_error", "runtime_error",
    "verifier_error", "scorer_error",
}
```

`infer_score_status()` classifies a record from its `error_type`, `finish_reason`,
`metadata.failure_reason`, verifier status, and (for ZeroClaw) classification fields.
`terminal_bench2` has bespoke logic: a record is `valid_scored` only when the verifier
actually observed a reward.

Two helpers run on every read, so historical JSONL re-scores consistently without rewriting
files: `normalize_legacy_timeout_zero_record()` rewrites old timeout errors to
`score 0.0` / `correct False` / `valid_scored`, and `infer_score_status()` is recomputed.

`ResultStore.load()` dedupes by `(task_id, sample_index)` keeping the last line and skips
malformed JSON. `completed_task_ids()` / `completed_sample_ids(scorer_name)` drive resume:
only `valid_scored` records count as done, and the `scorer_name` filter means changing the
scorer forces re-evaluation.

## Reports & metrics

`ReportGenerator.generate(result_store, config)` in `alphadiana/analysis/report.py` returns a
`RunSummary` and `to_markdown()` renders the summary table. Metrics are computed **only over
valid records** (plus a narrow metric-zero exception for `agent_empty_output` / `no_answer`
records flagged `metadata.metric_contribution == 0`), so infrastructure failures never count
as wrong answers.

| Metric | Definition |
|--------|------------|
| `accuracy` | `correct / len(metric_results)`, where `metric_results` = valid_scored records plus metric-zero records |
| `accuracy_total` | `correct / expected_sample_count` — denominator is what the run *should* have produced, so incomplete runs cannot silently inflate accuracy |
| **Pass@k** | fraction of tasks with at least one correct valid sample |
| **Avg@k** | mean over tasks of `(correct valid samples / num_samples)` |
| per-category | accuracy grouped by `task_metadata.category` (or the manifest's `task_metadata_by_id`) |

`num_samples > 1` enables Pass@k / Avg@k. By hard convention, **GPQA always uses
`num_samples=1` (pass@1)** and **AIME uses `num_samples=4`**. `to_markdown()` adds a
**Resolve Rate** row for `swebench_pro_os`. With `strict_report` enabled, missing samples,
invalid-scored records, and error records raise `strict_report_issues`.

`compute_reliability_summary` (`alphadiana/analysis/reliability.py`) adds
`observed_valid_accuracy`, `expected_sample_accuracy`, coverage (written / expected),
`missing_samples`, `error_records`, `pass_at_k`, `avg_at_k`, and `pass_power_k` (`pass^k`,
only when `num_samples > 1` with full coverage). Its `ERROR_STATUSES` are
`{agent_error, provider_error, runtime_error, scorer_error}`.

### Generating a report

```bash
# Regenerate the markdown report from existing {run_id}.jsonl files in a results dir.
alphadiana report <results_dir>
```

`alphadiana report` iterates every `*.jsonl` in the directory, builds a
`ResultStore(output_dir=results_dir, run_id=<file stem>)`, prints `to_markdown()`, and warns
on `strict_report_issues`.

## Reading & analyzing results

The canonical offline reader is `alphadiana/analysis/result_reader.py`, re-exported from
`alphadiana.analysis`:

```python
from alphadiana.analysis import (
    load_run_bundle, RunBundle, load_jsonl_records,
    compute_reliability_summary, ReportGenerator,
)

bundle = load_run_bundle(results_dir, run_id)   # frozen RunBundle: records, task_records, manifest
```

`RunBundle` is a frozen dataclass (`results_dir`, `run_id`, `jsonl_path`, `run_dir`,
`manifest`, `records`, `task_records`). `resolve_run_relative_path` rejects absolute paths so
bundles stay portable.

### Behavioral / trajectory metrics

`alphadiana/analysis/trajectory_metrics.py` defines `MAIN_METRIC_NAMES` — 14
outcome-conditioned metrics including `DeltaVerifyShare`, `AnswerAfterVerificationRate`,
`ErrorRecoveryRate`, `PrematureAnswerRate`, `VerificationConversionRate`, and
`OperationalTaxAdjustedAccuracy`. `compute_outcome_conditioned_metrics` and predicate helpers
(`has_answer_after_verification`, `has_error_recovery`, `has_premature_answer`, ...) operate
over canonical actions classified by `action_events.py`
(`plan` / `reason` / `tool_use` / `verify` / `recover` / `answer`).

### Action-space analysis tooling

The chord-diagram and action-frequency pipeline lives in
`alphadiana/analysis/scripts/analyze_tools/`. It consumes the result-store layout above
(`tasks/*.json`, optional `logprobs/`, `artifacts/{task}/agent/normalized_trace.json`) and
needs only `matplotlib`, `numpy`, `pandas` — no API keys.

```bash
# Extract action events from raw trajectories (one --spec per harness).
python analyze_tools/compute_six_action_statistics.py \
  --spec GPQA:DirectLLM:gpqa_qwen_dl:results/gpqa_directllm_qwen \
  --spec GPQA:OpenClaw:gpqa_qwen_oc:results/gpqa_openclaw_qwen \
  --output-dir analyze_tools/data/six_action_statistics
```

This produces `action_transitions_by_outcome.csv` (the key input for chord plots),
`action_counts_by_outcome.csv`, and `trajectory_metrics.csv`. See
`PORTABLE_ANALYSIS_GUIDE.md` and `README.md` in that directory for the full pipeline and the
`data/*.csv` -> `figures/*.pdf` outputs.

## FAQ

**Q: Math Verify shows "No symbolic match" on clearly correct answers.**
The `math-verify` library is not installed or failed to parse the expression. Run
`pip install math-verify`. If it persists, the scorer automatically falls back to normalized
string comparison.

**Q: Why are `accuracy` and `accuracy_total` different?**
By design. `accuracy` divides correct answers by completed (valid) records, while
`accuracy_total` divides by the expected sample count. Missing samples are surfaced rather
than counted as failures, so an incomplete run cannot inflate `accuracy`.

**Q: Re-running the same config restarts everything — why not?**
It resumes. `alphadiana run` loads the existing `{run_id}.jsonl`, skips already
`valid_scored` samples, and only re-evaluates when the scorer changes or `--redo-all` is set.
