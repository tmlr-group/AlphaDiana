# Six-Action Statistics Report

## Scope

This report computes statistics for success, failure, and unknown trajectories using the six-action space: Problem Framing, Plan Formation, Solution Execution, Tool Grounding, Result Auditing, and Answer Delivery.

## Source Data Paths

| Benchmark | Harness | Source ID | Source path | Task records |
|---|---|---|---|---:|
| GPQA | DirectLLM | `gpqa_directllm_phase9` | `results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs` | 198 |
| GPQA | OpenClaw | `gpqa_openclaw_v2` | `results/full_gpqa_v2_openclaw_qwen35_27b_logprobs` | 198 |
| GPQA | OpenCode | `gpqa_opencode_v2` | `results/full_gpqa_v2_opencode_qwen35_27b_logprobs` | 198 |
| GPQA | ZeroClaw | `gpqa_zeroclaw_v2` | `results/full_gpqa_v2_zeroclaw_qwen35_27b_logprobs` | 198 |
| HLE | DirectLLM | `hle_directllm_hf_20260423` | `results/hf-alphadiana-benchmark-results/full_run/20260423-hle-directllm-qwen35_27b-v01/results/20260423-hle-directllm-qwen35_27b-v01` | 591 |
| HLE | OpenClaw | `hle_openclaw_merged` | `results/quick_260430_hle_openclaw_qwen35_27b_merged` | 591 |
| HLE | OpenCode | `hle_opencode_20260426` | `/path/to/xxx/alphadiana-results/20260426-hle-opencode-qwen35_27b-v01` | 591 |
| HLE | ZeroClaw | `hle_zeroclaw_20260426` | `/path/to/xxx/alphadiana-results/20260426-hle-zeroclaw-qwen35_27b-v01` | 591 |
| AIMEPass4 | DirectLLM | `aime_pass4_directllm` | `/path/to/xxx/alphadiana_results/full_20260423_qwen35_27b_aime2026_directllm_r1_pass4` | 120 |
| AIMEPass4 | OpenClaw | `aime_pass4_openclaw` | `/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_openclaw_qwen35_27b_pass4_t9300_from_20260428` | 120 |
| AIMEPass4 | OpenCode | `aime_pass4_opencode` | `/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_opencode_qwen35_27b_pass4_t9300_from_20260425` | 120 |
| AIMEPass4 | ZeroClaw | `aime_pass4_zeroclaw` | `/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_zeroclaw_qwen35_27b_pass4_t9300_from_20260428` | 120 |

## Extraction Logic

- Task records are loaded from each result store's `tasks/*.json` files.
- List-valued task files are treated as multiple sample trajectories. This is required for AIME Pass@4.
- Outcome is `success` when `correct is True`, `failure` when `correct is False`, and `unknown` otherwise.
- Action spans are extracted with `compute_six_action_frequencies.py`: system/user/tool-result-only rows are excluded; assistant text is segmented; tool-call events are retained.
- Action transition counts are computed per trajectory as `__START__ -> first_action -> ... -> last_action -> __END__`.
- Entropy uses `token_entropy_stats.mean`, `max`, `p50`, and `p90` when present.
- Token length uses `token_entropy_stats.n_tokens`, falling back to completion token usage when available.
- Tool type extraction reads task-level tool events. For OpenCode, it also attempts `artifacts/<task_id>/workspace/opencode_output.jsonl` to recover tool names such as `bash`.
- Tool type transitions are computed per trajectory as `__START__ -> first_tool_type -> ... -> last_tool_type -> __END__`.

## Calculation Logic

- `action_counts_by_outcome.csv`: count action event rows by benchmark, harness, outcome, and action.
- `action_transitions_by_outcome.csv`: count adjacent action pairs by benchmark, harness, and outcome.
- `entropy_token_summary_by_outcome.csv`: summary statistics for entropy, token length, wall time, action-event count, and tool-call count.
- `tool_type_counts_by_outcome.csv`: count tool events by benchmark, harness, outcome, and inferred tool type.
- `tool_type_transitions_by_outcome.csv`: count adjacent tool-type pairs by benchmark, harness, and outcome.

## Failure Case Summary

| Benchmark | Harness | Failure traj. | Mean tokens | Mean entropy | Mean actions | Mean tools | Top action | Top tool | Extraction failures |
|---|---|---:|---:|---:|---:|---:|---|---|---|
| AIMEPass4 | DirectLLM | 11 | 47113.363636 | 0.314394 | 1803.727273 | 0.000000 | Solution Execution |  |  |
| AIMEPass4 | OpenClaw | 15 | 64169.066667 | 0.108959 | 895.400000 | 0.866667 | Solution Execution | python | missing_entropy=1; missing_token_length=1; no_action_events=28 |
| AIMEPass4 | OpenCode | 35 | 31404.914286 | 0.235899 | 467.000000 | 0.314286 | Solution Execution | tool |  |
| AIMEPass4 | ZeroClaw | 37 | 17253.428571 | 0.055769 | 97.351351 | 0.000000 | Solution Execution |  | missing_token_length=23 |
| GPQA | DirectLLM | 39 | 19534.076923 | 0.294571 | 857.282051 | 0.000000 | Solution Execution |  |  |
| GPQA | OpenClaw | 67 | 28283.603448 | 0.120127 | 307.089552 | 0.701493 | Solution Execution | web_fetch | missing_token_length=12 |
| GPQA | OpenCode | 52 | 3244.083333 | 0.276491 | 65.634615 | 0.365385 | Solution Execution | python | missing_token_length=4; no_action_events=2 |
| GPQA | ZeroClaw | 37 | 2338.189189 | 0.362605 | 38.918919 | 0.000000 | Solution Execution |  | missing_token_length=7; no_action_events=7 |
| HLE | DirectLLM | 455 | 17179.670330 | 0.425673 | 551.276923 | 0.000000 | Solution Execution |  |  |
| HLE | OpenClaw | 509 | 6972.286837 | 0.249638 | 139.550098 | 10.909627 | Solution Execution | web_fetch |  |
| HLE | OpenCode | 508 | 7377.848425 | 0.331745 | 163.625984 | 3.673228 | Solution Execution | web_fetch |  |
| HLE | ZeroClaw | 503 | 2287.010965 | 0.310747 | 42.353877 | 0.000000 | Solution Execution |  | missing_token_length=47 |

## Action Count Preview

| Benchmark | Harness | Outcome | Action | Count |
|---|---|---|---|---:|
| AIMEPass4 | DirectLLM | failure | Answer Delivery | 20 |
| AIMEPass4 | DirectLLM | failure | Plan Formation | 3742 |
| AIMEPass4 | DirectLLM | failure | Problem Framing | 155 |
| AIMEPass4 | DirectLLM | failure | Result Auditing | 1891 |
| AIMEPass4 | DirectLLM | failure | Solution Execution | 14033 |
| AIMEPass4 | DirectLLM | success | Answer Delivery | 205 |
| AIMEPass4 | DirectLLM | success | Plan Formation | 7195 |
| AIMEPass4 | DirectLLM | success | Problem Framing | 369 |
| AIMEPass4 | DirectLLM | success | Result Auditing | 5498 |
| AIMEPass4 | DirectLLM | success | Solution Execution | 42831 |
| AIMEPass4 | OpenClaw | failure | Answer Delivery | 8 |
| AIMEPass4 | OpenClaw | failure | Plan Formation | 1238 |
| AIMEPass4 | OpenClaw | failure | Problem Framing | 886 |
| AIMEPass4 | OpenClaw | failure | Result Auditing | 282 |
| AIMEPass4 | OpenClaw | failure | Solution Execution | 11003 |
| AIMEPass4 | OpenClaw | failure | Tool Grounding | 14 |
| AIMEPass4 | OpenClaw | success | Answer Delivery | 97 |
| AIMEPass4 | OpenClaw | success | Plan Formation | 241 |
| AIMEPass4 | OpenClaw | success | Problem Framing | 42 |
| AIMEPass4 | OpenClaw | success | Result Auditing | 361 |
| AIMEPass4 | OpenClaw | success | Solution Execution | 3470 |
| AIMEPass4 | OpenClaw | success | Tool Grounding | 222 |
| AIMEPass4 | OpenCode | failure | Answer Delivery | 76 |
| AIMEPass4 | OpenCode | failure | Plan Formation | 636 |
| AIMEPass4 | OpenCode | failure | Problem Framing | 423 |
| AIMEPass4 | OpenCode | failure | Result Auditing | 2523 |
| AIMEPass4 | OpenCode | failure | Solution Execution | 12676 |
| AIMEPass4 | OpenCode | failure | Tool Grounding | 11 |
| AIMEPass4 | OpenCode | success | Answer Delivery | 196 |
| AIMEPass4 | OpenCode | success | Plan Formation | 468 |
| AIMEPass4 | OpenCode | success | Problem Framing | 211 |
| AIMEPass4 | OpenCode | success | Result Auditing | 2185 |
| AIMEPass4 | OpenCode | success | Solution Execution | 12609 |
| AIMEPass4 | OpenCode | success | Tool Grounding | 147 |
| AIMEPass4 | OpenCode | unknown | Plan Formation | 70 |
| AIMEPass4 | OpenCode | unknown | Problem Framing | 209 |
| AIMEPass4 | OpenCode | unknown | Result Auditing | 1246 |
| AIMEPass4 | OpenCode | unknown | Solution Execution | 4412 |
| AIMEPass4 | ZeroClaw | failure | Answer Delivery | 8 |
| AIMEPass4 | ZeroClaw | failure | Plan Formation | 124 |
| AIMEPass4 | ZeroClaw | failure | Problem Framing | 30 |
| AIMEPass4 | ZeroClaw | failure | Result Auditing | 454 |
| AIMEPass4 | ZeroClaw | failure | Solution Execution | 2986 |
| AIMEPass4 | ZeroClaw | success | Answer Delivery | 90 |
| AIMEPass4 | ZeroClaw | success | Plan Formation | 367 |
| AIMEPass4 | ZeroClaw | success | Problem Framing | 109 |
| AIMEPass4 | ZeroClaw | success | Result Auditing | 754 |
| AIMEPass4 | ZeroClaw | success | Solution Execution | 6440 |
| GPQA | DirectLLM | failure | Answer Delivery | 30 |
| GPQA | DirectLLM | failure | Plan Formation | 6778 |
| GPQA | DirectLLM | failure | Problem Framing | 212 |
| GPQA | DirectLLM | failure | Result Auditing | 3036 |
| GPQA | DirectLLM | failure | Solution Execution | 23378 |
| GPQA | DirectLLM | success | Answer Delivery | 185 |
| GPQA | DirectLLM | success | Plan Formation | 4977 |
| GPQA | DirectLLM | success | Problem Framing | 420 |
| GPQA | DirectLLM | success | Result Auditing | 2917 |
| GPQA | DirectLLM | success | Solution Execution | 24587 |
| GPQA | OpenClaw | failure | Answer Delivery | 32 |
| GPQA | OpenClaw | failure | Plan Formation | 585 |
| GPQA | OpenClaw | failure | Problem Framing | 1872 |
| GPQA | OpenClaw | failure | Result Auditing | 3889 |
| GPQA | OpenClaw | failure | Solution Execution | 14158 |
| GPQA | OpenClaw | failure | Tool Grounding | 39 |
| GPQA | OpenClaw | success | Answer Delivery | 141 |
| GPQA | OpenClaw | success | Plan Formation | 394 |
| GPQA | OpenClaw | success | Problem Framing | 67 |
| GPQA | OpenClaw | success | Result Auditing | 221 |
| GPQA | OpenClaw | success | Solution Execution | 2400 |
| GPQA | OpenClaw | success | Tool Grounding | 223 |
| GPQA | OpenCode | failure | Answer Delivery | 50 |
| GPQA | OpenCode | failure | Plan Formation | 223 |

## Entropy and Token Preview

| Benchmark | Harness | Outcome | Metric | N | Missing | Mean | Median | P75 |
|---|---|---|---|---:|---:|---:|---:|---:|
| AIMEPass4 | DirectLLM | failure | mean_entropy | 11 | 0 | 0.314394 | 0.298862 | 0.360670 |
| AIMEPass4 | DirectLLM | failure | n_tokens | 11 | 0 | 47113.363636 | 46022.000000 | 55986.500000 |
| AIMEPass4 | DirectLLM | failure | action_event_count | 11 | 0 | 1803.727273 | 1780.000000 | 2146.500000 |
| AIMEPass4 | DirectLLM | failure | tool_call_count | 11 | 0 | 0.000000 | 0.000000 | 0.000000 |
| AIMEPass4 | DirectLLM | success | mean_entropy | 109 | 0 | 0.286259 | 0.277740 | 0.312358 |
| AIMEPass4 | DirectLLM | success | n_tokens | 109 | 0 | 20085.027523 | 19796.000000 | 23760.000000 |
| AIMEPass4 | DirectLLM | success | action_event_count | 109 | 0 | 514.660550 | 467.000000 | 685.000000 |
| AIMEPass4 | DirectLLM | success | tool_call_count | 109 | 0 | 0.000000 | 0.000000 | 0.000000 |
| AIMEPass4 | OpenClaw | failure | mean_entropy | 15 | 0 | 0.108959 | 0.072373 | 0.138359 |
| AIMEPass4 | OpenClaw | failure | n_tokens | 15 | 0 | 64169.066667 | 25175.000000 | 131072.000000 |
| AIMEPass4 | OpenClaw | failure | action_event_count | 15 | 0 | 895.400000 | 404.000000 | 1289.000000 |
| AIMEPass4 | OpenClaw | failure | tool_call_count | 15 | 0 | 0.866667 | 0.000000 | 0.000000 |
| AIMEPass4 | OpenClaw | success | mean_entropy | 77 | 0 | 0.163730 | 0.159361 | 0.186678 |
| AIMEPass4 | OpenClaw | success | n_tokens | 77 | 0 | 4127.922078 | 2308.000000 | 4209.000000 |
| AIMEPass4 | OpenClaw | success | action_event_count | 77 | 0 | 57.571429 | 16.000000 | 34.000000 |
| AIMEPass4 | OpenClaw | success | tool_call_count | 77 | 0 | 2.571429 | 2.000000 | 4.000000 |
| AIMEPass4 | OpenClaw | unknown | mean_entropy | 27 | 1 | 0.007811 | 0.006763 | 0.008493 |
| AIMEPass4 | OpenClaw | unknown | n_tokens | 27 | 1 | 118283.666667 | 118303.000000 | 121888.000000 |
| AIMEPass4 | OpenClaw | unknown | action_event_count | 28 | 0 | 0.000000 | 0.000000 | 0.000000 |
| AIMEPass4 | OpenClaw | unknown | tool_call_count | 28 | 0 | 0.000000 | 0.000000 | 0.000000 |
| AIMEPass4 | OpenCode | failure | mean_entropy | 35 | 0 | 0.235899 | 0.247474 | 0.315253 |
| AIMEPass4 | OpenCode | failure | n_tokens | 35 | 0 | 31404.914286 | 13792.000000 | 33153.000000 |
| AIMEPass4 | OpenCode | failure | action_event_count | 35 | 0 | 467.000000 | 250.000000 | 671.000000 |
| AIMEPass4 | OpenCode | failure | tool_call_count | 35 | 0 | 0.314286 | 0.000000 | 0.000000 |
| AIMEPass4 | OpenCode | success | mean_entropy | 80 | 0 | 0.230772 | 0.228401 | 0.269068 |
| AIMEPass4 | OpenCode | success | n_tokens | 80 | 0 | 6146.587500 | 2485.000000 | 5830.250000 |
| AIMEPass4 | OpenCode | success | action_event_count | 80 | 0 | 197.700000 | 88.000000 | 206.000000 |
| AIMEPass4 | OpenCode | success | tool_call_count | 80 | 0 | 1.837500 | 1.000000 | 2.000000 |
| AIMEPass4 | OpenCode | unknown | mean_entropy | 5 | 0 | 0.081411 | 0.074895 | 0.080448 |
| AIMEPass4 | OpenCode | unknown | n_tokens | 5 | 0 | 90092.800000 | 95481.000000 | 104790.000000 |
| AIMEPass4 | OpenCode | unknown | action_event_count | 5 | 0 | 1187.400000 | 1608.000000 | 1734.000000 |
| AIMEPass4 | OpenCode | unknown | tool_call_count | 5 | 0 | 0.000000 | 0.000000 | 0.000000 |
| AIMEPass4 | ZeroClaw | failure | mean_entropy | 37 | 0 | 0.055769 | 0.000000 | 0.104892 |
| AIMEPass4 | ZeroClaw | failure | n_tokens | 14 | 23 | 17253.428571 | 7402.000000 | 10531.000000 |
| AIMEPass4 | ZeroClaw | failure | action_event_count | 37 | 0 | 97.351351 | 1.000000 | 56.000000 |
| AIMEPass4 | ZeroClaw | failure | tool_call_count | 37 | 0 | 0.000000 | 0.000000 | 0.000000 |
| AIMEPass4 | ZeroClaw | success | mean_entropy | 83 | 0 | 0.127627 | 0.123583 | 0.145329 |
| AIMEPass4 | ZeroClaw | success | n_tokens | 83 | 0 | 5228.132530 | 3256.000000 | 7307.500000 |
| AIMEPass4 | ZeroClaw | success | action_event_count | 83 | 0 | 93.493976 | 52.000000 | 132.500000 |
| AIMEPass4 | ZeroClaw | success | tool_call_count | 83 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | DirectLLM | failure | mean_entropy | 39 | 0 | 0.294571 | 0.318769 | 0.389776 |
| GPQA | DirectLLM | failure | n_tokens | 39 | 0 | 19534.076923 | 15175.000000 | 32768.000000 |
| GPQA | DirectLLM | failure | action_event_count | 39 | 0 | 857.282051 | 553.000000 | 1278.000000 |
| GPQA | DirectLLM | failure | tool_call_count | 39 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | DirectLLM | success | mean_entropy | 159 | 0 | 0.322206 | 0.316161 | 0.377757 |
| GPQA | DirectLLM | success | n_tokens | 159 | 0 | 9049.666667 | 8554.000000 | 10861.500000 |
| GPQA | DirectLLM | success | action_event_count | 159 | 0 | 208.088050 | 132.000000 | 279.500000 |
| GPQA | DirectLLM | success | tool_call_count | 159 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | OpenClaw | failure | mean_entropy | 67 | 0 | 0.120127 | 0.037977 | 0.177857 |
| GPQA | OpenClaw | failure | n_tokens | 58 | 9 | 28283.603448 | 20982.500000 | 50176.750000 |
| GPQA | OpenClaw | failure | action_event_count | 67 | 0 | 307.089552 | 200.000000 | 544.000000 |
| GPQA | OpenClaw | failure | tool_call_count | 67 | 0 | 0.701493 | 0.000000 | 0.000000 |
| GPQA | OpenClaw | success | mean_entropy | 131 | 0 | 0.247818 | 0.239430 | 0.330208 |
| GPQA | OpenClaw | success | n_tokens | 128 | 3 | 2372.937500 | 1565.500000 | 2817.500000 |
| GPQA | OpenClaw | success | action_event_count | 131 | 0 | 26.305344 | 21.000000 | 31.000000 |
| GPQA | OpenClaw | success | tool_call_count | 131 | 0 | 1.366412 | 1.000000 | 2.000000 |
| GPQA | OpenCode | failure | mean_entropy | 52 | 0 | 0.276491 | 0.300787 | 0.402796 |
| GPQA | OpenCode | failure | n_tokens | 48 | 4 | 3244.083333 | 2223.000000 | 5805.250000 |
| GPQA | OpenCode | failure | action_event_count | 52 | 0 | 65.634615 | 66.000000 | 80.500000 |
| GPQA | OpenCode | failure | tool_call_count | 52 | 0 | 0.365385 | 0.000000 | 0.000000 |
| GPQA | OpenCode | success | mean_entropy | 145 | 0 | 0.301411 | 0.298904 | 0.370182 |
| GPQA | OpenCode | success | n_tokens | 145 | 0 | 1066.744828 | 936.000000 | 1322.000000 |
| GPQA | OpenCode | success | action_event_count | 145 | 0 | 48.958621 | 46.000000 | 58.000000 |
| GPQA | OpenCode | success | tool_call_count | 145 | 0 | 0.972414 | 1.000000 | 2.000000 |
| GPQA | OpenCode | unknown | mean_entropy | 1 | 0 | 0.045275 | 0.045275 | 0.045275 |
| GPQA | OpenCode | unknown | n_tokens | 1 | 0 | 19310.000000 | 19310.000000 | 19310.000000 |
| GPQA | OpenCode | unknown | action_event_count | 1 | 0 | 273.000000 | 273.000000 | 273.000000 |
| GPQA | OpenCode | unknown | tool_call_count | 1 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | ZeroClaw | failure | mean_entropy | 37 | 0 | 0.362605 | 0.362327 | 0.443968 |
| GPQA | ZeroClaw | failure | n_tokens | 37 | 0 | 2338.189189 | 1835.000000 | 2303.000000 |
| GPQA | ZeroClaw | failure | action_event_count | 37 | 0 | 38.918919 | 34.000000 | 40.000000 |
| GPQA | ZeroClaw | failure | tool_call_count | 37 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | ZeroClaw | success | mean_entropy | 154 | 0 | 0.247544 | 0.233742 | 0.331820 |
| GPQA | ZeroClaw | success | n_tokens | 154 | 0 | 2101.668831 | 1965.500000 | 2477.750000 |
| GPQA | ZeroClaw | success | action_event_count | 154 | 0 | 39.435065 | 34.000000 | 43.000000 |
| GPQA | ZeroClaw | success | tool_call_count | 154 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | ZeroClaw | unknown | mean_entropy | 7 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | ZeroClaw | unknown | n_tokens | 0 | 7 |  |  |  |
| GPQA | ZeroClaw | unknown | action_event_count | 7 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | ZeroClaw | unknown | tool_call_count | 7 | 0 | 0.000000 | 0.000000 | 0.000000 |
| HLE | DirectLLM | failure | mean_entropy | 455 | 0 | 0.425673 | 0.462583 | 0.546737 |
| HLE | DirectLLM | failure | n_tokens | 455 | 0 | 17179.670330 | 8489.000000 | 14319.000000 |
| HLE | DirectLLM | failure | action_event_count | 455 | 0 | 551.276923 | 183.000000 | 420.500000 |
| HLE | DirectLLM | failure | tool_call_count | 455 | 0 | 0.000000 | 0.000000 | 0.000000 |
| HLE | DirectLLM | success | mean_entropy | 136 | 0 | 0.390476 | 0.369827 | 0.483433 |
| HLE | DirectLLM | success | n_tokens | 136 | 0 | 13176.764706 | 10535.500000 | 16668.500000 |
| HLE | DirectLLM | success | action_event_count | 136 | 0 | 408.029412 | 265.000000 | 476.000000 |
| HLE | DirectLLM | success | tool_call_count | 136 | 0 | 0.000000 | 0.000000 | 0.000000 |
| HLE | OpenClaw | failure | mean_entropy | 509 | 0 | 0.249638 | 0.265713 | 0.393018 |
| HLE | OpenClaw | failure | n_tokens | 509 | 0 | 6972.286837 | 2296.000000 | 12028.000000 |
| HLE | OpenClaw | failure | action_event_count | 509 | 0 | 139.550098 | 40.000000 | 198.000000 |
| HLE | OpenClaw | failure | tool_call_count | 509 | 0 | 10.909627 | 0.000000 | 2.000000 |
| HLE | OpenClaw | success | mean_entropy | 79 | 0 | 0.341572 | 0.358383 | 0.406583 |
| HLE | OpenClaw | success | n_tokens | 79 | 0 | 2604.936709 | 1882.000000 | 3171.000000 |
| HLE | OpenClaw | success | action_event_count | 79 | 0 | 54.582278 | 34.000000 | 54.000000 |
| HLE | OpenClaw | success | tool_call_count | 79 | 0 | 3.189873 | 0.000000 | 2.500000 |
| HLE | OpenClaw | unknown | mean_entropy | 3 | 0 | 0.161178 | 0.058286 | 0.219869 |
| HLE | OpenClaw | unknown | n_tokens | 3 | 0 | 12686.666667 | 11574.000000 | 18168.000000 |
| HLE | OpenClaw | unknown | action_event_count | 3 | 0 | 154.333333 | 59.000000 | 217.500000 |
| HLE | OpenClaw | unknown | tool_call_count | 3 | 0 | 21.666667 | 6.000000 | 32.500000 |
| HLE | OpenCode | failure | mean_entropy | 508 | 0 | 0.331745 | 0.386261 | 0.457929 |
| HLE | OpenCode | failure | n_tokens | 508 | 0 | 7377.848425 | 1247.500000 | 3211.000000 |
| HLE | OpenCode | failure | action_event_count | 508 | 0 | 163.625984 | 56.000000 | 144.000000 |
| HLE | OpenCode | failure | tool_call_count | 508 | 0 | 3.673228 | 0.000000 | 0.000000 |
| HLE | OpenCode | success | mean_entropy | 82 | 0 | 0.419434 | 0.417058 | 0.488961 |
| HLE | OpenCode | success | n_tokens | 82 | 0 | 1465.695122 | 1171.500000 | 1499.000000 |
| HLE | OpenCode | success | action_event_count | 82 | 0 | 65.109756 | 52.000000 | 70.000000 |
| HLE | OpenCode | success | tool_call_count | 82 | 0 | 1.597561 | 0.000000 | 0.000000 |
| HLE | OpenCode | unknown | mean_entropy | 1 | 0 | 0.259921 | 0.259921 | 0.259921 |
| HLE | OpenCode | unknown | n_tokens | 1 | 0 | 4729.000000 | 4729.000000 | 4729.000000 |
| HLE | OpenCode | unknown | action_event_count | 1 | 0 | 92.000000 | 92.000000 | 92.000000 |
| HLE | OpenCode | unknown | tool_call_count | 1 | 0 | 4.000000 | 4.000000 | 4.000000 |
| HLE | ZeroClaw | failure | mean_entropy | 503 | 0 | 0.310747 | 0.337804 | 0.407211 |
| HLE | ZeroClaw | failure | n_tokens | 456 | 47 | 2287.010965 | 1597.000000 | 2688.500000 |
| HLE | ZeroClaw | failure | action_event_count | 503 | 0 | 42.353877 | 32.000000 | 47.000000 |
| HLE | ZeroClaw | failure | tool_call_count | 503 | 0 | 0.000000 | 0.000000 | 0.000000 |
| HLE | ZeroClaw | success | mean_entropy | 88 | 0 | 0.337373 | 0.349062 | 0.414406 |
| HLE | ZeroClaw | success | n_tokens | 88 | 0 | 2339.715909 | 1590.000000 | 2659.750000 |
| HLE | ZeroClaw | success | action_event_count | 88 | 0 | 56.181818 | 35.500000 | 57.000000 |
| HLE | ZeroClaw | success | tool_call_count | 88 | 0 | 0.000000 | 0.000000 | 0.000000 |

## Tool Count Preview

| Benchmark | Harness | Outcome | Tool type | Count |
|---|---|---|---|---:|
| AIMEPass4 | OpenClaw | failure | python | 13 |
| AIMEPass4 | OpenClaw | success | bash | 15 |
| AIMEPass4 | OpenClaw | success | edit | 2 |
| AIMEPass4 | OpenClaw | success | python | 153 |
| AIMEPass4 | OpenClaw | success | read | 1 |
| AIMEPass4 | OpenClaw | success | search | 2 |
| AIMEPass4 | OpenClaw | success | tool | 2 |
| AIMEPass4 | OpenClaw | success | write | 23 |
| AIMEPass4 | OpenCode | failure | tool | 11 |
| AIMEPass4 | OpenCode | success | tool | 147 |
| GPQA | OpenClaw | failure | bash | 2 |
| GPQA | OpenClaw | failure | python | 10 |
| GPQA | OpenClaw | failure | search | 2 |
| GPQA | OpenClaw | failure | tool | 1 |
| GPQA | OpenClaw | failure | web_fetch | 29 |
| GPQA | OpenClaw | failure | write | 3 |
| GPQA | OpenClaw | success | bash | 20 |
| GPQA | OpenClaw | success | python | 113 |
| GPQA | OpenClaw | success | tool | 12 |
| GPQA | OpenClaw | success | web_fetch | 29 |
| GPQA | OpenClaw | success | write | 5 |
| GPQA | OpenCode | failure | bash | 1 |
| GPQA | OpenCode | failure | python | 10 |
| GPQA | OpenCode | failure | web_fetch | 8 |
| GPQA | OpenCode | success | bash | 14 |
| GPQA | OpenCode | success | invalid | 1 |
| GPQA | OpenCode | success | python | 121 |
| GPQA | OpenCode | success | read | 2 |
| GPQA | OpenCode | success | web_fetch | 2 |
| GPQA | OpenCode | success | write | 1 |
| HLE | OpenClaw | failure | bash | 313 |
| HLE | OpenClaw | failure | edit | 2 |
| HLE | OpenClaw | failure | python | 96 |
| HLE | OpenClaw | failure | read | 1 |
| HLE | OpenClaw | failure | search | 291 |
| HLE | OpenClaw | failure | tool | 86 |
| HLE | OpenClaw | failure | web_fetch | 4739 |
| HLE | OpenClaw | failure | write | 25 |
| HLE | OpenClaw | success | python | 26 |
| HLE | OpenClaw | success | read | 2 |
| HLE | OpenClaw | success | search | 31 |
| HLE | OpenClaw | success | tool | 4 |
| HLE | OpenClaw | success | web_fetch | 183 |
| HLE | OpenClaw | success | write | 6 |
| HLE | OpenClaw | unknown | bash | 1 |
| HLE | OpenClaw | unknown | read | 1 |
| HLE | OpenClaw | unknown | search | 2 |
| HLE | OpenClaw | unknown | tool | 2 |
| HLE | OpenClaw | unknown | web_fetch | 59 |
| HLE | OpenCode | failure | bash | 697 |
| HLE | OpenCode | failure | image | 2 |
| HLE | OpenCode | failure | invalid | 7 |
| HLE | OpenCode | failure | python | 106 |
| HLE | OpenCode | failure | read | 7 |
| HLE | OpenCode | failure | search | 144 |
| HLE | OpenCode | failure | web_fetch | 898 |
| HLE | OpenCode | failure | write | 5 |
| HLE | OpenCode | success | bash | 17 |
| HLE | OpenCode | success | invalid | 1 |
| HLE | OpenCode | success | python | 36 |
| HLE | OpenCode | success | search | 43 |
| HLE | OpenCode | success | web_fetch | 33 |
| HLE | OpenCode | success | write | 1 |
| HLE | OpenCode | unknown | bash | 1 |
| HLE | OpenCode | unknown | image | 1 |
| HLE | OpenCode | unknown | python | 1 |
| HLE | OpenCode | unknown | read | 1 |

## Output Files

- `source_manifest.csv`
- `trajectory_metrics.csv`
- `action_counts_by_outcome.csv`
- `action_transitions_by_outcome.csv`
- `entropy_token_summary_by_outcome.csv`
- `tool_type_counts_by_outcome.csv`
- `tool_type_transitions_by_outcome.csv`
- `failure_case_summary.csv`
- `extraction_failure_log.csv`
