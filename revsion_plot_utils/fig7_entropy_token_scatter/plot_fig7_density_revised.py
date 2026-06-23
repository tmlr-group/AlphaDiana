#!/usr/bin/env python3
"""Fig 7 (revised) — entropy vs output-length as 2D KDE density contours.

Replaces the overplotted combined scatter
(`original/fig_entropy_token_scatter_combined.pdf`) with overlaid correct/wrong
density contours, so the low-entropy x long-output "confident collapse" region is
legible. Keeps the current green/red outcome theme. No in-figure legend (the
correct/wrong key goes in the caption).

Layout: two stacked model blocks (Qwen on top, Gemma below), each a
benchmark (row) x harness (col) grid, so panels are larger than the old 3x8.

Run:
    python3 plot_fig7_density_revised.py
Reads:  data/entropy_token_scatter.csv
Writes: revised/fig7_entropy_token_density.{pdf,png,svg}
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import gaussian_kde

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))   # revsion_plot_utils/ for _style
from _style import (apply_publication_style, add_panel_label, finalize_figure,
                    graded_cmap, C_CORRECT, C_WRONG)

CSV_PATH = HERE / "data" / "entropy_token_scatter.csv"
OUT = HERE / "revised" / "fig7_entropy_token_density"

MODEL_ORDER = ["Qwen3.5-27B", "Gemma4-31B"]
BENCH_ORDER = ["GPQA", "HLE", "AIME"]
BENCH_LABEL = {"GPQA": "GPQA-Diamond", "HLE": "HLE", "AIME": "AIME (Pass@4)"}
HARNESS_ORDER = ["directllm", "openclaw", "zeroclaw", "opencode"]
HARNESS_LABEL = {"directllm": "DirectLLM", "openclaw": "OpenClaw",
                 "zeroclaw": "ZeroClaw", "opencode": "OpenCode"}

KDE_GRID = 120
KDE_LEVELS = 8
HPD_MASS = 0.85         # keep the densest 85% of mass, NaN the rest (no bg bleed)
KDE_MIN_PTS = 12
FILL_ALPHA = 0.55
LINE_ALPHA = 0.85
CMAP_CORRECT = graded_cmap(C_CORRECT, "kde_correct")
CMAP_WRONG = graded_cmap(C_WRONG, "kde_wrong")


def load_rows(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def xy(rows, model, bench, harness, correct):
    xs, ys = [], []
    for r in rows:
        if (r["model"] == model and r["benchmark"] == bench
                and r["harness"] == harness and r["correct"] == correct):
            n = float(r["n_tokens"])
            e = float(r["mean_entropy"])
            if n > 0 and np.isfinite(e):
                xs.append(np.log10(n))
                ys.append(e)
    return np.asarray(xs), np.asarray(ys)


def clip_kde_tails(Z: np.ndarray, mass: float = HPD_MASS) -> np.ndarray:
    """NaN the low-density background, keeping the densest `mass` of probability."""
    flat = Z.ravel()
    order = np.argsort(flat)[::-1]
    z_sorted = flat[order]
    csum = np.cumsum(z_sorted) / z_sorted.sum()
    thr = z_sorted[min(int(np.searchsorted(csum, mass)), len(z_sorted) - 1)]
    out = Z.copy()
    out[out < thr] = np.nan
    return out


def kde_grid(x, y, x_lim, y_lim):
    if len(x) < KDE_MIN_PTS:
        return None
    rng = np.random.default_rng(42)
    xj = x + rng.uniform(0, 0.005, size=len(x))   # break degenerate covariances
    try:
        kde = gaussian_kde(np.vstack([xj, y]))
    except Exception:
        return None
    xi = np.linspace(*x_lim, KDE_GRID)
    yi = np.linspace(*y_lim, KDE_GRID)
    X, Y = np.meshgrid(xi, yi)
    Z = kde(np.vstack([X.ravel(), Y.ravel()])).reshape(X.shape)
    return xi, yi, Z


def draw_density(ax, x, y, x_lim, y_lim, cmap):
    g = kde_grid(x, y, x_lim, y_lim)
    if g is None:
        if len(x):                                 # too few points: light scatter
            ax.scatter(x, y, s=4, color=cmap(0.9), alpha=0.5, edgecolors="none")
        return
    xi, yi, Z = g
    Zc = clip_kde_tails(Z)
    ax.contourf(xi, yi, Zc, levels=KDE_LEVELS, cmap=cmap, alpha=FILL_ALPHA)
    ax.contour(xi, yi, Zc, levels=KDE_LEVELS, colors=[cmap(0.95)],
               linewidths=0.4, alpha=LINE_ALPHA)


def model_limits(rows, model):
    lx = [np.log10(float(r["n_tokens"])) for r in rows
          if r["model"] == model and float(r["n_tokens"]) > 0]
    ly = [float(r["mean_entropy"]) for r in rows
          if r["model"] == model and np.isfinite(float(r["mean_entropy"]))]
    return ((min(lx) - 0.15, max(lx) + 0.15),
            (-0.02, min(1.02, max(ly) + 0.05)))


def main():
    apply_publication_style(font_size=8, axes_linewidth=0.8)
    rows = load_rows(CSV_PATH)
    limits = {m: model_limits(rows, m) for m in MODEL_ORDER}

    nb, nh = len(BENCH_ORDER), len(HARNESS_ORDER)
    nrows = len(MODEL_ORDER) * nb
    fig, axes = plt.subplots(nrows, nh, figsize=(8.6, 12.8))
    fig.subplots_adjust(left=0.115, right=0.985, top=0.925, bottom=0.055,
                        hspace=0.16, wspace=0.10)

    panel = iter("abcdefghijklmnopqrstuvwx")
    for mi, model in enumerate(MODEL_ORDER):
        x_lim, y_lim = limits[model]
        for bi, bench in enumerate(BENCH_ORDER):
            r = mi * nb + bi
            for hi, harness in enumerate(HARNESS_ORDER):
                ax = axes[r, hi]
                # low-entropy guide band (the "confident" axis of the claim)
                ax.axhspan(y_lim[0], 0.15, color="0.5", alpha=0.05, lw=0)
                xw, yw = xy(rows, model, bench, harness, "0")   # wrong behind
                draw_density(ax, xw, yw, x_lim, y_lim, CMAP_WRONG)
                xc, yc = xy(rows, model, bench, harness, "1")   # correct on top
                draw_density(ax, xc, yc, x_lim, y_lim, CMAP_CORRECT)

                ax.set_xlim(x_lim)
                ax.set_ylim(y_lim)
                ax.grid(True, alpha=0.18, lw=0.5)
                add_panel_label(ax, next(panel), x=-0.02, y=1.0, fontsize=8)

                if r == mi * nb:                                # block header
                    ax.set_title(HARNESS_LABEL[harness], fontsize=10,
                                 fontweight="bold", pad=4)
                if hi != 0:
                    ax.set_yticklabels([])
                if bi != nb - 1:
                    ax.set_xticklabels([])
            # benchmark row label
            axes[r, 0].set_ylabel(BENCH_LABEL[bench], fontsize=9)

        # model block label: horizontal, centered above the block's harness headers
        y_block_top = axes[mi * nb, 0].get_position().y1
        fig.text(0.55, y_block_top + 0.028, model, ha="center", va="bottom",
                 fontsize=13, fontweight="bold")

    fig.text(0.55, 0.022, r"$\log_{10}$ output tokens", ha="center", fontsize=11)
    fig.text(0.028, 0.5, "Mean token entropy (nat)", va="center",
             rotation="vertical", fontsize=11)

    saved = finalize_figure(fig, OUT, dpi=300)
    for p in saved:
        print("wrote", p)


if __name__ == "__main__":
    main()
