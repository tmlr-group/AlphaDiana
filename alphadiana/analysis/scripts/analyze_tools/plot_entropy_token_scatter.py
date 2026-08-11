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
import os
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

SKILL_DIR = os.environ.get("ALPHADIANA_ACADEMIC_PLOT_DIR", "").strip()
if SKILL_DIR and SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)

from academic_plot import set_academic_style

FIG_DIR = Path(__file__).parent / "figures" / "scatter"
FIG_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = Path(__file__).parent / "data"
CSV_PATH = DATA_DIR / "entropy_token_scatter.csv"

C_CORRECT = "#2ca02c"
C_WRONG   = "#d62728"

CMAP_CORRECT = LinearSegmentedColormap.from_list("kde_correct",
    [(1, 1, 1), (0.75, 0.93, 0.75), (0.40, 0.83, 0.40), (0.17, 0.63, 0.17)])
CMAP_WRONG = LinearSegmentedColormap.from_list("kde_wrong",
    [(1, 1, 1), (0.96, 0.75, 0.75), (0.88, 0.40, 0.40), (0.84, 0.15, 0.16)])

HARNESS_ORDER = ["directllm", "openclaw", "zeroclaw", "opencode"]
HARNESS_LABEL = {"directllm": "DirectLLM", "openclaw": "OpenClaw",
                 "zeroclaw": "ZeroClaw", "opencode": "OpenCode"}
BENCH_ORDER = ["GPQA", "HLE", "AIME"]
BENCH_LABEL = {"GPQA": "GPQA-Diamond", "HLE": "HLE", "AIME": "AIME (Pass@4)"}

KDE_GRID     = 100
KDE_LEVELS   = 10
KDE_ALPHA    = 0.45
KDE_LW       = 0.7
KDE_MIN_PTS  = 10
HPD_MASS     = 0.85   # fraction of probability mass to retain (trims low-density tails)


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


def clip_kde_tails(Z: np.ndarray, mass: float = HPD_MASS) -> np.ndarray:
    """Mask low-density tails: keep only the region containing `mass` of probability.
    Returns Z with below-threshold values set to NaN."""
    z_flat = Z.ravel()
    order = np.argsort(z_flat)[::-1]
    z_sorted = z_flat[order]
    cumsum = np.cumsum(z_sorted) / z_sorted.sum()
    idx = int(np.searchsorted(cumsum, mass, side="left"))
    idx = min(idx, len(z_sorted) - 1)
    threshold = z_sorted[idx]
    Zc = Z.copy()
    Zc[Zc < threshold] = np.nan
    return Zc


def plot_one_model(model: str, out_name: str) -> None:
    set_academic_style(font_size=18)
    rows_data = load_data(CSV_PATH)

    # Compute per-model global limits
    model_rows = [r for r in rows_data if r["model"] == model]
    all_log_tokens = np.array([np.log10(max(float(r["n_tokens"]), 1)) for r in model_rows])
    all_entropy    = np.array([float(r["mean_entropy"]) for r in model_rows])
    x_lim = (all_log_tokens.min() - 0.1, all_log_tokens.max() + 0.1)
    y_lim = (-0.02, min(1.05, all_entropy.max() + 0.05))

    n_rows = len(BENCH_ORDER)
    n_cols = len(HARNESS_ORDER)

    fig, axes = plt.subplots(n_rows, n_cols, figsize=(16, 20))
    fig.subplots_adjust(hspace=0.15, wspace=0.10, left=0.08, right=0.98, top=0.94, bottom=0.10)

    for bi, benchmark in enumerate(BENCH_ORDER):
        for hi, harness in enumerate(HARNESS_ORDER):
            ax = axes[bi, hi]

            subset = [r for r in rows_data
                      if r["model"] == model
                      and r["benchmark"] == benchmark
                      and r["harness"] == harness]

            cr = [r for r in subset if r["correct"] == "1"]
            wr = [r for r in subset if r["correct"] == "0"]

            if cr:
                x_c = np.array([np.log10(max(float(r["n_tokens"]), 1)) for r in cr])
                y_c = np.array([float(r["mean_entropy"]) for r in cr])
                xi, yi, Z = compute_kde(x_c, y_c, x_lim, y_lim)
                if Z is not None:
                    Zc = clip_kde_tails(Z)
                    ax.contourf(xi, yi, Zc, levels=KDE_LEVELS, cmap=CMAP_CORRECT,
                               alpha=KDE_ALPHA)
                    ax.contour(xi, yi, Zc, levels=KDE_LEVELS, colors=C_CORRECT,
                              linewidths=KDE_LW, alpha=0.80)

            if wr:
                x_w = np.array([np.log10(max(float(r["n_tokens"]), 1)) for r in wr])
                y_w = np.array([float(r["mean_entropy"]) for r in wr])
                xi, yi, Z = compute_kde(x_w, y_w, x_lim, y_lim)
                if Z is not None:
                    Zc = clip_kde_tails(Z)
                    ax.contourf(xi, yi, Zc, levels=KDE_LEVELS, cmap=CMAP_WRONG,
                               alpha=KDE_ALPHA)
                    ax.contour(xi, yi, Zc, levels=KDE_LEVELS, colors=C_WRONG,
                              linewidths=KDE_LW, alpha=0.80)

            ax.set_xlim(x_lim)
            ax.set_ylim(y_lim)
            ax.grid(True, alpha=0.18)
            ax.tick_params(labelsize=16)

            if hi != 0:
                ax.set_yticklabels([])
            if bi != n_rows - 1:
                ax.set_xticklabels([])

        # Row label (vertical)
        axes[bi, 0].set_ylabel(BENCH_LABEL[benchmark],
                                fontsize=18, rotation=90, labelpad=18,
                                va="center", ha="center")

    # Column headers
    for hi, harness in enumerate(HARNESS_ORDER):
        fig.text((0.08 + 0.90 * (hi + 0.5) / n_cols), 0.975,
                 HARNESS_LABEL[harness], ha="center", va="top",
                 fontsize=20, fontweight="bold")

    # Shared axis labels
    fig.text(0.018, 0.50, "Mean token entropy (nat)", va="center",
             rotation="vertical", fontsize=20)
    fig.text(0.50, 0.025, "log₁₀ output tokens", ha="center", fontsize=20)

    out_path = FIG_DIR / out_name
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


def main() -> None:
    plot_one_model("Qwen3.5-27B", "fig_entropy_token_scatter_qwen.pdf")
    plot_one_model("Gemma4-31B",  "fig_entropy_token_scatter_gemma.pdf")


if __name__ == "__main__":
    main()
