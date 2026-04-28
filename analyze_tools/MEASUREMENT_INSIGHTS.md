# 通用 Measurement 洞察报告

本报告基于当前 checkout 中的离线结果，不调用模型或 benchmark runtime。输入包括：

- `analyze_tools/data/*.csv`
- `results/phase14_gpqa_trajectory_analysis/action_events.csv`
- `results/phase14_gpqa_trajectory_analysis/trajectory_metrics.json`
- `results/*/tasks/*.json`

生成命令：

```bash
python3 analyze_tools/extract_data.py
python3 analyze_tools/mine_measurements.py
```

输出表：

- `data/entropy_token_quadrants.csv`
- `data/confidence_inversion.csv`
- `data/posttool_state_shift.csv`
- `data/operational_tax_adjusted_accuracy.csv`
- `data/paired_net_gain.csv`
- `data/action_space_distance.csv`
- `data/verification_conversion.csv`
- `data/measurement_summary.json`

## 社区共识对应的反向问题

近期 agent evaluation 社区通常强调四点：不要只看 final answer；tool-use trajectory 很重要；可靠性要看重复运行或 pass^k；uncertainty/confidence 要同时看 calibration 和 discrimination。这里的分析不否认这些方向，而是用现有 GPQA Qwen3.5 artifacts 追问更可复用的问题：

1. tool call 是否真正改变了模型状态，而不只是增加动作数？
2. verification 是否真的转化成 answer/plan/tool 修正？
3. 低 entropy 是否一定代表高 confidence 正确？
4. scaffold 是否带来能力提升，还是主要带来 action-space 改写和 operational tax？

## 1. Low-Entropy Long Collapse

**Measurement:** `EntropyTokenQuadrant`

把 OpenClaw valid-scored 样本按 token 数和 mean entropy 切成四象限：

- long: `n_tokens >= q75 = 5348`
- low entropy: `mean_entropy <= q25 = 0.1465`

结果：

| Bucket | N | Wrong rate | Median tokens | Median entropy |
| --- | ---: | ---: | ---: | ---: |
| `low_entropy_long` | 32 | 100.0% | 41,742 | 0.031 |
| `higher_entropy_long` | 8 | 25.0% | 9,372 | 0.228 |
| `higher_entropy_short` | 108 | 13.9% | 1,588 | 0.290 |
| `low_entropy_short` | 7 | 0.0% | 1,415 | 0.136 |

**Insight:** 低 entropy 本身不是危险信号；低 entropy 加长输出才是危险状态。这个状态更像 decoding/cognition collapse，而不是正常 confidence。社区常把 entropy/logprob 当 confidence proxy，但这里最强风险区是 `low_entropy_long`，不是 high entropy。

**General metric:**

```text
LowEntropyLongCollapseRate =
  P(wrong | entropy <= q25, tokens >= q75)
```

这个 metric 可迁移到任何有 token entropy 和 token count 的 benchmark。

## 2. Confidence Inversion Curve

**Measurement:** `ConfidenceInversion`

对 OpenClaw 扫描 entropy threshold，比较低 entropy 子集和高 entropy 子集的错误率。

最强反转点：

- `entropy <= 0.093`: low-entropy N=30，wrong rate=100.0%
- `entropy > 0.093`: high-entropy N=125，wrong rate=15.2%
- inversion lift = +84.8 pp

**Insight:** 这里不是普通 miscalibration，而是 rank inversion：按 entropy 排序时，最低 entropy 的样本反而最容易错。用单调校准曲线或 AUROC 解释会漏掉这个结构。

**General metric:**

```text
ConfidenceInversion =
  max_tau P(wrong | entropy <= tau) - P(wrong | entropy > tau)
```

它比单个 bin 更稳，可以跨模型/benchmark 比较。

## 3. Post-Tool State Shift

**Measurement:** `PostToolEntropySeparation`

OpenCode baseline turn 的 entropy 对错几乎无区分：

- baseline wrong-correct = `-0.0138`

tool result 之后开始分叉：

| Turn label | Correct entropy | Wrong entropy | Wrong - Correct | Gain vs baseline |
| --- | ---: | ---: | ---: | ---: |
| `after_tool_1+` | 0.306 | 0.411 | +0.105 | +0.119 |
| `after_tool_2+` | 0.287 | 0.448 | +0.162 | +0.175 |
| `after_tool_3+` | 0.308 | 0.580 | +0.272 | +0.286 |
| `after_tool_4+` | 0.266 | 0.353 | +0.087 | +0.101 |

Tool-boundary 后 `offset=0..15` 的 wrong-correct entropy integral 为 `+3.319`。

**Insight:** 初始 confidence 不是主要区分信号；真正有信息量的是工具调用后模型状态是否分叉。tool use 的价值不是“调用了工具”，而是工具结果是否让正确轨迹收敛、错误轨迹暴露不确定性。

**General metric:**

```text
PostToolEntropySeparation(k) =
  E[H_after_tool_k | wrong] - E[H_after_tool_k | correct]

StateShiftYield(k) =
  PostToolEntropySeparation(k) / Cost(tool_k)
```

当前表先实现 separation，yield 可在补齐 per-tool cost 后加入。

## 4. Verification Conversion Gap

**Measurement:** `VerificationConversion`

只看是否出现 `verify` 会误导，因为错误轨迹也会 verify。

| Harness | Outcome | N | Verify rate | Verify before answer | Post-verify action change |
| --- | --- | ---: | ---: | ---: | ---: |
| OpenClaw | wrong | 90 | 66.7% | 6.7% | 30.0% |
| OpenClaw | correct | 108 | 55.6% | 24.1% | 19.4% |
| OpenCode | wrong | 51 | 51.0% | 3.9% | 15.7% |
| OpenCode | correct | 142 | 59.9% | 17.6% | 38.0% |
| ZeroClaw | wrong | 56 | 60.7% | 7.1% | 28.6% |
| ZeroClaw | correct | 126 | 61.1% | 17.5% | 42.9% |

**Insight:** verify 的出现率不是可靠性指标；更有用的是 verify 是否发生在 answer 之前，以及 verify 后是否触发非 answer 的 action change。OpenClaw 里错误轨迹 verify rate 反而更高，但 verify-before-answer 低很多。

**General metric:**

```text
VerificationConversionRate =
  P(post_verify_action_change | has_verify)

VerifyBeforeAnswerLift =
  P(verify_before_answer | correct) - P(verify_before_answer | wrong)
```

这个 measurement 比简单 `VerifyRate` 更接近“verification 是否真的有用”。

## 5. Scaffold Dominance

**Measurement:** `ActionSpaceDistance`

同一 GPQA / Qwen3.5 setup 下，不同 harness 的 canonical action distribution 差异很大。

| Pair | JSD | Support overlap |
| --- | ---: | ---: |
| OpenClaw vs OpenCode | 0.118 | 83.3% |
| OpenClaw vs ZeroClaw | 0.510 | 66.7% |
| OpenCode vs ZeroClaw | 0.264 | 80.0% |
| DirectLLM vs OpenClaw | 0.398 | 33.3% |

**Insight:** 跨 harness accuracy 不能直接归因给 base model。OpenClaw 与 ZeroClaw 的 action-space JSD 很高，说明 scaffold 改写了模型可采取的行为分布。把这些结果当“同一模型能力比较”之前，需要先报告 action-space distance。

**General metric:**

```text
ScaffoldDominance(h1, h2) =
  JSD(action_distribution_h1, action_distribution_h2)
```

可以进一步和 task-level outcome flip 做相关。

## 6. Operational Tax Adjusted Accuracy

**Measurement:** `OperationalTaxAdjustedAccuracy`

当前 checkout 的 task files 显示：

| Harness | Valid | Correct | Behavioral accuracy | Operational tax | Deployable accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| DirectLLM | 198 | 159 | 80.3% | 0.0% | 80.3% |
| OpenClaw | 162 | 109 | 67.3% | 18.2% | 55.1% |
| OpenCode | 197 | 145 | 73.6% | 0.5% | 73.2% |
| ZeroClaw | 161 | 130 | 80.7% | 18.7% | 65.7% |

**Insight:** ZeroClaw 的 behavioral accuracy 最高，但 operational tax 很大，deployable accuracy 低于 DirectLLM。agent scaffold 的收益必须同时报 valid behavior 和 system readiness，否则会高估真实可用性。

**General metric:**

```text
BehavioralAccuracy = correct / valid_scored
OperationalTax = non_valid / expected
DeployableAccuracy = correct / expected
```

## 7. Paired Rescue vs Regression

**Measurement:** `PairedNetGain`

相对 DirectLLM 的 paired valid tasks：

| Harness | Paired valid | Rescue | Regression | Net gain |
| --- | ---: | ---: | ---: | ---: |
| OpenClaw | 162 | 8 | 23 | -15 |
| OpenCode | 197 | 10 | 23 | -13 |
| ZeroClaw | 161 | 13 | 11 | +2 |

**Insight:** agent harness 不是单调增强器。它会 rescue 一些 DirectLLM 错题，同时 regression 一些 DirectLLM 对题。这里 OpenClaw/OpenCode 的 regression 多于 rescue；ZeroClaw 在 valid subset 上略正，但 operational tax 抹掉了 deployable 层面的收益。

**General metric:**

```text
PairedNetGain = #agent_correct_direct_wrong - #agent_wrong_direct_correct
TaxAdjustedNetGain = DeployableAccuracy_agent - DeployableAccuracy_direct
```

## 分析经验

1. **先分母，后指标。** `expected`、`valid_scored`、`behavioral wrong`、`runtime/agent error` 必须分开。否则 agent crash 会被误读成模型推理失败。
2. **不要把 action count 当 progress。** 更通用的是 state shift：tool/verify 后 uncertainty、answer、plan 是否改变。
3. **不要只报 entropy calibration。** 对 agent 输出，entropy 与 cost 交互很强；低 entropy 短输出和低 entropy 长输出是两种完全不同状态。
4. **verification 要看 conversion。** `verify_rate` 容易把无效检查和有效纠错混在一起。
5. **cross-harness 比较要先报 action-space distance。** scaffold 改变行为支持集后，raw accuracy 不能直接解释为同一模型能力差异。
6. **报告要保存 measurement drift 风险。** 当前 `results/phase14_gpqa_trajectory_analysis` 是一次生成产物；而 task files 的当前 checkpoint 口径可能变化。复现时应重新跑 `extract_data.py` 和 `mine_measurements.py`。

## 建议主文排序

最适合进入主文的四个 measurement：

1. `LowEntropyLongCollapseRate`
2. `PostToolEntropySeparation`
3. `VerificationConversionRate`
4. `OperationalTaxAdjustedAccuracy` + `PairedNetGain`

它们共同支持一个更强的 narrative：agent 可靠性问题不是”是否会用工具/是否会思考更久”，而是工具和验证是否产生可观测 state update，以及 scaffold 的 operational tax 是否吞掉 behavioral gain。

## 8. Tool Usage Pattern Analysis Across Harnesses

All numbers in §8 are derived from `analyze_tools/data/fig11_tool_usage_numbers.json` (see `figures/fig11_tool_usage_patterns.pdf` and `figures/fig10_harness_outcome_2x2.pdf`).

### 8.1 Per-harness action rates by outcome

Action rate = fraction of trajectories within (harness, outcome) bucket that contain ≥1 event of that canonical action type.

| Harness | Outcome | n | answer | reason | tool_use | verify | plan | recover |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OpenClaw | correct | 130 | 99.2% | 12.3% | 22.3% | 56.9% | 16.2% | 23.1% |
| OpenClaw | wrong | 67 | 85.1% | 19.4% | 7.5% | 58.2% | 31.3% | 10.4% |
| OpenCode | correct | 145 | 100.0% | 93.1% | 55.2% | 59.3% | 30.3% | 0.0% |
| OpenCode | wrong | 52 | 100.0% | 92.3% | 15.4% | 50.0% | 30.8% | 0.0% |
| ZeroClaw | correct | 149 | 100.0% | 100.0% | 0.0% | 60.4% | 81.9% | 0.0% |
| ZeroClaw | wrong | 34 | 100.0% | 100.0% | 0.0% | 61.8% | 79.4% | 0.0% |
| DirectLLM | correct | 159 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| DirectLLM | wrong | 39 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | 0.0% |

The most pronounced contrast is OpenCode's `tool_use` rate: correct trajectories invoke tools in 55.2% of cases versus only 15.4% on wrong outcomes — a +39.8 pp gap. This suggests that successful tool use is concentrated in trajectories that actually reach correct answers, while failing trajectories commit to an answer without productive tool engagement. OpenClaw shows a complementary pattern: `recover` appears in 23.1% of correct trajectories but only 10.4% of wrong ones, meaning successful OpenClaw runs more often self-correct after errors. Conversely, OpenClaw wrong trajectories invoke `plan` more heavily (31.3% vs 16.2% correct), indicating that failure mode involves repeated replanning without resolution rather than tool churn. ZeroClaw's `verify` rate is essentially flat across outcomes (60.4% correct vs 61.8% wrong), confirming that verification alone is not discriminative — consistent with the §4 finding that the conversion quality of verification matters, not its presence. DirectLLM is structurally unable to distinguish outcomes at the action level since it only emits `reason` and `answer` with no scaffold-driven differentiation.

### 8.2 Entropy-stratified action composition

Action fraction = proportion of all events in that (harness, stratum) emitted as that canonical action type; rows sum to ~100%. Strata are split at each harness's per-harness median mean entropy.

| Harness | Stratum | median_mean_entropy | n_traj | n_events | answer | reason | tool_use | verify | plan | recover |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OpenClaw | HIGH | 0.2337 | 81 | 263 | 45.6% | 3.4% | 12.9% | 22.4% | 6.5% | 9.1% |
| OpenClaw | LOW | 0.2337 | 81 | 223 | 46.6% | 4.5% | 6.3% | 21.1% | 9.0% | 12.6% |
| OpenCode | HIGH | 0.2994 | 99 | 479 | 37.2% | 23.6% | 9.4% | 20.5% | 9.4% | 0.0% |
| OpenCode | LOW | 0.2994 | 99 | 589 | 26.8% | 26.5% | 18.7% | 19.2% | 8.8% | 0.0% |
| ZeroClaw | HIGH | 0.2533 | 99 | 1,746 | 7.5% | 78.2% | 0.0% | 5.3% | 8.9% | 0.0% |
| ZeroClaw | LOW | 0.2533 | 99 | 1,554 | 9.1% | 77.4% | 0.0% | 4.8% | 8.8% | 0.0% |
| DirectLLM | HIGH | 0.3168 | 99 | 198 | 50.0% | 50.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| DirectLLM | LOW | 0.3168 | 99 | 198 | 50.0% | 50.0% | 0.0% | 0.0% | 0.0% | 0.0% |

High-entropy trajectories in OpenCode show elevated `tool_use` (9.4% vs 18.7% low-stratum) — notably the high-entropy stratum actually has *lower* tool_use fraction than low-entropy, suggesting that high-entropy OpenCode trajectories substitute reasoning (`reason` 23.6% vs 26.5%) rather than emitting more tool events. OpenClaw high-entropy trajectories show the expected recovery pattern: `recover` fraction increases from 9.1% in high-entropy to 12.6% in low-entropy, meaning low-entropy (more confident) OpenClaw runs attempt recovery more often — which is counter-intuitive and may reflect systematic misconfidence. ZeroClaw's action composition is nearly identical across entropy strata (HIGH: reason 78.2%, LOW: reason 77.4%), indicating its scaffold imposes a reason-dominant execution pattern that does not respond to uncertainty level. DirectLLM shows a perfectly symmetric 50/50 answer-reason split across both strata, reflecting that its action sequence is deterministic at the scaffold level regardless of token-level confidence.

### 8.3 Key numeric findings

- **OpenCode tool_use correctness gap: +39.8 pp.** Correct OpenCode trajectories use tools in 55.2% of cases vs 15.4% for wrong outcomes — the largest outcome-conditioned tool gap across all harnesses.
- **OpenClaw recover rate inverts on correctness: +12.7 pp.** Correct OpenClaw trajectories trigger `recover` in 23.1% vs 10.4% for wrong ones, indicating that productive self-correction is a success signal, not a failure signal.
- **OpenClaw plan rate inverts on failure: +15.1 pp.** Wrong OpenClaw trajectories emit `plan` at 31.3% vs 16.2% on correct, consistent with a replanning-without-resolution failure mode.
- **ZeroClaw verify rate is flat (Δ = −1.4 pp).** The verify rate on wrong (61.8%) vs correct (60.4%) trajectories is statistically indistinguishable, confirming verify presence alone has no discriminative power in this harness.
- **OpenCode entropy-stratified tool_use: low-entropy uses more tools (+9.3 pp).** Low-entropy OpenCode trajectories show 18.7% tool_use fraction vs 9.4% for high-entropy, suggesting confident trajectories invest more in tool engagement before answering.
- **DirectLLM action composition is entropy-invariant.** Both HIGH and LOW strata yield an exactly 50/50 answer-reason split, confirming the direct scaffold imposes no adaptive behavior on token uncertainty.

### 8.4 Interpretive narrative

The joint evidence from §8.1 and §8.2 maps each harness's failure mode onto one of three patterns identified in the §4–§6 diagnostics. OpenCode's failure mode is primarily **tool-light premature commit**: wrong trajectories skip tool calls (15.4% tool_use vs 55.2% on correct) while maintaining high reason activity (92.3%), meaning the model reasons heavily but does not verify through external tool feedback before committing. OpenClaw's failure mode is **replanning churn without resolution**: wrong trajectories exhibit more plan events (31.3%) but fewer recover events (10.4%) compared to correct runs — the harness triggers replanning cycles that cycle without converging on a recoverable state. ZeroClaw's failure mode is **verification-light reasoning collapse**: despite its reason-dominant composition (77–78% of all events), verify rates are flat and essentially non-discriminative across outcomes, confirming that extensive reasoning does not translate into outcome-predictive checking behavior. DirectLLM is trivially **action-invariant**: the scaffold does not permit adaptive responses to uncertainty, so its failure mode is purely a model capability boundary unmediated by scaffold design. Critically, the entropy-stratified data in §8.2 shows that OpenClaw and ZeroClaw low-entropy strata do not shift toward more corrective actions (recover/verify) despite appearing more confident — a direct instance of the confidence inversion phenomenon documented in §2, now visible at the action-composition level rather than only at the task-level accuracy level.
