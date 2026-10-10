# Shared registry, logprob and reporting contracts

This implements the revised boundaries in #109 on main `8faa2ce12ad9f63bef51521bf3fe655f785d3303`. The five correctness fixes from #108 are already in that baseline. No evaluator, model setting or historical experiment result is changed by this refactor.

## Ownership and compatibility

| Previous internal surface | Canonical owner | Contract |
| --- | --- | --- |
| Engine component registry copies | `registry.py` | Family namespaces, aliases, decorators, overwrite order and loaders remain unchanged. |
| Analysis logprob forwarding paths | `alphadiana/logprobs.py` | Raw candidates stay separate from compact Int16 output; both conversions preserve request attribution. |
| Report/reliability/execution aggregation | `results/statistics.py`; `analysis/export.py` | Statistics owns sample selection and denominators; export owns ReportGenerator/RunSummary and rendering. |
| `analysis/reports.py`, `analysis/action_report.py` | `analysis/actions/action_space_report.py` | Internal imports migrate; artifact filenames and explicit data-view labels remain. |

`registry_loader.py` still owns deferred built-in imports and its family-specific loaders. CLI `list-benchmarks` now calls that loader. `engine/components.py` stays a forwarding entry point. Registry-only imports do not load native adapters. Internal imports use the canonical paths. Public composition entry points remain supported; see the [analysis migration map](../../alphadiana/analysis/README.md) for internal paths that moved.

Benchmark package flattening is deferred. Removing packages such as `benchmarks.aime` would break supported nested imports such as `benchmarks.aime.benchmark`; a separate migration decision is needed. Skills, paper resources, research scripts, native harness directories, scorer/metric boundaries and Runner lifecycle remain in place.

## Counts and denominators

All four consumers—core reports, dashboard, reliability and Analyzer execution metrics—select the last persisted attempt for each `(task_id, sample_index)` in one run and use the existing score-status classifier. A failed retry replaces an earlier success in the latest view. Original JSONL history remains untouched and is accessible with `Artifacts.records(latest=False)`.

| View | Unit / denominator |
| --- | --- |
| Core `total_tasks`, `completed`, `failed` | Historical **sample** counts: planned samples, valid scored samples, remaining planned samples. Names are retained for compatibility. |
| Dashboard `total_tasks`, `completed`, `failed` | **Task** counts: planned tasks, tasks with at least one valid scored sample, remaining planned tasks. |
| Core/dashboard `accuracy` | Correct samples / eligible observed samples. Includes the existing explicit zero-contribution policy for empty answers. |
| Reliability `observed_valid_accuracy` | Correct valid samples / valid scored samples; excludes unmeasured failures. |
| Core/dashboard `accuracy_total`, reliability `expected_sample_accuracy`, execution `accuracy` | Correct valid samples / planned samples. |
| `pass_at_k`, `avg_at_k` | Task success and per-task correct/k, averaged over planned tasks. Missing tasks contribute zero. |
| Reliability `coverage`; execution resource coverage | Latest observed samples / planned samples; resource availability is counted separately, including failed samples. |

The manifest determines planned counts and k, followed by record/config fallbacks. Missing samples are counted by expected task/sample keys, so an unrelated or out-of-range record cannot fill a missing sample. Resource statistics retain their existing choice of denominator rather than being equated with score accuracy.

The offline `analyze_action_space` CLI deliberately retains two views. Its feature CSV and both cluster views use **all persisted attempts**, including superseded retries; `valid_scored_only` filters that history by score status, not by latest attempt. Reliability uses **latest samples**. Markdown shows both sets of record, valid-score and error counts side by side, plus the denominator for each reliability metric and each cluster view. JSON/CSV label the selection with `data_view`; reliability JSON also records metric denominators. With a correct attempt followed by an incorrect retry for the same sample, clustering keeps both attempts while reliability counts one incorrect sample. Historical clustering inputs, labels and algorithms remain unchanged, and source JSONL is never rewritten.

Two inconsistencies are explicitly corrected: direct reliability calls no longer count retry history as additional samples, and a count-only manifest includes unnamed missing tasks in core Avg@k, matching reliability and explicit-ID manifests. Legacy rows without `score_status` use the common classifier. No field unit or persisted result schema is changed. Existing reports are not rewritten; recomputing affected incomplete/retried runs may produce corrected aggregates.

Draft #82 was inspected before reuse. Its report code sets `total_tasks=expected_task_count`, `completed=len(completed_task_ids)` and `failed=len(attempted_task_ids - completed_task_ids)`. Those are task counts and differ from main's core sample counters, including how unobserved tasks enter failures. That reporting patch is not imported here.

## Retained action methods

The old methods remain static implementations in the main source tree, callable for comparison or reproduction. `analysis/actions/llm_annotations.py` contains the historical fixed-character splitter; `analysis/actions/keyword_baseline.py` contains the historical deterministic keyword method. Neither is a default or an error fallback. The explicit `custom-spans-v1` plugin remains supported and produces a separately named report. Ordinary action analysis uses the LLM-selected trajectory ranges and main's label definitions/priority. Old calibration is not inherited.

## Acceptance evidence

`tests/integration/test_shared_main_contracts.py` covers canonical/legacy identity, registry isolation and replacement, fresh-process discovery, optional import boundaries, raw/compact candidate and attribution preservation, all four statistics consumers, failed retries, missing tasks/samples, count-only manifests and callable historical action methods. The #108 regressions still exercise default-mode DirectLLM persistence and actual localhost streaming failures.

Delivery receipts identify the exact source tree and actual test results. Installed-wheel checks must run outside the checkout and verify imports, registration, public API operation and packaged skill bytes. The frontend contract is unchanged by this refactor; backend response and unit tests cover its existing fields.

A small live API pilot uses constructed English, Chinese and tool-call traces. It checks the segmentation protocol and source mapping, not benchmark accuracy or human agreement. A representative, independently reviewed annotation pilot is still needed before transferring scientific conclusions to the new method. Container and GPU experiments are outside this refactor's acceptance.
