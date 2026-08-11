# analyze_tools — GPQA LogProb / Entropy 分析工具

本目录包含对 OpenClaw 和 OpenCode 两个 harness 在 GPQA-Diamond 上运行结果的
LogProb / Entropy 分析脚本与图表，对话分析结论完全可复现。

## 目录结构

```
analyze_tools/
├── README.md                   本文件
├── extract_data.py             从 results/ 提取数据 → data/ CSV
├── plot_figures.py             读取 data/ CSV，生成学术风格图表 → figures/
├── degradation_diagnostics.py  DirectLLM vs harness paired regression diagnostics
├── data/                       中间数据（由 extract_data.py 生成）
│   ├── entropy_calibration.csv         entropy bin vs accuracy
│   ├── token_count_by_outcome.csv      OpenClaw 每题 token 数
│   ├── tool_boundary_profile.csv       tool call 边界 ±15 token entropy
│   ├── baseline_vs_posttool.csv        turn-level baseline vs post-tool entropy
│   ├── openclaw_entropy_by_outcome.csv OpenClaw per-task entropy + token 统计
│   ├── entropy_token_quadrants.csv     low-entropy × long-output collapse
│   ├── confidence_inversion.csv        entropy threshold vs wrong-rate inversion
│   ├── posttool_state_shift.csv        post-tool entropy separation / boundary shock
│   ├── operational_tax_adjusted_accuracy.csv valid-scored vs deployable accuracy
│   ├── paired_net_gain.csv             harness rescue/regression vs DirectLLM
│   ├── action_space_distance.csv       canonical action distribution distance
│   ├── verification_conversion.csv     verify-before-answer and post-verify change
│   ├── degradation_task_features.csv   paired task-level degradation features
│   ├── degradation_summary.csv         harness/outcome-level degradation summary
│   ├── degradation_cause_buckets.csv   cause-bucket counts for regressions/errors
│   ├── degradation_summary.json        machine-readable degradation report
│   └── measurement_summary.json        all derived measurement tables
└── figures/                    输出图表（PDF，300 dpi）
    ├── fig1_entropy_calibration.pdf
    ├── fig2_openclaw_tokens.pdf
    ├── fig3_tool_boundary_profile.pdf
    ├── fig4_baseline_vs_posttool.pdf
    └── fig5_openclaw_entropy_scatter.pdf
```

## 快速复现

```bash
# 1. 安装依赖（仅需一次）
pip install matplotlib scipy numpy

# 2. 提取数据（读取 results/，约 2 分钟）
python3 analyze_tools/extract_data.py

# 3. 挖掘通用 measurement / insight 表
python3 analyze_tools/mine_measurements.py

# 4. 诊断 DirectLLM 正确但 harness 退化的任务
python3 analyze_tools/degradation_diagnostics.py

# 5. 生成所有图表
python3 analyze_tools/plot_figures.py
```

## 依赖

- Python ≥ 3.10
- matplotlib ≥ 3.9、scipy ≥ 1.10、numpy ≥ 1.24
- `ALPHADIANA_ACADEMIC_PLOT_DIR` — optional path to the academic-plot skill scripts; set it when `academic_plot` is not installed in the active environment.
- `ALPHADIANA_RESULTS_DIR` — root containing the named result directories consumed by extraction and analysis scripts; defaults to `<repo>/results`.
- `results/full_gpqa_v2_openclaw_qwen35_27b_logprobs/` — OpenClaw 结果目录
- `results/full_gpqa_v2_opencode_qwen35_27b_logprobs/` — OpenCode 结果目录

## 各图说明

| 图 | 文件 | 说明 |
|----|------|------|
| Fig 1 | `fig1_entropy_calibration.pdf` | Entropy 分箱准确率，展示"entropy<0.1 → acc≈8%"的 OpenClaw 过度自信现象 |
| Fig 2 | `fig2_openclaw_tokens.pdf` | OpenClaw 答对/错 token 数箱线图 + CDF，展示答错时 18× token 膨胀 |
| Fig 3 | `fig3_tool_boundary_profile.pdf` | Tool call 边界 ±15 token entropy profile，正确答案收敛，错误答案振荡 |
| Fig 4 | `fig4_baseline_vs_posttool.pdf` | Baseline（turn 0）vs post-tool entropy；baseline 无区分力，tool result 后分叉 |
| Fig 5 | `fig5_openclaw_entropy_scatter.pdf` | OpenClaw 每题 token 数 × mean entropy 散点，"confident wrong"区域可视化 |

## 核心结论速查

- **OpenClaw** entropy < 0.1 → acc ≈ 8%（"confident wrong"失败模式）
- **OpenClaw** 答错时 token 中位数 = 30,053；答对时 = 1,635（18× 差距）
- **OpenCode** tool call 边界 offset=0 entropy 峰值：答对 0.668，答错 0.882
- **Baseline 无预测力**（答对 0.312 vs 答错 0.302）；tool result 后首次分叉（Δ=+0.187）

## 通用 measurement 报告

见 `MEASUREMENT_INSIGHTS.md`。新增脚本 `mine_measurements.py` 将现有 GPQA / Qwen3.5
结果转成更通用的 agent-eval measurement：

- `LowEntropyLongCollapseRate`：低 entropy + 长输出是否变成高风险坍缩区
- `PostToolEntropySeparation`：tool result 后正确/错误轨迹是否分叉
- `VerificationConversionRate`：verify 是否发生在 answer 前并触发后续 action change
- `ScaffoldDominance`：不同 harness 的 canonical action distribution JSD
- `OperationalTaxAdjustedAccuracy`：valid-scored 行为准确率与 deployable accuracy 分离
- `PairedNetGain`：相对 DirectLLM 的 rescue/regression 四格

## Degradation diagnostics

`degradation_diagnostics.py` answers the narrower root-cause question:
when a harness performs worse than DirectLLM on the same GPQA task, which
observable factors changed?

It writes:

- `data/degradation_task_features.csv` — one row per `(harness, task_id)` pair with
  paired outcome, cause bucket, answer-format flags, action counts, tool/verify
  conversion flags, language-loop markers, token/entropy deltas, and logprob-sidecar
  summaries.
- `data/degradation_summary.csv` — aggregate rates by harness and paired outcome.
- `data/degradation_cause_buckets.csv` — counts of operational errors,
  answer-format/extraction failures, long-low-entropy overruns, verification without
  conversion, tool-use-not-integrated, planning/recovery churn, and valid answer
  changes.
- `data/degradation_summary.json` — machine-readable report with run IDs and summary
  tables.

The tool reads current task JSONs as task sample lists and uses the first sample
record for `num_samples=1` runs. It refreshes action events from persisted artifacts
instead of trusting previously generated Phase 14 CSVs.

## AIME k=32 timeout and case analysis

`aime_k32_analysis.py` analyzes local AIME 2026 Qwen3.5 k=32 full-run artifacts
from persisted `tasks/*.json` sample lists. It is separate from the GPQA tools
above because the AIME runs use repeated samples and the key question is whether
high pass@32 hides timeout-scored-zero samples, and how each harness changes the
failure mode relative to DirectLLM.

```bash
python3 analyze_tools/aime_k32_analysis.py \
  --direct-run-dir <direct-llm-aime-run-dir> \
  --stdout
```

It writes:

- `data/aime_k32_timeout_summary.csv` — one row per harness with observed
  samples, correct samples, ordinary wrong samples, timeout-scored-zero samples,
  non-valid/missing samples, Avg@32, and Pass@32.
- `data/aime_k32_timeout_by_task.csv` — per-task timeout and pass distribution.
- `data/aime_k32_outcome_failure_modes.csv` — outcome-level medians for token
  count, entropy, wall time, trajectory/tool counts, timeout seconds, and
  harness-specific timeout metadata flags.
- `data/aime_k32_paired_task_summary.csv` — one row per AIME task comparing
  DirectLLM/OpenCode/OpenClaw/ZeroClaw correct, ordinary-wrong, timeout,
  nonvalid, and missing-sample counts.
- `data/aime_k32_case_studies.csv` and `data/aime_k32_case_studies.md` —
  representative high-leverage tasks such as universal failure, direct-easy
  harness timeout collapse, and OpenClaw pass-loss cases.
- `data/aime_k32_timeout_summary.json` — machine-readable copy of all generated
  tables and case-study digests.

For deeper qualitative case-packet analysis, use:

```bash
python3 analyze_tools/aime_case_packets.py \
  --direct-run-dir <direct-llm-aime-run-dir> \
  --cases aime_28 aime_18 aime_14 aime_30 aime_8 aime_15
```

This writes `data/aime_case_packets.json`, a sanitized packet file with compact
problem text, per-harness counts, representative sample metadata, trajectory
heads, and truncated raw-output excerpts. `mimo_case_review.py` can optionally
send those packets to an OpenAI-compatible endpoint; it reads the API key from
`TOKEN_PLAN_API_KEY` or hidden stdin and never writes the key to outputs.

For the rollout-distribution view used by the current AIME report:

```bash
python3 analyze_tools/aime_rollout_distribution.py \
  --direct-run-dir <direct-llm-aime-run-dir> \
  --stdout

python3 analyze_tools/aime_rollout_audit.py \
  --direct-run-dir <direct-llm-aime-run-dir> \
  --stdout
```

It writes:

- `data/aime_rollout_sample_features.csv` — one row per rollout with outcome,
  wall-time band, token-count cap flags, token entropy, trajectory/tool markers,
  final-answer markers, and compact predicted values.
- `data/aime_rollout_harness_distribution.csv` — aggregate distributions for
  DirectLLM, OpenCode, OpenClaw, and ZeroClaw.
- `data/aime_rollout_task_distribution.csv` — one row per `(harness, task_id)`
  with correct/wrong/timeout counts, outcome entropy, long-tail counts, and
  token/entropy quantiles.
- `data/aime_rollout_vs_direct_distribution.csv` — paired task deltas versus
  DirectLLM, including task tags such as `wall_longtail`, `token_cap_cluster`,
  `low_entropy_long_cluster`, `pass_masked_timeout`, and `rescue`.
- `data/aime_rollout_longtail_samples.csv` — rollout rows selected by long wall
  time, token cap, or low-entropy-long criteria.
- `data/aime_rollout_distribution_summary.json` — machine-readable summary.
- `data/aime_rollout_audit.csv` — every `(harness, task_id, sample_index)`
  rollout with outcome, local pattern label, entropy, finalization markers,
  tool/runtime markers, and compact trajectory/output signals.
- `data/aime_rollout_audit_all.md` — a human-checkable per-task table listing
  all 32 rollouts for DirectLLM, OpenCode, OpenClaw, and ZeroClaw.
- `data/aime_rollout_audit_*_summary.csv` — harness, task, and pattern summary
  tables derived from the per-rollout audit.

The current Chinese analysis draft is
`AIME_OPENCODE_ZEROCLAW_DEEP_REPORT.md`; despite the historical filename, it now
includes DirectLLM, OpenCode, OpenClaw, and ZeroClaw.

## AIME Appendix E publication figures

`aime_appendix_e_figures.py` consumes the rollout feature/audit CSVs above and
generates the formal Appendix E figures. The script intentionally conditions
the first four analyses on answer-bearing rollouts (`correct` and
`ordinary_wrong`) so the paper section focuses on reasoning-pattern differences:
entropy-length interaction, stable wrong consensus, tool engagement versus
final-answer conversion, repeated self-check churn, and item-level
rescue/regression.

```bash
python3 analyze_tools/aime_appendix_e_figures.py
```

It writes:

- `data/appendix_e/*.csv` and `data/appendix_e/appendix_e_summary.json` —
  source tables for the Appendix E claims.
- `figures/macro_analyze/figE1_entropy_length_quadrants.pdf`
- `figures/macro_analyze/figE2_outcome_entropy_accuracy.pdf`
- `figures/macro_analyze/figE3_tool_conversion.pdf`
- `figures/macro_analyze/figE4_repetition_churn.pdf`
- `figures/macro_analyze/figE5_rescue_regression_heatmap.pdf`

The rewritten Appendix E source used in the paper zip is mirrored at
`appendix_e_rewrite.tex`.

## AIME harness degradation deep dive

`aime_degradation_deep_dive.py` is the current analysis for the question "why
does a harness make AIME worse?"  It reads the AIME rollout audit plus per-task
JSON trajectories, extracts canonical action sequences for answer-bearing
rollouts, and combines them with 32-rollout diversity/failure-mode tables.  The
main output separates productive collapse, unproductive answer explosion, and
non-answer collapse.

```bash
python3 analyze_tools/aime_degradation_deep_dive.py \
  --direct-run-dir <direct-llm-aime-run-dir>
```

It writes:

- `data/aime_deep_dive/*.csv` and `data/aime_deep_dive/summary.json`,
  including `aime_verify_transition_by_task.csv`,
  `aime_trace_microcases.csv`, and
  `aime_zeroclaw_aime30_template_audit.csv`,
  `aime_openclaw_coverage_audit.csv`, and
  `aime_failure_mode_sensitivity.csv` for the task-level and sample-level
  evidence used by the Chinese report.
- `figures/macro_analyze/figE6_action_composition_by_outcome.{pdf,png}`
- `figures/macro_analyze/figE7_verify_transition_by_outcome.{pdf,png}`
- `figures/macro_analyze/figE8_verify_entropy_length.{pdf,png}`
- `figures/macro_analyze/figE9_topic_delta_heatmap.{pdf,png}`
- `figures/macro_analyze/figE10_rollout_diversity_by_topic.{pdf,png}`
- `figures/macro_analyze/figE11_representative_task_diversity.{pdf,png}`
- `figures/macro_analyze/figE12_failure_mode_taxonomy.{pdf,png}`
- `AIME_HARNESS_DEGRADATION_DEEP_DIVE_CN.md`
