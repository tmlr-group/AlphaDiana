# Action Count Audit

This audit checks whether any extracted segment was counted as more than one
action in the six-action analysis.

## Audited Files

- `analyze_tools/data/six_action_analysis/six_action_events.csv`
- `analyze_tools/data/six_action_statistics/action_counts_by_outcome.csv`

## Segment Identity

An extracted segment is identified by:

```text
benchmark, harness, source_id, task_id, sample_index, step_id, segment_index
```

An action event is identified by:

```text
benchmark, harness, source_id, task_id, sample_index, event_index
```

## Results

| Check | Result |
|---|---:|
| Raw action event rows | 749032 |
| Unique segment identities | 749032 |
| Duplicate segment identities | 0 |
| Segment identities with multiple actions | 0 |
| Unique event-index identities | 749032 |
| Duplicate event-index identities | 0 |
| Action count keys recomputed from events | 149 |
| Reported action count keys | 149 |
| Missing count rows | 0 |
| Extra count rows | 0 |
| Count mismatches | 0 |

## Conclusion

No extracted segment is counted as two different actions. Each event row has
exactly one action label, and the aggregated action-count table is exactly
reproducible from the raw event table.

One raw assistant message can still produce multiple extracted segments, and
each of those segments can receive its own action. That is expected and is not a
double count of the same segment.
