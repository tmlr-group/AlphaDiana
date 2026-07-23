# ARM Macro Analysis: Cross-Benchmark Success/Failure Patterns

## Objective

Identify **universal** and **conditional** ARM (Agentic Reasoning Mode) patterns
that distinguish correct from wrong trajectories across harnesses and benchmarks.
The key methodological principle is **pool first, split second** — find patterns
that hold across everything, then test whether they survive stratification.

## Data

- GPQA-Diamond: 586 trajectories (OpenClaw, OpenCode, ZeroClaw) with ARM + entropy + keyword markers
- HLE: 922 trajectories (DirectLLM, OpenCode) with ARM + context rotting
- Total: 1,508 trajectories, 4 harnesses, 2 benchmarks

## ARM Taxonomy

| Mode | Name | Description |
|------|------|-------------|
| PD | Principle-Based Deduction | Derivation from laws, theorems, equations |
| SE | Systematic Elimination | Explicit option-by-option comparison |
| IC | Intuitive Commitment | Direct assertion without extended derivation |
| UN | Uncertainty Navigation | Explicit hedging, probabilistic language |
| RR | Recovery-Replanning | Correction of prior reasoning, direction change |
| reason | Undifferentiated | Generic deliberation not triggering any classifier |

## Analysis Layers

### Layer 1: Universal (pooled across all)

1. **PD/IC ratio quartile × outcome**: Does the ratio of deduction to commitment
   predict correctness universally?
2. **Arm entropy decile × outcome**: Is there a U-shaped risk curve?
3. **Dominant mode × outcome**: Which cognitive modes associate with
   correctness?

### Layer 2: Conditional (benchmark × harness interactions)

1. **ΔPD by benchmark**: Does PD over-expression predict failure on GPQA but
   not HLE?
2. **ΔArm_ent by harness**: Does arm entropy direction differ by architecture?
3. **RR × benchmark × harness**: Is high RR productive (HLE) or oscillatory
   (GPQA ZeroClaw)?
4. **Harness × Benchmark × Outcome full matrix**: Complete ARM profile for
   every cell.

### Layer 3: Risk Taxonomy

1. **2D risk plane**: PD rate × arm entropy with accuracy heatmap
2. **Risk zone assignment**: collapse / oscillation / healthy / exploration /
   knowledge_navigation / confident_commit / pd_elevated
3. **Three-factor risk score**: PD_load + mode_collapse + commit_gap

## Key Findings

### Universal

| PD/IC quartile | Acc | PD | IC | Interpretation |
|---------------|-----|----|----|----------------|
| Q1 (lowest) | 21.8% | 0.00 | 0.14 | Under-derive + over-commit (HLE-dominant pool) |
| Q2 | 24.9% | 0.04 | 0.18 | Low derivation, high commitment |
| Q3 | 57.3% | 0.20 | 0.03 | Moderate derivation, low commitment |
| Q4 (highest) | 52.0% | 0.75 | 0.00 | Heavy derivation, zero commitment — but ~half correct |

**The PD/IC ratio is NOT a universal failure predictor.** It works on GPQA
(Q4=31% acc vs Q3=88%) but the pooled signal is washed out by HLE where PD
rates are uniformly low.

| Arm entropy decile | Acc | PD | RR | Interpretation |
|-------------------|-----|----|----|----------------|
| D1-D2 (lowest) | 12-34% | varies | varies | Mode collapse = danger |
| D5-D7 (mid) | 55-72% | 0.24-0.41 | 0.02-0.11 | Healthy deliberation |
| D10 (highest) | 27.8% | 0.15 | 0.22 | Mode oscillation = danger |

**Arm entropy is U-shaped:** both too low AND too high predict failure.
But the "too high" threshold differs by benchmark (HLE normal range is 1.1-1.2).

### Conditional (the critical interactions)

**ΔPD by benchmark:**
```
GPQA: PD(correct)=0.390  PD(wrong)=0.530  ΔPD=+0.140  → PD over-expression = failure
HLE:  PD(correct)=0.138  PD(wrong)=0.126  ΔPD=-0.012  → NO signal (direction reversed!)
```

**ΔArm_ent by harness:**
```
OpenClaw:  ΔArm_ent = -0.127  (collapse on wrong)
OpenCode:  ΔArm_ent = -0.283  (collapse on wrong)
ZeroClaw:  ΔArm_ent = +0.169  (oscillation on wrong)
DirectLLM: ΔArm_ent = -0.094  (mild decline)
```

**Harness × Benchmark × Outcome full matrix:**
```
                GPQA-Diamond                         HLE
         OClaw      OCode      ZClaw        DirLLM     OCode
PD(c)    .538       .353       .280         .125       .162
PD(w)    .608       .498       .406         .106       .144
ΔPD      +.070      +.145      +.126        -.019      -.017

RR(c)    .017       .019       .021         .272       .364
RR(w)    .010       .011       .044         .259       .310

ArmE(c)  .523       .598       .643         1.230      .166
ArmE(w)  .395       .464       .812         1.136      .128
```

### Risk Zones

| Zone | n | Acc | PD | IC | RR | Arm ent | Description |
|------|---|-----|----|----|----|---------|-------------|
| healthy | 164 | **81.7%** | 0.15 | 0.04 | 0.03 | 0.59 | Moderate PD, mid entropy |
| collapse | 72 | 47.4% | 1.00 | 0.00 | 0.00 | 0.00 | Pure PD, zero diversity |
| oscillation | 42 | 59.5% | 0.47 | 0.03 | 0.06 | 0.96 | High PD, high entropy |
| confident_commit | 107 | **83.6%** | 0.13 | 0.06 | 0.04 | 0.61 | Low PD, decisive |
| pd_elevated | 201 | 70.8% | 0.58 | 0.01 | 0.00 | 0.65 | High PD, moderate entropy |
| exploration | 248 | 25.0% | 0.08 | 0.09 | 0.25 | 1.21 | Low PD, high RR/UN |
| knowledge_nav | 507 | 13.2% | 0.00 | 0.17 | 0.39 | 0.11 | No PD, RR-dominant |
