# Six-Action Frequency Report

## Action Extraction Logic

The classifier uses exactly six action labels: Problem Framing, Plan Formation, Solution Execution, Tool Grounding, Result Auditing, and Answer Delivery.

The primary unit is a model-generated action span. System, user, and observation-only tool-result rows are excluded. Assistant text is split by paragraph or long-line boundaries so long DirectLLM outputs can contain multiple actions. Tool-call events are retained as model actions.

Priority order is Answer Delivery, Tool Grounding, Result Auditing, Solution Execution, Plan Formation, then Problem Framing. If no precise rule fires, substantive assistant text defaults to Solution Execution with `low_confidence=true`; very short low-context text defaults to Plan Formation with `low_confidence=true`.

## Number Calculation Logic

- `six_action_events.csv` is the auditable event table. Each row is one extracted action span.
- `six_action_frequency_by_outcome.csv` counts event rows by benchmark, harness, outcome, and action. `event_pct = event_count / event_total * 100` within that benchmark-harness-outcome bucket.
- `six_action_trajectory_rates.csv` counts trajectories containing at least one action. `trajectory_rate_pct = trajectories_with_action / trajectory_count * 100`; `mean_events_per_trajectory` is the mean number of events of that action per trajectory.
- `six_action_denominator_ledger.csv` records task files, trajectory records, outcome counts, records with extracted events, total action events, and low-confidence events.
- `six_action_failure_summary.csv` summarizes failure trajectories only, including dominant failure action composition and terminal action rates.

For AIME Pass@4, each sample is treated as one trajectory. A sample with `correct=true` is a success trajectory; a sample with `correct=false` is a failure trajectory. Pass@4 task-level success is not used as the action-frequency denominator.

## Denominator Ledger

| Benchmark | Harness | Source ID | Records | Success | Failure | Unknown | Events | Low-conf events |
|---|---|---|---:|---:|---:|---:|---:|---:|
| GPQA | DirectLLM | `gpqa_directllm_phase9` | 198 | 159 | 39 | 0 | 66520 | 34322 |
| GPQA | OpenClaw | `gpqa_openclaw_v2` | 198 | 131 | 67 | 0 | 24021 | 6370 |
| GPQA | OpenCode | `gpqa_opencode_v2` | 198 | 145 | 52 | 1 | 10785 | 3080 |
| GPQA | ZeroClaw | `gpqa_zeroclaw_v2` | 198 | 154 | 37 | 7 | 7513 | 2254 |
| HLE | DirectLLM | `hle_directllm_hf_20260423` | 591 | 136 | 455 | 0 | 306323 | 188185 |
| HLE | OpenClaw | `hle_openclaw_merged` | 591 | 79 | 509 | 3 | 75806 | 25009 |
| HLE | OpenCode | `hle_opencode_20260426` | 591 | 82 | 508 | 1 | 88553 | 25756 |
| HLE | ZeroClaw | `hle_zeroclaw_20260426` | 591 | 88 | 503 | 0 | 26248 | 10224 |
| AIMEPass4 | DirectLLM | `aime_pass4_directllm` | 120 | 109 | 11 | 0 | 75939 | 30361 |
| AIMEPass4 | OpenClaw | `aime_pass4_openclaw` | 120 | 77 | 15 | 28 | 17864 | 5511 |
| AIMEPass4 | OpenCode | `aime_pass4_opencode` | 120 | 80 | 35 | 5 | 38098 | 5019 |
| AIMEPass4 | ZeroClaw | `aime_pass4_zeroclaw` | 120 | 83 | 37 | 0 | 11362 | 2054 |

## Event Frequency by Outcome

| Benchmark | Harness | Outcome | Events | Problem Framing | Plan Formation | Solution Execution | Tool Grounding | Result Auditing | Answer Delivery |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| AIMEPass4 | DirectLLM | failure | 19841 | 155 (0.78%) | 3742 (18.86%) | 14033 (70.73%) | 0 (0.00%) | 1891 (9.53%) | 20 (0.10%) |
| AIMEPass4 | DirectLLM | success | 56098 | 369 (0.66%) | 7195 (12.83%) | 42831 (76.35%) | 0 (0.00%) | 5498 (9.80%) | 205 (0.37%) |
| AIMEPass4 | OpenClaw | failure | 13431 | 886 (6.60%) | 1238 (9.22%) | 11003 (81.92%) | 14 (0.10%) | 282 (2.10%) | 8 (0.06%) |
| AIMEPass4 | OpenClaw | success | 4433 | 42 (0.95%) | 241 (5.44%) | 3470 (78.28%) | 222 (5.01%) | 361 (8.14%) | 97 (2.19%) |
| AIMEPass4 | OpenCode | failure | 16345 | 423 (2.59%) | 636 (3.89%) | 12676 (77.55%) | 11 (0.07%) | 2523 (15.44%) | 76 (0.46%) |
| AIMEPass4 | OpenCode | success | 15816 | 211 (1.33%) | 468 (2.96%) | 12609 (79.72%) | 147 (0.93%) | 2185 (13.82%) | 196 (1.24%) |
| AIMEPass4 | OpenCode | unknown | 5937 | 209 (3.52%) | 70 (1.18%) | 4412 (74.31%) | 0 (0.00%) | 1246 (20.99%) | 0 (0.00%) |
| AIMEPass4 | ZeroClaw | failure | 3602 | 30 (0.83%) | 124 (3.44%) | 2986 (82.90%) | 0 (0.00%) | 454 (12.60%) | 8 (0.22%) |
| AIMEPass4 | ZeroClaw | success | 7760 | 109 (1.40%) | 367 (4.73%) | 6440 (82.99%) | 0 (0.00%) | 754 (9.72%) | 90 (1.16%) |
| GPQA | DirectLLM | failure | 33434 | 212 (0.63%) | 6778 (20.27%) | 23378 (69.92%) | 0 (0.00%) | 3036 (9.08%) | 30 (0.09%) |
| GPQA | DirectLLM | success | 33086 | 420 (1.27%) | 4977 (15.04%) | 24587 (74.31%) | 0 (0.00%) | 2917 (8.82%) | 185 (0.56%) |
| GPQA | OpenClaw | failure | 20575 | 1872 (9.10%) | 585 (2.84%) | 14158 (68.81%) | 39 (0.19%) | 3889 (18.90%) | 32 (0.16%) |
| GPQA | OpenClaw | success | 3446 | 67 (1.94%) | 394 (11.43%) | 2400 (69.65%) | 223 (6.47%) | 221 (6.41%) | 141 (4.09%) |
| GPQA | OpenCode | failure | 3413 | 149 (4.37%) | 223 (6.53%) | 2383 (69.82%) | 17 (0.50%) | 591 (17.32%) | 50 (1.46%) |
| GPQA | OpenCode | success | 7099 | 185 (2.61%) | 575 (8.10%) | 5505 (77.55%) | 138 (1.94%) | 398 (5.61%) | 298 (4.20%) |
| GPQA | OpenCode | unknown | 273 | 0 (0.00%) | 2 (0.73%) | 229 (83.88%) | 0 (0.00%) | 42 (15.38%) | 0 (0.00%) |
| GPQA | ZeroClaw | failure | 1440 | 46 (3.19%) | 134 (9.31%) | 1060 (73.61%) | 0 (0.00%) | 168 (11.67%) | 32 (2.22%) |
| GPQA | ZeroClaw | success | 6073 | 137 (2.26%) | 487 (8.02%) | 4859 (80.01%) | 0 (0.00%) | 431 (7.10%) | 159 (2.62%) |
| HLE | DirectLLM | failure | 250831 | 1758 (0.70%) | 68162 (27.17%) | 146365 (58.35%) | 0 (0.00%) | 33937 (13.53%) | 609 (0.24%) |
| HLE | DirectLLM | success | 55492 | 605 (1.09%) | 8810 (15.88%) | 38383 (69.17%) | 0 (0.00%) | 7540 (13.59%) | 154 (0.28%) |
| HLE | OpenClaw | failure | 71031 | 2851 (4.01%) | 4115 (5.79%) | 45002 (63.36%) | 5377 (7.57%) | 13003 (18.31%) | 683 (0.96%) |
| HLE | OpenClaw | success | 4312 | 185 (4.29%) | 286 (6.63%) | 3041 (70.52%) | 247 (5.73%) | 461 (10.69%) | 92 (2.13%) |
| HLE | OpenClaw | unknown | 463 | 2 (0.43%) | 7 (1.51%) | 262 (56.59%) | 64 (13.82%) | 127 (27.43%) | 1 (0.22%) |
| HLE | OpenCode | failure | 83122 | 2740 (3.30%) | 4031 (4.85%) | 59319 (71.36%) | 1759 (2.12%) | 14527 (17.48%) | 746 (0.90%) |
| HLE | OpenCode | success | 5339 | 251 (4.70%) | 401 (7.51%) | 3840 (71.92%) | 122 (2.29%) | 549 (10.28%) | 176 (3.30%) |
| HLE | OpenCode | unknown | 92 | 2 (2.17%) | 0 (0.00%) | 55 (59.78%) | 4 (4.35%) | 31 (33.70%) | 0 (0.00%) |
| HLE | ZeroClaw | failure | 21304 | 996 (4.68%) | 1846 (8.67%) | 14616 (68.61%) | 0 (0.00%) | 3409 (16.00%) | 437 (2.05%) |
| HLE | ZeroClaw | success | 4944 | 178 (3.60%) | 346 (7.00%) | 3538 (71.56%) | 0 (0.00%) | 747 (15.11%) | 135 (2.73%) |

## Failure Case Summary

| Benchmark | Harness | Failure traj. | Failure events | Top action | Top action % | Terminal answer % | Terminal audit % | Tool event % | Audit event % |
|---|---|---:|---:|---|---:|---:|---:|---:|---:|
| AIMEPass4 | DirectLLM | 11 | 19841 | Solution Execution | 70.73 | 100.00 | 0.00 | 0.00 | 9.53 |
| AIMEPass4 | OpenClaw | 15 | 13431 | Solution Execution | 81.92 | 53.33 | 0.00 | 0.10 | 2.10 |
| AIMEPass4 | OpenCode | 35 | 16345 | Solution Execution | 77.55 | 68.57 | 5.71 | 0.07 | 15.44 |
| AIMEPass4 | ZeroClaw | 37 | 3602 | Solution Execution | 82.90 | 16.22 | 10.81 | 0.00 | 12.60 |
| GPQA | DirectLLM | 39 | 33434 | Solution Execution | 69.92 | 66.67 | 5.13 | 0.00 | 9.08 |
| GPQA | OpenClaw | 67 | 20575 | Solution Execution | 68.81 | 28.36 | 17.91 | 0.19 | 18.90 |
| GPQA | OpenCode | 50 | 3413 | Solution Execution | 69.82 | 48.00 | 12.00 | 0.50 | 17.32 |
| GPQA | ZeroClaw | 37 | 1440 | Solution Execution | 73.61 | 81.08 | 2.70 | 0.00 | 11.67 |
| HLE | DirectLLM | 455 | 250831 | Solution Execution | 58.35 | 93.85 | 0.88 | 0.00 | 13.53 |
| HLE | OpenClaw | 509 | 71031 | Solution Execution | 63.36 | 55.99 | 6.68 | 7.57 | 18.31 |
| HLE | OpenCode | 508 | 83122 | Solution Execution | 71.36 | 70.67 | 5.31 | 2.12 | 17.48 |
| HLE | ZeroClaw | 503 | 21304 | Solution Execution | 68.61 | 72.76 | 5.37 | 0.00 | 16.00 |

## Failure Interpretation Guide

- High Solution Execution on failures means wrong trajectories kept doing internal reasoning or calculation rather than being blocked by missing output.
- High Tool Grounding on failures means tool interaction or observation integration dominated the failed trajectory.
- High Result Auditing on failures means trajectories spent substantial mass checking, diagnosing, or correcting but did not end correct.
- High terminal Answer Delivery on failures means the model still emitted a final answer despite being wrong.
- High terminal Result Auditing on failures means the trajectory often ended in checking, uncertainty, or unresolved correction instead of a final answer.

## Output Files

- `analyze_tools/data/six_action_analysis/six_action_frequency_by_outcome.csv`
- `analyze_tools/data/six_action_analysis/six_action_trajectory_rates.csv`
- `analyze_tools/data/six_action_analysis/six_action_failure_summary.csv`
- `analyze_tools/data/six_action_analysis/six_action_events.csv`
- `analyze_tools/data/six_action_analysis/six_action_denominator_ledger.csv`
