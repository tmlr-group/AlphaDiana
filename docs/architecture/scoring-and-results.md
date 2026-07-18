---
sidebar_position: 5
---

# Scoring & Results

Scoring, persistence, and reporting are separate stages. A scorer converts an `AgentResponse` into a `ScoreResult`; `ResultStore` persists the task/sample record and artifacts; `ReportGenerator` aggregates only records that satisfy the current validity rules.

## Scorers

Generic scorer keys are `exact_match`, `numeric`, `math_verify`, and `llm_judge`. Benchmark-specific keys are:

| Key | Contract |
| --- | --- |
| `imo_verify` | IMO AnswerBench verifier; `math_verify` is rejected for this benchmark |
| `swe_bench` | Official SWE-bench evaluation adapter |
| `swebench_pro` | SWE-bench Pro evaluator and resolved metadata |
| `terminal_bench2` | Binary score from an actually observed verifier reward |
| `external_benchmark` | external_benchmark verifier |
| `external_benchmark_qjl` | Validates host-owned official artifacts and their hashes before scoring |
| `decodingtrust` | Runs the DTAP judge and records task/attack metrics |

Scorers do not share one universal no-answer rationale. Benchmark adapters may interpret missing patches, rewards, verifier output, or official artifacts differently. The universal status is the top-level `score_status`, not benchmark-specific supporting fields.

## Result layout

For a run ID, the store maintains a run manifest, JSONL records, task JSON files, reports, and optional artifact directories. Exact optional files depend on the harness, but the stable reading rules are:

- one JSONL record represents one `(task_id, sample_index)`;
- `tasks/<task>.json` is a sample list even when `num_samples=1`, so read `data[0]` for the first sample;
- large or structured observations are referenced through artifact paths rather than forced into one record;
- the manifest records expected task/sample counts and task metadata for strict reporting.

Task records include response content, `score`, `correct`, rationale, `score_status`, trajectories, timings, token usage, harness metadata, scorer metadata, and any error record. Benchmark-only fields such as verifier reward or patch data are supporting evidence.

## Logprob sidecars

`ResultStore.write_logprob_sidecars()` understands two metadata fields:

- `logprob_records`: raw float records, written under `logprobs/`; when present, the store can derive an Int16 compact sidecar;
- `logprob_int16_records`: already-quantized records, written directly under `logprobs_int16/`.

The stable artifact references are `logprobs_float` and `logprobs_int16`, when those files exist.

There is a current DirectLLM caveat: DirectLLM defaults to `logprobs_format: int16`, which places quantized records in `logprob_records` rather than `logprob_int16_records`. The result store therefore treats them as raw input and may quantize them again. Do not claim that this mode preserves a valid float-plus-compact pair until the field contract is corrected and covered by an artifact-shape test. Set `logprobs_format: float` explicitly when artifact fidelity matters.

## Redaction and sharing

Persistence applies best-effort redaction:

- values under keys resembling API keys, tokens, authorization, passwords, or secrets are replaced;
- common environment-assignment and Authorization-header patterns are redacted inside strings;
- several runtime/controller metadata paths apply similar recursive filtering.

This is defense in depth, not a guarantee that a result bundle is safe to publish. It cannot reliably detect arbitrary credentials, secrets embedded in prompts, private source text, unconventional token formats, or every provider payload. Before uploading a bundle, run a secret scanner and manually review at least command history, prompts, `workspace_file_contents`, provider request/response payloads, logs, and failure artifacts.

## `score_status` validity

`infer_score_status()` in `alphadiana/analysis/io/status.py` is the canonical classifier. `valid_scored` requires normal score fields and no disqualifying failure evidence. Common invalid statuses include `unscored`, `preserved_failure`, `no_answer`, `agent_empty_output`, `agent_error`, `provider_error`, `runtime_error`, `verifier_error`, and `scorer_error`.

### Timeout completion

Supported harness timeouts can be valid scored-zero samples. DirectLLM, OpenCode, OpenClaw, and ZeroClaw attach harness-specific timeout metadata and return `finish_reason: timeout`; the score is zero and the checkpoint treats the sample as complete.

Old error rows with explicit timeout evidence are normalized on load to `score=0`, `correct=false`, `finish_reason=timeout`, and `valid_scored`. The normalizer deliberately excludes provider/control-plane failures, context overflow, heartbeat/session taint, and other non-timeout evidence.

### TerminalBench 2

For `terminal_bench2`, ordinary score fields are not sufficient. `verifier_status: ok` must include an observed reward. `verifier_status: skipped_duplicate` is valid only when `verifier_reward_observed=true`, an actual reward value is present, and normal score fields exist. Missing reward evidence remains verifier-invalid and rerunnable.

## Reports

`RunSummary` includes expected/written sample counts, valid and invalid counts, errors, missing samples/tasks, accuracy, mean score, mean wall time, total tokens, Pass@k, Avg@k, and per-category variants. `k` is the configured `num_samples`; the engine does not assign benchmark-specific values.

DecodingTrust adds denominator-scoped fields:

- task-success count, denominator, and rate;
- attack-success count, denominator, and rate;
- count of valid DecodingTrust records.

These denominators only include records whose scorer metadata contains the corresponding boolean. Do not infer a full-run rate from a narrower denominator without reporting that scope.

Strict reporting compares the manifest's expected samples with written valid/invalid records and can fail the command when required samples are missing or inconsistent.

## Reading results safely

1. Load the run manifest and task JSON/JSONL records.
2. Use `infer_score_status`, not the presence of a numeric field alone.
3. Confirm the scorer name matches the run being analyzed.
4. Inspect task-level error, trajectory, and artifacts for abnormal rows.
5. For benchmark-specific claims, inspect the matching verifier, patch, reward, or DTAP judge evidence.

## Related pages

- [Engine & Runner](./engine-and-runner)
- [Observability & Proxies](./observability)
- [Registries](./registries)
