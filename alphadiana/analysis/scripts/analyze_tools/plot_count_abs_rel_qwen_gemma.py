#!/usr/bin/env python3
"""Combined count-abs / relative success-failure action matrix for Qwen and Gemma.

Audited sources:
  analyze_tools/data/six_action_statistics/action_counts_by_outcome.csv
  analyze_tools/data/six_action_statistics_gemma/action_counts_by_outcome.csv

Audit performed 2026-05-07:
  - Qwen: 149 action rows, 749032 action events, no event_total mismatches.
  - Gemma: 138 action rows, 109216 action events, no event_total mismatches.
  - Unknown outcomes are included on the failure side.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, Normalize

import plot_count_abs_rel_qwen as base


raw_table = r"""
| Model | Benchmark | Harness | Outcome | Events | Problem Framing | Plan Formation | Solution Execution | Tool Grounding | Result Auditing | Answer Delivery |
|---|---|---|---|---|---|---|---|---|---|---|
| Qwen3.5-27B | AIMEPass4 | DirectLLM | failure | 19841 | 155 (0.78%) | 3742 (18.86%) | 14033 (70.73%) | 0 (0.00%) | 1891 (9.53%) | 20 (0.10%) |
| Qwen3.5-27B | AIMEPass4 | DirectLLM | success | 56098 | 369 (0.66%) | 7195 (12.83%) | 42831 (76.35%) | 0 (0.00%) | 5498 (9.80%) | 205 (0.37%) |
| Qwen3.5-27B | AIMEPass4 | OpenClaw | failure | 13431 | 886 (6.60%) | 1238 (9.22%) | 11003 (81.92%) | 14 (0.10%) | 282 (2.10%) | 8 (0.06%) |
| Qwen3.5-27B | AIMEPass4 | OpenClaw | success | 4433 | 42 (0.95%) | 241 (5.44%) | 3470 (78.28%) | 222 (5.01%) | 361 (8.14%) | 97 (2.19%) |
| Qwen3.5-27B | AIMEPass4 | OpenCode | failure | 16345 | 423 (2.59%) | 636 (3.89%) | 12676 (77.55%) | 11 (0.07%) | 2523 (15.44%) | 76 (0.46%) |
| Qwen3.5-27B | AIMEPass4 | OpenCode | success | 15816 | 211 (1.33%) | 468 (2.96%) | 12609 (79.72%) | 147 (0.93%) | 2185 (13.82%) | 196 (1.24%) |
| Qwen3.5-27B | AIMEPass4 | OpenCode | unknown | 5937 | 209 (3.52%) | 70 (1.18%) | 4412 (74.31%) | 0 (0.00%) | 1246 (20.99%) | 0 (0.00%) |
| Qwen3.5-27B | AIMEPass4 | ZeroClaw | failure | 3602 | 30 (0.83%) | 124 (3.44%) | 2986 (82.90%) | 0 (0.00%) | 454 (12.60%) | 8 (0.22%) |
| Qwen3.5-27B | AIMEPass4 | ZeroClaw | success | 7760 | 109 (1.40%) | 367 (4.73%) | 6440 (82.99%) | 0 (0.00%) | 754 (9.72%) | 90 (1.16%) |
| Qwen3.5-27B | GPQA | DirectLLM | failure | 33434 | 212 (0.63%) | 6778 (20.27%) | 23378 (69.92%) | 0 (0.00%) | 3036 (9.08%) | 30 (0.09%) |
| Qwen3.5-27B | GPQA | DirectLLM | success | 33086 | 420 (1.27%) | 4977 (15.04%) | 24587 (74.31%) | 0 (0.00%) | 2917 (8.82%) | 185 (0.56%) |
| Qwen3.5-27B | GPQA | OpenClaw | failure | 20575 | 1872 (9.10%) | 585 (2.84%) | 14158 (68.81%) | 39 (0.19%) | 3889 (18.90%) | 32 (0.16%) |
| Qwen3.5-27B | GPQA | OpenClaw | success | 3446 | 67 (1.94%) | 394 (11.43%) | 2400 (69.65%) | 223 (6.47%) | 221 (6.41%) | 141 (4.09%) |
| Qwen3.5-27B | GPQA | OpenCode | failure | 3413 | 149 (4.37%) | 223 (6.53%) | 2383 (69.82%) | 17 (0.50%) | 591 (17.32%) | 50 (1.46%) |
| Qwen3.5-27B | GPQA | OpenCode | success | 7099 | 185 (2.61%) | 575 (8.10%) | 5505 (77.55%) | 138 (1.94%) | 398 (5.61%) | 298 (4.20%) |
| Qwen3.5-27B | GPQA | OpenCode | unknown | 273 | 0 (0.00%) | 2 (0.73%) | 229 (83.88%) | 0 (0.00%) | 42 (15.38%) | 0 (0.00%) |
| Qwen3.5-27B | GPQA | ZeroClaw | failure | 1440 | 46 (3.19%) | 134 (9.31%) | 1060 (73.61%) | 0 (0.00%) | 168 (11.67%) | 32 (2.22%) |
| Qwen3.5-27B | GPQA | ZeroClaw | success | 6073 | 137 (2.26%) | 487 (8.02%) | 4859 (80.01%) | 0 (0.00%) | 431 (7.10%) | 159 (2.62%) |
| Qwen3.5-27B | HLE | DirectLLM | failure | 250831 | 1758 (0.70%) | 68162 (27.17%) | 146365 (58.35%) | 0 (0.00%) | 33937 (13.53%) | 609 (0.24%) |
| Qwen3.5-27B | HLE | DirectLLM | success | 55492 | 605 (1.09%) | 8810 (15.88%) | 38383 (69.17%) | 0 (0.00%) | 7540 (13.59%) | 154 (0.28%) |
| Qwen3.5-27B | HLE | OpenClaw | failure | 71031 | 2851 (4.01%) | 4115 (5.79%) | 45002 (63.36%) | 5377 (7.57%) | 13003 (18.31%) | 683 (0.96%) |
| Qwen3.5-27B | HLE | OpenClaw | success | 4312 | 185 (4.29%) | 286 (6.63%) | 3041 (70.52%) | 247 (5.73%) | 461 (10.69%) | 92 (2.13%) |
| Qwen3.5-27B | HLE | OpenClaw | unknown | 463 | 2 (0.43%) | 7 (1.51%) | 262 (56.59%) | 64 (13.82%) | 127 (27.43%) | 1 (0.22%) |
| Qwen3.5-27B | HLE | OpenCode | failure | 83122 | 2740 (3.30%) | 4031 (4.85%) | 59319 (71.36%) | 1759 (2.12%) | 14527 (17.48%) | 746 (0.90%) |
| Qwen3.5-27B | HLE | OpenCode | success | 5339 | 251 (4.70%) | 401 (7.51%) | 3840 (71.92%) | 122 (2.29%) | 549 (10.28%) | 176 (3.30%) |
| Qwen3.5-27B | HLE | OpenCode | unknown | 92 | 2 (2.17%) | 0 (0.00%) | 55 (59.78%) | 4 (4.35%) | 31 (33.70%) | 0 (0.00%) |
| Qwen3.5-27B | HLE | ZeroClaw | failure | 21304 | 996 (4.68%) | 1846 (8.67%) | 14616 (68.61%) | 0 (0.00%) | 3409 (16.00%) | 437 (2.05%) |
| Qwen3.5-27B | HLE | ZeroClaw | success | 4944 | 178 (3.60%) | 346 (7.00%) | 3538 (71.56%) | 0 (0.00%) | 747 (15.11%) | 135 (2.73%) |
| Gemma4-31B | AIMEPass4 | DirectLLM | failure | 259 | 0 (0.00%) | 10 (3.86%) | 207 (79.92%) | 0 (0.00%) | 32 (12.36%) | 10 (3.86%) |
| Gemma4-31B | AIMEPass4 | DirectLLM | success | 1401 | 6 (0.43%) | 49 (3.50%) | 1226 (87.51%) | 0 (0.00%) | 8 (0.57%) | 112 (7.99%) |
| Gemma4-31B | AIMEPass4 | OpenClaw | failure | 31 | 0 (0.00%) | 3 (9.68%) | 7 (22.58%) | 19 (61.29%) | 0 (0.00%) | 2 (6.45%) |
| Gemma4-31B | AIMEPass4 | OpenClaw | success | 1384 | 5 (0.36%) | 52 (3.76%) | 1076 (77.75%) | 134 (9.68%) | 3 (0.22%) | 114 (8.24%) |
| Gemma4-31B | AIMEPass4 | OpenClaw | unknown | 34 | 0 (0.00%) | 0 (0.00%) | 4 (11.76%) | 29 (85.29%) | 0 (0.00%) | 1 (2.94%) |
| Gemma4-31B | AIMEPass4 | OpenCode | failure | 33 | 0 (0.00%) | 7 (21.21%) | 10 (30.30%) | 14 (42.42%) | 0 (0.00%) | 2 (6.06%) |
| Gemma4-31B | AIMEPass4 | OpenCode | success | 2155 | 14 (0.65%) | 142 (6.59%) | 1622 (75.27%) | 133 (6.17%) | 12 (0.56%) | 232 (10.77%) |
| Gemma4-31B | AIMEPass4 | OpenCode | unknown | 834 | 7 (0.84%) | 48 (5.76%) | 630 (75.54%) | 78 (9.35%) | 68 (8.15%) | 3 (0.36%) |
| Gemma4-31B | AIMEPass4 | ZeroClaw | failure | 376 | 0 (0.00%) | 31 (8.24%) | 314 (83.51%) | 0 (0.00%) | 28 (7.45%) | 3 (0.80%) |
| Gemma4-31B | AIMEPass4 | ZeroClaw | success | 1470 | 4 (0.27%) | 103 (7.01%) | 1231 (83.74%) | 0 (0.00%) | 16 (1.09%) | 116 (7.89%) |
| Gemma4-31B | GPQA | DirectLLM | failure | 250 | 18 (7.20%) | 22 (8.80%) | 175 (70.00%) | 0 (0.00%) | 7 (2.80%) | 28 (11.20%) |
| Gemma4-31B | GPQA | DirectLLM | success | 1407 | 64 (4.55%) | 62 (4.41%) | 1106 (78.61%) | 0 (0.00%) | 10 (0.71%) | 165 (11.73%) |
| Gemma4-31B | GPQA | OpenClaw | failure | 315 | 9 (2.86%) | 13 (4.13%) | 165 (52.38%) | 90 (28.57%) | 11 (3.49%) | 27 (8.57%) |
| Gemma4-31B | GPQA | OpenClaw | success | 1483 | 66 (4.45%) | 70 (4.72%) | 1032 (69.59%) | 131 (8.83%) | 15 (1.01%) | 169 (11.40%) |
| Gemma4-31B | GPQA | OpenClaw | unknown | 21 | 0 (0.00%) | 0 (0.00%) | 10 (47.62%) | 9 (42.86%) | 0 (0.00%) | 2 (9.52%) |
| Gemma4-31B | GPQA | OpenCode | failure | 501 | 15 (2.99%) | 16 (3.19%) | 343 (68.46%) | 49 (9.78%) | 36 (7.19%) | 42 (8.38%) |
| Gemma4-31B | GPQA | OpenCode | success | 2139 | 56 (2.62%) | 104 (4.86%) | 1454 (67.98%) | 143 (6.69%) | 34 (1.59%) | 348 (16.27%) |
| Gemma4-31B | GPQA | ZeroClaw | failure | 346 | 9 (2.60%) | 17 (4.91%) | 206 (59.54%) | 0 (0.00%) | 91 (26.30%) | 23 (6.65%) |
| Gemma4-31B | GPQA | ZeroClaw | success | 1706 | 60 (3.52%) | 91 (5.33%) | 1271 (74.50%) | 0 (0.00%) | 113 (6.62%) | 171 (10.02%) |
| Gemma4-31B | HLE | DirectLLM | failure | 6278 | 166 (2.64%) | 227 (3.62%) | 5396 (85.95%) | 0 (0.00%) | 97 (1.55%) | 392 (6.24%) |
| Gemma4-31B | HLE | DirectLLM | success | 1326 | 55 (4.15%) | 89 (6.71%) | 982 (74.06%) | 0 (0.00%) | 35 (2.64%) | 165 (12.44%) |
| Gemma4-31B | HLE | OpenClaw | failure | 4621 | 107 (2.32%) | 180 (3.90%) | 2038 (44.10%) | 1827 (39.54%) | 100 (2.16%) | 369 (7.99%) |
| Gemma4-31B | HLE | OpenClaw | success | 1369 | 35 (2.56%) | 46 (3.36%) | 781 (57.05%) | 335 (24.47%) | 29 (2.12%) | 143 (10.45%) |
| Gemma4-31B | HLE | OpenCode | failure | 66430 | 630 (0.95%) | 10532 (15.85%) | 32782 (49.35%) | 5108 (7.69%) | 16580 (24.96%) | 798 (1.20%) |
| Gemma4-31B | HLE | OpenCode | success | 2623 | 34 (1.30%) | 162 (6.18%) | 1544 (58.86%) | 555 (21.16%) | 44 (1.68%) | 284 (10.83%) |
| Gemma4-31B | HLE | ZeroClaw | failure | 8594 | 96 (1.12%) | 253 (2.94%) | 6330 (73.66%) | 0 (0.00%) | 1532 (17.83%) | 383 (4.46%) |
| Gemma4-31B | HLE | ZeroClaw | success | 1830 | 40 (2.19%) | 83 (4.54%) | 1105 (60.38%) | 0 (0.00%) | 430 (23.50%) | 172 (9.40%) |
"""

ACTIONS = base.ACTIONS
ACTION_LABELS = base.ACTION_LABELS
MODEL_ORDER = ["Qwen3.5-27B", "Gemma4-31B"]
BENCH_ORDER = base.BENCH_ORDER
HARNESS_ORDER = base.HARNESS_ORDER
ROW_KEYS = [(m, b, h) for m in MODEL_ORDER for b in BENCH_ORDER for h in HARNESS_ORDER]
DISPLAY_BENCH = {"AIMEPass4": "AIME26"}
ROW_LABELS = [f"{m.replace('3.5-27B', '3.5')}\n{DISPLAY_BENCH.get(b, b)}\n{h}" for m, b, h in ROW_KEYS]
OUTPUT_PATH = Path("analyze_tools/figures/count_abs_rel_qwen_gemma.pdf")


def build_matrices(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list[list[tuple[float, float]]]]:
    color_matrix = []
    total_matrix = []
    pie_matrix = []
    for model, bench, harness in ROW_KEYS:
        sub = df[(df["Model"] == model) & (df["Benchmark"] == bench) & (df["Harness"] == harness)]
        success = sub[sub["Outcome"] == "success"]
        fail = sub[sub["Outcome"].isin(["failure", "unknown"])]
        row_color = []
        row_total = []
        row_pie = []
        for action in ACTIONS:
            sc = int(success[action + "_count"].sum())
            fc = int(fail[action + "_count"].sum())
            total = sc + fc
            succ_frac = sc / total if total else 0.5
            row_color.append(succ_frac * 100)
            row_total.append(total)
            row_pie.append((succ_frac, 1 - succ_frac))
        color_matrix.append(row_color)
        total_matrix.append(row_total)
        pie_matrix.append(row_pie)
    return np.array(color_matrix), np.array(total_matrix, dtype=int), pie_matrix


def plot() -> None:
    df = base.parse_table(raw_table)
    color_matrix, total_matrix, pie_matrix = build_matrices(df)

    plt.style.use("default")
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 17,
        "xtick.labelsize": 12,
        "ytick.labelsize": 9,
        "figure.dpi": 180,
    })
    fig, ax = plt.subplots(figsize=(15.2, 18.0))
    cmap = LinearSegmentedColormap.from_list("soft_bluegreen", ["#F8FBFD", "#D8ECE9", "#9CCFC1", "#3E9B87"])
    norm = Normalize(vmin=0, vmax=100)
    im = ax.imshow(color_matrix, cmap=cmap, norm=norm, aspect="auto")

    ax.set_xticks(np.arange(len(ACTIONS)))
    ax.set_xticklabels(ACTION_LABELS)
    ax.set_yticks(np.arange(len(ROW_LABELS)))
    ax.set_yticklabels(ROW_LABELS)
    ax.set_xticks(np.arange(-0.5, len(ACTIONS), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(ROW_LABELS), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.8)
    ax.tick_params(which="minor", bottom=False, left=False)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    for y in [3.5, 7.5, 11.5, 15.5, 19.5]:
        ax.axhline(y, color="#C9D8DE", lw=2.2)

    nrows, ncols = color_matrix.shape
    pie_size = 0.035
    for i in range(nrows):
        for j in range(ncols):
            succ_frac, fail_frac = pie_matrix[i][j]
            cell_color = cmap(norm(color_matrix[i, j]))
            x_frac = (j + 0.5) / ncols
            y_frac = 1 - (i + 0.40) / nrows
            pie_ax = ax.inset_axes([x_frac - pie_size / 2, y_frac - pie_size / 2, pie_size, pie_size], transform=ax.transAxes)
            pie_ax.pie(
                [fail_frac, succ_frac],
                colors=["black", cell_color],
                startangle=90,
                counterclock=False,
                wedgeprops={"linewidth": 0.5, "edgecolor": "white"},
            )
            pie_ax.set_aspect("equal")
            pie_ax.set_xticks([])
            pie_ax.set_yticks([])
            for spine in pie_ax.spines.values():
                spine.set_visible(False)
            ax.text(
                j,
                i + 0.31,
                f"{total_matrix[i, j]:,}",
                ha="center",
                va="center",
                fontsize=7.6,
                color="#13242B",
                fontweight="bold",
                bbox={"boxstyle": "round,pad=0.12", "facecolor": "white", "edgecolor": "none", "alpha": 0.88},
            )

    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.025)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    fig.savefig(OUTPUT_PATH, dpi=300, bbox_inches="tight")
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    plot()
