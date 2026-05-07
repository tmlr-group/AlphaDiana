"""
compute_arm_macro.py — Cross-benchmark ARM macro analysis pipeline.

Pipeline:
  Step 1: Load + unify ARM data across GPQA and HLE
  Step 2: Universal patterns (pooled across all harnesses × benchmarks)
  Step 3: Conditional patterns (benchmark × harness interactions)
  Step 4: Risk taxonomy (2D PD×arm_entropy plane, 3-factor score)
  Step 5: Synthesis tables for LaTeX

Outputs (analyze_tools/data/):
  arm_macro_unified.csv          — unified ARM + entropy data
  arm_macro_universal.csv        — pooled cross-tabulations
  arm_macro_conditional.csv      — benchmark × harness stratified
  arm_macro_risk_taxonomy.csv    — risk zone assignments
  arm_macro_synthesis_tables.tex — LaTeX tables
"""

import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "analyze_tools" / "data"

# ═══════════════════════════════════════════════════════════════════════════
# STEP 1: LOAD + UNIFY
# ═══════════════════════════════════════════════════════════════════════════

def load_gpqa_arm():
    """Load GPQA ARM trajectory features with entropy + keyword markers."""
    arm = pd.read_csv(DATA / "arm_trajectory_features.csv")
    deg = pd.read_csv(DATA / "degradation_task_features.csv")

    # Merge ARM with degradation (entropy + markers)
    # Drop harness_correct from deg to avoid duplicate column on merge
    cols_from_deg = [
        'task_id', 'harness',
        'harness_n_tokens', 'harness_mean_entropy',
        'harness_head_entropy_mean', 'harness_tail_entropy_mean',
        'harness_tail_minus_head_entropy', 'harness_low_entropy_token_share',
        'harness_top1_prob_mean', 'harness_repeated_ngram_rate',
        'looping_marker_count', 'self_correction_marker_count',
        'uncertainty_marker_count', 'tool_intent_marker_count',
        'tool_use_count',
    ]
    deg_sub = deg[cols_from_deg].copy()

    merged = arm.merge(deg_sub, on=['task_id', 'harness'], how='inner')
    merged['benchmark'] = 'GPQA'
    # harness_correct comes from ARM CSV (not DEG) since we dropped it from DEG
    merged['outcome'] = merged['harness_correct'].map({True: 'correct', False: 'wrong'})

    # Standardize column names
    merged = merged.rename(columns={
        'harness_n_tokens': 'n_tokens',
        'harness_mean_entropy': 'mean_entropy',
        'harness_head_entropy_mean': 'head_entropy',
        'harness_tail_entropy_mean': 'tail_entropy',
        'harness_tail_minus_head_entropy': 'delta_entropy',
        'harness_low_entropy_token_share': 'low_ent_share',
        'harness_top1_prob_mean': 'top1_prob',
        'harness_repeated_ngram_rate': 'ngram_repeat',
    })

    # Compute derived metrics
    merged['PD_IC_ratio'] = merged['rate_PD'] / merged['rate_IC'].clip(lower=0.001)
    merged['commit_gap'] = merged['rate_PD'] - merged['rate_IC']
    merged['mode_collapse'] = 1.0 - merged['arm_entropy'] / merged['arm_entropy'].max()

    return merged


def load_hle_arm():
    """Load HLE ARM data with entropy from context rotting."""
    arm = pd.read_csv(DATA / "cross_hle_arm_modes.csv")
    rot = pd.read_csv(DATA / "cross_context_rotting.csv")
    outcomes = pd.read_csv(DATA / "hle_entropy_by_outcome.csv")

    # Merge outcome labels
    arm = arm.merge(outcomes[['harness', 'task_id', 'correct']],
                    on=['harness', 'task_id'], how='left')
    arm['outcome'] = arm['correct'].apply(
        lambda x: 'correct' if x == 1 or x is True else ('wrong' if x == 0 or x is False else None)
    )

    # Merge context rotting (head/tail entropy)
    rot_sub = rot[['harness', 'task_id', 'n_tokens', 'head_entropy',
                    'tail_entropy', 'delta_entropy', 'low_ent_share', 'top1_prob_mean']]
    arm = arm.merge(rot_sub, on=['harness', 'task_id'], how='left')
    arm = arm.rename(columns={'top1_prob_mean': 'top1_prob'})

    arm['benchmark'] = 'HLE'

    # Compute derived metrics
    arm['PD_IC_ratio'] = arm['rate_PD'] / arm['rate_IC'].clip(lower=0.001)
    arm['commit_gap'] = arm['rate_PD'] - arm['rate_IC']
    arm['mode_collapse'] = 1.0 - arm['arm_entropy'] / arm['arm_entropy'].max()

    # HLE doesn't have transition data or keyword markers
    for col in ['ngram_repeat', 'looping_marker_count', 'self_correction_marker_count',
                'uncertainty_marker_count', 'tool_intent_marker_count', 'tool_use_count',
                'mean_entropy', 'low_ent_share']:
        if col not in arm.columns:
            arm[col] = np.nan

    return arm


def unify_data():
    """Load and unify GPQA + HLE ARM data."""
    gpqa = load_gpqa_arm()
    hle = load_hle_arm()

    # Common columns
    common_cols = [
        'task_id', 'harness', 'benchmark', 'outcome',
        'n_segments', 'arm_entropy', 'dominant_mode', 'dominant_rate',
        'rate_PD', 'rate_SE', 'rate_IC', 'rate_UN', 'rate_RR', 'rate_reason',
        'n_tokens', 'head_entropy', 'tail_entropy', 'delta_entropy',
        'PD_IC_ratio', 'commit_gap', 'mode_collapse',
    ]
    # Optional columns
    opt_cols = ['top1_prob', 'ngram_repeat', 'looping_marker_count',
                'self_correction_marker_count', 'uncertainty_marker_count',
                'tool_intent_marker_count', 'tool_use_count', 'mean_entropy',
                'low_ent_share', 'paired_outcome', 'PD->PD', 'reason->PD',
                'reason->IC', 'PD->reason', 'arm_sequence', 'harness_correct']

    all_cols = common_cols + [c for c in opt_cols if c in gpqa.columns or c in hle.columns]
    all_cols = [c for c in all_cols if c in gpqa.columns or c in hle.columns]

    unified = pd.concat([
        gpqa[[c for c in all_cols if c in gpqa.columns]],
        hle[[c for c in all_cols if c in hle.columns]],
    ], ignore_index=True)

    return unified


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2: UNIVERSAL PATTERNS (pooled across everything)
# ═══════════════════════════════════════════════════════════════════════════

def compute_universal_patterns(df):
    """Pooled cross-tabulations across all harnesses and benchmarks."""
    results = {}

    # ── 2.1 PD/IC ratio quartile × outcome ──
    df['PDIC_q'] = pd.qcut(df['PD_IC_ratio'].rank(method='first'), 4,
                           labels=['Q1_lowest', 'Q2', 'Q3', 'Q4_highest'])
    pdic_table = df.groupby('PDIC_q', observed=False).agg(
        n=('outcome', 'count'),
        acc=('outcome', lambda x: (x == 'correct').mean()),
        PD=('rate_PD', 'mean'),
        IC=('rate_IC', 'mean'),
        arm_ent=('arm_entropy', 'mean'),
        tokens=('n_tokens', 'mean'),
    ).round(4)
    results['pdic_quartile'] = pdic_table

    # ── 2.2 Arm entropy decile × outcome ──
    df['arm_ent_d'] = pd.qcut(df['arm_entropy'].rank(method='first'), 10,
                              labels=[f'D{i+1}' for i in range(10)])
    arment_table = df.groupby('arm_ent_d', observed=False).agg(
        n=('outcome', 'count'),
        acc=('outcome', lambda x: (x == 'correct').mean()),
        PD=('rate_PD', 'mean'),
        RR=('rate_RR', 'mean'),
        UN=('rate_UN', 'mean'),
    ).round(4)
    results['arment_decile'] = arment_table

    # ── 2.3 Dominant mode × outcome ──
    dom_table = df.groupby('dominant_mode', observed=False).agg(
        n=('outcome', 'count'),
        acc=('outcome', lambda x: (x == 'correct').mean()),
        PD=('rate_PD', 'mean'),
        IC=('rate_IC', 'mean'),
        RR=('rate_RR', 'mean'),
        arm_ent=('arm_entropy', 'mean'),
    ).round(4)
    # Compute odds ratio
    overall_acc = (df['outcome'] == 'correct').mean()
    dom_table['odds_ratio'] = (dom_table['acc'] / (1 - dom_table['acc'])) / \
                               (overall_acc / (1 - overall_acc))
    results['dominant_mode'] = dom_table

    # ── 2.4 Correct vs wrong ARM profile (pooled) ──
    profile = df.groupby('outcome').agg(
        n=('outcome', 'count'),
        PD=('rate_PD', 'mean'),
        reason=('rate_reason', 'mean'),
        IC=('rate_IC', 'mean'),
        UN=('rate_UN', 'mean'),
        RR=('rate_RR', 'mean'),
        arm_ent=('arm_entropy', 'mean'),
        tokens=('n_tokens', 'mean'),
    ).round(4)
    results['pooled_profile'] = profile

    return results


# ═══════════════════════════════════════════════════════════════════════════
# STEP 3: CONDITIONAL PATTERNS (benchmark × harness interactions)
# ═══════════════════════════════════════════════════════════════════════════

def compute_conditional_patterns(df):
    """Stratified analysis: benchmark × harness interactions."""
    results = {}

    # ── 3.1 PD rate × benchmark interaction ──
    bench_pd = df.groupby(['benchmark', 'outcome']).agg(
        n=('outcome', 'count'),
        PD=('rate_PD', 'mean'),
        IC=('rate_IC', 'mean'),
        RR=('rate_RR', 'mean'),
        arm_ent=('arm_entropy', 'mean'),
    ).round(4)

    # Compute ΔPD = PD(wrong) - PD(correct) per benchmark
    delta_pd = {}
    for bench in bench_pd.index.get_level_values('benchmark').unique():
        try:
            c = bench_pd.loc[(bench, 'correct'), 'PD']
            w = bench_pd.loc[(bench, 'wrong'), 'PD']
            delta_pd[bench] = round(w - c, 4)
        except KeyError:
            delta_pd[bench] = None
    results['benchmark_pd_delta'] = delta_pd
    results['benchmark_pd'] = bench_pd

    # ── 3.2 Harness × benchmark × outcome ARM profile ──
    hbo = df.groupby(['harness', 'benchmark', 'outcome']).agg(
        n=('outcome', 'count'),
        PD=('rate_PD', 'mean'),
        reason=('rate_reason', 'mean'),
        IC=('rate_IC', 'mean'),
        UN=('rate_UN', 'mean'),
        RR=('rate_RR', 'mean'),
        arm_ent=('arm_entropy', 'mean'),
        tokens=('n_tokens', 'mean'),
        head_ent=('head_entropy', 'mean'),
        tail_ent=('tail_entropy', 'mean'),
        delta_ent=('delta_entropy', 'mean'),
    ).round(4)
    results['harness_benchmark_outcome'] = hbo

    # ── 3.3 Arm entropy × harness interaction ──
    h_arment = df.groupby(['harness', 'outcome']).agg(
        n=('outcome', 'count'),
        arm_ent=('arm_entropy', 'mean'),
        PD=('rate_PD', 'mean'),
    ).round(4)

    delta_arment = {}
    for h in h_arment.index.get_level_values('harness').unique():
        try:
            c = h_arment.loc[(h, 'correct'), 'arm_ent']
            w = h_arment.loc[(h, 'wrong'), 'arm_ent']
            delta_arment[h] = round(w - c, 4)
        except KeyError:
            delta_arment[h] = None
    results['harness_arment_delta'] = delta_arment
    results['harness_arment'] = h_arment

    # ── 3.4 RR × benchmark × harness ──
    rr_table = df.groupby(['benchmark', 'harness', 'outcome']).agg(
        n=('outcome', 'count'),
        RR=('rate_RR', 'mean'),
        PD=('rate_PD', 'mean'),
        arm_ent=('arm_entropy', 'mean'),
    ).round(4)
    results['rr_interaction'] = rr_table

    return results


# ═══════════════════════════════════════════════════════════════════════════
# STEP 4: RISK TAXONOMY
# ═══════════════════════════════════════════════════════════════════════════

def compute_risk_taxonomy(df):
    """2D risk plane + 3-factor score + cluster assignment."""
    results = {}

    # ── 4.1 Risk zone assignment ──
    def assign_risk_zone(row):
        pd_rate = row['rate_PD']
        ae = row['arm_entropy']
        bench = row['benchmark']

        if bench == 'HLE':
            if ae > 0.8 and pd_rate < 0.2:
                return 'exploration'
            elif pd_rate < 0.2:
                return 'knowledge_navigation'
            else:
                return 'hle_other'
        else:  # GPQA
            if pd_rate > 0.5 and ae < 0.3:
                return 'collapse'
            elif pd_rate > 0.35 and ae > 0.7:
                return 'oscillation'
            elif pd_rate < 0.4 and 0.4 <= ae <= 0.8:
                return 'healthy'
            elif pd_rate < 0.35:
                return 'confident_commit'
            else:
                return 'pd_elevated'

    df['risk_zone'] = df.apply(assign_risk_zone, axis=1)

    zone_table = df.groupby('risk_zone', observed=False).agg(
        n=('outcome', 'count'),
        acc=('outcome', lambda x: (x == 'correct').mean()),
        PD=('rate_PD', 'mean'),
        IC=('rate_IC', 'mean'),
        RR=('rate_RR', 'mean'),
        arm_ent=('arm_entropy', 'mean'),
        tokens=('n_tokens', 'mean'),
    ).round(4)
    results['risk_zones'] = zone_table

    # ── 4.2 Risk zone × benchmark × harness contingency ──
    zone_cont = df.groupby(['risk_zone', 'benchmark', 'harness'], observed=False).size().unstack(fill_value=0)
    results['zone_contingency'] = zone_cont

    # ── 4.3 Three-factor risk score (simple weighted sum) ──
    features = ['rate_PD', 'mode_collapse', 'commit_gap']
    valid = df.dropna(subset=features + ['outcome']).copy()

    # Normalize each feature to [0,1]
    for f in features:
        fmin, fmax = valid[f].min(), valid[f].max()
        if fmax > fmin:
            valid[f'{f}_norm'] = (valid[f] - fmin) / (fmax - fmin)
        else:
            valid[f'{f}_norm'] = 0

    # Simple weighted risk score (equal weights)
    valid['risk_score'] = (valid['rate_PD_norm'] + valid['mode_collapse_norm'] + valid['commit_gap_norm']) / 3

    # AUC using simple ranking
    def simple_auc(y_true, y_score):
        """Compute AUC without sklearn."""
        y_true = np.array(y_true, dtype=bool)
        y_score = np.array(y_score)
        pos = y_score[y_true]
        neg = y_score[~y_true]
        if len(pos) == 0 or len(neg) == 0:
            return np.nan
        n_pos, n_neg = len(pos), len(neg)
        # Count pairs where pos > neg
        auc_val = 0
        for p in pos:
            auc_val += (p > neg).sum() + 0.5 * (p == neg).sum()
        return auc_val / (n_pos * n_neg)

    y_true = (valid['outcome'] == 'wrong').values
    y_score = valid['risk_score'].values

    auc = simple_auc(y_true, y_score)

    risk_by_bench = {}
    for b in valid['benchmark'].unique():
        g = valid[valid['benchmark'] == b]
        if len(g['outcome'].unique()) > 1:
            risk_by_bench[b] = simple_auc((g['outcome']=='wrong').values, g['risk_score'].values)

    risk_by_harness = {}
    for h in valid['harness'].unique():
        g = valid[valid['harness'] == h]
        if len(g['outcome'].unique()) > 1:
            risk_by_harness[h] = simple_auc((g['outcome']=='wrong').values, g['risk_score'].values)

    results['risk_score_auc'] = {'pooled': auc, 'by_benchmark': risk_by_bench,
                                  'by_harness': risk_by_harness}
    results['risk_score_weights'] = {'rate_PD': 1/3, 'mode_collapse': 1/3, 'commit_gap': 1/3}

    return results, valid


# ═══════════════════════════════════════════════════════════════════════════
# STEP 5: SYNTHESIS + OUTPUT
# ═══════════════════════════════════════════════════════════════════════════

def print_synthesis(univ, cond, risk):
    """Print key findings to stdout."""
    print("\n" + "=" * 70)
    print("ARM MACRO ANALYSIS — SYNTHESIS")
    print("=" * 70)

    # Universal
    print("\n─── UNIVERSAL: PD/IC ratio quartiles ───")
    print(univ['pdic_quartile'].to_string())

    print("\n─── UNIVERSAL: Arm entropy deciles ───")
    print(univ['arment_decile'].to_string())

    print("\n─── UNIVERSAL: Dominant mode × outcome ───")
    print(univ['dominant_mode'].to_string())

    # Conditional
    print("\n─── CONDITIONAL: ΔPD by benchmark ───")
    for bench, delta in cond['benchmark_pd_delta'].items():
        print(f"  {bench}: ΔPD = {delta:+.4f}")

    print("\n─── CONDITIONAL: ΔArm_ent by harness ───")
    for h, delta in cond['harness_arment_delta'].items():
        print(f"  {h}: ΔArm_ent = {delta:+.4f}")

    print("\n─── CONDITIONAL: Harness × Benchmark × Outcome ───")
    print(cond['harness_benchmark_outcome'].to_string())

    # Risk
    print("\n─── RISK: Zone accuracy ───")
    print(risk['risk_zones'].to_string())

    print("\n─── RISK: 3-factor score ───")
    print(f"  Pooled AUC: {risk['risk_score_auc']['pooled']:.4f}")
    print(f"  Weights: {risk['risk_score_weights']}")
    print(f"  AUC by benchmark: {risk['risk_score_auc']['by_benchmark']}")
    print(f"  AUC by harness: {risk['risk_score_auc']['by_harness']}")


def main():
    print("Loading + unifying ARM data...")
    df = unify_data()
    print(f"  Unified: {len(df)} trajectories")
    print(f"  Benchmarks: {df['benchmark'].unique()}")
    print(f"  Harnesses: {df['harness'].unique()}")
    print(f"  Outcomes: {df['outcome'].value_counts().to_dict()}")

    # Save unified
    outpath = DATA / "arm_macro_unified.csv"
    df.to_csv(outpath, index=False)
    print(f"  -> {outpath}")

    # Step 2: Universal
    print("\nComputing universal patterns...")
    univ = compute_universal_patterns(df)

    # Step 3: Conditional
    print("Computing conditional patterns...")
    cond = compute_conditional_patterns(df)

    # Step 4: Risk taxonomy
    print("Computing risk taxonomy...")
    risk, df_with_risk = compute_risk_taxonomy(df)

    # Save risk-augmented data
    riskpath = DATA / "arm_macro_risk.csv"
    df_with_risk.to_csv(riskpath, index=False)
    print(f"  -> {riskpath}")

    # Step 5: Print synthesis
    print_synthesis(univ, cond, risk)

    # ═══ Generate LaTeX tables ═══
    print("\n\nGenerating LaTeX tables...")
    tex_lines = []

    # Table: PD/IC quartile
    tex_lines.append(r"\begin{table}[t]")
    tex_lines.append(r"\centering\small")
    tex_lines.append(r"\caption{PD/IC ratio quartile analysis, pooled across all harnesses and benchmarks.}")
    tex_lines.append(r"\label{tab:arm-macro-pdic}")
    tex_lines.append(r"\begin{tabular}{lrrrrr}\toprule")
    tex_lines.append(r"PD/IC quartile & $n$ & Acc\% & PD rate & IC rate & Arm ent. \\ \midrule")
    for idx, row in univ['pdic_quartile'].iterrows():
        tex_lines.append(f"  {idx} & {int(row['n'])} & {row['acc']*100:.1f}\\% & {row['PD']:.3f} & {row['IC']:.3f} & {row['arm_ent']:.3f} \\\\")
    tex_lines.append(r"\bottomrule\end{tabular}\end{table}")

    # Table: Arm entropy decile
    tex_lines.append(r"\begin{table}[t]")
    tex_lines.append(r"\centering\small")
    tex_lines.append(r"\caption{Arm entropy decile analysis, pooled across all harnesses and benchmarks.}")
    tex_lines.append(r"\label{tab:arm-macro-arment}")
    tex_lines.append(r"\begin{tabular}{lrrrrr}\toprule")
    tex_lines.append(r"Arm ent. decile & $n$ & Acc\% & PD & RR & UN \\ \midrule")
    for idx, row in univ['arment_decile'].iterrows():
        tex_lines.append(f"  {idx} & {int(row['n'])} & {row['acc']*100:.1f}\\% & {row['PD']:.3f} & {row['RR']:.3f} & {row['UN']:.3f} \\\\")
    tex_lines.append(r"\bottomrule\end{tabular}\end{table}")

    # Table: Harness × Benchmark × Outcome
    tex_lines.append(r"\begin{table}[t]")
    tex_lines.append(r"\centering\small")
    tex_lines.append(r"\caption{ARM profile by harness, benchmark, and outcome.}")
    tex_lines.append(r"\label{tab:arm-macro-hbo}")
    tex_lines.append(r"\begin{tabular}{lllrrrrrr}\toprule")
    tex_lines.append(r"Bench. & Harness & Out. & PD & reason & IC & UN & RR & Arm ent. \\ \midrule")
    for (h, b, o), row in cond['harness_benchmark_outcome'].iterrows():
        tex_lines.append(f"  {b} & {h} & {o[0]} & {row['PD']:.3f} & {row['reason']:.3f} & {row['IC']:.3f} & {row['UN']:.3f} & {row['RR']:.3f} & {row['arm_ent']:.3f} \\\\")
    tex_lines.append(r"\bottomrule\end{tabular}\end{table}")

    # Table: Risk zones
    tex_lines.append(r"\begin{table}[t]")
    tex_lines.append(r"\centering\small")
    tex_lines.append(r"\caption{Risk zone taxonomy based on PD rate and arm entropy.}")
    tex_lines.append(r"\label{tab:arm-macro-risk}")
    tex_lines.append(r"\begin{tabular}{lrrrrrr}\toprule")
    tex_lines.append(r"Risk zone & $n$ & Acc\% & PD & IC & RR & Arm ent. \\ \midrule")
    for idx, row in risk['risk_zones'].iterrows():
        tex_lines.append(f"  {idx} & {int(row['n'])} & {row['acc']*100:.1f}\\% & {row['PD']:.3f} & {row['IC']:.3f} & {row['RR']:.3f} & {row['arm_ent']:.3f} \\\\")
    tex_lines.append(r"\bottomrule\end{tabular}\end{table}")

    texpath = DATA / "arm_macro_synthesis_tables.tex"
    with open(texpath, 'w') as f:
        f.write('\n'.join(tex_lines))
    print(f"  -> {texpath}")

    print("\nDone.")


if __name__ == "__main__":
    main()
