# ARM Macro Analysis Pipeline

## Goal

Identify **cross-harness, cross-benchmark** regularities in ARM cognitive modes
that distinguish correct from wrong trajectories. Not per-harness anecdotes;
universal or conditionally-universal patterns with quantitative support.

## Scope

- **Harnesses**: DirectLLM, OpenClaw, OpenCode, ZeroClaw
- **Benchmarks**: GPQA-Diamond, HLE (AIME if ARM data available)
- **ARM modes**: PD, SE, IC, UN, RR, reason (+ arm entropy, transitions)
- **Outcome**: correct / wrong (and paired outcome where DirectLLM available)

---

## Phase 1: Data Preparation

### 1.1 Unify ARM data across benchmarks

```
Input:
  - GPQA:   analyze_tools/data/arm_trajectory_features.csv   (n≈590)
  - HLE:    analyze_tools/data/cross_hle_arm_modes.csv        (n≈914)

Output:
  - analyze_tools/data/arm_macro_unified.csv

Columns:
  task_id, harness, benchmark, outcome, n_segments,
  rate_PD, rate_SE, rate_IC, rate_UN, rate_RR, rate_reason,
  arm_entropy, dominant_mode, dominant_rate,
  PD->PD, reason->PD, reason->IC, PD->reason (GPQA only, NaN for HLE)
```

### 1.2 Merge entropy + keyword markers

```
Input:
  - GPQA: degradation_task_features.csv (head/tail entropy, keyword markers)
  - HLE:  cross_context_rotting.csv      (head/tail entropy)

Output additional columns:
  head_entropy, tail_entropy, delta_entropy,
  low_ent_share, top1_prob, ngram_repeat,
  looping_markers, self_corr_markers, uncert_markers
```

### 1.3 Outcome normalization

```
- GPQA: harness_correct (bool) + paired_outcome (both_correct/both_wrong/regression/rescue)
- HLE:  correct (bool)
- Standardize to: outcome ∈ {correct, wrong}
```

---

## Phase 2: Universal Pattern Discovery

### 2.1 The PD/IC ratio as universal predictor

**Question**: Does the ratio of deduction (PD) to commitment (IC) predict
correctness across ALL harnesses and benchmarks?

**Compute**:
```
For each trajectory:  PD_IC_ratio = rate_PD / max(rate_IC, 0.001)
Pool across all harnesses × benchmarks.
Compute AUC for PD_IC_ratio → outcome.
Compute accuracy by PD_IC_ratio quartile.
```

**Expected output table**:
```
PD/IC quartile    n     Acc%    mean_PD    mean_IC    mean_arm_ent
Q1 (lowest)      xxx    xx%     x.xxx      x.xxx      x.xxx
Q2               xxx    xx%     x.xxx      x.xxx      x.xxx
Q3               xxx    xx%     x.xxx      x.xxx      x.xxx
Q4 (highest)     xxx    xx%     x.xxx      x.xxx      x.xxx
```

**Hypothesis**: PD/IC ratio is the strongest single-number predictor of failure,
but only when PD > 0.20 (i.e., on science/math benchmarks). On HLE where PD is
already low, the ratio carries no signal.

### 2.2 Arm entropy as a U-shaped risk indicator

**Question**: Is arm entropy universally U-shaped (both too low AND too high
predict failure)?

**Compute**:
```
Bin arm_entropy into deciles.
For each decile: accuracy, mean PD, mean IC, mean RR.
Do this pooled, then split by benchmark, then split by harness.
```

**Expected output table**:
```
Arm_ent decile  n    Acc%   PD     IC     RR     Dominant mode
D1 (lowest)    xxx   xx%   x.xx   x.xx   x.xx   PD/reason
D2-D9          ...
D10 (highest)  xxx   xx%   x.xx   x.xx   x.xx   RR/UN
```

**Hypothesis**: 
- Low arm entropy (<0.3) → mode collapse → high failure rate (OpenClaw signature)
- Mid arm entropy (0.3-0.8) → healthy deliberation → highest accuracy
- High arm entropy (>0.8) → mode oscillation → elevated failure (ZeroClaw wrong signature)
- BUT on HLE, high arm entropy is normal (1.1-1.2) and not predictive of failure

### 2.3 Dominant mode × outcome universal contingency table

**Question**: Which dominant cognitive mode most strongly associates with
correctness, pooled across everything?

**Compute**:
```
For each trajectory, assign dominant_mode = argmax(rate_PD, rate_reason, rate_IC, rate_UN, rate_RR).
Cross-tabulate dominant_mode × outcome.
Compute odds ratio for each mode.
```

**Expected output table**:
```
Dominant mode   n     Acc%    Odds ratio (correct/wrong)
PD             xxx    xx%     x.xx
reason         xxx    xx%     x.xx
IC             xxx    xx%     x.xx
UN             xxx    xx%     x.xx
RR             xxx    xx%     x.xx
```

---

## Phase 3: Conditional Pattern Discovery

### 3.1 Benchmark as moderator of PD → outcome relationship

**Question**: Does PD rate predict failure on GPQA but not on HLE?

**Compute**:
```
Logistic regression: outcome ~ rate_PD * benchmark
Interaction term tests whether benchmark moderates the PD→outcome relationship.
Stratify by benchmark: compute PD→outcome correlation within each.
```

**Expected output**:
```
Benchmark   PD(correct)  PD(wrong)  ΔPD     PD-outcome correlation
GPQA        x.xxx        x.xxx      +x.xx   r = x.xx (significant)
HLE         x.xxx        x.xxx      -x.xx   r = x.xx (ns or reversed)
```

### 3.2 Harness as moderator of arm entropy → outcome relationship

**Question**: Does arm entropy predict failure differently under different
harness architectures?

**Compute**:
```
Logistic regression: outcome ~ arm_entropy * harness
Stratify by harness: compute arm_entropy→outcome correlation within each.
```

**Expected output table**:
```
Harness     Arm_ent(C)  Arm_ent(W)  ΔArm_ent   Direction
OpenClaw    x.xxx        x.xxx        -x.xx     Lower on wrong (collapse)
OpenCode    x.xxx        x.xxx        -x.xx     Lower on wrong (mild)
ZeroClaw    x.xxx        x.xxx        +x.xx     HIGHER on wrong (oscillation)
DirectLLM   x.xxx        x.xxx        ~0        Flat
```

### 3.3 The RR × benchmark interaction

**Question**: Is high RR a good sign on HLE but a bad sign on GPQA ZeroClaw?

**Compute**:
```
Logistic regression: outcome ~ rate_RR * benchmark * harness
Triple interaction: does the RR→outcome slope flip sign across (benchmark, harness) cells?
```

**Expected output**:
```
Benchmark  Harness    RR(C)   RR(W)   ΔRR    Interpretation
GPQA       OpenClaw   x.xx    x.xx    -x.xx  RR = success signal
GPQA       ZeroClaw   x.xx    x.xx    +x.xx  RR = confusion signal
HLE        DirectLLM  x.xx    x.xx    ~0     RR = normal, non-diagnostic
HLE        OpenCode   x.xx    x.xx    ~0     RR = normal, non-diagnostic
```

---

## Phase 4: Multi-dimensional Risk Taxonomy

### 4.1 2D risk plane: PD rate × Arm entropy

**Question**: Can we identify "safe" and "danger" zones in the (PD rate, arm
entropy) plane?

**Compute**:
```
Scatter each trajectory into 2D space: (rate_PD, arm_entropy).
Color by outcome.
Compute accuracy heatmap on 10×10 grid.
Overlay harness boundaries.
```

**Expected finding**: 
- **Safe zone**: PD < 0.4, arm_entropy 0.4-0.8 → high accuracy across all harnesses
- **Collapse zone**: PD > 0.5, arm_entropy < 0.3 → near-zero accuracy (OpenClaw wrong)
- **Oscillation zone**: PD > 0.3, arm_entropy > 0.7 → mixed accuracy (ZeroClaw)
- **Exploration zone**: PD < 0.2, arm_entropy > 1.0 → HLE-typical, accuracy depends on knowledge

### 4.2 Three-factor risk score

**Question**: Can we construct a simple 3-factor score that predicts trajectory
correctness?

**Score components**:
```
1. PD_load      = rate_PD (higher = riskier on science, neutral on knowledge)
2. Mode_collapse = 1 - arm_entropy/max_arm_entropy (higher = riskier)
3. Commit_gap   = rate_PD - rate_IC (higher = riskier; model derives but won't commit)
```

**Compute**:
```
Risk_score = w1*PD_load + w2*Mode_collapse + w3*Commit_gap
Fit weights via logistic regression on pooled data.
Evaluate AUC per harness, per benchmark.
```

**Deliverable**: A simple diagnostic: "If Risk_score > T, trajectory has >80%
probability of being wrong."

---

## Phase 5: Transition-Level Universal Signatures

### 5.1 The PD self-loop as universal danger signal

**Question**: Is PD→PD transition probability universally higher on wrong
trajectories?

**Compute** (GPQA only, since HLE lacks transition data):
```
For each trajectory: compute PD_self_loop = PD→PD rate.
Pool across GPQA harnesses.
Binary classifier: PD_self_loop → outcome.
Find optimal threshold.
```

**Expected**:
```
PD_self_loop > 0.5 → 3× higher odds of being wrong (GPQA)
```

### 5.2 The commitment exit fraction

**Question**: Does the fraction of cognitive transitions that exit to IC
(reason→IC + PD→IC) predict correctness?

**Compute**:
```
Commit_exit_frac = (reason→IC_count + PD→IC_count) / total_transitions
Correlate with outcome.
```

---

## Phase 6: Synthesis — The Universal ARM Failure Taxonomy

### 6.1 Cluster trajectories by ARM profile

**Method**: K-means on (rate_PD, rate_IC, rate_UN, rate_RR, arm_entropy), k=4-6.

**Expected clusters**:
```
C1: "Healthy deduction"     — moderate PD, moderate IC, low UN, mid arm_ent → HIGH accuracy
C2: "PD spiral"             — very high PD, near-zero IC, low arm_ent → VERY LOW accuracy
C3: "Mode oscillation"      — high PD, high arm_ent, high RR/UN → LOW accuracy
C4: "Knowledge exploration" — low PD, high RR/UN, high arm_ent → accuracy depends on benchmark
C5: "Confident commitment"  — high IC, low UN, low arm_ent → HIGH accuracy (when correct), LOW (when wrong)
```

### 6.2 Cluster × benchmark × harness contingency

**Question**: Which clusters dominate in which (benchmark, harness) cells?

**Expected output**:
```
                 GPQA                     HLE
           OClaw  OCode  ZClaw    DirLLM  OCode  ZClaw
C1 (healthy)   40%   55%    60%        -      -      -
C2 (spiral)    35%   10%     0%        -      -      -
C3 (oscillate)  5%    5%    25%        -      -      -
C4 (explore)    5%   10%     5%      70%    60%    55%
C5 (confident) 15%   20%    10%      30%    40%    45%
```

---

## Output Deliverables

| # | Deliverable | Format |
|---|-------------|--------|
| 1 | `arm_macro_unified.csv` | Data: all trajectories, all benchmarks, unified schema |
| 2 | `arm_macro_summary.tex` | LaTeX: 6-8 universal findings with tables |
| 3 | `fig_arm_risk_plane.pdf` | Figure: PD×arm_entropy 2D accuracy heatmap |
| 4 | `fig_arm_clusters.pdf` | Figure: cluster visualization |
| 5 | `arm_macro_stats.json` | Machine-readable summary statistics |

## Implementation Order

```
Step 1 (1hr):  Phase 1 — unify data → arm_macro_unified.csv
Step 2 (2hr):  Phase 2 — universal patterns (PD/IC ratio, arm_entropy U-shape,
               dominant mode contingency)
Step 3 (1hr):  Phase 3 — conditional patterns (benchmark × harness interactions)
Step 4 (1hr):  Phase 4 — risk taxonomy (2D plane, 3-factor score)
Step 5 (1hr):  Phase 5 — transition signatures (GPQA only)
Step 6 (2hr):  Phase 6 — clustering + synthesis + LaTeX writeup
```

## Key Design Decisions

1. **Pool first, split second.** Start with all trajectories pooled to find
   universal patterns; then test whether they survive stratification.

2. **Interaction terms over separate analyses.** The core insight is that
   benchmark × harness × ARM interactions matter more than main effects.
   All regressions should include interaction terms.

3. **Simple metrics over complex models.** PD/IC ratio and arm entropy are
   interpretable; a 3-factor score is the maximum complexity worth pursuing.
   No random forests or neural nets.

4. **Representative prose, not p-values.** Findings should state "across all
   harness-benchmark pairs, trajectories with X characteristic are Y times
   more likely to be wrong" — not "p < 0.001 for the interaction term."
