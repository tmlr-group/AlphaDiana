#!/usr/bin/env python3
"""Fig 5 (revised) — HLE action transitions as row-normalized heatmaps.

Replaces the 24 chord PDFs with a single small-multiple grid of 6x6 transition
matrices P(to | from), rows = model, cols = harness, aggregated over outcomes.
This is precise, directly comparable across harnesses, and avoids the chord's
problems: the Reasoning self-loop no longer swamps the picture (PowerNorm gives
the off-diagonal signal color range, and every cell is annotated), and genuinely
absent actions (e.g. Tool Use under DirectLLM/ZeroClaw) are shown as honest grey
"absent" cells instead of pad_matrix's fabricated micro-edges.

Keeps the project's 6-action palette as the axis tick color key. No in-figure
legend (the action key + the P(to|from) definition go in the caption).

Run:    python3 plot_fig5_transition_heatmap_revised.py
Reads:  data/action_transitions_by_outcome_{qwen,gemma}.csv
Writes: revised/fig5_hle_transition_heatmaps.{pdf,png,svg}
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import PowerNorm

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from _style import (apply_publication_style, add_panel_label, finalize_figure,
                    ACTION_LABELS, ACTION_COLORS, OLD_TO_NEW)

DATA = HERE / "data"
OUT = HERE / "revised" / "fig5_hle_transition_heatmaps"
BENCH = "HLE"
SHORT = ["Understand", "Plan", "Reason", "Tool", "Verify", "Finalize"]
IDX = {a: i for i, a in enumerate(ACTION_LABELS)}
MODELS = [("Qwen3.5-27B", "action_transitions_by_outcome_qwen.csv"),
          ("Gemma4-31B", "action_transitions_by_outcome_gemma.csv")]
HARNESS_ORDER = ["DirectLLM", "OpenClaw", "OpenCode", "ZeroClaw"]
CMAP = plt.get_cmap("magma")
CMAP.set_bad("0.88")          # absent cells
NORM = PowerNorm(gamma=0.45, vmin=0, vmax=1)


def counts(path: Path, harness: str) -> np.ndarray:
    """6x6 transition counts for HLE/<harness>, summed over outcomes."""
    mat = np.zeros((6, 6))
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["benchmark"] != BENCH or r["harness"] != harness:
                continue
            fa = OLD_TO_NEW.get(r["from_action"]); ta = OLD_TO_NEW.get(r["to_action"])
            if fa in IDX and ta in IDX:
                mat[IDX[fa], IDX[ta]] += float(r["transition_count"])
    return mat


def row_normalize(mat: np.ndarray):
    """P(to|from); NaN out absent actions (no outgoing AND no incoming)."""
    P = np.full((6, 6), np.nan)
    rsum = mat.sum(1)
    csum = mat.sum(0)
    absent = (rsum == 0) & (csum == 0)
    for i in range(6):
        if rsum[i] > 0:
            P[i] = mat[i] / rsum[i]
    # blank out absent actions entirely (row + column) so they read as "never used"
    for k in range(6):
        if absent[k]:
            P[k, :] = np.nan
            P[:, k] = np.nan
    return np.ma.masked_invalid(P), absent


def draw(ax, P, absent):
    im = ax.imshow(P, cmap=CMAP, norm=NORM, aspect="equal")
    for i in range(6):
        for j in range(6):
            v = P[i, j]
            if np.ma.is_masked(v):
                if absent[i] or absent[j]:
                    ax.text(j, i, "·", ha="center", va="center",
                            fontsize=7, color="0.6")
                continue
            ax.text(j, i, f"{v*100:.0f}", ha="center", va="center", fontsize=5.4,
                    color="white" if NORM(v) > 0.5 else "0.15")
    ax.set_xticks(range(6)); ax.set_yticks(range(6))
    return im


def main():
    apply_publication_style(font_size=8, axes_linewidth=0.6)
    nm, nh = len(MODELS), len(HARNESS_ORDER)
    fig, axes = plt.subplots(nm, nh, figsize=(11.2, 6.2))
    fig.subplots_adjust(left=0.10, right=0.90, top=0.88, bottom=0.16,
                        hspace=0.18, wspace=0.16)
    panel = iter("abcdefgh")
    im = None
    for mi, (model, fn) in enumerate(MODELS):
        path = DATA / fn
        for hi, harness in enumerate(HARNESS_ORDER):
            ax = axes[mi, hi]
            P, absent = row_normalize(counts(path, harness))
            im = draw(ax, P, absent)
            add_panel_label(ax, next(panel), x=-0.10, y=1.03, fontsize=9)
            if mi == 0:
                ax.set_title(harness, fontsize=11, fontweight="bold", pad=6)
            # x tick labels only on bottom row, colored by action
            if mi == nm - 1:
                ax.set_xticklabels(SHORT, rotation=45, ha="right", fontsize=6.5)
                for t, a in zip(ax.get_xticklabels(), ACTION_LABELS):
                    t.set_color(ACTION_COLORS[a])
            else:
                ax.set_xticklabels([])
            # y tick labels only on left col, colored by action
            if hi == 0:
                ax.set_yticklabels(SHORT, fontsize=6.5)
                for t, a in zip(ax.get_yticklabels(), ACTION_LABELS):
                    t.set_color(ACTION_COLORS[a])
            else:
                ax.set_yticklabels([])
            ax.tick_params(length=0)
        # model row label
        axes[mi, 0].text(-0.42, 0.5, model, transform=axes[mi, 0].transAxes,
                         rotation=90, va="center", ha="center",
                         fontsize=11, fontweight="bold")

    fig.text(0.5, 0.055, "to action", ha="center", fontsize=10)
    fig.text(0.045, 0.52, "from action", va="center", rotation="vertical", fontsize=10)
    cax = fig.add_axes([0.915, 0.16, 0.014, 0.72])
    cb = fig.colorbar(im, cax=cax)
    cb.set_label("P(to | from)", fontsize=9)
    cb.ax.tick_params(labelsize=7)

    saved = finalize_figure(fig, OUT, dpi=300)
    for p in saved:
        print("wrote", p)


if __name__ == "__main__":
    main()
