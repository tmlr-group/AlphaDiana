# Main review corrections

The changes tracked by #108 preserve raw DirectLLM probabilities until
ResultStore writes raw and compact sidecars, enforce streaming deadlines across
header waits and stalled reads, include bundled skill resources in wheels, and
copy the job context for each concurrent task submission.

Dashboard aggregation now uses `ReportGenerator.summarize`, including the latest
attempt per sample, score validity, the run manifest and expected denominators.
Raw attempt history remains unchanged. A later failed attempt does not resurrect
an earlier success. No change is made to benchmark scoring criteria.

## Dashboard field contracts

| Field | Unit and denominator |
| --- | --- |
| `total_tasks` | Planned task count, or observed unique tasks without a manifest |
| `completed` | Tasks with at least one valid scored latest sample |
| `failed` | Planned tasks without any such sample; includes unobserved tasks |
| `expected_sample_count` | Planned samples; explicit new field |
| `valid_scored` | Valid scored latest samples; explicit new field |
| `missing_tasks`, `missing_samples` | Planned entries with no saved latest record |
| `accuracy` | Core report accuracy over eligible observed samples, including existing explicit zero-contribution policy |
| `accuracy_total` | Correct samples divided by planned samples |
| `pass_at_k` | Planned tasks with any valid correct sample divided by planned tasks |
| `avg_at_k` | Correct samples / planned k per task, averaged over planned task IDs |

Dashboard task counters retain their units. The core `RunSummary` historical
`total_tasks/completed/failed` fields remain sample counts; they are deliberately
not copied into those dashboard task fields. Dashboard accuracy now uses the
same sample denominator as the core report, correcting its former task-any-success
calculation. The frontend displays the sample denominator and completeness warning.
No fields are borrowed from draft #82 without checking their units.

## Validation scope

`tests/test_main_review_regressions.py` exercises a real localhost HTTP provider,
ResultStore sidecars, streaming stalls (headers, first chunk, partial text, and
keepalive-only streams), incomplete/invalid/retried reports and simultaneous jobs.
`tests/test_api_pipeline_demo.py` uses the default DirectLLM format through the
public Runner API, rescores saved evidence and exports analysis figures.

`tests/test_packaged_runtime_resources.py` checks resource lookup from an unrelated
working directory. Release checks must also run it against an installed wheel,
compare packaged skill resources with the source snapshot, and build/lint the
dashboard. Exact command results and tested commits are recorded on the delivery PR.

These software checks use scripted providers. They do not constitute real-model
or real-container benchmark acceptance, repair historical damaged sidecars, or
recompute earlier experimental scores. Broad registry/report-path restructuring
and the remaining statistics consumers belong to #109.
