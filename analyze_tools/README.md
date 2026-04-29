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
- `/home/xxx/academic-plot/scripts/` — academic-plot skill（plot_figures.py 自动加入 sys.path）
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
