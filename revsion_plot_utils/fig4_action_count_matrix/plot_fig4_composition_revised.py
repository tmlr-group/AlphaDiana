#!/usr/bin/env python3
"""Fig 4 (v2) — action composition, one figure per model.

Per follow-up: split into two individual plots (one per model); label the two
outcome bars with LaTeX check / cross marks instead of S/F; show only the action
ratios (drop the total-count annotation); larger fonts; no panel-letter indices.

Layout per model: benchmark rows x harness cols; each cell is a check/cross pair of
100%-stacked action-composition bars (check = success, cross = failure+unknown),
colored by the shared 6-action palette. No in-figure legend (action color key +
check/cross definition go in the caption).

Run:    python3 plot_fig4_composition_revised.py
Reads:  data/action_counts_by_outcome_{qwen,gemma}.csv
Writes: revised/fig4_action_composition_{qwen,gemma}.{pdf,png,svg}
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

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
SIDES = [("success", 0.0), ("failure", 1.0)]


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


def draw_cell(ax, comp, total, bench, harness):
    for side, x in SIDES:
        key = (bench, harness, side)
        tot = total.get(key, 0.0)
        if tot <= 0:
            ax.text(x, 0.5, "–", ha="center", va="center", fontsize=12, color="0.6")
            continue
        bottom = 0.0
        for action in ACTION_LABELS:
            frac = comp[key].get(action, 0.0) / tot
            if frac <= 0:
                continue
            ax.bar(x, frac, width=0.8, bottom=bottom,
                   color=ACTION_COLORS[action], edgecolor="white", lw=0.4)
            if frac >= 0.10:
                lum = sum(int(ACTION_COLORS[action][k:k+2], 16) for k in (1, 3, 5)) / 3
                ax.text(x, bottom + frac / 2, f"{frac*100:.0f}", ha="center",
                        va="center", fontsize=8.5,
                        color="white" if lum < 140 else "black")
            bottom += frac
    ax.set_xlim(-0.62, 1.62)
    ax.set_ylim(0, 1)
    ax.set_xticks([0, 1])
    ax.set_yticks([0, 0.5, 1.0])
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)


def plot_model(model, csv_name, slug):
    apply_publication_style(font_size=13, axes_linewidth=0.9)
    comp, total = load(DATA / csv_name)
    nb, nh = len(BENCH_ORDER), len(HARNESS_ORDER)
    fig, axes = plt.subplots(nb, nh, figsize=(8.4, 7.2))
    fig.subplots_adjust(left=0.115, right=0.99, top=0.925, bottom=0.085,
                        hspace=0.11, wspace=0.09)
    for bi, bench in enumerate(BENCH_ORDER):
        for hi, harness in enumerate(HARNESS_ORDER):
            ax = axes[bi, hi]
            draw_cell(ax, comp, total, bench, harness)
            if bi == 0:
                ax.set_title(harness, fontsize=14, fontweight="bold")
            if bi == nb - 1:
                ax.set_xticklabels([CHECK, CROSS], fontsize=15)
            else:
                ax.set_xticklabels([])
            if hi != 0:
                ax.set_yticklabels([])
            ax.tick_params(labelsize=11, length=0)
        axes[bi, 0].set_ylabel(f"{BENCH_LABEL[bench]}\naction share", fontsize=13)
    fig.suptitle(model, fontsize=16, fontweight="bold", y=0.985)
    saved = finalize_figure(fig, HERE / "revised" / f"fig4_action_composition_{slug}",
                            dpi=300)
    for p in saved:
        print("wrote", p)


def main():
    for model, csv_name, slug in MODELS:
        plot_model(model, csv_name, slug)


if __name__ == "__main__":
    main()
