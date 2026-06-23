#!/usr/bin/env python3
"""Count-abs / relative success-failure action matrix for Qwen3.5-27B.

Audited source:
  analyze_tools/data/six_action_statistics/action_counts_by_outcome.csv

Audit performed 2026-05-07:
  - Each (benchmark, harness, outcome) action-count sum equals event_total.
  - Unknown outcomes are included on the failure side, matching the requested
    sc/(sc+fc) calculation.
  - Scope: HLE, GPQA, and AIMEPass4 across DirectLLM/OpenClaw/OpenCode/ZeroClaw.
"""

from __future__ import annotations

import re
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Patch


raw_table = r"""
| Benchmark | Harness | Outcome | Events | Problem Framing | Plan Formation | Solution Execution | Tool Grounding | Result Auditing | Answer Delivery |
|---|---|---|---|---|---|---|---|---|---|
| AIMEPass4 | DirectLLM | failure | 19841 | 155 (0.78%) | 3742 (18.86%) | 14033 (70.73%) | 0 (0.00%) | 1891 (9.53%) | 20 (0.10%) |
| AIMEPass4 | DirectLLM | success | 56098 | 369 (0.66%) | 7195 (12.83%) | 42831 (76.35%) | 0 (0.00%) | 5498 (9.80%) | 205 (0.37%) |
| AIMEPass4 | OpenClaw | failure | 13431 | 886 (6.60%) | 1238 (9.22%) | 11003 (81.92%) | 14 (0.10%) | 282 (2.10%) | 8 (0.06%) |
| AIMEPass4 | OpenClaw | success | 4433 | 42 (0.95%) | 241 (5.44%) | 3470 (78.28%) | 222 (5.01%) | 361 (8.14%) | 97 (2.19%) |
| AIMEPass4 | OpenCode | failure | 16345 | 423 (2.59%) | 636 (3.89%) | 12676 (77.55%) | 11 (0.07%) | 2523 (15.44%) | 76 (0.46%) |
| AIMEPass4 | OpenCode | success | 15816 | 211 (1.33%) | 468 (2.96%) | 12609 (79.72%) | 147 (0.93%) | 2185 (13.82%) | 196 (1.24%) |
| AIMEPass4 | OpenCode | unknown | 5937 | 209 (3.52%) | 70 (1.18%) | 4412 (74.31%) | 0 (0.00%) | 1246 (20.99%) | 0 (0.00%) |
| AIMEPass4 | ZeroClaw | failure | 3602 | 30 (0.83%) | 124 (3.44%) | 2986 (82.90%) | 0 (0.00%) | 454 (12.60%) | 8 (0.22%) |
| AIMEPass4 | ZeroClaw | success | 7760 | 109 (1.40%) | 367 (4.73%) | 6440 (82.99%) | 0 (0.00%) | 754 (9.72%) | 90 (1.16%) |
| GPQA | DirectLLM | failure | 33434 | 212 (0.63%) | 6778 (20.27%) | 23378 (69.92%) | 0 (0.00%) | 3036 (9.08%) | 30 (0.09%) |
| GPQA | DirectLLM | success | 33086 | 420 (1.27%) | 4977 (15.04%) | 24587 (74.31%) | 0 (0.00%) | 2917 (8.82%) | 185 (0.56%) |
| GPQA | OpenClaw | failure | 20575 | 1872 (9.10%) | 585 (2.84%) | 14158 (68.81%) | 39 (0.19%) | 3889 (18.90%) | 32 (0.16%) |
| GPQA | OpenClaw | success | 3446 | 67 (1.94%) | 394 (11.43%) | 2400 (69.65%) | 223 (6.47%) | 221 (6.41%) | 141 (4.09%) |
| GPQA | OpenCode | failure | 3413 | 149 (4.37%) | 223 (6.53%) | 2383 (69.82%) | 17 (0.50%) | 591 (17.32%) | 50 (1.46%) |
| GPQA | OpenCode | success | 7099 | 185 (2.61%) | 575 (8.10%) | 5505 (77.55%) | 138 (1.94%) | 398 (5.61%) | 298 (4.20%) |
| GPQA | OpenCode | unknown | 273 | 0 (0.00%) | 2 (0.73%) | 229 (83.88%) | 0 (0.00%) | 42 (15.38%) | 0 (0.00%) |
| GPQA | ZeroClaw | failure | 1440 | 46 (3.19%) | 134 (9.31%) | 1060 (73.61%) | 0 (0.00%) | 168 (11.67%) | 32 (2.22%) |
| GPQA | ZeroClaw | success | 6073 | 137 (2.26%) | 487 (8.02%) | 4859 (80.01%) | 0 (0.00%) | 431 (7.10%) | 159 (2.62%) |
| HLE | DirectLLM | failure | 250831 | 1758 (0.70%) | 68162 (27.17%) | 146365 (58.35%) | 0 (0.00%) | 33937 (13.53%) | 609 (0.24%) |
| HLE | DirectLLM | success | 55492 | 605 (1.09%) | 8810 (15.88%) | 38383 (69.17%) | 0 (0.00%) | 7540 (13.59%) | 154 (0.28%) |
| HLE | OpenClaw | failure | 71031 | 2851 (4.01%) | 4115 (5.79%) | 45002 (63.36%) | 5377 (7.57%) | 13003 (18.31%) | 683 (0.96%) |
| HLE | OpenClaw | success | 4312 | 185 (4.29%) | 286 (6.63%) | 3041 (70.52%) | 247 (5.73%) | 461 (10.69%) | 92 (2.13%) |
| HLE | OpenClaw | unknown | 463 | 2 (0.43%) | 7 (1.51%) | 262 (56.59%) | 64 (13.82%) | 127 (27.43%) | 1 (0.22%) |
| HLE | OpenCode | failure | 83122 | 2740 (3.30%) | 4031 (4.85%) | 59319 (71.36%) | 1759 (2.12%) | 14527 (17.48%) | 746 (0.90%) |
| HLE | OpenCode | success | 5339 | 251 (4.70%) | 401 (7.51%) | 3840 (71.92%) | 122 (2.29%) | 549 (10.28%) | 176 (3.30%) |
| HLE | OpenCode | unknown | 92 | 2 (2.17%) | 0 (0.00%) | 55 (59.78%) | 4 (4.35%) | 31 (33.70%) | 0 (0.00%) |
| HLE | ZeroClaw | failure | 21304 | 996 (4.68%) | 1846 (8.67%) | 14616 (68.61%) | 0 (0.00%) | 3409 (16.00%) | 437 (2.05%) |
| HLE | ZeroClaw | success | 4944 | 178 (3.60%) | 346 (7.00%) | 3538 (71.56%) | 0 (0.00%) | 747 (15.11%) | 135 (2.73%) |
"""

MODEL_NAME = "Qwen3.5-27B"
OUTPUT_PATH = Path("analyze_tools/figures/count_abs_rel_qwen.pdf")
CMAP_COLORS = ["#F8FBFD", "#D8ECE9", "#9CCFC1", "#3E9B87"]

ACTIONS = [
    "Problem Framing",
    "Plan Formation",
    "Solution Execution",
    "Tool Grounding",
    "Result Auditing",
    "Answer Delivery",
]
ACTION_LABELS = ["Understanding", "Planning", "Reasoning", "Tool Use", "Verification", "Finalization"]
BENCH_ORDER = ["AIMEPass4", "GPQA", "HLE"]
HARNESS_ORDER = ["DirectLLM", "OpenClaw", "OpenCode", "ZeroClaw"]
ROW_KEYS = [(b, h) for b in BENCH_ORDER for h in HARNESS_ORDER]
DISPLAY_BENCH = {"AIMEPass4": "AIME26"}
ROW_LABELS = [f"{DISPLAY_BENCH.get(b, b)}\n{h}" for b, h in ROW_KEYS]


def parse_count_pct(value: str) -> tuple[int, float]:
    match = re.match(r"([\d,]+)\s*\(([-\d.]+)%\)", str(value))
    if not match:
        raise ValueError(f"Cannot parse count/percent cell: {value!r}")
    return int(match.group(1).replace(",", "")), float(match.group(2))


def parse_table(raw: str) -> pd.DataFrame:
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    header = [cell.strip() for cell in lines[0].strip("|").split("|")]
    records = []
    for line in lines[2:]:
        values = [cell.strip() for cell in line.strip("|").split("|")]
        if len(values) == len(header):
            records.append(dict(zip(header, values)))
    df = pd.DataFrame(records)
    for action in ACTIONS:
        parsed = df[action].apply(parse_count_pct)
        df[action + "_count"] = parsed.apply(lambda pair: pair[0])
    df["Events"] = df["Events"].astype(str).str.replace(",", "", regex=False).astype(int)
    return df


def build_matrices(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list[list[tuple[float, float]]]]:
    color_matrix = []
    total_matrix = []
    pie_matrix = []
    for bench, harness in ROW_KEYS:
        sub = df[(df["Benchmark"] == bench) & (df["Harness"] == harness)]
        success = sub[sub["Outcome"] == "success"]
        fail = sub[sub["Outcome"].isin(["failure", "unknown"])]
        row_color = []
        row_total = []
        row_pie = []
        for action in ACTIONS:
            sc = int(success[action + "_count"].sum())
            fc = int(fail[action + "_count"].sum())
            total = sc + fc
            succ_frac = sc / total if total > 0 else 0.5
            row_color.append(succ_frac * 100)
            row_total.append(total)
            row_pie.append((succ_frac, 1 - succ_frac))
        color_matrix.append(row_color)
        total_matrix.append(row_total)
        pie_matrix.append(row_pie)
    return np.array(color_matrix), np.array(total_matrix, dtype=int), pie_matrix


def plot() -> None:
    df = parse_table(raw_table)
    color_matrix, total_matrix, pie_matrix = build_matrices(df)

    plt.style.use("default")
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 12,
        "axes.titlesize": 17,
        "axes.labelsize": 13,
        "xtick.labelsize": 12,
        "ytick.labelsize": 12,
        "figure.dpi": 180,
    })

    fig, ax = plt.subplots(figsize=(13.8, 10.8))
    cmap = LinearSegmentedColormap.from_list("plot_theme", CMAP_COLORS)
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
    for y in [3.5, 7.5]:
        ax.axhline(y, color="#C9D8DE", lw=2.4)

    nrows, ncols = color_matrix.shape
    pie_size = 0.055
    for i in range(nrows):
        for j in range(ncols):
            succ_frac, fail_frac = pie_matrix[i][j]
            cell_color = cmap(norm(color_matrix[i, j]))
            x_frac = (j + 0.5) / ncols
            y_frac = 1 - (i + 0.40) / nrows
            pie_ax = ax.inset_axes(
                [x_frac - pie_size / 2, y_frac - pie_size / 2, pie_size, pie_size],
                transform=ax.transAxes,
            )
            pie_ax.pie(
                [fail_frac, succ_frac],
                colors=["black", cell_color],
                startangle=90,
                counterclock=False,
                wedgeprops={"linewidth": 0.6, "edgecolor": "white"},
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
                fontsize=8.8,
                color="#13242B",
                fontweight="bold",
                bbox={"boxstyle": "round,pad=0.16", "facecolor": "white", "edgecolor": "none", "alpha": 0.88},
            )

    cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.03)
    cbar.outline.set_linewidth(0.6)
    cbar.outline.set_edgecolor("#C7CDD6")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    fig.savefig(OUTPUT_PATH, dpi=300, bbox_inches="tight")
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    plot()
