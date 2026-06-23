#!/usr/bin/env python3
"""Fig 5 (v3) — HLE action transitions as per-harness Sankey flow diagrams.

Full revision (chord and heatmap/stacked-bar versions did not convey the transition
meaning directly). Each panel is a bipartite Sankey: left column = source action,
right column = next action, ribbons sized by transition frequency and colored by the
source action (shared 6-action palette). Reading is literal: a thick Reason->Reason
band = the agent persists in reasoning; branches to Verify / Tool show where it goes.
Genuinely absent actions (e.g. Tool under DirectLLM/ZeroClaw) simply have no node.

Flat conference layout: 1 row x two model blocks side by side (Qwen | gap | Gemma),
harness columns, HLE only. No in-figure legend (node color = action; key in caption).

Run:    python3 plot_fig5_sankey_revised.py
Reads:  data/action_transitions_by_outcome_{qwen,gemma}.csv
Writes: revised/fig5_hle_sankey.{pdf,png,svg}
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
import matplotlib.patches as patches

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from _style import (apply_publication_style, finalize_figure,
                    ACTION_LABELS, ACTION_COLORS, OLD_TO_NEW, C_CORRECT, C_WRONG)

DATA = HERE / "data"
OUT = HERE / "revised" / "fig5_hle_sankey"
BENCH = "HLE"
SHORT = ["Understand", "Plan", "Reason", "Tool", "Verify", "Finalize"]
IDX = {a: i for i, a in enumerate(ACTION_LABELS)}
COLORS = [ACTION_COLORS[a] for a in ACTION_LABELS]
MODELS = [("Qwen3.5-27B", "action_transitions_by_outcome_qwen.csv"),
          ("Gemma4-31B", "action_transitions_by_outcome_gemma.csv")]
HARNESS_ORDER = ["DirectLLM", "OpenClaw", "OpenCode", "ZeroClaw"]

X_LNODE = (0.00, 0.06)      # left node band
X_RNODE = (0.94, 1.00)      # right node band
XL, XR = X_LNODE[1], X_RNODE[0]
GAP = 0.025                 # vertical gap between nodes (axes fraction)
MIN_RIBBON = 0.0015         # skip ribbons thinner than this (axes fraction)


def counts(path: Path, harness: str, outcomes: set) -> np.ndarray:
    mat = np.zeros((6, 6))
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["benchmark"] != BENCH or r["harness"] != harness:
                continue
            if r["outcome"] not in outcomes:
                continue
            fa = OLD_TO_NEW.get(r["from_action"]); ta = OLD_TO_NEW.get(r["to_action"])
            if fa in IDX and ta in IDX:
                mat[IDX[fa], IDX[ta]] += float(r["transition_count"])
    return mat


# correct = success; wrong = failure + unknown (matches Fig 4/6)
OUTCOMES = [("Correct", {"success"}, C_CORRECT),
            ("Wrong", {"failure", "unknown"}, C_WRONG)]


def stack(weights, s):
    """Return {action_idx: (y_top, height)} for present actions, vertically centered."""
    present = [i for i in range(6) if weights[i] > 0]
    heights = {i: weights[i] * s for i in present}
    total = sum(heights.values()) + GAP * max(0, len(present) - 1)
    y_top = (1 + total) / 2
    nodes = {}
    for i in present:
        nodes[i] = (y_top, heights[i])
        y_top -= heights[i] + GAP
    return nodes


def ribbon(ax, yL_hi, yL_lo, yR_hi, yR_lo, color):
    mid = (XL + XR) / 2
    verts = [(XL, yL_hi), (mid, yL_hi), (mid, yR_hi), (XR, yR_hi),
             (XR, yR_lo), (mid, yR_lo), (mid, yL_lo), (XL, yL_lo), (XL, yL_hi)]
    codes = [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
             MplPath.LINETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
             MplPath.CLOSEPOLY]
    ax.add_patch(patches.PathPatch(MplPath(verts, codes), facecolor=color,
                                   edgecolor="none", alpha=0.62, lw=0))


def draw_sankey(ax, mat):
    T = mat.sum()
    if T <= 0:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", fontsize=8, color="0.5")
        ax.axis("off"); return
    s = (1 - GAP * 5) / T                      # same vertical scale both sides
    lnodes = stack(mat.sum(1), s)              # by outgoing total
    rnodes = stack(mat.sum(0), s)              # by incoming total

    # sub-slice cursors (descend from each node's top)
    lcur = {i: lnodes[i][0] for i in lnodes}
    rcur = {j: rnodes[j][0] for j in rnodes}
    for i in range(6):                         # source-major, target order within
        for j in range(6):
            h = mat[i, j] * s
            if h <= 0:
                continue
            yL_hi, yL_lo = lcur[i], lcur[i] - h
            yR_hi, yR_lo = rcur[j], rcur[j] - h
            if h >= MIN_RIBBON:
                ribbon(ax, yL_hi, yL_lo, yR_hi, yR_lo, COLORS[i])
            lcur[i] -= h
            rcur[j] -= h

    for nodes, x0 in ((lnodes, X_LNODE[0]), (rnodes, X_RNODE[0])):
        w = X_LNODE[1] - X_LNODE[0]
        for k, (yt, h) in nodes.items():
            ax.add_patch(patches.Rectangle((x0, yt - h), w, h, facecolor=COLORS[k],
                                           edgecolor="white", lw=0.4))
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.04, 1.04)
    ax.axis("off")


def plot_model(model, fn, slug):
    """One compact figure per model: rows = Correct / Wrong, columns = harness."""
    apply_publication_style(font_size=10)
    path = DATA / fn
    nh = len(HARNESS_ORDER)
    no = len(OUTCOMES)
    fig, axes = plt.subplots(no, nh, figsize=(7.6, 4.0))
    fig.subplots_adjust(left=0.075, right=0.99, top=0.90, bottom=0.03,
                        wspace=0.10, hspace=0.12)
    for oi, (oname, oset, _) in enumerate(OUTCOMES):
        for hi, harness in enumerate(HARNESS_ORDER):
            ax = axes[oi, hi]
            draw_sankey(ax, counts(path, harness, oset))
            if oi == 0:
                ax.set_title(harness, fontsize=11, fontweight="bold")
    for oi, (oname, _, ocolor) in enumerate(OUTCOMES):
        pos = axes[oi, 0].get_position()
        fig.text(0.022, (pos.y0 + pos.y1) / 2, oname, rotation=90, va="center",
                 ha="center", fontsize=12, fontweight="bold", color=ocolor)
    saved = finalize_figure(fig, OUT.parent / f"fig5_hle_sankey_{slug}", dpi=300)
    for p in saved:
        print("wrote", p)


def main():
    for model, fn in MODELS:
        slug = "qwen" if "Qwen" in model else "gemma"
        plot_model(model, fn, slug)


if __name__ == "__main__":
    main()
