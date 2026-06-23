#!/usr/bin/env python3
"""
plot_entropy_token_scatter.py — Combined scatter figure: Qwen (left) + Gemma (right)
side by side. Correct = green, wrong = red.

Produces:
    figures/scatter/fig_entropy_token_scatter_combined.pdf

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
MODEL_ORDER = ["Qwen3.5-27B", "Gemma4-31B"]
MODEL_SHORT = {"Qwen3.5-27B": "Qwen3.5-27B", "Gemma4-31B": "Gemma4-31B"}

ALPHA       = 0.55
MARKER_SIZE = 18


def load_data(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def plot_combined() -> None:
    set_academic_style(font_size=18)
    rows_data = load_data(CSV_PATH)

    # Per-model global limits
    limits = {}
    for model in MODEL_ORDER:
        mr = [r for r in rows_data if r["model"] == model]
        lx = np.array([np.log10(max(float(r["n_tokens"]), 1)) for r in mr])
        ly = np.array([float(r["mean_entropy"]) for r in mr])
        limits[model] = (
            (lx.min() - 0.12, lx.max() + 0.12),
            (-0.03, min(1.05, ly.max() + 0.05)),
        )

    n_bench = len(BENCH_ORDER)
    n_har   = len(HARNESS_ORDER)
    n_cols  = len(HARNESS_ORDER) * len(MODEL_ORDER) + 1

    fig, axes = plt.subplots(n_bench, n_cols, figsize=(20, 10),
                             gridspec_kw={"width_ratios": [1]*4 + [0.08] + [1]*4})
    fig.subplots_adjust(hspace=0.10, wspace=0.08,
                        left=0.08, right=0.99, top=0.90, bottom=0.10)

    for bi, benchmark in enumerate(BENCH_ORDER):
        for mi, model in enumerate(MODEL_ORDER):
            x_lim, y_lim = limits[model]
            col_offset = mi * (n_har + 1)

            for hi, harness in enumerate(HARNESS_ORDER):
                ax = axes[bi, col_offset + hi]

                subset = [r for r in rows_data
                          if r["model"] == model
                          and r["benchmark"] == benchmark
                          and r["harness"] == harness]

                # Wrong first (behind), correct on top
                wr = [r for r in subset if r["correct"] == "0"]
                if wr:
                    x_w = np.array([np.log10(max(float(r["n_tokens"]), 1)) for r in wr])
                    y_w = np.array([float(r["mean_entropy"]) for r in wr])
                    ax.scatter(x_w, y_w, s=MARKER_SIZE, color=C_WRONG, marker="o",
                              alpha=ALPHA, edgecolors="none", rasterized=True, zorder=1)

                cr = [r for r in subset if r["correct"] == "1"]
                if cr:
                    x_c = np.array([np.log10(max(float(r["n_tokens"]), 1)) for r in cr])
                    y_c = np.array([float(r["mean_entropy"]) for r in cr])
                    ax.scatter(x_c, y_c, s=MARKER_SIZE, color=C_CORRECT, marker="o",
                              alpha=ALPHA, edgecolors="none", rasterized=True, zorder=2)

                ax.set_xlim(x_lim)
                ax.set_ylim(y_lim)
                ax.grid(True, alpha=0.20)
                ax.tick_params(labelsize=16)

                # Y ticks: only leftmost column of each model half
                is_leftmost_qwen  = (mi == 0 and hi == 0)
                is_leftmost_gemma = (mi == 1 and hi == 0)
                if not (is_leftmost_qwen or is_leftmost_gemma):
                    ax.set_yticklabels([])

                # X ticks: only bottom row
                if bi != n_bench - 1:
                    ax.set_xticklabels([])

        # Row labels (benchmark) — left of Qwen columns, vertical
        axes[bi, 0].set_ylabel(BENCH_LABEL[benchmark],
                                fontsize=20, rotation=90, labelpad=14,
                                va="center", ha="center")

        # Hide gap column
        axes[bi, n_har].set_visible(False)

    # Column headers — harness names, positioned per half
    for mi, model in enumerate(MODEL_ORDER):
        col_offset = mi * (n_har + 1)
        for hi, harness in enumerate(HARNESS_ORDER):
            # Fractional x positions
            if mi == 0:
                x = 0.08 + (0.43) * (hi + 0.5) / 4
            else:
                x = 0.55 + (0.44) * (hi + 0.5) / 4
            fig.text(x, 0.95, HARNESS_LABEL[harness], ha="center", va="top",
                    fontsize=20, fontweight="bold")

    # Model labels centered over each half
    fig.text(0.295, 0.985, MODEL_SHORT[MODEL_ORDER[0]], ha="center", va="top",
             fontsize=22, fontweight="bold")
    fig.text(0.770, 0.985, MODEL_SHORT[MODEL_ORDER[1]], ha="center", va="top",
             fontsize=22, fontweight="bold")

    # Shared Y label (left edge, vertical)
    fig.text(0.018, 0.50, "Mean token entropy (nat)", va="center",
             rotation="vertical", fontsize=20)
    # Shared X label (bottom, horizontal)
    fig.text(0.50, 0.018, "log₁₀ output tokens", ha="center", fontsize=20)

    out_path = FIG_DIR / "fig_entropy_token_scatter_combined.pdf"
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


def main() -> None:
    plot_combined()


if __name__ == "__main__":
    main()
