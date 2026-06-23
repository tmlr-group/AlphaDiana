#!/usr/bin/env python3
"""Fig 5 (v2) — HLE action transitions as stacked next-action bars.

Per version-1 notes the magma P(to|from) heatmap was hard to read (color scheme +
matrix framing). This version shows, for each source action, the composition of the
NEXT action as a stacked bar colored by destination action, reusing the shared
6-action palette (same color language as Fig 4). Reading: "from Reasoning, the bar
is mostly green (stays in Reasoning), with orange (-> Verification) and purple
(-> Tool Use)". Genuinely absent source actions (no outgoing transitions, e.g. Tool
Use under DirectLLM/ZeroClaw) are drawn as a hatched "absent" bar, not fabricated.

Faceted rows = model, cols = harness, aggregated over outcomes, for HLE.
No in-figure legend (destination color = the 6-action palette; key in caption).

Run:    python3 plot_fig5_stacked_revised.py
Reads:  data/action_transitions_by_outcome_{qwen,gemma}.csv
Writes: revised/fig5_hle_next_action_bars.{pdf,png,svg}
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
from _style import (apply_publication_style, add_panel_label, finalize_figure,
                    ACTION_LABELS, ACTION_COLORS, OLD_TO_NEW)

DATA = HERE / "data"
OUT = HERE / "revised" / "fig5_hle_next_action_bars"
BENCH = "HLE"
SHORT = ["Understand", "Plan", "Reason", "Tool", "Verify", "Finalize"]
IDX = {a: i for i, a in enumerate(ACTION_LABELS)}
MODELS = [("Qwen3.5-27B", "action_transitions_by_outcome_qwen.csv"),
          ("Gemma4-31B", "action_transitions_by_outcome_gemma.csv")]
HARNESS_ORDER = ["DirectLLM", "OpenClaw", "OpenCode", "ZeroClaw"]


def counts(path: Path, harness: str) -> np.ndarray:
    mat = np.zeros((6, 6))
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["benchmark"] != BENCH or r["harness"] != harness:
                continue
            fa = OLD_TO_NEW.get(r["from_action"]); ta = OLD_TO_NEW.get(r["to_action"])
            if fa in IDX and ta in IDX:
                mat[IDX[fa], IDX[ta]] += float(r["transition_count"])
    return mat


def draw(ax, mat):
    rsum = mat.sum(1)
    xs = np.arange(6)
    for i in xs:
        if rsum[i] <= 0:                      # absent source action -> hatched stub
            ax.bar(i, 1.0, width=0.8, facecolor="0.93", edgecolor="0.6",
                   hatch="////", lw=0.4)
            continue
        bottom = 0.0
        for d, action in enumerate(ACTION_LABELS):
            frac = mat[i, d] / rsum[i]
            if frac <= 0:
                continue
            ax.bar(i, frac, width=0.8, bottom=bottom,
                   color=ACTION_COLORS[action], edgecolor="white", lw=0.4)
            if frac >= 0.14:
                lum = sum(int(ACTION_COLORS[action][k:k+2], 16) for k in (1, 3, 5)) / 3
                ax.text(i, bottom + frac / 2, f"{frac*100:.0f}", ha="center",
                        va="center", fontsize=5.4,
                        color="white" if lum < 140 else "black")
            bottom += frac
    ax.set_xlim(-0.6, 5.6)
    ax.set_ylim(0, 1)
    ax.set_xticks(xs)
    ax.set_yticks([0, 0.5, 1.0])


def main():
    """Flat conference layout: 1 row x two model blocks side by side (HLE only)."""
    apply_publication_style(font_size=8, axes_linewidth=0.7)
    nh = len(HARNESS_ORDER)
    ncol = nh * 2 + 1
    fig, axes = plt.subplots(1, ncol, figsize=(13.4, 3.5),
                             gridspec_kw={"width_ratios": [1] * nh + [0.16] + [1] * nh})
    fig.subplots_adjust(left=0.055, right=0.995, top=0.78, bottom=0.27, wspace=0.12)
    panel = iter("abcdefgh")
    for mi, (model, fn) in enumerate(MODELS):
        path = DATA / fn
        base = mi * (nh + 1)
        for hi, harness in enumerate(HARNESS_ORDER):
            ax = axes[base + hi]
            draw(ax, counts(path, harness))
            add_panel_label(ax, next(panel), x=-0.07, y=1.04, fontsize=8)
            ax.set_title(harness, fontsize=10, fontweight="bold", pad=5)
            ax.set_xticklabels(SHORT, rotation=45, ha="right", fontsize=6)
            for t, a in zip(ax.get_xticklabels(), ACTION_LABELS):
                t.set_color(ACTION_COLORS[a])
            if hi == 0:
                ax.set_ylabel("Next-action share", fontsize=8.5)
            else:
                ax.set_yticklabels([])
            ax.tick_params(length=0)
    axes[nh].set_visible(False)              # gap column

    for mi, (model, _) in enumerate(MODELS):
        left = axes[mi * (nh + 1)].get_position().x0
        right = axes[mi * (nh + 1) + nh - 1].get_position().x1
        fig.text((left + right) / 2, 0.93, model, ha="center", va="bottom",
                 fontsize=12, fontweight="bold")
    fig.text(0.53, 0.015, "source action  (HLE;  segment color = next action)",
             ha="center", fontsize=9)
    saved = finalize_figure(fig, OUT, dpi=300)
    for p in saved:
        print("wrote", p)


if __name__ == "__main__":
    main()
