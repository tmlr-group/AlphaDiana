# Reproduce Six-Action Statistics

Run from the repository root:

```bash
python analyze_tools/compute_six_action_statistics.py \
  --spec GPQA:DirectLLM:gpqa_directllm_phase9:results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs \
  --spec GPQA:OpenClaw:gpqa_openclaw_v2:results/full_gpqa_v2_openclaw_qwen35_27b_logprobs \
  --spec GPQA:OpenCode:gpqa_opencode_v2:results/full_gpqa_v2_opencode_qwen35_27b_logprobs \
  --spec GPQA:ZeroClaw:gpqa_zeroclaw_v2:results/full_gpqa_v2_zeroclaw_qwen35_27b_logprobs \
  --spec HLE:DirectLLM:hle_directllm_hf_20260423:results/hf-alphadiana-benchmark-results/full_run/20260423-hle-directllm-qwen35_27b-v01/results/20260423-hle-directllm-qwen35_27b-v01 \
  --spec HLE:OpenClaw:hle_openclaw_merged:results/quick_260430_hle_openclaw_qwen35_27b_merged \
  --spec HLE:OpenCode:hle_opencode_20260426:/path/to/xxx/alphadiana-results/20260426-hle-opencode-qwen35_27b-v01 \
  --spec HLE:ZeroClaw:hle_zeroclaw_20260426:/path/to/xxx/alphadiana-results/20260426-hle-zeroclaw-qwen35_27b-v01 \
  --spec AIMEPass4:DirectLLM:aime_pass4_directllm:/path/to/xxx/alphadiana_results/full_20260423_qwen35_27b_aime2026_directllm_r1_pass4 \
  --spec AIMEPass4:OpenClaw:aime_pass4_openclaw:/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_openclaw_qwen35_27b_pass4_t9300_from_20260428 \
  --spec AIMEPass4:OpenCode:aime_pass4_opencode:/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_opencode_qwen35_27b_pass4_t9300_from_20260425 \
  --spec AIMEPass4:ZeroClaw:aime_pass4_zeroclaw:/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_zeroclaw_qwen35_27b_pass4_t9300_from_20260428
```

The script writes outputs to `analyze_tools/data/six_action_statistics/` by default.
