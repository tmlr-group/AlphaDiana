#!/usr/bin/env python3
"""Fig 4 (alt) — horizontal-bar version with events sidebar, one figure per model.

Matches the layout in original/fig_4_previous_version.png: per benchmark, 8 horizontal
100%-stacked action-composition bars (4 harnesses x check/cross outcome), each with a
grey "events" sidebar (log scale) showing the volume the composition is computed from.
Outcome marked with LaTeX check / cross; ratios labeled on segments; no panel-letter
indices. Split into two individual plots (one per model).

Run:    python3 plot_fig4_hbars_revised.py
Reads:  data/action_counts_by_outcome_{qwen,gemma}.csv
Writes: revised/fig4_action_hbars_{qwen,gemma}.{pdf,png,svg}
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from _style import (apply_publication_style, finalize_figure,
                    ACTION_LABELS, ACTION_COLORS, OLD_TO_NEW)

DATA = HERE / "data"
MODELS = [("Qwen3.5-27B", "action_counts_by_outcome_qwen.csv", "qwen"),
          ("Gemma4-31B", "action_counts_by_outcome_gemma.csv", "gemma")]
BENCH_ORDER = ["AIMEPass4", "GPQA", "HLE"]
BENCH_LABEL = {"AIMEPass4": "AIME", "GPQA": "GPQA-Diamond", "HLE": "HLE"}
HARNESS_ORDER = ["DirectLLM", "OpenClaw", "OpenCode", "ZeroClaw"]
CHECK, CROSS = r"$\checkmark$", r"$\times$"
BAR_H = 0.8


def load(path: Path):
    comp = defaultdict(lambda: defaultdict(float))
    total = defaultdict(float)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            side = "success" if r["outcome"] == "success" else "failure"
            action = OLD_TO_NEW.get(r["action"], r["action"])
            comp[(r["benchmark"], r["harness"], side)][action] += float(r["count"])
    for key, actions in comp.items():
        total[key] = sum(actions.values())
    return comp, total


def bar_layout():
    """8 rows (4 harness x 2 outcome), grouped, top-to-bottom; return ys + (h,side)."""
    ys, labels, y = [], [], 0.0
    for h in HARNESS_ORDER:
        for side in ("success", "failure"):
            ys.append(y); labels.append((h, side)); y += 1.0
        y += 0.35
    ymax = max(ys)
    return [ymax - v for v in ys], labels


def fmt_k(v):
    return f"{v/1000:.1f}k" if v >= 1000 else f"{int(v)}"


def draw_facet(ax, ax_side, comp, total, bench, ys, labels, show_x):
    for (h, side), y in zip(labels, ys):
        key = (bench, h, side)
        tot = total.get(key, 0.0)
        left = 0.0
        if tot > 0:
            for action in ACTION_LABELS:
                frac = comp[key].get(action, 0.0) / tot
                if frac <= 0:
                    continue
                ax.barh(y, frac, left=left, height=BAR_H,
                        color=ACTION_COLORS[action], edgecolor="white", lw=0.4)
                if frac >= 0.08:
                    lum = sum(int(ACTION_COLORS[action][k:k+2], 16) for k in (1, 3, 5)) / 3
                    ax.text(left + frac / 2, y, f"{frac*100:.0f}", ha="center",
                            va="center", fontsize=24,
                            color="white" if lum < 140 else "black")
                left += frac
            ax_side.barh(y, tot, height=BAR_H, color="0.62", edgecolor="white", lw=0.4)
            ax_side.text(tot * 1.3, y, fmt_k(tot), ha="left", va="center",
                         fontsize=22, color="0.3")

    ax.set_xlim(0, 1)
    ax.set_ylim(min(ys) - 0.55, max(ys) + 0.55)
    ax.set_yticks(ys)
    ax.set_yticklabels([CHECK if s == "success" else CROSS for h, s in labels],
                       fontsize=29)
    ax.tick_params(length=0)
    # one harness name per ✓/✗ pair, vertically centered between the two rows
    pos = {lab: y for lab, y in zip(labels, ys)}
    for h in HARNESS_ORDER:
        ym = (pos[(h, "success")] + pos[(h, "failure")]) / 2
        ax.text(-0.085, ym, h, transform=ax.get_yaxis_transform(),
                ha="right", va="center", fontsize=29)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    if show_x:
        ax.set_xticks([0, 0.5, 1.0]); ax.set_xticklabels(["0", "50", "100"], fontsize=26)
    else:
        ax.set_xticks([0, 0.5, 1.0]); ax.set_xticklabels([])

    ax_side.set_xscale("log")
    ax_side.set_xlim(40, 6e5)     # low enough for the smallest count (65), room for labels
    ax_side.set_ylim(ax.get_ylim())
    ax_side.set_yticks([])
    for sp in ("top", "right", "left"):
        ax_side.spines[sp].set_visible(False)
    if show_x:
        ax_side.set_xticks([1e3, 1e5]); ax_side.set_xticklabels(["$10^3$", "$10^5$"], fontsize=24)
    else:
        ax_side.set_xticks([1e3, 1e5]); ax_side.set_xticklabels([])


def plot_model(model, csv_name, slug):
    apply_publication_style(font_size=19, axes_linewidth=0.8)
    comp, total = load(DATA / csv_name)
    ys, labels = bar_layout()
    nb = len(BENCH_ORDER)
    fig = plt.figure(figsize=(13.8, 16.5))
    outer = fig.add_gridspec(nb, 1, left=0.275, right=0.975, top=0.94, bottom=0.05,
                             hspace=0.075)
    for bi, bench in enumerate(BENCH_ORDER):
        inner = outer[bi].subgridspec(1, 2, width_ratios=[1, 0.2], wspace=0.04)
        ax = fig.add_subplot(inner[0, 0])
        ax_side = fig.add_subplot(inner[0, 1])
        draw_facet(ax, ax_side, comp, total, bench, ys, labels, show_x=(bi == nb - 1))
        ax.text(-0.42, 0.5, BENCH_LABEL[bench], transform=ax.transAxes, rotation=90,
                va="center", ha="center", fontsize=32, fontweight="bold")
        if bi == 0:
            ax_side.set_title("events", fontsize=26, color="0.3", pad=8)
    fig.suptitle(model, fontsize=38, fontweight="bold", x=0.55, y=0.985)
    saved = finalize_figure(fig, HERE / "revised" / f"fig4_action_hbars_{slug}", dpi=300)
    for p in saved:
        print("wrote", p)


def main():
    for model, csv_name, slug in MODELS:
        plot_model(model, csv_name, slug)


if __name__ == "__main__":
    main()
