# Six-Action Statistics Report

## Scope

This report computes statistics for success, failure, and unknown trajectories using the six-action space: Problem Framing, Plan Formation, Solution Execution, Tool Grounding, Result Auditing, and Answer Delivery.

## Source Data Paths

| Benchmark | Harness | Source ID | Source path | Task records |
|---|---|---|---|---:|
| GPQA | DirectLLM | `gpqa_gemma_directllm` | `results/422_full/results/full_gpqa_directllm_gemma4_31b_logprobs` | 198 |
| GPQA | OpenClaw | `gpqa_gemma_openclaw` | `results/422_full/results/full_gpqa_openclaw_gemma4_31b_logprobs` | 198 |
| GPQA | OpenCode | `gpqa_gemma_opencode` | `results/422_full/results/full_gpqa_opencode_gemma4_31b_logprobs` | 198 |
| GPQA | ZeroClaw | `gpqa_gemma_zeroclaw` | `results/422_full/results/full_gpqa_zeroclaw_gemma4_31b_logprobs` | 198 |
| HLE | DirectLLM | `hle_gemma_directllm` | `results/422_full/results/full_hle_directllm_gemma4_31b_logprobs` | 591 |
| HLE | OpenClaw | `hle_gemma_openclaw` | `results/422_full/results/full_hle_openclaw_gemma4_31b_logprobs` | 591 |
| HLE | OpenCode | `hle_gemma_opencode` | `results/422_full/results/full_hle_opencode_gemma4_31b_logprobs` | 591 |
| HLE | ZeroClaw | `hle_gemma_zeroclaw` | `results/422_full/results/full_hle_zeroclaw_gemma4_31b_logprobs` | 591 |
| AIMEPass4 | DirectLLM | `aime_gemma_directllm` | `/path/to/xxx/results/full_aime2026_directllm_gemma4_31b_k4_logprobs` | 120 |
| AIMEPass4 | OpenClaw | `aime_gemma_openclaw` | `/path/to/xxx/results/quick_260503_aime2026_openclaw_gemma4_31b_8012_pass4_c1` | 120 |
| AIMEPass4 | OpenCode | `aime_gemma_opencode` | `/path/to/xxx/results/full_20260503_aime2026_opencode_gemma4_31b_8012_pass4_c4` | 120 |
| AIMEPass4 | ZeroClaw | `aime_gemma_zeroclaw` | `/path/to/xxx/results/full_20260503_aime2026_zeroclaw_gemma4_31b_8011_pass4_c4` | 120 |

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
| AIMEPass4 | DirectLLM | 9 | 15108.777778 | 0.151431 | 28.777778 | 0.000000 | Solution Execution |  |  |
| AIMEPass4 | OpenClaw | 3 | 14197.333333 | 0.189118 | 10.333333 | 6.333333 | Tool Grounding | write |  |
| AIMEPass4 | OpenCode | 3 | 102858.666667 | 0.064950 | 11.000000 | 12.000000 | Tool Grounding | bash |  |
| AIMEPass4 | ZeroClaw | 4 | 48584.750000 | 0.177618 | 94.000000 | 0.000000 | Solution Execution |  |  |
| GPQA | DirectLLM | 33 | 21797.606061 | 0.203862 | 7.575758 | 0.000000 | Solution Execution |  | no_action_events=5 |
| GPQA | OpenClaw | 27 | 10124.888889 | 0.263712 | 11.666667 | 3.333333 | Solution Execution | bash |  |
| GPQA | OpenCode | 24 | 18838.708333 | 0.229143 | 20.875000 | 2.041667 | Solution Execution | web_fetch |  |
| GPQA | ZeroClaw | 27 | 33595.481481 | 0.225302 | 12.814815 | 0.000000 | Solution Execution |  |  |
| HLE | DirectLLM | 426 | 13579.704225 | 0.255880 | 14.737089 | 0.000000 | Solution Execution |  | no_action_events=33 |
| HLE | OpenClaw | 448 | 19885.334821 | 0.245631 | 10.314732 | 4.055804 | Solution Execution | bash | no_action_events=23 |
| HLE | OpenCode | 449 | 12983.768374 | 0.248476 | 147.951002 | 11.527840 | Solution Execution | web_fetch |  |
| HLE | ZeroClaw | 419 | 10978.607229 | 0.269975 | 20.510740 | 0.000000 | Solution Execution |  | missing_token_length=4 |

## Action Count Preview

| Benchmark | Harness | Outcome | Action | Count |
|---|---|---|---|---:|
| AIMEPass4 | DirectLLM | failure | Answer Delivery | 10 |
| AIMEPass4 | DirectLLM | failure | Plan Formation | 10 |
| AIMEPass4 | DirectLLM | failure | Result Auditing | 32 |
| AIMEPass4 | DirectLLM | failure | Solution Execution | 207 |
| AIMEPass4 | DirectLLM | success | Answer Delivery | 112 |
| AIMEPass4 | DirectLLM | success | Plan Formation | 49 |
| AIMEPass4 | DirectLLM | success | Problem Framing | 6 |
| AIMEPass4 | DirectLLM | success | Result Auditing | 8 |
| AIMEPass4 | DirectLLM | success | Solution Execution | 1226 |
| AIMEPass4 | OpenClaw | failure | Answer Delivery | 2 |
| AIMEPass4 | OpenClaw | failure | Plan Formation | 3 |
| AIMEPass4 | OpenClaw | failure | Solution Execution | 7 |
| AIMEPass4 | OpenClaw | failure | Tool Grounding | 19 |
| AIMEPass4 | OpenClaw | success | Answer Delivery | 114 |
| AIMEPass4 | OpenClaw | success | Plan Formation | 52 |
| AIMEPass4 | OpenClaw | success | Problem Framing | 5 |
| AIMEPass4 | OpenClaw | success | Result Auditing | 3 |
| AIMEPass4 | OpenClaw | success | Solution Execution | 1076 |
| AIMEPass4 | OpenClaw | success | Tool Grounding | 134 |
| AIMEPass4 | OpenClaw | unknown | Answer Delivery | 1 |
| AIMEPass4 | OpenClaw | unknown | Solution Execution | 4 |
| AIMEPass4 | OpenClaw | unknown | Tool Grounding | 29 |
| AIMEPass4 | OpenCode | failure | Answer Delivery | 2 |
| AIMEPass4 | OpenCode | failure | Plan Formation | 7 |
| AIMEPass4 | OpenCode | failure | Solution Execution | 10 |
| AIMEPass4 | OpenCode | failure | Tool Grounding | 14 |
| AIMEPass4 | OpenCode | success | Answer Delivery | 232 |
| AIMEPass4 | OpenCode | success | Plan Formation | 142 |
| AIMEPass4 | OpenCode | success | Problem Framing | 14 |
| AIMEPass4 | OpenCode | success | Result Auditing | 12 |
| AIMEPass4 | OpenCode | success | Solution Execution | 1622 |
| AIMEPass4 | OpenCode | success | Tool Grounding | 133 |
| AIMEPass4 | OpenCode | unknown | Answer Delivery | 3 |
| AIMEPass4 | OpenCode | unknown | Plan Formation | 48 |
| AIMEPass4 | OpenCode | unknown | Problem Framing | 7 |
| AIMEPass4 | OpenCode | unknown | Result Auditing | 68 |
| AIMEPass4 | OpenCode | unknown | Solution Execution | 630 |
| AIMEPass4 | OpenCode | unknown | Tool Grounding | 78 |
| AIMEPass4 | ZeroClaw | failure | Answer Delivery | 3 |
| AIMEPass4 | ZeroClaw | failure | Plan Formation | 31 |
| AIMEPass4 | ZeroClaw | failure | Result Auditing | 28 |
| AIMEPass4 | ZeroClaw | failure | Solution Execution | 314 |
| AIMEPass4 | ZeroClaw | success | Answer Delivery | 116 |
| AIMEPass4 | ZeroClaw | success | Plan Formation | 103 |
| AIMEPass4 | ZeroClaw | success | Problem Framing | 4 |
| AIMEPass4 | ZeroClaw | success | Result Auditing | 16 |
| AIMEPass4 | ZeroClaw | success | Solution Execution | 1231 |
| GPQA | DirectLLM | failure | Answer Delivery | 28 |
| GPQA | DirectLLM | failure | Plan Formation | 22 |
| GPQA | DirectLLM | failure | Problem Framing | 18 |
| GPQA | DirectLLM | failure | Result Auditing | 7 |
| GPQA | DirectLLM | failure | Solution Execution | 175 |
| GPQA | DirectLLM | success | Answer Delivery | 165 |
| GPQA | DirectLLM | success | Plan Formation | 62 |
| GPQA | DirectLLM | success | Problem Framing | 64 |
| GPQA | DirectLLM | success | Result Auditing | 10 |
| GPQA | DirectLLM | success | Solution Execution | 1106 |
| GPQA | OpenClaw | failure | Answer Delivery | 27 |
| GPQA | OpenClaw | failure | Plan Formation | 13 |
| GPQA | OpenClaw | failure | Problem Framing | 9 |
| GPQA | OpenClaw | failure | Result Auditing | 11 |
| GPQA | OpenClaw | failure | Solution Execution | 165 |
| GPQA | OpenClaw | failure | Tool Grounding | 90 |
| GPQA | OpenClaw | success | Answer Delivery | 169 |
| GPQA | OpenClaw | success | Plan Formation | 70 |
| GPQA | OpenClaw | success | Problem Framing | 66 |
| GPQA | OpenClaw | success | Result Auditing | 15 |
| GPQA | OpenClaw | success | Solution Execution | 1032 |
| GPQA | OpenClaw | success | Tool Grounding | 131 |
| GPQA | OpenClaw | unknown | Answer Delivery | 2 |
| GPQA | OpenClaw | unknown | Solution Execution | 10 |
| GPQA | OpenClaw | unknown | Tool Grounding | 9 |

## Entropy and Token Preview

| Benchmark | Harness | Outcome | Metric | N | Missing | Mean | Median | P75 |
|---|---|---|---|---:|---:|---:|---:|---:|
| AIMEPass4 | DirectLLM | failure | mean_entropy | 9 | 0 | 0.151431 | 0.153696 | 0.158271 |
| AIMEPass4 | DirectLLM | failure | n_tokens | 9 | 0 | 15108.777778 | 14980.000000 | 15224.000000 |
| AIMEPass4 | DirectLLM | failure | action_event_count | 9 | 0 | 28.777778 | 12.000000 | 23.000000 |
| AIMEPass4 | DirectLLM | failure | tool_call_count | 9 | 0 | 0.000000 | 0.000000 | 0.000000 |
| AIMEPass4 | DirectLLM | success | mean_entropy | 111 | 0 | 0.130233 | 0.127144 | 0.147095 |
| AIMEPass4 | DirectLLM | success | n_tokens | 111 | 0 | 8036.189189 | 7987.000000 | 10111.000000 |
| AIMEPass4 | DirectLLM | success | action_event_count | 111 | 0 | 12.621622 | 8.000000 | 20.000000 |
| AIMEPass4 | DirectLLM | success | tool_call_count | 111 | 0 | 0.000000 | 0.000000 | 0.000000 |
| AIMEPass4 | OpenClaw | failure | mean_entropy | 3 | 0 | 0.189118 | 0.201954 | 0.209914 |
| AIMEPass4 | OpenClaw | failure | n_tokens | 3 | 0 | 14197.333333 | 14223.000000 | 17404.000000 |
| AIMEPass4 | OpenClaw | failure | action_event_count | 3 | 0 | 10.333333 | 12.000000 | 13.000000 |
| AIMEPass4 | OpenClaw | failure | tool_call_count | 3 | 0 | 6.333333 | 7.000000 | 7.500000 |
| AIMEPass4 | OpenClaw | success | mean_entropy | 114 | 0 | 0.134465 | 0.133136 | 0.160029 |
| AIMEPass4 | OpenClaw | success | n_tokens | 114 | 0 | 6826.412281 | 6086.000000 | 8678.750000 |
| AIMEPass4 | OpenClaw | success | action_event_count | 114 | 0 | 12.140351 | 9.000000 | 17.750000 |
| AIMEPass4 | OpenClaw | success | tool_call_count | 114 | 0 | 1.157895 | 0.000000 | 1.000000 |
| AIMEPass4 | OpenClaw | unknown | mean_entropy | 3 | 0 | 0.077200 | 0.018204 | 0.113861 |
| AIMEPass4 | OpenClaw | unknown | n_tokens | 3 | 0 | 98752.333333 | 133574.000000 | 137828.000000 |
| AIMEPass4 | OpenClaw | unknown | action_event_count | 3 | 0 | 11.333333 | 12.000000 | 14.000000 |
| AIMEPass4 | OpenClaw | unknown | tool_call_count | 3 | 0 | 9.666667 | 11.000000 | 11.500000 |
| AIMEPass4 | OpenCode | failure | mean_entropy | 3 | 0 | 0.064950 | 0.016729 | 0.096714 |
| AIMEPass4 | OpenCode | failure | n_tokens | 3 | 0 | 102858.666667 | 131070.000000 | 136573.500000 |
| AIMEPass4 | OpenCode | failure | action_event_count | 3 | 0 | 11.000000 | 10.000000 | 16.000000 |
| AIMEPass4 | OpenCode | failure | tool_call_count | 3 | 0 | 12.000000 | 12.000000 | 12.000000 |
| AIMEPass4 | OpenCode | success | mean_entropy | 116 | 0 | 0.119503 | 0.118570 | 0.139205 |
| AIMEPass4 | OpenCode | success | n_tokens | 116 | 0 | 5961.655172 | 4791.500000 | 7670.250000 |
| AIMEPass4 | OpenCode | success | action_event_count | 116 | 0 | 18.577586 | 12.000000 | 21.000000 |
| AIMEPass4 | OpenCode | success | tool_call_count | 116 | 0 | 1.043103 | 0.000000 | 2.000000 |
| AIMEPass4 | OpenCode | unknown | mean_entropy | 1 | 0 | 0.069775 | 0.069775 | 0.069775 |
| AIMEPass4 | OpenCode | unknown | n_tokens | 1 | 0 | 66365.000000 | 66365.000000 | 66365.000000 |
| AIMEPass4 | OpenCode | unknown | action_event_count | 1 | 0 | 834.000000 | 834.000000 | 834.000000 |
| AIMEPass4 | OpenCode | unknown | tool_call_count | 1 | 0 | 4.000000 | 4.000000 | 4.000000 |
| AIMEPass4 | ZeroClaw | failure | mean_entropy | 4 | 0 | 0.177618 | 0.187288 | 0.196803 |
| AIMEPass4 | ZeroClaw | failure | n_tokens | 4 | 0 | 48584.750000 | 48389.000000 | 63331.750000 |
| AIMEPass4 | ZeroClaw | failure | action_event_count | 4 | 0 | 94.000000 | 111.000000 | 122.000000 |
| AIMEPass4 | ZeroClaw | failure | tool_call_count | 4 | 0 | 0.000000 | 0.000000 | 0.000000 |
| AIMEPass4 | ZeroClaw | success | mean_entropy | 116 | 0 | 0.134205 | 0.133072 | 0.157387 |
| AIMEPass4 | ZeroClaw | success | n_tokens | 116 | 0 | 8376.896552 | 7255.500000 | 9558.250000 |
| AIMEPass4 | ZeroClaw | success | action_event_count | 116 | 0 | 12.672414 | 9.500000 | 15.250000 |
| AIMEPass4 | ZeroClaw | success | tool_call_count | 116 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | DirectLLM | failure | mean_entropy | 33 | 0 | 0.203862 | 0.217851 | 0.259348 |
| GPQA | DirectLLM | failure | n_tokens | 33 | 0 | 21797.606061 | 9816.000000 | 12523.000000 |
| GPQA | DirectLLM | failure | action_event_count | 33 | 0 | 7.575758 | 7.000000 | 9.000000 |
| GPQA | DirectLLM | failure | tool_call_count | 33 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | DirectLLM | success | mean_entropy | 165 | 0 | 0.200356 | 0.187894 | 0.249465 |
| GPQA | DirectLLM | success | n_tokens | 165 | 0 | 6042.672727 | 5375.000000 | 8227.000000 |
| GPQA | DirectLLM | success | action_event_count | 165 | 0 | 8.527273 | 8.000000 | 9.000000 |
| GPQA | DirectLLM | success | tool_call_count | 165 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | OpenClaw | failure | mean_entropy | 27 | 0 | 0.263712 | 0.243411 | 0.304444 |
| GPQA | OpenClaw | failure | n_tokens | 27 | 0 | 10124.888889 | 8194.000000 | 13799.500000 |
| GPQA | OpenClaw | failure | action_event_count | 27 | 0 | 11.666667 | 10.000000 | 14.000000 |
| GPQA | OpenClaw | failure | tool_call_count | 27 | 0 | 3.333333 | 2.000000 | 4.500000 |
| GPQA | OpenClaw | success | mean_entropy | 169 | 0 | 0.200916 | 0.192918 | 0.253056 |
| GPQA | OpenClaw | success | n_tokens | 169 | 0 | 4952.165680 | 4067.000000 | 6465.000000 |
| GPQA | OpenClaw | success | action_event_count | 169 | 0 | 8.775148 | 9.000000 | 10.000000 |
| GPQA | OpenClaw | success | tool_call_count | 169 | 0 | 0.775148 | 0.000000 | 1.000000 |
| GPQA | OpenClaw | unknown | mean_entropy | 2 | 0 | 0.265151 | 0.265151 | 0.269383 |
| GPQA | OpenClaw | unknown | n_tokens | 2 | 0 | 9040.500000 | 9040.500000 | 9054.250000 |
| GPQA | OpenClaw | unknown | action_event_count | 2 | 0 | 10.500000 | 10.500000 | 10.750000 |
| GPQA | OpenClaw | unknown | tool_call_count | 2 | 0 | 4.500000 | 4.500000 | 4.750000 |
| GPQA | OpenCode | failure | mean_entropy | 24 | 0 | 0.229143 | 0.239743 | 0.297768 |
| GPQA | OpenCode | failure | n_tokens | 24 | 0 | 18838.708333 | 10023.000000 | 16492.500000 |
| GPQA | OpenCode | failure | action_event_count | 24 | 0 | 20.875000 | 14.500000 | 19.250000 |
| GPQA | OpenCode | failure | tool_call_count | 24 | 0 | 2.041667 | 1.000000 | 2.250000 |
| GPQA | OpenCode | success | mean_entropy | 174 | 0 | 0.186132 | 0.188388 | 0.229918 |
| GPQA | OpenCode | success | n_tokens | 174 | 0 | 4524.339080 | 2950.500000 | 5337.000000 |
| GPQA | OpenCode | success | action_event_count | 174 | 0 | 12.293103 | 12.000000 | 15.000000 |
| GPQA | OpenCode | success | tool_call_count | 174 | 0 | 0.833333 | 1.000000 | 1.000000 |
| GPQA | ZeroClaw | failure | mean_entropy | 27 | 0 | 0.225302 | 0.248797 | 0.280674 |
| GPQA | ZeroClaw | failure | n_tokens | 27 | 0 | 33595.481481 | 11980.000000 | 18854.000000 |
| GPQA | ZeroClaw | failure | action_event_count | 27 | 0 | 12.814815 | 11.000000 | 17.500000 |
| GPQA | ZeroClaw | failure | tool_call_count | 27 | 0 | 0.000000 | 0.000000 | 0.000000 |
| GPQA | ZeroClaw | success | mean_entropy | 171 | 0 | 0.189576 | 0.185955 | 0.237403 |
| GPQA | ZeroClaw | success | n_tokens | 171 | 0 | 5562.573099 | 3933.000000 | 6843.000000 |
| GPQA | ZeroClaw | success | action_event_count | 171 | 0 | 9.976608 | 9.000000 | 12.000000 |
| GPQA | ZeroClaw | success | tool_call_count | 171 | 0 | 0.000000 | 0.000000 | 0.000000 |
| HLE | DirectLLM | failure | mean_entropy | 426 | 0 | 0.255880 | 0.270835 | 0.349950 |
| HLE | DirectLLM | failure | n_tokens | 426 | 0 | 13579.704225 | 7078.500000 | 11721.750000 |
| HLE | DirectLLM | failure | action_event_count | 426 | 0 | 14.737089 | 7.000000 | 9.000000 |
| HLE | DirectLLM | failure | tool_call_count | 426 | 0 | 0.000000 | 0.000000 | 0.000000 |
| HLE | DirectLLM | success | mean_entropy | 165 | 0 | 0.256087 | 0.234120 | 0.322155 |
| HLE | DirectLLM | success | n_tokens | 165 | 0 | 7786.412121 | 7693.000000 | 10209.000000 |
| HLE | DirectLLM | success | action_event_count | 165 | 0 | 8.036364 | 7.000000 | 8.000000 |
| HLE | DirectLLM | success | tool_call_count | 165 | 0 | 0.000000 | 0.000000 | 0.000000 |
| HLE | OpenClaw | failure | mean_entropy | 448 | 0 | 0.245631 | 0.270490 | 0.345245 |
| HLE | OpenClaw | failure | n_tokens | 448 | 0 | 19885.334821 | 5789.500000 | 12632.250000 |
| HLE | OpenClaw | failure | action_event_count | 448 | 0 | 10.314732 | 8.000000 | 13.000000 |
| HLE | OpenClaw | failure | tool_call_count | 448 | 0 | 4.055804 | 1.000000 | 6.000000 |
| HLE | OpenClaw | success | mean_entropy | 143 | 0 | 0.252606 | 0.254262 | 0.310905 |
| HLE | OpenClaw | success | n_tokens | 143 | 0 | 7112.307692 | 6416.000000 | 8915.500000 |
| HLE | OpenClaw | success | action_event_count | 143 | 0 | 9.573427 | 8.000000 | 11.000000 |
| HLE | OpenClaw | success | tool_call_count | 143 | 0 | 2.321678 | 0.000000 | 2.000000 |
| HLE | OpenCode | failure | mean_entropy | 449 | 0 | 0.248476 | 0.259673 | 0.322098 |
| HLE | OpenCode | failure | n_tokens | 449 | 0 | 12983.768374 | 6612.000000 | 11752.000000 |
| HLE | OpenCode | failure | action_event_count | 449 | 0 | 147.951002 | 14.000000 | 24.000000 |
| HLE | OpenCode | failure | tool_call_count | 449 | 0 | 11.527840 | 2.000000 | 5.000000 |
| HLE | OpenCode | success | mean_entropy | 142 | 0 | 0.233434 | 0.230098 | 0.292839 |
| HLE | OpenCode | success | n_tokens | 142 | 0 | 8532.725352 | 6860.500000 | 11130.750000 |
| HLE | OpenCode | success | action_event_count | 142 | 0 | 18.471831 | 15.500000 | 21.000000 |
| HLE | OpenCode | success | tool_call_count | 142 | 0 | 4.147887 | 1.000000 | 3.750000 |
| HLE | ZeroClaw | failure | mean_entropy | 419 | 0 | 0.269975 | 0.282835 | 0.341518 |
| HLE | ZeroClaw | failure | n_tokens | 415 | 4 | 10978.607229 | 5823.000000 | 11718.000000 |
| HLE | ZeroClaw | failure | action_event_count | 419 | 0 | 20.510740 | 9.000000 | 13.000000 |
| HLE | ZeroClaw | failure | tool_call_count | 419 | 0 | 0.000000 | 0.000000 | 0.000000 |
| HLE | ZeroClaw | success | mean_entropy | 172 | 0 | 0.255168 | 0.246933 | 0.318066 |
| HLE | ZeroClaw | success | n_tokens | 172 | 0 | 9265.825581 | 6954.000000 | 12711.750000 |
| HLE | ZeroClaw | success | action_event_count | 172 | 0 | 10.639535 | 9.000000 | 13.000000 |
| HLE | ZeroClaw | success | tool_call_count | 172 | 0 | 0.000000 | 0.000000 | 0.000000 |

## Tool Count Preview

| Benchmark | Harness | Outcome | Tool type | Count |
|---|---|---|---|---:|
| AIMEPass4 | OpenClaw | failure | bash | 3 |
| AIMEPass4 | OpenClaw | failure | edit | 2 |
| AIMEPass4 | OpenClaw | failure | python | 6 |
| AIMEPass4 | OpenClaw | failure | search | 1 |
| AIMEPass4 | OpenClaw | failure | write | 7 |
| AIMEPass4 | OpenClaw | success | bash | 14 |
| AIMEPass4 | OpenClaw | success | edit | 9 |
| AIMEPass4 | OpenClaw | success | python | 81 |
| AIMEPass4 | OpenClaw | success | tool | 2 |
| AIMEPass4 | OpenClaw | success | write | 26 |
| AIMEPass4 | OpenClaw | unknown | bash | 5 |
| AIMEPass4 | OpenClaw | unknown | edit | 2 |
| AIMEPass4 | OpenClaw | unknown | python | 10 |
| AIMEPass4 | OpenClaw | unknown | search | 1 |
| AIMEPass4 | OpenClaw | unknown | tool | 2 |
| AIMEPass4 | OpenClaw | unknown | write | 9 |
| AIMEPass4 | OpenCode | failure | bash | 18 |
| AIMEPass4 | OpenCode | failure | python | 18 |
| AIMEPass4 | OpenCode | success | bash | 10 |
| AIMEPass4 | OpenCode | success | python | 90 |
| AIMEPass4 | OpenCode | success | tool | 17 |
| AIMEPass4 | OpenCode | success | write | 4 |
| AIMEPass4 | OpenCode | unknown | python | 4 |
| GPQA | OpenClaw | failure | bash | 37 |
| GPQA | OpenClaw | failure | python | 16 |
| GPQA | OpenClaw | failure | search | 14 |
| GPQA | OpenClaw | failure | tool | 19 |
| GPQA | OpenClaw | failure | web_fetch | 4 |
| GPQA | OpenClaw | success | bash | 38 |
| GPQA | OpenClaw | success | python | 78 |
| GPQA | OpenClaw | success | search | 6 |
| GPQA | OpenClaw | success | tool | 4 |
| GPQA | OpenClaw | success | web_fetch | 5 |
| GPQA | OpenClaw | unknown | bash | 3 |
| GPQA | OpenClaw | unknown | python | 2 |
| GPQA | OpenClaw | unknown | read | 1 |
| GPQA | OpenClaw | unknown | tool | 3 |
| GPQA | OpenCode | failure | bash | 3 |
| GPQA | OpenCode | failure | python | 17 |
| GPQA | OpenCode | failure | search | 4 |
| GPQA | OpenCode | failure | task | 1 |
| GPQA | OpenCode | failure | web_fetch | 24 |
| GPQA | OpenCode | success | bash | 15 |
| GPQA | OpenCode | success | python | 91 |
| GPQA | OpenCode | success | read | 1 |
| GPQA | OpenCode | success | search | 7 |
| GPQA | OpenCode | success | task | 3 |
| GPQA | OpenCode | success | web_fetch | 28 |
| HLE | OpenClaw | failure | bash | 718 |
| HLE | OpenClaw | failure | edit | 17 |
| HLE | OpenClaw | failure | python | 215 |
| HLE | OpenClaw | failure | read | 7 |
| HLE | OpenClaw | failure | search | 189 |
| HLE | OpenClaw | failure | tool | 472 |
| HLE | OpenClaw | failure | web_fetch | 161 |
| HLE | OpenClaw | failure | write | 38 |
| HLE | OpenClaw | success | bash | 125 |
| HLE | OpenClaw | success | edit | 3 |
| HLE | OpenClaw | success | python | 80 |
| HLE | OpenClaw | success | search | 31 |
| HLE | OpenClaw | success | tool | 57 |
| HLE | OpenClaw | success | web_fetch | 30 |
| HLE | OpenClaw | success | write | 6 |
| HLE | OpenCode | failure | bash | 359 |
| HLE | OpenCode | failure | edit | 2 |
| HLE | OpenCode | failure | glob | 1 |
| HLE | OpenCode | failure | invalid | 1 |
| HLE | OpenCode | failure | python | 411 |
| HLE | OpenCode | failure | read | 9 |
| HLE | OpenCode | failure | search | 73 |
| HLE | OpenCode | failure | task | 27 |
| HLE | OpenCode | failure | web_fetch | 4268 |
| HLE | OpenCode | failure | write | 25 |
| HLE | OpenCode | success | bash | 114 |
| HLE | OpenCode | success | python | 170 |
| HLE | OpenCode | success | read | 2 |
| HLE | OpenCode | success | search | 19 |
| HLE | OpenCode | success | task | 4 |
| HLE | OpenCode | success | web_fetch | 275 |
| HLE | OpenCode | success | write | 5 |

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
