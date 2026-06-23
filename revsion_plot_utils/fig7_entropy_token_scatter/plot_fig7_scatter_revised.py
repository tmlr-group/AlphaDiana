#!/usr/bin/env python3
"""Fig 7 (v2) — entropy vs output-length scatter, conference two-column layout.

Keep SCATTER, conference two-column layout (Qwen block left, Gemma block right). Per
follow-up the auxiliary trend/guide lines are removed; the correct-vs-wrong
difference is now shown with a covariance ellipse (2 std) plus a centroid marker per
outcome, so the location and spread of each cloud (and their separation) read
without any line. Keeps the green/red theme. No in-figure legend (key in caption).

Run:    python3 plot_fig7_scatter_revised.py
Reads:  data/entropy_token_scatter.csv
Writes: revised/fig7_entropy_token_scatter.{pdf,png,svg}
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from _style import apply_publication_style, finalize_figure, C_CORRECT, C_WRONG

CSV_PATH = HERE / "data" / "entropy_token_scatter.csv"
OUT = HERE / "revised" / "fig7_entropy_token_scatter"

MODEL_ORDER = ["Qwen3.5-27B", "Gemma4-31B"]
BENCH_ORDER = ["GPQA", "HLE", "AIME"]
BENCH_LABEL = {"GPQA": "GPQA-Diamond", "HLE": "HLE", "AIME": "AIME"}
HARNESS_ORDER = ["directllm", "openclaw", "zeroclaw", "opencode"]
HARNESS_LABEL = {"directllm": "DirectLLM", "openclaw": "OpenClaw",
                 "zeroclaw": "ZeroClaw", "opencode": "OpenCode"}
MARKER_S = 5
ALPHA = 0.30
N_STD = 2.0          # ellipse size (~95% for a Gaussian)
MIN_ELLIPSE = 10


def load(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def pts(rows, model, bench, harness, correct):
    x, y = [], []
    for r in rows:
        if (r["model"] == model and r["benchmark"] == bench
                and r["harness"] == harness and r["correct"] == correct):
            n = float(r["n_tokens"]); e = float(r["mean_entropy"])
            if n > 0 and np.isfinite(e):
                x.append(np.log10(n)); y.append(e)
    return np.asarray(x), np.asarray(y)


def conf_ellipse(ax, x, y, color):
    """2-std covariance ellipse + centroid for one outcome cloud."""
    if len(x) < MIN_ELLIPSE:
        return
    from matplotlib.patches import Ellipse
    cov = np.cov(x, y)
    if not np.all(np.isfinite(cov)):
        return
    vals, vecs = np.linalg.eigh(cov)
    order = vals.argsort()[::-1]
    vals, vecs = vals[order], vecs[:, order]
    angle = np.degrees(np.arctan2(vecs[1, 0], vecs[0, 0]))
    w, h = 2 * N_STD * np.sqrt(np.maximum(vals, 0))
    cx, cy = x.mean(), y.mean()
    ax.add_patch(Ellipse((cx, cy), w, h, angle=angle, facecolor=color,
                         edgecolor=color, lw=1.5, alpha=0.12, zorder=4))
    ax.add_patch(Ellipse((cx, cy), w, h, angle=angle, facecolor="none",
                         edgecolor=color, lw=1.5, zorder=5))
    ax.plot(cx, cy, marker="o", color=color, ms=5, mec="white", mew=0.6, zorder=6)


def global_limits(rows):
    """One shared scale for every panel so left (Qwen) and right (Gemma) align."""
    lx = [np.log10(float(r["n_tokens"])) for r in rows if float(r["n_tokens"]) > 0]
    ly = [float(r["mean_entropy"]) for r in rows
          if np.isfinite(float(r["mean_entropy"]))]
    return ((min(lx) - 0.15, max(lx) + 0.15),
            (-0.02, min(1.02, max(ly) + 0.05)))


def plot_one(model, rows, x_lim, y_lim, slug):
    """One figure per model: bench rows x harness cols (shared global scale)."""
    apply_publication_style(font_size=9, axes_linewidth=0.7)
    nb, nh = len(BENCH_ORDER), len(HARNESS_ORDER)
    fig, axes = plt.subplots(nb, nh, figsize=(7.8, 5.6))
    fig.subplots_adjust(left=0.155, right=0.99, top=0.905, bottom=0.115,
                        hspace=0.10, wspace=0.08)
    for bi, bench in enumerate(BENCH_ORDER):
        for hi, harness in enumerate(HARNESS_ORDER):
            ax = axes[bi, hi]
            xw, yw = pts(rows, model, bench, harness, "0")
            ax.scatter(xw, yw, s=MARKER_S, color=C_WRONG, alpha=ALPHA,
                       edgecolors="none", rasterized=True, zorder=1)
            xc, yc = pts(rows, model, bench, harness, "1")
            ax.scatter(xc, yc, s=MARKER_S, color=C_CORRECT, alpha=ALPHA,
                       edgecolors="none", rasterized=True, zorder=2)
            conf_ellipse(ax, xw, yw, C_WRONG)
            conf_ellipse(ax, xc, yc, C_CORRECT)
            ax.set_xlim(x_lim); ax.set_ylim(y_lim)
            ax.grid(True, alpha=0.15, lw=0.4)
            ax.tick_params(labelsize=8)
            if bi == 0:
                ax.set_title(HARNESS_LABEL[harness], fontsize=10, fontweight="bold")
            if hi != 0:
                ax.set_yticklabels([])
            if bi != nb - 1:
                ax.set_xticklabels([])
        pos = axes[bi, 0].get_position()        # benchmark row label
        fig.text(0.085, (pos.y0 + pos.y1) / 2, BENCH_LABEL[bench], rotation=90,
                 va="center", ha="center", fontsize=11, fontweight="bold")
    fig.text(0.032, 0.51, "Mean token entropy (nat)", va="center",
             rotation="vertical", fontsize=11)
    fig.text(0.57, 0.03, r"$\log_{10}$ output tokens", ha="center", fontsize=11)
    fig.suptitle(model, fontsize=14, fontweight="bold", y=0.975)
    saved = finalize_figure(fig, OUT.parent / f"fig7_entropy_token_scatter_{slug}", dpi=400)
    for p in saved:
        print("wrote", p)


def main():
    rows = load(CSV_PATH)
    x_lim, y_lim = global_limits(rows)
    for model in MODEL_ORDER:
        slug = "qwen" if "Qwen" in model else "gemma"
        plot_one(model, rows, x_lim, y_lim, slug)


if __name__ == "__main__":
    main()
