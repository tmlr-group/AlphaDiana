"""
Script 5: plot_gpqa_figures.py

Generate all GPQA analysis figures for the AlphaDiana paper.

Figures:
  A: fig_gpqa_entropy_token.pdf  — entropy x token density (1x4 subplots per harness)
  B: fig_gpqa_subdomain.pdf      — subdomain pass rate grouped bar chart
  C: fig_gpqa_paired_outcome.pdf — paired-outcome failure signatures heatmap
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "analyze_tools", "data")
FIG_DIR = os.path.join(BASE, "analyze_tools", "figures")

os.makedirs(FIG_DIR, exist_ok=True)

# ── Style constants ─────────────────────────────────────────────────────────
FONT_SIZE = 9
LABEL_SIZE = 8
TICK_SIZE = 7
COL_W = 3.5   # single column (inches)
FULL_W = 7.0  # full width (inches)

plt.rcParams.update({
    "font.size": FONT_SIZE,
    "axes.labelsize": LABEL_SIZE,
    "xtick.labelsize": TICK_SIZE,
    "ytick.labelsize": TICK_SIZE,
    "axes.titlesize": FONT_SIZE,
    "legend.fontsize": LABEL_SIZE,
    "figure.dpi": 150,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
})

HARNESS_LABELS = {
    "openclaw": "OpenClaw",
    "opencode": "OpenCode",
    "zeroclaw": "ZeroClaw",
    "directllm": "DirectLLM",
}
HARNESS_COLORS = {
    "directllm": "#555555",
    "openclaw":  "#1f77b4",
    "opencode":  "#ff7f0e",
    "zeroclaw":  "#2ca02c",
}
OUTCOME_COLORS = {"correct": "#1a6fbf", "wrong": "#d62728"}
OUTCOME_MARKERS = {"correct": "o", "wrong": "x"}

CORRECT_OUTCOMES = {"both_correct", "rescue"}
WRONG_OUTCOMES   = {"both_wrong",  "regression"}


# ─────────────────────────────────────────────────────────────────────────────
# Figure A: Entropy × Token density (1 × 4 subplots, one per harness)
# ─────────────────────────────────────────────────────────────────────────────
def plot_entropy_token(features_df):
    harnesses = ["openclaw", "opencode", "zeroclaw"]

    fig, axes = plt.subplots(1, 3, figsize=(FULL_W, 2.3), sharey=False)
    fig.subplots_adjust(wspace=0.35)

    for ax, harness in zip(axes, harnesses):
        sub = features_df[
            (features_df["harness"] == harness) &
            (features_df["harness_score_status"] == "valid_scored")
        ].copy()
        if sub.empty:
            ax.set_visible(False)
            continue

        sub["outcome_label"] = sub["paired_outcome"].apply(
            lambda x: "correct" if x in CORRECT_OUTCOMES
            else ("wrong" if x in WRONG_OUTCOMES else None)
        )
        sub = sub.dropna(subset=["outcome_label"])
        sub = sub.dropna(subset=["harness_mean_entropy", "harness_n_tokens"])
        sub["log_tokens"] = np.log10(sub["harness_n_tokens"].clip(lower=1))

        # Quadrant thresholds
        q25_ent = sub["harness_mean_entropy"].quantile(0.25)
        q75_tok = sub["log_tokens"].quantile(0.75)

        for outcome in ["correct", "wrong"]:
            grp = sub[sub["outcome_label"] == outcome]
            ax.scatter(
                grp["log_tokens"],
                grp["harness_mean_entropy"],
                c=OUTCOME_COLORS[outcome],
                marker=OUTCOME_MARKERS[outcome],
                alpha=0.55,
                s=14,
                linewidths=0.5,
                label=outcome.capitalize(),
                zorder=3,
            )

        # Quadrant lines
        ax.axhline(q25_ent, color="gray", lw=0.7, ls="--", zorder=2)
        ax.axvline(q75_tok, color="gray", lw=0.7, ls="--", zorder=2)

        # Annotate bottom-right quadrant (low entropy, long)
        br = sub[(sub["harness_mean_entropy"] <= q25_ent) & (sub["log_tokens"] >= q75_tok)]
        if len(br) > 0:
            wrong_rate = (br["outcome_label"] == "wrong").mean()
            ax.text(
                q75_tok + 0.04,
                q25_ent - 0.01,
                f"wrong={wrong_rate:.0%}\n(n={len(br)})",
                fontsize=6,
                color="#d62728",
                va="top",
                ha="left",
            )

        ax.set_title(HARNESS_LABELS.get(harness, harness), fontsize=FONT_SIZE)
        ax.set_xlabel("log$_{10}$(tokens)", fontsize=LABEL_SIZE)
        if ax == axes[0]:
            ax.set_ylabel("mean entropy (nats)", fontsize=LABEL_SIZE)

    # Shared legend on the last axis
    handles, labels = axes[-1].get_legend_handles_labels()
    if handles:
        axes[-1].legend(handles, labels, loc="upper right", markerscale=1.2, framealpha=0.7)

    fig.suptitle("GPQA-Diamond: Entropy × Token Density by Outcome", fontsize=FONT_SIZE, y=1.01)
    out_path = os.path.join(FIG_DIR, "fig_gpqa_entropy_token.pdf")
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Figure B: Subdomain pass rate grouped bar chart
# ─────────────────────────────────────────────────────────────────────────────
def plot_subdomain(passrate_df):
    # Sort by directllm_rate descending
    df = passrate_df.dropna(subset=["directllm_rate"]).sort_values(
        "directllm_rate", ascending=True  # ascending so top of chart = highest
    ).reset_index(drop=True)

    harness_cols = [
        ("directllm_rate", "DirectLLM", HARNESS_COLORS["directllm"]),
        ("openclaw_rate",  "OpenClaw",  HARNESS_COLORS["openclaw"]),
        ("opencode_rate",  "OpenCode",  HARNESS_COLORS["opencode"]),
        ("zeroclaw_rate",  "ZeroClaw",  HARNESS_COLORS["zeroclaw"]),
    ]

    n_sub = len(df)
    bar_height = 0.18
    y = np.arange(n_sub)
    offsets = np.array([-1.5, -0.5, 0.5, 1.5]) * bar_height

    fig_h = max(3.5, 0.28 * n_sub)
    fig, ax = plt.subplots(figsize=(COL_W + 0.5, fig_h))

    for offset, (col, label, color) in zip(offsets, harness_cols):
        vals = df[col].fillna(0).values
        ax.barh(y + offset, vals, height=bar_height, color=color, label=label, alpha=0.85)

    ax.set_yticks(y)
    # Shorten long subdomain names
    labels_short = [
        s if len(s) <= 28 else s[:26] + ".."
        for s in df["subdomain"].tolist()
    ]
    ax.set_yticklabels(labels_short, fontsize=6)
    ax.set_xlabel("Pass rate", fontsize=LABEL_SIZE)
    ax.set_xlim(0, 1.0)
    ax.set_title("GPQA-Diamond: Subdomain Pass Rates", fontsize=FONT_SIZE)
    ax.legend(loc="lower right", fontsize=6, framealpha=0.7)
    ax.grid(axis="x", lw=0.4, alpha=0.5)

    fig.tight_layout()
    out_path = os.path.join(FIG_DIR, "fig_gpqa_subdomain.pdf")
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Figure C: Paired-outcome failure signatures heatmap
# ─────────────────────────────────────────────────────────────────────────────
def plot_paired_outcome_heatmap(summary_df):
    harnesses = ["openclaw", "opencode", "zeroclaw"]
    outcomes = ["both_correct", "rescue", "regression", "both_wrong"]
    outcome_labels = ["Both Correct", "Rescue", "Regression", "Both Wrong"]

    # Features to show and their display labels
    features = [
        ("malformed_prediction_rate",    "Malformed\nprediction"),
        ("missing_boxed_answer_rate",     "Missing\nboxed ans."),
        ("low_entropy_long_rate",         "Low ent.\nlong traj."),
        ("mean_token_ratio_vs_direct",    "Token ratio\nvs Direct"),
    ]
    feat_cols  = [f[0] for f in features]
    feat_names = [f[1] for f in features]

    fig, axes = plt.subplots(1, 3, figsize=(FULL_W, 2.8))
    fig.subplots_adjust(wspace=0.4)

    vmin, vmax = 0.0, 1.0  # rates are 0-1 except token_ratio; handle separately

    for ax, harness in zip(axes, harnesses):
        h_df = summary_df[summary_df["harness"] == harness]

        # Build matrix: rows=outcomes, cols=features
        mat = np.full((len(outcomes), len(feat_cols)), np.nan)
        for i, outcome in enumerate(outcomes):
            row = h_df[h_df["paired_outcome"] == outcome]
            if row.empty:
                continue
            row = row.iloc[0]
            for j, col in enumerate(feat_cols):
                if col in row.index:
                    mat[i, j] = float(row[col])

        # Normalize each column independently for display
        mat_norm = mat.copy()
        col_vmaxes = []
        for j in range(mat.shape[1]):
            col_max = np.nanmax(mat[:, j])
            col_vmaxes.append(col_max if col_max > 0 else 1.0)
            mat_norm[:, j] = mat[:, j] / col_vmaxes[j]

        im = ax.imshow(mat_norm, aspect="auto", cmap="YlOrRd", vmin=0, vmax=1,
                       interpolation="nearest")

        # Annotate cells with actual values
        for i in range(len(outcomes)):
            for j in range(len(feat_cols)):
                val = mat[i, j]
                if not np.isnan(val):
                    txt = f"{val:.2f}"
                    # Text color: white if dark cell
                    cell_brightness = mat_norm[i, j]
                    color = "white" if cell_brightness > 0.65 else "black"
                    ax.text(j, i, txt, ha="center", va="center",
                            fontsize=6, color=color)

        ax.set_xticks(range(len(feat_cols)))
        ax.set_xticklabels(feat_names, fontsize=6, rotation=0, ha="center")
        ax.set_yticks(range(len(outcomes)))
        if ax == axes[0]:
            ax.set_yticklabels(outcome_labels, fontsize=6)
        else:
            ax.set_yticklabels([])
        ax.set_title(HARNESS_LABELS.get(harness, harness), fontsize=FONT_SIZE)

        # n per outcome in title suffix
        n_labels = []
        for outcome in outcomes:
            row = h_df[h_df["paired_outcome"] == outcome]
            if not row.empty:
                n_labels.append(str(int(row.iloc[0]["n"])))
            else:
                n_labels.append("0")
        # Add n as right-side labels
        ax2 = ax.twinx()
        ax2.set_ylim(ax.get_ylim())
        ax2.set_yticks(range(len(outcomes)))
        ax2.set_yticklabels([f"n={n}" for n in n_labels], fontsize=5)
        ax2.tick_params(length=0)

    fig.suptitle(
        "GPQA-Diamond: Paired-Outcome Failure Signatures (column-normalized)",
        fontsize=FONT_SIZE, y=1.01
    )
    out_path = os.path.join(FIG_DIR, "fig_gpqa_paired_outcome.pdf")
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    features_path  = os.path.join(DATA_DIR, "degradation_task_features.csv")
    passrate_path  = os.path.join(DATA_DIR, "gpqa_subdomain_passrate.csv")
    summary_path   = os.path.join(DATA_DIR, "degradation_summary.csv")

    # Figure A
    print("Loading feature matrix for Figure A ...")
    features_df = pd.read_csv(features_path)
    plot_entropy_token(features_df)

    # Figure B
    print("Loading subdomain pass rates for Figure B ...")
    if os.path.exists(passrate_path):
        passrate_df = pd.read_csv(passrate_path)
        plot_subdomain(passrate_df)
    else:
        print(f"  WARNING: {passrate_path} not found — skipping Figure B")

    # Figure C
    print("Loading degradation summary for Figure C ...")
    if os.path.exists(summary_path):
        summary_df = pd.read_csv(summary_path)
        # Rename column to match expected key
        if "paired_outcome" not in summary_df.columns and "outcome" in summary_df.columns:
            summary_df = summary_df.rename(columns={"outcome": "paired_outcome"})
        plot_paired_outcome_heatmap(summary_df)
    else:
        print(f"  WARNING: {summary_path} not found — skipping Figure C")

    print("\nAll figures done.")


if __name__ == "__main__":
    main()
