#!/usr/bin/env python3
"""
plot_entropy_token_scatter.py — Scatter plot of trajectory-level entropy vs token length.
One figure per model, correct (green) vs wrong (red) per subplot.

Produces:
    figures/scatter/fig_entropy_token_scatter_qwen.pdf
    figures/scatter/fig_entropy_token_scatter_gemma.pdf

Run:
    python3 analyze_tools/plot_entropy_token_scatter.py
    (from repo root)
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

SKILL_DIR = Path("/home/xxx/academic-plot/scripts")
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from academic_plot import set_academic_style

FIG_DIR = Path(__file__).parent / "figures" / "scatter"
FIG_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = Path(__file__).parent / "data"
CSV_PATH = DATA_DIR / "entropy_token_scatter.csv"

C_CORRECT = "#2ca02c"
C_WRONG   = "#d62728"

HARNESS_ORDER = ["directllm", "openclaw", "zeroclaw", "opencode"]
HARNESS_LABEL = {"directllm": "DirectLLM", "openclaw": "OpenClaw",
                 "zeroclaw": "ZeroClaw", "opencode": "OpenCode"}
BENCH_ORDER = ["GPQA", "HLE", "AIME"]
BENCH_LABEL = {"GPQA": "GPQA-Diamond", "HLE": "HLE", "AIME": "AIME (Pass@4)"}

KDE_GRID     = 100
KDE_LEVELS   = 12
KDE_LW       = 0.9
KDE_MIN_PTS  = 10


def load_data(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def compute_kde(x: np.ndarray, y: np.ndarray,
                x_lim: tuple[float, float], y_lim: tuple[float, float]):
    if len(x) < KDE_MIN_PTS:
        return None, None, None
    try:
        from scipy.stats import gaussian_kde
        np.random.seed(42)
        x_jitter = x + np.random.uniform(0, 0.005, size=len(x))
        kde = gaussian_kde(np.vstack([x_jitter, y]))
    except Exception:
        return None, None, None
    xi = np.linspace(x_lim[0], x_lim[1], KDE_GRID)
    yi = np.linspace(y_lim[0], y_lim[1], KDE_GRID)
    X, Y = np.meshgrid(xi, yi)
    try:
        Z = kde(np.vstack([X.ravel(), Y.ravel()])).reshape(X.shape)
    except Exception:
        return None, None, None
    return xi, yi, Z


def plot_one_model(model: str, out_name: str) -> None:
    set_academic_style(font_size=14)
    rows_data = load_data(CSV_PATH)

    # Compute per-model global limits
    model_rows = [r for r in rows_data if r["model"] == model]
    all_log_tokens = np.array([np.log10(max(float(r["n_tokens"]), 1)) for r in model_rows])
    all_entropy    = np.array([float(r["mean_entropy"]) for r in model_rows])
    x_lim = (all_log_tokens.min() - 0.1, all_log_tokens.max() + 0.1)
    y_lim = (-0.02, min(1.05, all_entropy.max() + 0.05))

    n_rows = len(BENCH_ORDER)
    n_cols = len(HARNESS_ORDER)

    fig, axes = plt.subplots(n_rows, n_cols, figsize=(22, 14))
    fig.subplots_adjust(hspace=0.12, wspace=0.08, left=0.06, right=0.98, top=0.94, bottom=0.08)

    for bi, benchmark in enumerate(BENCH_ORDER):
        for hi, harness in enumerate(HARNESS_ORDER):
            ax = axes[bi, hi]

            subset = [r for r in rows_data
                      if r["model"] == model
                      and r["benchmark"] == benchmark
                      and r["harness"] == harness]

            # KDE density fills (behind scatter)
            cr = [r for r in subset if r["correct"] == "1"]
            wr = [r for r in subset if r["correct"] == "0"]

            if cr:
                x_c = np.array([np.log10(max(float(r["n_tokens"]), 1)) for r in cr])
                y_c = np.array([float(r["mean_entropy"]) for r in cr])
                xi, yi, Z = compute_kde(x_c, y_c, x_lim, y_lim)
                if Z is not None:
                    ax.contourf(xi, yi, Z, levels=KDE_LEVELS, cmap=CMAP_CORRECT,
                               alpha=KDE_ALPHA)

            if wr:
                x_w = np.array([np.log10(max(float(r["n_tokens"]), 1)) for r in wr])
                y_w = np.array([float(r["mean_entropy"]) for r in wr])
                xi, yi, Z = compute_kde(x_w, y_w, x_lim, y_lim)
                if Z is not None:
                    ax.contourf(xi, yi, Z, levels=KDE_LEVELS, cmap=CMAP_WRONG,
                               alpha=KDE_ALPHA)

            # Scatter points (on top of KDE)
            if cr:
                ax.scatter(x_c, y_c, s=MARKER_SIZE, color=C_CORRECT, marker="o",
                          alpha=ALPHA, edgecolors="none", rasterized=True)
            if wr:
                ax.scatter(x_w, y_w, s=MARKER_SIZE, color=C_WRONG, marker="o",
                          alpha=ALPHA, edgecolors="none", rasterized=True)

            ax.set_xlim(x_lim)
            ax.set_ylim(y_lim)
            ax.grid(True, alpha=0.18)
            ax.tick_params(labelsize=12)

            if hi != 0:
                ax.set_yticklabels([])
            if bi != n_rows - 1:
                ax.set_xticklabels([])

        # Row label
        axes[bi, 0].set_ylabel(BENCH_LABEL[benchmark],
                                fontsize=14, rotation=0, labelpad=28,
                                va="center", ha="right")

    # Column headers
    for hi, harness in enumerate(HARNESS_ORDER):
        fig.text((0.06 + 0.92 * (hi + 0.5) / n_cols), 0.975,
                 HARNESS_LABEL[harness], ha="center", va="top",
                 fontsize=16, fontweight="bold")

    # Shared axis labels
    fig.text(0.012, 0.50, "Mean token entropy (nat)", va="center",
             rotation="vertical", fontsize=16)
    fig.text(0.50, 0.015, "log₁₀ output tokens", ha="center", fontsize=16)

    # Suptitle
    model_display = {"Qwen3.5-27B": "Qwen3.5-27B", "Gemma4-31B": "Gemma4-31B"}
    fig.suptitle(model_display[model], fontsize=18, fontweight="bold", y=0.995)

    out_path = FIG_DIR / out_name
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


def main() -> None:
    plot_one_model("Qwen3.5-27B", "fig_entropy_token_scatter_qwen.pdf")
    plot_one_model("Gemma4-31B",  "fig_entropy_token_scatter_gemma.pdf")


if __name__ == "__main__":
    main()
